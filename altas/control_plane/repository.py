"""Repository layer for control-plane state and audit history."""

from __future__ import annotations

import hmac
import json
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from altas.cortex.managed_dispatch import CortexDispatchAdmission

from .database import Database
from .redaction import sanitize_for_storage
from .security import hash_secret


DEMO_TENANT_ID = "tenant_demo_fixed_ops"
DEMO_STORE_ID = "store-sunrise-vw"
DEMO_UNENTITLED_STORE_ID = "store-harbor-toyota"
DEMO_SUBSCRIPTION_ID = "subscription_demo_professional"
DEMO_DEVICE_ID = "device_demo_local_worker"
DEMO_AGENT_ID = "agent_demo_atlas"
DEMO_DEVICE_SECRET = "atlas-demo-device-secret-v1"
DEMO_USER_ID = "user_demo_owner"
DEMO_USER_SUBJECT = "atlas-demo-owner"
DEMO_IDENTITY_ISSUER = "https://identity.dev.atlas.invalid"
DEMO_MEMBERSHIP_ID = "membership_demo_owner"

DEMO_CAPABILITIES = (
    "jobs.complete",
    "jobs.poll",
    "model.chat",
    "fixed_ops.daily_report",
    "fixed_ops.synthetic_export",
    "cortex.memory_maintenance",
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(
        sanitize_for_storage(value), sort_keys=True, separators=(",", ":")
    )


def _record(
    row: sqlite3.Row | None,
    *,
    json_columns: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for column in json_columns:
        raw = item.get(column)
        decoded_column = column.removesuffix("_json")
        if raw is None:
            item[decoded_column] = None
        else:
            try:
                item[decoded_column] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                item[decoded_column] = {}
        del item[column]
    return item


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _safe_device(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None:
        return None
    safe = dict(record)
    safe.pop("secret_hash", None)
    safe.pop("public_key_b64", None)
    return safe


@dataclass(frozen=True, slots=True)
class DemoSeed:
    tenant_id: str = DEMO_TENANT_ID
    store_id: str = DEMO_STORE_ID
    unentitled_store_id: str = DEMO_UNENTITLED_STORE_ID
    device_id: str = DEMO_DEVICE_ID
    agent_id: str = DEMO_AGENT_ID
    device_secret: str = DEMO_DEVICE_SECRET


class IdempotencyConflict(ValueError):
    """A key was reused for a materially different job request."""


class ModelUsageLimitExceeded(ValueError):
    """A model request would exceed a persisted per-job allowance."""


class CortexDispatchLimitExceeded(ValueError):
    """A worker attempted to exceed server-owned Cortex dispatch admission."""


class InvalidJobClaim(ValueError):
    """A worker presented a missing, stale, or otherwise invalid claim token."""


class AccountAccessDenied(PermissionError):
    """An account is not allowed to mutate the requested store resource."""


class EnrollmentNotRedeemable(ValueError):
    """An enrollment is absent, expired, revoked, or already consumed."""


class DeviceCredentialConflict(ValueError):
    """Device-bound key material conflicts with current persisted state."""


class ControlPlaneRepository:
    """All database access for the local prototype.

    Callers receive ordinary dictionaries so records can move directly through
    FastAPI's encoder, while SQL and JSON decoding remain centralized here.
    """

    _LISTABLE_TABLES = {
        "agents": (),
        "audit_logs": ("details_json",),
        "devices": ("metadata_json",),
        "entitlements": ("config_json",),
        "jobs": ("payload_json", "result_json"),
        "stores": (),
        "subscriptions": (),
        "tenants": (),
        "usage_events": (),
    }

    def __init__(self, database: Database) -> None:
        self.database = database

    def seed_demo(self) -> DemoSeed:
        """Idempotently create a deterministic, usable single-store tenant."""

        timestamp = "2026-01-01T00:00:00Z"
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO tenants
                    (id, name, slug, status, created_at, updated_at)
                VALUES (?, ?, ?, 'active', ?, ?)
                """,
                (
                    DEMO_TENANT_ID,
                    "Atlas Demo Dealer Group",
                    "atlas-demo",
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO users
                    (id, issuer, subject, email, display_name, status,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    DEMO_USER_ID,
                    DEMO_IDENTITY_ISSUER,
                    DEMO_USER_SUBJECT,
                    "owner@atlas.invalid",
                    "Atlas Demo Owner",
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO stores
                    (id, tenant_id, name, external_ref, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    DEMO_STORE_ID,
                    DEMO_TENANT_ID,
                    "Sunrise Volkswagen",
                    "TEKION-SUNRISE-VW",
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO memberships
                    (id, user_id, tenant_id, role, status, created_at, updated_at)
                VALUES (?, ?, ?, 'owner', 'active', ?, ?)
                """,
                (
                    DEMO_MEMBERSHIP_ID,
                    DEMO_USER_ID,
                    DEMO_TENANT_ID,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO membership_store_grants
                    (membership_id, store_id, status, created_at, updated_at)
                VALUES (?, ?, 'active', ?, ?)
                """,
                (
                    DEMO_MEMBERSHIP_ID,
                    DEMO_STORE_ID,
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO stores
                    (id, tenant_id, name, external_ref, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    DEMO_UNENTITLED_STORE_ID,
                    DEMO_TENANT_ID,
                    "Harbor Toyota",
                    "TEKION-HARBOR-TOYOTA",
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO subscriptions
                    (id, tenant_id, plan, status, current_period_end, created_at, updated_at)
                VALUES (?, ?, 'professional', 'active', ?, ?, ?)
                """,
                (
                    DEMO_SUBSCRIPTION_ID,
                    DEMO_TENANT_ID,
                    "2099-12-31T23:59:59Z",
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO devices
                    (id, tenant_id, store_id, name, secret_hash, status,
                     metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'active', '{}', ?, ?)
                """,
                (
                    DEMO_DEVICE_ID,
                    DEMO_TENANT_ID,
                    DEMO_STORE_ID,
                    "Demo managed worker",
                    hash_secret(DEMO_DEVICE_SECRET),
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO agents
                    (id, tenant_id, store_id, device_id, name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'Atlas Fixed Ops', 'active', ?, ?)
                """,
                (
                    DEMO_AGENT_ID,
                    DEMO_TENANT_ID,
                    DEMO_STORE_ID,
                    DEMO_DEVICE_ID,
                    timestamp,
                    timestamp,
                ),
            )
            for capability in DEMO_CAPABILITIES:
                entitlement_id = f"entitlement_demo_{capability.replace('.', '_')}"
                connection.execute(
                    """
                    INSERT OR IGNORE INTO entitlements
                        (id, tenant_id, store_id, capability, status, config_json,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'active', '{}', ?, ?)
                    """,
                    (
                        entitlement_id,
                        DEMO_TENANT_ID,
                        DEMO_STORE_ID,
                        capability,
                        timestamp,
                        timestamp,
                    ),
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO jobs
                    (id, tenant_id, store_id, agent_id, capability, status,
                     payload_json, requested_by, idempotency_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'queued', ?, 'demo-seed', ?, ?, ?)
                """,
                (
                    "job_demo_daily_report",
                    DEMO_TENANT_ID,
                    DEMO_STORE_ID,
                    DEMO_AGENT_ID,
                    "fixed_ops.daily_report",
                    _json({"report_date": "2026-07-08", "source": "sample"}),
                    "demo-daily-report-v1",
                    timestamp,
                    timestamp,
                ),
            )
        return DemoSeed()

    def authenticate_device(self, secret: str) -> dict[str, Any] | None:
        supplied_hash = hash_secret(secret)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM devices WHERE secret_hash = ? "
                "AND credential_kind = 'legacy_bearer'",
                (supplied_hash,),
            ).fetchone()
        device = _record(row, json_columns=("metadata_json",))
        if not device or not hmac.compare_digest(device["secret_hash"], supplied_hash):
            return None
        # The hash proves authentication but is never useful to API consumers.
        del device["secret_hash"]
        device.pop("public_key_b64", None)
        return device

    def get_user_by_identity(
        self, *, issuer: str, subject: str
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE issuer = ? AND subject = ?",
                (issuer, subject),
            ).fetchone()
        return _record(row)

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return _record(row)

    def list_account_memberships(self, user_id: str) -> list[dict[str, Any]]:
        """Return only live memberships and explicitly granted live stores."""

        with self.database.connect() as connection:
            memberships = connection.execute(
                """
                SELECT m.id, m.tenant_id, m.role, t.name AS tenant_name,
                       t.slug AS tenant_slug
                FROM memberships m
                JOIN users u ON u.id = m.user_id
                JOIN tenants t ON t.id = m.tenant_id
                WHERE m.user_id = ? AND u.status = 'active'
                  AND m.status = 'active' AND t.status = 'active'
                ORDER BY t.name, m.id
                """,
                (user_id,),
            ).fetchall()
            output: list[dict[str, Any]] = []
            for membership in memberships:
                stores = connection.execute(
                    """
                    SELECT s.id, s.name
                    FROM membership_store_grants g
                    JOIN stores s ON s.id = g.store_id
                    WHERE g.membership_id = ? AND g.status = 'active'
                      AND s.status = 'active' AND s.tenant_id = ?
                    ORDER BY s.name, s.id
                    """,
                    (membership["id"], membership["tenant_id"]),
                ).fetchall()
                output.append({
                    "id": membership["id"],
                    "role": membership["role"],
                    "tenant": {
                        "id": membership["tenant_id"],
                        "name": membership["tenant_name"],
                        "slug": membership["tenant_slug"],
                    },
                    "stores": [dict(store) for store in stores],
                })
        return output

    @staticmethod
    def _account_store_access(
        connection: sqlite3.Connection,
        *,
        user_id: str,
        store_id: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT m.id AS membership_id, m.tenant_id, m.role,
                   s.id AS store_id
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

    def create_device_enrollment(
        self,
        *,
        user_id: str,
        store_id: str,
        device_class: str,
        device_name: str,
        agent_id: str | None,
        ttl_seconds: int,
    ) -> tuple[dict[str, Any], str]:
        """Create one account-authorized, short-lived redemption transaction."""

        now_datetime = datetime.now(UTC)
        now = _format_utc(now_datetime)
        expires_at = _format_utc(now_datetime + timedelta(seconds=ttl_seconds))
        enrollment_id = f"enrollment_{uuid.uuid4().hex}"
        correlation_id = f"correlation_{uuid.uuid4().hex}"
        enrollment_token = secrets.token_urlsafe(32)
        token_hash = hash_secret(enrollment_token)
        with self.database.transaction(immediate=True) as connection:
            access = self._account_store_access(
                connection, user_id=user_id, store_id=store_id
            )
            if access is None:
                raise AccountAccessDenied("store_access_denied")
            if device_class == "worker" and access["role"] not in {
                "owner",
                "operator",
            }:
                raise AccountAccessDenied("role_not_allowed")
            if device_class == "worker":
                agent = connection.execute(
                    """
                    SELECT id FROM agents
                    WHERE id = ? AND tenant_id = ? AND store_id = ?
                      AND status = 'active' AND device_id IS NULL
                    """,
                    (agent_id, access["tenant_id"], store_id),
                ).fetchone()
                if agent is None:
                    raise AccountAccessDenied("agent_not_enrollable")
            elif agent_id is not None:
                raise AccountAccessDenied("agent_not_allowed")
            connection.execute(
                """
                INSERT INTO device_enrollments
                    (id, token_hash, correlation_id, created_by_user_id,
                     membership_id, tenant_id, store_id, agent_id, device_class,
                     device_name, status, expires_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    enrollment_id,
                    token_hash,
                    correlation_id,
                    user_id,
                    access["membership_id"],
                    access["tenant_id"],
                    store_id,
                    agent_id,
                    device_class,
                    device_name.strip(),
                    expires_at,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM device_enrollments WHERE id = ?",
                (enrollment_id,),
            ).fetchone()
        enrollment = _record(row)
        if enrollment is None:
            raise RuntimeError("created enrollment disappeared")
        enrollment.pop("token_hash", None)
        return enrollment, enrollment_token

    def redeem_device_enrollment(
        self,
        *,
        enrollment_token: str,
        public_key_b64: str,
        public_key_thumbprint: str,
        platform: str,
        platform_version: str | None,
        app_version: str | None,
        credential_ttl_seconds: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Atomically consume an enrollment and bind one Ed25519 device."""

        supplied_hash = hash_secret(enrollment_token)
        now_datetime = datetime.now(UTC)
        now = _format_utc(now_datetime)
        credential_expires_at = _format_utc(
            now_datetime + timedelta(seconds=credential_ttl_seconds)
        )
        device_id = f"device_{uuid.uuid4().hex}"
        # Older SQLite files retain the original NOT NULL legacy-secret
        # column.  This random preimage is immediately discarded, and
        # credential_kind prevents the digest from authenticating anything.
        unreachable_secret_hash = hash_secret(secrets.token_urlsafe(48))
        metadata = {
            "app_version": app_version,
            "platform": platform,
            "platform_version": platform_version,
        }
        try:
            with self.database.transaction(immediate=True) as connection:
                row = connection.execute(
                    "SELECT * FROM device_enrollments WHERE token_hash = ?",
                    (supplied_hash,),
                ).fetchone()
                if row is None or not hmac.compare_digest(
                    str(row["token_hash"]), supplied_hash
                ):
                    raise EnrollmentNotRedeemable("enrollment_not_redeemable")
                expires_at = _parse_utc(str(row["expires_at"]))
                if (
                    row["status"] != "pending"
                    or expires_at is None
                    or expires_at <= now_datetime
                ):
                    raise EnrollmentNotRedeemable("enrollment_not_redeemable")
                access = self._account_store_access(
                    connection,
                    user_id=str(row["created_by_user_id"]),
                    store_id=str(row["store_id"]),
                )
                if (
                    access is None
                    or access["membership_id"] != row["membership_id"]
                    or access["tenant_id"] != row["tenant_id"]
                ):
                    raise EnrollmentNotRedeemable("enrollment_not_redeemable")
                if row["device_class"] == "worker":
                    if access["role"] not in {"owner", "operator"}:
                        raise EnrollmentNotRedeemable("enrollment_not_redeemable")
                    agent = connection.execute(
                        """
                        SELECT id FROM agents
                        WHERE id = ? AND tenant_id = ? AND store_id = ?
                          AND status = 'active' AND device_id IS NULL
                        """,
                        (row["agent_id"], row["tenant_id"], row["store_id"]),
                    ).fetchone()
                    if agent is None:
                        raise EnrollmentNotRedeemable("enrollment_not_redeemable")
                connection.execute(
                    """
                    INSERT INTO devices
                        (id, tenant_id, store_id, name, secret_hash, device_class,
                         credential_kind, public_key_b64, public_key_thumbprint,
                         credential_version, credential_expires_at,
                         enrolled_by_user_id, status, metadata_json,
                         created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'ed25519', ?, ?, 1, ?, ?,
                            'active', ?, ?, ?)
                    """,
                    (
                        device_id,
                        row["tenant_id"],
                        row["store_id"],
                        row["device_name"],
                        unreachable_secret_hash,
                        row["device_class"],
                        public_key_b64,
                        public_key_thumbprint,
                        credential_expires_at,
                        row["created_by_user_id"],
                        _json(metadata),
                        now,
                        now,
                    ),
                )
                if row["device_class"] == "worker":
                    bound = connection.execute(
                        "UPDATE agents SET device_id = ?, updated_at = ? "
                        "WHERE id = ? AND device_id IS NULL",
                        (device_id, now, row["agent_id"]),
                    )
                    if bound.rowcount != 1:
                        raise EnrollmentNotRedeemable("enrollment_not_redeemable")
                redeemed = connection.execute(
                    """
                    UPDATE device_enrollments
                    SET status = 'redeemed', redeemed_device_id = ?,
                        redeemed_at = ?, updated_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (device_id, now, now, row["id"]),
                )
                if redeemed.rowcount != 1:
                    raise EnrollmentNotRedeemable("enrollment_not_redeemable")
                device_row = connection.execute(
                    "SELECT * FROM devices WHERE id = ?", (device_id,)
                ).fetchone()
                enrollment_row = connection.execute(
                    "SELECT * FROM device_enrollments WHERE id = ?", (row["id"],)
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise DeviceCredentialConflict("device_key_already_registered") from exc
        device = _record(device_row, json_columns=("metadata_json",))
        enrollment = _record(enrollment_row)
        safe_device = _safe_device(device)
        if safe_device is None or enrollment is None:
            raise RuntimeError("redeemed device disappeared")
        enrollment.pop("token_hash", None)
        return safe_device, enrollment

    def get_device_credential(self, device_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM devices WHERE id = ?", (device_id,)
            ).fetchone()
        return _record(row, json_columns=("metadata_json",))

    def authenticate_device_session(
        self, *, device_id: str, credential_version: int
    ) -> dict[str, Any] | None:
        now = datetime.now(UTC)
        device = self.get_device_credential(device_id)
        credential_expiry = _parse_utc(
            str(device.get("credential_expires_at") or "") if device else None
        )
        if (
            not device
            or device.get("status") != "active"
            or device.get("credential_kind") != "ed25519"
            or device.get("credential_revoked_at") is not None
            or device.get("credential_version") != credential_version
            or not device.get("public_key_b64")
            or credential_expiry is None
            or credential_expiry <= now
        ):
            return None
        return _safe_device(device)

    def consume_device_proof_nonce(
        self,
        *,
        device_id: str,
        credential_version: int,
        public_key_thumbprint: str,
        nonce: str,
        purpose: str,
        nonce_expires_at: str,
    ) -> bool:
        """Persist proof freshness only if the live device key is unchanged."""

        now = utc_now()
        try:
            with self.database.transaction(immediate=True) as connection:
                device = connection.execute(
                    """
                    SELECT status, credential_kind, credential_version,
                           public_key_thumbprint, credential_expires_at,
                           credential_revoked_at
                    FROM devices WHERE id = ?
                    """,
                    (device_id,),
                ).fetchone()
                credential_expiry = _parse_utc(
                    str(device["credential_expires_at"] or "") if device else None
                )
                if (
                    device is None
                    or device["status"] != "active"
                    or device["credential_kind"] != "ed25519"
                    or device["credential_version"] != credential_version
                    or device["public_key_thumbprint"] != public_key_thumbprint
                    or device["credential_revoked_at"] is not None
                    or credential_expiry is None
                    or credential_expiry <= datetime.now(UTC)
                ):
                    return False
                connection.execute(
                    "DELETE FROM device_proof_nonces WHERE expires_at <= ?", (now,)
                )
                connection.execute(
                    """
                    INSERT INTO device_proof_nonces
                        (device_id, nonce, purpose, used_at, expires_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (device_id, nonce, purpose, now, nonce_expires_at),
                )
        except sqlite3.IntegrityError:
            return False
        return True

    def rotate_device_credential(
        self,
        *,
        device_id: str,
        expected_version: int,
        new_public_key_b64: str,
        new_public_key_thumbprint: str,
        nonce: str,
        nonce_expires_at: str,
        credential_expires_at: str,
    ) -> dict[str, Any]:
        """Atomically consume a proof and replace the device public key."""

        now = utc_now()
        try:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    "DELETE FROM device_proof_nonces WHERE expires_at <= ?", (now,)
                )
                connection.execute(
                    """
                    INSERT INTO device_proof_nonces
                        (device_id, nonce, purpose, used_at, expires_at)
                    VALUES (?, ?, 'key_rotation', ?, ?)
                    """,
                    (device_id, nonce, now, nonce_expires_at),
                )
                updated = connection.execute(
                    """
                    UPDATE devices
                    SET public_key_b64 = ?, public_key_thumbprint = ?,
                        credential_version = credential_version + 1,
                        credential_expires_at = ?, credential_revoked_at = NULL,
                        updated_at = ?
                    WHERE id = ? AND status = 'active'
                      AND credential_kind = 'ed25519'
                      AND credential_revoked_at IS NULL
                      AND credential_version = ?
                    """,
                    (
                        new_public_key_b64,
                        new_public_key_thumbprint,
                        credential_expires_at,
                        now,
                        device_id,
                        expected_version,
                    ),
                )
                if updated.rowcount != 1:
                    raise DeviceCredentialConflict("device_credential_stale")
                row = connection.execute(
                    "SELECT * FROM devices WHERE id = ?", (device_id,)
                ).fetchone()
        except sqlite3.IntegrityError as exc:
            raise DeviceCredentialConflict("device_credential_conflict") from exc
        device = _record(row, json_columns=("metadata_json",))
        safe = _safe_device(device)
        if safe is None:
            raise RuntimeError("rotated device disappeared")
        return safe

    def list_account_devices(
        self, *, user_id: str, store_id: str
    ) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            access = self._account_store_access(
                connection, user_id=user_id, store_id=store_id
            )
            if access is None:
                raise AccountAccessDenied("store_access_denied")
            rows = connection.execute(
                "SELECT * FROM devices WHERE tenant_id = ? AND store_id = ? "
                "ORDER BY created_at DESC, id",
                (access["tenant_id"], store_id),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            record = _safe_device(_record(row, json_columns=("metadata_json",)))
            if record is not None:
                output.append(record)
        return output

    def revoke_account_device(self, *, user_id: str, device_id: str) -> dict[str, Any]:
        now = utc_now()
        terminated_job_count = 0
        with self.database.transaction(immediate=True) as connection:
            device = connection.execute(
                "SELECT * FROM devices WHERE id = ?", (device_id,)
            ).fetchone()
            if device is None:
                raise AccountAccessDenied("device_not_found")
            access = self._account_store_access(
                connection,
                user_id=user_id,
                store_id=str(device["store_id"]),
            )
            if (
                access is None
                or access["tenant_id"] != device["tenant_id"]
                or access["role"] not in {"owner", "operator"}
            ):
                raise AccountAccessDenied("device_not_found")
            if device["status"] == "active":
                connection.execute(
                    """
                    UPDATE devices
                    SET status = 'disabled', credential_revoked_at = ?,
                        credential_version = credential_version + 1,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, device_id),
                )
                terminated = connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'canceled', claimed_by_device_id = NULL,
                        claim_token_hash = NULL, result_json = NULL,
                        error = 'device_disabled', completed_at = ?, updated_at = ?
                    WHERE status IN ('queued', 'running')
                      AND (
                        device_id = ? OR claimed_by_device_id = ? OR
                        agent_id IN (
                            SELECT id FROM agents WHERE device_id = ?
                        )
                      )
                    """,
                    (now, now, device_id, device_id, device_id),
                )
                terminated_job_count = terminated.rowcount
                connection.execute(
                    "UPDATE agents SET device_id = NULL, updated_at = ? "
                    "WHERE device_id = ?",
                    (now, device_id),
                )
            row = connection.execute(
                "SELECT * FROM devices WHERE id = ?", (device_id,)
            ).fetchone()
        record = _safe_device(_record(row, json_columns=("metadata_json",)))
        if record is None:
            raise RuntimeError("revoked device disappeared")
        record["terminated_job_count"] = terminated_job_count
        return record

    def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        return self._get("tenants", tenant_id)

    def get_store(self, store_id: str) -> dict[str, Any] | None:
        return self._get("stores", store_id)

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        record = self._get("devices", device_id, json_columns=("metadata_json",))
        return _safe_device(record)

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        return self._get("agents", agent_id)

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        job = self._get("jobs", job_id, json_columns=("payload_json", "result_json"))
        if job:
            job.pop("claim_token_hash", None)
        return job

    def validate_job_claim(
        self,
        job_id: str,
        *,
        device_id: str,
        claim_token: str,
    ) -> bool:
        """Validate one active claim attempt without exposing its stored hash."""

        supplied_hash = hash_secret(claim_token)
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT claim_token_hash FROM jobs
                WHERE id = ? AND status = 'running'
                  AND claimed_by_device_id = ?
                """,
                (job_id, device_id),
            ).fetchone()
        expected_hash = row["claim_token_hash"] if row else None
        return bool(
            expected_hash and hmac.compare_digest(str(expected_hash), supplied_hash)
        )

    def get_active_subscription(self, tenant_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM subscriptions
                WHERE tenant_id = ? AND status IN ('active', 'trialing')
                ORDER BY created_at DESC LIMIT 1
                """,
                (tenant_id,),
            ).fetchone()
        return _record(row)

    def get_entitlement(
        self, tenant_id: str, store_id: str, capability: str
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM entitlements
                WHERE tenant_id = ? AND store_id = ? AND capability = ?
                LIMIT 1
                """,
                (tenant_id, store_id, capability),
            ).fetchone()
        return _record(row, json_columns=("config_json",))

    def list_active_capabilities(self, tenant_id: str, store_id: str) -> list[str]:
        now = utc_now()
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT capability FROM entitlements
                WHERE tenant_id = ? AND store_id = ? AND status = 'active'
                  AND (expires_at IS NULL OR expires_at > ?)
                ORDER BY capability
                """,
                (tenant_id, store_id, now),
            ).fetchall()
        return [str(row["capability"]) for row in rows]

    def update_heartbeat(
        self,
        device_id: str,
        *,
        worker_version: str,
        health_status: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE devices
                SET worker_version = ?, health_status = ?, metadata_json = ?,
                    last_heartbeat_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (worker_version, health_status, _json(metadata), now, now, device_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(device_id)
        device = self.get_device(device_id)
        if device is None:  # Defensive: the row was present inside the transaction.
            raise KeyError(device_id)
        return device

    def list_records(
        self,
        table: str,
        *,
        tenant_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if table not in self._LISTABLE_TABLES:
            raise ValueError(f"unsupported table: {table}")
        bounded_limit = max(1, min(limit, 500))
        order_column = "created_at"
        query = f"SELECT * FROM {table}"  # table is selected from a fixed allowlist.
        parameters: list[Any] = []
        if tenant_id:
            query += " WHERE id = ?" if table == "tenants" else " WHERE tenant_id = ?"
            parameters.append(tenant_id)
        query += f" ORDER BY {order_column} DESC LIMIT ?"
        parameters.append(bounded_limit)
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        records = [
            _record(row, json_columns=self._LISTABLE_TABLES[table]) for row in rows
        ]
        output = [record for record in records if record is not None]
        if table == "devices":
            for item in output:
                item.pop("secret_hash", None)
                item.pop("public_key_b64", None)
        elif table == "jobs":
            for item in output:
                item.pop("claim_token_hash", None)
        return output

    def overview(self) -> dict[str, Any]:
        with self.database.connect() as connection:
            scalar_queries = {
                "tenants": "SELECT COUNT(*) FROM tenants",
                "stores": "SELECT COUNT(*) FROM stores WHERE status = 'active'",
                "devices": "SELECT COUNT(*) FROM devices WHERE status = 'active'",
                "agents": "SELECT COUNT(*) FROM agents WHERE status = 'active'",
                "queued_jobs": "SELECT COUNT(*) FROM jobs WHERE status = 'queued'",
                "running_jobs": "SELECT COUNT(*) FROM jobs WHERE status = 'running'",
                "failed_jobs": "SELECT COUNT(*) FROM jobs WHERE status = 'failed'",
                "usage_tokens": "SELECT COALESCE(SUM(total_tokens), 0) FROM usage_events",
                "usage_cost_micros": "SELECT COALESCE(SUM(cost_micros), 0) FROM usage_events",
            }
            result = {
                name: int(connection.execute(query).fetchone()[0])
                for name, query in scalar_queries.items()
            }
        return result

    def queue_job(
        self,
        *,
        tenant_id: str,
        store_id: str,
        agent_id: str,
        device_id: str | None,
        capability: str,
        payload: dict[str, Any],
        requested_by: str,
        idempotency_key: str | None,
    ) -> tuple[dict[str, Any], bool]:
        if capability == "cortex.memory_maintenance":
            raise ValueError("cortex_dispatch_requires_dedicated_admission")
        now = utc_now()
        job_id = f"job_{uuid.uuid4().hex}"
        with self.database.transaction(immediate=True) as connection:
            if idempotency_key:
                existing = connection.execute(
                    """
                    SELECT * FROM jobs
                    WHERE tenant_id = ? AND idempotency_key = ?
                    """,
                    (tenant_id, idempotency_key),
                ).fetchone()
                if existing:
                    item = _record(
                        existing, json_columns=("payload_json", "result_json")
                    )
                    if item is None:
                        raise RuntimeError("failed to decode existing job")
                    expected = {
                        "store_id": store_id,
                        "agent_id": agent_id,
                        "device_id": device_id,
                        "capability": capability,
                        "payload": sanitize_for_storage(payload),
                    }
                    actual = {key: item.get(key) for key in expected}
                    if actual != expected:
                        raise IdempotencyConflict("idempotency_key_conflict")
                    item.pop("claim_token_hash", None)
                    return item, False
            connection.execute(
                """
                INSERT INTO jobs
                    (id, tenant_id, store_id, agent_id, device_id, capability,
                     status, payload_json, requested_by, idempotency_key,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    tenant_id,
                    store_id,
                    agent_id,
                    device_id,
                    capability,
                    _json(payload),
                    requested_by,
                    idempotency_key,
                    now,
                    now,
                ),
            )
        job = self.get_job(job_id)
        if job is None:
            raise RuntimeError("queued job disappeared")
        return job, True

    def ensure_cortex_maintenance_job(
        self,
        *,
        tenant_id: str,
        store_id: str,
        agent_id: str,
        device_id: str,
        dispatch_key: str,
        dispatch_admission: dict[str, Any],
        max_jobs_per_24h: int,
    ) -> tuple[dict[str, Any], bool, bool]:
        """Atomically admit one bounded, idempotent Cortex maintenance job.

        The caller's opaque digest provides retry identity only; it does not
        authorize arbitrary spend. The server permits one active Cortex job
        for the bound device/profile and caps all admissions, including
        terminal-job requeues, in a rolling day.
        """

        admission = CortexDispatchAdmission.from_mapping(dispatch_admission)
        if not admission.is_canonical() or not hmac.compare_digest(
            admission.canonical_dispatch_key(), dispatch_key
        ):
            raise ValueError("cortex_dispatch_commitment_mismatch")
        cortex_payload = {"dispatch_admission": admission.to_mapping()}
        idempotency_key = f"cortex-maintenance:{dispatch_key}"
        requested_by = f"worker:{device_id}"
        job_id = f"job_{uuid.uuid4().hex}"
        with self.database.transaction(immediate=True) as connection:
            now_datetime = datetime.now(UTC)
            now = now_datetime.isoformat(timespec="seconds").replace("+00:00", "Z")
            window_start = (
                (now_datetime - timedelta(hours=24))
                .isoformat(timespec="seconds")
                .replace("+00:00", "Z")
            )

            def enforce_rolling_admission_limit() -> None:
                recent_count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM cortex_dispatch_admissions
                        WHERE tenant_id=? AND store_id=? AND agent_id=? AND device_id=?
                          AND admitted_at>=?
                        """,
                        (tenant_id, store_id, agent_id, device_id, window_start),
                    ).fetchone()[0]
                )
                if recent_count >= max_jobs_per_24h:
                    raise CortexDispatchLimitExceeded(
                        "cortex_dispatch_daily_limit_exceeded"
                    )

            def record_admission(*, admitted_job_id: str, admission_kind: str) -> None:
                admission_id = (
                    f"cortex-create:{admitted_job_id}"
                    if admission_kind == "created"
                    else f"cortex-requeue:{uuid.uuid4().hex}"
                )
                connection.execute(
                    """
                    INSERT INTO cortex_dispatch_admissions
                        (id, tenant_id, store_id, agent_id, device_id, job_id,
                         admission_kind, admitted_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        admission_id,
                        tenant_id,
                        store_id,
                        agent_id,
                        device_id,
                        admitted_job_id,
                        admission_kind,
                        now,
                    ),
                )

            existing = connection.execute(
                """
                SELECT * FROM jobs
                WHERE tenant_id = ? AND idempotency_key = ?
                """,
                (tenant_id, idempotency_key),
            ).fetchone()
            if existing:
                item = _record(existing, json_columns=("payload_json", "result_json"))
                if item is None:
                    raise RuntimeError("failed to decode existing Cortex job")
                expected = {
                    "store_id": store_id,
                    "agent_id": agent_id,
                    "device_id": device_id,
                    "capability": "cortex.memory_maintenance",
                    "payload": cortex_payload,
                    "requested_by": requested_by,
                }
                actual = {key: item.get(key) for key in expected}
                if actual != expected:
                    raise IdempotencyConflict("idempotency_key_conflict")
                requeued = False
                if item["status"] in {"failed", "canceled"}:
                    other_active = connection.execute(
                        """
                        SELECT id FROM jobs
                        WHERE tenant_id=? AND store_id=? AND agent_id=? AND device_id=?
                          AND capability='cortex.memory_maintenance'
                          AND status IN ('queued','running') AND id!=?
                        LIMIT 1
                        """,
                        (tenant_id, store_id, agent_id, device_id, item["id"]),
                    ).fetchone()
                    if other_active:
                        raise CortexDispatchLimitExceeded(
                            "cortex_dispatch_already_active"
                        )
                    enforce_rolling_admission_limit()
                    connection.execute(
                        """
                        UPDATE jobs
                        SET status='queued', claimed_by_device_id=NULL,
                            claim_token_hash=NULL, result_json=NULL, error=NULL,
                            started_at=NULL, completed_at=NULL, updated_at=?
                        WHERE id=? AND status IN ('failed','canceled')
                        """,
                        (now, item["id"]),
                    )
                    record_admission(
                        admitted_job_id=str(item["id"]),
                        admission_kind="requeued",
                    )
                    updated = connection.execute(
                        "SELECT * FROM jobs WHERE id=?", (item["id"],)
                    ).fetchone()
                    item = _record(
                        updated, json_columns=("payload_json", "result_json")
                    )
                    if item is None:
                        raise RuntimeError("requeued Cortex job disappeared")
                    requeued = True
                item.pop("claim_token_hash", None)
                return item, False, requeued

            active = connection.execute(
                """
                SELECT id FROM jobs
                WHERE tenant_id=? AND store_id=? AND agent_id=? AND device_id=?
                  AND capability='cortex.memory_maintenance'
                  AND status IN ('queued', 'running')
                LIMIT 1
                """,
                (tenant_id, store_id, agent_id, device_id),
            ).fetchone()
            if active:
                raise CortexDispatchLimitExceeded("cortex_dispatch_already_active")

            enforce_rolling_admission_limit()

            connection.execute(
                """
                INSERT INTO jobs
                    (id, tenant_id, store_id, agent_id, device_id, capability,
                     status, payload_json, requested_by, idempotency_key,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'cortex.memory_maintenance', 'queued',
                        ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    tenant_id,
                    store_id,
                    agent_id,
                    device_id,
                    _json(cortex_payload),
                    requested_by,
                    idempotency_key,
                    now,
                    now,
                ),
            )
            record_admission(admitted_job_id=job_id, admission_kind="created")
        job = self.get_job(job_id)
        if job is None:
            raise RuntimeError("queued Cortex job disappeared")
        return job, True, False

    def claim_next_job(
        self,
        *,
        tenant_id: str,
        store_id: str,
        agent_id: str,
        device_id: str,
        allowed_capabilities: tuple[str, ...],
        visibility_timeout_seconds: int,
    ) -> dict[str, Any] | None:
        if not allowed_capabilities:
            return None
        placeholders = ",".join("?" for _ in allowed_capabilities)
        now_datetime = datetime.now(UTC)
        now = now_datetime.isoformat(timespec="seconds").replace("+00:00", "Z")
        stale_before = now_datetime - timedelta(seconds=visibility_timeout_seconds)
        stale_before_text = stale_before.isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
        claim_token = secrets.token_urlsafe(32)
        claim_token_hash = hash_secret(claim_token)
        with self.database.transaction(immediate=True) as connection:
            # Polling acts as the visibility-timeout recovery point. Clearing
            # the old token before any new claim means a late completion from
            # the prior attempt can never terminate the re-dispatched job.
            connection.execute(
                """
                UPDATE jobs
                SET status = 'queued', claimed_by_device_id = NULL,
                    claim_token_hash = NULL, started_at = NULL, updated_at = ?
                WHERE status = 'running' AND claimed_by_device_id = ?
                  AND started_at IS NOT NULL AND started_at <= ?
                """,
                (now, device_id, stale_before_text),
            )
            existing_claim = connection.execute(
                """
                SELECT id FROM jobs
                WHERE status = 'running' AND claimed_by_device_id = ?
                LIMIT 1
                """,
                (device_id,),
            ).fetchone()
            if existing_claim is not None:
                return None
            row = connection.execute(
                f"""
                SELECT * FROM jobs
                WHERE tenant_id = ? AND store_id = ? AND agent_id = ?
                  AND status = 'queued'
                  AND (device_id IS NULL OR device_id = ?)
                  AND capability IN ({placeholders})
                ORDER BY created_at ASC, id ASC
                LIMIT 1
                """,
                (tenant_id, store_id, agent_id, device_id, *allowed_capabilities),
            ).fetchone()
            if row is None:
                return None
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'running', claimed_by_device_id = ?,
                    claim_token_hash = ?, attempt_count = attempt_count + 1,
                    started_at = ?, updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (device_id, claim_token_hash, now, now, row["id"]),
            )
            if cursor.rowcount != 1:
                return None
            updated = connection.execute(
                "SELECT * FROM jobs WHERE id = ?", (row["id"],)
            ).fetchone()
        job = _record(updated, json_columns=("payload_json", "result_json"))
        if job is None:
            return None
        job.pop("claim_token_hash", None)
        job["claim_token"] = claim_token
        return job

    def complete_job(
        self,
        job_id: str,
        *,
        device_id: str,
        claim_token: str,
        status: str,
        result: dict[str, Any] | None,
        error: str | None,
    ) -> dict[str, Any]:
        if status not in {"succeeded", "failed"}:
            raise ValueError("terminal job status must be succeeded or failed")
        now = utc_now()
        claim_token_hash = hash_secret(claim_token)
        safe_error = sanitize_for_storage(error) if error is not None else None
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = ?, result_json = ?, error = ?, claim_token_hash = NULL,
                    completed_at = ?, updated_at = ?
                WHERE id = ? AND status = 'running'
                  AND claimed_by_device_id = ? AND claim_token_hash = ?
                """,
                (
                    status,
                    _json(result) if result is not None else None,
                    safe_error,
                    now,
                    now,
                    job_id,
                    device_id,
                    claim_token_hash,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("job_not_running")
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def release_job(self, job_id: str, *, device_id: str, claim_token: str) -> None:
        """Return a just-claimed job to the queue after a policy race."""

        now = utc_now()
        claim_token_hash = hash_secret(claim_token)
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = 'queued', claimed_by_device_id = NULL,
                    claim_token_hash = NULL, started_at = NULL, updated_at = ?
                WHERE id = ? AND status = 'running' AND claimed_by_device_id = ?
                  AND claim_token_hash = ?
                """,
                (now, job_id, device_id, claim_token_hash),
            )

    def release_claimed_job(self, job_id: str, *, device_id: str) -> bool:
        """Release a device-owned claim after a post-claim policy denial.

        This variant is intentionally server-only: ownership is established by
        authenticated device context, while the claim token remains confined to
        the worker completion contract.
        """

        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'queued', claimed_by_device_id = NULL,
                    claim_token_hash = NULL, started_at = NULL, updated_at = ?
                WHERE id = ? AND status = 'running'
                  AND claimed_by_device_id = ?
                """,
                (now, job_id, device_id),
            )
        return cursor.rowcount == 1

    def requeue_job(self, job_id: str) -> dict[str, Any]:
        """Explicitly recover a stranded job while invalidating its old claim."""

        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT capability FROM jobs WHERE id=?", (job_id,)
            ).fetchone()
            if existing is not None and str(existing["capability"]) == (
                "cortex.memory_maintenance"
            ):
                raise ValueError("cortex_dispatch_requires_dedicated_admission")
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'queued', claimed_by_device_id = NULL,
                    claim_token_hash = NULL, result_json = NULL, error = NULL,
                    started_at = NULL, completed_at = NULL, updated_at = ?
                WHERE id = ? AND status IN ('running', 'failed')
                """,
                (now, job_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("job_not_requeueable")
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def has_cortex_dispatch_admission(self, job_id: str) -> bool:
        """Return whether the dedicated admission ledger authorizes this job."""

        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM cortex_dispatch_admissions WHERE job_id=? LIMIT 1",
                (job_id,),
            ).fetchone()
        return row is not None

    def quarantine_cortex_dispatch(self, job_id: str, *, device_id: str) -> bool:
        """Terminally quarantine an invalid claimed Cortex dispatch."""

        now = utc_now()
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                "UPDATE jobs SET status='canceled', claimed_by_device_id=NULL, "
                "claim_token_hash=NULL, error='CortexDispatchAdmissionInvalid', "
                "completed_at=?, updated_at=? WHERE id=? AND status='running' "
                "AND capability='cortex.memory_maintenance' "
                "AND claimed_by_device_id=?",
                (now, now, job_id, device_id),
            )
        return cursor.rowcount == 1

    def toggle(
        self, resource: str, resource_id: str, enabled: bool | None
    ) -> dict[str, Any]:
        table_statuses = {
            "agents": ("active", "disabled"),
            "devices": ("active", "disabled"),
            "entitlements": ("active", "disabled"),
            "stores": ("active", "disabled"),
            "subscriptions": ("active", "paused"),
        }
        if resource not in table_statuses:
            raise ValueError("unsupported toggle resource")
        active_status, inactive_status = table_statuses[resource]
        terminated_job_count = 0
        with self.database.transaction(immediate=True) as connection:
            row = connection.execute(
                f"SELECT * FROM {resource} WHERE id = ?", (resource_id,)
            ).fetchone()
            if row is None:
                raise KeyError(resource_id)
            should_enable = (
                row["status"] != active_status if enabled is None else enabled
            )
            target = active_status if should_enable else inactive_status
            now = utc_now()
            connection.execute(
                f"UPDATE {resource} SET status = ?, updated_at = ? WHERE id = ?",
                (target, now, resource_id),
            )
            if resource == "devices" and target == inactive_status:
                connection.execute(
                    """
                    UPDATE devices
                    SET credential_revoked_at = ?,
                        credential_version = credential_version + 1
                    WHERE id = ? AND credential_kind = 'ed25519'
                      AND credential_revoked_at IS NULL
                    """,
                    (now, resource_id),
                )
                terminated = connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'canceled', claimed_by_device_id = NULL,
                        claim_token_hash = NULL, result_json = NULL,
                        error = 'device_disabled', completed_at = ?, updated_at = ?
                    WHERE status IN ('queued', 'running')
                      AND (
                        device_id = ? OR claimed_by_device_id = ? OR
                        agent_id IN (
                            SELECT id FROM agents WHERE device_id = ?
                        )
                      )
                    """,
                    (now, now, resource_id, resource_id, resource_id),
                )
                terminated_job_count = terminated.rowcount
                connection.execute(
                    "UPDATE agents SET device_id = NULL, updated_at = ? "
                    "WHERE device_id = ?",
                    (now, resource_id),
                )
            updated = connection.execute(
                f"SELECT * FROM {resource} WHERE id = ?", (resource_id,)
            ).fetchone()
        json_columns = self._LISTABLE_TABLES.get(resource, ())
        record = _record(updated, json_columns=json_columns)
        if record is None:
            raise KeyError(resource_id)
        if resource == "devices":
            record.pop("secret_hash", None)
            record.pop("public_key_b64", None)
            record["terminated_job_count"] = terminated_job_count
        return record

    def reserve_model_usage(
        self,
        *,
        tenant_id: str,
        store_id: str,
        agent_id: str,
        device_id: str,
        job_id: str,
        claim_token: str,
        model: str,
        requested_tokens: int,
        request_limit: int,
        requested_token_limit: int,
        cortex_dispatch_admission: dict[str, Any] | None = None,
        cortex_dispatch_commitment: str | None = None,
    ) -> dict[str, Any]:
        event_id = f"usage_{uuid.uuid4().hex}"
        reservation_id = f"reservation_{uuid.uuid4().hex}"
        created_at = utc_now()
        supplied_claim_hash = hash_secret(claim_token)
        with self.database.transaction(immediate=True) as connection:
            claim = connection.execute(
                """
                SELECT claim_token_hash, capability, payload_json FROM jobs
                WHERE id = ? AND status = 'running'
                  AND tenant_id = ? AND store_id = ? AND agent_id = ?
                  AND claimed_by_device_id = ?
                """,
                (job_id, tenant_id, store_id, agent_id, device_id),
            ).fetchone()
            expected_claim_hash = claim["claim_token_hash"] if claim else None
            if not expected_claim_hash or not hmac.compare_digest(
                str(expected_claim_hash), supplied_claim_hash
            ):
                raise InvalidJobClaim("job_claim_invalid")
            if str(claim["capability"]) == "cortex.memory_maintenance":
                try:
                    supplied_admission = CortexDispatchAdmission.from_mapping(
                        cortex_dispatch_admission or {}
                    )
                    persisted_payload = json.loads(str(claim["payload_json"] or ""))
                    persisted_admission = CortexDispatchAdmission.from_mapping(
                        persisted_payload["dispatch_admission"]
                    )
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise InvalidJobClaim("cortex_dispatch_admission_invalid") from exc
                ledger_row = connection.execute(
                    "SELECT 1 FROM cortex_dispatch_admissions WHERE job_id=? LIMIT 1",
                    (job_id,),
                ).fetchone()
                supplied_json = supplied_admission.canonical_json()
                persisted_json = persisted_admission.canonical_json()
                commitment = str(cortex_dispatch_commitment or "")
                if (
                    ledger_row is None
                    or not hmac.compare_digest(supplied_json, persisted_json)
                    or not hmac.compare_digest(
                        commitment, persisted_admission.dispatch_key_commitment
                    )
                ):
                    raise InvalidJobClaim("cortex_dispatch_admission_invalid")
            elif cortex_dispatch_admission is not None or cortex_dispatch_commitment:
                raise InvalidJobClaim("cortex_dispatch_scope_invalid")
            totals = connection.execute(
                """
                SELECT COUNT(*) AS request_count,
                       COALESCE(SUM(requested_tokens), 0) AS requested_tokens
                FROM usage_events WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
            request_count = int(totals["request_count"])
            previously_requested = int(totals["requested_tokens"])
            if request_count >= request_limit:
                raise ModelUsageLimitExceeded("model_request_limit_exceeded")
            if previously_requested + requested_tokens > requested_token_limit:
                raise ModelUsageLimitExceeded("model_requested_token_limit_exceeded")
            connection.execute(
                """
                INSERT INTO usage_events
                    (id, tenant_id, store_id, agent_id, device_id, job_id, request_id,
                     model, provider, input_tokens, output_tokens, total_tokens,
                     requested_tokens, cost_micros, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'reserved', 0, 0, 0, ?, 0, ?)
                """,
                (
                    event_id,
                    tenant_id,
                    store_id,
                    agent_id,
                    device_id,
                    job_id,
                    reservation_id,
                    model,
                    requested_tokens,
                    created_at,
                ),
            )
        return {
            "id": event_id,
            "request_id": reservation_id,
            "requested_tokens": requested_tokens,
            "created_at": created_at,
        }

    def finalize_model_usage(
        self,
        event_id: str,
        *,
        request_id: str,
        provider: str,
        input_tokens: int,
        output_tokens: int,
        cost_micros: int = 0,
    ) -> None:
        total_tokens = input_tokens + output_tokens
        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE usage_events
                SET request_id = ?, provider = ?, input_tokens = ?,
                    output_tokens = ?, total_tokens = ?, cost_micros = ?
                WHERE id = ? AND provider = 'reserved'
                """,
                (
                    request_id,
                    provider,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    cost_micros,
                    event_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("model_usage_reservation_invalid")

    def fail_model_usage(self, event_id: str) -> None:
        """Keep a failed provider attempt charged against request allowances."""

        with self.database.transaction(immediate=True) as connection:
            cursor = connection.execute(
                """
                UPDATE usage_events SET provider = 'failed'
                WHERE id = ? AND provider = 'reserved'
                """,
                (event_id,),
            )
            if cursor.rowcount != 1:
                raise ValueError("model_usage_reservation_invalid")

    def record_audit(
        self,
        *,
        actor_type: str,
        action: str,
        outcome: str,
        tenant_id: str | None = None,
        store_id: str | None = None,
        agent_id: str | None = None,
        device_id: str | None = None,
        user_id: str | None = None,
        job_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        correlation_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        audit_id = f"audit_{uuid.uuid4().hex}"
        created_at = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO audit_logs
                    (id, tenant_id, store_id, agent_id, device_id, user_id,
                     job_id, actor_type, action, outcome, resource_type,
                     resource_id, correlation_id, details_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    tenant_id,
                    store_id,
                    agent_id,
                    device_id,
                    user_id,
                    job_id,
                    actor_type,
                    action,
                    outcome,
                    resource_type,
                    resource_id,
                    correlation_id,
                    _json(details or {}),
                    created_at,
                ),
            )
        return {"id": audit_id, "created_at": created_at}

    def _get(
        self,
        table: str,
        record_id: str,
        *,
        json_columns: tuple[str, ...] = (),
    ) -> dict[str, Any] | None:
        if table not in self._LISTABLE_TABLES:
            raise ValueError(f"unsupported table: {table}")
        with self.database.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM {table} WHERE id = ?", (record_id,)
            ).fetchone()
        return _record(row, json_columns=json_columns)
