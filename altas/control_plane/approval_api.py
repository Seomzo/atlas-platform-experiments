"""Worker and enrolled-phone APIs for exact managed approvals."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from altas.managed.actions import ACTION_SCHEMA_VERSION, ManagedAction

from .approval_repository import (
    ApprovalAccessDenied,
    ApprovalExpired,
    ApprovalNotFound,
    ApprovalRepository,
)
from .config import ControlPlaneSettings
from .policy import PolicyEngine
from .repository import ControlPlaneRepository
from .security import DeviceSessionSigner, InvalidDeviceSession, LeaseClaims


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ManagedActionModel(_StrictModel):
    schema_version: Literal["atlas.managed-action.v1"] = ACTION_SCHEMA_VERSION
    kind: Literal[
        "read",
        "navigate",
        "analyze",
        "draft",
        "download_export",
        "send_message",
        "submit",
        "mutate",
        "credential",
        "administrative",
    ]
    operation: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
    summary: str = Field(min_length=1, max_length=240)
    target_type: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
    target_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")
    target_label: str = Field(min_length=1, max_length=160)


class ApprovalRequest(_StrictModel):
    relay_session_id: str = Field(min_length=1, max_length=128)
    action: ManagedActionModel


class ApprovalResponse(_StrictModel):
    decision: Literal["approve", "deny"]
    reason: Literal["user_approved", "user_denied", "not_recognized", "wrong_target"]
    expected_version: int = Field(ge=1)
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def decision_matches_reason(self) -> "ApprovalResponse":
        if self.decision == "approve" and self.reason != "user_approved":
            raise ValueError("approval reason does not match decision")
        if self.decision == "deny" and self.reason == "user_approved":
            raise ValueError("approval reason does not match decision")
        return self


class ApprovalConsumeRequest(_StrictModel):
    expected_version: int = Field(ge=1)
    action_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    action: ManagedActionModel


PhoneContext = dict[str, dict[str, Any]]


def build_approval_routers(
    *,
    settings: ControlPlaneSettings,
    repository: ControlPlaneRepository,
    approval_repository: ApprovalRepository,
    policy: PolicyEngine,
    device_session_signer: DeviceSessionSigner,
    require_account: Callable[..., dict[str, Any]],
    require_worker_device: Callable[..., dict[str, Any]],
) -> tuple[APIRouter, APIRouter]:
    worker_router = APIRouter(prefix="/api/v1/worker", tags=["managed-approvals"])
    mobile_router = APIRouter(prefix="/api/v1/mobile/relay", tags=["mobile-approvals"])

    def require_phone(
        device_session: Annotated[
            str | None,
            Header(alias="X-Atlas-Device-Session"),
        ] = None,
        user: dict[str, Any] = Depends(require_account),
    ) -> PhoneContext:
        try:
            claims = device_session_signer.verify(device_session or "")
        except InvalidDeviceSession:
            claims = None
        device = (
            repository.authenticate_device_session(
                device_id=claims.device_id,
                credential_version=claims.credential_version,
            )
            if claims is not None
            else None
        )
        if (
            device is None
            or device.get("device_class") != "phone"
            or device.get("enrolled_by_user_id") != user.get("id")
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "phone_device_session_required"},
            )
        return {"user": user, "device": device}

    def approval_error(exc: ValueError) -> HTTPException:
        code = str(exc)
        if isinstance(exc, ApprovalNotFound):
            return HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": code},
            )
        if isinstance(exc, ApprovalAccessDenied):
            return HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": code},
            )
        if isinstance(exc, ApprovalExpired):
            return HTTPException(
                status_code=status.HTTP_410_GONE,
                detail={"code": code},
            )
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": code},
        )

    def authorize_worker(
        *,
        device: dict[str, Any],
        store_id: str,
        agent_id: str,
        job_id: str,
        capability: str,
        lease_token: str,
        claim_token: str,
        audit_action: str,
    ) -> LeaseClaims:
        decision = policy.evaluate(
            authenticated_device=device,
            store_id=store_id,
            agent_id=agent_id,
            capability=capability,
            lease_token=lease_token,
            audit_action=audit_action,
            job_id=job_id,
        )
        if not decision.allowed or decision.lease_claims is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": decision.code},
            )
        job = repository.get_job(job_id)
        if (
            job is None
            or job.get("tenant_id") != device.get("tenant_id")
            or job.get("store_id") != store_id
            or job.get("agent_id") != agent_id
            or job.get("capability") != capability
            or job.get("claimed_by_device_id") != device.get("id")
            or not repository.validate_job_claim(
                job_id,
                device_id=str(device["id"]),
                claim_token=claim_token,
            )
        ):
            repository.record_audit(
                actor_type="worker",
                action=audit_action,
                outcome="denied",
                tenant_id=device.get("tenant_id"),
                store_id=store_id,
                agent_id=agent_id,
                device_id=device.get("id"),
                job_id=job_id,
                resource_type="managed_approval",
                details={"reason": "approval_job_claim_invalid"},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "approval_job_claim_invalid"},
            )
        return decision.lease_claims

    @worker_router.post("/approvals", status_code=status.HTTP_201_CREATED)
    def request_approval(
        request: ApprovalRequest,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=160),
        ],
        store_id: Annotated[str, Header(alias="X-Atlas-Store-ID")],
        agent_id: Annotated[str, Header(alias="X-Atlas-Agent-ID")],
        job_id: Annotated[str, Header(alias="X-Atlas-Job-ID")],
        lease_token: Annotated[str, Header(alias="X-Atlas-Lease")],
        claim_token: Annotated[str, Header(alias="X-Atlas-Claim-Token")],
        device: dict[str, Any] = Depends(require_worker_device),
    ) -> dict[str, Any]:
        job = repository.get_job(job_id)
        capability = str((job or {}).get("capability") or "")
        claims = authorize_worker(
            device=device,
            store_id=store_id,
            agent_id=agent_id,
            job_id=job_id,
            capability=capability,
            lease_token=lease_token,
            claim_token=claim_token,
            audit_action="managed_approval.request.authorize",
        )
        try:
            action = ManagedAction.from_mapping(request.action.model_dump(mode="json"))
            approval, created = approval_repository.create_request(
                worker_device_id=str(device["id"]),
                relay_session_id=request.relay_session_id,
                job_id=job_id,
                claim_token=claim_token,
                lease_claims=claims,
                action=action,
                idempotency_key=idempotency_key,
                ttl_seconds=settings.managed_approval_ttl_seconds,
            )
        except ValueError as exc:
            raise approval_error(exc) from exc
        if created:
            repository.record_audit(
                actor_type="worker",
                action="managed_approval.request",
                outcome="pending",
                tenant_id=device["tenant_id"],
                store_id=store_id,
                agent_id=agent_id,
                device_id=device["id"],
                user_id=None,
                job_id=job_id,
                resource_type="managed_approval",
                resource_id=approval["id"],
                correlation_id=approval["correlation_id"],
                details={
                    "action_kind": approval["action_kind"],
                    "operation": approval["operation"],
                    "target_type": approval["target_type"],
                    "action_digest": approval["action_digest"],
                },
            )
        return {"approval": approval, "idempotent_replay": not created}

    def _worker_approval_context(
        *,
        approval_id: str,
        device: dict[str, Any],
        store_id: str,
        agent_id: str,
        job_id: str,
        lease_token: str,
        claim_token: str,
        audit_action: str,
    ) -> tuple[dict[str, Any], LeaseClaims]:
        try:
            approval = approval_repository.get_for_worker(
                approval_id=approval_id,
                worker_device_id=str(device["id"]),
                job_id=job_id,
            )
        except ApprovalNotFound as exc:
            raise approval_error(exc) from exc
        claims = authorize_worker(
            device=device,
            store_id=store_id,
            agent_id=agent_id,
            job_id=job_id,
            capability=str(approval["capability"]),
            lease_token=lease_token,
            claim_token=claim_token,
            audit_action=audit_action,
        )
        return approval, claims

    @worker_router.get("/approvals/{approval_id}")
    def get_worker_approval(
        approval_id: str,
        store_id: Annotated[str, Header(alias="X-Atlas-Store-ID")],
        agent_id: Annotated[str, Header(alias="X-Atlas-Agent-ID")],
        job_id: Annotated[str, Header(alias="X-Atlas-Job-ID")],
        lease_token: Annotated[str, Header(alias="X-Atlas-Lease")],
        claim_token: Annotated[str, Header(alias="X-Atlas-Claim-Token")],
        device: dict[str, Any] = Depends(require_worker_device),
    ) -> dict[str, Any]:
        approval, _ = _worker_approval_context(
            approval_id=approval_id,
            device=device,
            store_id=store_id,
            agent_id=agent_id,
            job_id=job_id,
            lease_token=lease_token,
            claim_token=claim_token,
            audit_action="managed_approval.read.authorize",
        )
        return {"approval": approval}

    @worker_router.post("/approvals/{approval_id}/consume")
    def consume_approval(
        approval_id: str,
        request: ApprovalConsumeRequest,
        store_id: Annotated[str, Header(alias="X-Atlas-Store-ID")],
        agent_id: Annotated[str, Header(alias="X-Atlas-Agent-ID")],
        job_id: Annotated[str, Header(alias="X-Atlas-Job-ID")],
        lease_token: Annotated[str, Header(alias="X-Atlas-Lease")],
        claim_token: Annotated[str, Header(alias="X-Atlas-Claim-Token")],
        device: dict[str, Any] = Depends(require_worker_device),
    ) -> dict[str, Any]:
        stored, claims = _worker_approval_context(
            approval_id=approval_id,
            device=device,
            store_id=store_id,
            agent_id=agent_id,
            job_id=job_id,
            lease_token=lease_token,
            claim_token=claim_token,
            audit_action="managed_approval.consume.authorize",
        )
        try:
            action = ManagedAction.from_mapping(request.action.model_dump(mode="json"))
            approval = approval_repository.consume(
                approval_id=approval_id,
                worker_device_id=str(device["id"]),
                job_id=job_id,
                claim_token=claim_token,
                lease_claims=claims,
                action=action,
                expected_version=request.expected_version,
                action_digest=request.action_digest,
            )
        except ValueError as exc:
            raise approval_error(exc) from exc
        repository.record_audit(
            actor_type="worker",
            action="managed_approval.consume",
            outcome="consumed",
            tenant_id=device["tenant_id"],
            store_id=store_id,
            agent_id=agent_id,
            device_id=device["id"],
            user_id=None,
            job_id=job_id,
            resource_type="managed_approval",
            resource_id=approval_id,
            correlation_id=stored["correlation_id"],
            details={
                "action_digest": approval["action_digest"],
                "policy_version": approval["policy_version"],
            },
        )
        return {
            "approval": approval,
            "authorization": {
                "approval_id": approval["id"],
                "action_digest": approval["action_digest"],
                "job_id": approval["job_id"],
                "job_attempt": approval["job_attempt"],
                "policy_version": approval["policy_version"],
                "consumed_at": approval["consumed_at"],
            },
        }

    @mobile_router.get("/approvals")
    def list_mobile_approvals(
        relay_session_id: Annotated[str | None, Query(max_length=128)] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        context: PhoneContext = Depends(require_phone),
    ) -> dict[str, Any]:
        items = approval_repository.list_for_phone(
            user_id=str(context["user"]["id"]),
            phone_device_id=str(context["device"]["id"]),
            relay_session_id=relay_session_id,
            limit=limit,
        )
        return {"items": items, "count": len(items)}

    @mobile_router.get("/approvals/{approval_id}")
    def get_mobile_approval(
        approval_id: str,
        context: PhoneContext = Depends(require_phone),
    ) -> dict[str, Any]:
        try:
            approval = approval_repository.get_for_phone(
                approval_id=approval_id,
                user_id=str(context["user"]["id"]),
                phone_device_id=str(context["device"]["id"]),
            )
        except ApprovalNotFound as exc:
            raise approval_error(exc) from exc
        repository.record_audit(
            actor_type="phone",
            action="managed_approval.view",
            outcome="succeeded",
            tenant_id=context["device"]["tenant_id"],
            store_id=approval["store_id"],
            agent_id=approval["agent_id"],
            device_id=context["device"]["id"],
            user_id=context["user"]["id"],
            job_id=approval["job_id"],
            resource_type="managed_approval",
            resource_id=approval_id,
            correlation_id=approval["correlation_id"],
            details={"status": approval["status"], "version": approval["version"]},
        )
        return {"approval": approval}

    @mobile_router.post("/approvals/{approval_id}/responses")
    def respond_mobile_approval(
        approval_id: str,
        request: ApprovalResponse,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=160),
        ],
        context: PhoneContext = Depends(require_phone),
    ) -> dict[str, Any]:
        try:
            approval, created = approval_repository.respond(
                approval_id=approval_id,
                user_id=str(context["user"]["id"]),
                phone_device_id=str(context["device"]["id"]),
                decision=request.decision,
                decision_reason=request.reason,
                expected_version=request.expected_version,
                action_digest=request.action_digest,
                idempotency_key=idempotency_key,
            )
        except ValueError as exc:
            raise approval_error(exc) from exc
        if created:
            repository.record_audit(
                actor_type="phone",
                action="managed_approval.respond",
                outcome=approval["status"],
                tenant_id=context["device"]["tenant_id"],
                store_id=approval["store_id"],
                agent_id=approval["agent_id"],
                device_id=context["device"]["id"],
                user_id=context["user"]["id"],
                job_id=approval["job_id"],
                resource_type="managed_approval",
                resource_id=approval_id,
                correlation_id=approval["correlation_id"],
                details={
                    "decision": request.decision,
                    "reason": request.reason,
                    "action_digest": approval["action_digest"],
                },
            )
        return {"approval": approval, "idempotent_replay": not created}

    return worker_router, mobile_router
