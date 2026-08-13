"""Atlas worker supervisor for the prototype walking skeleton."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from altas import __version__
from altas.cortex.managed_dispatch import CortexDispatchAdmission
from altas.cortex.dream import MANAGED_CORTEX_CAPABILITY
from altas.fixed_ops import (
    SYNTHETIC_EXPORT_CAPABILITY,
    SyntheticApprovedExport,
    DailyFixedOpsReport,
)
from altas.managed.client import AltasControlPlaneClient, Lease
from altas.managed.context import ManagedContext, ManagedRequestAuthorization
from altas.managed.errors import AltasWorkerError, PolicyDenied


@dataclass(slots=True)
class WorkerSettings:
    control_plane_url: str
    device_token: str = field(repr=False)
    device_id: str
    tenant_id: str
    store_id: str
    agent_id: str
    model_id: str = "altas-fixed-ops"
    profile_home: Path | None = None
    poll_interval_seconds: float = 3.0
    approval_poll_interval_seconds: float = 1.0
    request_timeout_seconds: float = 8.0

    @classmethod
    def from_env(cls) -> "WorkerSettings":
        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            return value

        return cls(
            control_plane_url=os.getenv(
                "ATLAS_CONTROL_PLANE_URL", "http://127.0.0.1:8787"
            ).rstrip("/"),
            device_token=required("ATLAS_DEVICE_TOKEN"),
            device_id=required("ATLAS_DEVICE_ID"),
            tenant_id=required("ATLAS_TENANT_ID"),
            store_id=required("ATLAS_STORE_ID"),
            agent_id=required("ATLAS_AGENT_ID"),
            model_id=os.getenv("ATLAS_MODEL_ID", "altas-fixed-ops"),
            profile_home=Path(
                os.getenv("ATLAS_PROFILE_HOME")
                or os.getenv("HERMES_HOME")
                or "~/.atlas"
            ).expanduser(),
            poll_interval_seconds=float(os.getenv("ATLAS_POLL_INTERVAL_SECONDS", "3")),
            approval_poll_interval_seconds=float(
                os.getenv("ATLAS_APPROVAL_POLL_INTERVAL_SECONDS", "1")
            ),
            request_timeout_seconds=float(
                os.getenv("ATLAS_REQUEST_TIMEOUT_SECONDS", "8")
            ),
        )


@dataclass(frozen=True, slots=True)
class WorkerCycleResult:
    status: str
    job_id: str | None = None
    detail: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "status": self.status,
                "job_id": self.job_id,
                "detail": self.detail,
            },
            sort_keys=True,
        )


@dataclass(frozen=True, slots=True)
class PreparedCortexDispatch:
    admission: CortexDispatchAdmission
    dispatch_key: str = field(repr=False)


class AltasWorker:
    """Poll one assigned store and dispatch only registered capabilities."""

    def __init__(
        self,
        settings: WorkerSettings,
        *,
        client: AltasControlPlaneClient | None = None,
    ) -> None:
        self.settings = settings
        self._owns_client = client is None
        self.client = client or AltasControlPlaneClient(
            base_url=settings.control_plane_url,
            device_token=settings.device_token,
            timeout_seconds=settings.request_timeout_seconds,
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def heartbeat(self) -> Lease:
        return self.client.heartbeat(
            tenant_id=self.settings.tenant_id,
            store_id=self.settings.store_id,
            agent_id=self.settings.agent_id,
            worker_version=__version__,
            health_status="healthy",
            metadata={
                "runtime": "altas-prototype",
                "connector": "synthetic_tekion_fixture",
            },
        )

    def run_once(self, *, capability_filter: str | None = None) -> WorkerCycleResult:
        lease = self.heartbeat()
        if MANAGED_CORTEX_CAPABILITY in lease.capabilities:
            dispatch = self._prepare_cortex_dispatch()
            if dispatch:
                self.client.ensure_cortex_maintenance(
                    lease=lease.token,
                    tenant_id=self.settings.tenant_id,
                    store_id=self.settings.store_id,
                    agent_id=self.settings.agent_id,
                    dispatch_key=dispatch.dispatch_key,
                    dispatch_admission=dispatch.admission,
                )
        next_job_kwargs: dict[str, Any] = {
            "lease": lease.token,
            "tenant_id": self.settings.tenant_id,
            "store_id": self.settings.store_id,
            "agent_id": self.settings.agent_id,
        }
        if capability_filter:
            next_job_kwargs["capability"] = capability_filter
        job = self.client.next_job(**next_job_kwargs)
        if job is None:
            return WorkerCycleResult(status="idle", detail="No queued job")
        job_authorization_lease = lease
        take_job_lease = getattr(self.client, "take_job_authorization_lease", None)
        if callable(take_job_lease):
            issued_job_lease = take_job_lease()
            if issued_job_lease is not None:
                job_authorization_lease = issued_job_lease

        try:
            context = ManagedContext.from_job(job=job)
        except (KeyError, TypeError, ValueError) as exc:
            raise PolicyDenied(
                "SCOPE_MISMATCH",
                "Worker received an invalid claimed job context",
            ) from exc
        if (
            context.tenant_id != self.settings.tenant_id
            or context.store_id != self.settings.store_id
            or context.agent_id != self.settings.agent_id
            or context.device_id != self.settings.device_id
        ):
            # Do not acknowledge or execute a job outside the configured
            # worker boundary. The server should make this impossible; this is
            # defense in depth against contract regressions.
            raise PolicyDenied("SCOPE_MISMATCH", "Worker received a mismatched job")

        capability = str(job["capability"])
        if capability_filter and capability != capability_filter:
            raise PolicyDenied(
                "SCOPE_MISMATCH",
                "Worker received a job outside its capability filter",
            )
        if capability == MANAGED_CORTEX_CAPABILITY:
            from altas.managed.request_scope import (
                ManagedRequestScopeError,
                validate_managed_profile_identity,
            )

            try:
                validate_managed_profile_identity(self._profile_home(), context)
            except ManagedRequestScopeError as exc:
                raise PolicyDenied(
                    "SCOPE_MISMATCH",
                    "Worker profile identity does not match the claimed job",
                ) from exc
        cortex_dispatch: PreparedCortexDispatch | None = None
        if capability == MANAGED_CORTEX_CAPABILITY:
            try:
                cortex_dispatch = self._validate_claimed_cortex_dispatch(job)
            except (KeyError, PermissionError, TypeError, ValueError):
                completion_lease = self.heartbeat()
                self.client.complete_job(
                    lease=completion_lease.token,
                    context=context,
                    claim_token=str(job.get("claim_token") or ""),
                    status="failed",
                    error_code="CortexDispatchAdmissionInvalid",
                )
                return WorkerCycleResult(
                    status="failed",
                    job_id=context.job_id,
                    detail="CortexDispatchAdmissionInvalid",
                )
        claim_token = str(job.get("claim_token") or "")
        if not claim_token:
            raise ValueError("JobClaimTokenMissing")
        decision = self.client.evaluate_policy(
            lease=job_authorization_lease.token,
            context=context,
            claim_token=claim_token,
            capability=capability,
        )
        if not decision.allowed:
            raise PolicyDenied(decision.reason_code)
        authorization = ManagedRequestAuthorization(
            context=context,
            capability=capability,
            control_plane_url=self.settings.control_plane_url,
            lease_token=job_authorization_lease.token,
            claim_token=claim_token,
            cortex_dispatch_admission=(
                cortex_dispatch.admission if cortex_dispatch is not None else None
            ),
            cortex_dispatch_key=(
                cortex_dispatch.dispatch_key if cortex_dispatch is not None else None
            ),
        )

        try:
            result = self._execute_job(
                job=job,
                authorization=authorization,
            )
        except Exception as exc:
            # Upload a bounded error category, not arbitrary exception details.
            error_code = type(exc).__name__[:120]
            completion_lease = self.heartbeat()
            self.client.complete_job(
                lease=completion_lease.token,
                context=context,
                claim_token=claim_token,
                status="failed",
                error_code=error_code,
            )
            return WorkerCycleResult(
                status="failed",
                job_id=context.job_id,
                detail=error_code,
            )

        completion_lease = self.heartbeat()
        self.client.complete_job(
            lease=completion_lease.token,
            context=context,
            claim_token=claim_token,
            status="succeeded",
            result=result,
        )
        return WorkerCycleResult(
            status="succeeded",
            job_id=context.job_id,
            detail=capability,
        )

    def run_forever(self) -> None:
        try:
            while True:
                try:
                    print(self.run_once().to_json(), flush=True)
                except (AltasWorkerError, ValueError) as exc:
                    print(
                        WorkerCycleResult(
                            status="blocked",
                            detail=type(exc).__name__,
                        ).to_json(),
                        flush=True,
                    )
                time.sleep(self.settings.poll_interval_seconds)
        finally:
            self.close()

    def _execute_job(
        self,
        *,
        job: dict[str, Any],
        authorization: ManagedRequestAuthorization,
    ) -> dict[str, Any]:
        capability = str(job["capability"])
        if capability == MANAGED_CORTEX_CAPABILITY:
            return self._execute_cortex_maintenance(authorization)
        if capability == SYNTHETIC_EXPORT_CAPABILITY:
            return self._execute_synthetic_export(
                job=job,
                authorization=authorization,
            )
        if capability != "fixed_ops.daily_report":
            raise LookupError("CapabilityNotRegistered")

        context = authorization.context

        def summarize(prompt: str) -> str:
            response = self.client.chat_completion(
                lease=authorization.lease_token,
                context=context,
                claim_token=authorization.claim_token,
                model=self.settings.model_id,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are Atlas for Fixed Ops. Use only supplied "
                            "synthetic aggregate metrics and never invent data."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            try:
                return str(response["choices"][0]["message"]["content"])
            except (KeyError, IndexError, TypeError) as exc:
                raise ValueError("ModelGatewayResponseInvalid") from exc

        payload = job.get("payload") or {}
        if not isinstance(payload, dict):
            raise ValueError("JobPayloadInvalid")
        workflow = DailyFixedOpsReport(summarize=summarize)
        return workflow.run(
            store_id=context.store_id,
            business_date=payload.get("report_date"),
        )

    def _execute_synthetic_export(
        self,
        *,
        job: dict[str, Any],
        authorization: ManagedRequestAuthorization,
    ) -> dict[str, Any]:
        workflow = SyntheticApprovedExport.from_job(job)
        payload = job["payload"]
        relay_session_id = str(payload["relay_session_id"])
        action = workflow.action()
        attempt = int(job.get("attempt_count") or 0)
        if attempt < 1:
            raise ValueError("SyntheticExportAttemptInvalid")
        idempotency_key = (
            f"managed-action:{authorization.context.job_id}:{attempt}:{action.digest()}"
        )
        approval = self.client.request_managed_approval(
            lease=authorization.lease_token,
            context=authorization.context,
            claim_token=authorization.claim_token,
            relay_session_id=relay_session_id,
            action=action,
            idempotency_key=idempotency_key,
        )
        while approval.get("status") == "pending":
            expires_at = datetime.fromisoformat(
                str(approval["expires_at"]).replace("Z", "+00:00")
            ).astimezone(UTC)
            if expires_at <= datetime.now(UTC):
                raise PolicyDenied("APPROVAL_EXPIRED")
            time.sleep(self.settings.approval_poll_interval_seconds)
            approval = self.client.get_managed_approval(
                approval_id=str(approval["id"]),
                lease=authorization.lease_token,
                context=authorization.context,
                claim_token=authorization.claim_token,
            )
        if approval.get("status") != "approved":
            raise PolicyDenied(
                f"APPROVAL_{str(approval.get('status') or 'INVALID').upper()}"
            )
        action_authorization = self.client.consume_managed_approval(
            approval_id=str(approval["id"]),
            lease=authorization.lease_token,
            context=authorization.context,
            claim_token=authorization.claim_token,
            action=action,
            expected_version=int(approval["version"]),
        )
        return workflow.execute(
            context=authorization.context,
            authorization=action_authorization,
        )

    def _profile_home(self) -> Path:
        configured = self.settings.profile_home or Path("~/.atlas")
        return Path(configured).expanduser().resolve()

    def _prepare_cortex_dispatch(self) -> PreparedCortexDispatch | None:
        """Wake local Cortex and bind its exact next admitted model job.

        This runs before a control-plane job has been claimed, so it performs
        only deterministic/local maintenance.  A managed model route remains
        queued until the server issues the dedicated maintenance claim.
        """
        from agent.secret_scope import build_profile_secret_scope
        from altas.cortex.config import CortexConfig
        from altas.cortex.runtime import open_cortex_store
        from altas.cortex.scheduler import enqueue_cortex_wake
        from altas.cortex.store import utc_now
        from altas.cortex.worker import (
            DETERMINISTIC_JOB_TYPES,
            MODEL_JOB_TYPES,
            CortexDreamWorker,
            cortex_profile_runtime_scope,
        )

        home = self._profile_home()
        try:
            config = CortexConfig.load(home)
            if not config.enabled or not config.dream_enabled:
                return None
            profile_scope = build_profile_secret_scope(home)
            expected_identity = {
                "ATLAS_TENANT_ID": self.settings.tenant_id,
                "ATLAS_STORE_ID": self.settings.store_id,
                "ATLAS_AGENT_ID": self.settings.agent_id,
                "ATLAS_DEVICE_ID": self.settings.device_id,
            }
            if any(
                str(profile_scope.get(name) or "").strip() != expected
                for name, expected in expected_identity.items()
            ):
                return None
            identity_environment = {
                **profile_scope,
                **expected_identity,
                "ATLAS_MANAGED_MODE": "1",
            }
            with cortex_profile_runtime_scope(home, identity_environment):
                store, config = open_cortex_store(
                    home,
                    environ=identity_environment,
                )
                enqueue_cortex_wake(store, config, startup=True)
                CortexDreamWorker(
                    store,
                    config,
                    environ=identity_environment,
                    secret_scope=identity_environment,
                ).drain(max_jobs=25, job_types=DETERMINISTIC_JOB_TYPES)
            now = utc_now()
            with store.connect() as connection:
                active_lease = connection.execute(
                    "SELECT 1 FROM cortex_leases WHERE brain_id=? "
                    "AND lease_name='dream-cycle' AND expires_at>? LIMIT 1",
                    (store.brain_id, now),
                ).fetchone()
                if active_lease is not None:
                    return None
            row = store.next_leaseable_job(job_types=MODEL_JOB_TYPES)
            if row is None:
                return None
            admission, dispatch_key = CortexDispatchAdmission.create(
                local_job_id=str(row["id"]),
                admission_id=str(row["admission_id"]),
                root_job_id=str(row["root_job_id"]),
                canonical_input_hash=str(row["input_hash"]),
                attempt=int(row["attempt"]),
                due_at=str(row["due_at"]),
            )
            return PreparedCortexDispatch(admission, dispatch_key)
        except Exception:
            # Cortex availability must not stop unrelated named workflows.
            # Detailed health remains in the profile-local Cortex store.
            return None

    def _prepare_cortex_dispatch_key(self) -> str | None:
        """Compatibility helper returning only the opaque dispatch key."""

        prepared = self._prepare_cortex_dispatch()
        return prepared.dispatch_key if prepared is not None else None

    def _validate_claimed_cortex_dispatch(
        self,
        control_plane_job: dict[str, Any],
    ) -> PreparedCortexDispatch:
        """Match a claimed control-plane payload to the exact local job."""

        from agent.secret_scope import build_profile_secret_scope
        from altas.cortex.runtime import open_cortex_store
        from altas.cortex.worker import MODEL_JOB_TYPES, cortex_profile_runtime_scope

        payload = control_plane_job.get("payload")
        if not isinstance(payload, dict) or set(payload) != {"dispatch_admission"}:
            raise ValueError("Cortex control-plane payload is invalid")
        admission = CortexDispatchAdmission.from_mapping(payload["dispatch_admission"])
        recreated, dispatch_key = CortexDispatchAdmission.create(
            local_job_id=admission.local_job_id,
            admission_id=admission.admission_id,
            root_job_id=admission.root_job_id,
            canonical_input_hash=admission.canonical_input_hash,
            attempt=admission.attempt,
            due_at=admission.due_at,
        )
        if recreated != admission:
            raise PermissionError("Cortex dispatch commitment is not canonical")

        home = self._profile_home()
        profile_scope = build_profile_secret_scope(home)
        identity_environment = {
            **profile_scope,
            "ATLAS_TENANT_ID": self.settings.tenant_id,
            "ATLAS_STORE_ID": self.settings.store_id,
            "ATLAS_AGENT_ID": self.settings.agent_id,
            "ATLAS_DEVICE_ID": self.settings.device_id,
            "ATLAS_MANAGED_MODE": "1",
        }
        with cortex_profile_runtime_scope(home, identity_environment):
            store, _config = open_cortex_store(home, environ=identity_environment)
            local_job = store.next_leaseable_job(job_types=MODEL_JOB_TYPES)
        if local_job is None or not admission.matches_job(local_job):
            raise PermissionError(
                "claimed Cortex dispatch does not match the next local admitted job"
            )
        return PreparedCortexDispatch(admission, dispatch_key)

    def _execute_cortex_maintenance(
        self,
        authorization: ManagedRequestAuthorization,
    ) -> dict[str, Any]:
        """Process at most one local semantic job under this exact CP claim."""
        if authorization.capability != MANAGED_CORTEX_CAPABILITY:
            raise PermissionError("CortexMaintenanceCapabilityMismatch")

        from hermes_cli.config import load_config

        from altas.cortex.runtime import open_cortex_store
        from altas.cortex.worker import (
            CortexDreamWorker,
            cortex_profile_runtime_scope,
        )
        from altas.managed.request_scope import managed_request_scope

        home = self._profile_home()
        with managed_request_scope(home, authorization) as scoped:
            with cortex_profile_runtime_scope(home, scoped):
                store, config = open_cortex_store(home, environ=scoped)
                raw_config = load_config()
                results = CortexDreamWorker(
                    store,
                    config,
                    raw_config=raw_config,
                    environ=scoped,
                    secret_scope=scoped,
                ).run_exact_managed_dispatch(authorization.cortex_dispatch_admission)
        result = results
        if result.status in {"failed", "retention_failed", "locked", "disabled"}:
            raise RuntimeError("CortexMaintenanceFailed")
        return {
            "status": result.status,
            "processed": result.status == "succeeded",
        }
