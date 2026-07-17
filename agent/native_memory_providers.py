"""Registry for product-native memory providers.

Third-party memory integrations remain standalone plugins. This tiny registry
lets branded distributions activate first-party lifecycle implementations
without disguising product code as a bundled third-party plugin.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from agent.memory_provider import MemoryProvider


def cortex_owns_semantic_review(agent: object) -> bool:
    """Return whether Cortex reserves semantic-memory work for session end.

    Selection is enough to reserve ownership. If Cortex initialization is
    temporarily degraded, silently reviving Hermes's legacy per-turn frontier
    review would violate the configured lifecycle and could send personal
    evidence to the conversational model. ``_cortex_memory_active`` remains a
    compatibility fallback for lightweight agents/tests created before the
    selection marker was introduced.
    """

    return bool(
        getattr(agent, "_cortex_memory_selected", False)
        or getattr(agent, "_cortex_memory_active", False)
    )


def discover_native_memory_providers() -> list[tuple[str, str, bool]]:
    from hermes_cli.brand import is_atlas_branded

    if not is_atlas_branded():
        try:
            from hermes_cli.config import cfg_get, load_config

            explicitly_selected = (
                str(cfg_get(load_config(), "memory", "provider") or "").lower()
                == "cortex"
            )
        except Exception:
            explicitly_selected = False
        if not explicitly_selected:
            return []
    try:
        provider = load_native_memory_provider("cortex")
        available = bool(provider and provider.is_available())
    except Exception:
        available = False
    return [
        (
            "cortex",
            "Atlas Cortex — local evidence-backed memory, dream consolidation, and knowledge graph",
            available,
        )
    ]


def load_native_memory_provider(name: str) -> Optional["MemoryProvider"]:
    if str(name).strip().lower() != "cortex":
        return None
    from altas.cortex.provider import CortexMemoryProvider

    return CortexMemoryProvider()
