"""Fail-closed coordination for customer-visible session deletion."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from hermes_cli.active_sessions import claim_session_deletion


Reconciler = Callable[[Path, list[str]], Sequence[str]]


def _ordered_ids(session_ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        session_id
        for session_id in dict.fromkeys(session_ids)
        if isinstance(session_id, str) and session_id
    )


def session_activity_aliases(db: Any, session_ids: Iterable[str]) -> tuple[str, ...]:
    """Return durable ids plus gateway routing keys protecting those rows."""
    aliases: list[str] = list(_ordered_ids(session_ids))
    get_session = getattr(db, "get_session", None)
    if not callable(get_session):
        return tuple(aliases)
    for session_id in tuple(aliases):
        row = get_session(session_id)
        if not isinstance(row, dict):
            continue
        session_key = str(row.get("session_key") or "").strip()
        if session_key and session_key not in aliases:
            aliases.append(session_key)
    return tuple(aliases)


def _default_reconciler(profile_home: Path, session_ids: list[str]) -> Sequence[str]:
    from altas.cortex.lifecycle import reconcile_detached_session_deletions

    return reconcile_detached_session_deletions(profile_home, session_ids)


def delete_sessions_with_cortex(
    db: Any,
    profile_home: str | Path,
    session_ids: Iterable[str],
    *,
    sessions_dir: Path | None = None,
    reconciler: Reconciler | None = None,
    ignore_lease_ids: Iterable[str] = (),
    allowed_active_ids: Iterable[str] = (),
) -> tuple[tuple[str, ...], int]:
    """Reconcile and delete one pre-expanded explicit scope atomically.

    The registry lock excludes live processes while SessionDB's immediate
    transaction freezes the exact transcript rows. Cortex runs before any row
    deletion; once it succeeds, durable tombstones prevent stale resumers from
    recreating those ids even if the subsequent SQLite delete fails.
    """
    ordered = _ordered_ids(session_ids)
    if not ordered:
        return (), 0
    home = Path(profile_home).expanduser().resolve()
    aliases = session_activity_aliases(db, ordered)
    reconcile = reconciler or _default_reconciler
    with claim_session_deletion(
        ordered,
        active_aliases=aliases,
        hermes_home=home,
        ignore_lease_ids=ignore_lease_ids,
    ) as deletion:

        def before_delete(exact_ids: tuple[str, ...]) -> None:
            if exact_ids != ordered:
                raise RuntimeError(
                    "session deletion scope changed before privacy reconciliation"
                )
            reconciled = tuple(reconcile(home, list(exact_ids)))
            if reconciled != exact_ids:
                raise RuntimeError(
                    "Cortex privacy reconciliation returned a different deletion scope"
                )
            deletion.seal(exact_ids)

        deleted = db.delete_sessions(
            list(ordered),
            sessions_dir=sessions_dir,
            before_delete=before_delete,
            exact_scope=True,
            validate_related_scope=True,
            require_ended=True,
            allowed_active_ids=tuple(allowed_active_ids),
        )
        if deleted != len(ordered):
            raise RuntimeError(
                "session deletion scope changed after privacy reconciliation"
            )
    return ordered, deleted


def delete_empty_sessions_with_cortex(
    db: Any,
    profile_home: str | Path,
    *,
    sessions_dir: Path | None = None,
    reconciler: Reconciler | None = None,
) -> tuple[tuple[str, ...], int]:
    """Delete only the exact empty-session snapshot reconciled with Cortex."""
    candidates = _ordered_ids(db.list_empty_session_ids())
    if not candidates:
        return (), 0
    home = Path(profile_home).expanduser().resolve()
    aliases = session_activity_aliases(db, candidates)
    reconcile = reconciler or _default_reconciler
    reconciled_ids: tuple[str, ...] = ()
    with claim_session_deletion(
        candidates,
        active_aliases=aliases,
        hermes_home=home,
    ) as deletion:

        def before_delete(exact_ids: tuple[str, ...]) -> None:
            nonlocal reconciled_ids
            reconciled = tuple(reconcile(home, list(exact_ids)))
            if reconciled != exact_ids:
                raise RuntimeError(
                    "Cortex privacy reconciliation returned a different deletion scope"
                )
            deletion.seal(exact_ids)
            reconciled_ids = exact_ids

        deleted = db.delete_empty_sessions(
            sessions_dir=sessions_dir,
            candidate_ids=candidates,
            before_delete=before_delete,
        )
        if deleted != len(reconciled_ids):
            raise RuntimeError(
                "empty-session deletion scope changed after privacy reconciliation"
            )
    return reconciled_ids, deleted


__all__ = [
    "delete_empty_sessions_with_cortex",
    "delete_sessions_with_cortex",
    "session_activity_aliases",
]
