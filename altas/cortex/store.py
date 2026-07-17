"""Secure, profile-scoped SQLite system of record for Atlas Cortex."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

from altas.control_plane.redaction import sanitize_for_storage

from .config import CORTEX_SCHEMA_VERSION
from .models import EvidenceInput, RecallItem, RecallResult
from .managed_dispatch import CortexDispatchAdmission


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def stable_hash(*parts: Any) -> str:
    encoded = "\x1f".join(str(part or "") for part in parts).encode(
        "utf-8", errors="replace"
    )
    return hashlib.sha256(encoded).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _terms(query: str) -> list[str]:
    return [
        term.lower() for term in re.findall(r"[\w'-]{2,}", query, flags=re.UNICODE)[:24]
    ]


SCHEMA = """
CREATE TABLE IF NOT EXISTS cortex_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS brains (
    id TEXT PRIMARY KEY,
    owner_customer_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    timezone TEXT NOT NULL,
    retention_policy_id TEXT,
    encryption_key_ref TEXT,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS principals (
    id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL REFERENCES brains(id),
    principal_type TEXT NOT NULL,
    external_id TEXT NOT NULL,
    display_name TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    UNIQUE(brain_id, principal_type, external_id)
);

CREATE TABLE IF NOT EXISTS knowledge_spaces (
    id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL REFERENCES brains(id),
    slug TEXT NOT NULL,
    display_name TEXT NOT NULL,
    visibility TEXT NOT NULL DEFAULT 'private',
    source_policy TEXT NOT NULL DEFAULT 'local',
    read_policy_json TEXT NOT NULL DEFAULT '{}',
    write_policy_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    deleted_at TEXT,
    UNIQUE(brain_id, slug)
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL REFERENCES brains(id),
    logical_conversation_id TEXT NOT NULL,
    parent_session_id TEXT,
    title TEXT,
    summary TEXT,
    workspace TEXT,
    dealership_context TEXT,
    state TEXT NOT NULL CHECK(state IN ('active','finalized','reset','interrupted','deleted')),
    evidence_hash TEXT,
    started_at TEXT NOT NULL,
    finalized_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_items (
    id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL REFERENCES brains(id),
    knowledge_space_id TEXT NOT NULL REFERENCES knowledge_spaces(id),
    session_id TEXT REFERENCES sessions(id),
    source_type TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    actor_principal_id TEXT REFERENCES principals(id),
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    sensitivity TEXT NOT NULL DEFAULT 'private',
    retention_class TEXT NOT NULL DEFAULT 'standard',
    parent_evidence_id TEXT REFERENCES evidence_items(id),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    tombstoned_at TEXT,
    UNIQUE(brain_id, source_type, source_locator, content_hash)
);

CREATE TABLE IF NOT EXISTS work_events (
    id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL REFERENCES brains(id),
    session_id TEXT REFERENCES sessions(id),
    event_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    evidence_id TEXT REFERENCES evidence_items(id),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL REFERENCES brains(id),
    knowledge_space_id TEXT NOT NULL REFERENCES knowledge_spaces(id),
    session_id TEXT REFERENCES sessions(id),
    kind TEXT NOT NULL,
    normalized_text TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    entity_candidates_json TEXT NOT NULL DEFAULT '[]',
    valid_from TEXT,
    valid_until TEXT,
    processing_state TEXT NOT NULL,
    utility_action TEXT,
    triage_reason TEXT,
    epistemic_status TEXT NOT NULL DEFAULT 'reported',
    model TEXT,
    prompt_version TEXT,
    triage_attempts INTEGER NOT NULL DEFAULT 0,
    next_triage_at TEXT,
    idempotency_key TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    processed_at TEXT
);

CREATE TABLE IF NOT EXISTS memory_records (
    id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL REFERENCES brains(id),
    knowledge_space_id TEXT NOT NULL REFERENCES knowledge_spaces(id),
    kind TEXT NOT NULL,
    subject_entity_id TEXT,
    object_entity_id TEXT,
    canonical_statement TEXT NOT NULL,
    statement_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    epistemic_status TEXT NOT NULL,
    valid_from TEXT,
    valid_until TEXT,
    first_seen_at TEXT NOT NULL,
    last_confirmed_at TEXT NOT NULL,
    created_by_job_id TEXT,
    created_by_model TEXT,
    prompt_version TEXT,
    superseded_by_id TEXT REFERENCES memory_records(id),
    protected INTEGER NOT NULL DEFAULT 0,
    user_visible INTEGER NOT NULL DEFAULT 1,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS memory_evidence (
    memory_id TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL REFERENCES evidence_items(id) ON DELETE CASCADE,
    PRIMARY KEY(memory_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL REFERENCES brains(id),
    knowledge_space_id TEXT NOT NULL REFERENCES knowledge_spaces(id),
    entity_type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    normalized_name TEXT NOT NULL,
    description TEXT,
    visibility TEXT NOT NULL DEFAULT 'private',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    deleted_at TEXT,
    UNIQUE(brain_id, knowledge_space_id, entity_type, normalized_name)
);

CREATE TABLE IF NOT EXISTS entity_aliases (
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    knowledge_space_id TEXT NOT NULL REFERENCES knowledge_spaces(id),
    alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    evidence_id TEXT REFERENCES evidence_items(id),
    deleted_at TEXT,
    PRIMARY KEY(entity_id, knowledge_space_id, normalized_alias)
);

CREATE TABLE IF NOT EXISTS entity_evidence (
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL REFERENCES evidence_items(id) ON DELETE CASCADE,
    PRIMARY KEY(entity_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS entity_alias_evidence (
    entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    knowledge_space_id TEXT NOT NULL REFERENCES knowledge_spaces(id),
    normalized_alias TEXT NOT NULL,
    evidence_id TEXT NOT NULL REFERENCES evidence_items(id) ON DELETE CASCADE,
    PRIMARY KEY(entity_id, knowledge_space_id, normalized_alias, evidence_id)
);

CREATE TABLE IF NOT EXISTS relations (
    id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL REFERENCES brains(id),
    knowledge_space_id TEXT NOT NULL REFERENCES knowledge_spaces(id),
    subject_entity_id TEXT NOT NULL REFERENCES entities(id),
    predicate TEXT NOT NULL,
    object_entity_id TEXT NOT NULL REFERENCES entities(id),
    status TEXT NOT NULL DEFAULT 'active',
    epistemic_status TEXT NOT NULL DEFAULT 'reported',
    valid_from TEXT,
    valid_until TEXT,
    weight REAL NOT NULL DEFAULT 1.0,
    derived_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(brain_id, knowledge_space_id, subject_entity_id, predicate, object_entity_id, valid_from)
);

CREATE TABLE IF NOT EXISTS relation_evidence (
    relation_id TEXT NOT NULL REFERENCES relations(id) ON DELETE CASCADE,
    evidence_id TEXT NOT NULL REFERENCES evidence_items(id) ON DELETE CASCADE,
    PRIMARY KEY(relation_id, evidence_id)
);

CREATE TABLE IF NOT EXISTS compiled_views (
    id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL REFERENCES brains(id),
    knowledge_space_id TEXT NOT NULL REFERENCES knowledge_spaces(id),
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    synthesis TEXT NOT NULL,
    support_json TEXT NOT NULL DEFAULT '[]',
    model TEXT,
    prompt_version TEXT,
    input_hash TEXT NOT NULL,
    stale INTEGER NOT NULL DEFAULT 0,
    generated_at TEXT NOT NULL,
    UNIQUE(brain_id, target_type, target_id, input_hash)
);

CREATE TABLE IF NOT EXISTS communities (
    id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL REFERENCES brains(id),
    knowledge_space_id TEXT NOT NULL REFERENCES knowledge_spaces(id),
    level INTEGER NOT NULL DEFAULT 0,
    parent_id TEXT REFERENCES communities(id),
    label TEXT NOT NULL,
    report TEXT,
    algorithm_version TEXT NOT NULL,
    member_ids_json TEXT NOT NULL DEFAULT '[]',
    stale INTEGER NOT NULL DEFAULT 0,
    generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS retrieval_runs (
    id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL REFERENCES brains(id),
    principal_id TEXT REFERENCES principals(id),
    session_id TEXT REFERENCES sessions(id),
    query_hash TEXT NOT NULL,
    query_preview TEXT NOT NULL,
    route TEXT NOT NULL,
    allowed_spaces_json TEXT NOT NULL,
    candidate_ids_json TEXT NOT NULL,
    selected_ids_json TEXT NOT NULL,
    explanation_json TEXT NOT NULL,
    latency_ms INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_distill_admissions (
    id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL REFERENCES brains(id),
    logical_conversation_id TEXT NOT NULL,
    tip_session_id TEXT NOT NULL REFERENCES sessions(id),
    final_state TEXT NOT NULL CHECK(final_state IN ('finalized','reset','interrupted')),
    canonical_input_hash TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    admitted_at TEXT NOT NULL,
    revoked_at TEXT,
    revocation_reason TEXT,
    UNIQUE(brain_id, canonical_input_hash)
);

CREATE TABLE IF NOT EXISTS cognitive_jobs (
    id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL REFERENCES brains(id),
    job_type TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    state TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    scheduled_at TEXT NOT NULL,
    next_attempt_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    heartbeat_at TEXT,
    admission_id TEXT REFERENCES session_distill_admissions(id),
    parent_job_id TEXT REFERENCES cognitive_jobs(id),
    root_job_id TEXT REFERENCES cognitive_jobs(id),
    model TEXT,
    prompt_version TEXT,
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT NOT NULL DEFAULT '{}',
    checkpoint_json TEXT NOT NULL DEFAULT '{}',
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_micros INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    dead_letter_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(brain_id, job_type, input_hash)
);

CREATE TABLE IF NOT EXISTS cognitive_job_operations (
    job_id TEXT NOT NULL REFERENCES cognitive_jobs(id) ON DELETE CASCADE,
    operation_key TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    resource_id TEXT,
    result_json TEXT NOT NULL DEFAULT '{}',
    applied_at TEXT NOT NULL,
    PRIMARY KEY(job_id, operation_key)
);

CREATE TABLE IF NOT EXISTS cortex_leases (
    brain_id TEXT NOT NULL REFERENCES brains(id),
    lease_name TEXT NOT NULL,
    owner TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    PRIMARY KEY(brain_id, lease_name)
);

CREATE TABLE IF NOT EXISTS graphrag_indexes (
    id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL REFERENCES brains(id),
    version TEXT NOT NULL,
    path TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    signed_by TEXT,
    state TEXT NOT NULL,
    document_count INTEGER NOT NULL DEFAULT 0,
    published_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(brain_id, version)
);

CREATE TABLE IF NOT EXISTS graphrag_documents (
    id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL REFERENCES brains(id),
    index_id TEXT NOT NULL REFERENCES graphrag_indexes(id) ON DELETE CASCADE,
    knowledge_space_id TEXT NOT NULL REFERENCES knowledge_spaces(id),
    document_id TEXT NOT NULL,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    source_uri TEXT,
    entity_ids_json TEXT NOT NULL DEFAULT '[]',
    community_ids_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(index_id, document_id)
);

CREATE TABLE IF NOT EXISTS health_reports (
    id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL REFERENCES brains(id),
    job_id TEXT REFERENCES cognitive_jobs(id),
    status TEXT NOT NULL,
    report_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cortex_audit (
    id TEXT PRIMARY KEY,
    brain_id TEXT NOT NULL REFERENCES brains(id),
    principal_id TEXT,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT,
    outcome TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_evidence_session ON evidence_items(brain_id, session_id, ingested_at);
CREATE INDEX IF NOT EXISTS idx_evidence_space ON evidence_items(brain_id, knowledge_space_id, tombstoned_at);
CREATE INDEX IF NOT EXISTS idx_observations_queue ON observations(brain_id, processing_state, created_at);
CREATE INDEX IF NOT EXISTS idx_memory_active ON memory_records(brain_id, knowledge_space_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_entity_evidence_source ON entity_evidence(evidence_id, entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_alias_evidence_source ON entity_alias_evidence(evidence_id, entity_id);
CREATE INDEX IF NOT EXISTS idx_relations_subject ON relations(brain_id, subject_entity_id, status);
CREATE INDEX IF NOT EXISTS idx_relations_object ON relations(brain_id, object_entity_id, status);
CREATE INDEX IF NOT EXISTS idx_jobs_queue ON cognitive_jobs(brain_id, state, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_retrieval_session ON retrieval_runs(brain_id, session_id, created_at);
"""


FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS evidence_fts USING fts5(id UNINDEXED, content, tokenize='unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(id UNINDEXED, content, tokenize='unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS entity_fts USING fts5(id UNINDEXED, content, tokenize='unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS graphrag_fts USING fts5(id UNINDEXED, content, tokenize='unicode61');
"""

FTS_CONTENT_VERSION = "atlas.cortex.fts.v2"
FINAL_SESSION_STATES = frozenset({"finalized", "reset", "interrupted", "deleted"})
BOUNDARY_MARKER_VERSION = 2
LEGACY_SESSION_DISTILL_JOB_TYPE = "legacy_session_distill_unadmitted"
QUARANTINED_SESSION_DISTILL_JOB_TYPE = "quarantined_session_distill"
SESSION_DISTILL_RECOVERY_CURSOR_VERSION = 2
SESSION_DISTILL_RECOVERY_PAGE_SIZE = 100
SESSION_DISTILL_RECOVERY_FRESH_SIZE = 10
SESSION_DISTILL_RECOVERY_CANDIDATE_ATTEMPTS = 4
AUTOMATIC_RECALL_EVIDENCE_TYPES = frozenset({"user_message", "correction", "manual"})
_SENSITIVITY_RANK = {"private": 0, "sensitive": 1, "restricted": 2}
_RESTRICTED_EVIDENCE_PATTERNS = (
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b(?:social\s+security|ssn|tax\s+id)\b.{0,32}\d", re.I),
    re.compile(r"\b(?:credit|debit)\s+card\b.{0,48}\d", re.I),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(
        r"\b(?:password|passwd|api[_ -]?key|access[_ -]?token|secret[_ -]?key)\b\s*[:=]",
        re.I,
    ),
)
_SENSITIVE_EVIDENCE_PATTERNS = (
    re.compile(
        r"\b(?:diagnos(?:is|ed)|prescription|medication|medical\s+record|"
        r"patient|therapy|health\s+condition)\b",
        re.I,
    ),
    re.compile(
        r"\b(?:bank\s+account|routing\s+number|salary|payroll|passport|"
        r"driver'?s\s+licen[cs]e|date\s+of\s+birth|dob)\b",
        re.I,
    ),
)


def _classified_evidence_sensitivity(content: str, declared: str) -> str:
    normalized = str(declared or "private").strip().lower()
    if normalized == "confidential":
        normalized = "restricted"
    if normalized not in _SENSITIVITY_RANK:
        normalized = "private"
    detected = "private"
    if any(pattern.search(content) for pattern in _RESTRICTED_EVIDENCE_PATTERNS):
        detected = "restricted"
    elif any(pattern.search(content) for pattern in _SENSITIVE_EVIDENCE_PATTERNS):
        detected = "sensitive"
    return max((normalized, detected), key=_SENSITIVITY_RANK.__getitem__)


class CortexStore:
    """Owns one brain database inside one active Atlas profile."""

    def __init__(
        self,
        path: Path,
        *,
        owner_customer_id: str,
        display_name: str = "My Atlas",
        timezone_name: str = "local",
        redact_secrets: bool = True,
        recall_graph_hops: int = 1,
        graphrag_max_items: int = 6,
    ) -> None:
        self.path = Path(path)
        self.owner_customer_id = owner_customer_id.strip()
        if not self.owner_customer_id:
            raise ValueError(
                "owner_customer_id is required; Cortex never uses a global brain"
            )
        self.display_name = display_name.strip() or "My Atlas"
        self.timezone_name = timezone_name.strip() or "local"
        self.redact_secrets = redact_secrets
        self.recall_graph_hops = max(0, min(2, int(recall_graph_hops)))
        self.graphrag_max_items = max(1, min(20, int(graphrag_max_items)))
        self.brain_id = ""
        self._fts_available = False
        self._write_lock = threading.RLock()
        self._transaction_state = threading.local()

    def initialize(self) -> None:
        # ``Path.exists()`` is false for a dangling link. Check the directory
        # entry itself before O_CREAT so a link to a not-yet-existing target
        # cannot make Cortex create a database outside the intended profile.
        if self.path.is_symlink():
            raise ValueError("Cortex database path must not be a symbolic link")
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Setup may have created the directory under a permissive umask. The
        # database contents are 0600, but filenames, WAL presence, and index
        # layout are still private metadata, so tighten an existing directory
        # as well as a newly-created one.
        try:
            self.path.parent.chmod(0o700)
        except OSError:
            pass
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA secure_delete = ON")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(SCHEMA)
            try:
                connection.executescript(FTS_SCHEMA)
                self._fts_available = True
            except sqlite3.OperationalError:
                self._fts_available = False
            self._migrate(connection)
            connection.execute(
                "INSERT OR REPLACE INTO cortex_meta(key, value) VALUES('schema_version', ?)",
                (str(CORTEX_SCHEMA_VERSION),),
            )
            self.brain_id = self._ensure_brain(connection)
            self._ensure_space(connection, "personal", "Personal", "private", "local")
            self._ensure_space(
                connection,
                "atlas-capabilities",
                "Atlas capabilities",
                "private",
                "atlas",
            )
            self._ensure_space(
                connection, "tekion", "Tekion knowledge", "shared", "graphrag"
            )
            if self._fts_available:
                try:
                    self._synchronize_fts(connection)
                except sqlite3.OperationalError:
                    # FTS is an acceleration channel, never a prerequisite for
                    # opening the customer's system of record.
                    self._fts_available = False
        self._secure_sidecars()
        # A process may exit after recording boundary intent but before the
        # SQLite transaction commits. Replaying these tiny profile-local
        # markers makes a true session end recoverable without inferring one
        # from inactivity or running a nightly semantic pass.
        self.recover_pending_boundaries()

    def _synchronize_fts(self, connection: sqlite3.Connection) -> None:
        """Build the versioned lexical projection once for existing brains.

        Early Cortex databases created the FTS tables but did not query them,
        and an existing database can gain FTS support when SQLite is upgraded.
        The version marker makes the repair deterministic without rebuilding a
        large Tekion corpus at every startup.
        """
        row = connection.execute(
            "SELECT value FROM cortex_meta WHERE key='fts_content_version'"
        ).fetchone()
        if row and str(row["value"]) == FTS_CONTENT_VERSION:
            return
        for table in ("evidence_fts", "memory_fts", "entity_fts", "graphrag_fts"):
            connection.execute(f"DELETE FROM {table}")
        connection.execute(
            "INSERT INTO evidence_fts(id, content) "
            "SELECT id, content FROM evidence_items WHERE tombstoned_at IS NULL"
        )
        connection.execute(
            "INSERT INTO memory_fts(id, content) "
            "SELECT id, canonical_statement FROM memory_records "
            "WHERE status IN ('active','disputed') AND deleted_at IS NULL"
        )
        connection.execute(
            "INSERT INTO entity_fts(id, content) "
            "SELECT e.id, TRIM(e.canonical_name || ' ' || COALESCE(e.description,'') || ' ' || "
            "COALESCE((SELECT GROUP_CONCAT(a.alias, ' ') FROM entity_aliases a "
            "WHERE a.entity_id=e.id AND a.deleted_at IS NULL), '')) "
            "FROM entities e WHERE e.deleted_at IS NULL"
        )
        connection.execute(
            "INSERT INTO graphrag_fts(id, content) "
            "SELECT id, TRIM(title || ' ' || text) FROM graphrag_documents"
        )
        connection.execute(
            "INSERT OR REPLACE INTO cortex_meta(key, value) VALUES('fts_content_version', ?)",
            (FTS_CONTENT_VERSION,),
        )

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        """Apply additive durability and admission columns to early pilots."""
        admission_columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(session_distill_admissions)"
            )
        }
        admission_additions = {
            "revoked_at": "TEXT",
            "revocation_reason": "TEXT",
        }
        for name, declaration in admission_additions.items():
            if name not in admission_columns:
                connection.execute(
                    f"ALTER TABLE session_distill_admissions ADD COLUMN "
                    f"{name} {declaration}"
                )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(cognitive_jobs)")
        }
        additions = {
            "max_attempts": "INTEGER NOT NULL DEFAULT 5",
            "next_attempt_at": "TEXT",
            "heartbeat_at": "TEXT",
            "checkpoint_json": "TEXT NOT NULL DEFAULT '{}'",
            "input_tokens": "INTEGER NOT NULL DEFAULT 0",
            "output_tokens": "INTEGER NOT NULL DEFAULT 0",
            "cost_micros": "INTEGER NOT NULL DEFAULT 0",
            "dead_letter_at": "TEXT",
            "admission_id": "TEXT REFERENCES session_distill_admissions(id)",
            "parent_job_id": "TEXT REFERENCES cognitive_jobs(id)",
            "root_job_id": "TEXT REFERENCES cognitive_jobs(id)",
        }
        for name, declaration in additions.items():
            if name not in columns:
                connection.execute(
                    f"ALTER TABLE cognitive_jobs ADD COLUMN {name} {declaration}"
                )
        connection.execute(
            "UPDATE cognitive_jobs SET next_attempt_at=COALESCE(next_attempt_at, scheduled_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_admission "
            "ON cognitive_jobs(admission_id, created_at)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_admission_root "
            "ON cognitive_jobs(admission_id, job_type, parent_job_id, input_hash)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_distill_admissions_brain "
            "ON session_distill_admissions(brain_id, admitted_at)"
        )
        # Pre-v2 rows have no durable proof that they were atomically admitted
        # by a real logical-session boundary. Relabel and deterministically
        # rekey every one -- including terminal history -- so it can never be
        # leased and cannot occupy the canonical UNIQUE namespace needed by a
        # later genuine boundary. Outputs and operation receipts remain linked
        # to the same job id for diagnostics.
        now = utc_now()
        legacy_rows = connection.execute(
            "SELECT id, brain_id, input_hash, state FROM cognitive_jobs "
            "WHERE job_type='session_distill' AND admission_id IS NULL"
        ).fetchall()
        for row in legacy_rows:
            rekeyed_hash = stable_hash(
                row["brain_id"],
                row["id"],
                row["input_hash"],
                "legacy-unadmitted-session-distill:v2",
            )
            active = str(row["state"]) in {"queued", "running"}
            connection.execute(
                "UPDATE cognitive_jobs SET job_type=?, input_hash=?, "
                "state=CASE WHEN ? THEN 'failed' ELSE state END, "
                "error=COALESCE(error, ?), "
                "completed_at=CASE WHEN ? THEN COALESCE(completed_at, ?) "
                "ELSE completed_at END, lease_owner=NULL, lease_expires_at=NULL, "
                "heartbeat_at=CASE WHEN ? THEN ? ELSE heartbeat_at END, updated_at=? "
                "WHERE id=? AND job_type='session_distill' AND admission_id IS NULL",
                (
                    LEGACY_SESSION_DISTILL_JOB_TYPE,
                    rekeyed_hash,
                    active,
                    "missing durable session-distill admission",
                    active,
                    now,
                    active,
                    now,
                    now,
                    row["id"],
                ),
            )
        observation_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(observations)")
        }
        observation_additions = {
            "triage_attempts": "INTEGER NOT NULL DEFAULT 0",
            "next_triage_at": "TEXT",
        }
        for name, declaration in observation_additions.items():
            if name not in observation_columns:
                connection.execute(
                    f"ALTER TABLE observations ADD COLUMN {name} {declaration}"
                )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_observations_due "
            "ON observations(brain_id, processing_state, next_triage_at, created_at)"
        )
        entity_alias_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(entity_aliases)")
        }
        if "deleted_at" not in entity_alias_columns:
            connection.execute("ALTER TABLE entity_aliases ADD COLUMN deleted_at TEXT")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS entity_evidence ("
            "entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE, "
            "evidence_id TEXT NOT NULL REFERENCES evidence_items(id) ON DELETE CASCADE, "
            "PRIMARY KEY(entity_id, evidence_id))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS entity_alias_evidence ("
            "entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE, "
            "knowledge_space_id TEXT NOT NULL REFERENCES knowledge_spaces(id), "
            "normalized_alias TEXT NOT NULL, "
            "evidence_id TEXT NOT NULL REFERENCES evidence_items(id) ON DELETE CASCADE, "
            "PRIMARY KEY(entity_id, knowledge_space_id, normalized_alias, evidence_id))"
        )
        # Pilot databases stored one provenance link directly on each alias.
        # Backfill it into the many-to-many support tables so rewinding one
        # session cannot hide an entity that another session still supports.
        connection.execute(
            "INSERT OR IGNORE INTO entity_evidence(entity_id, evidence_id) "
            "SELECT entity_id, evidence_id FROM entity_aliases "
            "WHERE evidence_id IS NOT NULL"
        )
        connection.execute(
            "INSERT OR IGNORE INTO entity_alias_evidence("
            "entity_id, knowledge_space_id, normalized_alias, evidence_id) "
            "SELECT entity_id, knowledge_space_id, normalized_alias, evidence_id "
            "FROM entity_aliases WHERE evidence_id IS NOT NULL"
        )
        graphrag_columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(graphrag_indexes)")
        }
        if "signed_by" not in graphrag_columns:
            connection.execute("ALTER TABLE graphrag_indexes ADD COLUMN signed_by TEXT")

    def _secure_sidecars(self) -> None:
        for candidate in self.path.parent.glob(f"{self.path.name}*"):
            if candidate.is_file() and not candidate.is_symlink():
                try:
                    candidate.chmod(0o600)
                except OSError:
                    pass

    @property
    def _boundary_marker_directory(self) -> Path:
        return self.path.parent / "pending-boundaries"

    def _boundary_marker_path(self, session_id: str) -> Path:
        digest = stable_hash(self.brain_id, session_id, "logical-boundary:v1")
        return self._boundary_marker_directory / f"{digest}.json"

    def _persist_boundary_marker(
        self,
        session_id: str,
        *,
        state: str,
        enqueue_distill: bool,
        summary: str,
        base_spec: Mapping[str, Any],
        prepared_spec: Mapping[str, Any] | None = None,
    ) -> Path:
        """Atomically persist an evidence-bound logical session boundary.

        ``base_spec`` is the durable pre-capture epoch. ``prepared_spec`` is
        added after final transcript capture, while the SQLite write lock is
        still held. Recovery may replay either identity: a crash rolls the
        SQLite transaction back to the base epoch, while a committed boundary
        that merely failed to unlink its marker matches the prepared epoch.
        Evidence appended by a later resumed turn matches neither and therefore
        cannot be folded into the older boundary.
        """

        directory = self._boundary_marker_directory
        if directory.exists() and directory.is_symlink():
            raise ValueError("Cortex boundary marker directory must not be a symlink")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            directory.chmod(0o700)
        except OSError:
            pass
        target = self._boundary_marker_path(session_id)
        temporary = directory / f".{target.stem}.{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        payload = _json({
            "version": BOUNDARY_MARKER_VERSION,
            "session_id": session_id,
            "state": state,
            "enqueue_distill": bool(enqueue_distill),
            "summary": summary[:4_000],
            "base": self._boundary_spec_identity(base_spec),
            "prepared": (
                self._boundary_spec_identity(prepared_spec)
                if prepared_spec is not None
                else None
            ),
            "recorded_at": utc_now(),
        }).encode("utf-8")
        try:
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.replace(temporary, target)
            try:
                target.chmod(0o600)
            except OSError:
                pass
            directory_descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return target

    @staticmethod
    def _boundary_spec_identity(spec: Mapping[str, Any]) -> dict[str, str]:
        input_data = spec.get("input_data")
        if not isinstance(input_data, Mapping):
            raise ValueError("invalid Cortex boundary specification")
        identity = {
            "logical_conversation_id": str(
                input_data.get("logical_conversation_id") or ""
            ),
            "evidence_hash": str(input_data.get("evidence_hash") or ""),
            "input_hash": str(spec.get("input_hash") or ""),
        }
        if (
            not identity["logical_conversation_id"]
            or len(identity["logical_conversation_id"]) > 512
            or len(identity["evidence_hash"]) != 64
            or len(identity["input_hash"]) != 64
        ):
            raise ValueError("invalid Cortex boundary identity")
        return identity

    @classmethod
    def _boundary_identity_matches(
        cls, identity: Mapping[str, Any] | None, spec: Mapping[str, Any]
    ) -> bool:
        if not isinstance(identity, Mapping):
            return False
        try:
            return {
                "logical_conversation_id": str(
                    identity.get("logical_conversation_id") or ""
                ),
                "evidence_hash": str(identity.get("evidence_hash") or ""),
                "input_hash": str(identity.get("input_hash") or ""),
            } == cls._boundary_spec_identity(spec)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _clear_boundary_marker(marker: Path) -> None:
        try:
            marker.unlink(missing_ok=True)
        except OSError:
            # A committed SQLite boundary is already durable. Leaving the
            # marker behind is safe: recovery is idempotent and will retry the
            # unlink on the next profile open.
            pass

    def pending_boundary_count(self) -> int:
        directory = self._boundary_marker_directory
        if not directory.exists() or directory.is_symlink():
            return 0
        return sum(
            1
            for marker in directory.glob("*.json")
            if marker.is_file() and not marker.is_symlink()
        )

    def recover_pending_boundaries(self) -> tuple[str, ...]:
        """Replay durable logical-boundary intent left by an interrupted exit."""

        directory = self._boundary_marker_directory
        if not directory.exists():
            return ()
        if directory.is_symlink():
            raise ValueError("Cortex boundary marker directory must not be a symlink")
        recovered: list[str] = []
        for marker in sorted(directory.glob("*.json"))[:10_000]:
            if not marker.is_file() or marker.is_symlink():
                continue
            try:
                raw = marker.read_bytes()
                if len(raw) > 64 * 1024:
                    raise ValueError("boundary marker exceeds size limit")
                payload = json.loads(raw.decode("utf-8"))
                if not isinstance(payload, Mapping):
                    raise ValueError("boundary marker must be an object")
                session_id = str(payload.get("session_id") or "")
                state = str(payload.get("state") or "")
                base_identity = payload.get("base")
                prepared_identity = payload.get("prepared")
                summary = str(payload.get("summary") or "")
                if (
                    int(payload.get("version") or 0) != BOUNDARY_MARKER_VERSION
                    or not session_id
                    or len(session_id) > 512
                    or state not in FINAL_SESSION_STATES
                    or not isinstance(base_identity, Mapping)
                    or len(summary) > 4_000
                ):
                    raise ValueError("invalid boundary marker")
                expected = self._boundary_marker_path(session_id)
                if expected.name != marker.name:
                    raise ValueError("boundary marker identity mismatch")
                matched = False
                with self.transaction() as connection:
                    current_spec = self._lineage_distill_spec_in_transaction(
                        connection, session_id
                    )
                    matched = self._boundary_identity_matches(
                        prepared_identity, current_spec
                    ) or self._boundary_identity_matches(base_identity, current_spec)
                    if matched:
                        self._finalize_session_in_transaction(
                            connection,
                            current_spec,
                            state=state,
                            summary=summary,
                            enqueue_distill=bool(payload.get("enqueue_distill", True)),
                        )
                # A mismatch means the physical/logical session was resumed
                # and acquired newer evidence. The old marker is superseded;
                # its next genuine boundary will consolidate the full lineage.
                self._clear_boundary_marker(marker)
                if matched:
                    recovered.append(session_id)
            except LookupError:
                # A marker cannot authorize creation or cross-brain access. If
                # its brain-scoped session no longer exists, discard it.
                self._clear_boundary_marker(marker)
            except (OSError, sqlite3.Error):
                # The marker remains the durable retry source.
                continue
            except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError):
                # Malformed local state must not block the whole customer
                # brain. Move it out of the replay namespace for inspection.
                try:
                    marker.replace(marker.with_suffix(".invalid"))
                except OSError:
                    pass
        return tuple(recovered)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        active = getattr(self._transaction_state, "connection", None)
        if active is not None:
            yield active
            return
        connection = sqlite3.connect(str(self.path), timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA secure_delete = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        active = getattr(self._transaction_state, "connection", None)
        if active is not None:
            depth = int(getattr(self._transaction_state, "depth", 0)) + 1
            self._transaction_state.depth = depth
            savepoint = f"cortex_nested_{depth}_{uuid.uuid4().hex}"
            active.execute(f"SAVEPOINT {savepoint}")
            try:
                yield active
            except BaseException:
                active.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                active.execute(f"RELEASE SAVEPOINT {savepoint}")
                raise
            else:
                active.execute(f"RELEASE SAVEPOINT {savepoint}")
            finally:
                self._transaction_state.depth = depth - 1
            return
        with self._write_lock, self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._transaction_state.connection = connection
            self._transaction_state.depth = 0
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()
            finally:
                self._transaction_state.connection = None
                self._transaction_state.depth = 0
        self._secure_sidecars()

    def _ensure_brain(self, connection: sqlite3.Connection) -> str:
        row = connection.execute(
            "SELECT id, owner_customer_id FROM brains WHERE deleted_at IS NULL LIMIT 1"
        ).fetchone()
        if row:
            if row["owner_customer_id"] != self.owner_customer_id:
                raise ValueError(
                    "Cortex brain owner mismatch; refusing to open another customer's database"
                )
            return str(row["id"])
        brain_id = new_id("brain")
        connection.execute(
            "INSERT INTO brains(id, owner_customer_id, display_name, timezone, created_at) "
            "VALUES(?,?,?,?,?)",
            (
                brain_id,
                self.owner_customer_id,
                self.display_name,
                self.timezone_name,
                utc_now(),
            ),
        )
        return brain_id

    def _ensure_space(
        self,
        connection: sqlite3.Connection,
        slug: str,
        display_name: str,
        visibility: str,
        source_policy: str,
    ) -> str:
        row = connection.execute(
            "SELECT id FROM knowledge_spaces WHERE brain_id=? AND slug=? AND deleted_at IS NULL",
            (self.brain_id, slug),
        ).fetchone()
        if row:
            return str(row["id"])
        space_id = new_id("space")
        connection.execute(
            "INSERT INTO knowledge_spaces(id, brain_id, slug, display_name, visibility, "
            "source_policy, created_at) VALUES(?,?,?,?,?,?,?)",
            (
                space_id,
                self.brain_id,
                slug,
                display_name,
                visibility,
                source_policy,
                utc_now(),
            ),
        )
        return space_id

    def space_id(
        self, slug: str, *, connection: sqlite3.Connection | None = None
    ) -> str:
        def find(conn: sqlite3.Connection) -> str:
            row = conn.execute(
                "SELECT id FROM knowledge_spaces WHERE brain_id=? AND slug=? AND deleted_at IS NULL",
                (self.brain_id, slug),
            ).fetchone()
            if not row:
                raise PermissionError(
                    f"Cortex knowledge space is not authorized: {slug}"
                )
            return str(row["id"])

        if connection is not None:
            return find(connection)
        with self.connect() as conn:
            return find(conn)

    def ensure_principal(
        self,
        principal_type: str,
        external_id: str,
        *,
        display_name: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> str:
        if not principal_type.strip() or not external_id.strip():
            raise ValueError("principal_type and external_id are required")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT id FROM principals WHERE brain_id=? AND principal_type=? AND external_id=?",
                (self.brain_id, principal_type, external_id),
            ).fetchone()
            if row:
                return str(row["id"])
            principal_id = new_id("principal")
            connection.execute(
                "INSERT INTO principals(id, brain_id, principal_type, external_id, display_name, "
                "metadata_json, created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    principal_id,
                    self.brain_id,
                    principal_type,
                    external_id,
                    display_name or None,
                    _json(sanitize_for_storage(dict(metadata or {}))),
                    utc_now(),
                ),
            )
            return principal_id

    def ensure_session(
        self,
        session_id: str,
        *,
        parent_session_id: str = "",
        logical_conversation_id: str = "",
        title: str = "",
        workspace: str = "",
    ) -> None:
        if not session_id:
            raise ValueError("session_id is required")
        stored_title = (
            str(sanitize_for_storage(title)) if self.redact_secrets else str(title)
        )
        stored_workspace = (
            str(sanitize_for_storage(workspace))
            if self.redact_secrets
            else str(workspace)
        )
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO sessions(id, brain_id, logical_conversation_id, parent_session_id, "
                "title, workspace, state, started_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET updated_at=excluded.updated_at, "
                "title=COALESCE(NULLIF(excluded.title,''), sessions.title)",
                (
                    session_id,
                    self.brain_id,
                    logical_conversation_id or session_id,
                    parent_session_id or None,
                    stored_title or None,
                    stored_workspace or None,
                    "active",
                    now,
                    now,
                ),
            )

    def session_lineage(self, session_id: str) -> dict[str, str]:
        """Return brain-scoped physical/logical lineage for a Cortex session."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, logical_conversation_id, parent_session_id, state "
                "FROM sessions WHERE id=? AND brain_id=?",
                (session_id, self.brain_id),
            ).fetchone()
        if row is None:
            return {}
        return {
            "session_id": str(row["id"]),
            "logical_conversation_id": str(row["logical_conversation_id"]),
            "parent_session_id": str(row["parent_session_id"] or ""),
            "state": str(row["state"]),
        }

    def reopen_session(self, session_id: str) -> None:
        """Reactivate one resumable physical segment without changing lineage."""

        with self.transaction() as connection:
            row = connection.execute(
                "SELECT state FROM sessions WHERE id=? AND brain_id=?",
                (session_id, self.brain_id),
            ).fetchone()
            if row is None:
                raise LookupError("Cortex session was not found")
            if str(row["state"]) == "deleted":
                raise PermissionError("deleted Cortex sessions cannot be resumed")
            connection.execute(
                "UPDATE sessions SET state='active', finalized_at=NULL, "
                "evidence_hash=NULL, updated_at=? WHERE id=? AND brain_id=?",
                (utc_now(), session_id, self.brain_id),
            )

    def find_evidence_by_source_row(
        self,
        session_id: str,
        *,
        source_type: str,
        source_row_id: int,
    ) -> str | None:
        """Resolve already-captured evidence by immutable SessionDB row id."""

        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, metadata_json FROM evidence_items WHERE brain_id=? "
                "AND session_id=? AND source_type=? AND tombstoned_at IS NULL",
                (self.brain_id, session_id, source_type),
            ).fetchall()
        wanted = str(source_row_id)
        fallback: str | None = None
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(metadata, Mapping):
                continue
            if metadata.get("source_row_id") is not None:
                if wanted == str(metadata["source_row_id"]):
                    return str(row["id"])
                continue
            raw_candidates = metadata.get("source_row_ids", [])
            candidates = (
                {str(value) for value in raw_candidates}
                if isinstance(raw_candidates, list)
                else set()
            )
            if wanted in candidates and fallback is None:
                fallback = str(row["id"])
        return fallback

    def find_evidence_by_tool_call(
        self,
        session_id: str,
        *,
        source_type: str,
        tool_call_id: str,
    ) -> str | None:
        """Resolve an already-captured tool row by protocol call identity."""

        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, metadata_json FROM evidence_items WHERE brain_id=? "
                "AND session_id=? AND source_type=? AND tombstoned_at IS NULL",
                (self.brain_id, session_id, source_type),
            ).fetchall()
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(metadata, Mapping) and str(
                metadata.get("tool_call_id") or ""
            ) == str(tool_call_id):
                return str(row["id"])
        return None

    def append_evidence(self, session_id: str, evidence: EvidenceInput) -> str:
        content = evidence.content[:2_000_000]
        sensitivity = _classified_evidence_sensitivity(content, evidence.sensitivity)
        if self.redact_secrets:
            content = str(sanitize_for_storage(content))
        content_hash = stable_hash(content)
        occurred_at = evidence.occurred_at or utc_now()
        evidence_id = new_id("evidence")
        with self.transaction() as connection:
            self.ensure_session_in_transaction(connection, session_id)
            space_id = self.space_id(evidence.knowledge_space, connection=connection)
            try:
                connection.execute(
                    "INSERT INTO evidence_items(id, brain_id, knowledge_space_id, session_id, "
                    "source_type, source_locator, actor_principal_id, content, content_hash, "
                    "occurred_at, ingested_at, sensitivity, retention_class, parent_evidence_id, "
                    "metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        evidence_id,
                        self.brain_id,
                        space_id,
                        session_id,
                        evidence.source_type,
                        evidence.source_locator,
                        evidence.actor_principal_id,
                        content,
                        content_hash,
                        occurred_at,
                        utc_now(),
                        sensitivity,
                        evidence.retention_class,
                        evidence.parent_evidence_id,
                        _json(sanitize_for_storage(evidence.metadata)),
                    ),
                )
            except sqlite3.IntegrityError:
                row = connection.execute(
                    "SELECT id FROM evidence_items WHERE brain_id=? AND source_type=? "
                    "AND source_locator=? AND content_hash=?",
                    (
                        self.brain_id,
                        evidence.source_type,
                        evidence.source_locator,
                        content_hash,
                    ),
                ).fetchone()
                if not row:
                    raise
                return str(row["id"])
            # A genuinely new row after a prior boundary means this physical
            # session has been resumed. Keep the already queued boundary job
            # immutable (it carries an evidence-ID snapshot), while preventing
            # recovery from treating the newly active lineage as terminal.
            connection.execute(
                "UPDATE sessions SET state='active', finalized_at=NULL, "
                "evidence_hash=NULL, updated_at=? WHERE id=? AND brain_id=? "
                "AND state IN ('finalized','reset','interrupted')",
                (utc_now(), session_id, self.brain_id),
            )
            if self._fts_available:
                connection.execute(
                    "INSERT INTO evidence_fts(id, content) VALUES(?,?)",
                    (evidence_id, content),
                )
        return evidence_id

    def ensure_session_in_transaction(
        self, connection: sqlite3.Connection, session_id: str
    ) -> None:
        now = utc_now()
        connection.execute(
            "INSERT OR IGNORE INTO sessions(id, brain_id, logical_conversation_id, state, "
            "started_at, updated_at) VALUES(?,?,?,?,?,?)",
            (session_id, self.brain_id, session_id, "active", now, now),
        )
        owner = connection.execute(
            "SELECT brain_id, state FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        if not owner or owner["brain_id"] != self.brain_id:
            raise PermissionError("session does not belong to this Cortex brain")
        if owner["state"] == "deleted":
            raise PermissionError("deleted Cortex sessions cannot accept new evidence")

    def next_turn_key(
        self, session_id: str, user_content: str, assistant_content: str
    ) -> str:
        """Return a bounded unique key for legacy callers without a turn id."""
        digest = stable_hash(user_content, assistant_content)[:16]
        with self.connect() as connection:
            count = int(
                connection.execute(
                    "SELECT COUNT(*) AS n FROM evidence_items WHERE brain_id=? AND session_id=? "
                    "AND source_locator LIKE ?",
                    (
                        self.brain_id,
                        session_id,
                        f"{session_id}:turn:legacy-{digest}-%:user",
                    ),
                ).fetchone()["n"]
            )
        return f"legacy-{digest}-{count + 1}"

    def add_observation(
        self,
        *,
        session_id: str,
        kind: str,
        text: str,
        evidence_ids: Sequence[str],
        knowledge_space: str = "personal",
        processing_state: str = "pending",
        epistemic_status: str = "reported",
        idempotency_key: str = "",
        valid_from: str | None = None,
        valid_until: str | None = None,
        entity_candidates: Sequence[Mapping[str, Any]] = (),
    ) -> str:
        stored_text = str(sanitize_for_storage(text)) if self.redact_secrets else text
        normalized = " ".join(stored_text.split()).strip()
        if not normalized or not evidence_ids:
            raise ValueError("observation text and evidence_ids are required")
        key = idempotency_key or stable_hash(
            self.brain_id, session_id, kind, normalized, *sorted(evidence_ids)
        )
        observation_id = new_id("observation")
        with self.transaction() as connection:
            space_id = self.space_id(knowledge_space, connection=connection)
            self._validate_evidence_ids(
                connection, evidence_ids, knowledge_space_id=space_id
            )
            connection.execute(
                "INSERT OR IGNORE INTO observations(id, brain_id, knowledge_space_id, session_id, "
                "kind, normalized_text, evidence_ids_json, entity_candidates_json, valid_from, "
                "valid_until, processing_state, epistemic_status, idempotency_key, created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    observation_id,
                    self.brain_id,
                    space_id,
                    session_id,
                    kind,
                    normalized,
                    _json(list(evidence_ids)),
                    _json(sanitize_for_storage(list(entity_candidates))),
                    valid_from,
                    valid_until,
                    processing_state,
                    epistemic_status,
                    key,
                    utc_now(),
                ),
            )
            row = connection.execute(
                "SELECT id FROM observations WHERE idempotency_key=?", (key,)
            ).fetchone()
            return str(row["id"])

    def append_work_event(
        self,
        *,
        session_id: str,
        event_type: str,
        summary: str,
        evidence_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        occurred_at: str | None = None,
    ) -> str:
        """Record a replay-idempotent, non-payload work timeline event."""
        normalized_type = "_".join(str(event_type).strip().lower().split())[:100]
        stored_summary = (
            str(sanitize_for_storage(summary)) if self.redact_secrets else str(summary)
        )
        normalized_summary = " ".join(stored_summary.split()).strip()[:1_000]
        if not normalized_type or not normalized_summary:
            raise ValueError("work event type and summary are required")
        event_id = (
            "work_"
            + stable_hash(
                self.brain_id,
                session_id,
                normalized_type,
                evidence_id or "",
                normalized_summary,
            )[:32]
        )
        with self.transaction() as connection:
            self.ensure_session_in_transaction(connection, session_id)
            if evidence_id:
                self._validate_evidence_ids(connection, [evidence_id])
            connection.execute(
                "INSERT OR IGNORE INTO work_events(id, brain_id, session_id, event_type, "
                "summary, evidence_id, metadata_json, occurred_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    event_id,
                    self.brain_id,
                    session_id,
                    normalized_type,
                    normalized_summary,
                    evidence_id,
                    _json(sanitize_for_storage(dict(metadata or {}))),
                    occurred_at or utc_now(),
                ),
            )
        return event_id

    def _validate_evidence_ids(
        self,
        connection: sqlite3.Connection,
        evidence_ids: Sequence[str],
        *,
        knowledge_space_id: str | None = None,
    ) -> None:
        placeholders = ",".join("?" for _ in evidence_ids)
        space_clause = " AND knowledge_space_id=?" if knowledge_space_id else ""
        params: tuple[Any, ...] = (self.brain_id, *evidence_ids)
        if knowledge_space_id:
            params = (*params, knowledge_space_id)
        rows = connection.execute(
            f"SELECT id FROM evidence_items WHERE brain_id=? AND tombstoned_at IS NULL "
            f"AND id IN ({placeholders}){space_clause}",
            params,
        ).fetchall()
        if {row["id"] for row in rows} != set(evidence_ids):
            raise PermissionError(
                "observation referenced missing or unauthorized evidence"
            )

    def promote_memory(
        self,
        *,
        statement: str,
        kind: str,
        evidence_ids: Sequence[str],
        knowledge_space: str = "personal",
        epistemic_status: str = "reported",
        protected: bool = False,
        valid_from: str | None = None,
        valid_until: str | None = None,
        job_id: str | None = None,
        model: str = "",
        prompt_version: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> tuple[str, bool]:
        stored_statement = (
            str(sanitize_for_storage(statement)) if self.redact_secrets else statement
        )
        canonical = " ".join(stored_statement.split()).strip()
        if not canonical or not evidence_ids:
            raise ValueError("durable memory requires a statement and evidence")
        now = utc_now()
        statement_hash = stable_hash(canonical.lower())
        with self.transaction() as connection:
            space_id = self.space_id(knowledge_space, connection=connection)
            self._validate_evidence_ids(
                connection, evidence_ids, knowledge_space_id=space_id
            )
            existing = connection.execute(
                "SELECT id FROM memory_records WHERE brain_id=? AND knowledge_space_id=? "
                "AND statement_hash=? AND status='active' AND deleted_at IS NULL",
                (self.brain_id, space_id, statement_hash),
            ).fetchone()
            if existing:
                memory_id = str(existing["id"])
                connection.execute(
                    "UPDATE memory_records SET last_confirmed_at=?, updated_at=? WHERE id=?",
                    (now, now, memory_id),
                )
                created = False
            else:
                memory_id = new_id("memory")
                connection.execute(
                    "INSERT INTO memory_records(id, brain_id, knowledge_space_id, kind, "
                    "canonical_statement, statement_hash, status, epistemic_status, valid_from, "
                    "valid_until, first_seen_at, last_confirmed_at, created_by_job_id, "
                    "created_by_model, prompt_version, protected, metadata_json, created_at, updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        memory_id,
                        self.brain_id,
                        space_id,
                        kind,
                        canonical,
                        statement_hash,
                        "active",
                        epistemic_status,
                        valid_from,
                        valid_until,
                        now,
                        now,
                        job_id,
                        model or None,
                        prompt_version or None,
                        int(protected),
                        _json(sanitize_for_storage(dict(metadata or {}))),
                        now,
                        now,
                    ),
                )
                if self._fts_available:
                    connection.execute(
                        "INSERT INTO memory_fts(id, content) VALUES(?,?)",
                        (memory_id, canonical),
                    )
                created = True
            connection.executemany(
                "INSERT OR IGNORE INTO memory_evidence(memory_id, evidence_id) VALUES(?,?)",
                [(memory_id, evidence_id) for evidence_id in evidence_ids],
            )
            return memory_id, created

    def supersede_memory(
        self,
        old_memory_id: str,
        *,
        statement: str,
        kind: str,
        evidence_ids: Sequence[str],
        knowledge_space: str = "personal",
        epistemic_status: str = "reported",
        job_id: str | None = None,
        model: str = "",
        prompt_version: str = "",
    ) -> str:
        new_memory_id, _ = self.promote_memory(
            statement=statement,
            kind=kind,
            evidence_ids=evidence_ids,
            knowledge_space=knowledge_space,
            epistemic_status=epistemic_status,
            job_id=job_id,
            model=model,
            prompt_version=prompt_version,
        )
        if new_memory_id == old_memory_id:
            return old_memory_id
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT id FROM memory_records WHERE id=? AND brain_id=? AND status='active'",
                (old_memory_id, self.brain_id),
            ).fetchone()
            if not row:
                raise LookupError("active memory to supersede was not found")
            connection.execute(
                "UPDATE memory_records SET status='superseded', valid_until=COALESCE(valid_until, ?), "
                "superseded_by_id=?, updated_at=? WHERE id=?",
                (now, new_memory_id, now, old_memory_id),
            )
        return new_memory_id

    def upsert_entity(
        self,
        *,
        entity_type: str,
        canonical_name: str,
        knowledge_space: str = "personal",
        description: str = "",
        aliases: Sequence[str] = (),
        evidence_id: str | None = None,
        evidence_ids: Sequence[str] = (),
    ) -> tuple[str, bool]:
        stored_name = (
            str(sanitize_for_storage(canonical_name))
            if self.redact_secrets
            else canonical_name
        )
        stored_description = (
            str(sanitize_for_storage(description))
            if self.redact_secrets
            else description
        )
        stored_aliases = tuple(
            str(sanitize_for_storage(alias)) if self.redact_secrets else alias
            for alias in aliases
        )
        normalized = " ".join(stored_name.lower().split())
        if not normalized:
            raise ValueError("canonical_name is required")
        support_ids = tuple(
            dict.fromkeys(
                value
                for value in (evidence_id, *evidence_ids)
                if value is not None and str(value).strip()
            )
        )
        now = utc_now()
        with self.transaction() as connection:
            space_id = self.space_id(knowledge_space, connection=connection)
            if support_ids:
                self._validate_evidence_ids(
                    connection, support_ids, knowledge_space_id=space_id
                )
            row = connection.execute(
                "SELECT id, deleted_at FROM entities WHERE brain_id=? AND knowledge_space_id=? "
                "AND entity_type=? AND normalized_name=?",
                (self.brain_id, space_id, entity_type, normalized),
            ).fetchone()
            if row:
                entity_id = str(row["id"])
                connection.execute(
                    "UPDATE entities SET last_seen_at=?, deleted_at=NULL, "
                    "description=COALESCE(NULLIF(?,''),description) "
                    "WHERE id=?",
                    (now, stored_description, entity_id),
                )
                created = row["deleted_at"] is not None
            else:
                entity_id = new_id("entity")
                connection.execute(
                    "INSERT INTO entities(id, brain_id, knowledge_space_id, entity_type, "
                    "canonical_name, normalized_name, description, first_seen_at, last_seen_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        entity_id,
                        self.brain_id,
                        space_id,
                        entity_type,
                        stored_name.strip(),
                        normalized,
                        stored_description or None,
                        now,
                        now,
                    ),
                )
                created = True
            if support_ids:
                connection.executemany(
                    "INSERT OR IGNORE INTO entity_evidence(entity_id, evidence_id) "
                    "VALUES(?,?)",
                    [(entity_id, support_id) for support_id in support_ids],
                )
            alias_values = sorted(
                {stored_name, *stored_aliases},
                key=lambda item: " ".join(str(item).lower().split()),
            )
            for alias in alias_values:
                normalized_alias = " ".join(alias.lower().split())
                if normalized_alias:
                    connection.execute(
                        "INSERT OR IGNORE INTO entity_aliases(entity_id, knowledge_space_id, alias, "
                        "normalized_alias, evidence_id, deleted_at) VALUES(?,?,?,?,?,NULL)",
                        (
                            entity_id,
                            space_id,
                            alias.strip(),
                            normalized_alias,
                            support_ids[0] if support_ids else None,
                        ),
                    )
                    connection.execute(
                        "UPDATE entity_aliases SET alias=?, deleted_at=NULL "
                        "WHERE entity_id=? AND knowledge_space_id=? AND normalized_alias=?",
                        (alias.strip(), entity_id, space_id, normalized_alias),
                    )
                    if support_ids:
                        connection.executemany(
                            "INSERT OR IGNORE INTO entity_alias_evidence("
                            "entity_id, knowledge_space_id, normalized_alias, evidence_id) "
                            "VALUES(?,?,?,?)",
                            [
                                (entity_id, space_id, normalized_alias, support_id)
                                for support_id in support_ids
                            ],
                        )
            self._refresh_entity_fts_in_transaction(connection, entity_id)
            return entity_id, created

    def _refresh_entity_fts_in_transaction(
        self, connection: sqlite3.Connection, entity_id: str
    ) -> None:
        if not self._fts_available:
            return
        connection.execute("DELETE FROM entity_fts WHERE id=?", (entity_id,))
        indexed = connection.execute(
            "SELECT canonical_name, description FROM entities "
            "WHERE id=? AND brain_id=? AND deleted_at IS NULL",
            (entity_id, self.brain_id),
        ).fetchone()
        if not indexed:
            return
        indexed_aliases = " ".join(
            str(item["alias"])
            for item in connection.execute(
                "SELECT alias FROM entity_aliases WHERE entity_id=? "
                "AND deleted_at IS NULL ORDER BY normalized_alias",
                (entity_id,),
            ).fetchall()
        )
        connection.execute(
            "INSERT INTO entity_fts(id, content) VALUES(?,?)",
            (
                entity_id,
                f"{indexed['canonical_name']} {indexed['description'] or ''} "
                f"{indexed_aliases}".strip(),
            ),
        )

    def upsert_relation(
        self,
        *,
        subject_entity_id: str,
        predicate: str,
        object_entity_id: str,
        evidence_ids: Sequence[str],
        knowledge_space: str = "personal",
        epistemic_status: str = "reported",
        valid_from: str | None = None,
        valid_until: str | None = None,
        derived_by: str = "cortex",
    ) -> tuple[str, bool]:
        normalized_predicate = "_".join(str(predicate).strip().lower().split())
        if not normalized_predicate or len(normalized_predicate) > 100:
            raise ValueError("relation predicate is required and bounded")
        support_ids = tuple(dict.fromkeys(str(value) for value in evidence_ids))
        if not support_ids:
            raise ValueError("relation requires evidence")
        if subject_entity_id == object_entity_id:
            raise ValueError("relation endpoints must be distinct")
        now = utc_now()
        with self.transaction() as connection:
            space_id = self.space_id(knowledge_space, connection=connection)
            self._validate_evidence_ids(
                connection, support_ids, knowledge_space_id=space_id
            )
            entities = connection.execute(
                "SELECT id FROM entities WHERE brain_id=? AND knowledge_space_id=? "
                "AND id IN (?,?) AND deleted_at IS NULL",
                (self.brain_id, space_id, subject_entity_id, object_entity_id),
            ).fetchall()
            if {row["id"] for row in entities} != {subject_entity_id, object_entity_id}:
                raise PermissionError(
                    "relation endpoints must exist in the authorized space"
                )
            row = connection.execute(
                "SELECT id FROM relations WHERE brain_id=? AND knowledge_space_id=? "
                "AND subject_entity_id=? AND predicate=? AND object_entity_id=? "
                "AND valid_from IS ?",
                (
                    self.brain_id,
                    space_id,
                    subject_entity_id,
                    normalized_predicate,
                    object_entity_id,
                    valid_from,
                ),
            ).fetchone()
            if row:
                relation_id = str(row["id"])
                connection.execute(
                    "UPDATE relations SET updated_at=?, valid_until=COALESCE(?, valid_until), "
                    "status='active' WHERE id=?",
                    (now, valid_until, relation_id),
                )
                created = False
            else:
                relation_id = new_id("relation")
                connection.execute(
                    "INSERT INTO relations(id, brain_id, knowledge_space_id, subject_entity_id, "
                    "predicate, object_entity_id, epistemic_status, valid_from, valid_until, "
                    "derived_by, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        relation_id,
                        self.brain_id,
                        space_id,
                        subject_entity_id,
                        normalized_predicate,
                        object_entity_id,
                        epistemic_status,
                        valid_from,
                        valid_until,
                        derived_by,
                        now,
                        now,
                    ),
                )
                created = True
            connection.executemany(
                "INSERT OR IGNORE INTO relation_evidence(relation_id, evidence_id) VALUES(?,?)",
                [(relation_id, support_id) for support_id in support_ids],
            )
            return relation_id, created

    def finalize_session(
        self,
        session_id: str,
        *,
        state: str = "finalized",
        summary: str = "",
        enqueue_distill: bool = True,
        capture_callback: Callable[[], Any] | None = None,
        commit_callback: Callable[[sqlite3.Connection], Any] | None = None,
    ) -> str:
        if state not in FINAL_SESSION_STATES:
            raise ValueError("invalid final session state")
        stored_summary = (
            str(sanitize_for_storage(summary)) if self.redact_secrets else summary
        )[:4_000]
        marker = self._boundary_marker_path(session_id)
        atomic_switch = commit_callback is not None
        # The preliminary read lets us persist intent even when BEGIN IMMEDIATE
        # itself fails. The process-local lock closes the ordinary append race;
        # the transaction recomputes the base identity to close cross-process
        # races before invoking the fallible capture callback.
        with self._write_lock:
            try:
                base_spec = self.lineage_distill_spec(session_id)
                marker = self._persist_boundary_marker(
                    session_id,
                    state=state,
                    enqueue_distill=enqueue_distill,
                    summary=stored_summary,
                    base_spec=base_spec,
                )
                with self.transaction() as connection:
                    base_spec = self._lineage_distill_spec_in_transaction(
                        connection, session_id
                    )
                    marker = self._persist_boundary_marker(
                        session_id,
                        state=state,
                        enqueue_distill=enqueue_distill,
                        summary=stored_summary,
                        base_spec=base_spec,
                    )
                    if capture_callback is not None:
                        capture_callback()
                    prepared_spec = self._lineage_distill_spec_in_transaction(
                        connection, session_id
                    )
                    marker = self._persist_boundary_marker(
                        session_id,
                        state=state,
                        enqueue_distill=enqueue_distill,
                        summary=stored_summary,
                        base_spec=base_spec,
                        prepared_spec=prepared_spec,
                    )
                    evidence_hash = self._finalize_session_in_transaction(
                        connection,
                        prepared_spec,
                        state=state,
                        summary=stored_summary,
                        enqueue_distill=enqueue_distill,
                    )
                    if commit_callback is not None:
                        commit_callback(connection)
            except BaseException as exc:
                if atomic_switch or isinstance(exc, LookupError):
                    # Any exception that unwinds an atomic switch is a
                    # definitive coordinated abort: the caller still owns the
                    # unchanged route, so replaying its marker later would
                    # finalize a conversation the caller was told remained
                    # active. A genuine process death never executes this
                    # handler and intentionally leaves the durable marker for
                    # startup recovery.
                    self._clear_boundary_marker(marker)
                raise
        self._clear_boundary_marker(marker)
        return evidence_hash

    def _finalize_session_in_transaction(
        self,
        connection: sqlite3.Connection,
        spec: Mapping[str, Any],
        *,
        state: str,
        summary: str,
        enqueue_distill: bool,
    ) -> str:
        """Apply one already-bound boundary specification atomically."""

        now = utc_now()
        input_data = spec.get("input_data")
        if not isinstance(input_data, Mapping):
            raise ValueError("invalid Cortex boundary specification")
        evidence_hash = str(input_data.get("evidence_hash") or "")
        logical_conversation_id = str(input_data.get("logical_conversation_id") or "")
        if state not in FINAL_SESSION_STATES or not logical_conversation_id:
            raise ValueError("invalid Cortex session boundary")
        stored_summary = (
            str(sanitize_for_storage(summary)) if self.redact_secrets else summary
        )[:4_000]
        cursor = connection.execute(
            "UPDATE sessions SET state=?, summary=COALESCE(NULLIF(?,''), summary), "
            "evidence_hash=?, finalized_at=?, updated_at=? "
            "WHERE brain_id=? AND logical_conversation_id=? AND state!='deleted'",
            (
                state,
                stored_summary,
                evidence_hash,
                now,
                now,
                self.brain_id,
                logical_conversation_id,
            ),
        )
        if cursor.rowcount < 1:
            raise LookupError("Cortex session was not found")
        evidence_ids = input_data.get("evidence_ids")
        if (
            state != "deleted"
            and enqueue_distill
            and isinstance(evidence_ids, list)
            and evidence_ids
        ):
            admission_id = self._admit_session_distill_in_transaction(
                connection,
                spec,
                final_state=state,
                admitted_at=now,
            )
            self._enqueue_session_distill_root_in_transaction(connection, admission_id)
        return evidence_hash

    def _admit_session_distill_in_transaction(
        self,
        connection: sqlite3.Connection,
        spec: Mapping[str, Any],
        *,
        final_state: str,
        admitted_at: str,
    ) -> str:
        """Persist the immutable proof that authorizes one semantic root job."""

        input_data = spec.get("input_data")
        if not isinstance(input_data, Mapping):
            raise ValueError("invalid Cortex boundary specification")
        snapshot = dict(input_data)
        logical_id = str(snapshot.get("logical_conversation_id") or "")
        tip_session_id = str(snapshot.get("session_id") or "")
        evidence_hash = str(snapshot.get("evidence_hash") or "")
        input_hash = str(spec.get("input_hash") or "")
        evidence_ids = snapshot.get("evidence_ids")
        if (
            final_state not in {"finalized", "reset", "interrupted"}
            or int(snapshot.get("distill_version") or 0) != 2
            or not logical_id
            or not tip_session_id
            or len(evidence_hash) != 64
            or len(input_hash) != 64
            or not isinstance(evidence_ids, list)
            or not evidence_ids
            or input_hash
            != stable_hash(logical_id, evidence_hash, "session_distill:v2")
        ):
            raise ValueError("invalid Cortex session-distill admission")
        snapshot_json = _json(snapshot)
        admission_id = new_id("admission")
        connection.execute(
            "INSERT OR IGNORE INTO session_distill_admissions("
            "id, brain_id, logical_conversation_id, tip_session_id, final_state, "
            "canonical_input_hash, evidence_hash, snapshot_json, admitted_at) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            (
                admission_id,
                self.brain_id,
                logical_id,
                tip_session_id,
                final_state,
                input_hash,
                evidence_hash,
                snapshot_json,
                admitted_at,
            ),
        )
        row = connection.execute(
            "SELECT * FROM session_distill_admissions WHERE brain_id=? "
            "AND canonical_input_hash=?",
            (self.brain_id, input_hash),
        ).fetchone()
        if row is None:
            raise RuntimeError("Cortex session-distill admission was not persisted")
        if row["revoked_at"] is not None:
            raise PermissionError(
                "Cortex session-distill admission was revoked by privacy deletion"
            )
        # Repeating the same boundary is idempotent. A later physical tip can
        # have the same evidence identity, so the first immutable snapshot is
        # retained; only its semantic identity must match.
        if (
            str(row["logical_conversation_id"]) != logical_id
            or str(row["evidence_hash"]) != evidence_hash
            or str(row["canonical_input_hash"]) != input_hash
        ):
            raise ValueError("conflicting Cortex session-distill admission")
        return str(row["id"])

    def lineage_distill_spec(self, session_id: str) -> dict[str, Any]:
        """Return the canonical v2 distillation identity for a physical tip."""

        with self.connect() as connection:
            return self._lineage_distill_spec_in_transaction(connection, session_id)

    def _lineage_distill_spec_in_transaction(
        self,
        connection: sqlite3.Connection,
        session_id: str,
    ) -> dict[str, Any]:
        tip = connection.execute(
            "SELECT logical_conversation_id FROM sessions WHERE id=? AND brain_id=?",
            (session_id, self.brain_id),
        ).fetchone()
        if tip is None:
            raise LookupError("Cortex session was not found")
        logical_conversation_id = str(tip["logical_conversation_id"] or session_id)
        lineage_rows = connection.execute(
            "SELECT id FROM sessions WHERE brain_id=? AND logical_conversation_id=? "
            "AND state!='deleted' ORDER BY started_at, id",
            (self.brain_id, logical_conversation_id),
        ).fetchall()
        session_ids = tuple(str(row["id"]) for row in lineage_rows)
        if not session_ids:
            raise LookupError("Cortex session lineage was not found")
        placeholders = ",".join("?" for _ in session_ids)
        evidence_rows = connection.execute(
            "SELECT id, session_id, content_hash FROM evidence_items "
            f"WHERE brain_id=? AND session_id IN ({placeholders}) "
            "AND tombstoned_at IS NULL ORDER BY occurred_at, ingested_at, id",
            (self.brain_id, *session_ids),
        ).fetchall()
        evidence_hash = stable_hash(
            *(
                f"{row['session_id']}:{row['id']}:{row['content_hash']}"
                for row in evidence_rows
            )
        )
        input_hash = stable_hash(
            logical_conversation_id,
            evidence_hash,
            "session_distill:v2",
        )
        return {
            "input_hash": input_hash,
            "input_data": {
                "session_id": session_id,
                "logical_conversation_id": logical_conversation_id,
                "session_ids": list(session_ids),
                # This immutable boundary snapshot is the semantic scope. A
                # session may be resumed before the asynchronous worker runs;
                # new evidence must wait for that later logical finalization.
                "evidence_ids": [str(row["id"]) for row in evidence_rows],
                "evidence_hash": evidence_hash,
                "distill_version": 2,
            },
        }

    def reconcile_rewind(
        self,
        session_id: str,
        source_row_ids: Sequence[int | str],
        *,
        all_session_evidence: bool = False,
        _connection: sqlite3.Connection | None = None,
    ) -> int:
        """Exclude evidence and derived records removed by a session rewind.

        Rewound content remains as an auditable tombstone, but it is removed
        from automatic recall, session-end promotion, FTS, and active memory state.
        Matching uses stable SessionDB row locators and the immutable row-ID
        provenance captured with completed turns.
        """
        row_ids = {str(value) for value in source_row_ids if str(value).strip()}
        if not session_id or (not row_ids and not all_session_evidence):
            return 0
        now = utc_now()
        transaction = (
            nullcontext(_connection)
            if _connection is not None
            else self.transaction()
        )
        with transaction as connection:
            assert connection is not None
            session = connection.execute(
                "SELECT id FROM sessions WHERE id=? AND brain_id=?",
                (session_id, self.brain_id),
            ).fetchone()
            if not session:
                return 0
            # A finalized session summary and its boundary hash describe the
            # pre-rewind transcript even when the rewound SessionDB rows were
            # never captured as Cortex evidence. Reopen the physical segment
            # and remove those derived projections before matching evidence so
            # an unmatched-but-valid rewind still fails closed in Starmap.
            connection.execute(
                "UPDATE sessions SET state='active', summary=NULL, "
                "evidence_hash=NULL, finalized_at=NULL, updated_at=? "
                "WHERE id=? AND brain_id=? AND state!='deleted'",
                (now, session_id, self.brain_id),
            )
            rows = connection.execute(
                "SELECT id, source_locator, metadata_json FROM evidence_items "
                "WHERE brain_id=? AND session_id=? AND tombstoned_at IS NULL",
                (self.brain_id, session_id),
            ).fetchall()
            affected: set[str] = set()
            for row in rows:
                locator = str(row["source_locator"] or "")
                try:
                    metadata = json.loads(row["metadata_json"] or "{}")
                except (TypeError, json.JSONDecodeError):
                    metadata = {}
                metadata_ids = {
                    str(value)
                    for value in (
                        metadata.get("source_row_ids", [])
                        if isinstance(metadata, Mapping)
                        else []
                    )
                }
                if all_session_evidence or (
                    isinstance(metadata, Mapping)
                    and metadata.get("source_row_id") is not None
                ):
                    if isinstance(metadata, Mapping) and metadata.get(
                        "source_row_id"
                    ) is not None:
                        metadata_ids.add(str(metadata["source_row_id"]))
                locator_match = any(f":row:{row_id}:" in locator for row_id in row_ids)
                if (
                    all_session_evidence
                    or locator_match
                    or metadata_ids.intersection(row_ids)
                ):
                    affected.add(str(row["id"]))
            if not affected:
                return 0

            marks = ",".join("?" for _ in affected)
            ordered = tuple(sorted(affected))
            connection.execute(
                f"UPDATE evidence_items SET tombstoned_at=? WHERE brain_id=? "
                f"AND id IN ({marks})",
                (now, self.brain_id, *ordered),
            )
            if self._fts_available:
                connection.execute(
                    f"DELETE FROM evidence_fts WHERE id IN ({marks})", ordered
                )

            observations = connection.execute(
                "SELECT id, evidence_ids_json FROM observations WHERE brain_id=? "
                "AND session_id=?",
                (self.brain_id, session_id),
            ).fetchall()
            for observation in observations:
                try:
                    supports = {
                        str(value)
                        for value in json.loads(
                            observation["evidence_ids_json"] or "[]"
                        )
                    }
                except (TypeError, json.JSONDecodeError):
                    supports = set()
                if supports.intersection(affected):
                    connection.execute(
                        "UPDATE observations SET processing_state='rewound', "
                        "utility_action='session_reconcile', processed_at=? "
                        "WHERE id=? AND brain_id=?",
                        (now, observation["id"], self.brain_id),
                    )

            memories = connection.execute(
                f"SELECT DISTINCT memory_id FROM memory_evidence WHERE evidence_id IN ({marks})",
                ordered,
            ).fetchall()
            for memory in memories:
                memory_id = str(memory["memory_id"])
                active_support = connection.execute(
                    "SELECT 1 FROM memory_evidence me JOIN evidence_items e "
                    "ON e.id=me.evidence_id WHERE me.memory_id=? AND e.brain_id=? "
                    "AND e.tombstoned_at IS NULL LIMIT 1",
                    (memory_id, self.brain_id),
                ).fetchone()
                if active_support:
                    continue
                connection.execute(
                    "UPDATE memory_records SET status='rewound', "
                    "valid_until=COALESCE(valid_until, ?), updated_at=? "
                    "WHERE id=? AND brain_id=? AND status IN ('active','disputed')",
                    (now, now, memory_id, self.brain_id),
                )
                if self._fts_available:
                    connection.execute(
                        "DELETE FROM memory_fts WHERE id=?", (memory_id,)
                    )

            relations = connection.execute(
                f"SELECT DISTINCT relation_id FROM relation_evidence "
                f"WHERE evidence_id IN ({marks})",
                ordered,
            ).fetchall()
            for relation in relations:
                relation_id = str(relation["relation_id"])
                active_support = connection.execute(
                    "SELECT 1 FROM relation_evidence re JOIN evidence_items e "
                    "ON e.id=re.evidence_id WHERE re.relation_id=? AND e.brain_id=? "
                    "AND e.tombstoned_at IS NULL LIMIT 1",
                    (relation_id, self.brain_id),
                ).fetchone()
                if not active_support:
                    connection.execute(
                        "UPDATE relations SET status='rewound', valid_until=COALESCE(valid_until, ?), "
                        "updated_at=? WHERE id=? AND brain_id=?",
                        (now, now, relation_id, self.brain_id),
                    )

            alias_rows = connection.execute(
                f"SELECT DISTINCT entity_id, knowledge_space_id, normalized_alias "
                f"FROM entity_alias_evidence WHERE evidence_id IN ({marks})",
                ordered,
            ).fetchall()
            aliases_rewound = 0
            for alias_row in alias_rows:
                alias_params = (
                    alias_row["entity_id"],
                    alias_row["knowledge_space_id"],
                    alias_row["normalized_alias"],
                )
                active_support = connection.execute(
                    "SELECT 1 FROM entity_alias_evidence eae "
                    "JOIN evidence_items e ON e.id=eae.evidence_id "
                    "WHERE eae.entity_id=? AND eae.knowledge_space_id=? "
                    "AND eae.normalized_alias=? AND e.brain_id=? "
                    "AND e.tombstoned_at IS NULL LIMIT 1",
                    (*alias_params, self.brain_id),
                ).fetchone()
                if active_support:
                    continue
                cursor = connection.execute(
                    "UPDATE entity_aliases SET deleted_at=? WHERE entity_id=? "
                    "AND knowledge_space_id=? AND normalized_alias=? "
                    "AND deleted_at IS NULL",
                    (now, *alias_params),
                )
                aliases_rewound += max(0, int(cursor.rowcount))

            entity_rows = connection.execute(
                f"SELECT DISTINCT entity_id FROM entity_evidence "
                f"WHERE evidence_id IN ({marks})",
                ordered,
            ).fetchall()
            entities_rewound = 0
            for entity_row in entity_rows:
                entity_id = str(entity_row["entity_id"])
                active_support = connection.execute(
                    "SELECT 1 FROM entity_evidence ee "
                    "JOIN evidence_items e ON e.id=ee.evidence_id "
                    "WHERE ee.entity_id=? AND e.brain_id=? "
                    "AND e.tombstoned_at IS NULL LIMIT 1",
                    (entity_id, self.brain_id),
                ).fetchone()
                if active_support:
                    self._refresh_entity_fts_in_transaction(connection, entity_id)
                    continue
                cursor = connection.execute(
                    "UPDATE entities SET deleted_at=? WHERE id=? AND brain_id=? "
                    "AND deleted_at IS NULL",
                    (now, entity_id, self.brain_id),
                )
                entities_rewound += max(0, int(cursor.rowcount))
                connection.execute(
                    "UPDATE entity_aliases SET deleted_at=COALESCE(deleted_at, ?) "
                    "WHERE entity_id=?",
                    (now, entity_id),
                )
                self._refresh_entity_fts_in_transaction(connection, entity_id)

            personal_space = self.space_id("personal", connection=connection)
            connection.execute(
                "UPDATE compiled_views SET stale=1 WHERE brain_id=? AND knowledge_space_id=?",
                (self.brain_id, personal_space),
            )
            connection.execute(
                "UPDATE communities SET stale=1 WHERE brain_id=? AND knowledge_space_id=?",
                (self.brain_id, personal_space),
            )
            self.audit_in_transaction(
                connection,
                principal_id=None,
                action=(
                    "session.delete_reconcile"
                    if all_session_evidence
                    else "session.rewind_reconcile"
                ),
                resource_type="session",
                resource_id=session_id,
                outcome="succeeded",
                details={
                    "evidence_count": len(affected),
                    "source_row_count": len(row_ids),
                    "entity_count": entities_rewound,
                    "entity_alias_count": aliases_rewound,
                },
            )
            return len(affected)

    def reconcile_session_delete(self, session_id: str) -> int:
        """Revoke semantic work and permanently scrub one session lineage.

        A privacy delete must win before transcript removal. Any actively
        running semantic job makes the operation fail closed; queued work is
        revoked durably so recovery cannot recreate it. Every physical segment
        in the logical compression lineage is reconciled and all Cortex-local
        raw or derived content that can depend on that lineage is erased in the
        same transaction. Only non-content receipts and aggregate audit counts
        remain. This deliberately favors privacy over retaining shared derived
        projections: surviving evidence can deterministically rebuild them.
        """

        if not session_id:
            return 0
        affected = 0
        erased_marker = "[permanently erased by session privacy request]"
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT logical_conversation_id FROM sessions "
                "WHERE id=? AND brain_id=?",
                (session_id, self.brain_id),
            ).fetchone()
            if row is None:
                return 0
            logical_id = str(row["logical_conversation_id"] or session_id)
            running = connection.execute(
                "SELECT 1 FROM cognitive_jobs j "
                "JOIN session_distill_admissions a ON a.id=j.admission_id "
                "WHERE a.brain_id=? AND a.logical_conversation_id=? "
                "AND j.job_type='session_distill' AND j.state='running' LIMIT 1",
                (self.brain_id, logical_id),
            ).fetchone()
            if running is not None:
                raise RuntimeError(
                    "Cortex semantic work is active; retry privacy deletion"
                )
            now = utc_now()
            lineage_ids = tuple(
                str(item["id"])
                for item in connection.execute(
                    "SELECT id FROM sessions WHERE brain_id=? "
                    "AND logical_conversation_id=? ORDER BY started_at, id",
                    (self.brain_id, logical_id),
                ).fetchall()
            )
            if not lineage_ids:
                raise RuntimeError("Cortex session lineage disappeared during deletion")
            lineage_marks = ",".join("?" for _ in lineage_ids)
            evidence_ids = tuple(
                str(item["id"])
                for item in connection.execute(
                    "SELECT id FROM evidence_items WHERE brain_id=? "
                    f"AND session_id IN ({lineage_marks}) ORDER BY id",
                    (self.brain_id, *lineage_ids),
                ).fetchall()
            )
            evidence_marks = ",".join("?" for _ in evidence_ids)
            admission_ids = tuple(
                str(item["id"])
                for item in connection.execute(
                    "SELECT id FROM session_distill_admissions WHERE brain_id=? "
                    "AND logical_conversation_id=? ORDER BY admitted_at, id",
                    (self.brain_id, logical_id),
                ).fetchall()
            )
            admission_marks = ",".join("?" for _ in admission_ids)
            job_ids: tuple[str, ...] = ()
            if admission_ids:
                job_ids = tuple(
                    str(item["id"])
                    for item in connection.execute(
                        "SELECT id FROM cognitive_jobs WHERE brain_id=? "
                        f"AND admission_id IN ({admission_marks}) ORDER BY created_at, id",
                        (self.brain_id, *admission_ids),
                    ).fetchall()
                )

            memory_ids: set[str] = set()
            relation_ids: set[str] = set()
            entity_ids: set[str] = set()
            if evidence_ids:
                memory_ids.update(
                    str(item["memory_id"])
                    for item in connection.execute(
                        "SELECT DISTINCT memory_id FROM memory_evidence "
                        f"WHERE evidence_id IN ({evidence_marks})",
                        evidence_ids,
                    ).fetchall()
                )
                relation_ids.update(
                    str(item["relation_id"])
                    for item in connection.execute(
                        "SELECT DISTINCT relation_id FROM relation_evidence "
                        f"WHERE evidence_id IN ({evidence_marks})",
                        evidence_ids,
                    ).fetchall()
                )
                entity_ids.update(
                    str(item["entity_id"])
                    for item in connection.execute(
                        "SELECT DISTINCT entity_id FROM entity_evidence "
                        f"WHERE evidence_id IN ({evidence_marks})",
                        evidence_ids,
                    ).fetchall()
                )
                entity_ids.update(
                    str(item["entity_id"])
                    for item in connection.execute(
                        "SELECT DISTINCT entity_id FROM entity_alias_evidence "
                        f"WHERE evidence_id IN ({evidence_marks})",
                        evidence_ids,
                    ).fetchall()
                )
                entity_ids.update(
                    str(item["entity_id"])
                    for item in connection.execute(
                        "SELECT DISTINCT entity_id FROM entity_aliases "
                        f"WHERE evidence_id IN ({evidence_marks})",
                        evidence_ids,
                    ).fetchall()
                )

            if memory_ids:
                memory_marks = ",".join("?" for _ in memory_ids)
                for item in connection.execute(
                    "SELECT subject_entity_id, object_entity_id FROM memory_records "
                    f"WHERE brain_id=? AND id IN ({memory_marks})",
                    (self.brain_id, *memory_ids),
                ).fetchall():
                    entity_ids.update(
                        str(value)
                        for value in (
                            item["subject_entity_id"],
                            item["object_entity_id"],
                        )
                        if value
                    )
            if relation_ids:
                relation_marks = ",".join("?" for _ in relation_ids)
                for item in connection.execute(
                    "SELECT subject_entity_id, object_entity_id FROM relations "
                    f"WHERE brain_id=? AND id IN ({relation_marks})",
                    (self.brain_id, *relation_ids),
                ).fetchall():
                    entity_ids.update(
                        (str(item["subject_entity_id"]), str(item["object_entity_id"]))
                    )
            if entity_ids:
                entity_marks = ",".join("?" for _ in entity_ids)
                relation_ids.update(
                    str(item["id"])
                    for item in connection.execute(
                        "SELECT id FROM relations WHERE brain_id=? AND "
                        f"(subject_entity_id IN ({entity_marks}) OR "
                        f"object_entity_id IN ({entity_marks}))",
                        (self.brain_id, *entity_ids, *entity_ids),
                    ).fetchall()
                )

            connection.execute(
                "UPDATE session_distill_admissions SET revoked_at=COALESCE(revoked_at, ?), "
                "revocation_reason=COALESCE(revocation_reason, 'session_deleted'), "
                "snapshot_json='{}' "
                "WHERE brain_id=? AND logical_conversation_id=?",
                (now, self.brain_id, logical_id),
            )
            connection.execute(
                "UPDATE cognitive_jobs SET "
                "state=CASE WHEN state='queued' THEN 'dead_letter' ELSE state END, "
                "completed_at=CASE WHEN state='queued' THEN COALESCE(completed_at, ?) "
                "ELSE completed_at END, "
                "dead_letter_at=CASE WHEN state='queued' THEN COALESCE(dead_letter_at, ?) "
                "ELSE dead_letter_at END, lease_owner=NULL, lease_expires_at=NULL, "
                "heartbeat_at=NULL, model=NULL, prompt_version=NULL, input_json='{}', "
                "output_json='{}', checkpoint_json='{}', error=NULL, updated_at=? "
                "WHERE brain_id=? AND admission_id IN (SELECT id "
                "FROM session_distill_admissions WHERE brain_id=? "
                "AND logical_conversation_id=?)",
                (
                    now,
                    now,
                    now,
                    self.brain_id,
                    self.brain_id,
                    logical_id,
                ),
            )
            if job_ids:
                job_marks = ",".join("?" for _ in job_ids)
                connection.execute(
                    f"DELETE FROM cognitive_job_operations WHERE job_id IN ({job_marks})",
                    job_ids,
                )
                connection.execute(
                    "DELETE FROM health_reports WHERE brain_id=? "
                    f"AND job_id IN ({job_marks})",
                    (self.brain_id, *job_ids),
                )

            for lineage_id in lineage_ids:
                affected += self.reconcile_rewind(
                    lineage_id,
                    (),
                    all_session_evidence=True,
                    _connection=connection,
                )

            if relation_ids:
                relation_marks = ",".join("?" for _ in relation_ids)
                connection.execute(
                    "DELETE FROM relations WHERE brain_id=? "
                    f"AND id IN ({relation_marks})",
                    (self.brain_id, *relation_ids),
                )

            if entity_ids:
                entity_marks = ",".join("?" for _ in entity_ids)
                connection.execute(
                    "DELETE FROM entity_alias_evidence "
                    f"WHERE entity_id IN ({entity_marks})",
                    tuple(entity_ids),
                )
                connection.execute(
                    f"DELETE FROM entity_aliases WHERE entity_id IN ({entity_marks})",
                    tuple(entity_ids),
                )
                connection.execute(
                    f"DELETE FROM entity_evidence WHERE entity_id IN ({entity_marks})",
                    tuple(entity_ids),
                )
                connection.executemany(
                    "UPDATE entities SET entity_type='erased', canonical_name=?, "
                    "normalized_name=?, description=NULL, metadata_json='{}', "
                    "deleted_at=COALESCE(deleted_at, ?) WHERE id=? AND brain_id=?",
                    [
                        (
                            erased_marker,
                            f"erased:{entity_id}",
                            now,
                            entity_id,
                            self.brain_id,
                        )
                        for entity_id in sorted(entity_ids)
                    ],
                )
                if self._fts_available:
                    connection.execute(
                        f"DELETE FROM entity_fts WHERE id IN ({entity_marks})",
                        tuple(entity_ids),
                    )

            if memory_ids:
                memory_marks = ",".join("?" for _ in memory_ids)
                connection.execute(
                    "UPDATE memory_records SET superseded_by_id=NULL "
                    f"WHERE superseded_by_id IN ({memory_marks})",
                    tuple(memory_ids),
                )
                connection.executemany(
                    "UPDATE memory_records SET kind='erased', subject_entity_id=NULL, "
                    "object_entity_id=NULL, canonical_statement=?, statement_hash=?, "
                    "status='deleted', valid_from=NULL, valid_until=?, "
                    "created_by_job_id=NULL, created_by_model=NULL, prompt_version=NULL, "
                    "superseded_by_id=NULL, protected=0, user_visible=0, "
                    "metadata_json='{}', deleted_at=COALESCE(deleted_at, ?), updated_at=? "
                    "WHERE id=? AND brain_id=?",
                    [
                        (
                            erased_marker,
                            stable_hash(erased_marker, memory_id),
                            now,
                            now,
                            now,
                            memory_id,
                            self.brain_id,
                        )
                        for memory_id in sorted(memory_ids)
                    ],
                )
                connection.execute(
                    f"DELETE FROM memory_evidence WHERE memory_id IN ({memory_marks})",
                    tuple(memory_ids),
                )
                if self._fts_available:
                    connection.execute(
                        f"DELETE FROM memory_fts WHERE id IN ({memory_marks})",
                        tuple(memory_ids),
                    )

            observation_ids: set[str] = {
                str(item["id"])
                for item in connection.execute(
                    "SELECT id FROM observations WHERE brain_id=? "
                    f"AND session_id IN ({lineage_marks})",
                    (self.brain_id, *lineage_ids),
                ).fetchall()
            }
            if evidence_ids:
                evidence_id_set = set(evidence_ids)
                for item in connection.execute(
                    "SELECT id, evidence_ids_json FROM observations WHERE brain_id=?",
                    (self.brain_id,),
                ).fetchall():
                    try:
                        cited = {
                            str(value)
                            for value in json.loads(item["evidence_ids_json"] or "[]")
                        }
                    except (TypeError, json.JSONDecodeError):
                        cited = set()
                    if cited.intersection(evidence_id_set):
                        observation_ids.add(str(item["id"]))
            if observation_ids:
                observation_marks = ",".join("?" for _ in observation_ids)
                connection.execute(
                    "DELETE FROM observations WHERE brain_id=? "
                    f"AND id IN ({observation_marks})",
                    (self.brain_id, *observation_ids),
                )

            connection.execute(
                "DELETE FROM work_events WHERE brain_id=? "
                f"AND session_id IN ({lineage_marks})",
                (self.brain_id, *lineage_ids),
            )

            derived_ids = set(evidence_ids).union(memory_ids, entity_ids, relation_ids)
            retrieval_ids: set[str] = set()
            for item in connection.execute(
                "SELECT id, session_id, candidate_ids_json, selected_ids_json "
                "FROM retrieval_runs WHERE brain_id=?",
                (self.brain_id,),
            ).fetchall():
                if str(item["session_id"] or "") in lineage_ids:
                    retrieval_ids.add(str(item["id"]))
                    continue
                referenced: set[str] = set()
                for field in ("candidate_ids_json", "selected_ids_json"):
                    try:
                        referenced.update(
                            str(value) for value in json.loads(item[field] or "[]")
                        )
                    except (TypeError, json.JSONDecodeError):
                        continue
                if referenced.intersection(derived_ids):
                    retrieval_ids.add(str(item["id"]))
            if retrieval_ids:
                retrieval_marks = ",".join("?" for _ in retrieval_ids)
                connection.execute(
                    "DELETE FROM retrieval_runs WHERE brain_id=? "
                    f"AND id IN ({retrieval_marks})",
                    (self.brain_id, *retrieval_ids),
                )

            personal_space = self.space_id("personal", connection=connection)
            connection.execute(
                "DELETE FROM compiled_views WHERE brain_id=? AND knowledge_space_id=?",
                (self.brain_id, personal_space),
            )
            connection.execute(
                "DELETE FROM communities WHERE brain_id=? AND knowledge_space_id=?",
                (self.brain_id, personal_space),
            )

            if evidence_ids:
                connection.execute(
                    "UPDATE evidence_items SET parent_evidence_id=NULL "
                    f"WHERE parent_evidence_id IN ({evidence_marks})",
                    evidence_ids,
                )
                connection.executemany(
                    "UPDATE evidence_items SET source_type='erased', source_locator=?, "
                    "actor_principal_id=NULL, content=?, content_hash=?, "
                    "sensitivity='private', retention_class='erased', "
                    "parent_evidence_id=NULL, metadata_json='{}', "
                    "tombstoned_at=COALESCE(tombstoned_at, ?) "
                    "WHERE id=? AND brain_id=?",
                    [
                        (
                            f"erased:{evidence_id}",
                            erased_marker,
                            stable_hash(erased_marker, evidence_id),
                            now,
                            evidence_id,
                            self.brain_id,
                        )
                        for evidence_id in evidence_ids
                    ],
                )
                if self._fts_available:
                    connection.execute(
                        f"DELETE FROM evidence_fts WHERE id IN ({evidence_marks})",
                        evidence_ids,
                    )

            scrub_resource_ids = set(lineage_ids).union(
                evidence_ids, memory_ids, entity_ids, relation_ids, job_ids, admission_ids
            )
            if scrub_resource_ids:
                resource_marks = ",".join("?" for _ in scrub_resource_ids)
                connection.execute(
                    "UPDATE cortex_audit SET details_json='{}' WHERE brain_id=? "
                    f"AND resource_id IN ({resource_marks})",
                    (self.brain_id, *scrub_resource_ids),
                )

            # Publish the privacy tombstone in the same BEGIN IMMEDIATE that
            # revoked semantic authority and scrubbed every support edge. A
            # concurrent capture/finalize can only run before this transaction
            # (and be included above) or after it (and reject the deleted
            # session); there is no gap where fresh evidence can survive.
            cursor = connection.execute(
                "UPDATE sessions SET state='deleted', title=NULL, summary=NULL, "
                "workspace=NULL, dealership_context=NULL, evidence_hash=NULL, "
                "finalized_at=?, updated_at=? "
                "WHERE brain_id=? AND logical_conversation_id=?",
                (now, now, self.brain_id, logical_id),
            )
            if cursor.rowcount != len(lineage_ids):
                raise RuntimeError(
                    "Cortex session lineage changed during privacy deletion"
                )
            self.audit_in_transaction(
                connection,
                principal_id=None,
                action="session.privacy_delete",
                resource_type="session",
                resource_id=session_id,
                outcome="succeeded",
                details={
                    "session_count": len(lineage_ids),
                    "evidence_count": len(evidence_ids),
                    "memory_count": len(memory_ids),
                    "entity_count": len(entity_ids),
                    "relation_count": len(relation_ids),
                    "observation_count": len(observation_ids),
                    "retrieval_count": len(retrieval_ids),
                    "job_count": len(job_ids),
                },
            )

        # secure_delete clears live cells; checkpoint + VACUUM removes old
        # content from free pages and WAL/SHM sidecars before the transcript
        # database is allowed to complete its own deletion.
        with self.connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("VACUUM")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._secure_sidecars()
        return affected

    def enqueue_job(
        self,
        job_type: str,
        *,
        input_hash: str,
        input_data: Mapping[str, Any],
        scheduled_at: str | None = None,
        max_attempts: int = 5,
    ) -> str:
        if job_type == "session_distill":
            raise ValueError(
                "session_distill requires a durable logical-session admission"
            )
        with self.transaction() as connection:
            return self.enqueue_job_in_transaction(
                connection,
                job_type,
                input_hash=input_hash,
                input_data=input_data,
                scheduled_at=scheduled_at,
                max_attempts=max_attempts,
            )

    def enqueue_job_in_transaction(
        self,
        connection: sqlite3.Connection,
        job_type: str,
        *,
        input_hash: str,
        input_data: Mapping[str, Any],
        scheduled_at: str | None = None,
        max_attempts: int = 5,
    ) -> str:
        if job_type == "session_distill":
            raise ValueError(
                "session_distill requires a durable logical-session admission"
            )
        return self._insert_job_in_transaction(
            connection,
            job_type,
            input_hash=input_hash,
            input_data=input_data,
            scheduled_at=scheduled_at,
            max_attempts=max_attempts,
        )

    def _quarantine_session_distill_job_in_transaction(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        *,
        reason: str,
    ) -> bool:
        """Relabel one invalid semantic row while preserving its history."""

        row = connection.execute(
            "SELECT id, input_hash FROM cognitive_jobs WHERE id=? AND brain_id=? "
            "AND job_type='session_distill'",
            (job_id, self.brain_id),
        ).fetchone()
        if row is None:
            return False
        now = utc_now()
        rekeyed_hash = stable_hash(
            self.brain_id,
            row["id"],
            row["input_hash"],
            "quarantined-session-distill:v2",
        )
        cursor = connection.execute(
            "UPDATE cognitive_jobs SET job_type=?, input_hash=?, state='failed', "
            "error=?, completed_at=COALESCE(completed_at, ?), lease_owner=NULL, "
            "lease_expires_at=NULL, heartbeat_at=?, updated_at=? "
            "WHERE id=? AND brain_id=? AND job_type='session_distill'",
            (
                QUARANTINED_SESSION_DISTILL_JOB_TYPE,
                rekeyed_hash,
                str(sanitize_for_storage(reason))[:2_000],
                now,
                now,
                now,
                job_id,
                self.brain_id,
            ),
        )
        return cursor.rowcount == 1

    def _insert_job_in_transaction(
        self,
        connection: sqlite3.Connection,
        job_type: str,
        *,
        input_hash: str,
        input_data: Mapping[str, Any],
        scheduled_at: str | None = None,
        max_attempts: int = 5,
        admission_id: str | None = None,
        parent_job_id: str | None = None,
        root_job_id: str | None = None,
        job_id: str | None = None,
    ) -> str:
        now = utc_now()
        inserted_job_id = job_id or new_id("job")
        stored_input = (
            dict(input_data)
            if job_type == "session_distill"
            else sanitize_for_storage(dict(input_data))
        )
        stored_input_json = _json(stored_input)
        if job_type == "session_distill":
            existing = connection.execute(
                "SELECT * FROM cognitive_jobs WHERE brain_id=? AND job_type=? "
                "AND input_hash=?",
                (self.brain_id, job_type, input_hash),
            ).fetchone()
            if existing is not None:
                exact_match = (
                    str(existing["admission_id"] or "") == str(admission_id or "")
                    and str(existing["parent_job_id"] or "") == str(parent_job_id or "")
                    and str(existing["root_job_id"] or "") == str(root_job_id or "")
                    and str(existing["input_json"] or "") == stored_input_json
                )
                if exact_match:
                    return str(existing["id"])
                try:
                    self._validate_session_distill_job_in_transaction(
                        connection, str(existing["id"])
                    )
                except (LookupError, PermissionError, TypeError, ValueError):
                    self._quarantine_session_distill_job_in_transaction(
                        connection,
                        str(existing["id"]),
                        reason="invalid session-distill UNIQUE namespace collider",
                    )
                else:
                    raise ValueError("conflicting valid Cortex session-distill job")
        connection.execute(
            "INSERT OR IGNORE INTO cognitive_jobs(id, brain_id, job_type, input_hash, state, "
            "max_attempts, scheduled_at, next_attempt_at, admission_id, parent_job_id, "
            "root_job_id, input_json, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                inserted_job_id,
                self.brain_id,
                job_type,
                input_hash,
                "queued",
                max(1, min(20, int(max_attempts))),
                scheduled_at or now,
                scheduled_at or now,
                admission_id,
                parent_job_id,
                root_job_id,
                stored_input_json,
                now,
                now,
            ),
        )
        row = connection.execute(
            "SELECT id, admission_id, parent_job_id, root_job_id, input_json "
            "FROM cognitive_jobs WHERE brain_id=? AND job_type=? AND input_hash=?",
            (self.brain_id, job_type, input_hash),
        ).fetchone()
        if row is None:
            raise RuntimeError("Cortex cognitive job was not persisted")
        if job_type == "session_distill" and (
            str(row["admission_id"] or "") != str(admission_id or "")
            or str(row["parent_job_id"] or "") != str(parent_job_id or "")
            or str(row["root_job_id"] or "") != str(root_job_id or "")
            or str(row["input_json"] or "") != stored_input_json
        ):
            raise ValueError("conflicting Cortex session-distill job identity")
        return str(row["id"])

    def _enqueue_session_distill_root_in_transaction(
        self,
        connection: sqlite3.Connection,
        admission_id: str,
    ) -> str:
        admission = connection.execute(
            "SELECT * FROM session_distill_admissions WHERE id=? AND brain_id=?",
            (admission_id, self.brain_id),
        ).fetchone()
        if admission is None:
            raise LookupError("Cortex session-distill admission was not found")
        if admission["revoked_at"] is not None:
            raise PermissionError("Cortex session-distill admission was revoked")
        try:
            snapshot = json.loads(admission["snapshot_json"] or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError(
                "invalid Cortex session-distill admission snapshot"
            ) from exc
        if not isinstance(snapshot, Mapping):
            raise ValueError("invalid Cortex session-distill admission snapshot")
        existing = connection.execute(
            "SELECT * FROM cognitive_jobs WHERE brain_id=? "
            "AND job_type='session_distill' AND input_hash=?",
            (self.brain_id, str(admission["canonical_input_hash"])),
        ).fetchone()
        if existing is not None:
            existing_id = str(existing["id"])
            try:
                self._validate_session_distill_job_in_transaction(
                    connection, existing_id
                )
                canonical = (
                    str(existing["admission_id"] or "") == admission_id
                    and existing["parent_job_id"] is None
                    and str(existing["root_job_id"] or "") == existing_id
                    and str(existing["input_json"] or "") == _json(dict(snapshot))
                )
            except (LookupError, PermissionError, TypeError, ValueError):
                canonical = False
            if canonical:
                return existing_id
            self._quarantine_session_distill_job_in_transaction(
                connection,
                existing_id,
                reason="invalid collider in canonical session-distill namespace",
            )
        job_id = new_id("job")
        return self._insert_job_in_transaction(
            connection,
            "session_distill",
            input_hash=str(admission["canonical_input_hash"]),
            input_data=dict(snapshot),
            admission_id=admission_id,
            parent_job_id=None,
            root_job_id=job_id,
            job_id=job_id,
        )

    @staticmethod
    def _decode_job_input(row: Mapping[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(str(row["input_json"] or "{}"))
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid Cortex cognitive job input") from exc
        if not isinstance(value, dict):
            raise ValueError("invalid Cortex cognitive job input")
        return value

    def _validate_session_distill_job_in_transaction(
        self,
        connection: sqlite3.Connection,
        job_id: str,
    ) -> sqlite3.Row:
        """Require an immutable boundary admission and an unbroken job chain."""

        job = connection.execute(
            "SELECT * FROM cognitive_jobs WHERE id=? AND brain_id=?",
            (job_id, self.brain_id),
        ).fetchone()
        if job is None or str(job["job_type"]) != "session_distill":
            raise PermissionError("session-distill job is not admitted")
        admission_id = str(job["admission_id"] or "")
        admission = connection.execute(
            "SELECT * FROM session_distill_admissions WHERE id=? AND brain_id=?",
            (admission_id, self.brain_id),
        ).fetchone()
        if admission is None:
            raise PermissionError("session-distill job has no durable admission")
        if admission["revoked_at"] is not None:
            raise PermissionError("session-distill admission was revoked")
        try:
            snapshot = json.loads(admission["snapshot_json"] or "{}")
        except (TypeError, json.JSONDecodeError) as exc:
            raise PermissionError("session-distill admission is malformed") from exc
        if not isinstance(snapshot, dict):
            raise PermissionError("session-distill admission is malformed")
        logical_id = str(snapshot.get("logical_conversation_id") or "")
        evidence_hash = str(snapshot.get("evidence_hash") or "")
        canonical_hash = str(admission["canonical_input_hash"] or "")
        if (
            logical_id != str(admission["logical_conversation_id"] or "")
            or evidence_hash != str(admission["evidence_hash"] or "")
            or canonical_hash
            != stable_hash(logical_id, evidence_hash, "session_distill:v2")
            or int(snapshot.get("distill_version") or 0) != 2
        ):
            raise PermissionError("session-distill admission is inconsistent")

        root_job_id = str(job["root_job_id"] or "")
        if not root_job_id:
            raise PermissionError("session-distill job has no admitted root")
        seen: set[str] = set()
        current = job
        while True:
            current_id = str(current["id"])
            if current_id in seen or len(seen) >= 10_000:
                raise PermissionError("session-distill continuation chain is cyclic")
            seen.add(current_id)
            if (
                str(current["job_type"]) != "session_distill"
                or str(current["brain_id"]) != self.brain_id
                or str(current["admission_id"] or "") != admission_id
                or str(current["root_job_id"] or "") != root_job_id
            ):
                raise PermissionError("session-distill continuation chain is invalid")
            payload = self._decode_job_input(current)
            if any(payload.get(key) != value for key, value in snapshot.items()):
                raise PermissionError(
                    "session-distill snapshot does not match admission"
                )
            parent_job_id = str(current["parent_job_id"] or "")
            if not parent_job_id:
                if (
                    current_id != root_job_id
                    or str(current["input_hash"] or "") != canonical_hash
                    or payload != snapshot
                ):
                    raise PermissionError("session-distill root is not canonical")
                break
            if (
                str(payload.get("continuation_of") or "") != parent_job_id
                or str(payload.get("lineage_root_hash") or "") != evidence_hash
                or len(str(payload.get("remaining_observation_hash") or "")) != 64
            ):
                raise PermissionError("session-distill continuation is not admitted")
            parent = connection.execute(
                "SELECT * FROM cognitive_jobs WHERE id=? AND brain_id=?",
                (parent_job_id, self.brain_id),
            ).fetchone()
            if parent is None:
                raise PermissionError("session-distill continuation parent is missing")
            current = parent
        return job

    def require_session_distill_admission(self, job: Mapping[str, Any]) -> None:
        """Validate a leased semantic job before any semantic preparation/model call."""

        job_id = str(job.get("id") or "")
        if not job_id:
            raise PermissionError("session-distill job is not admitted")
        with self.connect() as connection:
            stored = self._validate_session_distill_job_in_transaction(
                connection, job_id
            )
            supplied_input = job.get("input")
            if not isinstance(supplied_input, Mapping):
                raise PermissionError("session-distill job input is not authoritative")
            if _json(dict(supplied_input)) != str(stored["input_json"] or ""):
                raise PermissionError("session-distill job input was modified")
            for name in ("input_hash", "admission_id", "parent_job_id", "root_job_id"):
                if str(job.get(name) or "") != str(stored[name] or ""):
                    raise PermissionError("session-distill job provenance was modified")

    def recover_missing_session_distill_roots(
        self, *, limit: int = 100
    ) -> tuple[str, ...]:
        """Validate and recreate roots only from committed admissions."""

        bounded = max(1, min(1_000, int(limit)))
        recovered: list[str] = []
        with self.transaction() as connection:
            admissions = connection.execute(
                "SELECT a.id, a.canonical_input_hash "
                "FROM session_distill_admissions a WHERE a.brain_id=? "
                "AND a.revoked_at IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM cognitive_jobs j "
                "WHERE j.brain_id=a.brain_id AND j.admission_id=a.id "
                "AND j.job_type='session_distill' AND j.parent_job_id IS NULL "
                "AND j.root_job_id=j.id "
                "AND j.input_hash=a.canonical_input_hash "
                "AND j.input_json=a.snapshot_json) "
                "ORDER BY a.admitted_at, a.id LIMIT ?",
                (self.brain_id, bounded),
            ).fetchall()
            for admission in admissions:
                roots = connection.execute(
                    "SELECT * FROM cognitive_jobs WHERE brain_id=? "
                    "AND job_type='session_distill' AND admission_id=? "
                    "AND parent_job_id IS NULL",
                    (self.brain_id, str(admission["id"])),
                ).fetchall()
                valid_root = False
                for root in roots:
                    root_id = str(root["id"])
                    try:
                        self._validate_session_distill_job_in_transaction(
                            connection, root_id
                        )
                        canonical = str(root["root_job_id"] or "") == root_id and str(
                            root["input_hash"] or ""
                        ) == str(admission["canonical_input_hash"])
                    except (LookupError, PermissionError, TypeError, ValueError):
                        canonical = False
                    if canonical:
                        valid_root = True
                        continue
                    self._quarantine_session_distill_job_in_transaction(
                        connection,
                        root_id,
                        reason="invalid admitted session-distill root",
                    )
                if valid_root:
                    continue
                recovered.append(
                    self._enqueue_session_distill_root_in_transaction(
                        connection, str(admission["id"])
                    )
                )
        return tuple(recovered)

    def session_distill_recovery_page(
        self,
        *,
        page_size: int = SESSION_DISTILL_RECOVERY_PAGE_SIZE,
    ) -> dict[str, Any]:
        """Read one bounded recovery page without advancing its durable cursor.

        A small newest lane prevents admissions committed during a long historical
        rotation from waiting for wraparound.  The historical cursor is advanced
        only by :meth:`ack_session_distill_recovery_page` after the scheduler has
        processed the complete page.  Job loading is one raw summary row per
        admission; provenance ancestry is validated only for an actionable leaf
        at the enqueue boundary.
        """

        bounded = max(1, min(SESSION_DISTILL_RECOVERY_PAGE_SIZE, int(page_size)))
        lease_now = utc_now()
        fresh_limit = min(
            SESSION_DISTILL_RECOVERY_FRESH_SIZE,
            max(1, bounded // 10),
        )
        history_limit = max(0, bounded - fresh_limit)
        cursor_key = f"session_distill_recovery_cursor:{self.brain_id}"
        wrapped = False

        def decode_cursor(value: Any) -> tuple[str, str] | None:
            try:
                raw_cursor = json.loads(str(value or "{}"))
            except (TypeError, json.JSONDecodeError):
                return None
            if (
                not isinstance(raw_cursor, Mapping)
                or int(raw_cursor.get("version") or 0)
                != SESSION_DISTILL_RECOVERY_CURSOR_VERSION
                or str(raw_cursor.get("brain_id") or "") != self.brain_id
                or not str(raw_cursor.get("admitted_at") or "")
                or not str(raw_cursor.get("admission_id") or "")
            ):
                return None
            return (
                str(raw_cursor["admitted_at"]),
                str(raw_cursor["admission_id"]),
            )

        def cursor_payload(cursor: tuple[str, str] | None) -> dict[str, str] | None:
            if cursor is None:
                return None
            return {
                "admitted_at": cursor[0],
                "admission_id": cursor[1],
            }

        with self.connect() as connection:
            cursor_row = connection.execute(
                "SELECT value FROM cortex_meta WHERE key=?", (cursor_key,)
            ).fetchone()
            cursor = decode_cursor(cursor_row["value"] if cursor_row else None)

            fresh = connection.execute(
                "SELECT id, snapshot_json, admitted_at "
                "FROM session_distill_admissions WHERE brain_id=? "
                "AND revoked_at IS NULL "
                "ORDER BY admitted_at DESC, id DESC LIMIT ?",
                (self.brain_id, fresh_limit),
            ).fetchall()
            fresh_ids = tuple(str(row["id"]) for row in fresh)
            exclusion_sql = ""
            exclusion_params: tuple[str, ...] = ()
            if fresh_ids:
                exclusion_sql = (
                    " AND id NOT IN (" + ",".join("?" for _ in fresh_ids) + ")"
                )
                exclusion_params = fresh_ids

            history: Sequence[sqlite3.Row] = ()
            if history_limit:
                if cursor is None:
                    history = connection.execute(
                        "SELECT id, snapshot_json, admitted_at "
                        "FROM session_distill_admissions WHERE brain_id=? "
                        "AND revoked_at IS NULL"
                        f"{exclusion_sql} "
                        "ORDER BY admitted_at DESC, id DESC LIMIT ?",
                        (self.brain_id, *exclusion_params, history_limit),
                    ).fetchall()
                else:
                    history = connection.execute(
                        "SELECT id, snapshot_json, admitted_at "
                        "FROM session_distill_admissions WHERE brain_id=? "
                        "AND revoked_at IS NULL "
                        "AND (admitted_at<? OR (admitted_at=? AND id<?))"
                        f"{exclusion_sql} "
                        "ORDER BY admitted_at DESC, id DESC LIMIT ?",
                        (
                            self.brain_id,
                            cursor[0],
                            cursor[0],
                            cursor[1],
                            *exclusion_params,
                            history_limit,
                        ),
                    ).fetchall()
                    if not history:
                        wrapped = True
                        history = connection.execute(
                            "SELECT id, snapshot_json, admitted_at "
                            "FROM session_distill_admissions WHERE brain_id=? "
                            "AND revoked_at IS NULL"
                            f"{exclusion_sql} "
                            "ORDER BY admitted_at DESC, id DESC LIMIT ?",
                            (self.brain_id, *exclusion_params, history_limit),
                        ).fetchall()

            admissions = tuple((*fresh, *history))
            next_cursor = cursor
            if history:
                last = history[-1]
                next_cursor = (str(last["admitted_at"]), str(last["id"]))
            elif wrapped:
                next_cursor = None

            jobs: Sequence[sqlite3.Row] = ()
            if admissions:
                admission_ids = tuple(str(row["id"]) for row in admissions)
                placeholders = ",".join("?" for _ in admission_ids)
                # Active work takes precedence.  Otherwise return only the
                # newest succeeded terminal leaf. Failed/dead/quarantined
                # descendants intentionally do not block repair from the last
                # successful point.
                jobs = connection.execute(
                    "WITH candidates AS ("
                    "SELECT j.*, CASE WHEN j.state IN ('queued','running') "
                    "THEN 0 ELSE 1 END AS recovery_rank, "
                    "CASE WHEN (j.state='queued' AND j.parent_job_id IS NOT NULL "
                    "AND NOT EXISTS (SELECT 1 FROM cognitive_jobs parent "
                    "WHERE parent.id=j.parent_job_id "
                    "AND parent.brain_id=j.brain_id "
                    "AND parent.job_type='session_distill' "
                    "AND parent.admission_id=j.admission_id "
                    "AND parent.root_job_id=j.root_job_id "
                    "AND parent.state IN ('queued','running','succeeded'))) "
                    "OR (j.state='running' AND (j.lease_owner IS NULL "
                    "OR TRIM(j.lease_owner)='' OR j.started_at IS NULL "
                    "OR julianday(j.started_at) IS NULL OR j.heartbeat_at IS NULL "
                    "OR julianday(j.heartbeat_at) IS NULL "
                    "OR j.lease_expires_at IS NULL "
                    "OR julianday(j.lease_expires_at) IS NULL "
                    "OR julianday(j.lease_expires_at)<=julianday(?) "
                    "OR julianday(j.heartbeat_at)>julianday(j.lease_expires_at))) "
                    "THEN 0 ELSE 1 END "
                    "AS recovery_eligible "
                    "FROM cognitive_jobs j WHERE j.brain_id=? "
                    "AND j.job_type='session_distill' "
                    f"AND j.admission_id IN ({placeholders}) "
                    "AND (j.state IN ('queued','running') OR (j.state='succeeded' "
                    "AND NOT EXISTS (SELECT 1 FROM cognitive_jobs child "
                    "WHERE child.brain_id=j.brain_id "
                    "AND child.job_type='session_distill' "
                    "AND child.admission_id=j.admission_id "
                    "AND child.parent_job_id=j.id "
                    "AND child.state IN ('queued','running','succeeded'))))), "
                    "ranked AS (SELECT candidates.*, ROW_NUMBER() OVER ("
                    "PARTITION BY admission_id ORDER BY recovery_rank, "
                    "created_at DESC, id DESC) AS recovery_row FROM candidates) "
                    "SELECT * FROM ranked WHERE recovery_row=1 "
                    "ORDER BY admission_id LIMIT ?",
                    (lease_now, self.brain_id, *admission_ids, bounded),
                ).fetchall()

        token = _json({
            "version": SESSION_DISTILL_RECOVERY_CURSOR_VERSION,
            "brain_id": self.brain_id,
            "previous": cursor_payload(cursor),
            "next": cursor_payload(next_cursor),
        })
        return {
            "admissions": tuple(dict(row) for row in admissions),
            "jobs": tuple(dict(row) for row in jobs),
            "inspected_count": len(admissions),
            "job_count": len(jobs),
            "wrapped": wrapped,
            "cursor_token": token,
        }

    def ack_session_distill_recovery_page(self, cursor_token: str) -> bool:
        """CAS-advance a page token after its complete, successful processing."""

        try:
            token = json.loads(cursor_token)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid session-distill recovery cursor token") from exc
        if (
            not isinstance(token, Mapping)
            or int(token.get("version") or 0) != SESSION_DISTILL_RECOVERY_CURSOR_VERSION
            or str(token.get("brain_id") or "") != self.brain_id
        ):
            raise ValueError("invalid session-distill recovery cursor token")

        def decode_token_cursor(value: Any) -> tuple[str, str] | None:
            if value is None:
                return None
            if not isinstance(value, Mapping):
                raise ValueError("invalid session-distill recovery cursor token")
            admitted_at = str(value.get("admitted_at") or "")
            admission_id = str(value.get("admission_id") or "")
            if not admitted_at or not admission_id:
                raise ValueError("invalid session-distill recovery cursor token")
            return admitted_at, admission_id

        previous = decode_token_cursor(token.get("previous"))
        next_cursor = decode_token_cursor(token.get("next"))
        cursor_key = f"session_distill_recovery_cursor:{self.brain_id}"
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT value FROM cortex_meta WHERE key=?", (cursor_key,)
            ).fetchone()
            current: tuple[str, str] | None = None
            if row is not None:
                try:
                    raw = json.loads(str(row["value"] or "{}"))
                except (TypeError, json.JSONDecodeError):
                    raw = {}
                if (
                    isinstance(raw, Mapping)
                    and int(raw.get("version") or 0)
                    == SESSION_DISTILL_RECOVERY_CURSOR_VERSION
                    and str(raw.get("brain_id") or "") == self.brain_id
                    and str(raw.get("admitted_at") or "")
                    and str(raw.get("admission_id") or "")
                ):
                    current = (
                        str(raw["admitted_at"]),
                        str(raw["admission_id"]),
                    )
            if current == next_cursor:
                return True
            if current != previous:
                return False
            if next_cursor is None:
                connection.execute("DELETE FROM cortex_meta WHERE key=?", (cursor_key,))
            else:
                connection.execute(
                    "INSERT OR REPLACE INTO cortex_meta(key, value) VALUES(?,?)",
                    (
                        cursor_key,
                        _json({
                            "version": SESSION_DISTILL_RECOVERY_CURSOR_VERSION,
                            "brain_id": self.brain_id,
                            "admitted_at": next_cursor[0],
                            "admission_id": next_cursor[1],
                        }),
                    ),
                )
        return True

    def session_distill_recovery_candidate(
        self, admission_id: str
    ) -> dict[str, Any] | None:
        """Return one raw active-or-terminal candidate without walking ancestry."""

        lease_now = utc_now()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT j.*, CASE WHEN (j.state='queued' "
                "AND j.parent_job_id IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM cognitive_jobs parent "
                "WHERE parent.id=j.parent_job_id "
                "AND parent.brain_id=j.brain_id "
                "AND parent.job_type='session_distill' "
                "AND parent.admission_id=j.admission_id "
                "AND parent.root_job_id=j.root_job_id "
                "AND parent.state IN ('queued','running','succeeded'))) "
                "OR (j.state='running' AND (j.lease_owner IS NULL "
                "OR TRIM(j.lease_owner)='' OR j.started_at IS NULL "
                "OR julianday(j.started_at) IS NULL OR j.heartbeat_at IS NULL "
                "OR julianday(j.heartbeat_at) IS NULL "
                "OR j.lease_expires_at IS NULL "
                "OR julianday(j.lease_expires_at) IS NULL "
                "OR julianday(j.lease_expires_at)<=julianday(?) "
                "OR julianday(j.heartbeat_at)>julianday(j.lease_expires_at))) "
                "THEN 0 ELSE 1 END "
                "AS recovery_eligible "
                "FROM cognitive_jobs j WHERE j.brain_id=? "
                "AND j.admission_id=? AND j.job_type='session_distill' "
                "AND (j.state IN ('queued','running') OR (j.state='succeeded' "
                "AND NOT EXISTS (SELECT 1 FROM cognitive_jobs child "
                "WHERE child.brain_id=j.brain_id "
                "AND child.job_type='session_distill' "
                "AND child.admission_id=j.admission_id "
                "AND child.parent_job_id=j.id "
                "AND child.state IN ('queued','running','succeeded')))) "
                "ORDER BY CASE WHEN j.state IN ('queued','running') THEN 0 ELSE 1 END, "
                "j.created_at DESC, j.id DESC LIMIT 1",
                (lease_now, self.brain_id, admission_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def repair_ineligible_session_distill_candidate(
        self,
        job_id: str,
        *,
        reason: str = "session-distill continuation parent is not lease eligible",
    ) -> bool:
        """Repair one stale lease/orphan using only immediate bounded lineage."""

        now = utc_now()
        with self.transaction() as connection:
            repaired_lease = connection.execute(
                "UPDATE cognitive_jobs SET state=CASE WHEN attempt>=max_attempts "
                "THEN 'dead_letter' ELSE 'queued' END, "
                "dead_letter_at=CASE WHEN attempt>=max_attempts THEN ? "
                "ELSE dead_letter_at END, lease_owner=NULL, lease_expires_at=NULL, "
                "updated_at=? WHERE id=? AND brain_id=? "
                "AND job_type='session_distill' AND state='running' "
                "AND (lease_owner IS NULL OR TRIM(lease_owner)='' "
                "OR started_at IS NULL OR julianday(started_at) IS NULL "
                "OR heartbeat_at IS NULL OR julianday(heartbeat_at) IS NULL "
                "OR lease_expires_at IS NULL OR julianday(lease_expires_at) IS NULL "
                "OR julianday(lease_expires_at)<=julianday(?) "
                "OR julianday(heartbeat_at)>julianday(lease_expires_at))",
                (now, now, job_id, self.brain_id, now),
            ).rowcount
            row = connection.execute(
                "SELECT j.id FROM cognitive_jobs j WHERE j.id=? AND j.brain_id=? "
                "AND j.job_type='session_distill' AND j.state='queued' "
                "AND j.parent_job_id IS NOT NULL "
                "AND NOT EXISTS (SELECT 1 FROM cognitive_jobs parent "
                "WHERE parent.id=j.parent_job_id "
                "AND parent.brain_id=j.brain_id "
                "AND parent.job_type='session_distill' "
                "AND parent.admission_id=j.admission_id "
                "AND parent.root_job_id=j.root_job_id "
                "AND parent.state IN ('queued','running','succeeded'))",
                (job_id, self.brain_id),
            ).fetchone()
            if row is None:
                return repaired_lease == 1
            return self._quarantine_session_distill_job_in_transaction(
                connection, job_id, reason=reason
            )

    def quarantine_invalid_session_distill_job(
        self,
        job_id: str,
        *,
        reason: str = "invalid session-distill recovery chain",
    ) -> bool:
        """Quarantine a recovery candidate only after authoritative validation."""

        with self.transaction() as connection:
            try:
                self._validate_session_distill_job_in_transaction(connection, job_id)
            except (LookupError, PermissionError, TypeError, ValueError):
                return self._quarantine_session_distill_job_in_transaction(
                    connection, job_id, reason=reason
                )
        return False

    def enqueue_session_distill_continuation(
        self,
        parent_job_id: str,
        *,
        remaining_signature: Sequence[str],
        owner: str | None = None,
        recovered: bool = False,
    ) -> str:
        """Append one continuation to an admitted parent/root chain."""

        signature = tuple(str(value) for value in remaining_signature if str(value))
        if not signature:
            raise ValueError("session-distill continuation requires pending work")
        with self.transaction() as connection:
            parent = self._validate_session_distill_job_in_transaction(
                connection, parent_job_id
            )
            parent_state = str(parent["state"] or "")
            if owner is not None:
                if (
                    parent_state != "running"
                    or str(parent["lease_owner"] or "") != owner
                ):
                    raise PermissionError("session-distill parent lease is not owned")
            elif parent_state != "succeeded":
                raise PermissionError(
                    "recovered session-distill continuation requires a succeeded parent"
                )
            admission = connection.execute(
                "SELECT * FROM session_distill_admissions WHERE id=? AND brain_id=?",
                (str(parent["admission_id"]), self.brain_id),
            ).fetchone()
            if admission is None:
                raise PermissionError("session-distill admission is missing")
            snapshot = json.loads(admission["snapshot_json"] or "{}")
            if not isinstance(snapshot, dict):
                raise PermissionError("session-distill admission is malformed")
            remaining_hash = stable_hash(*signature)
            continuation_hash = stable_hash(
                self.brain_id,
                str(admission["canonical_input_hash"]),
                "session_distill_continuation:v2",
                remaining_hash,
            )
            continuation_input = dict(snapshot)
            continuation_input.update({
                "lineage_root_hash": str(admission["evidence_hash"]),
                "continuation_of": parent_job_id,
                "remaining_observation_hash": remaining_hash,
            })
            if recovered:
                continuation_input["recovered"] = True
            return self._insert_job_in_transaction(
                connection,
                "session_distill",
                input_hash=continuation_hash,
                input_data=continuation_input,
                admission_id=str(admission["id"]),
                parent_job_id=parent_job_id,
                root_job_id=str(parent["root_job_id"]),
            )

    def _requeue_expired_jobs_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        now: str,
    ) -> int:
        cursor = connection.execute(
            "UPDATE cognitive_jobs SET state=CASE WHEN attempt>=max_attempts "
            "THEN 'dead_letter' ELSE 'queued' END, "
            "dead_letter_at=CASE WHEN attempt>=max_attempts THEN ? "
            "ELSE dead_letter_at END, lease_owner=NULL, lease_expires_at=NULL, "
            "updated_at=? WHERE brain_id=? AND state='running' "
            "AND (lease_owner IS NULL OR TRIM(lease_owner)='' "
            "OR started_at IS NULL OR julianday(started_at) IS NULL "
            "OR heartbeat_at IS NULL OR julianday(heartbeat_at) IS NULL "
            "OR lease_expires_at IS NULL OR julianday(lease_expires_at) IS NULL "
            "OR julianday(lease_expires_at)<=julianday(?) "
            "OR julianday(heartbeat_at)>julianday(lease_expires_at))",
            (now, now, self.brain_id, now),
        )
        return max(0, int(cursor.rowcount))

    def _next_leaseable_job_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        job_types: Sequence[str],
        now: str,
    ) -> sqlite3.Row | None:
        """Select exactly what ``lease_job`` may run, without taking the lease."""

        placeholders = ",".join("?" for _ in job_types)
        for _ in range(100):
            candidate = connection.execute(
                f"SELECT j.*, COALESCE(j.next_attempt_at, j.scheduled_at) AS due_at "
                f"FROM cognitive_jobs j WHERE j.brain_id=? AND j.state='queued' "
                f"AND COALESCE(j.next_attempt_at, j.scheduled_at)<=? "
                f"AND j.attempt<j.max_attempts "
                f"AND j.job_type IN ({placeholders}) "
                f"AND (j.job_type!='session_distill' "
                f"OR j.parent_job_id IS NULL OR EXISTS ("
                f"SELECT 1 FROM cognitive_jobs parent "
                f"WHERE parent.id=j.parent_job_id AND parent.brain_id=j.brain_id "
                f"AND parent.state='succeeded')) "
                f"ORDER BY COALESCE(j.next_attempt_at, j.scheduled_at), "
                f"j.created_at, j.id LIMIT 1",
                (self.brain_id, now, *job_types),
            ).fetchone()
            if not candidate:
                return None
            if str(candidate["job_type"]) != "session_distill":
                return candidate
            try:
                self._validate_session_distill_job_in_transaction(
                    connection, str(candidate["id"])
                )
            except (LookupError, PermissionError, TypeError, ValueError):
                self._quarantine_session_distill_job_in_transaction(
                    connection,
                    str(candidate["id"]),
                    reason="invalid durable session-distill admission",
                )
                continue
            return candidate
        return None

    def next_leaseable_job(
        self,
        *,
        job_types: Sequence[str],
    ) -> dict[str, Any] | None:
        """Peek at the next runnable job using the exact leasing predicate.

        This does not increment attempts or lease valid work. It may apply the
        same deterministic expired-lease repair and invalid-provenance
        quarantine that ``lease_job`` applies before selection.
        """

        if not job_types:
            return None
        now = utc_now()
        with self.transaction() as connection:
            self._requeue_expired_jobs_in_transaction(connection, now=now)
            row = self._next_leaseable_job_in_transaction(
                connection,
                job_types=job_types,
                now=now,
            )
            if row is None:
                return None
            return {
                "id": str(row["id"]),
                "admission_id": str(row["admission_id"] or ""),
                "root_job_id": str(row["root_job_id"] or ""),
                "input_hash": str(row["input_hash"] or ""),
                "attempt": int(row["attempt"]),
                "due_at": str(row["due_at"]),
            }

    def lease_exact_managed_job(
        self,
        admission: CortexDispatchAdmission,
        *,
        owner: str,
        lease_seconds: int,
    ) -> dict[str, Any]:
        """Lease only the exact next local job admitted by the control plane.

        The comparison and state transition share one immediate transaction,
        so an intervening worker cannot swap or consume the selected job after
        the managed claim has been checked.
        """

        if not isinstance(admission, CortexDispatchAdmission):
            raise TypeError("admission must be a CortexDispatchAdmission")
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat().replace("+00:00", "Z")
        expiry = (
            (now_dt + timedelta(seconds=lease_seconds))
            .isoformat()
            .replace("+00:00", "Z")
        )
        with self.transaction() as connection:
            self._requeue_expired_jobs_in_transaction(connection, now=now)
            row = self._next_leaseable_job_in_transaction(
                connection,
                job_types=("session_distill",),
                now=now,
            )
            if row is None or not admission.matches_job(dict(row)):
                raise PermissionError(
                    "managed Cortex dispatch no longer matches the next admitted job"
                )
            cursor = connection.execute(
                "UPDATE cognitive_jobs SET state='running', attempt=attempt+1, "
                "started_at=?, lease_owner=?, lease_expires_at=?, heartbeat_at=?, "
                "updated_at=? WHERE id=? AND state='queued' AND attempt=? "
                "AND COALESCE(next_attempt_at, scheduled_at)=?",
                (
                    now,
                    owner,
                    expiry,
                    now,
                    now,
                    row["id"],
                    admission.attempt,
                    admission.due_at,
                ),
            )
            if cursor.rowcount != 1:
                raise PermissionError("managed Cortex dispatch lease was lost")
            result = dict(row)
            result["state"] = "running"
            result["attempt"] = int(row["attempt"]) + 1
            result["input"] = json.loads(result.pop("input_json") or "{}")
            return result

    def lease_job(
        self,
        *,
        job_types: Sequence[str],
        owner: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        if not job_types:
            return None
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat().replace("+00:00", "Z")
        expiry = (
            (now_dt + timedelta(seconds=lease_seconds))
            .isoformat()
            .replace("+00:00", "Z")
        )
        with self.transaction() as connection:
            self._requeue_expired_jobs_in_transaction(connection, now=now)
            row = self._next_leaseable_job_in_transaction(
                connection,
                job_types=job_types,
                now=now,
            )
            if row is None:
                return None
            cursor = connection.execute(
                "UPDATE cognitive_jobs SET state='running', attempt=attempt+1, started_at=?, "
                "lease_owner=?, lease_expires_at=?, heartbeat_at=?, updated_at=? "
                "WHERE id=? AND state='queued'",
                (now, owner, expiry, now, now, row["id"]),
            )
            if cursor.rowcount != 1:
                return None
            result = dict(row)
            result["state"] = "running"
            result["attempt"] = int(row["attempt"]) + 1
            result["input"] = json.loads(result.pop("input_json") or "{}")
            return result

    def complete_job(
        self,
        job_id: str,
        *,
        owner: str,
        output: Mapping[str, Any],
        model: str = "",
        prompt_version: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_micros: int = 0,
    ) -> None:
        now = utc_now()
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE cognitive_jobs SET state='succeeded', output_json=?, model=?, prompt_version=?, "
                "input_tokens=?, output_tokens=?, cost_micros=?, completed_at=?, "
                "lease_owner=NULL, lease_expires_at=NULL, heartbeat_at=?, updated_at=? "
                "WHERE id=? AND brain_id=? AND state='running' AND lease_owner=?",
                (
                    _json(sanitize_for_storage(dict(output))),
                    model or None,
                    prompt_version or None,
                    max(0, int(input_tokens)),
                    max(0, int(output_tokens)),
                    max(0, int(cost_micros)),
                    now,
                    now,
                    now,
                    job_id,
                    self.brain_id,
                    owner,
                ),
            )
            if cursor.rowcount != 1:
                raise PermissionError("job lease is not owned by this worker")

    def fail_job(
        self, job_id: str, *, owner: str, error: str, retry: bool = True
    ) -> None:
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT attempt, max_attempts FROM cognitive_jobs WHERE id=? AND brain_id=? "
                "AND state='running' AND lease_owner=?",
                (job_id, self.brain_id, owner),
            ).fetchone()
            if not row:
                raise PermissionError("job lease is not owned by this worker")
            can_retry = retry and int(row["attempt"]) < int(row["max_attempts"])
            delay_seconds = min(21_600, 30 * (2 ** max(0, int(row["attempt"]) - 1)))
            next_attempt = (
                (datetime.now(timezone.utc) + timedelta(seconds=delay_seconds))
                .isoformat()
                .replace("+00:00", "Z")
            )
            cursor = connection.execute(
                "UPDATE cognitive_jobs SET state=?, error=?, completed_at=?, dead_letter_at=?, "
                "next_attempt_at=?, lease_owner=NULL, lease_expires_at=NULL, heartbeat_at=?, "
                "updated_at=? WHERE id=? AND brain_id=? AND state='running' "
                "AND lease_owner=?",
                (
                    "queued" if can_retry else ("dead_letter" if retry else "failed"),
                    str(sanitize_for_storage(error))[:2_000],
                    None if can_retry else now,
                    None if can_retry or not retry else now,
                    next_attempt if can_retry else now,
                    now,
                    now,
                    job_id,
                    self.brain_id,
                    owner,
                ),
            )
            if cursor.rowcount != 1:
                raise PermissionError("job lease is not owned by this worker")

    def heartbeat_job(self, job_id: str, *, owner: str, lease_seconds: int) -> None:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat().replace("+00:00", "Z")
        expiry = (
            (now_dt + timedelta(seconds=lease_seconds))
            .isoformat()
            .replace("+00:00", "Z")
        )
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE cognitive_jobs SET heartbeat_at=?, lease_expires_at=?, updated_at=? "
                "WHERE id=? AND brain_id=? AND state='running' AND lease_owner=?",
                (now, expiry, now, job_id, self.brain_id, owner),
            )
            if cursor.rowcount != 1:
                raise PermissionError("job lease is not owned by this worker")

    def checkpoint_job(
        self, job_id: str, *, owner: str, phase: str, data: Mapping[str, Any]
    ) -> None:
        checkpoint = {
            "phase": phase,
            "data": sanitize_for_storage(dict(data)),
            "at": utc_now(),
        }
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE cognitive_jobs SET checkpoint_json=?, updated_at=? WHERE id=? AND brain_id=? "
                "AND state='running' AND lease_owner=?",
                (_json(checkpoint), utc_now(), job_id, self.brain_id, owner),
            )
            if cursor.rowcount != 1:
                raise PermissionError("job lease is not owned by this worker")

    def record_job_operation(
        self,
        job_id: str,
        *,
        operation_key: str,
        operation_type: str,
        resource_id: str | None,
        result: Mapping[str, Any] | None = None,
    ) -> bool:
        """Record an idempotent dream operation; False means it already ran."""
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO cognitive_job_operations(job_id, operation_key, "
                "operation_type, resource_id, result_json, applied_at) VALUES(?,?,?,?,?,?)",
                (
                    job_id,
                    operation_key,
                    operation_type,
                    resource_id,
                    _json(sanitize_for_storage(dict(result or {}))),
                    utc_now(),
                ),
            )
            if cursor.rowcount == 1:
                self.audit_in_transaction(
                    connection,
                    principal_id=None,
                    action="cortex.dream.operation",
                    resource_type=operation_type,
                    resource_id=resource_id,
                    outcome="succeeded",
                    details={"job_id": job_id, "result": dict(result or {})},
                )
            return cursor.rowcount == 1

    @contextmanager
    def atomic_job_operation(
        self,
        job_id: str,
        *,
        owner: str,
        operation_key: str,
    ) -> Iterator[bool]:
        """Make one cognitive mutation and its operation receipt atomic.

        Nested store writes reuse this transaction through SQLite savepoints.
        The caller must insert ``cognitive_job_operations.operation_key`` before
        leaving the context when ``True`` is yielded; otherwise everything is
        rolled back. ``False`` means a prior attempt already committed it.
        """
        with self.transaction() as connection:
            job = connection.execute(
                "SELECT id FROM cognitive_jobs WHERE id=? AND brain_id=? "
                "AND state='running' AND lease_owner=?",
                (job_id, self.brain_id, owner),
            ).fetchone()
            if not job:
                raise PermissionError("job lease is not owned by this worker")
            existing = connection.execute(
                "SELECT 1 FROM cognitive_job_operations WHERE job_id=? "
                "AND operation_key=?",
                (job_id, operation_key),
            ).fetchone()
            if existing:
                yield False
                return
            yield True
            receipt = connection.execute(
                "SELECT 1 FROM cognitive_job_operations WHERE job_id=? "
                "AND operation_key=?",
                (job_id, operation_key),
            ).fetchone()
            if not receipt:
                raise RuntimeError(
                    "cognitive mutation did not record its idempotency receipt"
                )

    def acquire_named_lease(self, name: str, *, owner: str, lease_seconds: int) -> bool:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat().replace("+00:00", "Z")
        expiry = (
            (now_dt + timedelta(seconds=lease_seconds))
            .isoformat()
            .replace("+00:00", "Z")
        )
        with self.transaction() as connection:
            connection.execute(
                "DELETE FROM cortex_leases WHERE brain_id=? AND lease_name=? AND expires_at<?",
                (self.brain_id, name, now),
            )
            cursor = connection.execute(
                "INSERT OR IGNORE INTO cortex_leases(brain_id, lease_name, owner, expires_at, "
                "heartbeat_at) VALUES(?,?,?,?,?)",
                (self.brain_id, name, owner, expiry, now),
            )
            return cursor.rowcount == 1

    def renew_named_lease(self, name: str, *, owner: str, lease_seconds: int) -> bool:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat().replace("+00:00", "Z")
        expiry = (
            (now_dt + timedelta(seconds=lease_seconds))
            .isoformat()
            .replace("+00:00", "Z")
        )
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE cortex_leases SET expires_at=?, heartbeat_at=? WHERE brain_id=? "
                "AND lease_name=? AND owner=?",
                (expiry, now, self.brain_id, name, owner),
            )
            return cursor.rowcount == 1

    def release_named_lease(self, name: str, *, owner: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                "DELETE FROM cortex_leases WHERE brain_id=? AND lease_name=? AND owner=?",
                (self.brain_id, name, owner),
            )

    def pending_observations(
        self,
        limit: int = 100,
        *,
        session_id: str | None = None,
        session_ids: Sequence[str] = (),
        evidence_ids: Sequence[str] = (),
        knowledge_space: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return due semantic candidates with filtering performed in SQLite.

        Deferred observations retain ``pending`` state but receive a bounded
        retry time, preventing an uncertain oldest row from starving newer
        turns. Customer-review rows are intentionally excluded until an
        explicit review workflow changes their state.
        """

        now = utc_now()
        clauses = [
            "o.brain_id=?",
            "((o.processing_state='pending' AND "
            "(o.next_triage_at IS NULL OR o.next_triage_at<=?)) OR "
            "(o.processing_state='retained-hot' AND o.valid_until IS NOT NULL "
            "AND o.valid_until<=?))",
        ]
        params: list[Any] = [self.brain_id, now, now]
        normalized_sessions = tuple(
            dict.fromkeys([
                *(str(value) for value in session_ids if str(value).strip()),
                *([session_id] if session_id else []),
            ])
        )
        if normalized_sessions:
            session_placeholders = ",".join("?" for _ in normalized_sessions)
            clauses.append(f"o.session_id IN ({session_placeholders})")
            params.extend(normalized_sessions)
        if knowledge_space:
            clauses.append("k.slug=?")
            params.append(knowledge_space)
        normalized_evidence = tuple(
            dict.fromkeys(str(value) for value in evidence_ids if str(value).strip())
        )
        if normalized_evidence:
            placeholders = ",".join("?" for _ in normalized_evidence)
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(o.evidence_ids_json) je "
                f"WHERE je.value IN ({placeholders}))"
            )
            params.extend(normalized_evidence)
        params.append(max(1, min(1_000, int(limit))))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT o.*, k.slug AS knowledge_space FROM observations o "
                "JOIN knowledge_spaces k ON k.id=o.knowledge_space_id "
                f"WHERE {' AND '.join(clauses)} "
                "ORDER BY COALESCE(o.next_triage_at, o.created_at), o.created_at LIMIT ?",
                tuple(params),
            ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["evidence_ids"] = json.loads(item.pop("evidence_ids_json"))
                item["entity_candidates"] = json.loads(
                    item.pop("entity_candidates_json")
                )
                result.append(item)
            return result

    def mark_observation(
        self,
        observation_id: str,
        *,
        state: str,
        action: str,
        reason: str,
        model: str = "",
        prompt_version: str = "",
    ) -> None:
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE observations SET processing_state=?, utility_action=?, triage_reason=?, "
                "model=?, prompt_version=?, processed_at=?, next_triage_at=NULL "
                "WHERE id=? AND brain_id=?",
                (
                    state,
                    action,
                    reason[:1_000],
                    model or None,
                    prompt_version or None,
                    utc_now(),
                    observation_id,
                    self.brain_id,
                ),
            )
            if cursor.rowcount != 1:
                raise LookupError("observation was not found")

    def defer_observation(
        self,
        observation_id: str,
        *,
        reason: str,
        model: str = "",
        prompt_version: str = "",
    ) -> str:
        """Record a non-semantic defer and schedule a fair bounded retry."""

        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat().replace("+00:00", "Z")
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT triage_attempts FROM observations WHERE id=? AND brain_id=?",
                (observation_id, self.brain_id),
            ).fetchone()
            if row is None:
                raise LookupError("observation was not found")
            attempt = int(row["triage_attempts"] or 0) + 1
            delay_seconds = min(24 * 60 * 60, 15 * 60 * (2 ** min(attempt - 1, 7)))
            next_triage_at = (
                (now_dt + timedelta(seconds=delay_seconds))
                .isoformat()
                .replace("+00:00", "Z")
            )
            connection.execute(
                "UPDATE observations SET processing_state='pending', "
                "utility_action='defer_unresolved', triage_reason=?, model=?, "
                "prompt_version=?, triage_attempts=?, next_triage_at=?, processed_at=? "
                "WHERE id=? AND brain_id=?",
                (
                    reason[:1_000],
                    model or None,
                    prompt_version or None,
                    attempt,
                    next_triage_at,
                    now,
                    observation_id,
                    self.brain_id,
                ),
            )
        return next_triage_at

    def recall(
        self,
        query: str,
        *,
        allowed_spaces: Sequence[str],
        principal_id: str | None = None,
        session_id: str | None = None,
        max_items: int = 8,
        max_chars: int = 6_000,
        graph_hops: int | None = None,
        graphrag_max_items: int | None = None,
    ) -> RecallResult:
        import time

        started = time.monotonic()
        query = " ".join(query.split()).strip()
        run_id = new_id("retrieval")
        if not query or not allowed_spaces:
            return RecallResult(run_id, query, "none", (), 0)
        terms = _terms(query)
        if not terms:
            return RecallResult(run_id, query, "none", (), 0)
        effective_graph_hops = max(
            0,
            min(
                2,
                int(self.recall_graph_hops if graph_hops is None else graph_hops),
            ),
        )
        effective_graphrag_max_items = max(
            1,
            min(
                20,
                int(
                    self.graphrag_max_items
                    if graphrag_max_items is None
                    else graphrag_max_items
                ),
            ),
        )

        with self.connect() as connection:
            space_rows = connection.execute(
                f"SELECT id, slug FROM knowledge_spaces WHERE brain_id=? AND deleted_at IS NULL "
                f"AND slug IN ({','.join('?' for _ in allowed_spaces)})",
                (self.brain_id, *allowed_spaces),
            ).fetchall()
            authorized = {row["id"]: row["slug"] for row in space_rows}
            if not authorized:
                raise PermissionError("no authorized Cortex knowledge spaces")
            candidates: list[RecallItem] = []
            candidates.extend(
                self._recall_memories(
                    connection, query, terms, authorized, max_items * 3
                )
            )
            candidates.extend(
                self._recall_entities(
                    connection,
                    query,
                    terms,
                    authorized,
                    max_items * 2,
                    graph_hops=effective_graph_hops,
                )
            )
            candidates.extend(
                self._recall_evidence(
                    connection, query, terms, authorized, max_items * 2
                )
            )
            candidates.extend(
                self._recall_graphrag(
                    connection,
                    query,
                    terms,
                    authorized,
                    effective_graphrag_max_items * 2,
                )
            )
            candidates.extend(
                self._recall_graphrag_communities(
                    connection,
                    query,
                    terms,
                    authorized,
                    effective_graphrag_max_items * 2,
                )
            )

            # Reciprocal-rank-like fusion is represented by channel score plus exact-term overlap.
            deduped: dict[tuple[str, str], RecallItem] = {}
            for item in sorted(candidates, key=lambda value: value.score, reverse=True):
                key = (item.kind, item.text.lower())
                if key not in deduped:
                    deduped[key] = item
            selected: list[RecallItem] = []
            used = 0
            selected_graphrag_items = 0
            for item in deduped.values():
                is_graphrag_item = item.kind in {"graphrag", "community"}
                if (
                    is_graphrag_item
                    and selected_graphrag_items >= effective_graphrag_max_items
                ):
                    continue
                cost = len(item.text) + 160
                if selected and used + cost > max_chars:
                    continue
                selected.append(item)
                used += cost
                if is_graphrag_item:
                    selected_graphrag_items += 1
                if len(selected) >= max_items:
                    break
            latency_ms = int((time.monotonic() - started) * 1_000)
            route = self._route_for_spaces({item.knowledge_space for item in selected})
            connection.execute(
                "INSERT INTO retrieval_runs(id, brain_id, principal_id, session_id, query_hash, "
                "query_preview, route, allowed_spaces_json, candidate_ids_json, selected_ids_json, "
                "explanation_json, latency_ms, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    run_id,
                    self.brain_id,
                    principal_id,
                    session_id,
                    stable_hash(query),
                    str(sanitize_for_storage(query[:240])),
                    route,
                    _json(list(allowed_spaces)),
                    _json([item.id for item in candidates]),
                    _json([item.id for item in selected]),
                    _json([
                        {"id": item.id, "why": item.why_matched} for item in selected
                    ]),
                    latency_ms,
                    utc_now(),
                ),
            )
        return RecallResult(run_id, query, route, tuple(selected), latency_ms)

    @staticmethod
    def _route_for_spaces(spaces: set[str]) -> str:
        domains = set()
        if "personal" in spaces:
            domains.add("personal")
        if "tekion" in spaces or any(space.startswith("tekion:") for space in spaces):
            domains.add("tekion")
        if "atlas-capabilities" in spaces:
            domains.add("capability")
        if len(domains) > 1:
            return "federated"
        return next(iter(domains), "personal")

    @staticmethod
    def _lexical_score(text: str, terms: Sequence[str], *, base: float) -> float:
        lowered = text.lower()
        hits = sum(1 for term in terms if term in lowered)
        exact = 1 if " ".join(terms) in lowered else 0
        return base + hits / max(1, len(terms)) + exact * 0.5

    def _recall_memories(
        self,
        connection: sqlite3.Connection,
        query: str,
        terms: Sequence[str],
        spaces: Mapping[str, str],
        limit: int,
    ) -> list[RecallItem]:
        rows = self._search_rows(
            connection,
            f"SELECT m.*, k.slug AS space_slug FROM memory_records m JOIN knowledge_spaces k "
            f"ON k.id=m.knowledge_space_id WHERE m.brain_id=? AND m.status IN ('active','disputed') "
            f"AND m.deleted_at IS NULL AND m.knowledge_space_id IN ({','.join('?' for _ in spaces)})",
            (self.brain_id, *spaces.keys()),
            text_column="canonical_statement",
            terms=terms,
            limit=limit,
            fts_table="memory_fts",
            id_column="m.id",
        )
        items = []
        for row in rows:
            evidence = connection.execute(
                "SELECT e.id, e.source_locator, e.occurred_at FROM memory_evidence me "
                "JOIN evidence_items e ON e.id=me.evidence_id WHERE me.memory_id=? "
                "AND e.tombstoned_at IS NULL ORDER BY e.occurred_at DESC LIMIT 4",
                (row["id"],),
            ).fetchall()
            items.append(
                RecallItem(
                    id=row["id"],
                    kind="memory",
                    text=row["canonical_statement"],
                    knowledge_space=row["space_slug"],
                    source_label="durable memory",
                    occurred_at=row["last_confirmed_at"],
                    evidence_locator=evidence[0]["source_locator"]
                    if evidence
                    else None,
                    status=row["status"],
                    epistemic_status=row["epistemic_status"],
                    why_matched="lexical match in durable memory",
                    score=self._lexical_score(
                        row["canonical_statement"], terms, base=3.0
                    )
                    + (0.2 if row["protected"] else 0),
                    evidence_ids=tuple(item["id"] for item in evidence),
                )
            )
        return items

    def _recall_entities(
        self,
        connection: sqlite3.Connection,
        query: str,
        terms: Sequence[str],
        spaces: Mapping[str, str],
        limit: int,
        *,
        graph_hops: int,
    ) -> list[RecallItem]:
        rows = self._search_rows(
            connection,
            f"SELECT e.*, k.slug AS space_slug FROM entities e JOIN knowledge_spaces k "
            f"ON k.id=e.knowledge_space_id WHERE e.brain_id=? AND e.deleted_at IS NULL "
            f"AND NOT EXISTS (SELECT 1 FROM entity_aliases fa "
            f"JOIN memory_evidence fme ON fme.evidence_id=fa.evidence_id "
            f"JOIN memory_records fm ON fm.id=fme.memory_id "
            f"WHERE fa.entity_id=e.id AND fm.brain_id=e.brain_id "
            f"AND fa.deleted_at IS NULL AND fm.status IN ('deleted','superseded')) "
            f"AND e.knowledge_space_id IN ({','.join('?' for _ in spaces)})",
            (self.brain_id, *spaces.keys()),
            text_column=(
                "COALESCE(e.canonical_name,'') || ' ' || COALESCE(e.description,'') || ' ' || "
                "COALESCE((SELECT GROUP_CONCAT(sa.alias, ' ') FROM entity_aliases sa "
                "WHERE sa.entity_id=e.id AND sa.deleted_at IS NULL), '')"
            ),
            terms=terms,
            limit=limit,
            fts_table="entity_fts",
            id_column="e.id",
        )
        result = []
        for row in rows:
            relation_text = self._entity_relation_context(
                connection,
                str(row["id"]),
                str(row["knowledge_space_id"]),
                graph_hops,
            )
            text = f"{row['canonical_name']}: {row['description'] or ''}".strip(": ")
            if relation_text:
                text = f"{text}. Related: {relation_text}"
            result.append(
                RecallItem(
                    id=row["id"],
                    kind="entity",
                    text=text,
                    knowledge_space=row["space_slug"],
                    source_label="knowledge graph",
                    occurred_at=row["last_seen_at"],
                    evidence_locator=None,
                    status="active",
                    epistemic_status="reported",
                    why_matched="entity or alias matched the query",
                    score=self._lexical_score(text, terms, base=2.0),
                    metadata={
                        "entity_type": row["entity_type"],
                        "graph_hops": graph_hops,
                    },
                )
            )
        return result

    def _entity_relation_context(
        self,
        connection: sqlite3.Connection,
        entity_id: str,
        knowledge_space_id: str,
        graph_hops: int,
    ) -> str:
        """Return bounded relation context up to the configured graph depth."""
        if graph_hops <= 0:
            return ""
        frontier = {entity_id}
        visited_entities = {entity_id}
        seen_relations: set[str] = set()
        by_hop: dict[int, list[str]] = {}
        for hop in range(1, graph_hops + 1):
            if not frontier:
                break
            ordered_frontier = sorted(frontier)
            marks = ",".join("?" for _ in ordered_frontier)
            rows = connection.execute(
                "SELECT r.id, r.subject_entity_id, r.object_entity_id, r.predicate, "
                "s.canonical_name AS subject_name, o.canonical_name AS object_name "
                "FROM relations r JOIN entities s ON s.id=r.subject_entity_id "
                "JOIN entities o ON o.id=r.object_entity_id "
                "WHERE r.brain_id=? AND r.knowledge_space_id=? "
                "AND r.status='active' AND s.deleted_at IS NULL "
                "AND o.deleted_at IS NULL AND "
                f"(r.subject_entity_id IN ({marks}) OR r.object_entity_id IN ({marks})) "
                "ORDER BY r.weight DESC, r.updated_at DESC LIMIT 12",
                (
                    self.brain_id,
                    knowledge_space_id,
                    *ordered_frontier,
                    *ordered_frontier,
                ),
            ).fetchall()
            next_frontier: set[str] = set()
            for relation in rows:
                relation_id = str(relation["id"])
                if relation_id not in seen_relations:
                    seen_relations.add(relation_id)
                    by_hop.setdefault(hop, []).append(
                        f"{relation['subject_name']} "
                        f"{str(relation['predicate']).replace('_', ' ')} "
                        f"{relation['object_name']}"
                    )
                for endpoint in (
                    str(relation["subject_entity_id"]),
                    str(relation["object_entity_id"]),
                ):
                    if endpoint not in visited_entities:
                        next_frontier.add(endpoint)
            visited_entities.update(next_frontier)
            frontier = next_frontier
        return "; ".join(
            f"{hop}-hop: " + "; ".join(by_hop[hop])
            for hop in sorted(by_hop)
            if by_hop[hop]
        )

    def _recall_evidence(
        self,
        connection: sqlite3.Connection,
        query: str,
        terms: Sequence[str],
        spaces: Mapping[str, str],
        limit: int,
    ) -> list[RecallItem]:
        source_types = tuple(sorted(AUTOMATIC_RECALL_EVIDENCE_TYPES))
        rows = self._search_rows(
            connection,
            f"SELECT e.*, k.slug AS space_slug FROM evidence_items e JOIN knowledge_spaces k "
            f"ON k.id=e.knowledge_space_id WHERE e.brain_id=? AND e.tombstoned_at IS NULL "
            f"AND e.source_type IN ({','.join('?' for _ in source_types)}) "
            f"AND NOT EXISTS (SELECT 1 FROM memory_evidence fme "
            f"JOIN memory_records fm ON fm.id=fme.memory_id "
            f"WHERE fme.evidence_id=e.id AND fm.brain_id=e.brain_id "
            f"AND fm.status IN ('deleted','superseded')) "
            f"AND e.knowledge_space_id IN ({','.join('?' for _ in spaces)})",
            (self.brain_id, *source_types, *spaces.keys()),
            text_column="content",
            terms=terms,
            limit=limit,
            fts_table="evidence_fts",
            id_column="e.id",
        )
        return [
            RecallItem(
                id=row["id"],
                kind="evidence",
                text=row["content"][:1_200],
                knowledge_space=row["space_slug"],
                source_label=row["source_type"].replace("_", " "),
                occurred_at=row["occurred_at"],
                evidence_locator=row["source_locator"],
                status="active",
                epistemic_status="reported",
                why_matched="direct source evidence matched the query",
                score=self._lexical_score(row["content"], terms, base=1.0),
                evidence_ids=(row["id"],),
            )
            for row in rows
        ]

    def _recall_graphrag(
        self,
        connection: sqlite3.Connection,
        query: str,
        terms: Sequence[str],
        spaces: Mapping[str, str],
        limit: int,
    ) -> list[RecallItem]:
        rows = self._search_rows(
            connection,
            f"SELECT d.*, k.slug AS space_slug, i.version FROM graphrag_documents d "
            f"JOIN graphrag_indexes i ON i.id=d.index_id JOIN knowledge_spaces k "
            f"ON k.id=d.knowledge_space_id WHERE d.brain_id=? AND i.state='active' "
            f"AND d.knowledge_space_id IN ({','.join('?' for _ in spaces)})",
            (self.brain_id, *spaces.keys()),
            text_column="COALESCE(d.title,'') || ' ' || d.text",
            terms=terms,
            limit=limit,
            fts_table="graphrag_fts",
            id_column="d.id",
        )
        return [
            RecallItem(
                id=row["id"],
                kind="graphrag",
                text=f"{row['title']}: {row['text'][:1_400]}",
                knowledge_space=row["space_slug"],
                source_label=f"Tekion GraphRAG index {row['version']}",
                occurred_at=None,
                evidence_locator=row["source_uri"],
                status="active",
                epistemic_status="verified",
                why_matched="versioned Tekion/dealership source matched the query",
                score=self._lexical_score(
                    f"{row['title']} {row['text']}", terms, base=2.5
                ),
                metadata={
                    "index_version": row["version"],
                    "document_id": row["document_id"],
                },
            )
            for row in rows
        ]

    def _recall_graphrag_communities(
        self,
        connection: sqlite3.Connection,
        query: str,
        terms: Sequence[str],
        spaces: Mapping[str, str],
        limit: int,
    ) -> list[RecallItem]:
        """Retrieve derived community reports for GraphRAG-global questions.

        These are explicitly labeled inferred/derived. Exact procedural claims
        should still resolve through the higher-fidelity document channel.
        Combining this channel with entity/document retrieval supplies a local
        + global (DRIFT-like) evidence bundle without allowing reports to
        masquerade as primary source text.
        """
        rows = self._search_rows(
            connection,
            f"SELECT c.*, k.slug AS space_slug, i.version FROM communities c "
            f"JOIN knowledge_spaces k ON k.id=c.knowledge_space_id "
            f"JOIN graphrag_indexes i ON i.brain_id=c.brain_id AND i.state='active' "
            f"WHERE c.brain_id=? AND k.slug='tekion' AND c.stale=0 "
            f"AND c.report IS NOT NULL "
            f"AND c.knowledge_space_id IN ({','.join('?' for _ in spaces)})",
            (self.brain_id, *spaces.keys()),
            text_column="COALESCE(c.label,'') || ' ' || COALESCE(c.report,'')",
            terms=terms,
            limit=limit,
        )
        global_query = bool(
            {
                "across",
                "overall",
                "themes",
                "theme",
                "patterns",
                "major",
                "summary",
            }.intersection(terms)
        )
        items: list[RecallItem] = []
        for row in rows:
            report = str(row["report"] or "")
            label = str(row["label"] or "Knowledge community")
            items.append(
                RecallItem(
                    id=str(row["id"]),
                    kind="community",
                    text=f"{label}: {report[:1_600]}",
                    knowledge_space=str(row["space_slug"]),
                    source_label=(
                        f"derived Tekion GraphRAG community report {row['version']}"
                    ),
                    occurred_at=str(row["generated_at"]),
                    evidence_locator=(
                        f"graphrag:{row['version']}:community:{row['id']}"
                    ),
                    status="active",
                    epistemic_status="inferred",
                    why_matched=(
                        "global GraphRAG community matched the corpus-wide query"
                        if global_query
                        else "GraphRAG community report matched the query"
                    ),
                    score=self._lexical_score(
                        f"{label} {report}", terms, base=3.0 if global_query else 1.8
                    ),
                    metadata={
                        "index_version": str(row["version"]),
                        "community_level": int(row["level"]),
                        "member_ids": json.loads(row["member_ids_json"] or "[]")[:100],
                        "derived": True,
                    },
                )
            )
        return items

    def _search_rows(
        self,
        connection: sqlite3.Connection,
        base_sql: str,
        params: Sequence[Any],
        *,
        text_column: str,
        terms: Sequence[str],
        limit: int,
        fts_table: str = "",
        id_column: str = "",
    ) -> list[sqlite3.Row]:
        if self._fts_available and fts_table and id_column:
            if fts_table not in {
                "evidence_fts",
                "memory_fts",
                "entity_fts",
                "graphrag_fts",
            }:
                raise ValueError("unsupported Cortex FTS projection")
            # Quoted OR terms avoid interpreting user text as FTS operators.
            # `_terms` excludes quotes, but escape defensively for direct tests.
            fts_query = " OR ".join(
                f'"{str(term).replace(chr(34), chr(34) * 2)}"'
                for term in terms
                if str(term).strip()
            )
            if fts_query:
                try:
                    rows = connection.execute(
                        f"{base_sql} AND {id_column} IN ("
                        f"SELECT id FROM {fts_table} WHERE {fts_table} MATCH ? "
                        f"ORDER BY bm25({fts_table}) LIMIT ?) LIMIT ?",
                        (*params, fts_query, limit, limit),
                    ).fetchall()
                    if rows:
                        return list(rows)
                except sqlite3.OperationalError:
                    # SQLite builds without FTS5 and malformed legacy virtual
                    # tables degrade to the bounded LIKE channel.
                    pass
        clauses = " OR ".join(f"LOWER({text_column}) LIKE ?" for _ in terms)
        # Joined queries make the pseudo-column ROWID ambiguous. Candidate
        # ranking happens in Python immediately after this bounded fetch, so
        # no database-level ordering is needed here.
        sql = f"{base_sql} AND ({clauses}) LIMIT ?"
        return list(
            connection.execute(
                sql, (*params, *(f"%{term}%" for term in terms), limit)
            ).fetchall()
        )

    def tombstone_memory(
        self, memory_id: str, *, principal_id: str | None = None
    ) -> None:
        now = utc_now()
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE memory_records SET status='deleted', deleted_at=?, updated_at=? "
                "WHERE id=? AND brain_id=?",
                (now, now, memory_id, self.brain_id),
            )
            if cursor.rowcount != 1:
                raise LookupError("memory was not found")
            if self._fts_available:
                connection.execute("DELETE FROM memory_fts WHERE id=?", (memory_id,))
            self.audit_in_transaction(
                connection,
                principal_id=principal_id,
                action="memory.delete",
                resource_type="memory",
                resource_id=memory_id,
                outcome="succeeded",
            )

    def erase_memory_permanently(
        self, memory_id: str, *, principal_id: str | None = None
    ) -> dict[str, int]:
        """Irreversibly scrub one memory and its Cortex-local provenance.

        Evidence can support several derived records, so an exact fact-level
        inverse is impossible after semantic projection. This deliberately
        favors privacy: every Cortex memory/relation/entity supported only by
        the target evidence is removed or content-scrubbed, affected compiled
        projections are deleted, and source evidence is reduced to a
        non-content receipt. The ordinary Atlas transcript, external provider
        logs, and offline backups are separate systems and are not represented
        as covered by this transaction.
        """

        now = utc_now()
        erased_marker = "[permanently erased by customer request]"
        counts = {
            "evidence": 0,
            "memories": 0,
            "entities": 0,
            "relations": 0,
            "observations": 0,
        }
        with self.transaction() as connection:
            target = connection.execute(
                "SELECT id, knowledge_space_id FROM memory_records "
                "WHERE id=? AND brain_id=?",
                (memory_id, self.brain_id),
            ).fetchone()
            if target is None:
                raise LookupError("memory was not found")
            space_id = str(target["knowledge_space_id"])
            evidence_ids = tuple(
                str(row["evidence_id"])
                for row in connection.execute(
                    "SELECT evidence_id FROM memory_evidence WHERE memory_id=?",
                    (memory_id,),
                ).fetchall()
            )
            marks = ",".join("?" for _ in evidence_ids)

            related_memory_ids = {memory_id}
            relation_ids: set[str] = set()
            entity_ids: set[str] = set()
            session_ids: set[str] = set()
            observation_ids: set[str] = set()
            affected_aliases: set[tuple[str, str, str]] = set()
            if evidence_ids:
                related_memory_ids.update(
                    str(row["memory_id"])
                    for row in connection.execute(
                        f"SELECT DISTINCT memory_id FROM memory_evidence "
                        f"WHERE evidence_id IN ({marks})",
                        evidence_ids,
                    ).fetchall()
                )
                relation_ids.update(
                    str(row["relation_id"])
                    for row in connection.execute(
                        f"SELECT DISTINCT relation_id FROM relation_evidence "
                        f"WHERE evidence_id IN ({marks})",
                        evidence_ids,
                    ).fetchall()
                )
                entity_ids.update(
                    str(row["entity_id"])
                    for row in connection.execute(
                        f"SELECT DISTINCT entity_id FROM entity_evidence "
                        f"WHERE evidence_id IN ({marks})",
                        evidence_ids,
                    ).fetchall()
                )
                affected_aliases.update(
                    (
                        str(row["entity_id"]),
                        str(row["knowledge_space_id"]),
                        str(row["normalized_alias"]),
                    )
                    for row in connection.execute(
                        f"SELECT DISTINCT entity_id, knowledge_space_id, "
                        f"normalized_alias FROM entity_alias_evidence "
                        f"WHERE evidence_id IN ({marks})",
                        evidence_ids,
                    ).fetchall()
                )
                affected_aliases.update(
                    (
                        str(row["entity_id"]),
                        str(row["knowledge_space_id"]),
                        str(row["normalized_alias"]),
                    )
                    for row in connection.execute(
                        f"SELECT entity_id, knowledge_space_id, normalized_alias "
                        f"FROM entity_aliases WHERE evidence_id IN ({marks})",
                        evidence_ids,
                    ).fetchall()
                )
                session_ids.update(
                    str(row["session_id"])
                    for row in connection.execute(
                        f"SELECT DISTINCT session_id FROM evidence_items "
                        f"WHERE brain_id=? AND id IN ({marks}) "
                        "AND session_id IS NOT NULL",
                        (self.brain_id, *evidence_ids),
                    ).fetchall()
                )
                for row in connection.execute(
                    "SELECT id, evidence_ids_json FROM observations WHERE brain_id=?",
                    (self.brain_id,),
                ).fetchall():
                    try:
                        cited = {
                            str(value)
                            for value in json.loads(row["evidence_ids_json"] or "[]")
                        }
                    except (TypeError, json.JSONDecodeError):
                        cited = set()
                    if cited.intersection(evidence_ids):
                        observation_ids.add(str(row["id"]))

                connection.execute(
                    f"DELETE FROM relation_evidence WHERE evidence_id IN ({marks})",
                    evidence_ids,
                )
                connection.execute(
                    f"DELETE FROM entity_alias_evidence WHERE evidence_id IN ({marks})",
                    evidence_ids,
                )
                connection.execute(
                    f"UPDATE entity_aliases SET evidence_id=NULL "
                    f"WHERE evidence_id IN ({marks})",
                    evidence_ids,
                )
                connection.executemany(
                    "DELETE FROM entity_aliases WHERE entity_id=? "
                    "AND knowledge_space_id=? AND normalized_alias=? "
                    "AND evidence_id IS NULL AND NOT EXISTS ("
                    "SELECT 1 FROM entity_alias_evidence a "
                    "WHERE a.entity_id=entity_aliases.entity_id "
                    "AND a.knowledge_space_id=entity_aliases.knowledge_space_id "
                    "AND a.normalized_alias=entity_aliases.normalized_alias)",
                    list(affected_aliases),
                )
                connection.execute(
                    f"DELETE FROM entity_evidence WHERE evidence_id IN ({marks})",
                    evidence_ids,
                )

            # Relations and entities with no remaining live support are
            # projections of the erased source and must not remain visible.
            unsupported_relations = {
                relation_id
                for relation_id in relation_ids
                if connection.execute(
                    "SELECT 1 FROM relation_evidence re JOIN evidence_items e "
                    "ON e.id=re.evidence_id WHERE re.relation_id=? "
                    "AND e.tombstoned_at IS NULL LIMIT 1",
                    (relation_id,),
                ).fetchone()
                is None
            }
            unsupported_entities = {
                entity_id
                for entity_id in entity_ids
                if connection.execute(
                    "SELECT 1 FROM entity_evidence ee JOIN evidence_items e "
                    "ON e.id=ee.evidence_id WHERE ee.entity_id=? "
                    "AND e.tombstoned_at IS NULL LIMIT 1",
                    (entity_id,),
                ).fetchone()
                is None
            }
            if unsupported_entities:
                entity_marks = ",".join("?" for _ in unsupported_entities)
                endpoint_relations = {
                    str(row["id"])
                    for row in connection.execute(
                        f"SELECT id FROM relations WHERE brain_id=? AND "
                        f"(subject_entity_id IN ({entity_marks}) OR "
                        f"object_entity_id IN ({entity_marks}))",
                        (
                            self.brain_id,
                            *unsupported_entities,
                            *unsupported_entities,
                        ),
                    ).fetchall()
                }
                unsupported_relations.update(endpoint_relations)
            if unsupported_relations:
                relation_marks = ",".join("?" for _ in unsupported_relations)
                connection.execute(
                    f"DELETE FROM relations WHERE brain_id=? "
                    f"AND id IN ({relation_marks})",
                    (self.brain_id, *unsupported_relations),
                )
            if unsupported_entities:
                entity_marks = ",".join("?" for _ in unsupported_entities)
                if self._fts_available:
                    connection.execute(
                        f"DELETE FROM entity_fts WHERE id IN ({entity_marks})",
                        tuple(unsupported_entities),
                    )
                connection.execute(
                    f"DELETE FROM entities WHERE brain_id=? AND id IN ({entity_marks})",
                    (self.brain_id, *unsupported_entities),
                )

            memory_marks = ",".join("?" for _ in related_memory_ids)
            connection.execute(
                f"UPDATE memory_records SET canonical_statement=?, statement_hash=?, "
                "subject_entity_id=NULL, object_entity_id=NULL, status='deleted', "
                "metadata_json='{}', deleted_at=?, updated_at=? "
                f"WHERE brain_id=? AND id IN ({memory_marks})",
                (
                    erased_marker,
                    stable_hash(erased_marker),
                    now,
                    now,
                    self.brain_id,
                    *related_memory_ids,
                ),
            )
            connection.execute(
                f"DELETE FROM memory_evidence WHERE memory_id IN ({memory_marks})",
                tuple(related_memory_ids),
            )
            if self._fts_available:
                connection.execute(
                    f"DELETE FROM memory_fts WHERE id IN ({memory_marks})",
                    tuple(related_memory_ids),
                )

            if evidence_ids:
                connection.execute(
                    f"DELETE FROM work_events WHERE brain_id=? "
                    f"AND evidence_id IN ({marks})",
                    (self.brain_id, *evidence_ids),
                )
                connection.executemany(
                    "UPDATE evidence_items SET content=?, content_hash=?, "
                    "source_locator=?, actor_principal_id=NULL, metadata_json='{}', "
                    "tombstoned_at=? WHERE id=? AND brain_id=?",
                    [
                        (
                            erased_marker,
                            stable_hash(erased_marker, evidence_id),
                            f"erased:{evidence_id}",
                            now,
                            evidence_id,
                            self.brain_id,
                        )
                        for evidence_id in evidence_ids
                    ],
                )
                if self._fts_available:
                    connection.execute(
                        f"DELETE FROM evidence_fts WHERE id IN ({marks})",
                        evidence_ids,
                    )
            if observation_ids:
                observation_marks = ",".join("?" for _ in observation_ids)
                connection.execute(
                    f"DELETE FROM observations WHERE brain_id=? "
                    f"AND id IN ({observation_marks})",
                    (self.brain_id, *observation_ids),
                )
            # Synthesis has no exact inverse-provenance map. Rebuild it from
            # remaining live evidence instead of risking residual text.
            connection.execute(
                "DELETE FROM compiled_views WHERE brain_id=? AND knowledge_space_id=?",
                (self.brain_id, space_id),
            )
            connection.execute(
                "DELETE FROM communities WHERE brain_id=? AND knowledge_space_id=?",
                (self.brain_id, space_id),
            )
            if session_ids:
                logical_ids = {
                    str(row["logical_conversation_id"])
                    for row in connection.execute(
                        f"SELECT logical_conversation_id FROM sessions WHERE brain_id=? "
                        f"AND id IN ({','.join('?' for _ in session_ids)})",
                        (self.brain_id, *session_ids),
                    ).fetchall()
                }
                if logical_ids:
                    logical_marks = ",".join("?" for _ in logical_ids)
                    connection.execute(
                        "UPDATE sessions SET summary=NULL, evidence_hash=NULL, "
                        f"updated_at=? WHERE brain_id=? AND logical_conversation_id "
                        f"IN ({logical_marks})",
                        (now, self.brain_id, *logical_ids),
                    )
                session_marks = ",".join("?" for _ in session_ids)
                connection.execute(
                    f"DELETE FROM retrieval_runs WHERE brain_id=? "
                    f"AND session_id IN ({session_marks})",
                    (self.brain_id, *session_ids),
                )
            connection.execute(
                "UPDATE retrieval_runs SET query_preview=?, explanation_json='[]' "
                "WHERE brain_id=?",
                (erased_marker, self.brain_id),
            )
            counts.update({
                "evidence": len(evidence_ids),
                "memories": len(related_memory_ids),
                "entities": len(unsupported_entities),
                "relations": len(unsupported_relations),
                "observations": len(observation_ids),
            })
            self.audit_in_transaction(
                connection,
                principal_id=principal_id,
                action="memory.permanent_erase",
                resource_type="memory",
                resource_id=memory_id,
                outcome="succeeded",
                details=counts,
            )

        # `secure_delete` covers freed cells; checkpoint + VACUUM removes WAL
        # and free-page copies from the live profile database. Offline backups
        # remain governed by their own retention/deletion system.
        with self.connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            connection.execute("VACUUM")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._secure_sidecars()
        return counts

    def prune_raw_evidence(self, retention_days: int) -> int:
        """Remove expired raw content while retaining non-content receipts.

        A zero-day policy means "retain until explicit deletion" and is the
        local default. Catalog/protected/legal-hold evidence is excluded from
        this raw-transcript policy. Durable memories remain as derived records,
        but expired hot observations and retrieval previews are scrubbed so
        they cannot become a shadow copy of the deleted source text.
        """
        days = max(0, min(3_650, int(retention_days)))
        if days == 0:
            return 0
        cutoff = (
            (datetime.now(timezone.utc) - timedelta(days=days))
            .isoformat()
            .replace("+00:00", "Z")
        )
        now = utc_now()
        with self.transaction() as connection:
            rows = connection.execute(
                "SELECT id FROM evidence_items WHERE brain_id=? AND tombstoned_at IS NULL "
                "AND ingested_at<? AND retention_class NOT IN "
                "('catalog','protected','legal_hold')",
                (self.brain_id, cutoff),
            ).fetchall()
            evidence_ids = {str(row["id"]) for row in rows}
            if not evidence_ids:
                return 0
            marks = ",".join("?" for _ in evidence_ids)
            connection.execute(
                f"UPDATE evidence_items SET content='[expired by retention policy]', "
                f"tombstoned_at=? WHERE brain_id=? AND id IN ({marks})",
                (now, self.brain_id, *sorted(evidence_ids)),
            )
            if self._fts_available:
                connection.execute(
                    f"DELETE FROM evidence_fts WHERE id IN ({marks})",
                    tuple(sorted(evidence_ids)),
                )

            observations = connection.execute(
                "SELECT id, evidence_ids_json FROM observations WHERE brain_id=?",
                (self.brain_id,),
            ).fetchall()
            expired_observations: list[str] = []
            for observation in observations:
                try:
                    supports = {
                        str(value)
                        for value in json.loads(
                            observation["evidence_ids_json"] or "[]"
                        )
                    }
                except (TypeError, json.JSONDecodeError):
                    supports = set()
                if supports.intersection(evidence_ids):
                    expired_observations.append(str(observation["id"]))
            if expired_observations:
                observation_marks = ",".join("?" for _ in expired_observations)
                connection.execute(
                    f"UPDATE observations SET normalized_text="
                    f"'[expired by retention policy]', entity_candidates_json='[]', "
                    f"processing_state='expired', processed_at=? WHERE brain_id=? "
                    f"AND id IN ({observation_marks})",
                    (now, self.brain_id, *expired_observations),
                )
            connection.execute(
                "UPDATE retrieval_runs SET query_preview='[expired by retention policy]' "
                "WHERE brain_id=? AND created_at<?",
                (self.brain_id, cutoff),
            )
            self.audit_in_transaction(
                connection,
                principal_id=None,
                action="evidence.retention_purge",
                resource_type="evidence_batch",
                resource_id=None,
                outcome="succeeded",
                details={"count": len(evidence_ids), "retention_days": days},
            )
            return len(evidence_ids)

    def audit_in_transaction(
        self,
        connection: sqlite3.Connection,
        *,
        principal_id: str | None,
        action: str,
        resource_type: str,
        resource_id: str | None,
        outcome: str,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO cortex_audit(id, brain_id, principal_id, action, resource_type, "
            "resource_id, outcome, details_json, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                new_id("audit"),
                self.brain_id,
                principal_id,
                action,
                resource_type,
                resource_id,
                outcome,
                _json(sanitize_for_storage(dict(details or {}))),
                utc_now(),
            ),
        )

    def record_health_event(
        self,
        *,
        status: str,
        code: str,
        details: Mapping[str, Any] | None = None,
    ) -> str:
        """Persist a bounded non-job health event without raw exception text."""
        normalized_status = str(status or "degraded").strip().lower()[:32] or "degraded"
        normalized_code = str(code or "runtime_event").strip()[:120]
        latest = self._latest_runtime_health_event(normalized_code)
        if latest and str(latest["status"]) == normalized_status:
            return str(latest["id"])
        report = {
            "event": normalized_code,
            "details": sanitize_for_storage(dict(details or {})),
        }
        report_id = new_id("health")
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO health_reports(id, brain_id, job_id, status, "
                "report_json, created_at) VALUES(?,?,?,?,?,?)",
                (
                    report_id,
                    self.brain_id,
                    None,
                    normalized_status,
                    _json(report),
                    utc_now(),
                ),
            )
            self.audit_in_transaction(
                connection,
                principal_id=None,
                action="cortex.health",
                resource_type="health_report",
                resource_id=report_id,
                outcome=normalized_status,
                details={"event": normalized_code},
            )
        return report_id

    def _latest_runtime_health_event(self, code: str) -> dict[str, Any] | None:
        """Return the newest non-job health state for one bounded event code."""

        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, status, report_json, created_at FROM health_reports "
                "WHERE brain_id=? AND job_id IS NULL ORDER BY created_at DESC LIMIT 500",
                (self.brain_id,),
            ).fetchall()
        for row in rows:
            try:
                report = json.loads(row["report_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(report, Mapping) and str(report.get("event") or "") == code:
                return dict(row)
        return None

    def resolve_health_event(
        self, code: str, *, details: Mapping[str, Any] | None = None
    ) -> str | None:
        """Resolve one runtime issue without clearing unrelated health codes."""

        normalized_code = str(code or "runtime_event").strip()[:120]
        latest = self._latest_runtime_health_event(normalized_code)
        if latest is None or str(latest["status"]) in {
            "healthy",
            "succeeded",
            "success",
        }:
            return None
        return self.record_health_event(
            status="healthy",
            code=normalized_code,
            details=details or {"resolved": True},
        )

    def health(self) -> dict[str, Any]:
        pending_boundaries = self.pending_boundary_count()
        with self.connect() as connection:
            counts = {}
            for name in (
                "sessions",
                "evidence_items",
                "observations",
                "memory_records",
                "entities",
                "relations",
                "cognitive_jobs",
                "graphrag_documents",
            ):
                counts[name] = int(
                    connection.execute(
                        f"SELECT COUNT(*) AS n FROM {name} WHERE brain_id=?",
                        (self.brain_id,),
                    ).fetchone()["n"]
                )
            pending = int(
                connection.execute(
                    "SELECT COUNT(*) AS n FROM cognitive_jobs WHERE brain_id=? AND state IN ('queued','running')",
                    (self.brain_id,),
                ).fetchone()["n"]
            )
            oldest_due = connection.execute(
                "SELECT MIN(COALESCE(next_attempt_at, scheduled_at)) AS due_at "
                "FROM cognitive_jobs WHERE brain_id=? AND state='queued'",
                (self.brain_id,),
            ).fetchone()["due_at"]
            oldest_expired_lease = connection.execute(
                "SELECT MIN(lease_expires_at) AS expires_at FROM cognitive_jobs "
                "WHERE brain_id=? AND state='running' AND lease_expires_at IS NOT NULL "
                "AND lease_expires_at<=?",
                (self.brain_id, utc_now()),
            ).fetchone()["expires_at"]
            expired_running = int(
                connection.execute(
                    "SELECT COUNT(*) AS n FROM cognitive_jobs WHERE brain_id=? "
                    "AND state='running' AND (lease_expires_at IS NULL OR lease_expires_at<=?)",
                    (self.brain_id, utc_now()),
                ).fetchone()["n"]
            )
            last_report = connection.execute(
                "SELECT status, report_json, created_at FROM health_reports WHERE brain_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (self.brain_id,),
            ).fetchone()
            runtime_rows = connection.execute(
                "SELECT status, report_json, created_at FROM health_reports "
                "WHERE brain_id=? AND job_id IS NULL ORDER BY created_at DESC LIMIT 1000",
                (self.brain_id,),
            ).fetchall()
            failed_jobs = int(
                connection.execute(
                    "SELECT COUNT(*) AS n FROM cognitive_jobs WHERE brain_id=? "
                    "AND state IN ('failed','dead_letter')",
                    (self.brain_id,),
                ).fetchone()["n"]
            )
        runtime_states: dict[str, str] = {}
        for row in runtime_rows:
            try:
                report = json.loads(row["report_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            code = str(report.get("event") or "") if isinstance(report, Mapping) else ""
            if code and code not in runtime_states:
                runtime_states[code] = str(row["status"])
        unresolved_runtime = sorted(
            code
            for code, status in runtime_states.items()
            if status not in {"healthy", "succeeded", "success"}
        )

        def overdue_age_seconds(value: Any) -> int:
            if not value:
                return 0
            try:
                created = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                return max(
                    0, int((datetime.now(timezone.utc) - created).total_seconds())
                )
            except ValueError:
                return 0

        oldest_overdue_age_seconds = max(
            overdue_age_seconds(oldest_due),
            overdue_age_seconds(oldest_expired_lease),
        )
        stale_pending = oldest_overdue_age_seconds >= 15 * 60 or expired_running > 0
        return {
            "name": "Atlas Cortex",
            "status": (
                "healthy"
                if not unresolved_runtime
                and not failed_jobs
                and not stale_pending
                and not pending_boundaries
                else "degraded"
            ),
            "schema_version": CORTEX_SCHEMA_VERSION,
            "brain_id": self.brain_id,
            "database_path": str(self.path),
            "fts_available": self._fts_available,
            "pending_jobs": pending,
            # Keep the legacy name for DTO compatibility, but measure time
            # since work became due rather than time since it was created.
            "oldest_pending_age_seconds": oldest_overdue_age_seconds,
            "oldest_overdue_age_seconds": oldest_overdue_age_seconds,
            "expired_running_jobs": expired_running,
            "stale_pending_jobs": stale_pending,
            "pending_session_boundaries": pending_boundaries,
            "unresolved_runtime_events": unresolved_runtime,
            "counts": counts,
            "last_dream": (
                {
                    "status": last_report["status"],
                    "report": json.loads(last_report["report_json"]),
                    "created_at": last_report["created_at"],
                }
                if last_report
                else None
            ),
        }

    def backup_paths(self) -> list[str]:
        return [str(self.path), str(self.path.parent)]
