"""Kanban-backed persistence and event projection for Atlas Task Threads.

Kanban ``tasks`` and ``task_events`` remain authoritative. The Atlas tables
below store only interactive-runtime linkage, turns, voice focus, and stable
approval correlation; they do not introduce another task scheduler or event
log.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from hermes_cli import kanban_db as kb

from .contracts import (
    APPROVAL_CHOICES,
    DB_TO_THREAD_STATUS,
    EVENT_SCHEMA_VERSION,
    TERMINAL_THREAD_STATUSES,
    THREAD_STATUSES,
    THREAD_TO_DB_STATUS,
    TURN_KINDS,
    TURN_STATUSES,
    WORKSPACE_MODES,
    iso_utc,
    require_non_empty,
)

_EVENT_KIND_PREFIXES = (
    "approval.",
    "thread.",
    "turn.",
    "worker.",
    "workspace.",
)

_ORPHANED_RUNTIME_REASON = (
    "Gateway restarted before the active Task Thread turn completed."
)

_ADAPTER_SCHEMA = """
CREATE TABLE IF NOT EXISTS atlas_task_threads (
    task_id             TEXT PRIMARY KEY,
    worker_profile_id   TEXT NOT NULL,
    voice_workspace_id  TEXT,
    stored_session_id   TEXT,
    runtime_session_id  TEXT,
    created_at          INTEGER NOT NULL,
    updated_at          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS atlas_task_turns (
    id                  TEXT PRIMARY KEY,
    task_id             TEXT NOT NULL,
    kind                TEXT NOT NULL,
    instruction         TEXT NOT NULL,
    status              TEXT NOT NULL,
    idempotency_key     TEXT NOT NULL UNIQUE,
    created_at          INTEGER NOT NULL,
    started_at          INTEGER,
    completed_at        INTEGER
);

CREATE TABLE IF NOT EXISTS atlas_voice_workspaces (
    id                  TEXT PRIMARY KEY,
    focused_task_id     TEXT,
    started_at          INTEGER NOT NULL,
    updated_at          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS atlas_approval_requests (
    id                  TEXT PRIMARY KEY,
    task_id             TEXT NOT NULL,
    command             TEXT NOT NULL,
    description         TEXT NOT NULL,
    allow_permanent     INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL,
    choice              TEXT,
    created_at          INTEGER NOT NULL,
    resolved_at         INTEGER
);

CREATE INDEX IF NOT EXISTS idx_atlas_threads_runtime_session
    ON atlas_task_threads(runtime_session_id);
CREATE INDEX IF NOT EXISTS idx_atlas_threads_workspace
    ON atlas_task_threads(voice_workspace_id);
CREATE INDEX IF NOT EXISTS idx_atlas_turns_task_created
    ON atlas_task_turns(task_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_atlas_approvals_task_status
    ON atlas_approval_requests(task_id, status, created_at);
"""


class TaskThreadNotFoundError(LookupError):
    """Raised when a Task Thread id is not present in the adapter."""


class ApprovalNotFoundError(LookupError):
    """Raised when a stable approval id is unknown or already resolved."""


class TaskThreadStore:
    """Small synchronous store used by the local JSON-RPC gateway."""

    def __init__(
        self,
        *,
        db_path: Path | str | None = None,
        board: str | None = None,
    ) -> None:
        self._db_path = Path(db_path) if db_path is not None else None
        self._board = board
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    @contextlib.contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        with kb.connect_closing(db_path=self._db_path, board=self._board) as conn:
            if not self._schema_ready:
                with self._schema_lock:
                    if not self._schema_ready:
                        conn.executescript(_ADAPTER_SCHEMA)
                        self._schema_ready = True
            yield conn

    @staticmethod
    def _adapter_row(
        conn: sqlite3.Connection, task_id: str
    ) -> sqlite3.Row | None:
        return conn.execute(
            "SELECT * FROM atlas_task_threads WHERE task_id = ?",
            (task_id,),
        ).fetchone()

    @staticmethod
    def _touch(conn: sqlite3.Connection, task_id: str, now: int) -> None:
        conn.execute(
            "UPDATE atlas_task_threads SET updated_at = ? WHERE task_id = ?",
            (now, task_id),
        )

    @staticmethod
    def _event_payload(
        row: sqlite3.Row,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            raw = json.loads(row["payload"]) if row["payload"] else {}
        except (TypeError, json.JSONDecodeError):
            raw = {}
        if not isinstance(raw, dict):
            raw = {}
        meta = raw.get("_atlas")
        payload = raw.get("payload")
        return (
            meta if isinstance(meta, dict) else {},
            payload if isinstance(payload, dict) else {},
        )

    @classmethod
    def _event_from_row(cls, row: sqlite3.Row) -> dict[str, Any]:
        meta, payload = cls._event_payload(row)
        return {
            "event_id": f"task-event:{int(row['id'])}",
            "sequence": int(row["id"]),
            "schema_version": EVENT_SCHEMA_VERSION,
            "timestamp": iso_utc(int(row["created_at"])),
            "voice_workspace_id": meta.get("voice_workspace_id"),
            "thread_id": row["task_id"],
            "turn_id": meta.get("turn_id"),
            "worker_profile_id": meta.get("worker_profile_id"),
            "correlation_id": str(meta.get("correlation_id") or ""),
            "causation_id": meta.get("causation_id"),
            "name": row["kind"],
            "payload": payload,
        }

    def _append_event(
        self,
        conn: sqlite3.Connection,
        task_id: str,
        name: str,
        payload: Optional[dict[str, Any]] = None,
        *,
        turn_id: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        now: int | None = None,
    ) -> dict[str, Any]:
        adapter = self._adapter_row(conn, task_id)
        if adapter is None:
            raise TaskThreadNotFoundError(task_id)
        timestamp = int(time.time()) if now is None else int(now)
        raw_payload = {
            "_atlas": {
                "voice_workspace_id": adapter["voice_workspace_id"],
                "turn_id": turn_id,
                "worker_profile_id": adapter["worker_profile_id"],
                "correlation_id": correlation_id or uuid.uuid4().hex,
                "causation_id": causation_id,
            },
            "payload": payload or {},
        }
        cursor = conn.execute(
            """
            INSERT INTO task_events (task_id, run_id, kind, payload, created_at)
            VALUES (?, NULL, ?, ?, ?)
            """,
            (
                task_id,
                name,
                json.dumps(raw_payload, ensure_ascii=False),
                timestamp,
            ),
        )
        self._touch(conn, task_id, timestamp)
        event_row = conn.execute(
            "SELECT * FROM task_events WHERE id = ?",
            (int(cursor.lastrowid),),
        ).fetchone()
        assert event_row is not None
        return self._event_from_row(event_row)

    @staticmethod
    def _resolve_project(
        project_id: str,
    ) -> tuple[str, str]:
        from hermes_cli import projects_db

        with projects_db.connect_closing() as project_conn:
            project = projects_db.get_project(project_conn, project_id)
        if project is None:
            raise ValueError("project_id does not resolve to an Atlas project")
        if not project.primary_path:
            raise ValueError("project has no primary repository path")
        return project.id, str(project.primary_path)

    def create_thread(
        self,
        *,
        idempotency_key: str,
        title: str,
        goal: str,
        worker_profile_id: str,
        voice_workspace_id: str | None = None,
        project_id: str | None = None,
        workspace_mode: str = "none",
    ) -> dict[str, Any]:
        idempotency_key = require_non_empty(
            idempotency_key, "idempotency_key"
        )
        title = require_non_empty(title, "title")
        goal = require_non_empty(goal, "goal")
        worker_profile_id = require_non_empty(
            worker_profile_id, "worker_profile_id"
        )
        voice_workspace_id = (
            str(voice_workspace_id or "").strip() or None
        )
        workspace_mode = str(workspace_mode or "none").strip()
        if workspace_mode not in WORKSPACE_MODES:
            raise ValueError(
                f"workspace_mode must be one of {sorted(WORKSPACE_MODES)}"
            )

        task_idempotency_key = f"atlas-task-thread:{idempotency_key}"
        workspace_kind = "scratch"
        workspace_path = None
        canonical_project_id = None
        if workspace_mode != "none":
            requested_project = require_non_empty(project_id, "project_id")
            canonical_project_id, project_path = self._resolve_project(
                requested_project
            )
            if workspace_mode == "isolated_worktree":
                workspace_kind = "worktree"
            else:
                workspace_kind = "dir"
                workspace_path = project_path

        now = int(time.time())
        with self._connection() as conn:
            existing = conn.execute(
                """
                SELECT a.task_id
                  FROM atlas_task_threads a
                  JOIN tasks t ON t.id = a.task_id
                 WHERE t.idempotency_key = ?
                   AND t.status != 'archived'
                 ORDER BY t.created_at DESC
                 LIMIT 1
                """,
                (task_idempotency_key,),
            ).fetchone()
            if existing is not None:
                return self._thread_from_connection(
                    conn, existing["task_id"]
                )

            task_id = kb.create_task(
                conn,
                title=title,
                body=goal,
                assignee=worker_profile_id,
                created_by="atlas.task_threads",
                workspace_kind=workspace_kind,
                workspace_path=workspace_path,
                project_id=canonical_project_id,
                idempotency_key=task_idempotency_key,
                session_id=voice_workspace_id,
                execution_mode="interactive",
            )
            with kb.write_txn(conn):
                conn.execute(
                    """
                    INSERT INTO atlas_task_threads (
                        task_id, worker_profile_id, voice_workspace_id,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        worker_profile_id,
                        voice_workspace_id,
                        now,
                        now,
                    ),
                )
                self._append_event(
                    conn,
                    task_id,
                    "thread.created",
                    {"status": "queued"},
                    correlation_id=idempotency_key,
                    now=now,
                )
                self._append_event(
                    conn,
                    task_id,
                    "thread.status_changed",
                    {"from": None, "to": "queued"},
                    correlation_id=idempotency_key,
                    now=now,
                )
            return self._thread_from_connection(conn, task_id)

    def materialize_workspace(self, thread_id: str) -> dict[str, Any]:
        """Create the task workspace and fail closed on worktree collisions."""
        with self._connection() as conn:
            task = kb.get_task(conn, thread_id)
            if task is None or self._adapter_row(conn, thread_id) is None:
                raise TaskThreadNotFoundError(thread_id)
            resolved_path = kb.resolve_workspace(task, board=self._board)
            resolved_text = str(resolved_path.resolve(strict=False))
            if task.workspace_kind == "worktree":
                expected_branch = (
                    str(task.branch_name or "").strip() or f"wt/{task.id}"
                )
                actual_branch = kb._git_current_branch(resolved_path)
                if actual_branch != expected_branch:
                    raise RuntimeError(
                        "refusing to reuse task worktree "
                        f"{resolved_text}: expected branch "
                        f"{expected_branch!r}, found {actual_branch!r}"
                    )
                if task.branch_name != expected_branch:
                    kb.set_branch_name(conn, thread_id, expected_branch)
            if task.workspace_path != resolved_text:
                kb.set_workspace_path(conn, thread_id, resolved_text)
            return self._thread_from_connection(conn, thread_id)

    def bind_runtime(
        self,
        thread_id: str,
        *,
        stored_session_id: str,
        runtime_session_id: str,
    ) -> dict[str, Any]:
        stored_session_id = require_non_empty(
            stored_session_id, "stored_session_id"
        )
        runtime_session_id = require_non_empty(
            runtime_session_id, "runtime_session_id"
        )
        now = int(time.time())
        with self._connection() as conn, kb.write_txn(conn):
            cur = conn.execute(
                """
                UPDATE atlas_task_threads
                   SET stored_session_id = ?, runtime_session_id = ?,
                       updated_at = ?
                 WHERE task_id = ?
                """,
                (
                    stored_session_id,
                    runtime_session_id,
                    now,
                    thread_id,
                ),
            )
            if cur.rowcount != 1:
                raise TaskThreadNotFoundError(thread_id)
            return self._thread_from_connection(conn, thread_id)

    def reconcile_orphaned_runtimes(
        self,
        live_runtime_session_ids: Iterable[str],
    ) -> list[dict[str, Any]]:
        """Fail closed for Task Thread runtimes lost with a gateway process.

        Runtime sessions are process-local, while Task Threads, turns, and
        approvals are durable. After a backend restart, a missing runtime
        cannot honestly remain ``running`` or ``waiting_approval``. Clear each
        stale binding, deny approvals whose waiter is gone, and interrupt
        unfinished work. A later explicit instruction can resume the stored
        Hermes session and reopen the same Task Thread.
        """
        live_ids = {
            str(runtime_id).strip()
            for runtime_id in live_runtime_session_ids
            if str(runtime_id).strip()
        }
        now = int(time.time())
        reconciled: list[dict[str, Any]] = []
        with self._connection() as conn, kb.write_txn(conn):
            rows = conn.execute(
                """
                SELECT a.task_id, a.runtime_session_id, t.status
                  FROM atlas_task_threads a
                  JOIN tasks t ON t.id = a.task_id
                 WHERE a.runtime_session_id IS NOT NULL
                   AND a.runtime_session_id != ''
                 ORDER BY a.task_id
                """
            ).fetchall()
            for row in rows:
                runtime_session_id = str(row["runtime_session_id"])
                if runtime_session_id in live_ids:
                    continue

                thread_id = str(row["task_id"])
                previous = DB_TO_THREAD_STATUS.get(
                    str(row["status"]), "failed"
                )
                conn.execute(
                    """
                    UPDATE atlas_task_threads
                       SET runtime_session_id = NULL, updated_at = ?
                     WHERE task_id = ? AND runtime_session_id = ?
                    """,
                    (now, thread_id, runtime_session_id),
                )
                if previous in TERMINAL_THREAD_STATUSES:
                    continue

                correlation_id = (
                    f"gateway-restart:{runtime_session_id}"
                )
                pending_approvals = conn.execute(
                    """
                    SELECT id FROM atlas_approval_requests
                     WHERE task_id = ? AND status = 'pending'
                     ORDER BY created_at ASC, id ASC
                    """,
                    (thread_id,),
                ).fetchall()
                for approval in pending_approvals:
                    approval_id = str(approval["id"])
                    conn.execute(
                        """
                        UPDATE atlas_approval_requests
                           SET status = 'resolved', choice = 'deny',
                               resolved_at = ?
                         WHERE id = ? AND status = 'pending'
                        """,
                        (now, approval_id),
                    )
                    self._append_event(
                        conn,
                        thread_id,
                        "approval.resolved",
                        {
                            "approval_id": approval_id,
                            "choice": "deny",
                            "reason": "gateway_restart",
                        },
                        correlation_id=approval_id,
                        now=now,
                    )

                open_turns = conn.execute(
                    """
                    SELECT id FROM atlas_task_turns
                     WHERE task_id = ? AND status IN ('queued', 'running')
                     ORDER BY created_at ASC, id ASC
                    """,
                    (thread_id,),
                ).fetchall()
                for turn in open_turns:
                    turn_id = str(turn["id"])
                    conn.execute(
                        """
                        UPDATE atlas_task_turns
                           SET status = 'interrupted', completed_at = ?
                         WHERE id = ?
                        """,
                        (now, turn_id),
                    )
                    self._append_event(
                        conn,
                        thread_id,
                        "turn.failed",
                        {
                            "status": "interrupted",
                            "reason": "gateway_restart",
                        },
                        turn_id=turn_id,
                        correlation_id=correlation_id,
                        now=now,
                    )

                conn.execute(
                    """
                    UPDATE tasks
                       SET status = 'cancelled', completed_at = ?,
                           last_failure_error = ?
                     WHERE id = ?
                    """,
                    (now, _ORPHANED_RUNTIME_REASON, thread_id),
                )
                status_event = self._append_event(
                    conn,
                    thread_id,
                    "thread.status_changed",
                    {
                        "from": previous,
                        "to": "interrupted",
                        "summary": None,
                        "blocker": _ORPHANED_RUNTIME_REASON,
                    },
                    correlation_id=correlation_id,
                    now=now,
                )
                self._append_event(
                    conn,
                    thread_id,
                    "thread.interrupted",
                    {
                        "summary": None,
                        "blocker": _ORPHANED_RUNTIME_REASON,
                        "reason": "gateway_restart",
                    },
                    correlation_id=correlation_id,
                    causation_id=status_event["event_id"],
                    now=now,
                )
                reconciled.append(
                    self._thread_from_connection(conn, thread_id)
                )
        return reconciled

    def runtime_binding(self, thread_id: str) -> dict[str, str | None]:
        with self._connection() as conn:
            row = self._adapter_row(conn, thread_id)
            if row is None:
                raise TaskThreadNotFoundError(thread_id)
            return {
                "runtime_session_id": row["runtime_session_id"],
                "stored_session_id": row["stored_session_id"],
                "worker_profile_id": row["worker_profile_id"],
            }

    def thread_id_for_runtime(
        self, runtime_session_id: str
    ) -> str | None:
        if not runtime_session_id:
            return None
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT task_id FROM atlas_task_threads
                 WHERE runtime_session_id = ?
                """,
                (runtime_session_id,),
            ).fetchone()
            return row["task_id"] if row else None

    def _thread_from_connection(
        self, conn: sqlite3.Connection, thread_id: str
    ) -> dict[str, Any]:
        task = kb.get_task(conn, thread_id)
        adapter = self._adapter_row(conn, thread_id)
        if task is None or adapter is None:
            raise TaskThreadNotFoundError(thread_id)
        approval = conn.execute(
            """
            SELECT id FROM atlas_approval_requests
             WHERE task_id = ? AND status = 'pending'
             ORDER BY created_at ASC, id ASC
             LIMIT 1
            """,
            (thread_id,),
        ).fetchone()
        return {
            "id": task.id,
            "title": task.title,
            "goal": task.body or "",
            "status": DB_TO_THREAD_STATUS.get(task.status, "failed"),
            "worker_profile_id": adapter["worker_profile_id"],
            "project_id": task.project_id,
            "workspace_kind": task.workspace_kind,
            "workspace_path": task.workspace_path,
            "branch": task.branch_name,
            "stored_session_id": adapter["stored_session_id"],
            "runtime_session_id": adapter["runtime_session_id"],
            "created_at": iso_utc(task.created_at),
            "updated_at": iso_utc(adapter["updated_at"]),
            "last_summary": task.result,
            "blocker": task.last_failure_error,
            "pending_approval_id": approval["id"] if approval else None,
        }

    def get_thread(self, thread_id: str) -> dict[str, Any]:
        thread_id = require_non_empty(thread_id, "thread_id")
        with self._connection() as conn:
            return self._thread_from_connection(conn, thread_id)

    def list_threads(
        self, *, include_archived: bool = False
    ) -> tuple[list[dict[str, Any]], int]:
        with self._connection() as conn:
            query = (
                "SELECT a.task_id FROM atlas_task_threads a "
                "JOIN tasks t ON t.id = a.task_id "
            )
            if not include_archived:
                query += "WHERE t.status != 'archived' "
            query += (
                "ORDER BY ("
                "SELECT MIN(e.id) FROM task_events e "
                "WHERE e.task_id = a.task_id "
                "AND e.kind = 'thread.created'"
                ") ASC, t.id ASC"
            )
            threads = [
                self._thread_from_connection(conn, row["task_id"])
                for row in conn.execute(query).fetchall()
            ]
            return threads, self._latest_cursor(conn)

    @staticmethod
    def _latest_cursor(conn: sqlite3.Connection) -> int:
        prefixes = " OR ".join(
            "e.kind LIKE ?" for _ in _EVENT_KIND_PREFIXES
        )
        row = conn.execute(
            f"""
            SELECT MAX(e.id) AS cursor
              FROM task_events e
              JOIN atlas_task_threads a ON a.task_id = e.task_id
             WHERE {prefixes}
            """,
            tuple(f"{prefix}%" for prefix in _EVENT_KIND_PREFIXES),
        ).fetchone()
        return int(row["cursor"] or 0)

    def set_status(
        self,
        thread_id: str,
        status: str,
        *,
        summary: str | None = None,
        blocker: str | None = None,
        turn_id: str | None = None,
        correlation_id: str | None = None,
        allow_reopen: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if status not in THREAD_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(THREAD_STATUSES)}"
            )
        now = int(time.time())
        with self._connection() as conn, kb.write_txn(conn):
            task = kb.get_task(conn, thread_id)
            if task is None or self._adapter_row(conn, thread_id) is None:
                raise TaskThreadNotFoundError(thread_id)
            previous = DB_TO_THREAD_STATUS.get(task.status, "failed")
            if previous == "archived" and status != previous:
                return self._thread_from_connection(conn, thread_id), None
            if (
                previous in TERMINAL_THREAD_STATUSES
                and status != previous
                and not allow_reopen
            ):
                return self._thread_from_connection(conn, thread_id), None
            if previous == status and summary is None and blocker is None:
                return self._thread_from_connection(conn, thread_id), None

            db_status = THREAD_TO_DB_STATUS[status]
            completed_at = now if status in TERMINAL_THREAD_STATUSES else None
            conn.execute(
                """
                UPDATE tasks
                   SET status = ?,
                       completed_at = ?,
                       result = COALESCE(?, result),
                       last_failure_error = ?
                 WHERE id = ?
                """,
                (
                    db_status,
                    completed_at,
                    summary,
                    blocker,
                    thread_id,
                ),
            )
            event = self._append_event(
                conn,
                thread_id,
                "thread.status_changed",
                {
                    "from": previous,
                    "to": status,
                    "summary": summary,
                    "blocker": blocker,
                },
                turn_id=turn_id,
                correlation_id=correlation_id,
                now=now,
            )
            terminal_name = {
                "completed": "thread.completed",
                "failed": "thread.failed",
                "interrupted": "thread.interrupted",
            }.get(status)
            if terminal_name:
                self._append_event(
                    conn,
                    thread_id,
                    terminal_name,
                    {"summary": summary, "blocker": blocker},
                    turn_id=turn_id,
                    correlation_id=correlation_id,
                    causation_id=event["event_id"],
                    now=now,
                )
            return self._thread_from_connection(conn, thread_id), event

    def create_turn(
        self,
        thread_id: str,
        *,
        kind: str,
        instruction: str,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]:
        if kind not in TURN_KINDS:
            raise ValueError(f"kind must be one of {sorted(TURN_KINDS)}")
        instruction = require_non_empty(instruction, "instruction")
        idempotency_key = require_non_empty(
            idempotency_key, "idempotency_key"
        )
        now = int(time.time())
        with self._connection() as conn, kb.write_txn(conn):
            if self._adapter_row(conn, thread_id) is None:
                raise TaskThreadNotFoundError(thread_id)
            existing = conn.execute(
                "SELECT * FROM atlas_task_turns WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["task_id"] != thread_id:
                    raise ValueError(
                        "idempotency_key already belongs to another thread"
                    )
                return self._turn_from_row(existing), False
            turn_id = f"turn_{uuid.uuid4().hex}"
            conn.execute(
                """
                INSERT INTO atlas_task_turns (
                    id, task_id, kind, instruction, status,
                    idempotency_key, created_at
                ) VALUES (?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    turn_id,
                    thread_id,
                    kind,
                    instruction,
                    idempotency_key,
                    now,
                ),
            )
            self._append_event(
                conn,
                thread_id,
                "turn.queued",
                {"kind": kind},
                turn_id=turn_id,
                correlation_id=idempotency_key,
                now=now,
            )
            row = conn.execute(
                "SELECT * FROM atlas_task_turns WHERE id = ?",
                (turn_id,),
            ).fetchone()
            assert row is not None
            return self._turn_from_row(row), True

    @staticmethod
    def _turn_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "thread_id": row["task_id"],
            "kind": row["kind"],
            "instruction": row["instruction"],
            "status": row["status"],
            "created_at": iso_utc(row["created_at"]),
            "started_at": iso_utc(row["started_at"]),
            "completed_at": iso_utc(row["completed_at"]),
        }

    def _latest_open_turn(
        self, conn: sqlite3.Connection, thread_id: str
    ) -> sqlite3.Row | None:
        return conn.execute(
            """
            SELECT * FROM atlas_task_turns
             WHERE task_id = ? AND status IN ('queued', 'running')
             ORDER BY created_at DESC, id DESC
             LIMIT 1
            """,
            (thread_id,),
        ).fetchone()

    def set_turn_status(
        self,
        thread_id: str,
        status: str,
        *,
        turn_id: str | None = None,
        correlation_id: str | None = None,
    ) -> dict[str, Any] | None:
        if status not in TURN_STATUSES:
            raise ValueError(
                f"status must be one of {sorted(TURN_STATUSES)}"
            )
        now = int(time.time())
        with self._connection() as conn, kb.write_txn(conn):
            row = (
                conn.execute(
                    "SELECT * FROM atlas_task_turns WHERE id = ? AND task_id = ?",
                    (turn_id, thread_id),
                ).fetchone()
                if turn_id
                else self._latest_open_turn(conn, thread_id)
            )
            if row is None:
                return None
            if row["status"] in {"completed", "failed", "interrupted"}:
                return self._turn_from_row(row)
            started_at = (
                now
                if status == "running" and row["started_at"] is None
                else row["started_at"]
            )
            completed_at = (
                now
                if status in {"completed", "failed", "interrupted"}
                else row["completed_at"]
            )
            conn.execute(
                """
                UPDATE atlas_task_turns
                   SET status = ?, started_at = ?, completed_at = ?
                 WHERE id = ?
                """,
                (status, started_at, completed_at, row["id"]),
            )
            event_name = {
                "running": "turn.started",
                "completed": "turn.completed",
                "failed": "turn.failed",
                "interrupted": "turn.failed",
            }.get(status)
            if event_name:
                self._append_event(
                    conn,
                    thread_id,
                    event_name,
                    {"status": status},
                    turn_id=row["id"],
                    correlation_id=correlation_id,
                    now=now,
                )
            updated = conn.execute(
                "SELECT * FROM atlas_task_turns WHERE id = ?",
                (row["id"],),
            ).fetchone()
            assert updated is not None
            return self._turn_from_row(updated)

    def focus_thread(
        self, voice_workspace_id: str, thread_id: str
    ) -> tuple[str, dict[str, Any]]:
        voice_workspace_id = require_non_empty(
            voice_workspace_id, "voice_workspace_id"
        )
        now = int(time.time())
        with self._connection() as conn, kb.write_txn(conn):
            if self._adapter_row(conn, thread_id) is None:
                raise TaskThreadNotFoundError(thread_id)
            conn.execute(
                """
                INSERT INTO atlas_voice_workspaces (
                    id, focused_task_id, started_at, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    focused_task_id = excluded.focused_task_id,
                    updated_at = excluded.updated_at
                """,
                (voice_workspace_id, thread_id, now, now),
            )
            event = self._append_event(
                conn,
                thread_id,
                "thread.focused",
                {"focused_thread_id": thread_id},
                correlation_id=uuid.uuid4().hex,
                now=now,
            )
            return thread_id, event

    def list_events(
        self,
        *,
        after_sequence: int = 0,
        thread_id: str | None = None,
        limit: int = 200,
    ) -> tuple[list[dict[str, Any]], int]:
        try:
            after_sequence = max(0, int(after_sequence))
        except (TypeError, ValueError):
            raise ValueError("after_sequence must be an integer") from None
        try:
            limit = min(500, max(1, int(limit)))
        except (TypeError, ValueError):
            raise ValueError("limit must be an integer") from None
        prefixes = " OR ".join(
            "e.kind LIKE ?" for _ in _EVENT_KIND_PREFIXES
        )
        params: list[Any] = [
            after_sequence,
            *(f"{prefix}%" for prefix in _EVENT_KIND_PREFIXES),
        ]
        thread_filter = ""
        if thread_id:
            thread_filter = " AND e.task_id = ?"
            params.append(thread_id)
        params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT e.*
                  FROM task_events e
                  JOIN atlas_task_threads a ON a.task_id = e.task_id
                 WHERE e.id > ?
                   AND ({prefixes})
                   {thread_filter}
                 ORDER BY e.id ASC
                 LIMIT ?
                """,
                tuple(params),
            ).fetchall()
            events = [self._event_from_row(row) for row in rows]
            cursor = (
                events[-1]["sequence"] if events else after_sequence
            )
            return events, cursor

    def record_approval(
        self,
        runtime_session_id: str,
        *,
        approval_id: str,
        command: str,
        description: str,
        allow_permanent: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        thread_id = self.thread_id_for_runtime(runtime_session_id)
        if thread_id is None:
            return None
        approval_id = require_non_empty(approval_id, "approval_id")
        now = int(time.time())
        with self._connection() as conn, kb.write_txn(conn):
            conn.execute(
                """
                INSERT INTO atlas_approval_requests (
                    id, task_id, command, description, allow_permanent,
                    status, created_at
                ) VALUES (?, ?, ?, ?, ?, 'pending', ?)
                ON CONFLICT(id) DO NOTHING
                """,
                (
                    approval_id,
                    thread_id,
                    str(command or ""),
                    str(description or ""),
                    1 if allow_permanent else 0,
                    now,
                ),
            )
            task = kb.get_task(conn, thread_id)
            if task is None:
                raise TaskThreadNotFoundError(thread_id)
            previous = DB_TO_THREAD_STATUS.get(task.status, "failed")
            if previous not in TERMINAL_THREAD_STATUSES:
                conn.execute(
                    "UPDATE tasks SET status = 'waiting_approval' WHERE id = ?",
                    (thread_id,),
                )
            event = self._append_event(
                conn,
                thread_id,
                "approval.requested",
                {
                    "approval_id": approval_id,
                    "command": str(command or ""),
                    "description": str(description or ""),
                    "allow_permanent": bool(allow_permanent),
                },
                correlation_id=approval_id,
                now=now,
            )
            row = conn.execute(
                "SELECT * FROM atlas_approval_requests WHERE id = ?",
                (approval_id,),
            ).fetchone()
            assert row is not None
            return self._approval_from_row(row), event

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "approval_id": row["id"],
            "thread_id": row["task_id"],
            "command": row["command"],
            "description": row["description"],
            "allow_permanent": bool(row["allow_permanent"]),
            "status": row["status"],
            "choice": row["choice"],
            "created_at": iso_utc(row["created_at"]),
            "resolved_at": iso_utc(row["resolved_at"]),
        }

    def pending_approval(
        self, approval_id: str
    ) -> tuple[dict[str, Any], dict[str, str | None]]:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM atlas_approval_requests
                 WHERE id = ? AND status = 'pending'
                """,
                (approval_id,),
            ).fetchone()
            if row is None:
                raise ApprovalNotFoundError(approval_id)
            adapter = self._adapter_row(conn, row["task_id"])
            if adapter is None:
                raise TaskThreadNotFoundError(row["task_id"])
            return self._approval_from_row(row), {
                "runtime_session_id": adapter["runtime_session_id"],
                "worker_profile_id": adapter["worker_profile_id"],
            }

    def resolve_approval(
        self, approval_id: str, choice: str
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if choice not in APPROVAL_CHOICES:
            raise ValueError(
                f"choice must be one of {sorted(APPROVAL_CHOICES)}"
            )
        now = int(time.time())
        with self._connection() as conn, kb.write_txn(conn):
            row = conn.execute(
                """
                SELECT * FROM atlas_approval_requests
                 WHERE id = ? AND status = 'pending'
                """,
                (approval_id,),
            ).fetchone()
            if row is None:
                raise ApprovalNotFoundError(approval_id)
            conn.execute(
                """
                UPDATE atlas_approval_requests
                   SET status = 'resolved', choice = ?, resolved_at = ?
                 WHERE id = ? AND status = 'pending'
                """,
                (choice, now, approval_id),
            )
            task_id = row["task_id"]
            task = kb.get_task(conn, task_id)
            pending = conn.execute(
                """
                SELECT 1 FROM atlas_approval_requests
                 WHERE task_id = ? AND status = 'pending'
                 LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            if (
                task is not None
                and task.status == "waiting_approval"
                and pending is None
            ):
                conn.execute(
                    "UPDATE tasks SET status = 'running' WHERE id = ?",
                    (task_id,),
                )
            event = self._append_event(
                conn,
                task_id,
                "approval.resolved",
                {"approval_id": approval_id, "choice": choice},
                correlation_id=approval_id,
                now=now,
            )
            updated = conn.execute(
                "SELECT * FROM atlas_approval_requests WHERE id = ?",
                (approval_id,),
            ).fetchone()
            assert updated is not None
            return self._approval_from_row(updated), event

    def list_approvals(
        self, *, thread_id: str | None = None
    ) -> list[dict[str, Any]]:
        with self._connection() as conn:
            query = "SELECT * FROM atlas_approval_requests"
            params: tuple[Any, ...] = ()
            if thread_id:
                query += " WHERE task_id = ?"
                params = (thread_id,)
            query += " ORDER BY created_at ASC, id ASC"
            return [
                self._approval_from_row(row)
                for row in conn.execute(query, params).fetchall()
            ]

    def record_gateway_event(
        self,
        runtime_session_id: str,
        event_name: str,
        payload: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        """Project a worker gateway event into the durable Task Thread log."""
        thread_id = self.thread_id_for_runtime(runtime_session_id)
        if thread_id is None:
            return []
        with self._connection() as conn:
            before_sequence = self._latest_cursor(conn)
        payload = payload if isinstance(payload, dict) else {}
        if event_name == "message.start":
            turn = self.set_turn_status(thread_id, "running")
            self.set_status(
                thread_id,
                "running",
                turn_id=turn["id"] if turn else None,
            )
        elif event_name == "message.delta":
            with self._connection() as conn, kb.write_txn(conn):
                turn = self._latest_open_turn(conn, thread_id)
                self._append_event(
                    conn,
                    thread_id,
                    "worker.message_delta",
                    {"text": str(payload.get("text") or "")},
                    turn_id=turn["id"] if turn else None,
                )
        elif event_name == "message.complete":
            with self._connection() as conn, kb.write_txn(conn):
                turn = self._latest_open_turn(conn, thread_id)
                turn_id = turn["id"] if turn else None
                self._append_event(
                    conn,
                    thread_id,
                    "worker.message_completed",
                    {
                        "text": str(
                            payload.get("text")
                            or payload.get("rendered")
                            or ""
                        ),
                        "status": payload.get("status"),
                    },
                    turn_id=turn_id,
                )
            completion_status = str(payload.get("status") or "").lower()
            summary = str(
                payload.get("text") or payload.get("rendered") or ""
            ).strip()
            if completion_status == "error":
                self.set_turn_status(
                    thread_id, "failed", turn_id=turn_id
                )
                self.set_status(
                    thread_id,
                    "failed",
                    summary=summary or None,
                    blocker=summary or "worker turn failed",
                    turn_id=turn_id,
                )
            elif completion_status in {"cancelled", "interrupted"}:
                self.set_turn_status(
                    thread_id, "interrupted", turn_id=turn_id
                )
                self.set_status(
                    thread_id,
                    "interrupted",
                    summary=summary or None,
                    turn_id=turn_id,
                )
            else:
                self.set_turn_status(
                    thread_id, "completed", turn_id=turn_id
                )
                self.set_status(
                    thread_id,
                    "completed",
                    summary=summary or None,
                    turn_id=turn_id,
                )
        elif event_name == "error":
            message = str(
                payload.get("message") or payload.get("text") or "worker error"
            )
            turn = self.set_turn_status(thread_id, "failed")
            self.set_status(
                thread_id,
                "failed",
                blocker=message,
                turn_id=turn["id"] if turn else None,
            )
        events, _ = self.list_events(
            after_sequence=before_sequence,
            thread_id=thread_id,
            limit=500,
        )
        return events
