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

from .database import Database
from .redaction import sanitize_for_storage
from .security import hash_secret


DEMO_TENANT_ID = "tenant_demo_fixed_ops"
DEMO_STORE_ID = "store-sunrise-vw"
DEMO_UNENTITLED_STORE_ID = "store-harbor-toyota"
DEMO_SUBSCRIPTION_ID = "subscription_demo_professional"
DEMO_DEVICE_ID = "device_demo_local_worker"
DEMO_AGENT_ID = "agent_demo_altas"
DEMO_DEVICE_SECRET = "altas-demo-device-secret-v1"

DEMO_CAPABILITIES = (
    "jobs.complete",
    "jobs.poll",
    "model.chat",
    "fixed_ops.daily_report",
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


class InvalidJobClaim(ValueError):
    """A worker presented a missing, stale, or otherwise invalid claim token."""


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
                    "Altas Demo Dealer Group",
                    "altas-demo",
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
                VALUES (?, ?, ?, ?, 'Altas Fixed Ops', 'active', ?, ?)
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
                "SELECT * FROM devices WHERE secret_hash = ?", (supplied_hash,)
            ).fetchone()
        device = _record(row, json_columns=("metadata_json",))
        if not device or not hmac.compare_digest(device["secret_hash"], supplied_hash):
            return None
        # The hash proves authentication but is never useful to API consumers.
        del device["secret_hash"]
        return device

    def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        return self._get("tenants", tenant_id)

    def get_store(self, store_id: str) -> dict[str, Any] | None:
        return self._get("stores", store_id)

    def get_device(self, device_id: str) -> dict[str, Any] | None:
        record = self._get("devices", device_id, json_columns=("metadata_json",))
        if record:
            record.pop("secret_hash", None)
        return record

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
            updated = connection.execute(
                f"SELECT * FROM {resource} WHERE id = ?", (resource_id,)
            ).fetchone()
        json_columns = self._LISTABLE_TABLES.get(resource, ())
        record = _record(updated, json_columns=json_columns)
        if record is None:
            raise KeyError(resource_id)
        if resource == "devices":
            record.pop("secret_hash", None)
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
    ) -> dict[str, Any]:
        event_id = f"usage_{uuid.uuid4().hex}"
        reservation_id = f"reservation_{uuid.uuid4().hex}"
        created_at = utc_now()
        supplied_claim_hash = hash_secret(claim_token)
        with self.database.transaction(immediate=True) as connection:
            claim = connection.execute(
                """
                SELECT claim_token_hash FROM jobs
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
        job_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        audit_id = f"audit_{uuid.uuid4().hex}"
        created_at = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO audit_logs
                    (id, tenant_id, store_id, agent_id, device_id, job_id, actor_type,
                     action, outcome, resource_type, resource_id, details_json,
                     created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id,
                    tenant_id,
                    store_id,
                    agent_id,
                    device_id,
                    job_id,
                    actor_type,
                    action,
                    outcome,
                    resource_type,
                    resource_id,
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
