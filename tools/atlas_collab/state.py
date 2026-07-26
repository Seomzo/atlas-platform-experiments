"""SQLite recovery ledger for Atlas development collaboration."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from .models import CollaborationEvent, TaskContract
from .redaction import assert_non_secret
from .state_machine import assert_transition

SCHEMA_VERSION = 1
SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    workstream_id TEXT NOT NULL,
    state TEXT NOT NULL,
    pre_pause_state TEXT,
    contract_json TEXT NOT NULL,
    contract_hash TEXT NOT NULL,
    context_hash TEXT,
    base_sha TEXT NOT NULL,
    branch TEXT,
    worktree TEXT,
    buzz_channel_id TEXT,
    buzz_canvas_id TEXT,
    github_issue INTEGER,
    github_pr INTEGER,
    failure_count INTEGER NOT NULL DEFAULT 0,
    turn_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    display_name TEXT NOT NULL,
    public_key TEXT NOT NULL UNIQUE,
    runtime TEXT NOT NULL,
    profile TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_health TEXT
);
CREATE TABLE IF NOT EXISTS task_agents (
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    agent_id TEXT NOT NULL REFERENCES agents(agent_id),
    contract_hash TEXT,
    context_hash TEXT,
    acknowledged_at TEXT,
    PRIMARY KEY (task_id, agent_id)
);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    idempotency_key TEXT NOT NULL UNIQUE,
    causation_id TEXT,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    buzz_event_id TEXT,
    github_ref TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_task_created
    ON events(task_id, created_at);
CREATE TABLE IF NOT EXISTS external_writes (
    idempotency_key TEXT PRIMARY KEY,
    system TEXT NOT NULL,
    target TEXT NOT NULL,
    external_id TEXT,
    status TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    agent_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('path', 'interface')),
    resource TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    released_at TEXT,
    UNIQUE(task_id, kind, resource, released_at)
);
CREATE TABLE IF NOT EXISTS turns (
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    agent_id TEXT NOT NULL,
    session_id TEXT,
    status TEXT NOT NULL,
    causation_id TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    PRIMARY KEY(task_id, agent_id, started_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_active_turn_per_agent
    ON turns(task_id, agent_id) WHERE finished_at IS NULL;
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    criterion_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    pointer TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS services (
    name TEXT PRIMARY KEY,
    manager TEXT NOT NULL,
    definition_path TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class StateStore:
    """Durable, idempotent state that never contains credentials."""

    def __init__(self, path: Path):
        self.path = path.expanduser()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path))
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.connection.executescript(SCHEMA)
        self.connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def create_task(
        self,
        contract: TaskContract,
        *,
        state: str = "intake",
        issue: int | None = None,
    ) -> bool:
        contract_dict = contract.to_dict()
        assert_non_secret(contract_dict)
        timestamp = _now()
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO tasks(
                task_id, workstream_id, state, contract_json, contract_hash,
                base_sha, github_issue, created_at, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                contract.task_id,
                contract.workstream_id,
                state,
                json.dumps(contract_dict, sort_keys=True),
                contract.digest,
                contract.base_sha,
                issue,
                timestamp,
                timestamp,
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def create_needs_clarification(
        self,
        *,
        task_id: str,
        workstream_id: str,
        base_sha: str,
        intake: dict[str, Any],
        reason: str,
        issue: int | None = None,
    ) -> bool:
        payload = {
            "schema_version": "atlas.collab.intake.v1",
            "task_id": task_id,
            "workstream_id": workstream_id,
            "base_sha": base_sha,
            "intake": intake,
            "missing_decision": reason,
        }
        assert_non_secret(payload)
        from .models import content_hash

        timestamp = _now()
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO tasks(
                task_id, workstream_id, state, contract_json, contract_hash,
                base_sha, github_issue, created_at, updated_at
            ) VALUES(?, ?, 'needs-clarification', ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                workstream_id,
                json.dumps(payload, sort_keys=True),
                content_hash(payload),
                base_sha,
                issue,
                timestamp,
                timestamp,
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def task(self, task_id: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["contract"] = json.loads(result.pop("contract_json"))
        return result

    def tasks(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                "SELECT task_id, workstream_id, state, updated_at FROM tasks ORDER BY created_at"
            )
        ]

    def update_task_refs(self, task_id: str, **refs: Any) -> None:
        allowed = {
            "context_hash",
            "branch",
            "worktree",
            "buzz_channel_id",
            "buzz_canvas_id",
            "github_issue",
            "github_pr",
        }
        unknown = set(refs) - allowed
        if unknown:
            raise ValueError(f"unsupported task references: {sorted(unknown)}")
        assert_non_secret(refs)
        assignments = ", ".join(f"{key} = ?" for key in refs)
        values = list(refs.values()) + [_now(), task_id]
        cursor = self.connection.execute(
            f"UPDATE tasks SET {assignments}, updated_at = ? WHERE task_id = ?",
            values,
        )
        self.connection.commit()
        if cursor.rowcount != 1:
            raise KeyError(task_id)

    def transition(self, task_id: str, target: str, *, actor_role: str) -> bool:
        task = self.task(task_id)
        if task is None:
            raise KeyError(task_id)
        current = task["state"]
        if current == target:
            return False
        if target == "paused":
            assert_transition(current, target, actor_role=actor_role)
            pre_pause = current
        elif current == "paused" and target == "resume":
            target = task["pre_pause_state"]
            if not target:
                raise ValueError("paused task has no resumable state")
            pre_pause = None
        else:
            assert_transition(current, target, actor_role=actor_role)
            pre_pause = None
        self.connection.execute(
            """
            UPDATE tasks
            SET state = ?, pre_pause_state = ?, updated_at = ?
            WHERE task_id = ?
            """,
            (target, pre_pause, _now(), task_id),
        )
        self.connection.commit()
        return True

    def record_event(self, event: CollaborationEvent) -> bool:
        envelope = event.to_dict()
        assert_non_secret(envelope)
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO events(
                event_id, task_id, idempotency_key, causation_id, event_type,
                actor_id, envelope_json, created_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.event_id,
                event.task_id,
                event.idempotency_key,
                event.causation_id or None,
                event.event_type,
                event.actor_id,
                json.dumps(envelope, sort_keys=True),
                event.timestamp,
            ),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def events(self, task_id: str) -> list[dict[str, Any]]:
        return [
            json.loads(row["envelope_json"])
            for row in self.connection.execute(
                "SELECT envelope_json FROM events WHERE task_id = ? ORDER BY created_at",
                (task_id,),
            )
        ]

    def reserve_external_write(
        self,
        key: str,
        *,
        system: str,
        target: str,
        payload_hash: str,
    ) -> bool:
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO external_writes(
                idempotency_key, system, target, status, payload_hash, updated_at
            ) VALUES(?, ?, ?, 'pending', ?, ?)
            """,
            (key, system, target, payload_hash, _now()),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def finish_external_write(self, key: str, external_id: str) -> None:
        self.connection.execute(
            """
            UPDATE external_writes
            SET external_id = ?, status = 'complete', updated_at = ?
            WHERE idempotency_key = ?
            """,
            (external_id, _now(), key),
        )
        self.connection.commit()

    def external_write(self, key: str) -> dict[str, Any] | None:
        row = self.connection.execute(
            "SELECT * FROM external_writes WHERE idempotency_key = ?", (key,)
        ).fetchone()
        return dict(row) if row else None

    def register_agent(
        self,
        *,
        agent_id: str,
        role: str,
        display_name: str,
        public_key: str,
        runtime: str,
        profile: str,
    ) -> bool:
        payload = locals().copy()
        payload.pop("self")
        assert_non_secret(payload)
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO agents(
                agent_id, role, display_name, public_key, runtime, profile
            ) VALUES(?, ?, ?, ?, ?, ?)
            """,
            (agent_id, role, display_name, public_key, runtime, profile),
        )
        self.connection.commit()
        return cursor.rowcount == 1

    def agents(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT agent_id, role, display_name, public_key, runtime,
                       profile, enabled, last_health
                FROM agents ORDER BY role
                """
            )
        ]

    def acknowledge(
        self, task_id: str, agent_id: str, contract_hash: str, context_hash: str
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO task_agents(
                task_id, agent_id, contract_hash, context_hash, acknowledged_at
            ) VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(task_id, agent_id) DO UPDATE SET
                contract_hash = excluded.contract_hash,
                context_hash = excluded.context_hash,
                acknowledged_at = excluded.acknowledged_at
            """,
            (task_id, agent_id, contract_hash, context_hash, _now()),
        )
        self.connection.commit()

    def all_acknowledged(self, task_id: str) -> bool:
        task = self.task(task_id)
        if task is None or not task["context_hash"]:
            return False
        rows = self.connection.execute(
            """
            SELECT contract_hash, context_hash, acknowledged_at
            FROM task_agents WHERE task_id = ?
            """,
            (task_id,),
        ).fetchall()
        return bool(rows) and all(
            row["acknowledged_at"]
            and row["contract_hash"] == task["contract_hash"]
            and row["context_hash"] == task["context_hash"]
            for row in rows
        )
