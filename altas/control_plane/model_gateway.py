"""OpenAI-compatible model routing with a deterministic local mock."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

import httpx

from .config import ControlPlaneSettings
from .schemas import ChatCompletionRequest


class ModelGatewayError(RuntimeError):
    """A sanitized model-provider failure safe to surface through the API."""


def _estimate_tokens(value: str) -> int:
    # A deterministic approximation is sufficient for prototype metering. Real
    # providers return authoritative counts, which replace this path.
    return max(1, (len(value.encode("utf-8")) + 3) // 4)


@dataclass(frozen=True, slots=True)
class GatewayResult:
    response: dict[str, Any]
    provider: str
    request_id: str
    input_tokens: int
    output_tokens: int


class ModelGateway:
    def __init__(self, settings: ControlPlaneSettings) -> None:
        self.settings = settings

    async def complete(self, request: ChatCompletionRequest) -> GatewayResult:
        if request.stream:
            raise ModelGatewayError("streaming_not_supported")
        if self.settings.mock_model:
            return self._mock_complete(request)
        return await self._upstream_complete(request)

    def _mock_complete(self, request: ChatCompletionRequest) -> GatewayResult:
        serialized_messages = [
            message.model_dump(mode="json", exclude_none=True)
            for message in request.messages
        ]
        canonical = json.dumps(
            {"messages": serialized_messages, "model": request.model},
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        last_input = next(
            (
                message.content
                for message in reversed(request.messages)
                if message.role in {"user", "tool"}
            ),
            "",
        )
        if not isinstance(last_input, str):
            last_input = json.dumps(last_input, sort_keys=True, separators=(",", ":"))
        content = (
            f"Atlas mock response {digest[:12]}. Validated request: {last_input[:1000]}"
        )
        input_tokens = _estimate_tokens(canonical)
        output_tokens = _estimate_tokens(content)
        request_id = f"chatcmpl-mock-{digest[:20]}"
        response: dict[str, Any] = {
            "id": request_id,
            "object": "chat.completion",
            # Fixed in mock mode so identical requests are byte-for-byte stable.
            "created": 1_767_225_600,
            "model": request.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            },
            "system_fingerprint": "altas-mock-v1",
        }
        return GatewayResult(
            response=response,
            provider="altas-mock",
            request_id=request_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    async def _upstream_complete(self, request: ChatCompletionRequest) -> GatewayResult:
        api_key = self.settings.upstream_api_key
        if not api_key:
            raise ModelGatewayError("upstream_not_configured")
        # Build the provider payload from an explicit allowlist even though the
        # request schema is strict. This is a second boundary against a future
        # schema field accidentally becoming an upstream cost-control escape.
        allowed_fields = {
            "frequency_penalty",
            "max_completion_tokens",
            "max_tokens",
            "messages",
            "model",
            "parallel_tool_calls",
            "presence_penalty",
            "seed",
            "stop",
            "temperature",
            "tool_choice",
            "tools",
            "top_p",
            "user",
        }
        serialized = request.model_dump(mode="json", exclude_none=True)
        payload = {
            key: value for key, value in serialized.items() if key in allowed_fields
        }
        # Never trust a downstream-compatible model/schema change to preserve
        # this invariant: one Atlas request can purchase exactly one choice.
        payload["n"] = 1
        payload["stream"] = False
        try:
            async with httpx.AsyncClient(
                timeout=self.settings.request_timeout_seconds
            ) as client:
                response = await client.post(
                    f"{self.settings.upstream_base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Never relay provider bodies: they can contain request data or
            # operational details. The full exception also stays out of audit.
            raise ModelGatewayError("upstream_request_failed") from exc
        if not isinstance(body, dict):
            raise ModelGatewayError("upstream_response_invalid")
        usage = body.get("usage") or {}
        if not isinstance(usage, dict):
            raise ModelGatewayError("upstream_response_invalid")
        try:
            input_tokens = int(usage.get("prompt_tokens", 0))
            output_tokens = int(usage.get("completion_tokens", 0))
        except (TypeError, ValueError) as exc:
            raise ModelGatewayError("upstream_response_invalid") from exc
        if input_tokens < 0 or output_tokens < 0:
            raise ModelGatewayError("upstream_response_invalid")
        request_id = str(
            body.get("id")
            or f"chatcmpl-{hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:20]}"
        )
        return GatewayResult(
            response=body,
            provider="upstream",
            request_id=request_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
