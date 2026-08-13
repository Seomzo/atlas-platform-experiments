"""FastAPI application factory for the Atlas local control plane."""

from __future__ import annotations

import hmac
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles

from altas.cortex.managed_dispatch import (
    CortexDispatchAdmission as CoreCortexDispatchAdmission,
    dispatch_key_commitment,
)

from .config import ControlPlaneSettings
from .database import Database
from .identity import (
    DeterministicIdentityProvider,
    IdentityVerificationError,
    IdentityVerifier,
    UnconfiguredIdentityProvider,
)
from .model_gateway import ModelGateway, ModelGatewayError
from .policy import PolicyDecision, PolicyDenied, PolicyEngine, not_expired
from .relay_api import build_relay_router
from .relay_repository import RelayRepository
from .relay_security import RelayCursorSigner, RelayPayloadCipher
from .repository import (
    AccountAccessDenied,
    ControlPlaneRepository,
    CortexDispatchLimitExceeded,
    DEMO_USER_SUBJECT,
    DemoSeed,
    DeviceCredentialConflict,
    EnrollmentNotRedeemable,
    IdempotencyConflict,
    InvalidJobClaim,
    ModelUsageLimitExceeded,
)
from .schemas import (
    ChatCompletionRequest,
    CortexMaintenanceRequest,
    DevIdentityTokenRequest,
    DeviceCredentialRotationRequest,
    DeviceEnrollmentRedemptionRequest,
    DeviceEnrollmentRequest,
    DeviceRevocationRequest,
    DeviceSessionRequest,
    HeartbeatRequest,
    JobCompletionRequest,
    PolicyEvaluationRequest,
    QueueJobRequest,
    RequeueJobRequest,
    ToggleRequest,
)
from .security import (
    DeviceSessionSigner,
    InvalidDeviceProof,
    InvalidDeviceSession,
    LeaseSigner,
    device_key_rotation_challenge,
    device_public_key_thumbprint,
    device_session_challenge,
    hash_secret,
    verify_device_signature,
)


AdminResource = Literal[
    "tenants",
    "stores",
    "subscriptions",
    "devices",
    "agents",
    "entitlements",
    "jobs",
    "usage_events",
    "audit_logs",
]
ToggleResource = Literal["stores", "subscriptions", "devices", "agents", "entitlements"]

# Dependencies are intentionally static and narrow. A job grants its own
# capability plus only the infrastructure capability explicitly required by
# that workflow; an arbitrary entitled capability is never enough.
JOB_CAPABILITY_DEPENDENCIES: dict[str, frozenset[str]] = {
    "fixed_ops.daily_report": frozenset({"model.chat"}),
    "cortex.memory_maintenance": frozenset({"model.chat"}),
}


def _job_allows_capability(job_capability: str, requested_capability: str) -> bool:
    return requested_capability == job_capability or requested_capability in (
        JOB_CAPABILITY_DEPENDENCIES.get(job_capability, frozenset())
    )


def _require_cortex_dispatch_admission(
    repository: ControlPlaneRepository,
    job: dict[str, Any],
) -> CoreCortexDispatchAdmission:
    """Require the dedicated ledger row and exact persisted Cortex payload."""

    if job.get("capability") != "cortex.memory_maintenance":
        raise ValueError("job is not Cortex maintenance")
    payload = job.get("payload")
    if not isinstance(payload, dict) or set(payload) != {"dispatch_admission"}:
        raise ValueError("invalid Cortex dispatch payload")
    admission = CoreCortexDispatchAdmission.from_mapping(payload["dispatch_admission"])
    if not admission.is_canonical():
        raise ValueError("non-canonical Cortex dispatch admission")
    if not repository.has_cortex_dispatch_admission(str(job.get("id") or "")):
        raise ValueError("missing Cortex dispatch admission")
    return admission


def _policy_error(decision: PolicyDecision) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": decision.code, "message": "control-plane policy denied"},
    )


def create_app(
    settings: ControlPlaneSettings | None = None,
    *,
    identity_verifier: IdentityVerifier | None = None,
) -> FastAPI:
    """Create an isolated control-plane application.

    Supplying settings makes tests and embedded deployments deterministic. The
    no-argument factory reads process environment configuration.
    """

    settings = settings or ControlPlaneSettings.from_env()
    database = Database(settings.database_path)
    database.initialize()
    repository = ControlPlaneRepository(database)
    relay_payload_cipher = RelayPayloadCipher(settings.lease_signing_key)
    relay_repository = RelayRepository(database, relay_payload_cipher)
    demo_seed: DemoSeed | None = (
        repository.seed_demo() if settings.seed_demo_data else None
    )
    lease_signer = LeaseSigner(settings.lease_signing_key)
    device_session_signer = DeviceSessionSigner(settings.lease_signing_key)
    relay_cursor_signer = RelayCursorSigner(settings.lease_signing_key)
    if identity_verifier is None:
        identity_verifier = (
            DeterministicIdentityProvider(settings.lease_signing_key)
            if settings.seed_demo_data
            else UnconfiguredIdentityProvider()
        )
    policy = PolicyEngine(
        repository,
        lease_signer,
        lease_ttl_seconds=settings.lease_ttl_seconds,
    )
    model_gateway = ModelGateway(settings)
    expected_admin_hash = hash_secret(settings.admin_token)

    app = FastAPI(
        title="Atlas Control Plane",
        version="0.1.0",
        description=(
            "Local-first control plane for licensed, policy-bound Atlas managed workers."
        ),
    )
    app.state.settings = settings
    app.state.database = database
    app.state.repository = repository
    app.state.lease_signer = lease_signer
    app.state.device_session_signer = device_session_signer
    app.state.relay_repository = relay_repository
    app.state.relay_cursor_signer = relay_cursor_signer
    app.state.identity_verifier = identity_verifier
    app.state.policy = policy
    app.state.demo_seed = demo_seed
    static_directory = Path(__file__).with_name("static")

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        client_host = request.client.host if request.client else ""
        loopback_hosts = {
            "127.0.0.1",
            "::1",
            "localhost",
            "testclient",
        }
        is_loopback = client_host in loopback_hosts
        path = request.url.path
        is_admin_surface = (
            path == "/"
            or path == "/static"
            or path.startswith("/static/")
            or path == "/api/v1/admin"
            or path.startswith("/api/v1/admin/")
        )
        if settings.seed_demo_data and not is_loopback:
            response = JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": {"code": "demo_loopback_only"}},
            )
        elif is_admin_surface and not is_loopback:
            response = JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": {"code": "admin_loopback_only"}},
            )
        else:
            response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=()",
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            (
                "default-src 'self'; "
                "base-uri 'none'; "
                "connect-src 'self'; "
                "font-src 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none'; "
                "img-src 'self' data:; "
                "object-src 'none'; "
                "script-src 'self'; "
                "style-src 'self' 'unsafe-inline'"
            ),
        )
        if request.url.path.startswith(("/api/", "/v1/")):
            response.headers["Cache-Control"] = "no-store"
        return response

    device_bearer = HTTPBearer(auto_error=False, scheme_name="AtlasDeviceBearer")
    account_bearer = HTTPBearer(auto_error=False, scheme_name="AtlasAccountBearer")
    admin_bearer = HTTPBearer(auto_error=False, scheme_name="AtlasAdminBearer")

    def require_device(
        credentials: HTTPAuthorizationCredentials | None = Depends(device_bearer),
    ) -> dict[str, Any]:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="device_bearer_required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        token = credentials.credentials
        device: dict[str, Any] | None
        if token.startswith("atlas-device-session-v1."):
            try:
                claims = device_session_signer.verify(token)
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
            if device is not None and claims is not None:
                device["_credential_version"] = claims.credential_version
                device["_authentication_kind"] = "device_session"
        else:
            device = repository.authenticate_device(token)
            if device is not None:
                device["_authentication_kind"] = "legacy_bearer"
        if device is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="device_authentication_failed",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return device

    def require_worker_device(
        device: dict[str, Any] = Depends(require_device),
    ) -> dict[str, Any]:
        if device.get("device_class") != "worker":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "worker_device_required"},
            )
        return device

    def require_account(
        credentials: HTTPAuthorizationCredentials | None = Depends(account_bearer),
    ) -> dict[str, Any]:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="account_bearer_required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        try:
            identity = identity_verifier.verify(credentials.credentials)
        except IdentityVerificationError:
            identity = None
        user = (
            repository.get_user_by_identity(
                issuer=identity.issuer,
                subject=identity.subject,
            )
            if identity is not None
            else None
        )
        if user is None or user.get("status") != "active":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="account_authentication_failed",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user

    def require_admin(
        credentials: HTTPAuthorizationCredentials | None = Depends(admin_bearer),
    ) -> None:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="admin_bearer_required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        supplied_hash = hash_secret(credentials.credentials)
        if not hmac.compare_digest(expected_admin_hash, supplied_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="admin_authentication_failed",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def authorize(
        *,
        device: dict[str, Any],
        store_id: str,
        agent_id: str,
        capability: str,
        lease_token: str,
        audit_action: str,
        job_id: str | None = None,
    ) -> PolicyDecision:
        decision = policy.evaluate(
            authenticated_device=device,
            store_id=store_id,
            agent_id=agent_id,
            capability=capability,
            lease_token=lease_token,
            audit_action=audit_action,
            job_id=job_id,
        )
        if not decision.allowed:
            raise _policy_error(decision)
        return decision

    def audit_worker_denial(
        *,
        action: str,
        device: dict[str, Any],
        store_id: str | None,
        agent_id: str | None,
        reason: str,
        resource_type: str | None = None,
        resource_id: str | None = None,
        job_id: str | None = None,
    ) -> None:
        repository.record_audit(
            actor_type="worker",
            action=action,
            outcome="denied",
            tenant_id=device.get("tenant_id"),
            store_id=store_id,
            agent_id=agent_id,
            device_id=device.get("id"),
            job_id=job_id,
            resource_type=resource_type,
            resource_id=resource_id,
            details={"reason": reason},
        )

    @app.get("/health", tags=["system"])
    def health() -> dict[str, Any]:
        healthy = database.healthy()
        return {
            "status": "ok" if healthy else "degraded",
            "database": "connected" if healthy else "unavailable",
            "model_mode": "mock" if settings.mock_model else "upstream",
        }

    @app.get("/", include_in_schema=False)
    def control_center() -> FileResponse:
        return FileResponse(
            static_directory / "index.html",
            headers={"Cache-Control": "no-store"},
        )

    if settings.seed_demo_data and isinstance(
        identity_verifier, DeterministicIdentityProvider
    ):

        @app.post("/api/v1/dev/identity/token", tags=["development"])
        def issue_development_identity(
            request: DevIdentityTokenRequest,
            _: None = Depends(require_admin),
        ) -> dict[str, Any]:
            """Mint a local assertion; never registered outside demo mode."""

            user = repository.get_user_by_identity(
                issuer=identity_verifier.issuer,
                subject=request.subject,
            )
            if user is None or user.get("status") != "active":
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"code": "development_identity_not_found"},
                )
            token, expires_at = identity_verifier.issue(
                subject=request.subject,
                ttl_seconds=settings.account_session_ttl_seconds,
            )
            return {
                "access_token": token,
                "token_type": "Bearer",
                "expires_at": expires_at,
                "expires_in": settings.account_session_ttl_seconds,
            }

    account = APIRouter(prefix="/api/v1/account", tags=["account"])

    @account.get("/context")
    def account_context(
        user: dict[str, Any] = Depends(require_account),
    ) -> dict[str, Any]:
        return {
            "user": {
                "id": user["id"],
                "display_name": user.get("display_name"),
                "email": user.get("email"),
            },
            "memberships": repository.list_account_memberships(user["id"]),
        }

    @account.post("/enrollments", status_code=status.HTTP_201_CREATED)
    def create_enrollment(
        request: DeviceEnrollmentRequest,
        user: dict[str, Any] = Depends(require_account),
    ) -> dict[str, Any]:
        try:
            enrollment, enrollment_token = repository.create_device_enrollment(
                user_id=user["id"],
                store_id=request.store_id,
                device_class=request.device_class,
                device_name=request.device_name,
                agent_id=request.agent_id,
                ttl_seconds=settings.enrollment_ttl_seconds,
            )
        except AccountAccessDenied as exc:
            repository.record_audit(
                actor_type="account",
                action="device.enrollment.create",
                outcome="denied",
                user_id=user["id"],
                resource_type="store",
                details={"reason": str(exc)},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": str(exc)},
            ) from exc
        repository.record_audit(
            actor_type="account",
            action="device.enrollment.create",
            outcome="succeeded",
            tenant_id=enrollment["tenant_id"],
            store_id=enrollment["store_id"],
            agent_id=enrollment.get("agent_id"),
            user_id=user["id"],
            resource_type="device_enrollment",
            resource_id=enrollment["id"],
            correlation_id=enrollment["correlation_id"],
            details={"device_class": enrollment["device_class"]},
        )
        return {
            "enrollment": enrollment,
            "redemption": {
                "token": enrollment_token,
                "algorithm": "Ed25519",
                "expires_at": enrollment["expires_at"],
                "one_time": True,
            },
        }

    @account.get("/devices")
    def list_account_devices(
        store_id: str = Query(..., min_length=1, max_length=128),
        user: dict[str, Any] = Depends(require_account),
    ) -> dict[str, Any]:
        try:
            items = repository.list_account_devices(
                user_id=user["id"], store_id=store_id
            )
        except AccountAccessDenied as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "store_access_denied"},
            ) from exc
        return {"items": items, "count": len(items)}

    @account.post("/devices/{device_id}/revoke")
    async def revoke_account_device(
        device_id: str,
        request: DeviceRevocationRequest,
        user: dict[str, Any] = Depends(require_account),
    ) -> dict[str, Any]:
        try:
            device = repository.revoke_account_device(
                user_id=user["id"], device_id=device_id
            )
        except AccountAccessDenied as exc:
            repository.record_audit(
                actor_type="account",
                action="device.revoke",
                outcome="denied",
                user_id=user["id"],
                resource_type="device",
                details={"reason": "device_not_found"},
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "device_not_found"},
            ) from exc
        repository.record_audit(
            actor_type="account",
            action="device.revoke",
            outcome="succeeded",
            tenant_id=device["tenant_id"],
            store_id=device["store_id"],
            device_id=device["id"],
            user_id=user["id"],
            resource_type="device",
            resource_id=device["id"],
            details={
                "reason": request.reason,
                "terminated_job_count": device["terminated_job_count"],
            },
        )
        relay_repository.revoke_device_scope(device["id"])
        await relay_hub.disconnect_worker(device["id"], reason="device_revoked")
        return {"device": device}

    app.include_router(account)

    device_api = APIRouter(prefix="/api/v1/device", tags=["device-identity"])

    @device_api.post(
        "/enrollments/redeem",
        status_code=status.HTTP_201_CREATED,
    )
    def redeem_device_enrollment(
        request: DeviceEnrollmentRedemptionRequest,
    ) -> dict[str, Any]:
        try:
            thumbprint = device_public_key_thumbprint(request.public_key)
            device, enrollment = repository.redeem_device_enrollment(
                enrollment_token=request.enrollment_token,
                public_key_b64=request.public_key,
                public_key_thumbprint=thumbprint,
                platform=request.platform,
                platform_version=request.platform_version,
                app_version=request.app_version,
                credential_ttl_seconds=settings.device_credential_ttl_seconds,
            )
        except InvalidDeviceProof as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "device_key_invalid"},
            ) from exc
        except EnrollmentNotRedeemable as exc:
            repository.record_audit(
                actor_type="device",
                action="device.enrollment.redeem",
                outcome="denied",
                resource_type="device_enrollment",
                details={"reason": "enrollment_not_redeemable"},
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "enrollment_not_redeemable"},
            ) from exc
        except DeviceCredentialConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "device_key_already_registered"},
            ) from exc
        repository.record_audit(
            actor_type="device",
            action="device.enrollment.redeem",
            outcome="succeeded",
            tenant_id=device["tenant_id"],
            store_id=device["store_id"],
            agent_id=enrollment.get("agent_id"),
            device_id=device["id"],
            user_id=enrollment["created_by_user_id"],
            resource_type="device_enrollment",
            resource_id=enrollment["id"],
            correlation_id=enrollment["correlation_id"],
            details={
                "credential_kind": "ed25519",
                "device_class": device["device_class"],
                "public_key_thumbprint": device["public_key_thumbprint"],
            },
        )
        return {"device": device, "enrollment": enrollment}

    def deny_device_proof(device_id: str) -> HTTPException:
        device = repository.get_device(device_id)
        repository.record_audit(
            actor_type="device",
            action="device.session.issue",
            outcome="denied",
            tenant_id=(device or {}).get("tenant_id"),
            store_id=(device or {}).get("store_id"),
            device_id=(device or {}).get("id"),
            resource_type="device",
            resource_id=(device or {}).get("id"),
            details={"reason": "device_proof_invalid"},
        )
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "device_proof_invalid"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    @device_api.post("/sessions")
    def create_device_session(request: DeviceSessionRequest) -> dict[str, Any]:
        now_epoch = int(time.time())
        if abs(now_epoch - request.timestamp) > settings.device_proof_max_skew_seconds:
            raise deny_device_proof(request.device_id)
        credential = repository.get_device_credential(request.device_id)
        if (
            credential is None
            or credential.get("credential_kind") != "ed25519"
            or not credential.get("public_key_b64")
            or not credential.get("public_key_thumbprint")
        ):
            raise deny_device_proof(request.device_id)
        try:
            verify_device_signature(
                public_key_b64=credential["public_key_b64"],
                signature_b64=request.signature,
                message=device_session_challenge(
                    device_id=request.device_id,
                    timestamp=request.timestamp,
                    nonce=request.nonce,
                ),
            )
        except InvalidDeviceProof as exc:
            raise deny_device_proof(request.device_id) from exc
        nonce_expires_at = (
            (
                datetime.now(UTC)
                + timedelta(seconds=settings.device_proof_max_skew_seconds)
            )
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        consumed = repository.consume_device_proof_nonce(
            device_id=request.device_id,
            credential_version=int(credential["credential_version"]),
            public_key_thumbprint=str(credential["public_key_thumbprint"]),
            nonce=request.nonce,
            purpose="session",
            nonce_expires_at=nonce_expires_at,
        )
        if not consumed:
            raise deny_device_proof(request.device_id)
        access_token, claims = device_session_signer.issue(
            device_id=request.device_id,
            credential_version=int(credential["credential_version"]),
            ttl_seconds=settings.device_session_ttl_seconds,
            nonce=secrets.token_hex(16),
        )
        repository.record_audit(
            actor_type="device",
            action="device.session.issue",
            outcome="succeeded",
            tenant_id=credential["tenant_id"],
            store_id=credential["store_id"],
            device_id=credential["id"],
            resource_type="device",
            resource_id=credential["id"],
            details={
                "credential_version": credential["credential_version"],
                "expires_at": claims.expires_at,
            },
        )
        return {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_at": claims.expires_at,
            "expires_in": claims.expires_at - claims.issued_at,
            "device": {
                "id": credential["id"],
                "device_class": credential["device_class"],
                "tenant_id": credential["tenant_id"],
                "store_id": credential["store_id"],
            },
        }

    @device_api.post("/credentials/rotate")
    def rotate_device_credential(
        request: DeviceCredentialRotationRequest,
        device: dict[str, Any] = Depends(require_device),
    ) -> dict[str, Any]:
        if device.get("_authentication_kind") != "device_session":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "device_session_required"},
            )
        now_epoch = int(time.time())
        if abs(now_epoch - request.timestamp) > settings.device_proof_max_skew_seconds:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "device_proof_invalid"},
            )
        try:
            new_thumbprint = device_public_key_thumbprint(request.new_public_key)
            verify_device_signature(
                public_key_b64=request.new_public_key,
                signature_b64=request.signature,
                message=device_key_rotation_challenge(
                    device_id=device["id"],
                    new_public_key_b64=request.new_public_key,
                    timestamp=request.timestamp,
                    nonce=request.nonce,
                ),
            )
        except InvalidDeviceProof as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "device_proof_invalid"},
            ) from exc
        if hmac.compare_digest(
            str(device.get("public_key_thumbprint") or ""), new_thumbprint
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "device_credential_unchanged"},
            )
        now_datetime = datetime.now(UTC)
        nonce_expires_at = (
            (now_datetime + timedelta(seconds=settings.device_proof_max_skew_seconds))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        credential_expires_at = (
            (now_datetime + timedelta(seconds=settings.device_credential_ttl_seconds))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        try:
            rotated = repository.rotate_device_credential(
                device_id=device["id"],
                expected_version=int(device["_credential_version"]),
                new_public_key_b64=request.new_public_key,
                new_public_key_thumbprint=new_thumbprint,
                nonce=request.nonce,
                nonce_expires_at=nonce_expires_at,
                credential_expires_at=credential_expires_at,
            )
        except DeviceCredentialConflict as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": str(exc)},
            ) from exc
        access_token, claims = device_session_signer.issue(
            device_id=rotated["id"],
            credential_version=int(rotated["credential_version"]),
            ttl_seconds=settings.device_session_ttl_seconds,
            nonce=secrets.token_hex(16),
        )
        repository.record_audit(
            actor_type="device",
            action="device.credential.rotate",
            outcome="succeeded",
            tenant_id=rotated["tenant_id"],
            store_id=rotated["store_id"],
            device_id=rotated["id"],
            resource_type="device",
            resource_id=rotated["id"],
            details={
                "credential_version": rotated["credential_version"],
                "public_key_thumbprint": rotated["public_key_thumbprint"],
            },
        )
        return {
            "device": rotated,
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_at": claims.expires_at,
            "expires_in": claims.expires_at - claims.issued_at,
        }

    app.include_router(device_api)

    relay_router, relay_hub = build_relay_router(
        settings=settings,
        repository=repository,
        relay_repository=relay_repository,
        device_session_signer=device_session_signer,
        cursor_signer=relay_cursor_signer,
        require_account=require_account,
    )
    app.state.relay_hub = relay_hub
    app.include_router(relay_router)

    worker = APIRouter(prefix="/api/v1/worker", tags=["worker"])

    @worker.post("/heartbeat")
    def heartbeat(
        request: HeartbeatRequest,
        device: dict[str, Any] = Depends(require_worker_device),
    ) -> dict[str, Any]:
        # Tenant identity always originates from bearer authentication. The
        # duplicated body field protects callers from accidentally crossing
        # contexts and is never trusted as authority.
        if request.tenant_id != device["tenant_id"]:
            repository.record_audit(
                actor_type="worker",
                action="lease.issue",
                outcome="denied",
                tenant_id=device["tenant_id"],
                store_id=request.store_id,
                agent_id=request.agent_id,
                device_id=device["id"],
                resource_type="lease",
                details={"reason": "tenant_context_mismatch"},
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "tenant_context_mismatch"},
            )
        try:
            lease_token, claims = policy.issue_lease(
                authenticated_device=device,
                store_id=request.store_id,
                agent_id=request.agent_id,
            )
        except PolicyDenied as exc:
            raise _policy_error(exc.decision) from exc
        updated_device = repository.update_heartbeat(
            device["id"],
            worker_version=request.worker_version,
            health_status=request.health_status,
            metadata=request.metadata,
        )
        return {
            "device": updated_device,
            "lease": {
                "token": lease_token,
                "issued_at": claims.issued_at,
                "expires_at": claims.expires_at,
                "expires_in": claims.expires_at - claims.issued_at,
                "capabilities": list(claims.capabilities),
            },
        }

    @worker.post("/policy/evaluate")
    def evaluate_policy(
        request: PolicyEvaluationRequest,
        device: dict[str, Any] = Depends(require_worker_device),
        lease_token: str = Header(..., alias="X-Atlas-Lease"),
        job_id: str = Header(..., alias="X-Atlas-Job-ID"),
        claim_token: str = Header(
            ..., min_length=32, max_length=256, alias="X-Atlas-Claim-Token"
        ),
    ) -> dict[str, Any]:
        """Managed-runtime policy decision point.

        Tenant identity is intentionally absent from the request model and is
        derived exclusively from the authenticated device.
        """

        job = repository.get_job(job_id)
        if (
            not job
            or job["status"] != "running"
            or job["tenant_id"] != device["tenant_id"]
            or job["store_id"] != request.store_id
            or job["agent_id"] != request.agent_id
            or job["claimed_by_device_id"] != device["id"]
        ):
            audit_worker_denial(
                action="policy.evaluate",
                device=device,
                store_id=request.store_id,
                agent_id=request.agent_id,
                reason="job_context_mismatch",
                resource_type="job",
                resource_id=job_id,
                job_id=job_id,
            )
            return {
                "allowed": False,
                "code": "job_context_mismatch",
                "tenant_id": device["tenant_id"],
                "store_id": request.store_id,
                "agent_id": request.agent_id,
                "capability": request.capability,
            }
        if not repository.validate_job_claim(
            job_id,
            device_id=device["id"],
            claim_token=claim_token,
        ):
            audit_worker_denial(
                action="policy.evaluate",
                device=device,
                store_id=request.store_id,
                agent_id=request.agent_id,
                reason="job_claim_invalid",
                resource_type="job",
                resource_id=job_id,
                job_id=job_id,
            )
            return {
                "allowed": False,
                "code": "job_claim_invalid",
                "tenant_id": device["tenant_id"],
                "store_id": request.store_id,
                "agent_id": request.agent_id,
                "capability": request.capability,
            }
        if not _job_allows_capability(job["capability"], request.capability):
            audit_worker_denial(
                action="policy.evaluate",
                device=device,
                store_id=request.store_id,
                agent_id=request.agent_id,
                reason="job_capability_mismatch",
                resource_type="capability",
                resource_id=request.capability,
                job_id=job_id,
            )
            return {
                "allowed": False,
                "code": "job_capability_mismatch",
                "tenant_id": device["tenant_id"],
                "store_id": request.store_id,
                "agent_id": request.agent_id,
                "capability": request.capability,
            }
        decision = policy.evaluate(
            authenticated_device=device,
            store_id=request.store_id,
            agent_id=request.agent_id,
            capability=request.capability,
            lease_token=lease_token,
            audit_action="policy.evaluate",
            job_id=job_id,
        )
        if not decision.allowed and request.capability == job["capability"]:
            repository.release_job(
                job_id,
                device_id=device["id"],
                claim_token=claim_token,
            )
        return {
            "allowed": decision.allowed,
            "code": decision.code,
            "tenant_id": device["tenant_id"],
            "store_id": request.store_id,
            "agent_id": request.agent_id,
            "capability": request.capability,
        }

    @worker.post("/jobs/cortex/ensure")
    def ensure_cortex_maintenance(
        request: CortexMaintenanceRequest,
        device: dict[str, Any] = Depends(require_worker_device),
        lease_token: str = Header(..., alias="X-Atlas-Lease"),
    ) -> dict[str, Any]:
        """Queue one idempotent, claim-bound Cortex maintenance dispatch.

        The worker derives ``dispatch_key`` from its private local Cortex job;
        it is an opaque digest, not a brain ID or memory payload.  Authority
        still comes exclusively from the authenticated device, current lease,
        assignment, subscription, and Cortex entitlement.
        """
        if request.tenant_id != device["tenant_id"]:
            audit_worker_denial(
                action="cortex.maintenance.request",
                device=device,
                store_id=request.store_id,
                agent_id=request.agent_id,
                reason="tenant_context_mismatch",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "tenant_context_mismatch"},
            )
        authorize(
            device=device,
            store_id=request.store_id,
            agent_id=request.agent_id,
            capability="cortex.memory_maintenance",
            lease_token=lease_token,
            audit_action="cortex.maintenance.request",
        )
        try:
            job, created, requeued = repository.ensure_cortex_maintenance_job(
                tenant_id=device["tenant_id"],
                store_id=request.store_id,
                agent_id=request.agent_id,
                device_id=device["id"],
                dispatch_key=request.dispatch_key,
                dispatch_admission=request.dispatch_admission.model_dump(mode="json"),
                max_jobs_per_24h=settings.cortex_max_jobs_per_device_per_24h,
            )
        except CortexDispatchLimitExceeded as exc:
            code = str(exc)
            audit_worker_denial(
                action="cortex.maintenance.request",
                device=device,
                store_id=request.store_id,
                agent_id=request.agent_id,
                reason=code,
            )
            response_status = (
                status.HTTP_409_CONFLICT
                if code == "cortex_dispatch_already_active"
                else status.HTTP_429_TOO_MANY_REQUESTS
            )
            raise HTTPException(
                status_code=response_status,
                detail={"code": code},
            ) from exc
        except IdempotencyConflict as exc:
            # The server owns every other field in this request, so a conflict
            # would indicate corrupted state rather than caller variation.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "cortex_dispatch_conflict"},
            ) from exc
        repository.record_audit(
            actor_type="worker",
            action="cortex.maintenance.queue",
            outcome="succeeded",
            tenant_id=device["tenant_id"],
            store_id=request.store_id,
            agent_id=request.agent_id,
            device_id=device["id"],
            job_id=job["id"],
            resource_type="job",
            resource_id=job["id"],
            details={"created": created, "requeued": requeued},
        )
        return {"job_id": job["id"], "created": created, "requeued": requeued}

    @worker.get("/jobs/next")
    def next_job(
        device: dict[str, Any] = Depends(require_worker_device),
        capability: str | None = Query(default=None, min_length=1, max_length=120),
        tenant_id: str = Header(..., alias="X-Atlas-Tenant-ID"),
        store_id: str = Header(..., alias="X-Atlas-Store-ID"),
        agent_id: str = Header(..., alias="X-Atlas-Agent-ID"),
        lease_token: str = Header(..., alias="X-Atlas-Lease"),
    ) -> dict[str, Any]:
        if tenant_id != device["tenant_id"]:
            audit_worker_denial(
                action="jobs.poll",
                device=device,
                store_id=store_id,
                agent_id=agent_id,
                reason="tenant_context_mismatch",
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "tenant_context_mismatch"},
            )
        poll_decision = authorize(
            device=device,
            store_id=store_id,
            agent_id=agent_id,
            capability="jobs.poll",
            lease_token=lease_token,
            audit_action="jobs.poll",
        )
        claims = poll_decision.lease_claims
        allowed_capabilities = claims.capabilities if claims else ()
        if capability:
            if capability not in allowed_capabilities:
                audit_worker_denial(
                    action="jobs.poll",
                    device=device,
                    store_id=store_id,
                    agent_id=agent_id,
                    reason="capability_not_leased",
                    resource_type="capability",
                    resource_id=capability,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "capability_not_leased"},
                )
            allowed_capabilities = (capability,)
        job = repository.claim_next_job(
            tenant_id=device["tenant_id"],
            store_id=store_id,
            agent_id=agent_id,
            device_id=device["id"],
            allowed_capabilities=allowed_capabilities,
            visibility_timeout_seconds=settings.job_visibility_timeout_seconds,
        )
        if job is None:
            return {"job": None}
        decision = policy.evaluate(
            authenticated_device=device,
            store_id=store_id,
            agent_id=agent_id,
            capability=job["capability"],
            lease_token=lease_token,
            audit_action="jobs.dispatch",
            job_id=job["id"],
        )
        if not decision.allowed:
            repository.release_job(
                job["id"],
                device_id=device["id"],
                claim_token=job["claim_token"],
            )
            raise _policy_error(decision)
        repository.record_audit(
            actor_type="worker",
            action="job.claim",
            outcome="succeeded",
            tenant_id=device["tenant_id"],
            store_id=store_id,
            agent_id=agent_id,
            device_id=device["id"],
            job_id=job["id"],
            resource_type="job",
            resource_id=job["id"],
            details={"capability": job["capability"]},
        )
        authorization_lease = None
        if job["capability"] == "cortex.memory_maintenance":
            try:
                _require_cortex_dispatch_admission(repository, job)
            except (KeyError, TypeError, ValueError):
                repository.quarantine_cortex_dispatch(job["id"], device_id=device["id"])
                audit_worker_denial(
                    action="jobs.dispatch",
                    device=device,
                    store_id=store_id,
                    agent_id=agent_id,
                    reason="cortex_dispatch_admission_invalid",
                    resource_type="job",
                    resource_id=job["id"],
                    job_id=job["id"],
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "cortex_dispatch_admission_invalid"},
                )
            job_lease_token, job_lease_claims = policy.issue_lease(
                authenticated_device=device,
                store_id=store_id,
                agent_id=agent_id,
                ttl_seconds=settings.cortex_job_lease_ttl_seconds,
                audit_action="lease.issue.cortex_job",
            )
            authorization_lease = {
                "token": job_lease_token,
                "expires_at": job_lease_claims.expires_at,
                "capabilities": list(job_lease_claims.capabilities),
            }
        return {"job": job, "authorization_lease": authorization_lease}

    @worker.post("/jobs/{job_id}/complete")
    def complete_job(
        job_id: str,
        request: JobCompletionRequest,
        device: dict[str, Any] = Depends(require_worker_device),
        lease_token: str = Header(..., alias="X-Atlas-Lease"),
    ) -> dict[str, Any]:
        job = repository.get_job(job_id)
        expected_context = (
            request.tenant_id == device["tenant_id"]
            and job is not None
            and job["tenant_id"] == device["tenant_id"]
            and job["store_id"] == request.store_id
            and job["agent_id"] == request.agent_id
            and job["claimed_by_device_id"] == device["id"]
        )
        if not expected_context:
            audit_worker_denial(
                action="job.complete",
                device=device,
                store_id=request.store_id,
                agent_id=request.agent_id,
                reason="job_context_mismatch",
                resource_type="job",
                resource_id=job_id,
                job_id=job_id,
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="job_not_found"
            )
        try:
            authorize(
                device=device,
                store_id=request.store_id,
                agent_id=request.agent_id,
                capability="jobs.complete",
                lease_token=lease_token,
                audit_action="jobs.complete",
                job_id=job_id,
            )
            authorize(
                device=device,
                store_id=request.store_id,
                agent_id=request.agent_id,
                capability=job["capability"],
                lease_token=lease_token,
                audit_action="jobs.complete_capability",
                job_id=job_id,
            )
        except HTTPException:
            # A claim must never remain running after its holder receives a
            # policy denial. The token check prevents a different caller from
            # releasing another attempt.
            repository.release_job(
                job_id,
                device_id=device["id"],
                claim_token=request.claim_token,
            )
            raise
        try:
            completed = repository.complete_job(
                job_id,
                device_id=device["id"],
                claim_token=request.claim_token,
                status=request.status,
                result=request.result,
                error=request.error,
            )
        except ValueError as exc:
            audit_worker_denial(
                action="job.complete",
                device=device,
                store_id=request.store_id,
                agent_id=request.agent_id,
                reason=str(exc),
                resource_type="job",
                resource_id=job_id,
                job_id=job_id,
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc
        repository.record_audit(
            actor_type="worker",
            action="job.complete",
            outcome=request.status,
            tenant_id=device["tenant_id"],
            store_id=request.store_id,
            agent_id=request.agent_id,
            device_id=device["id"],
            job_id=job_id,
            resource_type="job",
            resource_id=job_id,
            details={"capability": job["capability"]},
        )
        return {"job": completed}

    app.include_router(worker)

    admin = APIRouter(
        prefix="/api/v1/admin",
        tags=["admin"],
        dependencies=[Depends(require_admin)],
    )

    @admin.get("/overview")
    def admin_overview() -> dict[str, Any]:
        return repository.overview()

    @admin.post("/jobs", status_code=status.HTTP_201_CREATED)
    def queue_job(request: QueueJobRequest) -> dict[str, Any]:
        if request.capability == "cortex.memory_maintenance":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "cortex_dispatch_requires_dedicated_admission"},
            )
        tenant = repository.get_tenant(request.tenant_id)
        store = repository.get_store(request.store_id)
        agent = repository.get_agent(request.agent_id)
        subscription = repository.get_active_subscription(request.tenant_id)
        entitlement = repository.get_entitlement(
            request.tenant_id, request.store_id, request.capability
        )
        device_id = request.device_id or (agent or {}).get("device_id")
        device = repository.get_device(device_id) if device_id else None
        invalid_code: str | None = None
        if not tenant or tenant["status"] != "active":
            invalid_code = "tenant_inactive"
        elif (
            not store
            or store["tenant_id"] != request.tenant_id
            or store["status"] != "active"
        ):
            invalid_code = "store_inactive"
        elif (
            not agent
            or agent["tenant_id"] != request.tenant_id
            or agent["store_id"] != request.store_id
            or agent["status"] != "active"
        ):
            invalid_code = "agent_inactive"
        elif not subscription or not not_expired(
            subscription.get("current_period_end")
        ):
            invalid_code = "subscription_inactive"
        elif (
            not entitlement
            or entitlement["status"] != "active"
            or not not_expired(entitlement.get("expires_at"))
        ):
            invalid_code = "entitlement_inactive"
        elif agent.get("device_id") and agent["device_id"] != device_id:
            invalid_code = "agent_device_mismatch"
        elif device_id and (
            not device
            or device["tenant_id"] != request.tenant_id
            or device["store_id"] != request.store_id
            or device["status"] != "active"
        ):
            invalid_code = "device_inactive"
        if invalid_code:
            repository.record_audit(
                actor_type="admin",
                action="job.queue",
                outcome="denied",
                tenant_id=request.tenant_id,
                store_id=request.store_id,
                agent_id=request.agent_id,
                device_id=device_id,
                resource_type="capability",
                resource_id=request.capability,
                details={"reason": invalid_code},
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": invalid_code},
            )
        try:
            job, created = repository.queue_job(
                tenant_id=request.tenant_id,
                store_id=request.store_id,
                agent_id=request.agent_id,
                device_id=device_id,
                capability=request.capability,
                payload=request.payload,
                requested_by="local-admin",
                idempotency_key=request.idempotency_key,
            )
        except IdempotencyConflict as exc:
            repository.record_audit(
                actor_type="admin",
                action="job.queue",
                outcome="denied",
                tenant_id=request.tenant_id,
                store_id=request.store_id,
                agent_id=request.agent_id,
                device_id=device_id,
                resource_type="idempotency_key",
                resource_id=request.idempotency_key,
                details={"reason": str(exc)},
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": str(exc)},
            ) from exc
        repository.record_audit(
            actor_type="admin",
            action="job.queue",
            outcome="created" if created else "deduplicated",
            tenant_id=request.tenant_id,
            store_id=request.store_id,
            agent_id=request.agent_id,
            device_id=device_id,
            job_id=job["id"],
            resource_type="job",
            resource_id=job["id"],
            details={"capability": request.capability},
        )
        return {"job": job, "created": created}

    @admin.post("/jobs/{job_id}/requeue")
    def requeue_job(job_id: str, request: RequeueJobRequest) -> dict[str, Any]:
        existing = repository.get_job(job_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="job_not_found"
            )
        try:
            job = repository.requeue_job(job_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": str(exc)},
            ) from exc
        repository.record_audit(
            actor_type="admin",
            action="job.requeue",
            outcome="succeeded",
            tenant_id=job["tenant_id"],
            store_id=job["store_id"],
            agent_id=job["agent_id"],
            device_id=job.get("device_id"),
            job_id=job_id,
            resource_type="job",
            resource_id=job_id,
            details={"reason": request.reason, "prior_status": existing["status"]},
        )
        return {"job": job}

    @admin.post("/{resource}/{resource_id}/toggle")
    def toggle_resource(
        resource: ToggleResource,
        resource_id: str,
        request: ToggleRequest,
    ) -> dict[str, Any]:
        try:
            updated = repository.toggle(resource, resource_id, request.enabled)
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="resource_not_found",
            ) from exc
        repository.record_audit(
            actor_type="admin",
            action=f"{resource}.toggle",
            outcome="succeeded",
            tenant_id=updated.get("tenant_id"),
            store_id=updated.get("store_id"),
            device_id=updated.get("id") if resource == "devices" else None,
            resource_type=resource,
            resource_id=resource_id,
            details={
                "status": updated["status"],
                "reason": request.reason,
                "terminated_job_count": updated.get("terminated_job_count", 0),
            },
        )
        return {"resource": updated}

    @admin.get("/{resource}")
    def list_admin_records(
        resource: AdminResource,
        tenant_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        items = repository.list_records(resource, tenant_id=tenant_id, limit=limit)
        return {"items": items, "count": len(items)}

    app.include_router(admin)

    model_api = APIRouter(tags=["model-gateway"])

    @model_api.get("/v1/models")
    def list_models(
        device: dict[str, Any] = Depends(require_worker_device),
        tenant_id: str = Header(..., alias="X-Atlas-Tenant-ID"),
        store_id: str = Header(..., alias="X-Atlas-Store-ID"),
        agent_id: str = Header(..., alias="X-Atlas-Agent-ID"),
        lease_token: str = Header(..., alias="X-Atlas-Lease"),
    ) -> dict[str, Any]:
        if tenant_id != device["tenant_id"]:
            audit_worker_denial(
                action="model.list",
                device=device,
                store_id=store_id,
                agent_id=agent_id,
                reason="tenant_context_mismatch",
                resource_type="model",
                resource_id=settings.model_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "tenant_context_mismatch"},
            )
        authorize(
            device=device,
            store_id=store_id,
            agent_id=agent_id,
            capability="model.chat",
            lease_token=lease_token,
            audit_action="model.list",
        )
        return {
            "object": "list",
            "data": [
                {
                    "id": settings.model_id,
                    "object": "model",
                    "created": 1_767_225_600,
                    "owned_by": "altas",
                }
            ],
        }

    @model_api.post("/v1/chat/completions")
    async def chat_completions(
        request: ChatCompletionRequest,
        device: dict[str, Any] = Depends(require_worker_device),
        tenant_id: str = Header(..., alias="X-Atlas-Tenant-ID"),
        store_id: str = Header(..., alias="X-Atlas-Store-ID"),
        agent_id: str = Header(..., alias="X-Atlas-Agent-ID"),
        lease_token: str = Header(..., alias="X-Atlas-Lease"),
        job_id: str = Header(..., alias="X-Atlas-Job-ID"),
        claim_token: str = Header(
            ..., min_length=32, max_length=256, alias="X-Atlas-Claim-Token"
        ),
        cortex_dispatch_key: str | None = Header(
            default=None,
            min_length=64,
            max_length=64,
            pattern=r"^[0-9a-f]{64}$",
            alias="X-Atlas-Cortex-Dispatch-Key",
        ),
        cortex_dispatch_admission: str | None = Header(
            default=None,
            min_length=1,
            max_length=2048,
            alias="X-Atlas-Cortex-Dispatch-Admission",
        ),
    ) -> dict[str, Any]:
        if tenant_id != device["tenant_id"]:
            audit_worker_denial(
                action="model.chat",
                device=device,
                store_id=store_id,
                agent_id=agent_id,
                reason="tenant_context_mismatch",
                resource_type="model",
                resource_id=request.model,
                job_id=job_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "tenant_context_mismatch"},
            )
        job = repository.get_job(job_id)
        if (
            not job
            or job["status"] != "running"
            or job["tenant_id"] != device["tenant_id"]
            or job["store_id"] != store_id
            or job["agent_id"] != agent_id
            or job["claimed_by_device_id"] != device["id"]
        ):
            audit_worker_denial(
                action="model.chat",
                device=device,
                store_id=store_id,
                agent_id=agent_id,
                reason="job_context_mismatch",
                resource_type="job",
                resource_id=job_id,
                job_id=job_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "job_context_mismatch"},
            )
        if not repository.validate_job_claim(
            job_id,
            device_id=device["id"],
            claim_token=claim_token,
        ):
            audit_worker_denial(
                action="model.chat",
                device=device,
                store_id=store_id,
                agent_id=agent_id,
                reason="job_claim_invalid",
                resource_type="job",
                resource_id=job_id,
                job_id=job_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "job_claim_invalid"},
            )
        if not _job_allows_capability(job["capability"], "model.chat"):
            audit_worker_denial(
                action="model.chat",
                device=device,
                store_id=store_id,
                agent_id=agent_id,
                reason="job_capability_mismatch",
                resource_type="job",
                resource_id=job_id,
                job_id=job_id,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "job_capability_mismatch"},
            )
        authorize(
            device=device,
            store_id=store_id,
            agent_id=agent_id,
            capability="model.chat",
            lease_token=lease_token,
            audit_action="model.chat",
            job_id=job_id,
        )
        is_cortex_job = job["capability"] == "cortex.memory_maintenance"
        admitted_cortex_dispatch: CoreCortexDispatchAdmission | None = None
        supplied_cortex_dispatch: CoreCortexDispatchAdmission | None = None
        cortex_commitment: str | None = None
        if is_cortex_job:
            try:
                admitted_cortex_dispatch = _require_cortex_dispatch_admission(
                    repository, job
                )
                supplied_cortex_dispatch = CoreCortexDispatchAdmission.from_header(
                    cortex_dispatch_admission or ""
                )
                cortex_commitment = dispatch_key_commitment(cortex_dispatch_key or "")
            except (KeyError, TypeError, ValueError):
                admitted_cortex_dispatch = None
            if (
                admitted_cortex_dispatch is None
                or supplied_cortex_dispatch is None
                or not hmac.compare_digest(
                    supplied_cortex_dispatch.canonical_json(),
                    admitted_cortex_dispatch.canonical_json(),
                )
                or not hmac.compare_digest(
                    cortex_commitment,
                    admitted_cortex_dispatch.dispatch_key_commitment,
                )
            ):
                audit_worker_denial(
                    action="model.chat",
                    device=device,
                    store_id=store_id,
                    agent_id=agent_id,
                    reason="cortex_dispatch_admission_invalid",
                    resource_type="job",
                    resource_id=job_id,
                    job_id=job_id,
                )
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "cortex_dispatch_admission_invalid"},
                )
        elif cortex_dispatch_key is not None or cortex_dispatch_admission is not None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "cortex_dispatch_scope_invalid"},
            )
        expected_model = (
            settings.cortex_model_id if is_cortex_job else settings.model_id
        )
        if request.model != expected_model:
            audit_worker_denial(
                action="model.request",
                device=device,
                store_id=store_id,
                agent_id=agent_id,
                reason="model_not_allowed",
                resource_type="model",
                resource_id=request.model,
                job_id=job_id,
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "model_not_allowed"},
            )
        if (
            is_cortex_job
            and not settings.mock_model
            and not settings.cortex_upstream_model
        ):
            audit_worker_denial(
                action="model.request",
                device=device,
                store_id=store_id,
                agent_id=agent_id,
                reason="cortex_model_not_configured",
                resource_type="model",
                resource_id=request.model,
                job_id=job_id,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": "cortex_model_not_configured"},
            )
        requested_token_values = [
            value
            for value in (request.max_tokens, request.max_completion_tokens)
            if value is not None
        ]
        requested_tokens = (
            max(requested_token_values)
            if requested_token_values
            else settings.default_model_max_tokens
        )
        effective_request = request
        if not requested_token_values:
            effective_request = request.model_copy(
                update={"max_tokens": requested_tokens}
            )
        try:
            request_limit = (
                settings.cortex_max_model_requests_per_job
                if is_cortex_job
                else settings.max_model_requests_per_job
            )
            requested_token_limit = (
                settings.cortex_max_requested_tokens_per_job
                if is_cortex_job
                else settings.max_requested_tokens_per_job
            )
            reservation = repository.reserve_model_usage(
                tenant_id=device["tenant_id"],
                store_id=store_id,
                agent_id=agent_id,
                device_id=device["id"],
                job_id=job_id,
                claim_token=claim_token,
                model=request.model,
                requested_tokens=requested_tokens,
                request_limit=request_limit,
                requested_token_limit=requested_token_limit,
                cortex_dispatch_admission=(
                    admitted_cortex_dispatch.to_mapping()
                    if admitted_cortex_dispatch is not None
                    else None
                ),
                cortex_dispatch_commitment=cortex_commitment,
            )
        except (InvalidJobClaim, ModelUsageLimitExceeded) as exc:
            audit_worker_denial(
                action="model.request",
                device=device,
                store_id=store_id,
                agent_id=agent_id,
                reason=str(exc),
                resource_type="job",
                resource_id=job_id,
                job_id=job_id,
            )
            error_status = (
                status.HTTP_403_FORBIDDEN
                if isinstance(exc, InvalidJobClaim)
                else status.HTTP_429_TOO_MANY_REQUESTS
            )
            raise HTTPException(
                status_code=error_status, detail={"code": str(exc)}
            ) from exc
        gateway_request = effective_request
        if is_cortex_job and settings.cortex_upstream_model:
            gateway_request = effective_request.model_copy(
                update={"model": settings.cortex_upstream_model}
            )
        try:
            result = await model_gateway.complete(gateway_request)
        except ModelGatewayError as exc:
            repository.fail_model_usage(reservation["id"])
            repository.record_audit(
                actor_type="model-gateway",
                action="model.response",
                outcome="failed",
                tenant_id=device["tenant_id"],
                store_id=store_id,
                agent_id=agent_id,
                device_id=device["id"],
                job_id=job_id,
                resource_type="model",
                resource_id=request.model,
                details={"reason": str(exc)},
            )
            error_status = (
                status.HTTP_400_BAD_REQUEST
                if str(exc) == "streaming_not_supported"
                else status.HTTP_502_BAD_GATEWAY
            )
            raise HTTPException(
                status_code=error_status, detail={"code": str(exc)}
            ) from exc
        repository.finalize_model_usage(
            reservation["id"],
            request_id=result.request_id,
            provider=result.provider,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        repository.record_audit(
            actor_type="model-gateway",
            action="model.response",
            outcome="succeeded",
            tenant_id=device["tenant_id"],
            store_id=store_id,
            agent_id=agent_id,
            device_id=device["id"],
            job_id=job_id,
            resource_type="model",
            resource_id=request.model,
            details={
                "provider": result.provider,
                "request_id": result.request_id,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
            },
        )
        # Keep centrally selected provider model IDs private and preserve the
        # stable Atlas product alias on the OpenAI-compatible response.
        response = dict(result.response)
        response["model"] = request.model
        return response

    app.include_router(model_api)
    app.mount(
        "/static",
        StaticFiles(directory=static_directory),
        name="altas-control-center-static",
    )
    return app
