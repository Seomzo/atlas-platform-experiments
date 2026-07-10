"""Immutable tenant/store/job context for managed work."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


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
    def from_job(cls, *, device_id: str, job: dict[str, Any]) -> "ManagedContext":
        return cls(
            tenant_id=str(job["tenant_id"]),
            store_id=str(job["store_id"]),
            device_id=device_id,
            agent_id=str(job["agent_id"]),
            job_id=str(job["id"]),
            correlation_id=str(job.get("correlation_id") or job["id"]),
            user_id=str(job.get("created_by") or "system"),
        )

    def request_headers(self) -> dict[str, str]:
        """Return non-secret scope headers for the control plane."""

        return {
            "X-Altas-Tenant-ID": self.tenant_id,
            "X-Altas-Store-ID": self.store_id,
            "X-Altas-Agent-ID": self.agent_id,
            "X-Altas-Job-ID": self.job_id,
            "X-Altas-Correlation-ID": self.correlation_id,
        }
