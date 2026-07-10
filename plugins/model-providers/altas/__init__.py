"""Altas managed OpenAI-compatible model gateway profile.

Selecting this profile opts the engine process into mandatory Altas policy
enforcement before the model can receive and request tools.
"""

from __future__ import annotations

import os
from typing import Any

from providers import register_provider
from providers.base import ProviderProfile


class AltasGatewayProfile(ProviderProfile):
    """Gateway profile that activates managed enforcement per model request."""

    def prepare_messages(
        self, messages: list[dict[str, object]]
    ) -> list[dict[str, object]]:
        # Provider plugins are discovered eagerly, so managed mode must not be
        # enabled at import time. This hook only runs when the Altas profile is
        # actually selected and runs before a response can contain tool calls.
        os.environ["ALTAS_MANAGED_MODE"] = "1"
        return messages

    def build_api_kwargs_extras(
        self,
        *,
        reasoning_config: dict | None = None,
        **context: Any,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Attach the current job authorization to each gateway request.

        A managed engine process can move between jobs without reconstructing
        the OpenAI client. Reading the environment here, through a hook the
        upstream transport invokes for every request, prevents a lease, claim,
        or job identifier from being frozen when this module is imported.
        """

        del reasoning_config, context
        headers = _managed_headers()
        return {}, {"extra_headers": headers} if headers else {}

    def get_max_tokens(self, model: str | None) -> int:
        """Return the small, environment-configurable prototype output cap."""

        del model
        raw_value = os.getenv("ALTAS_DEFAULT_MODEL_MAX_TOKENS", "400").strip()
        try:
            value = int(raw_value)
        except ValueError:
            return 400
        return value if value > 0 else 400


def _gateway_url() -> str:
    return os.getenv("ALTAS_MODEL_GATEWAY_URL", "http://127.0.0.1:8787/v1").rstrip("/")


def _managed_headers() -> dict[str, str]:
    """Build fresh, request-scoped headers for one managed engine process."""

    mapping = {
        "ALTAS_LEASE_TOKEN": "X-Altas-Lease",
        "ALTAS_TENANT_ID": "X-Altas-Tenant-ID",
        "ALTAS_STORE_ID": "X-Altas-Store-ID",
        "ALTAS_AGENT_ID": "X-Altas-Agent-ID",
        "ALTAS_JOB_ID": "X-Altas-Job-ID",
        "ALTAS_CLAIM_TOKEN": "X-Altas-Claim-Token",
        "ALTAS_CORRELATION_ID": "X-Altas-Correlation-ID",
    }
    return {
        header: value
        for env_name, header in mapping.items()
        if (value := os.getenv(env_name, "").strip())
    }


altas = AltasGatewayProfile(
    name="altas",
    aliases=("altas-gateway", "altas-managed"),
    env_vars=("ALTAS_DEVICE_TOKEN",),
    display_name="Altas Gateway",
    description="Managed model access through the Altas Control Plane",
    base_url=_gateway_url(),
    auth_type="api_key",
    supports_health_check=True,
    supports_vision=False,
    fallback_models=("altas-fixed-ops",),
    # Request-scoped headers are supplied by build_api_kwargs_extras(); never
    # snapshot managed authorization in client-level default headers.
    default_headers={},
    default_max_tokens=400,
)

register_provider(altas)
