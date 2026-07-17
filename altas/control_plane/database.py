"""SQLite connection management and schema for the local control plane."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stores (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    name TEXT NOT NULL,
    external_ref TEXT,
    status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (tenant_id, external_ref)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    plan TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('trialing', 'active', 'past_due', 'paused', 'canceled')
    ),
    current_period_end TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    store_id TEXT NOT NULL REFERENCES stores(id),
    name TEXT NOT NULL,
    secret_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
    worker_version TEXT,
    last_heartbeat_at TEXT,
    health_status TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    store_id TEXT NOT NULL REFERENCES stores(id),
    device_id TEXT REFERENCES devices(id),
    name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entitlements (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    store_id TEXT NOT NULL REFERENCES stores(id),
    capability TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'disabled')),
    config_json TEXT NOT NULL DEFAULT '{}',
    expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (tenant_id, store_id, capability)
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    store_id TEXT NOT NULL REFERENCES stores(id),
    agent_id TEXT NOT NULL REFERENCES agents(id),
    device_id TEXT REFERENCES devices(id),
    claimed_by_device_id TEXT REFERENCES devices(id),
    claim_token_hash TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    capability TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'succeeded', 'failed', 'canceled')
    ),
    payload_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error TEXT,
    requested_by TEXT NOT NULL,
    idempotency_key TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE (tenant_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS cortex_dispatch_admissions (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    store_id TEXT NOT NULL REFERENCES stores(id),
    agent_id TEXT NOT NULL REFERENCES agents(id),
    device_id TEXT NOT NULL REFERENCES devices(id),
    job_id TEXT NOT NULL REFERENCES jobs(id),
    admission_kind TEXT NOT NULL CHECK (
        admission_kind IN ('created', 'requeued')
    ),
    admitted_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    store_id TEXT NOT NULL REFERENCES stores(id),
    agent_id TEXT NOT NULL REFERENCES agents(id),
    device_id TEXT NOT NULL REFERENCES devices(id),
    job_id TEXT NOT NULL REFERENCES jobs(id),
    request_id TEXT NOT NULL,
    model TEXT NOT NULL,
    provider TEXT NOT NULL,
    input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
    output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
    total_tokens INTEGER NOT NULL CHECK (total_tokens >= 0),
    requested_tokens INTEGER NOT NULL DEFAULT 0 CHECK (requested_tokens >= 0),
    cost_micros INTEGER NOT NULL DEFAULT 0 CHECK (cost_micros >= 0),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id TEXT PRIMARY KEY,
    tenant_id TEXT,
    store_id TEXT,
    agent_id TEXT,
    device_id TEXT,
    job_id TEXT,
    actor_type TEXT NOT NULL,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL,
    resource_type TEXT,
    resource_id TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stores_tenant ON stores(tenant_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_tenant_status
    ON subscriptions(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_devices_tenant_store ON devices(tenant_id, store_id);
CREATE INDEX IF NOT EXISTS idx_agents_tenant_store ON agents(tenant_id, store_id);
CREATE INDEX IF NOT EXISTS idx_entitlements_lookup
    ON entitlements(tenant_id, store_id, capability, status);
CREATE INDEX IF NOT EXISTS idx_jobs_worker_queue
    ON jobs(tenant_id, store_id, agent_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_cortex_admission
    ON jobs(device_id, capability, created_at, status);
CREATE INDEX IF NOT EXISTS idx_cortex_dispatch_admission_window
    ON cortex_dispatch_admissions(
        tenant_id, store_id, agent_id, device_id, admitted_at
    );
CREATE INDEX IF NOT EXISTS idx_usage_context
    ON usage_events(tenant_id, store_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_context
    ON audit_logs(tenant_id, store_id, created_at);
"""


class Database:
    """Owns schema initialization and short-lived SQLite connections."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        if self.path.is_symlink():
            raise ValueError("database path must not be a symbolic link")
        parent_existed = self.path.parent.exists()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not parent_existed:
            self.path.parent.chmod(0o700)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.executescript(SCHEMA)
            job_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(jobs)").fetchall()
            }
            migrations = {
                "claimed_by_device_id": "ALTER TABLE jobs ADD COLUMN claimed_by_device_id TEXT",
                "claim_token_hash": "ALTER TABLE jobs ADD COLUMN claim_token_hash TEXT",
                "attempt_count": (
                    "ALTER TABLE jobs ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"
                ),
            }
            for column, statement in migrations.items():
                if column not in job_columns:
                    connection.execute(statement)
            for table in ("usage_events", "audit_logs"):
                columns = {
                    row["name"]
                    for row in connection.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                }
                if "job_id" not in columns:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN job_id TEXT")
                if table == "usage_events" and "requested_tokens" not in columns:
                    connection.execute(
                        "ALTER TABLE usage_events ADD COLUMN "
                        "requested_tokens INTEGER NOT NULL DEFAULT 0"
                    )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_job ON usage_events(job_id, created_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_running_device "
                "ON jobs(claimed_by_device_id, status, started_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_job ON audit_logs(job_id, created_at)"
            )
            # Pre-provenance releases stored Cortex dispatches with an empty
            # payload. They can never be matched to an owner-local admission;
            # terminally quarantine active legacy/tampered rows so one poison
            # job cannot monopolize the one-active-dispatch guard forever.
            from altas.cortex.managed_dispatch import CortexDispatchAdmission

            active_cortex_jobs = connection.execute(
                "SELECT id, payload_json FROM jobs "
                "WHERE capability='cortex.memory_maintenance' "
                "AND status IN ('queued','running')"
            ).fetchall()
            for job in active_cortex_jobs:
                try:
                    payload = json.loads(str(job["payload_json"] or ""))
                    if not isinstance(payload, dict) or set(payload) != {
                        "dispatch_admission"
                    }:
                        raise ValueError("invalid Cortex dispatch payload")
                    admission = CortexDispatchAdmission.from_mapping(
                        payload["dispatch_admission"]
                    )
                    if not admission.is_canonical():
                        raise ValueError("non-canonical Cortex dispatch admission")
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    connection.execute(
                        "UPDATE jobs SET status='canceled', "
                        "claimed_by_device_id=NULL, claim_token_hash=NULL, "
                        "error='CortexDispatchAdmissionInvalid', started_at=NULL, "
                        "completed_at=COALESCE(completed_at, updated_at) WHERE id=?",
                        (job["id"],),
                    )
            # Existing databases predate the admission ledger. Count each
            # historical Cortex job once so restarting after an upgrade cannot
            # reset the rolling dispatch allowance. New jobs use the same
            # deterministic event ID, making this backfill restart-safe.
            connection.execute(
                """
                INSERT OR IGNORE INTO cortex_dispatch_admissions
                    (id, tenant_id, store_id, agent_id, device_id, job_id,
                     admission_kind, admitted_at)
                SELECT 'cortex-create:' || id, tenant_id, store_id, agent_id,
                       device_id, id, 'created', created_at
                FROM jobs
                WHERE capability='cortex.memory_maintenance'
                  AND device_id IS NOT NULL
                """
            )
        for database_file in self.path.parent.glob(f"{self.path.name}*"):
            if database_file.is_file():
                database_file.chmod(0o600)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.path,
            timeout=10.0,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def healthy(self) -> bool:
        try:
            with self.connect() as connection:
                row = connection.execute("SELECT 1 AS healthy").fetchone()
            return bool(row and row["healthy"] == 1)
        except sqlite3.Error:
            return False
