"""Deterministic circuit breakers for agent turn routing."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import CollaborationEvent
from .state import StateStore


class RoutingRejected(RuntimeError):
    """An event must not start another agent turn."""


class Router:
    def __init__(
        self,
        store: StateStore,
        *,
        turn_budget: int = 40,
        max_failures: int = 3,
    ):
        self.store = store
        self.turn_budget = turn_budget
        self.max_failures = max_failures

    def authorize(
        self,
        event: CollaborationEvent,
        *,
        target_agent_id: str,
        target_role: str,
    ) -> None:
        event.validate()
        task = self.store.task(event.task_id)
        if task is None:
            raise RoutingRejected("unknown task")
        if task["state"] in {
            "paused",
            "canceled",
            "completed",
            "human_approval_required",
        }:
            raise RoutingRejected(f"task state {task['state']} does not allow a turn")
        if not event.can_trigger_turn:
            raise RoutingRejected(
                "event type/recipient/hop policy does not trigger a turn"
            )
        if target_agent_id not in event.intended_for:
            raise RoutingRejected("target is not an intended recipient")
        if event.actor_id == target_agent_id:
            raise RoutingRejected("self-triggering is forbidden")
        if event.actor_role != "coordinator" and target_role != "coordinator":
            raise RoutingRejected(
                "worker-to-worker turns must route through coordinator"
            )
        if task["turn_count"] >= self.turn_budget:
            raise RoutingRejected("task turn budget exhausted")
        if task["failure_count"] >= self.max_failures:
            raise RoutingRejected("task circuit breaker is open")
        active = self.store.connection.execute(
            """
            SELECT 1 FROM turns
            WHERE task_id = ? AND finished_at IS NULL
            LIMIT 1
            """,
            (event.task_id,),
        ).fetchone()
        if active:
            raise RoutingRejected("a routed task turn is already active")

    def start(
        self,
        event: CollaborationEvent,
        *,
        target_agent_id: str,
        target_role: str,
        session_id: str,
    ) -> None:
        self.authorize(
            event,
            target_agent_id=target_agent_id,
            target_role=target_role,
        )
        timestamp = datetime.now(UTC).isoformat()
        with self.store.transaction() as connection:
            connection.execute(
                """
                INSERT INTO turns(
                    task_id, agent_id, session_id, status, causation_id, started_at
                ) VALUES(?, ?, ?, 'active', ?, ?)
                """,
                (
                    event.task_id,
                    target_agent_id,
                    session_id,
                    event.event_id,
                    timestamp,
                ),
            )
            connection.execute(
                "UPDATE tasks SET turn_count = turn_count + 1 WHERE task_id = ?",
                (event.task_id,),
            )

    def finish(
        self,
        *,
        task_id: str,
        agent_id: str,
        success: bool,
    ) -> None:
        timestamp = datetime.now(UTC).isoformat()
        with self.store.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE turns
                SET status = ?, finished_at = ?
                WHERE task_id = ? AND agent_id = ? AND finished_at IS NULL
                """,
                ("complete" if success else "failed", timestamp, task_id, agent_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"no active turn for {task_id}/{agent_id}")
            if not success:
                connection.execute(
                    "UPDATE tasks SET failure_count = failure_count + 1 WHERE task_id = ?",
                    (task_id,),
                )

    def run(
        self,
        runtime: Any,
        event: CollaborationEvent,
        *,
        target_agent_id: str,
        target_role: str,
        session_id: str,
        worktree: Path,
        prompt: str,
    ) -> dict[str, str]:
        self.start(
            event,
            target_agent_id=target_agent_id,
            target_role=target_role,
            session_id=session_id,
        )
        try:
            result = runtime.start_turn(
                session_id=session_id,
                worktree=worktree,
                prompt=prompt,
            )
        except Exception:
            self.finish(
                task_id=event.task_id,
                agent_id=target_agent_id,
                success=False,
            )
            raise
        self.finish(
            task_id=event.task_id,
            agent_id=target_agent_id,
            success=True,
        )
        return result
