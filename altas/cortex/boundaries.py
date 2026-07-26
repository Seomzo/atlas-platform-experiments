"""Shared authorization rules for Cortex semantic session boundaries."""

from __future__ import annotations

import re


_TOPOLOGY_ONLY_REASON_ROOTS = frozenset({"branch", "handoff", "resume"})
_TRUE_SEMANTIC_BOUNDARY_REASONS = frozenset({"compression_exhausted"})
_NON_BOUNDARY_REASON_ROOTS = frozenset({
    "compression",
    "cron",
    "nightly",
    "scheduled",
    "scheduler",
})


def require_semantic_boundary_reason(reason: str) -> None:
    """Reject non-boundary operations at semantic finalization primitives.

    High-level callers have dedicated branch/resume/rebind helpers. Keeping the
    guard at the provider and detached storage boundaries prevents a future
    caller from bypassing those helpers merely by forwarding a topology,
    compression, scheduled-maintenance, or routing-retention reason to a
    primitive that can admit end-of-session model work.
    """

    normalized = re.sub(r"[^a-z0-9]+", "_", str(reason or "").strip().lower()).strip(
        "_"
    )
    if normalized in _TRUE_SEMANTIC_BOUNDARY_REASONS:
        return
    root = normalized.partition("_")[0]
    if root in _TOPOLOGY_ONLY_REASON_ROOTS:
        raise ValueError(
            f"Cortex semantic boundary rejects topology-only reason {reason!r}"
        )
    if root in _NON_BOUNDARY_REASON_ROOTS or normalized.startswith("max_age"):
        raise ValueError(
            f"Cortex semantic boundary rejects non-boundary reason {reason!r}"
        )


__all__ = ["require_semantic_boundary_reason"]
