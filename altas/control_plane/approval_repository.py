"""Durable exact-ID managed approvals bound to live job attempts."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from altas.managed.actions import (
    ACTION_POLICY_VERSION,
    ManagedAction,
)

from .database import Database
from .relay_repository import RelayRepository
from .relay_security import RelayPayloadCipher
from .security import LeaseClaims, hash_secret


class ApprovalAccessDenied(ValueError):
    """The caller does not own the exact approval context."""


class ApprovalConflict(ValueError):
    """An approval identity or one-time state transition conflicts."""


class ApprovalNotFound(ValueError):
    """No approval exists in the caller's exact scope."""


class ApprovalExpired(ValueError):
    """The approval deadline passed before the requested transition."""


def _now() -> datetime:
    return datetime.now(UTC)


def _format(value: datetime) -> str:
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("approval timestamp invalid")
    return parsed.astimezone(UTC)


def _workflow_for_job(job: sqlite3.Row) -> str:
    workflow = str(job["capability"])
    try:
        payload = json.loads(str(job["payload_json"] or "{}"))
    except (json.JSONDecodeError, TypeError):
        payload = {}
    if isinstance(payload, dict):
        proposed = str(payload.get("workflow") or "").strip()
        if (
            proposed
            and len(proposed) <= 160
            and all(char.isalnum() or char in "._:/-" for char in proposed)
        ):
            workflow = proposed
    return workflow


class ApprovalRepository:
    """Own approval creation, exact response, expiry, and one-time consume."""

    def __init__(
        self,
        database: Database,
        *,
        cipher: RelayPayloadCipher,
        relay_repository: RelayRepository,
    ) -> None:
        self.database = database
        self.cipher = cipher
        self.relay_repository = relay_repository

    def _approval_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        action = self.cipher.decrypt(
            nonce=row["display_nonce"],
            ciphertext=row["display_ciphertext"],
            digest=row["display_digest"],
            context=f"managed-approval:{row['id']}",
        )
        return {
            key: row[key]
            for key in (
                "id",
                "store_id",
                "phone_device_id",
                "worker_device_id",
                "agent_id",
                "job_id",
                "relay_session_id",
                "job_attempt",
                "workflow",
                "capability",
                "action_kind",
                "operation",
                "target_type",
                "action_digest",
                "policy_version",
                "correlation_id",
                "status",
                "version",
                "decision",
                "decision_reason",
                "created_at",
                "updated_at",
                "expires_at",
                "decided_at",
                "consumed_at",
            )
        } | {"action": action}

    def _event_payload(self, approval: dict[str, Any]) -> dict[str, Any]:
        return {
            "approval_id": approval["id"],
            "job_id": approval["job_id"],
            "job_attempt": approval["job_attempt"],
            "workflow": approval["workflow"],
            "capability": approval["capability"],
            "action": approval["action"],
            "action_digest": approval["action_digest"],
            "policy_version": approval["policy_version"],
            "status": approval["status"],
            "version": approval["version"],
            "decision": approval["decision"],
            "decision_reason": approval["decision_reason"],
            "expires_at": approval["expires_at"],
        }

    def create_request(
        self,
        *,
        worker_device_id: str,
        relay_session_id: str,
        job_id: str,
        claim_token: str,
        lease_claims: LeaseClaims,
        action: ManagedAction,
        idempotency_key: str,
        ttl_seconds: int,
    ) -> tuple[dict[str, Any], bool]:
        if not action.requires_approval:
            raise ApprovalConflict("managed_action_does_not_require_approval")
        approval_id = f"managed_approval_{uuid.uuid4().hex}"
        correlation_id = f"approval_correlation_{uuid.uuid4().hex}"
        now_dt = _now()
        now = _format(now_dt)
        lease_expiry = datetime.fromtimestamp(lease_claims.expires_at, tz=UTC)
        expires_at_dt = min(now_dt + timedelta(seconds=ttl_seconds), lease_expiry)
        if expires_at_dt <= now_dt:
            raise ApprovalExpired("approval_lease_expired")
        expires_at = _format(expires_at_dt)
        idempotency_hash = hash_secret(idempotency_key)
        encrypted = self.cipher.encrypt(
            action.to_mapping(),
            context=f"managed-approval:{approval_id}",
        )
        claim_hash = hash_secret(claim_token)
        with self.database.transaction(immediate=True) as connection:
            job = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if (
                job is None
                or job["status"] != "running"
                or job["claimed_by_device_id"] != worker_device_id
                or not job["claim_token_hash"]
                or not hmac.compare_digest(str(job["claim_token_hash"]), claim_hash)
                or job["tenant_id"] != lease_claims.tenant_id
                or job["store_id"] != lease_claims.store_id
                or job["agent_id"] != lease_claims.agent_id
                or job["capability"] not in lease_claims.capabilities
            ):
                raise ApprovalAccessDenied("approval_job_claim_invalid")
            relay = connection.execute(
                """
                SELECT s.*, p.user_id
                FROM relay_sessions s
                JOIN relay_pairings p ON p.id = s.pairing_id
                JOIN devices phone ON phone.id = s.phone_device_id
                JOIN devices worker ON worker.id = s.worker_device_id
                JOIN agents a ON a.id = s.agent_id
                JOIN memberships m
                  ON m.user_id = p.user_id AND m.tenant_id = s.tenant_id
                JOIN membership_store_grants g
                  ON g.membership_id = m.id AND g.store_id = s.store_id
                JOIN tenants t ON t.id = s.tenant_id
                JOIN stores st ON st.id = s.store_id
                JOIN subscriptions sub ON sub.tenant_id = s.tenant_id
                JOIN entitlements e
                  ON e.tenant_id = s.tenant_id AND e.store_id = s.store_id
                WHERE s.id = ? AND s.status = 'active' AND p.status = 'active'
                  AND s.tenant_id = ? AND s.store_id = ?
                  AND s.worker_device_id = ? AND s.agent_id = ?
                  AND phone.status = 'active' AND phone.device_class = 'phone'
                  AND worker.status = 'active' AND worker.device_class = 'worker'
                  AND a.status = 'active' AND a.device_id = worker.id
                  AND m.status = 'active' AND m.role IN ('owner', 'operator')
                  AND g.status = 'active' AND t.status = 'active'
                  AND st.status = 'active' AND st.tenant_id = t.id
                  AND sub.status = 'active'
                  AND (sub.current_period_end IS NULL OR sub.current_period_end > ?)
                  AND e.capability = ? AND e.status = 'active'
                  AND (e.expires_at IS NULL OR e.expires_at > ?)
                """,
                (
                    relay_session_id,
                    job["tenant_id"],
                    job["store_id"],
                    worker_device_id,
                    job["agent_id"],
                    now,
                    job["capability"],
                    now,
                ),
            ).fetchone()
            if relay is None:
                raise ApprovalAccessDenied("approval_relay_scope_invalid")
            existing = connection.execute(
                """
                SELECT * FROM managed_approvals
                WHERE worker_device_id = ? AND idempotency_key = ?
                """,
                (worker_device_id, idempotency_hash),
            ).fetchone()
            if existing is not None:
                if (
                    existing["job_id"] != job_id
                    or existing["job_attempt"] != job["attempt_count"]
                    or existing["relay_session_id"] != relay_session_id
                    or existing["action_digest"] != action.digest()
                ):
                    raise ApprovalConflict("approval_idempotency_conflict")
                return self._approval_from_row(existing), False
            workflow = _workflow_for_job(job)
            connection.execute(
                """
                INSERT INTO managed_approvals
                    (id, tenant_id, store_id, user_id, phone_device_id,
                     worker_device_id, agent_id, job_id, relay_session_id,
                     job_attempt, workflow, capability, action_kind, operation,
                     target_type, action_digest, display_nonce,
                     display_ciphertext, display_digest, policy_version,
                     correlation_id, lease_nonce, lease_expires_at,
                     idempotency_key, status, version, created_at, updated_at,
                     expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, 'pending', 1, ?, ?, ?)
                """,
                (
                    approval_id,
                    job["tenant_id"],
                    job["store_id"],
                    relay["user_id"],
                    relay["phone_device_id"],
                    worker_device_id,
                    job["agent_id"],
                    job_id,
                    relay_session_id,
                    job["attempt_count"],
                    workflow,
                    job["capability"],
                    action.kind,
                    action.operation,
                    action.target_type,
                    action.digest(),
                    encrypted.nonce,
                    encrypted.ciphertext,
                    encrypted.digest,
                    ACTION_POLICY_VERSION,
                    correlation_id,
                    lease_claims.nonce,
                    lease_claims.expires_at,
                    idempotency_hash,
                    now,
                    now,
                    expires_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM managed_approvals WHERE id = ?", (approval_id,)
            ).fetchone()
            if row is None:
                raise RuntimeError("managed approval disappeared")
            approval = self._approval_from_row(row)
            self.relay_repository.insert_control_event(
                connection,
                session_id=relay_session_id,
                source_event_id=f"control-plane:{approval_id}:requested:v1",
                event_type="approval.requested",
                command_id=None,
                payload=self._event_payload(approval),
                created_at=now,
            )
        return approval, True

    def _phone_row(
        self,
        connection: sqlite3.Connection,
        *,
        approval_id: str,
        user_id: str,
        phone_device_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT a.* FROM managed_approvals a
            JOIN relay_sessions s ON s.id = a.relay_session_id
            JOIN relay_pairings p ON p.id = s.pairing_id
            JOIN devices phone ON phone.id = a.phone_device_id
            JOIN memberships m
              ON m.user_id = a.user_id AND m.tenant_id = a.tenant_id
            JOIN membership_store_grants g
              ON g.membership_id = m.id AND g.store_id = a.store_id
            JOIN tenants t ON t.id = a.tenant_id
            JOIN stores st ON st.id = a.store_id
            WHERE a.id = ? AND a.user_id = ? AND a.phone_device_id = ?
              AND p.user_id = a.user_id AND s.phone_device_id = a.phone_device_id
              AND s.status = 'active' AND p.status = 'active'
              AND phone.status = 'active' AND phone.device_class = 'phone'
              AND m.status = 'active' AND m.role IN ('owner', 'operator')
              AND g.status = 'active' AND t.status = 'active'
              AND st.status = 'active' AND st.tenant_id = t.id
            """,
            (approval_id, user_id, phone_device_id),
        ).fetchone()
        if row is None:
            raise ApprovalNotFound("managed_approval_not_found")
        return row

    def _expire_row(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        now_dt: datetime,
    ) -> sqlite3.Row:
        if (
            row["status"] in {"pending", "approved"}
            and _parse(str(row["expires_at"])) <= now_dt
        ):
            now = _format(now_dt)
            updated = connection.execute(
                """
                UPDATE managed_approvals
                SET status = 'expired', version = version + 1, updated_at = ?
                WHERE id = ? AND version = ? AND status IN ('pending', 'approved')
                """,
                (now, row["id"], row["version"]),
            )
            if updated.rowcount == 1:
                row = connection.execute(
                    "SELECT * FROM managed_approvals WHERE id = ?", (row["id"],)
                ).fetchone()
                approval = self._approval_from_row(row)
                self.relay_repository.insert_control_event(
                    connection,
                    session_id=row["relay_session_id"],
                    source_event_id=(
                        f"control-plane:{row['id']}:expired:v{row['version']}"
                    ),
                    event_type="approval.expired",
                    command_id=None,
                    payload=self._event_payload(approval),
                    created_at=now,
                )
        return row

    def get_for_phone(
        self,
        *,
        approval_id: str,
        user_id: str,
        phone_device_id: str,
    ) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            row = self._phone_row(
                connection,
                approval_id=approval_id,
                user_id=user_id,
                phone_device_id=phone_device_id,
            )
            row = self._expire_row(connection, row, now_dt=_now())
        return self._approval_from_row(row)

    def list_for_phone(
        self,
        *,
        user_id: str,
        phone_device_id: str,
        relay_session_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        now_dt = _now()
        with self.database.transaction(immediate=True) as connection:
            query = (
                "SELECT a.* FROM managed_approvals a "
                "WHERE a.user_id = ? AND a.phone_device_id = ? "
                "AND EXISTS ("
                "SELECT 1 FROM memberships m "
                "JOIN membership_store_grants g ON g.membership_id = m.id "
                "JOIN tenants t ON t.id = m.tenant_id "
                "JOIN stores st ON st.id = g.store_id "
                "WHERE m.user_id = a.user_id AND m.tenant_id = a.tenant_id "
                "AND g.store_id = a.store_id AND m.status = 'active' "
                "AND m.role IN ('owner','operator') AND g.status = 'active' "
                "AND t.status = 'active' AND st.status = 'active'"
                ")"
            )
            params: list[Any] = [user_id, phone_device_id]
            if relay_session_id:
                query += " AND a.relay_session_id = ?"
                params.append(relay_session_id)
            query += " ORDER BY a.created_at DESC, a.id DESC LIMIT ?"
            params.append(limit)
            rows = connection.execute(query, params).fetchall()
            rows = [self._expire_row(connection, row, now_dt=now_dt) for row in rows]
        return [self._approval_from_row(row) for row in rows]

    def respond(
        self,
        *,
        approval_id: str,
        user_id: str,
        phone_device_id: str,
        decision: str,
        decision_reason: str,
        expected_version: int,
        action_digest: str,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]:
        if decision not in {"approve", "deny"}:
            raise ValueError("approval decision invalid")
        allowed_reasons = (
            {"user_approved"}
            if decision == "approve"
            else {"user_denied", "not_recognized", "wrong_target"}
        )
        if decision_reason not in allowed_reasons:
            raise ValueError("approval reason does not match decision")
        current = self.get_for_phone(
            approval_id=approval_id,
            user_id=user_id,
            phone_device_id=phone_device_id,
        )
        if current["status"] == "expired" and current["decision"] is None:
            raise ApprovalExpired("managed_approval_expired")
        now = _format(_now())
        idempotency_hash = hash_secret(idempotency_key)
        approval: dict[str, Any] | None = None
        replay = False
        with self.database.transaction(immediate=True) as connection:
            row = self._phone_row(
                connection,
                approval_id=approval_id,
                user_id=user_id,
                phone_device_id=phone_device_id,
            )
            if row["decision"] in {"approve", "deny"}:
                if (
                    row["decision"] == decision
                    and row["decision_reason"] == decision_reason
                    and row["decision_idempotency_key"] == idempotency_hash
                    and row["action_digest"] == action_digest
                ):
                    approval = self._approval_from_row(row)
                    replay = True
                else:
                    raise ApprovalConflict("managed_approval_already_resolved")
            elif row["status"] != "pending":
                raise ApprovalConflict("managed_approval_not_pending")
            elif row["version"] != expected_version:
                raise ApprovalConflict("managed_approval_version_stale")
            elif not hmac.compare_digest(str(row["action_digest"]), action_digest):
                raise ApprovalConflict("managed_approval_action_modified")
            else:
                next_status = "approved" if decision == "approve" else "denied"
                updated = connection.execute(
                    """
                    UPDATE managed_approvals
                    SET status = ?, decision = ?, decision_reason = ?,
                        decided_by_user_id = ?, decided_by_device_id = ?,
                        decision_idempotency_key = ?, decided_at = ?, updated_at = ?,
                        version = version + 1
                    WHERE id = ? AND status = 'pending' AND version = ?
                      AND expires_at > ?
                    """,
                    (
                        next_status,
                        decision,
                        decision_reason,
                        user_id,
                        phone_device_id,
                        idempotency_hash,
                        now,
                        now,
                        approval_id,
                        expected_version,
                        now,
                    ),
                )
                if updated.rowcount == 1:
                    row = connection.execute(
                        "SELECT * FROM managed_approvals WHERE id = ?",
                        (approval_id,),
                    ).fetchone()
                    approval = self._approval_from_row(row)
                    self.relay_repository.insert_control_event(
                        connection,
                        session_id=row["relay_session_id"],
                        source_event_id=(
                            f"control-plane:{approval_id}:resolved:v{row['version']}"
                        ),
                        event_type="approval.resolved",
                        command_id=None,
                        payload=self._event_payload(approval),
                        created_at=now,
                    )
        if approval is None:
            refreshed = self.get_for_phone(
                approval_id=approval_id,
                user_id=user_id,
                phone_device_id=phone_device_id,
            )
            if refreshed["status"] == "expired":
                raise ApprovalExpired("managed_approval_expired")
            raise ApprovalConflict("managed_approval_race_lost")
        return approval, not replay

    def get_for_worker(
        self,
        *,
        approval_id: str,
        worker_device_id: str,
        job_id: str,
    ) -> dict[str, Any]:
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT * FROM managed_approvals
                WHERE id = ? AND worker_device_id = ? AND job_id = ?
                """,
                (approval_id, worker_device_id, job_id),
            ).fetchone()
            if row is None:
                raise ApprovalNotFound("managed_approval_not_found")
            row = self._expire_row(connection, row, now_dt=_now())
        return self._approval_from_row(row)

    def consume(
        self,
        *,
        approval_id: str,
        worker_device_id: str,
        job_id: str,
        claim_token: str,
        lease_claims: LeaseClaims,
        action: ManagedAction,
        expected_version: int,
        action_digest: str,
    ) -> dict[str, Any]:
        current = self.get_for_worker(
            approval_id=approval_id,
            worker_device_id=worker_device_id,
            job_id=job_id,
        )
        if current["status"] == "expired":
            raise ApprovalExpired("managed_approval_expired")
        now = _format(_now())
        supplied_claim_hash = hash_secret(claim_token)
        context_changed = False
        approval: dict[str, Any] | None = None
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                """
                SELECT * FROM managed_approvals
                WHERE id = ? AND worker_device_id = ? AND job_id = ?
                """,
                (approval_id, worker_device_id, job_id),
            ).fetchone()
            if row is None:
                raise ApprovalNotFound("managed_approval_not_found")
            if row["status"] == "consumed":
                raise ApprovalConflict("managed_approval_already_consumed")
            if row["status"] != "approved":
                raise ApprovalConflict("managed_approval_not_approved")
            if row["version"] != expected_version:
                raise ApprovalConflict("managed_approval_version_stale")
            actual_digest = action.digest()
            if action_digest != actual_digest or not hmac.compare_digest(
                str(row["action_digest"]), actual_digest
            ):
                raise ApprovalConflict("managed_approval_action_modified")
            context_valid = (
                row["lease_nonce"] == lease_claims.nonce
                and row["lease_expires_at"] == lease_claims.expires_at
                and row["tenant_id"] == lease_claims.tenant_id
                and row["store_id"] == lease_claims.store_id
                and row["agent_id"] == lease_claims.agent_id
                and row["worker_device_id"] == lease_claims.device_id
                and row["capability"] in lease_claims.capabilities
            )
            job = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            job_valid = (
                job is not None
                and job["status"] == "running"
                and job["attempt_count"] == row["job_attempt"]
                and job["claimed_by_device_id"] == worker_device_id
                and job["tenant_id"] == row["tenant_id"]
                and job["store_id"] == row["store_id"]
                and job["agent_id"] == row["agent_id"]
                and job["capability"] == row["capability"]
                and job["claim_token_hash"]
                and hmac.compare_digest(
                    str(job["claim_token_hash"]), supplied_claim_hash
                )
            )
            relay = connection.execute(
                """
                SELECT s.id FROM relay_sessions s
                JOIN relay_pairings p ON p.id = s.pairing_id
                JOIN devices phone ON phone.id = s.phone_device_id
                JOIN devices worker ON worker.id = s.worker_device_id
                JOIN agents a ON a.id = s.agent_id
                JOIN memberships m
                  ON m.user_id = p.user_id AND m.tenant_id = s.tenant_id
                JOIN membership_store_grants g
                  ON g.membership_id = m.id AND g.store_id = s.store_id
                JOIN tenants t ON t.id = s.tenant_id
                JOIN stores st ON st.id = s.store_id
                JOIN subscriptions sub ON sub.tenant_id = s.tenant_id
                JOIN entitlements e
                  ON e.tenant_id = s.tenant_id AND e.store_id = s.store_id
                WHERE s.id = ? AND s.status = 'active' AND p.status = 'active'
                  AND s.phone_device_id = ? AND s.worker_device_id = ?
                  AND s.tenant_id = ? AND s.store_id = ? AND s.agent_id = ?
                  AND p.user_id = ?
                  AND phone.status = 'active' AND worker.status = 'active'
                  AND a.status = 'active' AND a.device_id = worker.id
                  AND m.status = 'active' AND m.role IN ('owner', 'operator')
                  AND g.status = 'active'
                  AND t.status = 'active'
                  AND st.status = 'active' AND st.tenant_id = t.id
                  AND sub.status = 'active'
                  AND (sub.current_period_end IS NULL OR sub.current_period_end > ?)
                  AND e.capability = ? AND e.status = 'active'
                  AND (e.expires_at IS NULL OR e.expires_at > ?)
                """,
                (
                    row["relay_session_id"],
                    row["phone_device_id"],
                    worker_device_id,
                    row["tenant_id"],
                    row["store_id"],
                    row["agent_id"],
                    row["user_id"],
                    now,
                    row["capability"],
                    now,
                ),
            ).fetchone()
            if not context_valid or not job_valid or relay is None:
                connection.execute(
                    """
                    UPDATE managed_approvals
                    SET status = 'canceled', decision_reason = 'context_changed',
                        version = version + 1, updated_at = ?
                    WHERE id = ? AND status = 'approved'
                    """,
                    (now, approval_id),
                )
                canceled = connection.execute(
                    "SELECT * FROM managed_approvals WHERE id = ?", (approval_id,)
                ).fetchone()
                approval = self._approval_from_row(canceled)
                self.relay_repository.insert_control_event(
                    connection,
                    session_id=row["relay_session_id"],
                    source_event_id=(
                        f"control-plane:{approval_id}:canceled:v{canceled['version']}"
                    ),
                    event_type="approval.canceled",
                    command_id=None,
                    payload=self._event_payload(approval),
                    created_at=now,
                )
                context_changed = True
            else:
                updated = connection.execute(
                    """
                    UPDATE managed_approvals
                    SET status = 'consumed', consumed_at = ?, updated_at = ?,
                        version = version + 1
                    WHERE id = ? AND status = 'approved' AND version = ?
                      AND expires_at > ?
                    """,
                    (now, now, approval_id, expected_version, now),
                )
                if updated.rowcount == 1:
                    row = connection.execute(
                        "SELECT * FROM managed_approvals WHERE id = ?",
                        (approval_id,),
                    ).fetchone()
                    approval = self._approval_from_row(row)
                    self.relay_repository.insert_control_event(
                        connection,
                        session_id=row["relay_session_id"],
                        source_event_id=(
                            f"control-plane:{approval_id}:consumed:v{row['version']}"
                        ),
                        event_type="approval.consumed",
                        command_id=None,
                        payload=self._event_payload(approval),
                        created_at=now,
                    )
        if context_changed:
            raise ApprovalConflict("managed_approval_context_changed")
        if approval is None:
            refreshed = self.get_for_worker(
                approval_id=approval_id,
                worker_device_id=worker_device_id,
                job_id=job_id,
            )
            if refreshed["status"] == "expired":
                raise ApprovalExpired("managed_approval_expired")
            raise ApprovalConflict("managed_approval_race_lost")
        return approval

    def cancel_device(self, device_id: str) -> int:
        return self._cancel_where(
            "phone_device_id = ? OR worker_device_id = ?",
            (device_id, device_id),
            reason="device_revoked",
        )

    def cancel_job(self, job_id: str, *, reason: str) -> int:
        return self._cancel_where("job_id = ?", (job_id,), reason=reason)

    def cancel_agent(self, agent_id: str) -> int:
        return self._cancel_where(
            "agent_id = ?",
            (agent_id,),
            reason="agent_disabled",
        )

    def cancel_store(self, store_id: str) -> int:
        return self._cancel_where(
            "store_id = ?",
            (store_id,),
            reason="store_disabled",
        )

    def cancel_tenant(self, tenant_id: str) -> int:
        return self._cancel_where(
            "tenant_id = ?",
            (tenant_id,),
            reason="subscription_inactive",
        )

    def cancel_entitlement(
        self,
        *,
        tenant_id: str,
        store_id: str,
        capability: str,
    ) -> int:
        return self._cancel_where(
            "tenant_id = ? AND store_id = ? AND capability = ?",
            (tenant_id, store_id, capability),
            reason="entitlement_disabled",
        )

    def _cancel_where(
        self,
        predicate: str,
        params: tuple[Any, ...],
        *,
        reason: str,
    ) -> int:
        now = _format(_now())
        canceled_count = 0
        with self.database.transaction(immediate=True) as connection:
            rows = connection.execute(
                f"""
                SELECT * FROM managed_approvals
                WHERE ({predicate}) AND status IN ('pending', 'approved')
                """,
                params,
            ).fetchall()
            for row in rows:
                updated = connection.execute(
                    """
                    UPDATE managed_approvals
                    SET status = 'canceled', decision_reason = ?,
                        version = version + 1, updated_at = ?
                    WHERE id = ? AND version = ?
                      AND status IN ('pending', 'approved')
                    """,
                    (reason, now, row["id"], row["version"]),
                )
                if updated.rowcount != 1:
                    continue
                canceled_count += 1
                current = connection.execute(
                    "SELECT * FROM managed_approvals WHERE id = ?", (row["id"],)
                ).fetchone()
                approval = self._approval_from_row(current)
                self.relay_repository.insert_control_event(
                    connection,
                    session_id=current["relay_session_id"],
                    source_event_id=(
                        f"control-plane:{current['id']}:canceled:v{current['version']}"
                    ),
                    event_type="approval.canceled",
                    command_id=None,
                    payload=self._event_payload(approval),
                    created_at=now,
                )
        return canceled_count
