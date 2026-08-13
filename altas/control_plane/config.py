"""Runtime configuration for the Atlas control plane."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class ControlPlaneSettings:
    """Configuration injected into :func:`create_app`.

    Secrets are deliberately excluded from ``repr`` and are never persisted in
    the control-plane database. Production deployments should provide all three
    sensitive values through their process secret manager.
    """

    database_path: Path
    lease_signing_key: bytes = field(repr=False)
    admin_token: str = field(repr=False)
    seed_demo_data: bool = False
    mock_model: bool = True
    lease_ttl_seconds: int = 300
    account_session_ttl_seconds: int = 600
    enrollment_ttl_seconds: int = 600
    device_session_ttl_seconds: int = 300
    device_proof_max_skew_seconds: int = 60
    device_credential_ttl_seconds: int = 31_536_000
    relay_command_ttl_seconds: int = 300
    relay_max_pending_commands: int = 32
    relay_max_inflight_commands: int = 8
    relay_max_event_bytes: int = 131_072
    managed_approval_ttl_seconds: int = 120
    job_visibility_timeout_seconds: int = 900
    # A claimed Cortex batch can make at most 20 sequential 30-second model
    # requests. Give that exact job a longer signed lease while keeping it
    # below the 900-second claim visibility timeout.
    cortex_job_lease_ttl_seconds: int = 840
    max_model_requests_per_job: int = 8
    max_requested_tokens_per_job: int = 4096
    # Default Cortex batch: 100 candidates / 20 per call, with at most one
    # repair and one reviewed call (+ repair) per batch = 20 bounded calls.
    cortex_max_model_requests_per_job: int = 20
    cortex_max_requested_tokens_per_job: int = 80_000
    # Counts both first-time dispatches and requeues of terminal dispatches.
    cortex_max_jobs_per_device_per_24h: int = 24
    default_model_max_tokens: int = 400
    model_id: str = "altas-fixed-ops"
    cortex_model_id: str = "atlas-cortex-memory"
    cortex_upstream_model: str | None = None
    upstream_base_url: str = "https://api.openai.com/v1"
    upstream_api_key: str | None = field(default=None, repr=False)
    request_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        self.database_path = Path(self.database_path)
        if len(self.lease_signing_key) < 32:
            raise ValueError("lease_signing_key must contain at least 32 bytes")
        if not self.admin_token:
            raise ValueError("admin_token must not be empty")
        if self.lease_ttl_seconds < 15:
            raise ValueError("lease_ttl_seconds must be at least 15 seconds")
        if self.account_session_ttl_seconds < 15:
            raise ValueError("account_session_ttl_seconds must be at least 15 seconds")
        if self.enrollment_ttl_seconds < 30:
            raise ValueError("enrollment_ttl_seconds must be at least 30 seconds")
        if self.device_session_ttl_seconds < 15:
            raise ValueError("device_session_ttl_seconds must be at least 15 seconds")
        if not 15 <= self.device_proof_max_skew_seconds <= 300:
            raise ValueError(
                "device_proof_max_skew_seconds must be between 15 and 300 seconds"
            )
        if self.device_credential_ttl_seconds < 86_400:
            raise ValueError("device_credential_ttl_seconds must be at least one day")
        if self.relay_command_ttl_seconds < 30:
            raise ValueError("relay_command_ttl_seconds must be at least 30 seconds")
        if not 1 <= self.relay_max_inflight_commands <= self.relay_max_pending_commands:
            raise ValueError(
                "relay inflight commands must be between one and the pending limit"
            )
        if not 4096 <= self.relay_max_event_bytes <= 1_048_576:
            raise ValueError("relay_max_event_bytes must be between 4 KiB and 1 MiB")
        if not 30 <= self.managed_approval_ttl_seconds <= self.lease_ttl_seconds:
            raise ValueError(
                "managed approval TTL must be between 30 seconds and the lease TTL"
            )
        if self.job_visibility_timeout_seconds < 15:
            raise ValueError(
                "job_visibility_timeout_seconds must be at least 15 seconds"
            )
        if self.cortex_job_lease_ttl_seconds < self.lease_ttl_seconds:
            raise ValueError(
                "cortex_job_lease_ttl_seconds must not be shorter than lease_ttl_seconds"
            )
        if self.cortex_job_lease_ttl_seconds >= self.job_visibility_timeout_seconds:
            raise ValueError(
                "cortex_job_lease_ttl_seconds must be shorter than the job visibility timeout"
            )
        if self.max_model_requests_per_job < 1:
            raise ValueError("max_model_requests_per_job must be at least 1")
        if self.max_requested_tokens_per_job < 1:
            raise ValueError("max_requested_tokens_per_job must be at least 1")
        if self.cortex_max_model_requests_per_job < 1:
            raise ValueError("cortex_max_model_requests_per_job must be at least 1")
        if self.cortex_max_requested_tokens_per_job < 1:
            raise ValueError("cortex_max_requested_tokens_per_job must be at least 1")
        if self.cortex_max_jobs_per_device_per_24h < 1:
            raise ValueError("cortex_max_jobs_per_device_per_24h must be at least 1")
        if not 1 <= self.default_model_max_tokens <= 32768:
            raise ValueError("default_model_max_tokens must be between 1 and 32768")
        if self.default_model_max_tokens > self.max_requested_tokens_per_job:
            raise ValueError(
                "default_model_max_tokens must not exceed max_requested_tokens_per_job"
            )
        if not self.model_id.strip() or not self.cortex_model_id.strip():
            raise ValueError("model aliases must not be empty")
        if self.model_id == self.cortex_model_id:
            raise ValueError(
                "Cortex must use a model alias distinct from the chat model"
            )
        if self.cortex_upstream_model is not None:
            self.cortex_upstream_model = self.cortex_upstream_model.strip() or None
        if self.cortex_upstream_model == self.model_id:
            raise ValueError("Cortex must not route to the configured chat model")
        if self.seed_demo_data and not self.mock_model:
            raise ValueError(
                "demo seed data cannot be combined with a real model provider"
            )
        if not self.mock_model and not self.upstream_api_key:
            raise ValueError("ATLAS_UPSTREAM_API_KEY is required outside mock mode")

    @classmethod
    def from_env(cls) -> "ControlPlaneSettings":
        """Build settings from environment variables.

        Stable secrets are mandatory even for the local prototype. Generating
        them inside the server would make admin access unknowable and would
        invalidate every outstanding lease after a restart.
        """

        signing_key = os.getenv("ATLAS_LEASE_SIGNING_KEY")
        admin_token = os.getenv("ATLAS_ADMIN_TOKEN")
        if not signing_key:
            raise ValueError("ATLAS_LEASE_SIGNING_KEY is required")
        if not admin_token:
            raise ValueError("ATLAS_ADMIN_TOKEN is required")
        return cls(
            database_path=Path(
                os.getenv(
                    "ATLAS_DATABASE_PATH",
                    "data/atlas-control-plane.sqlite3",
                )
            ),
            lease_signing_key=signing_key.encode("utf-8"),
            admin_token=admin_token,
            seed_demo_data=_as_bool(os.getenv("ATLAS_SEED_DEMO_DATA"), default=False),
            mock_model=_as_bool(os.getenv("ATLAS_MODEL_MOCK"), default=True),
            lease_ttl_seconds=int(os.getenv("ATLAS_LEASE_TTL_SECONDS", "300")),
            job_visibility_timeout_seconds=int(
                os.getenv("ATLAS_JOB_VISIBILITY_TIMEOUT_SECONDS", "900")
            ),
            relay_command_ttl_seconds=int(
                os.getenv("ATLAS_RELAY_COMMAND_TTL_SECONDS", "300")
            ),
            relay_max_pending_commands=int(
                os.getenv("ATLAS_RELAY_MAX_PENDING_COMMANDS", "32")
            ),
            relay_max_inflight_commands=int(
                os.getenv("ATLAS_RELAY_MAX_INFLIGHT_COMMANDS", "8")
            ),
            relay_max_event_bytes=int(
                os.getenv("ATLAS_RELAY_MAX_EVENT_BYTES", "131072")
            ),
            managed_approval_ttl_seconds=int(
                os.getenv("ATLAS_MANAGED_APPROVAL_TTL_SECONDS", "120")
            ),
            cortex_job_lease_ttl_seconds=int(
                os.getenv("ATLAS_CORTEX_JOB_LEASE_TTL_SECONDS", "840")
            ),
            max_model_requests_per_job=int(
                os.getenv("ATLAS_MAX_MODEL_REQUESTS_PER_JOB", "8")
            ),
            max_requested_tokens_per_job=int(
                os.getenv("ATLAS_MAX_REQUESTED_TOKENS_PER_JOB", "4096")
            ),
            cortex_max_model_requests_per_job=int(
                os.getenv("ATLAS_CORTEX_MAX_MODEL_REQUESTS_PER_JOB", "20")
            ),
            cortex_max_requested_tokens_per_job=int(
                os.getenv("ATLAS_CORTEX_MAX_REQUESTED_TOKENS_PER_JOB", "80000")
            ),
            cortex_max_jobs_per_device_per_24h=int(
                os.getenv("ATLAS_CORTEX_MAX_JOBS_PER_DEVICE_PER_24H", "24")
            ),
            default_model_max_tokens=int(
                os.getenv("ATLAS_DEFAULT_MODEL_MAX_TOKENS", "400")
            ),
            model_id=os.getenv("ATLAS_MODEL_ID", "altas-fixed-ops"),
            cortex_model_id=os.getenv("ATLAS_CORTEX_MODEL_ID", "atlas-cortex-memory"),
            cortex_upstream_model=os.getenv("ATLAS_CORTEX_UPSTREAM_MODEL"),
            upstream_base_url=os.getenv(
                "ATLAS_UPSTREAM_BASE_URL", "https://api.openai.com/v1"
            ).rstrip("/"),
            upstream_api_key=os.getenv("ATLAS_UPSTREAM_API_KEY"),
            request_timeout_seconds=float(
                os.getenv("ATLAS_REQUEST_TIMEOUT_SECONDS", "30")
            ),
        )
