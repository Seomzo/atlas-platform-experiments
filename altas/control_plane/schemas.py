"""Pydantic request/response contracts for the control-plane API."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HeartbeatRequest(StrictModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    store_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    worker_version: str = Field(min_length=1, max_length=64)
    health_status: Literal["healthy", "degraded"] = "healthy"
    metadata: dict[str, Any] = Field(default_factory=dict)


class PolicyEvaluationRequest(StrictModel):
    store_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    capability: str = Field(min_length=1, max_length=160)


class JobCompletionRequest(StrictModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    store_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    claim_token: str = Field(min_length=32, max_length=256, repr=False)
    status: Literal["succeeded", "failed"]
    result: dict[str, Any] | None = None
    error: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> "JobCompletionRequest":
        if self.status == "failed" and not self.error:
            raise ValueError("failed jobs require an error")
        if self.status == "succeeded" and self.error:
            raise ValueError("successful jobs cannot include an error")
        return self


class QueueJobRequest(StrictModel):
    tenant_id: str = Field(min_length=1, max_length=128)
    store_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    device_id: str | None = Field(default=None, min_length=1, max_length=128)
    capability: str = Field(min_length=1, max_length=160)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=160)


class ToggleRequest(StrictModel):
    enabled: bool | None = None
    reason: str | None = Field(default=None, max_length=500)


class RequeueJobRequest(StrictModel):
    reason: str = Field(min_length=3, max_length=500)


MAX_CHAT_MESSAGES = 128
MAX_CHAT_MESSAGE_BYTES = 64 * 1024
MAX_CHAT_REQUEST_BYTES = 256 * 1024


def _json_size(value: Any) -> int:
    """Return the UTF-8 wire-size approximation for JSON-compatible input."""

    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("chat payload must be JSON serializable") from exc
    return len(serialized.encode("utf-8"))


class ChatMessage(StrictModel):
    """Explicit OpenAI-compatible message fields accepted by Atlas.

    ``content`` and tool-call payloads remain structurally compatible with the
    OpenAI SDK/Hermes, while per-message and whole-request limits keep those
    flexible JSON values from becoming an unbounded provider-cost input.
    """

    role: Literal["system", "developer", "user", "assistant", "tool"]
    content: Any = None
    name: str | None = Field(default=None, min_length=1, max_length=256)
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=256)
    tool_calls: list[dict[str, Any]] | None = Field(default=None, max_length=128)
    function_call: dict[str, Any] | None = None
    refusal: str | None = Field(default=None, max_length=8192)
    reasoning_content: str | None = Field(default=None, max_length=65536)
    reasoning_details: list[dict[str, Any]] | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def bound_message_size(self) -> "ChatMessage":
        if _json_size(self.model_dump(mode="json", exclude_none=True)) > (
            MAX_CHAT_MESSAGE_BYTES
        ):
            raise ValueError("chat message exceeds the maximum allowed size")
        return self


class ChatCompletionRequest(StrictModel):
    """Strict, cost-bounded subset of the OpenAI chat-completions schema.

    The gateway deliberately rejects arbitrary ``extra_body`` values and
    unsupported provider extensions. Every accepted option is declared here,
    and ``n`` is fixed to one so a caller cannot multiply provider spend.
    """

    model: str = Field(min_length=1, max_length=200)
    messages: list[ChatMessage] = Field(min_length=1, max_length=MAX_CHAT_MESSAGES)
    tools: list[dict[str, Any]] | None = Field(default=None, max_length=128)
    tool_choice: Literal["none", "auto", "required"] | dict[str, Any] | None = None
    parallel_tool_calls: bool | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    frequency_penalty: float | None = Field(default=None, ge=-2, le=2)
    presence_penalty: float | None = Field(default=None, ge=-2, le=2)
    max_tokens: int | None = Field(default=None, gt=0, le=32768)
    max_completion_tokens: int | None = Field(default=None, gt=0, le=32768)
    n: Literal[1] = 1
    stream: bool = False
    stop: str | list[str] | None = None
    seed: int | None = Field(default=None, ge=-(2**63), le=2**63 - 1)
    user: str | None = Field(default=None, max_length=256)

    @field_validator("messages")
    @classmethod
    def require_user_or_tool_input(
        cls, messages: list[ChatMessage]
    ) -> list[ChatMessage]:
        if not any(message.role in {"user", "tool"} for message in messages):
            raise ValueError("messages must include a user or tool message")
        return messages

    @field_validator("stop")
    @classmethod
    def bound_stop_sequences(cls, stop: str | list[str] | None):
        if stop is None:
            return None
        values = [stop] if isinstance(stop, str) else stop
        if len(values) > 4 or any(len(value) > 256 for value in values):
            raise ValueError("stop must contain at most four bounded strings")
        return stop

    @model_validator(mode="after")
    def bound_request_size(self) -> "ChatCompletionRequest":
        if _json_size(self.model_dump(mode="json", exclude_none=True)) > (
            MAX_CHAT_REQUEST_BYTES
        ):
            raise ValueError("chat request exceeds the maximum allowed size")
        return self
