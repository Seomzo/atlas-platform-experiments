"""Runtime identity and store factory for Atlas Cortex.

This is the single seam used by the agent provider, worker, and HTTP API. It
never accepts a client-supplied brain identifier.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Mapping

from hermes_constants import get_hermes_home

from .config import CortexConfig
from .store import CortexStore


_TRUE = {"1", "true", "yes", "on"}


def _runtime_environment(
    environ: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    """Return the authoritative identity mapping for this Cortex operation."""

    if environ is not None:
        return environ

    from agent.secret_scope import current_secret_scope, is_multiplex_active

    scoped = current_secret_scope()
    if scoped is not None:
        return scoped
    if is_multiplex_active():
        raise PermissionError(
            "Atlas Cortex requires an active profile secret scope while multiplexing"
        )
    return os.environ


def _managed_mode(environ: Mapping[str, Any]) -> bool:
    if str(environ.get("ATLAS_MANAGED_MODE") or "").strip().lower() in _TRUE:
        return True
    # The mode flag is deliberately request/process ephemeral, while these
    # deployment bindings are stable in a managed profile. Infer the same
    # owner boundary in the foreground agent and maintenance worker so they
    # can never open parallel local/managed brains for one customer.
    return all(
        str(environ.get(name) or "").strip()
        for name in ("ATLAS_TENANT_ID", "ATLAS_STORE_ID", "ATLAS_AGENT_ID")
    )


def resolve_owner_customer_id(
    hermes_home: str | Path,
    runtime_identity: Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, Any] | None = None,
) -> str:
    """Resolve the only owner key permitted to open this profile's brain.

    Managed mode fails closed unless authenticated tenant/store/agent context
    is present. Local mode derives a non-reversible identifier from the
    resolved profile path, so separate profile homes can never share a brain
    accidentally. Atlas profiles remain a UX boundary, not a claim of OS-level
    multi-tenant isolation.
    """
    values = _runtime_environment(environ)
    explicit_customer = str(values.get("ATLAS_CUSTOMER_ID") or "").strip()
    if _managed_mode(values):
        # Only deployment-controlled environment values may select a managed
        # brain. Runtime/user payload fields are useful as principals *inside*
        # that brain, but are never an authorization boundary.
        tenant = str(values.get("ATLAS_TENANT_ID") or "").strip()
        store = str(values.get("ATLAS_STORE_ID") or "").strip()
        agent_id = str(values.get("ATLAS_AGENT_ID") or "").strip()
        if explicit_customer:
            if not agent_id:
                raise PermissionError(
                    "Atlas Cortex managed mode requires ATLAS_AGENT_ID"
                )
            return f"managed:{explicit_customer}:{agent_id}"
        if not tenant or not store or not agent_id:
            raise PermissionError(
                "Atlas Cortex managed mode requires deployment-authenticated "
                "ATLAS_TENANT_ID, ATLAS_STORE_ID, and ATLAS_AGENT_ID"
            )
        return f"managed:{tenant}:{store}:{agent_id}"

    home = Path(hermes_home).expanduser().resolve()
    # The resolved profile home is already the canonical local brain boundary.
    # Do not also hash a mutable/sticky display profile name: multiplexed
    # gateways can address the same home through different launcher contexts,
    # which would make the rightful owner unable to reopen its own database.
    seed = f"{explicit_customer}|{home}".encode("utf-8", errors="replace")
    return f"local:{hashlib.sha256(seed).hexdigest()}"


def open_cortex_store(
    hermes_home: str | Path | None = None,
    runtime_identity: Mapping[str, Any] | None = None,
    *,
    environ: Mapping[str, Any] | None = None,
) -> tuple[CortexStore, CortexConfig]:
    """Open and initialize the Cortex store for the active authenticated scope."""
    home = Path(hermes_home) if hermes_home is not None else get_hermes_home()
    config = CortexConfig.load(home)
    if not config.enabled:
        raise RuntimeError("Atlas Cortex is disabled for this profile")
    owner_id = resolve_owner_customer_id(
        home,
        runtime_identity,
        environ=environ,
    )
    display_name = "My Atlas"
    if runtime_identity:
        candidate = runtime_identity.get("display_name") or runtime_identity.get(
            "user_name"
        )
        if candidate and str(candidate).strip():
            display_name = f"{str(candidate).strip()}'s Atlas"
    store = CortexStore(
        config.database_path,
        owner_customer_id=owner_id,
        display_name=display_name,
        timezone_name=config.timezone,
        redact_secrets=config.redact_secrets,
        recall_graph_hops=config.recall_graph_hops,
        graphrag_max_items=config.graphrag_max_items,
    )
    store.initialize()
    return store, config
