"""Immutable tenant/store/job context for managed work."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from altas.cortex.managed_dispatch import CortexDispatchAdmission


@dataclass(frozen=True, slots=True)
class ManagedContext:
    tenant_id: str
    store_id: str
    device_id: str
    agent_id: str
    job_id: str
    correlation_id: str
    user_id: str = "system"

    def __post_init__(self) -> None:
        for field_name, value in asdict(self).items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")

    @classmethod
    def from_job(cls, *, job: dict[str, Any]) -> "ManagedContext":
        """Build context from the server-issued claim, never local identity.

        ``claimed_by_device_id`` is populated atomically by the control plane
        when it claims the job.  Treating a worker setting as the source here
        would let a malformed response silently rebind the claim to the local
        device before policy and profile checks run.
        """

        claimed_by_device_id = job.get("claimed_by_device_id")
        if (
            not isinstance(claimed_by_device_id, str)
            or not claimed_by_device_id.strip()
        ):
            raise ValueError("claimed_by_device_id is required")
        return cls(
            tenant_id=str(job["tenant_id"]),
            store_id=str(job["store_id"]),
            device_id=claimed_by_device_id,
            agent_id=str(job["agent_id"]),
            job_id=str(job["id"]),
            correlation_id=str(job.get("correlation_id") or job["id"]),
            user_id=str(job.get("created_by") or "system"),
        )

    def request_headers(self) -> dict[str, str]:
        """Return non-secret scope headers for the control plane."""

        return {
            "X-Atlas-Tenant-ID": self.tenant_id,
            "X-Atlas-Store-ID": self.store_id,
            "X-Atlas-Agent-ID": self.agent_id,
            "X-Atlas-Job-ID": self.job_id,
            "X-Atlas-Correlation-ID": self.correlation_id,
        }


@dataclass(frozen=True, slots=True)
class ManagedRequestAuthorization:
    """One control-plane-authorized job request.

    The lease and claim are deliberately excluded from the generated repr so
    logging or assertion failures cannot expose bearer material.  This object
    is immutable and short-lived; callers must install it through
    :func:`altas.managed.request_scope.managed_request_scope` rather than copy
    its values into process-global environment variables.
    """

    context: ManagedContext
    capability: str
    control_plane_url: str
    lease_token: str = field(repr=False)
    claim_token: str = field(repr=False)
    cortex_dispatch_admission: CortexDispatchAdmission | None = None
    cortex_dispatch_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.context, ManagedContext):
            raise TypeError("context must be a ManagedContext")
        for field_name in (
            "capability",
            "control_plane_url",
            "lease_token",
            "claim_token",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} is required")
            object.__setattr__(self, field_name, value.strip())
        normalized_url = self.control_plane_url.rstrip("/")
        if not normalized_url.startswith(("http://", "https://")):
            raise ValueError("control_plane_url must be an HTTP(S) URL")
        object.__setattr__(self, "control_plane_url", normalized_url)
        if self.capability == "cortex.memory_maintenance":
            if not isinstance(
                self.cortex_dispatch_admission, CortexDispatchAdmission
            ) or not self.cortex_dispatch_admission.matches_dispatch_key(
                self.cortex_dispatch_key or ""
            ):
                raise ValueError(
                    "Cortex authorization requires exact dispatch provenance"
                )
        elif (
            self.cortex_dispatch_admission is not None
            or self.cortex_dispatch_key is not None
        ):
            raise ValueError("non-Cortex authorization cannot carry Cortex provenance")

    def ephemeral_scope_overlay(self) -> dict[str, str]:
        """Return only values whose authority ends with this job request."""

        overlay = {
            "ATLAS_MANAGED_MODE": "1",
            "ATLAS_LEASE_TOKEN": self.lease_token,
            "ATLAS_JOB_ID": self.context.job_id,
            "ATLAS_JOB_CAPABILITY": self.capability,
            "ATLAS_CLAIM_TOKEN": self.claim_token,
            "ATLAS_CORRELATION_ID": self.context.correlation_id,
            "ATLAS_CONTROL_PLANE_URL": self.control_plane_url,
        }
        if self.cortex_dispatch_admission is not None:
            overlay.update({
                "ATLAS_CORTEX_DISPATCH_ADMISSION": (
                    self.cortex_dispatch_admission.to_header()
                ),
                "ATLAS_CORTEX_DISPATCH_KEY": self.cortex_dispatch_key or "",
            })
        return overlay
