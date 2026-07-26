"""Path/interface claim leases and overlap detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import fnmatch
from pathlib import PurePosixPath
from uuid import uuid4

from .state import StateStore


class ClaimConflict(RuntimeError):
    """An active claim overlaps another owner."""


def _now() -> datetime:
    return datetime.now(UTC)


def _path_overlap(left: str, right: str) -> bool:
    left = str(PurePosixPath(left))
    right = str(PurePosixPath(right))
    return (
        left == right
        or left.startswith(f"{right}/")
        or right.startswith(f"{left}/")
        or fnmatch.fnmatch(left, right)
        or fnmatch.fnmatch(right, left)
    )


def acquire_claim(
    store: StateStore,
    *,
    task_id: str,
    agent_id: str,
    kind: str,
    resource: str,
    ttl_seconds: int = 3600,
) -> str:
    if kind not in {"path", "interface"}:
        raise ValueError("claim kind must be path or interface")
    if not 60 <= ttl_seconds <= 86400:
        raise ValueError("claim lease must be between 60 seconds and 24 hours")
    now = _now()
    rows = store.connection.execute(
        """
        SELECT agent_id, resource FROM claims
        WHERE task_id = ? AND kind = ? AND released_at IS NULL AND expires_at > ?
        """,
        (task_id, kind, now.isoformat()),
    ).fetchall()
    for row in rows:
        overlaps = (
            _path_overlap(resource, row["resource"])
            if kind == "path"
            else resource == row["resource"]
        )
        if overlaps and row["agent_id"] != agent_id:
            raise ClaimConflict(
                f"{kind} {resource!r} overlaps active claim by {row['agent_id']}"
            )
    claim_id = f"claim-{uuid4()}"
    store.connection.execute(
        """
        INSERT INTO claims(
            claim_id, task_id, agent_id, kind, resource, acquired_at, expires_at
        ) VALUES(?, ?, ?, ?, ?, ?, ?)
        """,
        (
            claim_id,
            task_id,
            agent_id,
            kind,
            resource,
            now.isoformat(),
            (now + timedelta(seconds=ttl_seconds)).isoformat(),
        ),
    )
    store.connection.commit()
    return claim_id


def release_claims(store: StateStore, task_id: str, agent_id: str | None = None) -> int:
    query = (
        "UPDATE claims SET released_at = ? WHERE task_id = ? AND released_at IS NULL"
    )
    values: list[str] = [_now().isoformat(), task_id]
    if agent_id:
        query += " AND agent_id = ?"
        values.append(agent_id)
    cursor = store.connection.execute(query, values)
    store.connection.commit()
    return cursor.rowcount
