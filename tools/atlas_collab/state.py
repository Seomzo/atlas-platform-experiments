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

SCHEMA_VERSION = 4
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
    clarification_count INTEGER NOT NULL DEFAULT 0,
    cost_microusd INTEGER NOT NULL DEFAULT 0,
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
    branch TEXT,
    worktree TEXT,
    session_id TEXT,
    git_name TEXT,
    git_email TEXT,
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
    released_at TEXT
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
CREATE TABLE IF NOT EXISTS task_processes (
    task_id TEXT NOT NULL REFERENCES tasks(task_id),
    pid INTEGER NOT NULL,
    create_time REAL NOT NULL,
    command_fingerprint TEXT NOT NULL,
    registered_at TEXT NOT NULL,
    stopped_at TEXT,
    PRIMARY KEY(task_id, pid, create_time)
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
        self._ensure_column(
            "tasks", "clarification_count", "INTEGER NOT NULL DEFAULT 0"
        )
        self._ensure_column("tasks", "cost_microusd", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("task_agents", "branch", "TEXT")
        self._ensure_column("task_agents", "worktree", "TEXT")
        self._ensure_column("task_agents", "session_id", "TEXT")
        self._ensure_column("task_agents", "git_name", "TEXT")
        self._ensure_column("task_agents", "git_email", "TEXT")
        self.connection.commit()
        self._migrate_claim_history_constraint()
        self.connection.execute(
            "INSERT OR REPLACE INTO metadata(key, value) VALUES('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.connection.commit()
        self._secure_permissions()

    def _ensure_column(self, table: str, column: str, declaration: str) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute(f"PRAGMA table_info({table})")
        }
        if column not in columns:
            self.connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
            )

    def _migrate_claim_history_constraint(self) -> None:
        row = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'claims'"
        ).fetchone()
        sql = str(row["sql"] if row else "")
        normalized = "".join(sql.lower().split())
        if "unique(task_id,kind,resource,released_at)" not in normalized:
            return
        with self.transaction() as connection:
            connection.execute("ALTER TABLE claims RENAME TO claims_legacy_v2")
            connection.execute(
                """
                CREATE TABLE claims (
                    claim_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    agent_id TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('path', 'interface')),
                    resource TEXT NOT NULL,
                    acquired_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    released_at TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT INTO claims(
                    claim_id, task_id, agent_id, kind, resource,
                    acquired_at, expires_at, released_at
                )
                SELECT claim_id, task_id, agent_id, kind, resource,
                       acquired_at, expires_at, released_at
                FROM claims_legacy_v2
                """
            )
            connection.execute("DROP TABLE claims_legacy_v2")

    def close(self) -> None:
        self.connection.close()
        self._secure_permissions()

    def _secure_permissions(self) -> None:
        if str(self.path) == ":memory:":
            return
        for suffix in ("", "-wal", "-shm"):
            selected = Path(f"{self.path}{suffix}")
            if selected.exists():
                selected.chmod(0o600)

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
        requested_roles = set(task["contract"].get("requested_roles", []))
        rows = self.connection.execute(
            """
            SELECT task_agents.contract_hash, task_agents.context_hash,
                   task_agents.acknowledged_at, agents.role
            FROM task_agents
            JOIN agents ON agents.agent_id = task_agents.agent_id
            WHERE task_agents.task_id = ?
            """,
            (task_id,),
        ).fetchall()
        acknowledged_roles = {row["role"] for row in rows}
        return (
            bool(rows)
            and acknowledged_roles == requested_roles
            and all(
                row["acknowledged_at"]
                and row["contract_hash"] == task["contract_hash"]
                and row["context_hash"] == task["context_hash"]
                for row in rows
            )
        )

    def bind_agent(
        self,
        *,
        task_id: str,
        agent_id: str,
        branch: str,
        worktree: str,
        session_id: str,
        git_name: str,
        git_email: str,
    ) -> None:
        payload = {
            "task_id": task_id,
            "agent_id": agent_id,
            "branch": branch,
            "worktree": worktree,
            "session_id": session_id,
            "git_name": git_name,
            "git_email": git_email,
        }
        assert_non_secret(payload)
        self.connection.execute(
            """
            INSERT INTO task_agents(
                task_id, agent_id, branch, worktree, session_id,
                git_name, git_email
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id, agent_id) DO UPDATE SET
                branch = excluded.branch,
                worktree = excluded.worktree,
                session_id = excluded.session_id,
                git_name = excluded.git_name,
                git_email = excluded.git_email
            """,
            (
                task_id,
                agent_id,
                branch,
                worktree,
                session_id,
                git_name,
                git_email,
            ),
        )
        self.connection.commit()

    def task_agents(self, task_id: str) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT task_id, agent_id, contract_hash, context_hash,
                       acknowledged_at, branch, worktree, session_id
                       , git_name, git_email
                FROM task_agents
                WHERE task_id = ?
                ORDER BY agent_id
                """,
                (task_id,),
            )
        ]

    def record_service(
        self,
        *,
        name: str,
        manager: str,
        definition_path: str,
        fingerprint: str,
        status: str,
    ) -> None:
        payload = {
            "name": name,
            "manager": manager,
            "definition_path": definition_path,
            "fingerprint": fingerprint,
            "status": status,
        }
        assert_non_secret(payload)
        self.connection.execute(
            """
            INSERT INTO services(
                name, manager, definition_path, fingerprint, status, updated_at
            ) VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                manager = excluded.manager,
                definition_path = excluded.definition_path,
                fingerprint = excluded.fingerprint,
                status = excluded.status,
                updated_at = excluded.updated_at
            """,
            (
                name,
                manager,
                definition_path,
                fingerprint,
                status,
                _now(),
            ),
        )
        self.connection.commit()

    def update_service_status(self, name: str, status: str) -> None:
        cursor = self.connection.execute(
            "UPDATE services SET status = ?, updated_at = ? WHERE name = ?",
            (status, _now(), name),
        )
        self.connection.commit()
        if cursor.rowcount != 1:
            raise KeyError(name)

    def services(self) -> list[dict[str, Any]]:
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT name, manager, definition_path, fingerprint, status,
                       updated_at
                FROM services
                ORDER BY name
                """
            )
        ]

    def add_cost(self, task_id: str, amount_usd: float) -> int:
        if amount_usd < 0:
            raise ValueError("cost cannot be negative")
        microusd = round(amount_usd * 1_000_000)
        self.connection.execute(
            """
            UPDATE tasks
            SET cost_microusd = cost_microusd + ?, updated_at = ?
            WHERE task_id = ?
            """,
            (microusd, _now(), task_id),
        )
        self.connection.commit()
        task = self.task(task_id)
        if task is None:
            raise KeyError(task_id)
        return int(task["cost_microusd"])

    def record_clarification(self, task_id: str, *, limit: int = 2) -> bool:
        if limit < 1:
            raise ValueError("clarification limit must be positive")
        self.connection.execute(
            """
            UPDATE tasks
            SET clarification_count = clarification_count + 1, updated_at = ?
            WHERE task_id = ?
            """,
            (_now(), task_id),
        )
        self.connection.commit()
        task = self.task(task_id)
        if task is None:
            raise KeyError(task_id)
        return int(task["clarification_count"]) >= limit
