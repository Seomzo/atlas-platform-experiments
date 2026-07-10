"""Runtime configuration for the Altas control plane."""

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
    job_visibility_timeout_seconds: int = 900
    max_model_requests_per_job: int = 8
    max_requested_tokens_per_job: int = 4096
    default_model_max_tokens: int = 400
    model_id: str = "altas-fixed-ops"
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
        if self.job_visibility_timeout_seconds < 15:
            raise ValueError(
                "job_visibility_timeout_seconds must be at least 15 seconds"
            )
        if self.max_model_requests_per_job < 1:
            raise ValueError("max_model_requests_per_job must be at least 1")
        if self.max_requested_tokens_per_job < 1:
            raise ValueError("max_requested_tokens_per_job must be at least 1")
        if not 1 <= self.default_model_max_tokens <= 32768:
            raise ValueError("default_model_max_tokens must be between 1 and 32768")
        if self.default_model_max_tokens > self.max_requested_tokens_per_job:
            raise ValueError(
                "default_model_max_tokens must not exceed max_requested_tokens_per_job"
            )
        if self.seed_demo_data and not self.mock_model:
            raise ValueError(
                "demo seed data cannot be combined with a real model provider"
            )
        if not self.mock_model and not self.upstream_api_key:
            raise ValueError("ALTAS_UPSTREAM_API_KEY is required outside mock mode")

    @classmethod
    def from_env(cls) -> "ControlPlaneSettings":
        """Build settings from environment variables.

        Stable secrets are mandatory even for the local prototype. Generating
        them inside the server would make admin access unknowable and would
        invalidate every outstanding lease after a restart.
        """

        signing_key = os.getenv("ALTAS_LEASE_SIGNING_KEY")
        admin_token = os.getenv("ALTAS_ADMIN_TOKEN")
        if not signing_key:
            raise ValueError("ALTAS_LEASE_SIGNING_KEY is required")
        if not admin_token:
            raise ValueError("ALTAS_ADMIN_TOKEN is required")
        return cls(
            database_path=Path(
                os.getenv(
                    "ALTAS_DATABASE_PATH",
                    "data/altas-control-plane.sqlite3",
                )
            ),
            lease_signing_key=signing_key.encode("utf-8"),
            admin_token=admin_token,
            seed_demo_data=_as_bool(os.getenv("ALTAS_SEED_DEMO_DATA"), default=False),
            mock_model=_as_bool(os.getenv("ALTAS_MODEL_MOCK"), default=True),
            lease_ttl_seconds=int(os.getenv("ALTAS_LEASE_TTL_SECONDS", "300")),
            job_visibility_timeout_seconds=int(
                os.getenv("ALTAS_JOB_VISIBILITY_TIMEOUT_SECONDS", "900")
            ),
            max_model_requests_per_job=int(
                os.getenv("ALTAS_MAX_MODEL_REQUESTS_PER_JOB", "8")
            ),
            max_requested_tokens_per_job=int(
                os.getenv("ALTAS_MAX_REQUESTED_TOKENS_PER_JOB", "4096")
            ),
            default_model_max_tokens=int(
                os.getenv("ALTAS_DEFAULT_MODEL_MAX_TOKENS", "400")
            ),
            model_id=os.getenv("ALTAS_MODEL_ID", "altas-fixed-ops"),
            upstream_base_url=os.getenv(
                "ALTAS_UPSTREAM_BASE_URL", "https://api.openai.com/v1"
            ).rstrip("/"),
            upstream_api_key=os.getenv("ALTAS_UPSTREAM_API_KEY"),
            request_timeout_seconds=float(
                os.getenv("ALTAS_REQUEST_TIMEOUT_SECONDS", "30")
            ),
        )
