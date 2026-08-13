"""HTTP client for the Atlas worker/control-plane contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from altas.managed.context import ManagedContext
from altas.managed.actions import ManagedAction
from altas.managed.context import ManagedActionAuthorization
from altas.cortex.managed_dispatch import CortexDispatchAdmission
from altas.managed.errors import (
    ControlPlaneUnavailable,
    DeviceAuthenticationError,
)


@dataclass(frozen=True, slots=True)
class Lease:
    token: str
    expires_at: str
    capabilities: tuple[str, ...] = ()

    def __repr__(self) -> str:
        return (
            "Lease(token='[REDACTED]', "
            f"expires_at={self.expires_at!r}, capabilities={self.capabilities!r})"
        )


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
        self._next_job_authorization_lease: Lease | None = None
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
            capabilities=tuple(
                str(value)
                for value in lease_payload.get("capabilities", [])
                if str(value).strip()
            ),
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

    def request_managed_approval(
        self,
        *,
        lease: str,
        context: ManagedContext,
        claim_token: str,
        relay_session_id: str,
        action: ManagedAction,
        idempotency_key: str,
    ) -> dict[str, Any]:
        response = self._request(
            "POST",
            "/api/v1/worker/approvals",
            headers={
                **self._headers(
                    lease=lease,
                    claim_token=claim_token,
                    context=context,
                ),
                "Idempotency-Key": idempotency_key,
            },
            json={
                "relay_session_id": relay_session_id,
                "action": action.to_mapping(),
            },
        )
        response.raise_for_status()
        approval = self._json(response).get("approval")
        if not isinstance(approval, dict):
            raise ControlPlaneUnavailable("Control Plane returned an invalid approval")
        return approval

    def get_managed_approval(
        self,
        *,
        approval_id: str,
        lease: str,
        context: ManagedContext,
        claim_token: str,
    ) -> dict[str, Any]:
        response = self._request(
            "GET",
            f"/api/v1/worker/approvals/{approval_id}",
            headers=self._headers(
                lease=lease,
                claim_token=claim_token,
                context=context,
            ),
        )
        response.raise_for_status()
        approval = self._json(response).get("approval")
        if not isinstance(approval, dict):
            raise ControlPlaneUnavailable("Control Plane returned an invalid approval")
        return approval

    def consume_managed_approval(
        self,
        *,
        approval_id: str,
        lease: str,
        context: ManagedContext,
        claim_token: str,
        action: ManagedAction,
        expected_version: int,
    ) -> ManagedActionAuthorization:
        response = self._request(
            "POST",
            f"/api/v1/worker/approvals/{approval_id}/consume",
            headers=self._headers(
                lease=lease,
                claim_token=claim_token,
                context=context,
            ),
            json={
                "action": action.to_mapping(),
                "action_digest": action.digest(),
                "expected_version": expected_version,
            },
        )
        response.raise_for_status()
        authorization = self._json(response).get("authorization")
        if not isinstance(authorization, dict):
            raise ControlPlaneUnavailable(
                "Control Plane returned an invalid action authorization"
            )
        receipt = ManagedActionAuthorization.from_mapping(authorization)
        receipt.authorize(action=action, context=context)
        return receipt

    def next_job(
        self,
        *,
        lease: str,
        tenant_id: str,
        store_id: str,
        agent_id: str,
        capability: str | None = None,
    ) -> dict[str, Any] | None:
        self._next_job_authorization_lease = None
        response = self._request(
            "GET",
            "/api/v1/worker/jobs/next",
            headers={
                **self._headers(lease=lease),
                "X-Atlas-Tenant-ID": tenant_id,
                "X-Atlas-Store-ID": store_id,
                "X-Atlas-Agent-ID": agent_id,
            },
            params={"capability": capability} if capability else None,
        )
        response.raise_for_status()
        payload = self._json(response)
        job = payload.get("job")
        if job is None:
            return None
        if not isinstance(job, dict):
            raise ControlPlaneUnavailable("Control Plane returned an invalid job")
        lease_payload = payload.get("authorization_lease")
        if isinstance(lease_payload, dict):
            token = str(lease_payload.get("token") or "").strip()
            expires_at = str(lease_payload.get("expires_at") or "").strip()
            if not token or not expires_at:
                raise ControlPlaneUnavailable(
                    "Control Plane returned an invalid job authorization lease"
                )
            self._next_job_authorization_lease = Lease(
                token=token,
                expires_at=expires_at,
                capabilities=tuple(
                    str(value)
                    for value in lease_payload.get("capabilities", [])
                    if str(value).strip()
                ),
            )
        return job

    def take_job_authorization_lease(self) -> Lease | None:
        """Consume the longer lease attached to the most recent claimed job."""

        lease = self._next_job_authorization_lease
        self._next_job_authorization_lease = None
        return lease

    def ensure_cortex_maintenance(
        self,
        *,
        lease: str,
        tenant_id: str,
        store_id: str,
        agent_id: str,
        dispatch_key: str,
        dispatch_admission: CortexDispatchAdmission,
    ) -> dict[str, Any]:
        """Idempotently request a managed job for one queued local Cortex job."""
        if not isinstance(dispatch_admission, CortexDispatchAdmission):
            raise TypeError("dispatch_admission must be a CortexDispatchAdmission")
        if not dispatch_admission.matches_dispatch_key(dispatch_key):
            raise ValueError("dispatch key does not match Cortex admission")
        response = self._request(
            "POST",
            "/api/v1/worker/jobs/cortex/ensure",
            headers=self._headers(lease=lease),
            json={
                "tenant_id": tenant_id,
                "store_id": store_id,
                "agent_id": agent_id,
                "dispatch_key": dispatch_key,
                "dispatch_admission": dispatch_admission.to_mapping(),
            },
        )
        if response.status_code in {409, 429}:
            payload = self._json(response)
            detail = payload.get("detail")
            code = str(detail.get("code") or "") if isinstance(detail, dict) else ""
            if code in {
                "cortex_dispatch_already_active",
                "cortex_dispatch_daily_limit_exceeded",
            }:
                # Admission denial applies only to creating another Cortex
                # dispatch. The worker must continue polling/executing the
                # already queued job and unrelated entitled work.
                return {
                    "admitted": False,
                    "created": False,
                    "requeued": False,
                    "code": code,
                }
        response.raise_for_status()
        return self._json(response)

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
