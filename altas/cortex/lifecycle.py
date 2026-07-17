"""Detached Cortex lifecycle recovery for agents no longer in memory."""

from __future__ import annotations

from pathlib import Path

from .boundaries import require_semantic_boundary_reason
from .config import CortexConfig
from .runtime import open_cortex_store
from .store import stable_hash


def cortex_boundary_required(hermes_home: str | Path) -> bool:
    """Return whether this profile requires Cortex lifecycle durability.

    Detached gateway paths have no live MemoryManager to identify the active
    provider.  Resolve the exact profile config instead: an Atlas profile that
    selected Cortex must fail closed when its database or lineage is missing,
    while upstream/non-Cortex profiles retain their historical no-op behavior.
    """

    from hermes_cli.config import load_config
    from hermes_constants import (
        reset_hermes_home_override,
        set_hermes_home_override,
    )

    home = Path(hermes_home).expanduser().resolve()
    token = set_hermes_home_override(home)
    try:
        raw = load_config()
    except Exception:
        # A configured Cortex database is durable evidence that this profile
        # used the provider.  Otherwise preserve compatibility for a profile
        # with no usable config and no Cortex state.
        return (home / "cortex" / "cortex.db").exists()
    finally:
        reset_hermes_home_override(token)

    memory = raw.get("memory") if isinstance(raw, dict) else None
    provider = (
        str(memory.get("provider") or "").strip().lower()
        if isinstance(memory, dict)
        else ""
    )
    try:
        enabled = CortexConfig.from_mapping(raw, home).enabled
    except Exception:
        # Invalid Cortex configuration must not downgrade an explicitly
        # selected durable provider into a compatibility no-op.
        return provider == "cortex"
    return enabled and provider == "cortex"


def finalize_detached_session(
    hermes_home: str | Path,
    session_id: str,
    *,
    reason: str = "session_expired",
) -> bool:
    """Finalize an existing local Cortex session without rebuilding an agent.

    Gateway cache eviction may remove the provider before the idle-expiry
    watcher runs. This brain-scoped recovery path only opens the active
    profile's canonical store and only touches a session already present
    there; a caller cannot supply a brain id.
    """
    require_semantic_boundary_reason(reason)
    if not session_id:
        return False
    config = CortexConfig.load(hermes_home)
    required = cortex_boundary_required(hermes_home)
    if not config.database_path.exists():
        return not required
    store, _ = open_cortex_store(hermes_home, {})
    lineage = store.session_lineage(session_id)
    if not lineage:
        return not required
    if lineage.get("state") in {"finalized", "reset", "deleted"}:
        return True

    store.finalize_session(
        session_id,
        state="finalized",
        enqueue_distill=config.capture_enabled,
    )
    del reason
    from .scheduler import reconcile_cortex_runtime

    supervisor = reconcile_cortex_runtime(store, config)
    if supervisor is not None:
        supervisor.wake()
    return True


def commit_detached_session_boundary(
    hermes_home: str | Path,
    session_id: str,
    *,
    new_session_id: str,
    parent_session_id: str = "",
    reason: str = "new_session",
    reset: bool = True,
) -> bool:
    """Atomically finalize one detached lineage and prepare its target.

    Gateway reset/end commands may outlive the soft agent cache. This is
    the detached equivalent of ``CortexMemoryProvider.commit_session_boundary``:
    the immutable distillation job and the target's lineage row commit in one
    SQLite transaction, so a caller never publishes a branch that Cortex sees
    as an unrelated root.
    """

    require_semantic_boundary_reason(reason)
    if not session_id or not new_session_id:
        return False
    config = CortexConfig.load(hermes_home)
    required = cortex_boundary_required(hermes_home)
    if not config.database_path.exists():
        return not required
    store, _ = open_cortex_store(hermes_home, {})
    if not store.session_lineage(session_id):
        return not required

    if reset:
        logical_conversation_id = new_session_id
    else:
        parent_lineage = (
            store.session_lineage(parent_session_id) if parent_session_id else {}
        )
        logical_conversation_id = str(
            parent_lineage.get("logical_conversation_id") or new_session_id
        )

    def prepare_target(_connection) -> None:
        store.ensure_session(
            new_session_id,
            parent_session_id=parent_session_id,
            logical_conversation_id=logical_conversation_id,
        )

    state = "reset" if reason in {"new_session", "reset"} else "finalized"
    store.finalize_session(
        session_id,
        state=state,
        enqueue_distill=config.capture_enabled,
        commit_callback=prepare_target,
    )
    if config.capture_enabled:
        from .scheduler import reconcile_cortex_runtime

        supervisor = reconcile_cortex_runtime(store, config)
        if supervisor is not None:
            supervisor.wake()
    return True


def prepare_detached_session_branch(
    hermes_home: str | Path,
    session_id: str,
    *,
    new_session_id: str,
    parent_session_id: str = "",
) -> bool:
    """Create one child lineage without finalizing its source session.

    A fork is a deterministic topology operation, not proof that the customer
    ended the source conversation.  This helper therefore creates (or verifies)
    the child and deliberately writes no boundary admission or semantic job.
    """

    if not session_id or not new_session_id:
        return False
    config = CortexConfig.load(hermes_home)
    required = cortex_boundary_required(hermes_home)
    if not config.database_path.exists():
        return not required
    store, _ = open_cortex_store(hermes_home, {})
    source = store.session_lineage(session_id)
    if not source:
        return not required

    parent_id = parent_session_id or session_id
    target = store.session_lineage(new_session_id)
    if target:
        return (
            str(target.get("parent_session_id") or "") == parent_id
            and str(target.get("logical_conversation_id") or "") == new_session_id
        )

    store.ensure_session(
        new_session_id,
        parent_session_id=parent_id,
        logical_conversation_id=new_session_id,
    )
    target = store.session_lineage(new_session_id)
    return (
        str(target.get("parent_session_id") or "") == parent_id
        and str(target.get("logical_conversation_id") or "") == new_session_id
    )


def prepare_detached_session_resume(
    hermes_home: str | Path,
    session_id: str,
    *,
    new_session_id: str,
) -> bool:
    """Validate/prepare a resume target without ending or rebinding its source.

    Gateway routing is compare-and-swapped only after this locally bounded
    check succeeds. Existing targets are deliberately *not* reopened here: the
    newly constructed target agent reopens them after the routing CAS wins.
    That keeps a failed CAS from mutating either logical conversation while
    still allowing pre-Cortex legacy transcripts to acquire a native root.
    """

    if not session_id or not new_session_id:
        return False
    config = CortexConfig.load(hermes_home)
    required = cortex_boundary_required(hermes_home)
    if not config.database_path.exists():
        return not required
    store, _ = open_cortex_store(hermes_home, {})
    target = store.session_lineage(new_session_id)
    if target:
        return str(target.get("state") or "") != "deleted"
    store.ensure_session(
        new_session_id,
        logical_conversation_id=new_session_id,
    )
    return bool(store.session_lineage(new_session_id))


def discard_detached_session_branch(
    hermes_home: str | Path,
    session_id: str,
    *,
    new_session_id: str,
) -> bool:
    """Hide an unpublished empty branch after its routing publication loses.

    Compensation is deliberately non-semantic: it refuses a child that gained
    evidence and marks only the still-empty child deleted with distillation
    disabled. The source conversation remains active and untouched.
    """

    if not session_id or not new_session_id:
        return False
    config = CortexConfig.load(hermes_home)
    required = cortex_boundary_required(hermes_home)
    if not config.database_path.exists():
        return not required
    store, _ = open_cortex_store(hermes_home, {})
    target = store.session_lineage(new_session_id)
    if not target:
        return True
    if (
        str(target.get("parent_session_id") or "") != session_id
        or str(target.get("logical_conversation_id") or "") != new_session_id
    ):
        return False
    spec = store.lineage_distill_spec(new_session_id)
    evidence_ids = spec.get("input_data", {}).get("evidence_ids", [])
    if evidence_ids:
        return False
    store.finalize_session(
        new_session_id,
        state="deleted",
        enqueue_distill=False,
    )
    return True


def reconcile_detached_rewind(
    hermes_home: str | Path,
    session_id: str,
    source_row_ids: list[int | str],
) -> bool:
    """Apply a rewind when the gateway no longer has a cached agent."""
    if not session_id or not source_row_ids:
        return False
    config = CortexConfig.load(hermes_home)
    if not config.enabled or not config.database_path.exists():
        return False
    store, _ = open_cortex_store(hermes_home, {})
    if not store.session_lineage(session_id):
        return False
    # ``reconcile_rewind`` and ``enqueue_job`` each own a transaction. Keeping
    # an outer transaction here deadlocks SQLite's non-reentrant write path and
    # is unnecessary: the rewind is the durable privacy/correctness boundary;
    # the follow-up job is only a deterministic index-reconciliation hint.
    reconciled = store.reconcile_rewind(session_id, source_row_ids)
    store.enqueue_job(
        "session_reconcile",
        input_hash=stable_hash(
            session_id,
            "rewound",
            *[str(value) for value in source_row_ids],
        ),
        input_data={
            "session_id": session_id,
            "rewound": True,
            "source_row_ids": list(source_row_ids),
            "reconciled_evidence_count": reconciled,
            "recovered_without_agent": True,
        },
    )
    return True


def reconcile_detached_session_delete(
    hermes_home: str | Path,
    session_id: str,
) -> bool:
    """Revoke and permanently scrub one Cortex lineage before transcript deletion.

    An absent database or absent lineage means this profile has no Cortex data
    for the requested session. An existing store failure propagates so callers
    can leave SessionDB untouched and retry instead of creating a split delete.
    """

    if not session_id:
        return False
    config = CortexConfig.load(hermes_home)
    if not config.database_path.exists():
        return True
    store, _ = open_cortex_store(hermes_home, {})
    if not store.session_lineage(session_id):
        return True
    store.reconcile_session_delete(session_id)
    return store.session_lineage(session_id).get("state") == "deleted"


def reconcile_detached_session_deletions(
    hermes_home: str | Path,
    session_ids: list[str] | tuple[str, ...],
) -> tuple[str, ...]:
    """Reconcile a pre-expanded SessionDB deletion set fail-closed.

    ``session_ids`` must come from ``SessionDB.get_session_delete_closure``
    for an explicit customer delete.  Multiple physical compression segments
    share one Cortex logical ID, so each logical lineage is reconciled only
    once while independent delegate lineages are reconciled separately.
    """

    ordered_ids = tuple(
        session_id
        for session_id in dict.fromkeys(session_ids)
        if isinstance(session_id, str) and session_id
    )
    if not ordered_ids:
        return ()
    config = CortexConfig.load(hermes_home)
    if not config.database_path.exists():
        return ordered_ids
    store, _ = open_cortex_store(hermes_home, {})
    reconciled_logical_ids: set[str] = set()
    for session_id in ordered_ids:
        lineage = store.session_lineage(session_id)
        if not lineage:
            continue
        logical_id = str(lineage.get("logical_conversation_id") or session_id)
        if logical_id in reconciled_logical_ids:
            continue
        store.reconcile_session_delete(session_id)
        reconciled_logical_ids.add(logical_id)
    for session_id in ordered_ids:
        lineage = store.session_lineage(session_id)
        if lineage and lineage.get("state") != "deleted":
            raise RuntimeError(
                f"Cortex privacy reconciliation did not delete {session_id}"
            )
    return ordered_ids


__all__ = [
    "commit_detached_session_boundary",
    "cortex_boundary_required",
    "discard_detached_session_branch",
    "finalize_detached_session",
    "prepare_detached_session_branch",
    "prepare_detached_session_resume",
    "reconcile_detached_session_delete",
    "reconcile_detached_session_deletions",
    "reconcile_detached_rewind",
]
