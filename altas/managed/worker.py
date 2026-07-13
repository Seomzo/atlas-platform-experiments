"""Atlas worker supervisor for the prototype walking skeleton."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

from altas import __version__
from altas.fixed_ops import DailyFixedOpsReport
from altas.managed.client import AltasControlPlaneClient, Lease
from altas.managed.context import ManagedContext
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
    poll_interval_seconds: float = 3.0
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
            poll_interval_seconds=float(os.getenv("ATLAS_POLL_INTERVAL_SECONDS", "3")),
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

    def run_once(self) -> WorkerCycleResult:
        lease = self.heartbeat()
        job = self.client.next_job(
            lease=lease.token,
            tenant_id=self.settings.tenant_id,
            store_id=self.settings.store_id,
            agent_id=self.settings.agent_id,
        )
        if job is None:
            return WorkerCycleResult(status="idle", detail="No queued job")

        context = ManagedContext.from_job(
            device_id=self.settings.device_id,
            job=job,
        )
        if (
            context.tenant_id != self.settings.tenant_id
            or context.store_id != self.settings.store_id
            or context.agent_id != self.settings.agent_id
        ):
            # Do not acknowledge or execute a job outside the configured
            # worker boundary. The server should make this impossible; this is
            # defense in depth against contract regressions.
            raise PolicyDenied("SCOPE_MISMATCH", "Worker received a mismatched job")

        capability = str(job["capability"])
        claim_token = str(job.get("claim_token") or "")
        if not claim_token:
            raise ValueError("JobClaimTokenMissing")
        decision = self.client.evaluate_policy(
            lease=lease.token,
            context=context,
            claim_token=claim_token,
            capability=capability,
        )
        if not decision.allowed:
            raise PolicyDenied(decision.reason_code)

        try:
            result = self._execute_job(
                job=job,
                context=context,
                lease=lease.token,
                claim_token=claim_token,
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
        context: ManagedContext,
        lease: str,
        claim_token: str,
    ) -> dict[str, Any]:
        capability = str(job["capability"])
        if capability != "fixed_ops.daily_report":
            raise LookupError("CapabilityNotRegistered")

        def summarize(prompt: str) -> str:
            response = self.client.chat_completion(
                lease=lease,
                context=context,
                claim_token=claim_token,
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
