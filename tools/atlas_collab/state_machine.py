"""Coordinator-owned task state transitions."""

from __future__ import annotations

from .models import ContractError

STATES = frozenset({
    "needs-clarification",
    "intake",
    "context_sync",
    "planning",
    "plan_review",
    "ready",
    "executing",
    "blocked",
    "replanning",
    "review_requested",
    "reviewing",
    "changes_requested",
    "integration_ready",
    "integrating",
    "human_approval_required",
    "completed",
    "canceled",
    "paused",
})
TRANSITIONS = {
    "needs-clarification": {"intake", "canceled"},
    "intake": {"context_sync", "needs-clarification", "canceled"},
    "context_sync": {"planning", "blocked", "canceled"},
    "planning": {"plan_review", "blocked", "canceled"},
    "plan_review": {"ready", "replanning", "blocked", "canceled"},
    "ready": {"executing", "blocked", "canceled"},
    "executing": {"blocked", "replanning", "review_requested", "canceled"},
    "blocked": {"replanning", "executing", "canceled"},
    "replanning": {"plan_review", "blocked", "canceled"},
    "review_requested": {"reviewing", "blocked", "canceled"},
    "reviewing": {"changes_requested", "integration_ready", "blocked", "canceled"},
    "changes_requested": {"executing", "replanning", "canceled"},
    "integration_ready": {"integrating", "blocked", "canceled"},
    "integrating": {"human_approval_required", "blocked", "canceled"},
    "human_approval_required": {"completed", "changes_requested", "canceled"},
    "paused": set(),
    "completed": set(),
    "canceled": set(),
}
TERMINAL_STATES = frozenset({"completed", "canceled"})


def assert_transition(current: str, target: str, *, actor_role: str) -> None:
    if actor_role != "coordinator":
        raise ContractError("only the coordinator may transition task state")
    if current not in STATES or target not in STATES:
        raise ContractError(f"unknown task state transition: {current} -> {target}")
    if current == target:
        return
    if target == "paused" and current not in TERMINAL_STATES:
        return
    if current == "paused":
        if target in TERMINAL_STATES:
            return
        raise ContractError("resume must restore the recorded pre-pause state")
    if target not in TRANSITIONS[current]:
        raise ContractError(f"unsafe task state transition: {current} -> {target}")
