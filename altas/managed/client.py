"""HTTP client for the Atlas worker/control-plane contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from altas.managed.context import ManagedContext
from altas.managed.errors import (
    ControlPlaneUnavailable,
    DeviceAuthenticationError,
)


@dataclass(frozen=True, slots=True)
class Lease:
    token: str
    expires_at: str

    def __repr__(self) -> str:
        return f"Lease(token='[REDACTED]', expires_at={self.expires_at!r})"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason_code: str
    decision_id: str | None = None


class AltasControlPlaneClient:
    """Small, explicit client that never logs bearer or lease values."""

    def __init__(
        self,
        *,
        base_url: str,
        device_token: str,
        timeout_seconds: float = 5.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not device_token:
            raise ValueError("device_token is required")
        self._device_token = device_token
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            headers={"User-Agent": "atlas-worker/0.1"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "AltasControlPlaneClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _headers(
        self,
        *,
        lease: str | None = None,
        claim_token: str | None = None,
        context: ManagedContext | None = None,
    ) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._device_token}"}
        if lease:
            headers["X-Atlas-Lease"] = lease
        if claim_token:
            headers["X-Atlas-Claim-Token"] = claim_token
        if context:
            headers.update(context.request_headers())
        return headers

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ControlPlaneUnavailable("Atlas Control Plane is unavailable") from exc
        if response.status_code in {401, 403}:
            raise DeviceAuthenticationError("Atlas device authorization was rejected")
        if response.status_code >= 500:
            raise ControlPlaneUnavailable(
                f"Atlas Control Plane returned {response.status_code}"
            )
        return response

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ControlPlaneUnavailable(
                "Control Plane returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise ControlPlaneUnavailable("Control Plane returned an invalid object")
        return payload

    def heartbeat(
        self,
        *,
        tenant_id: str,
        store_id: str,
        agent_id: str,
        worker_version: str,
        health_status: str = "healthy",
        metadata: dict[str, Any] | None = None,
    ) -> Lease:
        response = self._request(
            "POST",
            "/api/v1/worker/heartbeat",
            headers=self._headers(),
            json={
                "tenant_id": tenant_id,
                "store_id": store_id,
                "agent_id": agent_id,
                "worker_version": worker_version,
                "health_status": health_status,
                "metadata": metadata or {},
            },
        )
        response.raise_for_status()
        payload = self._json(response)
        lease_payload = payload.get("lease", payload)
        return Lease(
            token=str(lease_payload["token"]),
            expires_at=str(lease_payload["expires_at"]),
        )

    def evaluate_policy(
        self,
        *,
        lease: str,
        context: ManagedContext,
        claim_token: str,
        capability: str,
        tool_name: str | None = None,
    ) -> PolicyDecision:
        response = self._request(
            "POST",
            "/api/v1/worker/policy/evaluate",
            headers=self._headers(
                lease=lease,
                claim_token=claim_token,
                context=context,
            ),
            json={
                "store_id": context.store_id,
                "agent_id": context.agent_id,
                "capability": capability,
            },
        )
        if response.status_code >= 400 and response.status_code not in {401, 403}:
            payload = self._json(response)
            return PolicyDecision(
                allowed=False,
                reason_code=str(
                    payload.get("code")
                    or payload.get("reason_code")
                    or "POLICY_UNAVAILABLE"
                ),
                decision_id=payload.get("decision_id"),
            )
        payload = self._json(response)
        return PolicyDecision(
            allowed=bool(payload.get("allowed")),
            reason_code=str(
                payload.get("code")
                or payload.get("reason_code")
                or "POLICY_UNAVAILABLE"
            ),
            decision_id=payload.get("decision_id"),
        )

    def next_job(
        self,
        *,
        lease: str,
        tenant_id: str,
        store_id: str,
        agent_id: str,
    ) -> dict[str, Any] | None:
        response = self._request(
            "GET",
            "/api/v1/worker/jobs/next",
            headers={
                **self._headers(lease=lease),
                "X-Atlas-Tenant-ID": tenant_id,
                "X-Atlas-Store-ID": store_id,
                "X-Atlas-Agent-ID": agent_id,
            },
        )
        response.raise_for_status()
        payload = self._json(response)
        job = payload.get("job")
        if job is None:
            return None
        if not isinstance(job, dict):
            raise ControlPlaneUnavailable("Control Plane returned an invalid job")
        return job

    def complete_job(
        self,
        *,
        lease: str,
        context: ManagedContext,
        claim_token: str,
        status: str,
        result: dict[str, Any] | None = None,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            f"/api/v1/worker/jobs/{context.job_id}/complete",
            headers=self._headers(lease=lease, context=context),
            json={
                "tenant_id": context.tenant_id,
                "store_id": context.store_id,
                "agent_id": context.agent_id,
                "claim_token": claim_token,
                "status": status,
                "result": result,
                "error": error_code,
            },
        )
        response.raise_for_status()
        return self._json(response)

    def chat_completion(
        self,
        *,
        lease: str,
        context: ManagedContext,
        claim_token: str,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int = 400,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/v1/chat/completions",
            headers=self._headers(
                lease=lease,
                claim_token=claim_token,
                context=context,
            ),
            json={
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "stream": False,
            },
        )
        response.raise_for_status()
        return self._json(response)
