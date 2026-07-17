"""Atlas managed OpenAI-compatible model gateway profile.

Selecting this profile opts the engine process into mandatory Atlas policy
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
        # Request authorization is installed by the authenticated managed
        # dispatcher in a context-local secret scope. Never turn managed mode
        # on process-wide here: one multiplexed process can serve unrelated
        # profiles and concurrent jobs.
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
        raw_value = os.getenv("ATLAS_DEFAULT_MODEL_MAX_TOKENS", "400").strip()
        try:
            value = int(raw_value)
        except ValueError:
            return 400
        return value if value > 0 else 400


def _gateway_url() -> str:
    return os.getenv("ATLAS_MODEL_GATEWAY_URL", "http://127.0.0.1:8787/v1").rstrip("/")


def _managed_headers() -> dict[str, str]:
    """Build fresh, request-scoped headers for one managed engine process."""

    # In a multiplex gateway the active profile's credentials live in a
    # context-local mapping.  ``get_secret`` is intentionally fail-closed when
    # multiplexing is active without that scope, preventing a fallback to a
    # different customer's process-global environment.  Outside multiplex
    # mode it preserves the legacy os.environ behavior.
    from agent.secret_scope import get_secret

    mapping = {
        "ATLAS_LEASE_TOKEN": "X-Atlas-Lease",
        "ATLAS_TENANT_ID": "X-Atlas-Tenant-ID",
        "ATLAS_STORE_ID": "X-Atlas-Store-ID",
        "ATLAS_AGENT_ID": "X-Atlas-Agent-ID",
        "ATLAS_JOB_ID": "X-Atlas-Job-ID",
        "ATLAS_CLAIM_TOKEN": "X-Atlas-Claim-Token",
        "ATLAS_CORRELATION_ID": "X-Atlas-Correlation-ID",
        "ATLAS_CORTEX_DISPATCH_ADMISSION": "X-Atlas-Cortex-Dispatch-Admission",
        "ATLAS_CORTEX_DISPATCH_KEY": "X-Atlas-Cortex-Dispatch-Key",
    }
    return {
        header: value
        for env_name, header in mapping.items()
        if (value := str(get_secret(env_name, "") or "").strip())
    }


altas = AltasGatewayProfile(
    name="altas",
    aliases=("altas-gateway", "altas-managed"),
    env_vars=("ATLAS_DEVICE_TOKEN",),
    display_name="Atlas Gateway",
    description="Managed model access through the Atlas Control Plane",
    base_url=_gateway_url(),
    auth_type="api_key",
    supports_health_check=True,
    supports_vision=False,
    # The Cortex alias is an internal maintenance route and must never appear
    # in ordinary chat model discovery.
    fallback_models=("altas-fixed-ops",),
    # Request-scoped headers are supplied by build_api_kwargs_extras(); never
    # snapshot managed authorization in client-level default headers.
    default_headers={},
    default_max_tokens=400,
)

register_provider(altas)
