"""Encrypted local inbox/outbox for the outbound relay worker."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from altas.control_plane.relay_security import RelayPayloadCipher


class RelayInboxConflict(ValueError):
    """A command or event identity was reused with different content."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS relay_inbox (
    command_id TEXT PRIMARY KEY,
    worker_sequence INTEGER NOT NULL UNIQUE,
    session_id TEXT NOT NULL,
    gateway_session_id TEXT,
    command_type TEXT NOT NULL,
    payload_nonce TEXT NOT NULL,
    payload_ciphertext TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('pending', 'executing', 'completed', 'failed', 'uncertain')
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS relay_outbox (
    source_event_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL REFERENCES relay_inbox(command_id),
    session_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    frame_nonce TEXT NOT NULL,
    frame_ciphertext TEXT NOT NULL,
    frame_digest TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'acked')),
    created_at TEXT NOT NULL,
    acked_at TEXT
);

CREATE TABLE IF NOT EXISTS relay_session_bindings (
    session_id TEXT PRIMARY KEY,
    gateway_session_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_local_relay_inbox_state
    ON relay_inbox(state, worker_sequence);
CREATE INDEX IF NOT EXISTS idx_local_relay_outbox_state
    ON relay_outbox(state, created_at);
"""


class LocalRelayStore:
    """Make delivery acknowledgement subordinate to a committed local inbox."""

    def __init__(self, path: Path, *, storage_key: bytes) -> None:
        self.path = Path(path)
        self.cipher = RelayPayloadCipher(storage_key)
        self._initialize()

    def _initialize(self) -> None:
        if self.path.is_symlink():
            raise ValueError("relay store path must not be a symbolic link")
        parent_existed = self.path.parent.exists()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not parent_existed:
            self.path.parent.chmod(0o700)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(_SCHEMA)
        for database_file in self.path.parent.glob(f"{self.path.name}*"):
            if database_file.is_file():
                database_file.chmod(0o600)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
        finally:
            connection.close()

    def recover_uncertain(self) -> list[dict[str, Any]]:
        """Fail closed after a crash inside a non-idempotent local RPC call."""

        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM relay_inbox WHERE state = 'executing' ORDER BY worker_sequence"
            ).fetchall()
            connection.execute(
                """
                UPDATE relay_inbox SET state = 'uncertain', updated_at = ?
                WHERE state = 'executing'
                """,
                (now,),
            )
            connection.commit()
        return [self._command_from_row(row) for row in rows]

    def persist_command(self, command: dict[str, Any]) -> bool:
        """Commit a command before the connector may send ``command.ack``."""

        command_id = str(command.get("id") or "")
        session_id = str(command.get("session_id") or "")
        command_type = str(command.get("command_type") or "")
        payload = command.get("payload")
        worker_sequence = command.get("worker_sequence")
        gateway_session_id = str(command.get("gateway_session_id") or "") or None
        if (
            not command_id
            or not session_id
            or command_type
            not in {"session.create", "prompt.submit", "session.interrupt"}
            or not isinstance(payload, dict)
            or not isinstance(worker_sequence, int)
            or isinstance(worker_sequence, bool)
            or worker_sequence < 1
        ):
            raise ValueError("relay_command_invalid")
        encrypted = self.cipher.encrypt(
            payload,
            context=f"local-relay-command:{command_id}",
        )
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM relay_inbox WHERE command_id = ?", (command_id,)
            ).fetchone()
            if existing is not None:
                if (
                    existing["session_id"] != session_id
                    or existing["command_type"] != command_type
                    or existing["worker_sequence"] != worker_sequence
                    or existing["payload_digest"] != encrypted.digest
                ):
                    connection.rollback()
                    raise RelayInboxConflict("relay_command_identity_conflict")
                connection.commit()
                return False
            try:
                connection.execute(
                    """
                    INSERT INTO relay_inbox
                        (command_id, worker_sequence, session_id,
                         gateway_session_id, command_type, payload_nonce,
                         payload_ciphertext, payload_digest, state, created_at,
                         updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                    """,
                    (
                        command_id,
                        worker_sequence,
                        session_id,
                        gateway_session_id,
                        command_type,
                        encrypted.nonce,
                        encrypted.ciphertext,
                        encrypted.digest,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise RelayInboxConflict("relay_command_sequence_conflict") from exc
            connection.commit()
        return True

    def claim_next_command(self) -> dict[str, Any] | None:
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM relay_inbox WHERE state = 'pending'
                ORDER BY worker_sequence LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            updated = connection.execute(
                """
                UPDATE relay_inbox SET state = 'executing', updated_at = ?
                WHERE command_id = ? AND state = 'pending'
                """,
                (now, row["command_id"]),
            )
            if updated.rowcount != 1:
                connection.rollback()
                return None
            connection.commit()
        command = self._command_from_row(row)
        command["state"] = "executing"
        return command

    def _command_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = self.cipher.decrypt(
            nonce=row["payload_nonce"],
            ciphertext=row["payload_ciphertext"],
            digest=row["payload_digest"],
            context=f"local-relay-command:{row['command_id']}",
        )
        return {
            "id": row["command_id"],
            "worker_sequence": row["worker_sequence"],
            "session_id": row["session_id"],
            "gateway_session_id": row["gateway_session_id"],
            "command_type": row["command_type"],
            "payload": payload,
            "state": row["state"],
        }

    def mark_command(self, command_id: str, *, state: str) -> None:
        if state not in {"completed", "failed", "uncertain"}:
            raise ValueError("relay command terminal state invalid")
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE relay_inbox SET state = ?, updated_at = ?
                WHERE command_id = ? AND state IN ('executing', 'uncertain')
                """,
                (state, _now(), command_id),
            )
        if updated.rowcount != 1:
            raise ValueError("relay command state transition invalid")

    def bind_session(self, *, session_id: str, gateway_session_id: str) -> None:
        if not session_id or not gateway_session_id:
            raise ValueError("relay session binding invalid")
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO relay_session_bindings
                    (session_id, gateway_session_id, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    gateway_session_id = excluded.gateway_session_id,
                    updated_at = excluded.updated_at
                """,
                (session_id, gateway_session_id, now, now),
            )

    def gateway_session_id(self, session_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT gateway_session_id FROM relay_session_bindings WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return str(row["gateway_session_id"]) if row is not None else None

    def persist_event(self, frame: dict[str, Any]) -> bool:
        source_event_id = str(frame.get("source_event_id") or "")
        command_id = str(frame.get("command_id") or "")
        session_id = str(frame.get("session_id") or "")
        event_type = str(frame.get("event_type") or "")
        payload = frame.get("payload")
        if (
            not source_event_id
            or not command_id
            or not session_id
            or not event_type
            or not isinstance(payload, dict)
        ):
            raise ValueError("relay_event_invalid")
        encrypted = self.cipher.encrypt(
            frame,
            context=f"local-relay-event:{source_event_id}",
        )
        now = _now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM relay_outbox WHERE source_event_id = ?",
                (source_event_id,),
            ).fetchone()
            if existing is not None:
                if existing["frame_digest"] != encrypted.digest:
                    connection.rollback()
                    raise RelayInboxConflict("relay_event_identity_conflict")
                connection.commit()
                return False
            connection.execute(
                """
                INSERT INTO relay_outbox
                    (source_event_id, command_id, session_id, event_type,
                     frame_nonce, frame_ciphertext, frame_digest, state,
                     created_at, acked_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, NULL)
                """,
                (
                    source_event_id,
                    command_id,
                    session_id,
                    event_type,
                    encrypted.nonce,
                    encrypted.ciphertext,
                    encrypted.digest,
                    now,
                ),
            )
            connection.commit()
        return True

    def pending_events(self, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM relay_outbox WHERE state = 'pending'
                ORDER BY created_at, source_event_id LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            self.cipher.decrypt(
                nonce=row["frame_nonce"],
                ciphertext=row["frame_ciphertext"],
                digest=row["frame_digest"],
                context=f"local-relay-event:{row['source_event_id']}",
            )
            for row in rows
        ]

    def acknowledge_event(self, source_event_id: str) -> None:
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE relay_outbox SET state = 'acked', acked_at = ?
                WHERE source_event_id = ? AND state = 'pending'
                """,
                (_now(), source_event_id),
            )
            if updated.rowcount == 0:
                existing = connection.execute(
                    "SELECT state FROM relay_outbox WHERE source_event_id = ?",
                    (source_event_id,),
                ).fetchone()
                if existing is None:
                    raise ValueError("relay event not found")
