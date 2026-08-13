"""Durable, tenant-scoped storage for the mobile text relay."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from .database import Database
from .relay_security import RelayPayloadCipher


class RelayAccessDenied(ValueError):
    """The caller does not own the requested relay context."""


class RelayConflict(ValueError):
    """An idempotency key or event identity conflicts with stored data."""


class RelayNotFound(ValueError):
    """The requested relay resource does not exist in the caller's scope."""


class RelayBackpressure(ValueError):
    """A worker's bounded pending queue is full."""


def _now() -> datetime:
    return datetime.now(UTC)


def _format(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class RelayRepository:
    """Store encrypted commands/events and enforce exact phone/worker scope."""

    def __init__(self, database: Database, cipher: RelayPayloadCipher) -> None:
        self.database = database
        self.cipher = cipher

    @staticmethod
    def _account_store_access(
        connection: sqlite3.Connection,
        *,
        user_id: str,
        store_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT m.tenant_id, m.role
            FROM users u
            JOIN memberships m ON m.user_id = u.id
            JOIN membership_store_grants g ON g.membership_id = m.id
            JOIN tenants t ON t.id = m.tenant_id
            JOIN stores s ON s.id = g.store_id
            WHERE u.id = ? AND s.id = ? AND u.status = 'active'
              AND m.status = 'active' AND g.status = 'active'
              AND t.status = 'active' AND s.status = 'active'
              AND s.tenant_id = m.tenant_id
            LIMIT 1
            """,
            (user_id, store_id),
        ).fetchone()

    @staticmethod
    def _safe_pairing(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        return {
            key: item.get(key)
            for key in (
                "id",
                "tenant_id",
                "store_id",
                "phone_device_id",
                "worker_device_id",
                "agent_id",
                "status",
                "created_at",
                "updated_at",
            )
        }

    @staticmethod
    def _safe_session(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        return {
            key: item.get(key)
            for key in (
                "id",
                "pairing_id",
                "store_id",
                "phone_device_id",
                "worker_device_id",
                "agent_id",
                "gateway_session_id",
                "status",
                "created_at",
                "updated_at",
                "closed_at",
            )
        }

    @staticmethod
    def _safe_command(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        item = dict(row)
        return {
            key: item.get(key)
            for key in (
                "id",
                "session_id",
                "worker_sequence",
                "command_type",
                "status",
                "expires_at",
                "created_at",
                "updated_at",
                "dispatched_at",
                "acked_at",
                "completed_at",
            )
        }

    def create_pairing(
        self,
        *,
        user_id: str,
        phone_device_id: str,
        worker_device_id: str,
    ) -> dict[str, Any]:
        now = _format(_now())
        pairing_id = f"relay_pairing_{uuid.uuid4().hex}"
        with self.database.transaction(immediate=True) as connection:
            phone = connection.execute(
                "SELECT * FROM devices WHERE id = ?", (phone_device_id,)
            ).fetchone()
            worker = connection.execute(
                "SELECT * FROM devices WHERE id = ?", (worker_device_id,)
            ).fetchone()
            if (
                phone is None
                or phone["device_class"] != "phone"
                or phone["status"] != "active"
                or phone["credential_kind"] != "ed25519"
                or phone["enrolled_by_user_id"] != user_id
            ):
                raise RelayAccessDenied("phone_device_not_owned")
            access = self._account_store_access(
                connection,
                user_id=user_id,
                store_id=str(phone["store_id"]),
            )
            if access is None or access["tenant_id"] != phone["tenant_id"]:
                raise RelayAccessDenied("store_access_denied")
            if (
                worker is None
                or worker["device_class"] != "worker"
                or worker["status"] != "active"
                or worker["credential_kind"] != "ed25519"
                or worker["tenant_id"] != phone["tenant_id"]
                or worker["store_id"] != phone["store_id"]
            ):
                raise RelayAccessDenied("worker_device_not_available")
            agent = connection.execute(
                """
                SELECT * FROM agents
                WHERE device_id = ? AND tenant_id = ? AND store_id = ?
                  AND status = 'active'
                ORDER BY id LIMIT 1
                """,
                (worker_device_id, worker["tenant_id"], worker["store_id"]),
            ).fetchone()
            if agent is None:
                raise RelayAccessDenied("worker_agent_binding_required")
            existing = connection.execute(
                """
                SELECT * FROM relay_pairings
                WHERE phone_device_id = ? AND worker_device_id = ?
                  AND store_id = ? AND status = 'active'
                """,
                (phone_device_id, worker_device_id, phone["store_id"]),
            ).fetchone()
            if existing is not None:
                return self._safe_pairing(existing)
            connection.execute(
                """
                INSERT INTO relay_pairings
                    (id, tenant_id, store_id, user_id, phone_device_id,
                     worker_device_id, agent_id, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    pairing_id,
                    phone["tenant_id"],
                    phone["store_id"],
                    user_id,
                    phone_device_id,
                    worker_device_id,
                    agent["id"],
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM relay_pairings WHERE id = ?", (pairing_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("relay pairing disappeared")
        return self._safe_pairing(row)

    def get_pairing_for_phone(
        self,
        *,
        pairing_id: str,
        user_id: str,
        phone_device_id: str,
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT p.* FROM relay_pairings p
                JOIN devices phone ON phone.id = p.phone_device_id
                JOIN devices worker ON worker.id = p.worker_device_id
                JOIN agents a ON a.id = p.agent_id
                WHERE p.id = ? AND p.user_id = ? AND p.phone_device_id = ?
                  AND p.status = 'active' AND phone.status = 'active'
                  AND worker.status = 'active' AND a.status = 'active'
                  AND phone.device_class = 'phone'
                  AND worker.device_class = 'worker'
                  AND a.device_id = worker.id
                """,
                (pairing_id, user_id, phone_device_id),
            ).fetchone()
        if row is None:
            raise RelayNotFound("relay_pairing_not_found")
        return self._safe_pairing(row)

    def create_session(
        self,
        *,
        pairing_id: str,
        user_id: str,
        phone_device_id: str,
        idempotency_key: str,
        command_ttl_seconds: int,
        max_pending_commands: int,
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        now_dt = _now()
        now = _format(now_dt)
        expires_at = _format(now_dt + timedelta(seconds=command_ttl_seconds))
        session_id = f"relay_session_{uuid.uuid4().hex}"
        command_id = f"relay_command_{uuid.uuid4().hex}"
        with self.database.transaction(immediate=True) as connection:
            pairing = connection.execute(
                """
                SELECT p.* FROM relay_pairings p
                JOIN devices phone ON phone.id = p.phone_device_id
                JOIN devices worker ON worker.id = p.worker_device_id
                JOIN agents a ON a.id = p.agent_id
                WHERE p.id = ? AND p.user_id = ? AND p.phone_device_id = ?
                  AND p.status = 'active' AND phone.status = 'active'
                  AND worker.status = 'active' AND a.status = 'active'
                  AND a.device_id = worker.id
                """,
                (pairing_id, user_id, phone_device_id),
            ).fetchone()
            if pairing is None:
                raise RelayNotFound("relay_pairing_not_found")
            existing = connection.execute(
                """
                SELECT * FROM relay_sessions
                WHERE phone_device_id = ? AND idempotency_key = ?
                """,
                (phone_device_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["pairing_id"] != pairing_id:
                    raise RelayConflict("relay_idempotency_conflict")
                command = connection.execute(
                    """
                    SELECT * FROM relay_commands
                    WHERE session_id = ? AND command_type = 'session.create'
                    ORDER BY worker_sequence LIMIT 1
                    """,
                    (existing["id"],),
                ).fetchone()
                if command is None:
                    raise RuntimeError("relay session create command disappeared")
                return (
                    self._safe_session(existing),
                    self._safe_command(command),
                    False,
                )
            self._assert_queue_capacity(
                connection,
                worker_device_id=str(pairing["worker_device_id"]),
                max_pending_commands=max_pending_commands,
                now=now,
            )
            worker_sequence = self._next_worker_sequence(
                connection, str(pairing["worker_device_id"])
            )
            connection.execute(
                """
                INSERT INTO relay_sessions
                    (id, pairing_id, tenant_id, store_id, phone_device_id,
                     worker_device_id, agent_id, gateway_session_id,
                     idempotency_key, status, created_at, updated_at, closed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, 'pending', ?, ?, NULL)
                """,
                (
                    session_id,
                    pairing_id,
                    pairing["tenant_id"],
                    pairing["store_id"],
                    phone_device_id,
                    pairing["worker_device_id"],
                    pairing["agent_id"],
                    idempotency_key,
                    now,
                    now,
                ),
            )
            encrypted = self.cipher.encrypt({}, context=f"relay-command:{command_id}")
            connection.execute(
                """
                INSERT INTO relay_commands
                    (id, session_id, worker_device_id, worker_sequence,
                     command_type, payload_nonce, payload_ciphertext,
                     payload_digest, idempotency_key, status, expires_at,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, 'session.create', ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    command_id,
                    session_id,
                    pairing["worker_device_id"],
                    worker_sequence,
                    encrypted.nonce,
                    encrypted.ciphertext,
                    encrypted.digest,
                    f"session-create:{idempotency_key}",
                    expires_at,
                    now,
                    now,
                ),
            )
            self._insert_control_event(
                connection,
                session_id=session_id,
                source_event_id=f"control-plane:{command_id}:accepted",
                event_type="session.pending",
                command_id=command_id,
                payload={"command_id": command_id},
                created_at=now,
            )
            session = connection.execute(
                "SELECT * FROM relay_sessions WHERE id = ?", (session_id,)
            ).fetchone()
            command = connection.execute(
                "SELECT * FROM relay_commands WHERE id = ?", (command_id,)
            ).fetchone()
        if session is None or command is None:
            raise RuntimeError("relay session transaction incomplete")
        return self._safe_session(session), self._safe_command(command), True

    def enqueue_command(
        self,
        *,
        session_id: str,
        user_id: str,
        phone_device_id: str,
        command_type: str,
        payload: dict[str, Any],
        idempotency_key: str,
        command_ttl_seconds: int,
        max_pending_commands: int,
    ) -> tuple[dict[str, Any], bool]:
        if command_type not in {"prompt.submit", "session.interrupt"}:
            raise ValueError("relay command type is not phone-accessible")
        now_dt = _now()
        now = _format(now_dt)
        expires_at = _format(now_dt + timedelta(seconds=command_ttl_seconds))
        command_id = f"relay_command_{uuid.uuid4().hex}"
        encrypted = self.cipher.encrypt(payload, context=f"relay-command:{command_id}")
        with self.database.transaction(immediate=True) as connection:
            session = self._session_for_phone(
                connection,
                session_id=session_id,
                user_id=user_id,
                phone_device_id=phone_device_id,
            )
            if session["status"] not in {"pending", "active"}:
                raise RelayConflict("relay_session_not_writable")
            existing = connection.execute(
                """
                SELECT * FROM relay_commands
                WHERE session_id = ? AND idempotency_key = ?
                """,
                (session_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if (
                    existing["command_type"] != command_type
                    or existing["payload_digest"] != encrypted.digest
                ):
                    raise RelayConflict("relay_idempotency_conflict")
                return self._safe_command(existing), False
            self._assert_queue_capacity(
                connection,
                worker_device_id=str(session["worker_device_id"]),
                max_pending_commands=max_pending_commands,
                now=now,
            )
            worker_sequence = self._next_worker_sequence(
                connection, str(session["worker_device_id"])
            )
            connection.execute(
                """
                INSERT INTO relay_commands
                    (id, session_id, worker_device_id, worker_sequence,
                     command_type, payload_nonce, payload_ciphertext,
                     payload_digest, idempotency_key, status, expires_at,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                """,
                (
                    command_id,
                    session_id,
                    session["worker_device_id"],
                    worker_sequence,
                    command_type,
                    encrypted.nonce,
                    encrypted.ciphertext,
                    encrypted.digest,
                    idempotency_key,
                    expires_at,
                    now,
                    now,
                ),
            )
            event_type = (
                "prompt.accepted"
                if command_type == "prompt.submit"
                else "session.interrupt.accepted"
            )
            self._insert_control_event(
                connection,
                session_id=session_id,
                source_event_id=f"control-plane:{command_id}:accepted",
                event_type=event_type,
                command_id=command_id,
                payload={"command_id": command_id},
                created_at=now,
            )
            row = connection.execute(
                "SELECT * FROM relay_commands WHERE id = ?", (command_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("relay command disappeared")
        return self._safe_command(row), True

    @staticmethod
    def _session_for_phone(
        connection: sqlite3.Connection,
        *,
        session_id: str,
        user_id: str,
        phone_device_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT s.* FROM relay_sessions s
            JOIN relay_pairings p ON p.id = s.pairing_id
            JOIN devices phone ON phone.id = s.phone_device_id
            WHERE s.id = ? AND p.user_id = ? AND s.phone_device_id = ?
              AND p.status = 'active' AND phone.status = 'active'
            """,
            (session_id, user_id, phone_device_id),
        ).fetchone()
        if row is None:
            raise RelayNotFound("relay_session_not_found")
        return row

    @staticmethod
    def _next_worker_sequence(
        connection: sqlite3.Connection, worker_device_id: str
    ) -> int:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(worker_sequence), 0) + 1 AS next_sequence
            FROM relay_commands WHERE worker_device_id = ?
            """,
            (worker_device_id,),
        ).fetchone()
        return int(row["next_sequence"])

    @staticmethod
    def _assert_queue_capacity(
        connection: sqlite3.Connection,
        *,
        worker_device_id: str,
        max_pending_commands: int,
        now: str,
    ) -> None:
        connection.execute(
            """
            UPDATE relay_commands
            SET status = 'expired', completed_at = ?, updated_at = ?
            WHERE worker_device_id = ? AND status IN ('queued', 'dispatched', 'acked')
              AND expires_at <= ?
            """,
            (now, now, worker_device_id, now),
        )
        count = connection.execute(
            """
            SELECT COUNT(*) AS count FROM relay_commands
            WHERE worker_device_id = ?
              AND status IN ('queued', 'dispatched', 'acked')
            """,
            (worker_device_id,),
        ).fetchone()
        if int(count["count"]) >= max_pending_commands:
            raise RelayBackpressure("relay_worker_queue_full")

    def _insert_control_event(
        self,
        connection: sqlite3.Connection,
        *,
        session_id: str,
        source_event_id: str,
        event_type: str,
        command_id: str | None,
        payload: dict[str, Any],
        created_at: str,
    ) -> dict[str, Any]:
        event_id = f"relay_event_{uuid.uuid4().hex}"
        encrypted = self.cipher.encrypt(payload, context=f"relay-event:{event_id}")
        sequence_row = connection.execute(
            """
            SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
            FROM relay_events WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        sequence = int(sequence_row["next_sequence"])
        connection.execute(
            """
            INSERT INTO relay_events
                (id, session_id, sequence, source_event_id, event_type,
                 command_id, payload_nonce, payload_ciphertext, payload_digest,
                 created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                session_id,
                sequence,
                source_event_id,
                event_type,
                command_id,
                encrypted.nonce,
                encrypted.ciphertext,
                encrypted.digest,
                created_at,
            ),
        )
        return {
            "id": event_id,
            "session_id": session_id,
            "sequence": sequence,
            "source_event_id": source_event_id,
            "event_type": event_type,
            "command_id": command_id,
            "payload": payload,
            "created_at": created_at,
        }

    def list_sessions(
        self, *, user_id: str, phone_device_id: str, limit: int
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT s.* FROM relay_sessions s
                JOIN relay_pairings p ON p.id = s.pairing_id
                JOIN devices phone ON phone.id = s.phone_device_id
                WHERE p.user_id = ? AND s.phone_device_id = ?
                  AND phone.status = 'active'
                ORDER BY s.created_at DESC, s.id DESC LIMIT ?
                """,
                (user_id, phone_device_id, limit),
            ).fetchall()
        return [self._safe_session(row) for row in rows]

    def get_session(
        self, *, session_id: str, user_id: str, phone_device_id: str
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = self._session_for_phone(
                connection,
                session_id=session_id,
                user_id=user_id,
                phone_device_id=phone_device_id,
            )
        return self._safe_session(row)

    def list_events(
        self,
        *,
        session_id: str,
        user_id: str,
        phone_device_id: str,
        after_sequence: int,
        limit: int,
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            self._session_for_phone(
                connection,
                session_id=session_id,
                user_id=user_id,
                phone_device_id=phone_device_id,
            )
            rows = connection.execute(
                """
                SELECT * FROM relay_events
                WHERE session_id = ? AND sequence > ?
                ORDER BY sequence LIMIT ?
                """,
                (session_id, after_sequence, limit),
            ).fetchall()
        events: list[dict[str, Any]] = []
        for row in rows:
            payload = self.cipher.decrypt(
                nonce=row["payload_nonce"],
                ciphertext=row["payload_ciphertext"],
                digest=row["payload_digest"],
                context=f"relay-event:{row['id']}",
            )
            events.append({
                "id": row["id"],
                "session_id": row["session_id"],
                "sequence": row["sequence"],
                "source_event_id": row["source_event_id"],
                "event_type": row["event_type"],
                "command_id": row["command_id"],
                "payload": payload,
                "created_at": row["created_at"],
            })
        return events

    def open_worker_connection(
        self, *, worker_device_id: str, connection_id: str
    ) -> None:
        now = _format(_now())
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE relay_worker_connections
                SET disconnected_at = COALESCE(disconnected_at, ?),
                    close_reason = COALESCE(close_reason, 'superseded')
                WHERE worker_device_id = ? AND disconnected_at IS NULL
                """,
                (now, worker_device_id),
            )
            connection.execute(
                """
                INSERT INTO relay_worker_connections
                    (id, worker_device_id, connected_at, last_seen_at,
                     disconnected_at, close_reason)
                VALUES (?, ?, ?, ?, NULL, NULL)
                """,
                (connection_id, worker_device_id, now, now),
            )

    def revoke_device_scope(self, device_id: str) -> None:
        """Terminate every relay capability rooted in a revoked device."""

        now = _format(_now())
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE relay_pairings SET status = 'revoked', updated_at = ?
                WHERE status = 'active'
                  AND (phone_device_id = ? OR worker_device_id = ?)
                """,
                (now, device_id, device_id),
            )
            connection.execute(
                """
                UPDATE relay_sessions SET status = 'closed', updated_at = ?,
                    closed_at = COALESCE(closed_at, ?)
                WHERE status IN ('pending', 'active')
                  AND (phone_device_id = ? OR worker_device_id = ?)
                """,
                (now, now, device_id, device_id),
            )
            connection.execute(
                """
                UPDATE relay_commands SET status = 'canceled', updated_at = ?,
                    completed_at = COALESCE(completed_at, ?)
                WHERE status IN ('queued', 'dispatched', 'acked')
                  AND session_id IN (
                      SELECT id FROM relay_sessions
                      WHERE phone_device_id = ? OR worker_device_id = ?
                  )
                """,
                (now, now, device_id, device_id),
            )
            connection.execute(
                """
                UPDATE relay_worker_connections
                SET disconnected_at = COALESCE(disconnected_at, ?),
                    close_reason = COALESCE(close_reason, 'device_revoked'),
                    last_seen_at = ?
                WHERE worker_device_id = ? AND disconnected_at IS NULL
                """,
                (now, now, device_id),
            )

    def touch_worker_connection(self, connection_id: str) -> None:
        now = _format(_now())
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE relay_worker_connections SET last_seen_at = ?
                WHERE id = ? AND disconnected_at IS NULL
                """,
                (now, connection_id),
            )

    def close_worker_connection(self, connection_id: str, *, reason: str) -> None:
        now = _format(_now())
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE relay_worker_connections
                SET disconnected_at = COALESCE(disconnected_at, ?),
                    close_reason = COALESCE(close_reason, ?), last_seen_at = ?
                WHERE id = ?
                """,
                (now, reason[:80], now, connection_id),
            )

    def list_deliverable_commands(
        self, *, worker_device_id: str, limit: int
    ) -> list[dict[str, Any]]:
        now = _format(_now())
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE relay_commands
                SET status = 'expired', completed_at = ?, updated_at = ?
                WHERE worker_device_id = ? AND status IN ('queued', 'dispatched')
                  AND expires_at <= ?
                """,
                (now, now, worker_device_id, now),
            )
            rows = connection.execute(
                """
                SELECT c.*, s.gateway_session_id
                FROM relay_commands c
                JOIN relay_sessions s ON s.id = c.session_id
                WHERE c.worker_device_id = ?
                  AND c.status IN ('queued', 'dispatched')
                  AND c.expires_at > ?
                ORDER BY c.worker_sequence LIMIT ?
                """,
                (worker_device_id, now, limit),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"""
                    UPDATE relay_commands
                    SET status = 'dispatched', dispatched_at = COALESCE(dispatched_at, ?),
                        updated_at = ?
                    WHERE id IN ({placeholders}) AND status = 'queued'
                    """,
                    (now, now, *ids),
                )
        output: list[dict[str, Any]] = []
        for row in rows:
            payload = self.cipher.decrypt(
                nonce=row["payload_nonce"],
                ciphertext=row["payload_ciphertext"],
                digest=row["payload_digest"],
                context=f"relay-command:{row['id']}",
            )
            output.append({
                **self._safe_command(row),
                "gateway_session_id": row["gateway_session_id"],
                "payload": payload,
            })
        return output

    def acknowledge_command(
        self, *, worker_device_id: str, command_id: str
    ) -> dict[str, Any]:
        now = _format(_now())
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT * FROM relay_commands
                WHERE id = ? AND worker_device_id = ?
                """,
                (command_id, worker_device_id),
            ).fetchone()
            if row is None:
                raise RelayNotFound("relay_command_not_found")
            if row["status"] in {"queued", "dispatched"}:
                connection.execute(
                    """
                    UPDATE relay_commands
                    SET status = 'acked', acked_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, command_id),
                )
            updated = connection.execute(
                "SELECT * FROM relay_commands WHERE id = ?", (command_id,)
            ).fetchone()
        if updated is None:
            raise RuntimeError("relay command disappeared")
        return self._safe_command(updated)

    def append_worker_event(
        self,
        *,
        worker_device_id: str,
        session_id: str,
        source_event_id: str,
        event_type: str,
        command_id: str | None,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        now = _format(_now())
        event_id = f"relay_event_{uuid.uuid4().hex}"
        encrypted = self.cipher.encrypt(payload, context=f"relay-event:{event_id}")
        with self.database.transaction(immediate=True) as connection:
            session = connection.execute(
                """
                SELECT s.* FROM relay_sessions s
                JOIN devices worker ON worker.id = s.worker_device_id
                JOIN agents a ON a.id = s.agent_id
                WHERE s.id = ? AND s.worker_device_id = ?
                  AND worker.status = 'active' AND worker.device_class = 'worker'
                  AND a.status = 'active' AND a.device_id = worker.id
                """,
                (session_id, worker_device_id),
            ).fetchone()
            if session is None:
                raise RelayNotFound("relay_session_not_found")
            command = None
            if command_id is not None:
                command = connection.execute(
                    """
                    SELECT * FROM relay_commands
                    WHERE id = ? AND session_id = ? AND worker_device_id = ?
                    """,
                    (command_id, session_id, worker_device_id),
                ).fetchone()
                if command is None:
                    raise RelayNotFound("relay_command_not_found")
            existing = connection.execute(
                """
                SELECT * FROM relay_events
                WHERE session_id = ? AND source_event_id = ?
                """,
                (session_id, source_event_id),
            ).fetchone()
            if existing is not None:
                if (
                    existing["event_type"] != event_type
                    or existing["command_id"] != command_id
                    or existing["payload_digest"] != encrypted.digest
                ):
                    raise RelayConflict("relay_event_identity_conflict")
                stored_payload = self.cipher.decrypt(
                    nonce=existing["payload_nonce"],
                    ciphertext=existing["payload_ciphertext"],
                    digest=existing["payload_digest"],
                    context=f"relay-event:{existing['id']}",
                )
                return (
                    {
                        "id": existing["id"],
                        "session_id": existing["session_id"],
                        "sequence": existing["sequence"],
                        "source_event_id": existing["source_event_id"],
                        "event_type": existing["event_type"],
                        "command_id": existing["command_id"],
                        "payload": stored_payload,
                        "created_at": existing["created_at"],
                    },
                    False,
                )
            if command is not None and command["status"] in {
                "succeeded",
                "failed",
                "expired",
                "canceled",
            }:
                raise RelayConflict("relay_command_not_accepting_events")
            sequence_row = connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                FROM relay_events WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
            sequence = int(sequence_row["next_sequence"])
            connection.execute(
                """
                INSERT INTO relay_events
                    (id, session_id, sequence, source_event_id, event_type,
                     command_id, payload_nonce, payload_ciphertext,
                     payload_digest, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    session_id,
                    sequence,
                    source_event_id,
                    event_type,
                    command_id,
                    encrypted.nonce,
                    encrypted.ciphertext,
                    encrypted.digest,
                    now,
                ),
            )
            if event_type == "session.created":
                gateway_session_id = str(payload.get("gateway_session_id") or "")
                if not gateway_session_id:
                    raise RelayConflict("relay_gateway_session_id_required")
                connection.execute(
                    """
                    UPDATE relay_sessions SET gateway_session_id = ?,
                        status = 'active', updated_at = ? WHERE id = ?
                    """,
                    (gateway_session_id, now, session_id),
                )
            elif event_type in {"session.failed", "session.closed"}:
                state = "failed" if event_type == "session.failed" else "closed"
                connection.execute(
                    """
                    UPDATE relay_sessions SET status = ?, updated_at = ?,
                        closed_at = CASE WHEN ? = 'closed' THEN ? ELSE closed_at END
                    WHERE id = ?
                    """,
                    (state, now, state, now, session_id),
                )
            if command is not None:
                command_state: str | None = None
                if event_type in {
                    "session.created",
                    "message.complete",
                    "session.interrupted",
                }:
                    command_state = "succeeded"
                elif event_type in {"error", "session.failed"}:
                    command_state = "failed"
                if command_state is not None:
                    connection.execute(
                        """
                        UPDATE relay_commands SET status = ?, completed_at = ?,
                            updated_at = ? WHERE id = ?
                          AND status NOT IN ('expired', 'canceled')
                        """,
                        (command_state, now, now, command_id),
                    )
        return (
            {
                "id": event_id,
                "session_id": session_id,
                "sequence": sequence,
                "source_event_id": source_event_id,
                "event_type": event_type,
                "command_id": command_id,
                "payload": payload,
                "created_at": now,
            },
            True,
        )

    def get_command_status(
        self,
        *,
        command_id: str,
        user_id: str,
        phone_device_id: str,
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT c.* FROM relay_commands c
                JOIN relay_sessions s ON s.id = c.session_id
                JOIN relay_pairings p ON p.id = s.pairing_id
                WHERE c.id = ? AND p.user_id = ? AND s.phone_device_id = ?
                """,
                (command_id, user_id, phone_device_id),
            ).fetchone()
        if row is None:
            raise RelayNotFound("relay_command_not_found")
        return self._safe_command(row)
