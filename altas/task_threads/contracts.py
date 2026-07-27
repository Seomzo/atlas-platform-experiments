"""Wire-contract constants shared by the Task Thread store and gateway."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

EVENT_SCHEMA_VERSION = 1

THREAD_STATUSES = frozenset(
    {
        "queued",
        "starting",
        "running",
        "waiting_user",
        "waiting_approval",
        "completed",
        "failed",
        "interrupted",
        "archived",
    }
)
TURN_KINDS = frozenset({"initial", "followup", "steer"})
TURN_STATUSES = frozenset(
    {"queued", "running", "completed", "failed", "interrupted"}
)
APPROVAL_CHOICES = frozenset({"once", "session", "always", "deny"})
WORKSPACE_MODES = frozenset(
    {"none", "existing_project", "isolated_worktree"}
)

DB_TO_THREAD_STATUS = {
    "triage": "waiting_user",
    "todo": "queued",
    "scheduled": "queued",
    "ready": "queued",
    "starting": "starting",
    "running": "running",
    "waiting_user": "waiting_user",
    "waiting_approval": "waiting_approval",
    "blocked": "waiting_user",
    "review": "waiting_user",
    "done": "completed",
    "failed": "failed",
    "cancelled": "interrupted",
    "archived": "archived",
}
THREAD_TO_DB_STATUS = {
    "queued": "ready",
    "starting": "starting",
    "running": "running",
    "waiting_user": "waiting_user",
    "waiting_approval": "waiting_approval",
    "completed": "done",
    "failed": "failed",
    "interrupted": "cancelled",
    "archived": "archived",
}

TERMINAL_THREAD_STATUSES = frozenset(
    {"completed", "failed", "interrupted", "archived"}
)


def require_non_empty(value: Any, field: str) -> str:
    """Return a stripped string or raise a contract-shaped ValueError."""
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


def iso_utc(timestamp: int | float | None) -> str | None:
    """Format a Unix timestamp as canonical UTC JSON text."""
    if timestamp is None:
        return None
    return (
        datetime.fromtimestamp(float(timestamp), tz=timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
