"""Privacy-safe SessionDB retention coordinated with Atlas Cortex."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from hermes_state import SessionDB

from .config import CortexConfig
from .runtime import open_cortex_store
from .store import CortexStore


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _RetentionPlan:
    rows: tuple[dict[str, Any], ...]
    store: CortexStore | None
    cortex_present: bool


def _all_session_rows(session_db: SessionDB) -> list[dict[str, Any]]:
    """Read physical SessionDB rows without the projected-session view."""

    rows: list[dict[str, Any]] = []
    offset = 0
    page_size = 1_000
    while True:
        page = session_db.search_sessions(limit=page_size, offset=offset)
        rows.extend(page)
        if len(page) < page_size:
            return rows
        offset += len(page)


def _session_kind_metadata(row: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = row.get("model_config")
    if not raw:
        return {}
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _compression_edges(
    rows: Iterable[Mapping[str, Any]],
) -> Iterable[tuple[str, str]]:
    by_id = {str(row.get("id") or ""): row for row in rows if row.get("id")}
    for child_id, child in by_id.items():
        parent_id = str(child.get("parent_session_id") or "")
        parent = by_id.get(parent_id)
        if parent is None or str(parent.get("end_reason") or "") not in {
            "compression",
            "compressed",
        }:
            continue
        metadata = _session_kind_metadata(child)
        if metadata.get("_branched_from") is not None or metadata.get(
            "_delegate_from"
        ) is not None:
            continue
        yield parent_id, child_id


def _cortex_logical_groups(store: CortexStore) -> dict[str, set[str]]:
    groups: dict[str, set[str]] = defaultdict(set)
    with store.connect() as connection:
        rows = connection.execute(
            "SELECT id, logical_conversation_id FROM sessions WHERE brain_id=?",
            (store.brain_id,),
        ).fetchall()
    for row in rows:
        groups[str(row["logical_conversation_id"] or row["id"])].add(
            str(row["id"])
        )
    return dict(groups)


def _retention_components(
    session_rows: Iterable[Mapping[str, Any]],
    cortex_groups: Mapping[str, set[str]],
) -> dict[str, frozenset[str]]:
    """Combine physical compression and Cortex logical lineage identity."""

    rows = list(session_rows)
    physical_ids = {
        str(row.get("id") or "") for row in rows if str(row.get("id") or "")
    }
    parent = {session_id: session_id for session_id in physical_ids}

    def find(session_id: str) -> str:
        root = session_id
        while parent[root] != root:
            root = parent[root]
        while parent[session_id] != session_id:
            next_id = parent[session_id]
            parent[session_id] = root
            session_id = next_id
        return root

    def union(left: str, right: str) -> None:
        if left not in parent or right not in parent:
            return
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left, right in _compression_edges(rows):
        union(left, right)
    for members in cortex_groups.values():
        existing = [session_id for session_id in members if session_id in physical_ids]
        if not existing:
            continue
        anchor = existing[0]
        for session_id in existing[1:]:
            union(anchor, session_id)

    grouped: dict[str, set[str]] = defaultdict(set)
    for session_id in physical_ids:
        grouped[find(session_id)].add(session_id)
    return {
        session_id: frozenset(grouped[find(session_id)])
        for session_id in physical_ids
    }


def _reconcile_cortex_before_delete(
    store: CortexStore,
    session_ids: tuple[str, ...],
) -> None:
    """Revoke/tombstone every selected logical lineage without model work."""

    logical_ids: set[str] = set()
    for session_id in session_ids:
        lineage = store.session_lineage(session_id)
        if not lineage:
            continue
        logical_id = str(lineage.get("logical_conversation_id") or session_id)
        if logical_id in logical_ids:
            continue
        with store.connect() as connection:
            rows = connection.execute(
                "SELECT id, state FROM sessions WHERE brain_id=? "
                "AND logical_conversation_id=? ORDER BY started_at, id",
                (store.brain_id, logical_id),
            ).fetchall()
        live_rows = [row for row in rows if str(row["state"]) != "deleted"]
        if live_rows:
            store.reconcile_session_delete(str(live_rows[-1]["id"]))
        logical_ids.add(logical_id)

    for session_id in session_ids:
        lineage = store.session_lineage(session_id)
        if lineage and lineage.get("state") != "deleted":
            raise RuntimeError(
                f"Cortex retention reconciliation did not delete {session_id}"
            )


def _build_retention_plan(
    hermes_home: str | Path,
    session_db: SessionDB,
    *,
    older_than_days: float | None,
    source: str | None,
    filters: Mapping[str, Any],
) -> _RetentionPlan:
    home = Path(hermes_home).expanduser().resolve()
    config = CortexConfig.load(home)
    candidates = tuple(
        session_db.list_prune_candidates(
            older_than_days=older_than_days,
            source=source,
            **filters,
        )
    )
    if not config.database_path.exists():
        return _RetentionPlan(candidates, None, False)
    if not candidates:
        return _RetentionPlan((), None, True)

    store, _ = open_cortex_store(home, {})
    session_rows = _all_session_rows(session_db)
    components = _retention_components(
        session_rows,
        _cortex_logical_groups(store),
    )
    candidate_ids = {
        str(row["id"])
        for row in candidates
        if isinstance(row.get("id"), str) and row["id"]
    }
    approved = tuple(
        row
        for row in candidates
        if isinstance(row.get("id"), str)
        and components.get(str(row["id"]), frozenset({str(row["id"])}))
        <= candidate_ids
    )
    return _RetentionPlan(approved, store, True)


def list_prune_candidates_with_cortex(
    hermes_home: str | Path,
    session_db: SessionDB,
    older_than_days: float | None = None,
    source: str | None = None,
    **filters: Any,
) -> list[dict[str, Any]]:
    """Preview exactly the rows Cortex-aware retention is allowed to delete.

    This has the same filter surface and row shape as
    :meth:`SessionDB.list_prune_candidates`.  With no Cortex database it is a
    transparent pass-through.  With Cortex present, partial logical
    compression lineages are omitted so confirmation counts cannot promise a
    deletion that the privacy-safe executor will correctly refuse.
    """

    plan = _build_retention_plan(
        hermes_home,
        session_db,
        older_than_days=older_than_days,
        source=source,
        filters=filters,
    )
    return list(plan.rows)


def prune_sessions_with_cortex(
    hermes_home: str | Path,
    session_db: SessionDB,
    older_than_days: float | None = 90,
    source: str | None = None,
    sessions_dir: Path | None = None,
    **filters: Any,
) -> int:
    """Prune ended sessions without splitting Cortex compression lineages.

    With no Cortex database this delegates to ``SessionDB.prune_sessions``
    exactly.  When Cortex exists, a logical lineage is eligible only when
    every physical SessionDB segment in that lineage independently matches the
    ended-session prune filters.  Branch and delegate lineages stay
    independent.  Cortex reconciliation runs under SessionDB's exact-delete
    write barrier and before any transcript row is removed; failures therefore
    leave SessionDB untouched.
    """

    home = Path(hermes_home).expanduser().resolve()
    plan = _build_retention_plan(
        home,
        session_db,
        older_than_days=older_than_days,
        source=source,
        filters=filters,
    )
    if not plan.cortex_present:
        return session_db.prune_sessions(
            older_than_days=older_than_days,
            source=source,
            sessions_dir=sessions_dir,
            **filters,
        )

    candidate_ids = tuple(
        str(row["id"])
        for row in plan.rows
        if isinstance(row.get("id"), str) and row["id"]
    )
    if not candidate_ids:
        return 0
    if plan.store is None:
        raise RuntimeError("Cortex retention plan is missing its durable store")

    return session_db.delete_prune_candidates(
        candidate_ids,
        sessions_dir=sessions_dir,
        before_delete=lambda existing: _reconcile_cortex_before_delete(
            plan.store, existing
        ),
    )


def maybe_auto_prune_and_vacuum_with_cortex(
    hermes_home: str | Path,
    session_db: SessionDB,
    retention_days: int = 90,
    min_interval_hours: int = 24,
    vacuum: bool = True,
    sessions_dir: Path | None = None,
) -> dict[str, Any]:
    """Run Cortex-safe startup retention with SessionDB's idempotent policy.

    The ``last_auto_prune`` marker, VACUUM threshold, result shape, and
    never-block-startup failure behavior mirror
    :meth:`SessionDB.maybe_auto_prune_and_vacuum`.  Cortex reconciliation is
    deterministic and cannot enqueue or invoke semantic model work.
    """

    result: dict[str, Any] = {
        "skipped": False,
        "pruned": 0,
        "vacuumed": False,
    }
    try:
        last_raw = session_db.get_meta("last_auto_prune")
        now = time.time()
        if last_raw:
            try:
                if now - float(last_raw) < min_interval_hours * 3_600:
                    result["skipped"] = True
                    return result
            except (TypeError, ValueError):
                pass

        pruned = prune_sessions_with_cortex(
            hermes_home,
            session_db,
            older_than_days=retention_days,
            sessions_dir=sessions_dir,
        )
        result["pruned"] = pruned
        if vacuum and pruned > 0:
            try:
                session_db.vacuum()
                result["vacuumed"] = True
            except Exception as exc:
                logger.warning("state.db VACUUM failed: %s", exc)

        session_db.set_meta("last_auto_prune", str(now))
        if pruned > 0:
            logger.info(
                "state.db Cortex-aware auto-maintenance: pruned %d session(s) "
                "older than %d days%s",
                pruned,
                retention_days,
                " + VACUUM" if result["vacuumed"] else "",
            )
    except Exception as exc:
        logger.warning("state.db Cortex-aware auto-maintenance failed: %s", exc)
        result["error"] = str(exc)
    return result


__all__ = [
    "list_prune_candidates_with_cortex",
    "maybe_auto_prune_and_vacuum_with_cortex",
    "prune_sessions_with_cortex",
]
