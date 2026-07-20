from __future__ import annotations

import json
import threading
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from altas.cortex.config import CortexConfig
from altas.cortex.dream import (
    ACTIONS,
    COMMUNITY_ALGORITHM_VERSION,
    CortexDreamError,
    CortexModelRoute,
    CortexOutputError,
    CortexRouteError,
    DreamCandidate,
    DreamProcessor,
    TRIAGE_SCHEMA_VERSION,
    UsageTotals,
    resolve_model_route,
    validate_triage_output,
)
from altas.cortex.graph import build_graph_overview
from altas.cortex.models import EvidenceInput
from altas.cortex.scheduler import (
    WAKE_CRON_NAME,
    WAKE_CRON_SCRIPT,
    CortexDreamSchedule,
    enqueue_cortex_wake,
    ensure_cortex_wake_cron,
    recover_managed_cortex_runtimes,
    reconcile_cortex_runtime,
    run_cortex_wake,
    stop_cortex_runtimes,
)
from altas.cortex.store import CortexStore, stable_hash
from altas.cortex.worker import CortexDreamSupervisor, CortexDreamWorker


def _raw_config(
    home: Path, *, provider: str = "openrouter", model: str = "fast-model"
) -> dict[str, Any]:
    return {
        "model": {"provider": provider, "default": model},
        "auxiliary": {
            "cortex_triage": {
                "provider": provider,
                "model": "cheap-memory-model",
                "timeout": 30,
                "max_tokens": 2_000,
            },
            "cortex_reasoning": {
                "provider": provider,
                "model": "cheap-memory-model",
                "timeout": 60,
                "max_tokens": 2_000,
            },
        },
        "cortex": {
            "enabled": True,
            "timezone": "UTC",
            "storage": {"path": "cortex/cortex.db"},
            "dream": {
                "enabled": True,
                "local_time": "04:00",
                "startup_catchup": True,
                "poll_seconds": 30,
                "lease_seconds": 60,
                "max_batch": 20,
            },
            "security": {
                "redact_secrets": True,
                "sensitive_requires_review": True,
                "approved_model_providers": [],
            },
        },
    }


def _runtime(
    tmp_path: Path, *, owner: str = "customer-a"
) -> tuple[CortexStore, CortexConfig, dict[str, Any]]:
    raw = _raw_config(tmp_path)
    config = CortexConfig.from_mapping(raw, tmp_path)
    store = CortexStore(config.database_path, owner_customer_id=owner)
    store.initialize()
    return store, config, raw


def _observation(
    store: CortexStore,
    *,
    session_id: str = "session-1",
    source_type: str = "user_message",
    content: str = "I prefer email follow-ups.",
) -> tuple[str, str]:
    store.ensure_session(session_id)
    evidence_id = store.append_evidence(
        session_id,
        EvidenceInput(
            source_type=source_type,  # type: ignore[arg-type]
            content=content,
            source_locator=f"{session_id}:{source_type}:1",
        ),
    )
    observation_id = store.add_observation(
        session_id=session_id,
        kind="preference",
        text=content,
        evidence_ids=[evidence_id],
    )
    return evidence_id, observation_id


def _operation(
    observation_id: str,
    evidence_id: str,
    *,
    action: str = "promote_new",
    statement: str = "The customer prefers email follow-ups.",
    rationale: str = "This stable preference improves future communication.",
) -> dict[str, Any]:
    return {
        "observation_id": observation_id,
        "action": action,
        "memory_kind": "preference",
        "statement": statement,
        "evidence_ids": [evidence_id],
        "target_memory_id": None,
        "entities": [],
        "valid_from": None,
        "valid_until": None,
        "rationale": rationale,
        "sensitivity": "private",
        "retention": "long",
        "missing_information": "",
    }


def _response(
    payload: Any, *, prompt_tokens: int = 7, completion_tokens: int = 3
) -> Any:
    content = payload if isinstance(payload, str) else json.dumps(payload)
    return SimpleNamespace(
        model="fast-model",
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


class _FakeLLM:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("unexpected LLM call")
        return self.responses.pop(0)


def _job(
    store: CortexStore,
    evidence_id: str,
    *,
    session_id: str = "session-1",
    suffix: str = "one",
    max_attempts: int = 5,
) -> str:
    del suffix
    spec = store.lineage_distill_spec(session_id)
    store.finalize_session(session_id)
    with store.connect() as connection:
        row = connection.execute(
            "SELECT * FROM cognitive_jobs WHERE brain_id=? "
            "AND job_type='session_distill' AND input_hash=?",
            (store.brain_id, spec["input_hash"]),
        ).fetchone()
    assert row is not None
    job_id = str(row["id"])
    if str(row["state"]) == "succeeded":
        due = store.pending_observations(
            limit=1_000,
            session_ids=tuple(spec["input_data"]["session_ids"]),
            evidence_ids=tuple(spec["input_data"]["evidence_ids"]),
            knowledge_space="personal",
        )
        signature = [
            f"{item['id']}:{item.get('triage_attempts', 0)}:"
            f"{item.get('next_triage_at') or ''}"
            for item in due
        ]
        job_id = store.enqueue_session_distill_continuation(
            job_id,
            remaining_signature=signature,
        )
    with store.transaction() as connection:
        connection.execute(
            "UPDATE cognitive_jobs SET max_attempts=? WHERE id=?",
            (max_attempts, job_id),
        )
    assert evidence_id in spec["input_data"]["evidence_ids"]
    return job_id


def _succeeded_distill_root(
    store: CortexStore,
    session_id: str,
    *,
    with_observation: bool = False,
) -> tuple[str, str]:
    store.ensure_session(session_id)
    evidence_id = store.append_evidence(
        session_id,
        EvidenceInput(
            source_type="user_message",
            content=f"Durable evidence for {session_id}.",
            source_locator=f"{session_id}:user:1",
        ),
    )
    if with_observation:
        store.add_observation(
            session_id=session_id,
            kind="preference",
            text=f"Pending memory for {session_id}.",
            evidence_ids=[evidence_id],
        )
    spec = store.lineage_distill_spec(session_id)
    store.finalize_session(session_id)
    with store.transaction() as connection:
        root = connection.execute(
            "SELECT id FROM cognitive_jobs WHERE brain_id=? "
            "AND job_type='session_distill' AND input_hash=?",
            (store.brain_id, spec["input_hash"]),
        ).fetchone()
        assert root is not None
        connection.execute(
            "UPDATE cognitive_jobs SET state='succeeded', completed_at=? WHERE id=?",
            ("2026-07-14T00:00:00Z", root["id"]),
        )
    return evidence_id, str(root["id"])


def test_route_resolution_is_explicit_and_managed_mode_fails_closed(
    tmp_path: Path,
) -> None:
    raw = _raw_config(tmp_path)
    route = resolve_model_route("cortex_triage", config=raw, environ={})
    assert (route.provider, route.model) == ("openrouter", "cheap-memory-model")

    inherited_chat = _raw_config(tmp_path)
    inherited_chat["auxiliary"]["cortex_triage"].update(
        {"provider": "auto", "model": ""}
    )
    with pytest.raises(CortexRouteError, match="dedicated explicit provider"):
        resolve_model_route("cortex_triage", config=inherited_chat, environ={})

    same_as_chat = _raw_config(tmp_path)
    same_as_chat["auxiliary"]["cortex_triage"]["model"] = "fast-model"
    with pytest.raises(CortexRouteError, match="separate from the conversational"):
        resolve_model_route("cortex_triage", config=same_as_chat, environ={})

    aliased_chat = _raw_config(
        tmp_path,
        provider="anthropic",
        model="anthropic/claude-haiku-4-5-20251001",
    )
    aliased_chat["auxiliary"]["cortex_triage"].update(
        {
            "provider": "claude",
            "model": "claude/claude-haiku-4-5-20251001",
        }
    )
    with pytest.raises(CortexRouteError, match="separate from the conversational"):
        resolve_model_route("cortex_triage", config=aliased_chat, environ={})

    managed = dict(raw)
    with pytest.raises(CortexRouteError, match="Atlas-approved"):
        resolve_model_route(
            "cortex_triage",
            config=managed,
            environ={"ATLAS_MANAGED_MODE": "1"},
        )

    managed = _raw_config(tmp_path, provider="altas", model="atlas-memory-model")
    incomplete = {"ATLAS_MANAGED_MODE": "1", "ATLAS_DEVICE_TOKEN": "device"}
    with pytest.raises(CortexRouteError, match="request-scoped"):
        resolve_model_route("cortex_triage", config=managed, environ=incomplete)

    complete = {
        "ATLAS_MANAGED_MODE": "1",
        "ATLAS_DEVICE_TOKEN": "device",
        "ATLAS_LEASE_TOKEN": "lease",
        "ATLAS_TENANT_ID": "tenant",
        "ATLAS_STORE_ID": "store",
        "ATLAS_AGENT_ID": "agent",
        "ATLAS_JOB_ID": "job",
        "ATLAS_CLAIM_TOKEN": "claim-token-that-is-long-enough-for-the-gateway",
        "ATLAS_JOB_CAPABILITY": "cortex.memory_maintenance",
        "ATLAS_CONTROL_PLANE_URL": "https://control.example.test",
    }
    route = resolve_model_route("cortex_triage", config=managed, environ=complete)
    assert route.provider == "altas"
    assert route.managed_approved is True
    assert route.base_url == "https://control.example.test/v1"

    inherited = _raw_config(tmp_path, provider="altas", model="frontier-chat-model")
    inherited["auxiliary"]["cortex_triage"]["provider"] = "auto"
    inherited["auxiliary"]["cortex_triage"]["model"] = ""
    with pytest.raises(CortexRouteError, match="dedicated explicit provider"):
        resolve_model_route("cortex_triage", config=inherited, environ=complete)

    unrelated_job = {**complete, "ATLAS_JOB_CAPABILITY": "agent.turn"}
    with pytest.raises(CortexRouteError, match="dedicated memory-maintenance"):
        resolve_model_route("cortex_triage", config=managed, environ=unrelated_job)

    override = _raw_config(tmp_path, provider="altas", model="atlas-memory-model")
    override["auxiliary"]["cortex_triage"]["base_url"] = "https://unapproved.invalid/v1"
    with pytest.raises(CortexRouteError, match="cannot override"):
        resolve_model_route("cortex_triage", config=override, environ=complete)

    cross_provider = _raw_config(tmp_path, provider="altas", model="atlas-memory-model")
    cross_provider["auxiliary"]["cortex_triage"]["provider"] = "openrouter"
    cross_provider["auxiliary"]["cortex_triage"]["model"] = "external-model"
    with pytest.raises(CortexRouteError, match="Atlas-approved"):
        resolve_model_route("cortex_triage", config=cross_provider, environ=complete)


def test_explicit_custom_route_may_reuse_main_endpoint(tmp_path: Path) -> None:
    raw = _raw_config(tmp_path, provider="custom", model="local-memory-model")
    raw["model"].update({
        "base_url": "http://127.0.0.1:11434/v1",
        "api_key": "local-only-key",
        "api_mode": "chat_completions",
    })

    route = resolve_model_route("cortex_triage", config=raw, environ={})

    assert route.provider == "custom"
    assert route.model == "cheap-memory-model"
    assert route.base_url == "http://127.0.0.1:11434/v1"
    assert route.api_key == "local-only-key"
    assert route.api_mode == "chat_completions"

    raw["auxiliary"]["cortex_triage"]["provider"] = "openrouter"
    raw["auxiliary"]["cortex_triage"]["model"] = ""
    with pytest.raises(CortexRouteError, match="dedicated explicit model"):
        resolve_model_route("cortex_triage", config=raw, environ={})


def test_worker_repairs_json_once_promotes_with_usage_and_no_fallback(
    tmp_path: Path,
) -> None:
    store, config, raw = _runtime(tmp_path)
    evidence_id, observation_id = _observation(store)
    job_id = _job(store, evidence_id)
    valid = {
        "schema_version": TRIAGE_SCHEMA_VERSION,
        "operations": [_operation(observation_id, evidence_id)],
    }
    llm = _FakeLLM(_response("not-json"), _response(valid))

    result = CortexDreamWorker(
        store, config, raw_config=raw, llm_call=llm, owner="worker-a"
    ).run_once()

    assert result.status == "succeeded"
    assert len(llm.calls) == 2
    assert all(call["provider"] == "openrouter" for call in llm.calls)
    assert all(call["model"] == "cheap-memory-model" for call in llm.calls)
    assert all(call["fallback_policy"] == "none" for call in llm.calls)
    assert all(call["bounded_output"] is True for call in llm.calls)
    assert all(call["max_tokens"] == 2000 for call in llm.calls)
    assert all(call["task"] == "cortex_triage" for call in llm.calls)
    with store.connect() as connection:
        memory = connection.execute(
            "SELECT id, canonical_statement FROM memory_records WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()
        evidence_link = connection.execute(
            "SELECT evidence_id FROM memory_evidence WHERE memory_id=?",
            (memory["id"],),
        ).fetchone()
        job = connection.execute(
            "SELECT state, input_tokens, output_tokens, prompt_version "
            "FROM cognitive_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        operation_count = connection.execute(
            "SELECT COUNT(*) AS n FROM cognitive_job_operations WHERE job_id=?",
            (job_id,),
        ).fetchone()["n"]
        health = connection.execute(
            "SELECT status FROM health_reports WHERE job_id=? ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
    assert memory["canonical_statement"] == "The customer prefers email follow-ups."
    assert evidence_link["evidence_id"] == evidence_id
    assert dict(job) == {
        "state": "succeeded",
        "input_tokens": 14,
        "output_tokens": 6,
        "prompt_version": "atlas.cortex.triage.prompt.v1",
    }
    assert operation_count == 1
    assert health["status"] == "succeeded"


def test_active_session_cannot_directly_enqueue_or_run_unadmitted_semantics(
    tmp_path: Path,
) -> None:
    store, config, raw = _runtime(tmp_path)
    evidence_id, _observation_id = _observation(store, session_id="active-session")

    with pytest.raises(ValueError, match="durable logical-session admission"):
        store.enqueue_job(
            "session_distill",
            input_hash=stable_hash("forged-active-session"),
            input_data={
                "session_id": "active-session",
                "evidence_ids": [evidence_id],
            },
        )

    now = "2026-07-14T00:00:00Z"
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO cognitive_jobs("
            "id, brain_id, job_type, input_hash, state, scheduled_at, "
            "next_attempt_at, input_json, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "job_forged_active",
                store.brain_id,
                "session_distill",
                stable_hash("raw-forged-active-session"),
                "queued",
                now,
                now,
                json.dumps({
                    "session_id": "active-session",
                    "evidence_ids": [evidence_id],
                }),
                now,
                now,
            ),
        )
    llm = _FakeLLM()
    result = CortexDreamWorker(
        store, config, raw_config=raw, llm_call=llm, owner="worker-admission"
    ).run_once()

    assert result.status == "idle"
    assert llm.calls == []
    with store.connect() as connection:
        forged = connection.execute(
            "SELECT state, attempt, error FROM cognitive_jobs WHERE id=?",
            ("job_forged_active",),
        ).fetchone()
    assert dict(forged) == {
        "state": "failed",
        "attempt": 0,
        "error": "invalid durable session-distill admission",
    }


def test_processor_revalidates_admitted_snapshot_before_model_call(
    tmp_path: Path,
) -> None:
    store, config, raw = _runtime(tmp_path)
    evidence_id, _observation_id = _observation(store)
    _job(store, evidence_id)
    job = store.lease_job(
        job_types=("session_distill",),
        owner="tamper-worker",
        lease_seconds=60,
    )
    assert job is not None
    job["input"] = {**job["input"], "evidence_ids": ["evidence_forged"]}
    llm = _FakeLLM()
    processor = DreamProcessor(store, config, raw_config=raw, llm_call=llm)

    with pytest.raises(PermissionError, match="input was modified"):
        processor.process(job, owner="tamper-worker")

    assert llm.calls == []


def test_admitted_job_remains_runnable_after_session_reopens(tmp_path: Path) -> None:
    store, config, raw = _runtime(tmp_path)
    evidence_id, observation_id = _observation(
        store,
        session_id="reopened-session",
        content="The admitted callback preference is email.",
    )
    job_id = _job(store, evidence_id, session_id="reopened-session")
    store.append_evidence(
        "reopened-session",
        EvidenceInput(
            source_type="user_message",
            content="ACTIVE_EPOCH_MUST_NOT_ENTER_OLDER_ADMISSION",
            source_locator="reopened-session:user:new-epoch",
        ),
    )
    assert store.session_lineage("reopened-session")["state"] == "active"
    llm = _FakeLLM(
        _response({
            "schema_version": TRIAGE_SCHEMA_VERSION,
            "operations": [_operation(observation_id, evidence_id)],
        })
    )

    result = CortexDreamWorker(
        store, config, raw_config=raw, llm_call=llm, owner="reopen-worker"
    ).run_once()

    assert result.status == "succeeded"
    assert result.job_id == job_id
    assert len(llm.calls) == 1
    assert "ACTIVE_EPOCH_MUST_NOT_ENTER_OLDER_ADMISSION" not in json.dumps(llm.calls)


def test_triage_projects_bounded_typed_personal_relations(tmp_path: Path) -> None:
    store, config, raw = _runtime(tmp_path)
    evidence_id, observation_id = _observation(
        store,
        content="Jordan works at Northside Motors.",
    )
    job_id = _job(store, evidence_id, suffix="typed-personal-graph")
    operation = _operation(
        observation_id,
        evidence_id,
        statement="Jordan works at Northside Motors.",
    )
    operation["memory_kind"] = "relationship"
    operation["entities"] = [
        {"name": "Jordan", "type": "person", "aliases": []},
        {
            "name": "Northside Motors",
            "type": "dealership",
            "aliases": ["Northside"],
        },
    ]
    operation["relations"] = [
        {
            "subject_entity_index": 0,
            "predicate": "works_at",
            "object_entity_index": 1,
        }
    ]
    payload = {"schema_version": TRIAGE_SCHEMA_VERSION, "operations": [operation]}

    result = CortexDreamWorker(
        store,
        config,
        raw_config=raw,
        llm_call=_FakeLLM(_response(payload)),
        owner="worker-a",
    ).run_once()

    assert result.status == "succeeded"
    with store.connect() as connection:
        entities = connection.execute(
            "SELECT id, canonical_name FROM entities WHERE brain_id=? "
            "ORDER BY canonical_name",
            (store.brain_id,),
        ).fetchall()
        relation = connection.execute(
            "SELECT r.predicate, s.canonical_name AS subject_name, "
            "o.canonical_name AS object_name, r.derived_by "
            "FROM relations r JOIN entities s ON s.id=r.subject_entity_id "
            "JOIN entities o ON o.id=r.object_entity_id WHERE r.brain_id=?",
            (store.brain_id,),
        ).fetchone()
        entity_support_count = connection.execute(
            "SELECT COUNT(*) AS n FROM entity_evidence"
        ).fetchone()["n"]
        relation_support = connection.execute(
            "SELECT evidence_id FROM relation_evidence"
        ).fetchone()
        job = connection.execute(
            "SELECT output_json FROM cognitive_jobs WHERE id=?", (job_id,)
        ).fetchone()

    assert {row["canonical_name"] for row in entities} == {
        "Jordan",
        "Northside Motors",
    }
    assert dict(relation) == {
        "predicate": "works_at",
        "subject_name": "Jordan",
        "object_name": "Northside Motors",
        "derived_by": "cortex:personal-triage:v1",
    }
    assert entity_support_count == 2
    assert relation_support["evidence_id"] == evidence_id
    output = json.loads(job["output_json"])
    assert output["entities_created"] == 2
    assert output["relations_created"] == 1


def test_triage_uses_evidence_safe_co_mention_relation_fallback(
    tmp_path: Path,
) -> None:
    store, config, raw = _runtime(tmp_path)
    evidence_id, observation_id = _observation(
        store,
        content="Jordan and the Blue Atlas SUV were mentioned together.",
    )
    _job(store, evidence_id, suffix="co-mentioned-personal-graph")
    operation = _operation(
        observation_id,
        evidence_id,
        statement="Jordan and the Blue Atlas SUV were mentioned together.",
    )
    operation["entities"] = [
        {"name": "Jordan", "type": "person", "aliases": []},
        {"name": "Blue Atlas SUV", "type": "vehicle", "aliases": []},
    ]
    payload = {"schema_version": TRIAGE_SCHEMA_VERSION, "operations": [operation]}

    result = CortexDreamWorker(
        store,
        config,
        raw_config=raw,
        llm_call=_FakeLLM(_response(payload)),
        owner="worker-a",
    ).run_once()

    assert result.status == "succeeded"
    with store.connect() as connection:
        relation = connection.execute(
            "SELECT predicate FROM relations WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()
        job = connection.execute(
            "SELECT output_json FROM cognitive_jobs WHERE id=?", (result.job_id,)
        ).fetchone()
    assert relation["predicate"] == "co_mentioned_with"
    assert json.loads(job["output_json"])["relations_created"] == 1


@pytest.mark.parametrize(
    "relations,error",
    [
        (
            [
                {
                    "subject_entity_index": 0,
                    "predicate": "invented_unbounded_edge",
                    "object_entity_index": 1,
                }
            ],
            "predicate",
        ),
        (
            [
                {
                    "subject_entity_index": 0,
                    "predicate": "works_at",
                    "object_entity_index": 9,
                }
            ],
            "outside",
        ),
        (
            [
                {
                    "subject_entity_index": 0,
                    "predicate": "related_to",
                    "object_entity_index": 1,
                }
            ]
            * 25,
            "at most 24",
        ),
    ],
)
def test_triage_rejects_unbounded_or_ungrounded_relation_shape(
    relations: list[dict[str, Any]], error: str
) -> None:
    evidence_id = "evidence-1"
    observation_id = "observation-1"
    operation = _operation(observation_id, evidence_id)
    operation["entities"] = [
        {"name": "Jordan", "type": "person", "aliases": []},
        {"name": "Northside Motors", "type": "dealership", "aliases": []},
    ]
    operation["relations"] = relations
    candidate = DreamCandidate(
        observation_id=observation_id,
        session_id="session-1",
        knowledge_space="personal",
        kind="relationship",
        text="Jordan works at Northside Motors.",
        evidence=(
            {
                "id": evidence_id,
                "source_type": "user_message",
                "content": "Jordan works at Northside Motors.",
                "occurred_at": "2026-07-14T00:00:00Z",
                "sensitivity": "private",
            },
        ),
        authoritative_evidence_ids=frozenset({evidence_id}),
        allowed_evidence_ids=frozenset({evidence_id}),
        assistant_evidence_ids=frozenset(),
        related_memories=(),
        valid_from=None,
        valid_until=None,
    )

    with pytest.raises(CortexOutputError, match=error):
        validate_triage_output(
            json.dumps({
                "schema_version": TRIAGE_SCHEMA_VERSION,
                "operations": [operation],
            }),
            [candidate],
        )


def test_triage_accepts_bare_operations_array_as_wrapped_document() -> None:
    """Cheap triage models emit the operations array without the wrapper;
    validation must treat it identically to the canonical envelope."""
    evidence_id = "evidence-1"
    observation_id = "observation-1"
    operation = _operation(observation_id, evidence_id)
    candidate = DreamCandidate(
        observation_id=observation_id,
        session_id="session-1",
        knowledge_space="personal",
        kind="preference",
        text="The customer prefers email follow-ups.",
        evidence=(
            {
                "id": evidence_id,
                "source_type": "user_message",
                "content": "The customer prefers email follow-ups.",
                "occurred_at": "2026-07-14T00:00:00Z",
                "sensitivity": "private",
            },
        ),
        authoritative_evidence_ids=frozenset({evidence_id}),
        allowed_evidence_ids=frozenset({evidence_id}),
        assistant_evidence_ids=frozenset(),
        related_memories=(),
        valid_from=None,
        valid_until=None,
    )

    bare = validate_triage_output(json.dumps([operation]), [candidate])
    unversioned = validate_triage_output(
        json.dumps({"operations": [operation]}), [candidate]
    )
    wrapped = validate_triage_output(
        json.dumps({
            "schema_version": TRIAGE_SCHEMA_VERSION,
            "operations": [operation],
        }),
        [candidate],
    )
    assert bare == unversioned == wrapped

    # Other top-level shapes remain rejected.
    for payload in ({"ops": [operation]}, "operations", 7):
        with pytest.raises(CortexOutputError, match="top-level shape"):
            validate_triage_output(json.dumps(payload), [candidate])


def _candidate(observation_id: str, evidence_id: str) -> DreamCandidate:
    return DreamCandidate(
        observation_id=observation_id,
        session_id="session-1",
        knowledge_space="personal",
        kind="preference",
        text="The customer prefers email follow-ups.",
        evidence=(
            {
                "id": evidence_id,
                "source_type": "user_message",
                "content": "The customer prefers email follow-ups.",
                "occurred_at": "2026-07-14T00:00:00Z",
                "sensitivity": "private",
            },
        ),
        authoritative_evidence_ids=frozenset({evidence_id}),
        allowed_evidence_ids=frozenset({evidence_id}),
        assistant_evidence_ids=frozenset(),
        related_memories=(),
        valid_from=None,
        valid_until=None,
    )


def test_truncated_batch_output_splits_batch_instead_of_failing(
    tmp_path: Path,
) -> None:
    """When a batch response truncates (output token budget) and the one
    repair also fails, the processor must split the batch and recover
    instead of burning the job attempt."""
    store, config, raw = _runtime(tmp_path)
    candidates = [
        _candidate("observation-1", "evidence-1"),
        _candidate("observation-2", "evidence-2"),
    ]
    full = [
        _operation("observation-1", "evidence-1"),
        _operation("observation-2", "evidence-2"),
    ]
    truncated = json.dumps({
        "schema_version": TRIAGE_SCHEMA_VERSION,
        "operations": full,
    })[:120]
    llm = _FakeLLM(
        _response(truncated),  # initial batch call: cut mid-document
        _response(truncated),  # repair call: same truncation
        _response({
            "schema_version": TRIAGE_SCHEMA_VERSION,
            "operations": [full[0]],
        }),  # left half
        _response({
            "schema_version": TRIAGE_SCHEMA_VERSION,
            "operations": [full[1]],
        }),  # right half
    )
    processor = DreamProcessor(store, config, raw_config=raw, llm_call=llm)
    route = CortexModelRoute(
        task="cortex_triage", provider="openrouter", model="cheap-memory-model"
    )

    operations, _ = processor._call_and_validate(
        route,
        candidates,
        UsageTotals(),
        allowed_actions=ACTIONS,
        review=False,
    )

    assert [op.observation_id for op in operations] == [
        "observation-1",
        "observation-2",
    ]
    assert len(llm.calls) == 4

    # A single candidate that still fails after repair must raise.
    llm_single = _FakeLLM(_response(truncated), _response(truncated))
    processor_single = DreamProcessor(
        store, config, raw_config=raw, llm_call=llm_single
    )
    with pytest.raises(CortexOutputError):
        processor_single._call_and_validate(
            route,
            candidates[:1],
            UsageTotals(),
            allowed_actions=ACTIONS,
            review=False,
        )


def test_defer_operation_records_decision_without_consuming_observation(
    tmp_path: Path,
) -> None:
    store, config, raw = _runtime(tmp_path)
    evidence_id, observation_id = _observation(store)
    _job(store, evidence_id)
    operation = _operation(
        observation_id,
        evidence_id,
        action="defer_unresolved",
        statement="",
        rationale="Development route made no semantic decision.",
    )
    operation["sensitivity"] = "restricted"
    operation["retention"] = "short"
    payload = {
        "schema_version": TRIAGE_SCHEMA_VERSION,
        "operations": [operation],
    }

    result = CortexDreamWorker(
        store,
        config,
        raw_config=raw,
        llm_call=_FakeLLM(_response(payload)),
        owner="worker-a",
    ).run_once()

    assert result.status == "succeeded"
    with store.connect() as connection:
        observation = connection.execute(
            "SELECT processing_state, utility_action, triage_attempts, "
            "next_triage_at, processed_at "
            "FROM observations WHERE id=?",
            (observation_id,),
        ).fetchone()
        memory_count = connection.execute(
            "SELECT COUNT(*) AS n FROM memory_records WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()["n"]
        receipt = connection.execute(
            "SELECT operation_type FROM cognitive_job_operations"
        ).fetchone()
        job = connection.execute(
            "SELECT output_json FROM cognitive_jobs WHERE id=?",
            (result.job_id,),
        ).fetchone()
    assert observation["processing_state"] == "pending"
    assert observation["utility_action"] == "defer_unresolved"
    assert observation["triage_attempts"] == 1
    assert observation["next_triage_at"] is not None
    assert observation["processed_at"] is not None
    assert memory_count == 0
    assert receipt["operation_type"] == "defer_unresolved"
    assert json.loads(job["output_json"])["needs_review"] == 1


def test_sensitive_evidence_is_classified_before_redaction_and_cannot_be_downgraded(
    tmp_path: Path,
) -> None:
    store, _, _ = _runtime(tmp_path)
    store.ensure_session("sensitive-session")
    evidence_id = store.append_evidence(
        "sensitive-session",
        EvidenceInput(
            source_type="user_message",
            content="My social security number is 123-45-6789.",
            source_locator="sensitive:ssn",
        ),
    )
    with store.connect() as connection:
        evidence = connection.execute(
            "SELECT content, sensitivity FROM evidence_items WHERE id=?",
            (evidence_id,),
        ).fetchone()
    assert evidence["sensitivity"] == "restricted"

    observation_id = "sensitive-observation"
    candidate = DreamCandidate(
        observation_id=observation_id,
        session_id="sensitive-session",
        knowledge_space="personal",
        kind="stable_fact",
        text="The customer supplied restricted identity information.",
        evidence=(
            {
                "id": evidence_id,
                "source_type": "user_message",
                "content": evidence["content"],
                "occurred_at": "2026-07-14T00:00:00Z",
                "sensitivity": evidence["sensitivity"],
            },
        ),
        authoritative_evidence_ids=frozenset({evidence_id}),
        allowed_evidence_ids=frozenset({evidence_id}),
        assistant_evidence_ids=frozenset(),
        related_memories=(),
        valid_from=None,
        valid_until=None,
    )
    operation = _operation(observation_id, evidence_id)
    operation["sensitivity"] = "sensitive"

    with pytest.raises(CortexOutputError, match="cannot downgrade"):
        validate_triage_output(
            json.dumps({
                "schema_version": TRIAGE_SCHEMA_VERSION,
                "operations": [operation],
            }),
            [candidate],
        )


def test_managed_dream_clamps_oversized_config_to_hundred_and_continues(
    tmp_path: Path,
) -> None:
    raw = _raw_config(tmp_path, provider="altas", model="frontier-chat-model")
    raw["cortex"]["dream"]["max_batch"] = 1_000
    config = CortexConfig.from_mapping(raw, tmp_path)
    assert config.dream_max_batch == 100
    store = CortexStore(config.database_path, owner_customer_id="customer-a")
    store.initialize()
    store.ensure_session("managed-session")
    for index in range(101):
        content = f"Customer evidence {index}: " + ("x" * 8_000)
        evidence_id = store.append_evidence(
            "managed-session",
            EvidenceInput(
                source_type="user_message",
                content=content,
                source_locator=f"managed-session:user:{index}",
            ),
        )
        store.add_observation(
            session_id="managed-session",
            kind="event",
            text=content,
            evidence_ids=[evidence_id],
        )
    store.finalize_session("managed-session")
    environ = {
        "ATLAS_MANAGED_MODE": "1",
        "ATLAS_DEVICE_TOKEN": "device",
        "ATLAS_LEASE_TOKEN": "lease",
        "ATLAS_TENANT_ID": "tenant",
        "ATLAS_STORE_ID": "store",
        "ATLAS_AGENT_ID": "agent",
        "ATLAS_JOB_ID": "job",
        "ATLAS_CLAIM_TOKEN": "claim-token-that-is-long-enough-for-the-gateway",
        "ATLAS_JOB_CAPABILITY": "cortex.memory_maintenance",
        "ATLAS_CONTROL_PLANE_URL": "https://control.example.test",
    }

    class DeferringLLM:
        def __init__(self) -> None:
            self.batch_sizes: list[int] = []
            self.prompt_sizes: list[int] = []

        def __call__(self, **kwargs: Any) -> Any:
            raw_prompt = kwargs["messages"][-1]["content"]
            self.prompt_sizes.append(len(raw_prompt.encode("utf-8")))
            prompt = json.loads(raw_prompt)
            candidates = prompt["candidates"]
            self.batch_sizes.append(len(candidates))
            operations = []
            for candidate in candidates:
                operation = _operation(
                    candidate["observation_id"],
                    candidate["authoritative_evidence_ids"][0],
                    action="defer_unresolved",
                    statement="",
                    rationale="No semantic decision in the bounded test route.",
                )
                operations.append(operation)
            return _response({
                "schema_version": TRIAGE_SCHEMA_VERSION,
                "operations": operations,
            })

    llm = DeferringLLM()
    result = CortexDreamWorker(
        store,
        config,
        raw_config=raw,
        environ=environ,
        llm_call=llm,
        owner="worker-a",
    ).run_once()

    assert result.status == "succeeded"
    assert llm.batch_sizes == [20, 20, 20, 20, 20]
    assert max(llm.prompt_sizes) <= 60 * 1024
    with store.connect() as connection:
        pending = connection.execute(
            "SELECT COUNT(*) AS n FROM observations "
            "WHERE brain_id=? AND processing_state='pending'",
            (store.brain_id,),
        ).fetchone()["n"]
        job = connection.execute(
            "SELECT output_json FROM cognitive_jobs WHERE id=?",
            (result.job_id,),
        ).fetchone()
        continuation_count = connection.execute(
            "SELECT COUNT(*) AS n FROM cognitive_jobs WHERE brain_id=? "
            "AND job_type='session_distill' AND state='queued'",
            (store.brain_id,),
        ).fetchone()["n"]
    assert pending == 101
    assert continuation_count == 1
    assert json.loads(job["output_json"])["observations_processed"] == 100


def test_session_distill_spans_full_compression_lineage_and_excludes_unrelated(
    tmp_path: Path,
) -> None:
    store, config, raw = _runtime(tmp_path)
    logical_id = "logical-conversation-1"
    store.ensure_session("root-session", logical_conversation_id=logical_id)
    store.ensure_session(
        "compression-1",
        parent_session_id="root-session",
        logical_conversation_id=logical_id,
    )
    store.ensure_session(
        "compression-2",
        parent_session_id="compression-1",
        logical_conversation_id=logical_id,
    )
    lineage_rows = [
        _observation(
            store,
            session_id=session_id,
            content=f"Lineage evidence from {session_id}.",
        )
        for session_id in ("root-session", "compression-1", "compression-2")
    ]
    unrelated_evidence, unrelated_observation = _observation(
        store,
        session_id="unrelated-session",
        content="UNRELATED_CONVERSATION_MUST_NOT_REACH_THE_MODEL",
    )

    evidence_hash = store.finalize_session("compression-2")
    assert store.finalize_session("compression-2") == evidence_hash
    with store.connect() as connection:
        job_rows = connection.execute(
            "SELECT input_json FROM cognitive_jobs WHERE brain_id=? "
            "AND job_type='session_distill'",
            (store.brain_id,),
        ).fetchall()
        session_states = {
            str(row["id"]): str(row["state"])
            for row in connection.execute(
                "SELECT id, state FROM sessions WHERE brain_id=?",
                (store.brain_id,),
            ).fetchall()
        }
    assert len(job_rows) == 1
    payload = json.loads(job_rows[0]["input_json"])
    assert payload["logical_conversation_id"] == logical_id
    assert set(payload["session_ids"]) == {
        "root-session",
        "compression-1",
        "compression-2",
    }
    assert payload["session_id"] == "compression-2"
    assert payload["distill_version"] == 2
    assert session_states == {
        "root-session": "finalized",
        "compression-1": "finalized",
        "compression-2": "finalized",
        "unrelated-session": "active",
    }

    class DeferringLineageLLM:
        def __init__(self) -> None:
            self.prompts: list[str] = []

        def __call__(self, **kwargs: Any) -> Any:
            raw_prompt = kwargs["messages"][-1]["content"]
            self.prompts.append(raw_prompt)
            candidates = json.loads(raw_prompt)["candidates"]
            return _response({
                "schema_version": TRIAGE_SCHEMA_VERSION,
                "operations": [
                    _operation(
                        candidate["observation_id"],
                        candidate["authoritative_evidence_ids"][0],
                        action="defer_unresolved",
                        statement="",
                        rationale="The test route defers semantic promotion.",
                    )
                    for candidate in candidates
                ],
            })

    llm = DeferringLineageLLM()
    result = CortexDreamWorker(
        store,
        config,
        raw_config=raw,
        llm_call=llm,
        owner="lineage-worker",
    ).run_once()

    assert result.status == "succeeded"
    assert len(llm.prompts) == 1
    assert "UNRELATED_CONVERSATION_MUST_NOT_REACH_THE_MODEL" not in llm.prompts[0]
    with store.connect() as connection:
        processed = {
            str(row["id"]): str(row["utility_action"] or "")
            for row in connection.execute(
                "SELECT id, utility_action FROM observations WHERE id IN (?,?,?)",
                tuple(observation_id for _evidence_id, observation_id in lineage_rows),
            ).fetchall()
        }
        unrelated_state = connection.execute(
            "SELECT processing_state, utility_action FROM observations WHERE id=?",
            (unrelated_observation,),
        ).fetchone()
    assert set(processed.values()) == {"defer_unresolved"}
    assert dict(unrelated_state) == {
        "processing_state": "pending",
        "utility_action": None,
    }
    assert unrelated_evidence


def test_session_finalize_does_not_resurrect_a_deleted_physical_segment(
    tmp_path: Path,
) -> None:
    store, _config, _raw = _runtime(tmp_path)
    logical_id = "logical-with-deleted-segment"
    store.ensure_session("deleted-root", logical_conversation_id=logical_id)
    store.ensure_session(
        "active-tip",
        parent_session_id="deleted-root",
        logical_conversation_id=logical_id,
    )
    _observation(store, session_id="deleted-root", content="Deleted evidence")
    _observation(store, session_id="active-tip", content="Active evidence")
    with store.transaction() as connection:
        connection.execute(
            "UPDATE sessions SET state='deleted' WHERE id=? AND brain_id=?",
            ("deleted-root", store.brain_id),
        )

    store.finalize_session("active-tip")

    with store.connect() as connection:
        states = {
            str(row["id"]): str(row["state"])
            for row in connection.execute(
                "SELECT id, state FROM sessions WHERE brain_id=? ORDER BY id",
                (store.brain_id,),
            ).fetchall()
        }
        payload = json.loads(
            connection.execute(
                "SELECT input_json FROM cognitive_jobs WHERE brain_id=? "
                "AND job_type='session_distill'",
                (store.brain_id,),
            ).fetchone()["input_json"]
        )
    assert states == {"active-tip": "finalized", "deleted-root": "deleted"}
    assert payload["session_ids"] == ["active-tip"]


def test_session_distill_continues_until_more_than_max_batch_is_processed(
    tmp_path: Path,
) -> None:
    raw = _raw_config(tmp_path)
    raw["cortex"]["dream"]["max_batch"] = 2
    config = CortexConfig.from_mapping(raw, tmp_path)
    store = CortexStore(config.database_path, owner_customer_id="customer-a")
    store.initialize()
    observation_ids: list[str] = []
    for index in range(5):
        _evidence_id, observation_id = _observation(
            store,
            session_id="bounded-session",
            content=f"Durable preference number {index}.",
        )
        observation_ids.append(observation_id)
    store.finalize_session("bounded-session")

    class PromotingLLM:
        def __init__(self) -> None:
            self.batch_sizes: list[int] = []

        def __call__(self, **kwargs: Any) -> Any:
            candidates = json.loads(kwargs["messages"][-1]["content"])["candidates"]
            self.batch_sizes.append(len(candidates))
            return _response({
                "schema_version": TRIAGE_SCHEMA_VERSION,
                "operations": [
                    _operation(
                        candidate["observation_id"],
                        candidate["authoritative_evidence_ids"][0],
                        statement=f"Promoted {candidate['observation_id']}.",
                    )
                    for candidate in candidates
                ],
            })

    llm = PromotingLLM()
    worker = CortexDreamWorker(
        store,
        config,
        raw_config=raw,
        llm_call=llm,
        owner="continuation-worker",
    )

    assert [worker.run_once().status for _ in range(3)] == [
        "succeeded",
        "succeeded",
        "succeeded",
    ]
    assert llm.batch_sizes == [2, 2, 1]
    with store.connect() as connection:
        observation_states = {
            str(row["id"]): str(row["processing_state"])
            for row in connection.execute(
                "SELECT id, processing_state FROM observations WHERE brain_id=?",
                (store.brain_id,),
            ).fetchall()
        }
        jobs = connection.execute(
            "SELECT id, state, admission_id, parent_job_id, root_job_id, input_json "
            "FROM cognitive_jobs WHERE brain_id=? "
            "AND job_type='session_distill' ORDER BY created_at, id",
            (store.brain_id,),
        ).fetchall()
    assert observation_states == {
        observation_id: "promoted" for observation_id in observation_ids
    }
    assert len(jobs) == 3
    assert {str(row["state"]) for row in jobs} == {"succeeded"}
    assert (
        sum(1 for row in jobs if json.loads(row["input_json"]).get("continuation_of"))
        == 2
    )
    roots = [row for row in jobs if row["parent_job_id"] is None]
    assert len(roots) == 1
    root = roots[0]
    assert root["root_job_id"] == root["id"]
    assert all(row["admission_id"] == root["admission_id"] for row in jobs)
    by_id = {str(row["id"]): row for row in jobs}
    for row in jobs:
        if row["parent_job_id"] is None:
            continue
        assert row["root_job_id"] == root["id"]
        assert str(row["parent_job_id"]) in by_id
        assert json.loads(row["input_json"])["continuation_of"] == row["parent_job_id"]


def test_continuation_waits_for_immediate_parent_success_before_lease(
    tmp_path: Path,
) -> None:
    store, config, _raw = _runtime(tmp_path)
    evidence_id, _observation_id = _observation(
        store,
        session_id="parent-race-session",
        content="A bounded continuation race.",
    )
    root_id = _job(store, evidence_id, session_id="parent-race-session")
    root = store.lease_job(
        job_types=("session_distill",),
        owner="parent-worker",
        lease_seconds=60,
    )
    assert root is not None and root["id"] == root_id
    child_id = store.enqueue_session_distill_continuation(
        root_id,
        remaining_signature=["observation:0:due"],
        owner="parent-worker",
    )

    # Recovery can overlap a worker between continuation enqueue and parent
    # completion. A running parent is live lineage, not an orphan signal.
    assert CortexDreamSchedule(store, config).enqueue_recovery_jobs() == ()
    with store.connect() as connection:
        live_parent = connection.execute(
            "SELECT state, lease_owner, lease_expires_at FROM cognitive_jobs WHERE id=?",
            (root_id,),
        ).fetchone()
    assert live_parent["state"] == "running"
    assert live_parent["lease_owner"] == "parent-worker"
    assert live_parent["lease_expires_at"] is not None
    assert (
        store.lease_job(
            job_types=("session_distill",),
            owner="racing-worker",
            lease_seconds=60,
        )
        is None
    )
    with store.connect() as connection:
        blocked = connection.execute(
            "SELECT state, attempt FROM cognitive_jobs WHERE id=?", (child_id,)
        ).fetchone()
    assert dict(blocked) == {"state": "queued", "attempt": 0}

    store.complete_job(root_id, owner="parent-worker", output={})
    child = store.lease_job(
        job_types=("session_distill",),
        owner="child-worker",
        lease_seconds=60,
    )
    assert child is not None and child["id"] == child_id


def test_deferred_observation_does_not_starve_new_work_and_can_retry_later(
    tmp_path: Path,
) -> None:
    store, config, raw = _runtime(tmp_path)
    old_evidence, old_observation = _observation(
        store,
        session_id="fair-session",
        content="An uncertain preference that needs another look.",
    )
    _job(store, old_evidence, session_id="fair-session", suffix="defer-old")
    deferred = _operation(
        old_observation,
        old_evidence,
        action="defer_unresolved",
        statement="",
        rationale="The evidence is not yet clear enough.",
    )
    first_llm = _FakeLLM(
        _response({"schema_version": TRIAGE_SCHEMA_VERSION, "operations": [deferred]})
    )
    first = CortexDreamWorker(
        store,
        config,
        raw_config=raw,
        llm_call=first_llm,
        owner="fairness-worker-a",
    ).run_once()
    assert first.status == "succeeded"

    new_evidence, new_observation = _observation(
        store,
        session_id="fair-session",
        content="A new clear preference should be processed immediately.",
    )
    assert [row["id"] for row in store.pending_observations(limit=10)] == [
        new_observation
    ]
    _job(store, new_evidence, session_id="fair-session", suffix="promote-new")
    promote_new = _operation(
        new_observation,
        new_evidence,
        statement="The customer has a clear new preference.",
    )
    second_llm = _FakeLLM(
        _response({
            "schema_version": TRIAGE_SCHEMA_VERSION,
            "operations": [promote_new],
        })
    )
    second = CortexDreamWorker(
        store,
        config,
        raw_config=raw,
        llm_call=second_llm,
        owner="fairness-worker-b",
    ).run_once()
    assert second.status == "succeeded"

    with store.transaction() as connection:
        connection.execute(
            "UPDATE observations SET next_triage_at='2000-01-01T00:00:00Z' WHERE id=?",
            (old_observation,),
        )
    assert [row["id"] for row in store.pending_observations(limit=10)] == [
        old_observation
    ]
    _job(store, old_evidence, session_id="fair-session", suffix="retry-old")
    promote_old = _operation(
        old_observation,
        old_evidence,
        statement="The customer confirmed the earlier preference.",
    )
    third_llm = _FakeLLM(
        _response({
            "schema_version": TRIAGE_SCHEMA_VERSION,
            "operations": [promote_old],
        })
    )
    third = CortexDreamWorker(
        store,
        config,
        raw_config=raw,
        llm_call=third_llm,
        owner="fairness-worker-c",
    ).run_once()

    assert third.status == "succeeded"
    with store.connect() as connection:
        rows = connection.execute(
            "SELECT id, processing_state, triage_attempts, next_triage_at "
            "FROM observations WHERE id IN (?,?)",
            (old_observation, new_observation),
        ).fetchall()
    states = {str(row["id"]): dict(row) for row in rows}
    assert states[old_observation]["processing_state"] == "promoted"
    assert states[old_observation]["triage_attempts"] == 1
    assert states[old_observation]["next_triage_at"] is None
    assert states[new_observation]["processing_state"] == "promoted"


def test_assistant_only_observation_is_never_sent_or_promoted(tmp_path: Path) -> None:
    store, config, raw = _runtime(tmp_path)
    evidence_id, observation_id = _observation(
        store,
        source_type="assistant_message",
        content="The customer probably prefers phone calls.",
    )
    _job(store, evidence_id)
    llm = _FakeLLM()

    result = CortexDreamWorker(
        store, config, raw_config=raw, llm_call=llm, owner="worker-a"
    ).run_once()

    assert result.status == "succeeded"
    assert llm.calls == []
    with store.connect() as connection:
        memory_count = connection.execute(
            "SELECT COUNT(*) AS n FROM memory_records WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()["n"]
        observation = connection.execute(
            "SELECT processing_state, utility_action FROM observations WHERE id=?",
            (observation_id,),
        ).fetchone()
    assert memory_count == 0
    assert dict(observation) == {
        "processing_state": "discarded",
        "utility_action": "discard_transient",
    }


def test_mixed_observation_never_exposes_assistant_text_to_triage(
    tmp_path: Path,
) -> None:
    store, config, raw = _runtime(tmp_path)
    store.ensure_session("session-1")
    user_evidence = store.append_evidence(
        "session-1",
        EvidenceInput(
            source_type="user_message",
            content="I prefer email follow-ups.",
            source_locator="session-1:user:mixed",
        ),
    )
    assistant_evidence = store.append_evidence(
        "session-1",
        EvidenceInput(
            source_type="assistant_message",
            content="ASSISTANT_SPECULATION_MUST_NOT_REACH_THE_MODEL",
            source_locator="session-1:assistant:mixed",
        ),
    )
    observation_id = store.add_observation(
        session_id="session-1",
        kind="preference",
        text="ASSISTANT_SPECULATION_MUST_NOT_REACH_THE_MODEL",
        evidence_ids=[user_evidence, assistant_evidence],
    )
    _job(store, user_evidence, suffix="mixed")
    valid = {
        "schema_version": TRIAGE_SCHEMA_VERSION,
        "operations": [_operation(observation_id, user_evidence)],
    }
    llm = _FakeLLM(_response(valid))

    result = CortexDreamWorker(
        store, config, raw_config=raw, llm_call=llm, owner="worker-a"
    ).run_once()

    assert result.status == "succeeded"
    assert "ASSISTANT_SPECULATION_MUST_NOT_REACH_THE_MODEL" not in json.dumps(llm.calls)


def test_invalid_evidence_gets_one_repair_then_dead_letters_without_writes(
    tmp_path: Path,
) -> None:
    store, config, raw = _runtime(tmp_path)
    evidence_id, observation_id = _observation(store)
    job_id = _job(store, evidence_id, max_attempts=1)
    invalid = {
        "schema_version": TRIAGE_SCHEMA_VERSION,
        "operations": [_operation(observation_id, "evidence_foreign")],
    }
    llm = _FakeLLM(_response(invalid), _response(invalid))

    result = CortexDreamWorker(
        store, config, raw_config=raw, llm_call=llm, owner="worker-a"
    ).run_once()

    assert result.status == "failed"
    assert result.error_type == "CortexOutputError"
    assert len(llm.calls) == 2
    with store.connect() as connection:
        job = connection.execute(
            "SELECT state, attempt, dead_letter_at FROM cognitive_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        memory_count = connection.execute(
            "SELECT COUNT(*) AS n FROM memory_records WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()["n"]
    assert job["state"] == "dead_letter"
    assert job["attempt"] == 1
    assert job["dead_letter_at"] is not None
    assert memory_count == 0


def test_deeper_review_uses_reasoning_slot_before_applying(tmp_path: Path) -> None:
    store, config, raw = _runtime(tmp_path)
    evidence_id, observation_id = _observation(store)
    _job(store, evidence_id)
    escalate = _operation(
        observation_id,
        evidence_id,
        action="needs_deeper_review",
        statement="",
        rationale="The statement may represent a temporal change.",
    )
    promote = _operation(observation_id, evidence_id)
    llm = _FakeLLM(
        _response({"schema_version": TRIAGE_SCHEMA_VERSION, "operations": [escalate]}),
        _response({"schema_version": TRIAGE_SCHEMA_VERSION, "operations": [promote]}),
    )

    result = CortexDreamWorker(
        store, config, raw_config=raw, llm_call=llm, owner="worker-a"
    ).run_once()

    assert result.status == "succeeded"
    assert [call["task"] for call in llm.calls] == [
        "cortex_triage",
        "cortex_reasoning",
    ]
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) AS n FROM memory_records WHERE brain_id=?",
                (store.brain_id,),
            ).fetchone()["n"]
            == 1
        )


def test_daily_recovery_marker_never_performs_semantic_processing(
    tmp_path: Path,
) -> None:
    store, config, raw = _runtime(tmp_path)
    evidence_id, observation_id = _observation(store)
    _job(store, evidence_id, suffix="first")
    valid = {
        "schema_version": TRIAGE_SCHEMA_VERSION,
        "operations": [_operation(observation_id, evidence_id)],
    }
    first_llm = _FakeLLM(_response(valid))
    worker = CortexDreamWorker(
        store, config, raw_config=raw, llm_call=first_llm, owner="worker-a"
    )
    assert worker.run_once().status == "succeeded"

    second_evidence_id, second_observation_id = _observation(
        store,
        session_id="session-2",
        content="The customer now prefers text follow-ups.",
    )
    store.enqueue_job(
        "dream_cycle",
        input_hash=stable_hash("dream-test", "second"),
        input_data={"scheduled_slot": "2026-07-14T04:00:00Z"},
    )
    second_llm = _FakeLLM()
    worker = CortexDreamWorker(
        store, config, raw_config=raw, llm_call=second_llm, owner="worker-b"
    )
    assert worker.run_once().status == "succeeded"
    assert second_llm.calls == []
    with store.connect() as connection:
        memory_count = connection.execute(
            "SELECT COUNT(*) AS n FROM memory_records WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()["n"]
        observation = connection.execute(
            "SELECT processing_state FROM observations WHERE id=?",
            (second_observation_id,),
        ).fetchone()
    assert memory_count == 1
    assert observation["processing_state"] == "pending"
    assert second_evidence_id


def test_dream_cycle_builds_stable_connected_component_communities(
    tmp_path: Path,
) -> None:
    store, config, raw = _runtime(tmp_path)
    store.ensure_session("session-community")
    evidence_id = store.append_evidence(
        "session-community",
        EvidenceInput(
            source_type="manual",
            content="Jordan owns the Atlas while Casey is a separate contact.",
            source_locator="test:community:source",
        ),
    )
    jordan_id, _ = store.upsert_entity(
        entity_type="person",
        canonical_name="Jordan",
        evidence_id=evidence_id,
    )
    atlas_id, _ = store.upsert_entity(
        entity_type="vehicle",
        canonical_name="Atlas",
        evidence_id=evidence_id,
    )
    casey_id, _ = store.upsert_entity(
        entity_type="person",
        canonical_name="Casey",
        evidence_id=evidence_id,
    )
    store.upsert_relation(
        subject_entity_id=jordan_id,
        predicate="owns",
        object_entity_id=atlas_id,
        evidence_ids=[evidence_id],
    )
    store.enqueue_job(
        "dream_cycle",
        input_hash=stable_hash("community-dream", "first"),
        input_data={"scheduled_slot": "2026-07-20T04:00:00Z"},
    )
    llm = _FakeLLM()
    worker = CortexDreamWorker(
        store, config, raw_config=raw, llm_call=llm, owner="worker-community"
    )

    assert worker.run_once().status == "succeeded"
    assert llm.calls == []
    with store.connect() as connection:
        first_rows = connection.execute(
            "SELECT id, member_ids_json FROM communities WHERE brain_id=? "
            "AND algorithm_version=? ORDER BY id",
            (store.brain_id, COMMUNITY_ALGORITHM_VERSION),
        ).fetchall()
    first_memberships = {
        frozenset(json.loads(row["member_ids_json"])) for row in first_rows
    }
    assert first_memberships == {
        frozenset({jordan_id, atlas_id}),
        frozenset({casey_id}),
    }
    first_ids = {str(row["id"]) for row in first_rows}
    overview = build_graph_overview(store, limit=100)
    assert first_ids.issubset({
        str(node["id"])
        for node in overview["nodes"]
        if node["type"] == "community"
    })

    store.enqueue_job(
        "dream_cycle",
        input_hash=stable_hash("community-dream", "second"),
        input_data={"scheduled_slot": "2026-07-21T04:00:00Z"},
    )
    assert worker.run_once().status == "succeeded"
    with store.connect() as connection:
        second_rows = connection.execute(
            "SELECT id, member_ids_json FROM communities WHERE brain_id=? "
            "AND algorithm_version=? ORDER BY id",
            (store.brain_id, COMMUNITY_ALGORITHM_VERSION),
        ).fetchall()

    assert {str(row["id"]) for row in second_rows} == first_ids
    assert len(second_rows) == len(first_rows)


def test_processor_rejects_unknown_semantic_job_type_before_model_call(
    tmp_path: Path,
) -> None:
    store, config, raw = _runtime(tmp_path)
    llm = _FakeLLM()
    processor = DreamProcessor(store, config, raw_config=raw, llm_call=llm)

    with pytest.raises(CortexDreamError, match="unsupported Cortex semantic job type"):
        processor.process(
            {"id": "job-unknown", "job_type": "unexpected", "input": {}},
            owner="worker-a",
        )

    assert llm.calls == []


def test_session_reconcile_job_replays_without_calling_a_model(tmp_path: Path) -> None:
    store, config, raw = _runtime(tmp_path)
    store.ensure_session("session-1")
    evidence_id = store.append_evidence(
        "session-1",
        EvidenceInput(
            source_type="user_message",
            content="The callback window is Tuesday afternoon.",
            source_locator="session-1:row:41:user",
            metadata={"source_row_id": 41},
        ),
    )
    observation_id = store.add_observation(
        session_id="session-1",
        kind="preference",
        text="The callback window is Tuesday afternoon.",
        evidence_ids=[evidence_id],
    )
    memory_id, _created = store.promote_memory(
        kind="preference",
        statement="The callback window is Tuesday afternoon.",
        evidence_ids=[evidence_id],
    )
    job_id = store.enqueue_job(
        "session_reconcile",
        input_hash=stable_hash("session-1", "rewound", 41),
        input_data={"session_id": "session-1", "source_row_ids": [41]},
    )
    llm = _FakeLLM()

    result = CortexDreamWorker(
        store, config, raw_config=raw, llm_call=llm, owner="worker-a"
    ).run_once()

    assert result.status == "succeeded"
    assert result.job_id == job_id
    assert llm.calls == []
    with store.connect() as connection:
        evidence = connection.execute(
            "SELECT tombstoned_at FROM evidence_items WHERE id=?", (evidence_id,)
        ).fetchone()
        observation = connection.execute(
            "SELECT processing_state FROM observations WHERE id=?", (observation_id,)
        ).fetchone()
        memory = connection.execute(
            "SELECT status FROM memory_records WHERE id=?", (memory_id,)
        ).fetchone()
        job = connection.execute(
            "SELECT state, model, prompt_version, input_tokens, output_tokens "
            "FROM cognitive_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
    assert evidence["tombstoned_at"] is not None
    assert observation["processing_state"] == "rewound"
    assert memory["status"] == "rewound"
    assert dict(job) == {
        "state": "succeeded",
        "model": None,
        "prompt_version": "atlas.cortex.reconcile.v1",
        "input_tokens": 0,
        "output_tokens": 0,
    }


def test_rewind_tombstones_unsupported_entities_but_preserves_shared_graph(
    tmp_path: Path,
) -> None:
    store, _config, _raw = _runtime(tmp_path)
    store.ensure_session("session-a")
    first_evidence = store.append_evidence(
        "session-a",
        EvidenceInput(
            source_type="user_message",
            content="Jordan (First-only) owns the Blue Atlas SUV. Taylor is visiting.",
            source_locator="session-a:row:41:user",
            metadata={"source_row_id": 41},
        ),
    )
    store.ensure_session("session-b")
    second_evidence = store.append_evidence(
        "session-b",
        EvidenceInput(
            source_type="user_message",
            content="Jordan (Second-only) owns the Blue Atlas SUV.",
            source_locator="session-b:row:52:user",
            metadata={"source_row_id": 52},
        ),
    )
    jordan_id, _ = store.upsert_entity(
        entity_type="person",
        canonical_name="Jordan Customer",
        aliases=("J.C.", "First-only"),
        evidence_id=first_evidence,
    )
    same_jordan_id, _ = store.upsert_entity(
        entity_type="person",
        canonical_name="Jordan Customer",
        aliases=("J.C.", "Second-only"),
        evidence_id=second_evidence,
    )
    assert same_jordan_id == jordan_id
    vehicle_id, _ = store.upsert_entity(
        entity_type="vehicle",
        canonical_name="Blue Atlas SUV",
        evidence_ids=(first_evidence, second_evidence),
    )
    exclusive_id, _ = store.upsert_entity(
        entity_type="person",
        canonical_name="Taylor Visitor",
        aliases=("First-only visitor",),
        evidence_id=first_evidence,
    )
    relation_id, created = store.upsert_relation(
        subject_entity_id=jordan_id,
        predicate="owns",
        object_entity_id=vehicle_id,
        evidence_ids=(first_evidence, second_evidence),
    )
    assert created is True

    assert store.reconcile_rewind("session-a", [41]) == 1

    with store.connect() as connection:
        jordan = connection.execute(
            "SELECT deleted_at FROM entities WHERE id=?", (jordan_id,)
        ).fetchone()
        exclusive = connection.execute(
            "SELECT deleted_at FROM entities WHERE id=?", (exclusive_id,)
        ).fetchone()
        aliases = {
            str(row["normalized_alias"]): row["deleted_at"]
            for row in connection.execute(
                "SELECT normalized_alias, deleted_at FROM entity_aliases "
                "WHERE entity_id=?",
                (jordan_id,),
            ).fetchall()
        }
        relation = connection.execute(
            "SELECT status FROM relations WHERE id=?", (relation_id,)
        ).fetchone()
        jordan_fts = (
            connection.execute(
                "SELECT content FROM entity_fts WHERE id=?", (jordan_id,)
            ).fetchone()
            if store._fts_available
            else None
        )
        exclusive_fts = (
            connection.execute(
                "SELECT id FROM entity_fts WHERE id=?", (exclusive_id,)
            ).fetchone()
            if store._fts_available
            else None
        )

    assert jordan["deleted_at"] is None
    assert exclusive["deleted_at"] is not None
    assert aliases["first-only"] is not None
    assert aliases["second-only"] is None
    assert aliases["j.c."] is None
    assert relation["status"] == "active"
    if store._fts_available:
        assert "First-only" not in jordan_fts["content"]
        assert "Second-only" in jordan_fts["content"]
        assert exclusive_fts is None

    first_only_recall = store.recall(
        "First-only", allowed_spaces=("personal",), max_items=10, max_chars=10_000
    )
    assert all(
        item.id not in {jordan_id, exclusive_id} for item in first_only_recall.items
    )
    second_only_recall = store.recall(
        "Second-only", allowed_spaces=("personal",), max_items=10, max_chars=10_000
    )
    assert any(item.id == jordan_id for item in second_only_recall.items)
    graph_after_first = build_graph_overview(store, limit=100)
    graph_ids = {node["id"] for node in graph_after_first["nodes"]}
    assert jordan_id in graph_ids
    assert vehicle_id in graph_ids
    assert exclusive_id not in graph_ids
    assert relation_id in {edge["id"] for edge in graph_after_first["edges"]}

    assert store.reconcile_rewind("session-b", [52]) == 1
    with store.connect() as connection:
        final_entities = connection.execute(
            "SELECT id, deleted_at FROM entities WHERE id IN (?,?)",
            (jordan_id, vehicle_id),
        ).fetchall()
        final_relation = connection.execute(
            "SELECT status FROM relations WHERE id=?", (relation_id,)
        ).fetchone()
        remaining_fts = (
            connection.execute(
                "SELECT id FROM entity_fts WHERE id IN (?,?)",
                (jordan_id, vehicle_id),
            ).fetchall()
            if store._fts_available
            else []
        )
    assert all(row["deleted_at"] is not None for row in final_entities)
    assert final_relation["status"] == "rewound"
    if store._fts_available:
        assert remaining_fts == []
    final_graph_ids = {
        node["id"] for node in build_graph_overview(store, limit=100)["nodes"]
    }
    assert jordan_id not in final_graph_ids
    assert vehicle_id not in final_graph_ids


def test_session_checkpoint_never_triggers_semantic_model_work(tmp_path: Path) -> None:
    store, config, raw = _runtime(tmp_path)
    evidence_id, observation_id = _observation(store)
    job_id = store.enqueue_job(
        "session_checkpoint",
        input_hash=stable_hash("session-1", "checkpoint", evidence_id),
        input_data={"session_id": "session-1", "evidence_ids": [evidence_id]},
    )
    llm = _FakeLLM()

    result = CortexDreamWorker(
        store, config, raw_config=raw, llm_call=llm, owner="worker-a"
    ).run_once()

    assert result.status == "succeeded"
    assert llm.calls == []
    with store.connect() as connection:
        observation = connection.execute(
            "SELECT processing_state FROM observations WHERE id=?", (observation_id,)
        ).fetchone()
        memory_count = connection.execute(
            "SELECT COUNT(*) AS n FROM memory_records WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()["n"]
        job = connection.execute(
            "SELECT state, model, prompt_version FROM cognitive_jobs WHERE id=?",
            (job_id,),
        ).fetchone()
    assert observation["processing_state"] == "pending"
    assert memory_count == 0
    assert dict(job) == {
        "state": "succeeded",
        "model": None,
        "prompt_version": "atlas.cortex.checkpoint.v1",
    }


def test_operation_mutation_rolls_back_if_atomic_receipt_cannot_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, config, raw = _runtime(tmp_path)
    evidence_id, observation_id = _observation(store)
    _job(store, evidence_id, max_attempts=1)
    payload = {
        "schema_version": TRIAGE_SCHEMA_VERSION,
        "operations": [_operation(observation_id, evidence_id)],
    }

    def fail_receipt(*_args: Any, **_kwargs: Any) -> bool:
        raise RuntimeError("receipt storage failed")

    monkeypatch.setattr(store, "record_job_operation", fail_receipt)
    result = CortexDreamWorker(
        store,
        config,
        raw_config=raw,
        llm_call=_FakeLLM(_response(payload)),
        owner="worker-a",
    ).run_once()

    assert result.status == "failed"
    with store.connect() as connection:
        memory_count = connection.execute(
            "SELECT COUNT(*) AS n FROM memory_records WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()["n"]
        observation = connection.execute(
            "SELECT processing_state FROM observations WHERE id=?",
            (observation_id,),
        ).fetchone()
        operation_count = connection.execute(
            "SELECT COUNT(*) AS n FROM cognitive_job_operations",
        ).fetchone()["n"]
    assert memory_count == 0
    assert observation["processing_state"] == "pending"
    assert operation_count == 0


def test_per_brain_lease_prevents_a_second_worker(tmp_path: Path) -> None:
    store, config, raw = _runtime(tmp_path)
    evidence_id, _ = _observation(store)
    job_id = _job(store, evidence_id)
    assert store.acquire_named_lease(
        "dream-cycle", owner="other-worker", lease_seconds=60
    )

    result = CortexDreamWorker(
        store,
        config,
        raw_config=raw,
        llm_call=_FakeLLM(),
        owner="blocked-worker",
    ).run_once()

    assert result.status == "locked"
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT state FROM cognitive_jobs WHERE id=?", (job_id,)
            ).fetchone()["state"]
            == "queued"
        )


def test_scheduler_startup_catchup_and_recovery_are_enqueue_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store, config, _ = _runtime(tmp_path)
    schedule = CortexDreamSchedule(store, config)
    before_daily = datetime(2026, 7, 14, 2, 30, tzinfo=timezone.utc)
    assert schedule.enqueue_due(now=before_daily, startup=False) is None
    catchup_job = schedule.enqueue_due(now=before_daily, startup=True)
    assert catchup_job is not None
    assert schedule.enqueue_due(now=before_daily, startup=True) is None

    store.ensure_session("finalized-session")
    evidence_id = store.append_evidence(
        "finalized-session",
        EvidenceInput(
            source_type="user_message",
            content="Remember the approved callback window.",
            source_locator="finalized-session:user:1",
        ),
    )
    expected_evidence_hash = store.finalize_session("finalized-session")
    # Simulate an interrupted enqueue after the session transaction committed.
    with store.transaction() as connection:
        connection.execute(
            "DELETE FROM cognitive_jobs WHERE brain_id=? AND job_type='session_distill'",
            (store.brain_id,),
        )
    recovered = schedule.enqueue_recovery_jobs()
    assert len(recovered) == 1
    enqueue_cortex_wake(store, config, now=before_daily, startup=True)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    with store.connect() as connection:
        payload = json.loads(
            connection.execute(
                "SELECT input_json FROM cognitive_jobs WHERE id=?", (recovered[0],)
            ).fetchone()["input_json"]
        )
    assert payload == {
        "distill_version": 2,
        "evidence_ids": [evidence_id],
        "evidence_hash": expected_evidence_hash,
        "logical_conversation_id": "finalized-session",
        "session_id": "finalized-session",
        "session_ids": ["finalized-session"],
    }
    assert evidence_id


def test_recovery_quarantines_invalid_root_and_recreates_canonical_job(
    tmp_path: Path,
) -> None:
    store, config, _ = _runtime(tmp_path)
    evidence_id, _observation_id = _observation(
        store,
        session_id="invalid-root-session",
        content="The admission survives a corrupt root row.",
    )
    root_id = _job(store, evidence_id, session_id="invalid-root-session")
    with store.transaction() as connection:
        connection.execute(
            "UPDATE cognitive_jobs SET input_json='{}' WHERE id=?",
            (root_id,),
        )

    recovered = CortexDreamSchedule(store, config).enqueue_recovery_jobs()

    assert len(recovered) == 1
    assert recovered[0] != root_id
    with store.connect() as connection:
        old = connection.execute(
            "SELECT job_type, state FROM cognitive_jobs WHERE id=?", (root_id,)
        ).fetchone()
        new = connection.execute(
            "SELECT job_type, admission_id, parent_job_id, root_job_id "
            "FROM cognitive_jobs WHERE id=?",
            (recovered[0],),
        ).fetchone()
    assert dict(old) == {
        "job_type": "quarantined_session_distill",
        "state": "failed",
    }
    assert new["job_type"] == "session_distill"
    assert new["admission_id"]
    assert new["parent_job_id"] is None
    assert new["root_job_id"] == recovered[0]


def test_scheduler_ignores_invalid_descendant_and_continues_from_valid_leaf(
    tmp_path: Path,
) -> None:
    store, config, _ = _runtime(tmp_path)
    evidence_id, _observation_id = _observation(
        store,
        session_id="invalid-descendant-session",
        content="Pending work must survive an invalid descendant.",
    )
    root_id = _job(store, evidence_id, session_id="invalid-descendant-session")
    with store.transaction() as connection:
        connection.execute(
            "UPDATE cognitive_jobs SET state='succeeded', completed_at=? WHERE id=?",
            ("2026-07-14T00:00:00Z", root_id),
        )
    invalid_child = store.enqueue_session_distill_continuation(
        root_id,
        remaining_signature=["invalid-child-signature"],
    )
    with store.transaction() as connection:
        connection.execute(
            "UPDATE cognitive_jobs SET state='succeeded', input_json='{}', "
            "completed_at=? WHERE id=?",
            ("2026-07-14T00:00:01Z", invalid_child),
        )

    recovered = CortexDreamSchedule(store, config).enqueue_recovery_jobs()

    assert len(recovered) == 1
    with store.connect() as connection:
        invalid = connection.execute(
            "SELECT job_type, state FROM cognitive_jobs WHERE id=?",
            (invalid_child,),
        ).fetchone()
        replacement = connection.execute(
            "SELECT parent_job_id, root_job_id, admission_id FROM cognitive_jobs "
            "WHERE id=?",
            (recovered[0],),
        ).fetchone()
        root = connection.execute(
            "SELECT admission_id FROM cognitive_jobs WHERE id=?", (root_id,)
        ).fetchone()
    assert dict(invalid) == {
        "job_type": "quarantined_session_distill",
        "state": "failed",
    }
    assert replacement["parent_job_id"] == root_id
    assert replacement["root_job_id"] == root_id
    assert replacement["admission_id"] == root["admission_id"]


@pytest.mark.parametrize(
    ("orphan_state", "lease_expires_at"),
    (
        pytest.param("queued", None, id="queued"),
        pytest.param("running", None, id="running-missing-lease"),
        pytest.param("running", "not-a-timestamp", id="running-malformed-lease"),
    ),
)
def test_scheduler_quarantines_orphaned_child_after_root_replacement(
    tmp_path: Path,
    orphan_state: str,
    lease_expires_at: str | None,
) -> None:
    store, config, _ = _runtime(tmp_path)
    evidence_id, _observation_id = _observation(
        store,
        session_id="orphaned-child-session",
        content="Pending work must continue from the replacement root.",
    )
    old_root = _job(store, evidence_id, session_id="orphaned-child-session")
    with store.transaction() as connection:
        connection.execute(
            "UPDATE cognitive_jobs SET state='succeeded', completed_at=? WHERE id=?",
            ("2026-07-14T00:00:00Z", old_root),
        )
    orphaned_child = store.enqueue_session_distill_continuation(
        old_root,
        remaining_signature=["orphaned-child-signature"],
    )
    with store.transaction() as connection:
        connection.execute(
            "UPDATE cognitive_jobs SET input_json='{}' WHERE id=?",
            (old_root,),
        )

    replacements = store.recover_missing_session_distill_roots()
    assert len(replacements) == 1
    replacement_root = replacements[0]
    unrelated_running = store.enqueue_job(
        "session_checkpoint",
        input_hash=f"unrelated-running:{orphan_state}:{lease_expires_at}",
        input_data={"session_id": "unrelated-session"},
    )
    with store.transaction() as connection:
        connection.execute(
            "UPDATE cognitive_jobs SET state='succeeded', completed_at=? WHERE id=?",
            ("2026-07-14T00:00:01Z", replacement_root),
        )
        if orphan_state == "running":
            connection.execute(
                "UPDATE cognitive_jobs SET state='running', lease_owner='dead-worker', "
                "lease_expires_at=?, started_at=?, heartbeat_at=? WHERE id=?",
                (
                    lease_expires_at,
                    "2026-07-14T00:00:00Z",
                    "2026-07-14T00:00:01Z",
                    orphaned_child,
                ),
            )
        connection.execute(
            "UPDATE cognitive_jobs SET state='running', lease_owner='other-dead-worker', "
            "lease_expires_at=NULL, started_at=?, heartbeat_at=? WHERE id=?",
            (
                "2026-07-14T00:00:00Z",
                "2026-07-14T00:00:01Z",
                unrelated_running,
            ),
        )

    recovered = CortexDreamSchedule(store, config).enqueue_recovery_jobs()

    assert len(recovered) == 1
    with store.connect() as connection:
        orphan = connection.execute(
            "SELECT job_type, state FROM cognitive_jobs WHERE id=?",
            (orphaned_child,),
        ).fetchone()
        continuation = connection.execute(
            "SELECT parent_job_id, root_job_id FROM cognitive_jobs WHERE id=?",
            (recovered[0],),
        ).fetchone()
        unrelated = connection.execute(
            "SELECT state, lease_owner, lease_expires_at FROM cognitive_jobs WHERE id=?",
            (unrelated_running,),
        ).fetchone()
    assert dict(orphan) == {
        "job_type": "quarantined_session_distill",
        "state": "failed",
    }
    assert continuation["parent_job_id"] == replacement_root
    assert continuation["root_job_id"] == replacement_root
    assert dict(unrelated) == {
        "state": "running",
        "lease_owner": "other-dead-worker",
        "lease_expires_at": None,
    }


def test_recovery_page_retries_after_crash_before_cursor_ack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, config, _ = _runtime(tmp_path)
    for index in range(12):
        _succeeded_distill_root(
            store,
            f"crash-before-ack-{index:02d}",
            with_observation=index == 11,
        )

    cursor_key = f"session_distill_recovery_cursor:{store.brain_id}"
    original_ack = store.ack_session_distill_recovery_page
    tokens: list[str] = []

    def crash_before_ack(cursor_token: str) -> bool:
        tokens.append(cursor_token)
        raise RuntimeError("simulated scheduler crash before page ack")

    monkeypatch.setattr(store, "ack_session_distill_recovery_page", crash_before_ack)
    schedule = CortexDreamSchedule(store, config)
    with pytest.raises(RuntimeError, match="before page ack"):
        schedule.enqueue_recovery_jobs()
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT value FROM cortex_meta WHERE key=?", (cursor_key,)
            ).fetchone()
            is None
        )

    def record_ack(cursor_token: str) -> bool:
        tokens.append(cursor_token)
        return original_ack(cursor_token)

    monkeypatch.setattr(store, "ack_session_distill_recovery_page", record_ack)
    assert schedule.enqueue_recovery_jobs() == ()

    assert len(tokens) == 2
    assert tokens[0] == tokens[1]
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT value FROM cortex_meta WHERE key=?", (cursor_key,)
            ).fetchone()
            is not None
        )
        continuations = connection.execute(
            "SELECT COUNT(*) AS count FROM cognitive_jobs "
            "WHERE brain_id=? AND job_type='session_distill' "
            "AND parent_job_id IS NOT NULL",
            (store.brain_id,),
        ).fetchone()
    assert continuations["count"] == 1


def test_recovery_page_cursor_advances_only_after_explicit_ack(tmp_path: Path) -> None:
    store, _config, _ = _runtime(tmp_path)
    for index in range(15):
        _succeeded_distill_root(store, f"explicit-ack-{index:02d}")

    cursor_key = f"session_distill_recovery_cursor:{store.brain_id}"
    first = store.session_distill_recovery_page(page_size=10)
    retry = store.session_distill_recovery_page(page_size=10)
    assert first["cursor_token"] == retry["cursor_token"]
    assert tuple(row["id"] for row in first["admissions"]) == tuple(
        row["id"] for row in retry["admissions"]
    )
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT value FROM cortex_meta WHERE key=?", (cursor_key,)
            ).fetchone()
            is None
        )

    assert store.ack_session_distill_recovery_page(first["cursor_token"])
    assert store.ack_session_distill_recovery_page(first["cursor_token"])
    second = store.session_distill_recovery_page(page_size=10)

    assert first["inspected_count"] == 10
    assert second["inspected_count"] == 6
    assert first["cursor_token"] != second["cursor_token"]
    first_ids = {str(row["id"]) for row in first["admissions"]}
    second_ids = {str(row["id"]) for row in second["admissions"]}
    assert len(first_ids & second_ids) == 1


def test_new_admission_is_recovered_during_historical_rotation(tmp_path: Path) -> None:
    store, config, _ = _runtime(tmp_path)
    for index in range(15):
        _succeeded_distill_root(store, f"historical-rotation-{index:02d}")

    historical = store.session_distill_recovery_page(page_size=10)
    assert store.ack_session_distill_recovery_page(historical["cursor_token"])
    _evidence_id, new_root = _succeeded_distill_root(
        store,
        "fresh-during-rotation",
        with_observation=True,
    )

    recovered = CortexDreamSchedule(store, config).enqueue_recovery_jobs()

    assert len(recovered) == 1
    with store.connect() as connection:
        continuation = connection.execute(
            "SELECT parent_job_id, root_job_id FROM cognitive_jobs WHERE id=?",
            (recovered[0],),
        ).fetchone()
    assert continuation["parent_job_id"] == new_root
    assert continuation["root_job_id"] == new_root


def test_recovery_page_has_constant_query_and_job_budget_for_long_chain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _config, _ = _runtime(tmp_path)
    _evidence_id, root_id = _succeeded_distill_root(store, "long-recovery-chain")
    leaf_id = root_id
    for index in range(128):
        leaf_id = store.enqueue_session_distill_continuation(
            leaf_id,
            remaining_signature=[f"long-chain-step-{index}"],
        )
        with store.transaction() as connection:
            connection.execute(
                "UPDATE cognitive_jobs SET state='succeeded', completed_at=? WHERE id=?",
                (f"2026-07-14T00:01:{index % 60:02d}Z", leaf_id),
            )

    statements: list[str] = []
    original_connect = store.connect

    @contextmanager
    def traced_connect():
        with original_connect() as connection:
            connection.set_trace_callback(statements.append)
            try:
                yield connection
            finally:
                connection.set_trace_callback(None)

    monkeypatch.setattr(store, "connect", traced_connect)
    monkeypatch.setattr(
        store,
        "_validate_session_distill_job_in_transaction",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("recovery page must not walk job ancestry")
        ),
    )
    page = store.session_distill_recovery_page()

    assert page["inspected_count"] == 1
    assert page["job_count"] == 1
    assert page["jobs"][0]["id"] == leaf_id
    assert len(statements) == 4
    assert not any(
        statement.lstrip().upper().startswith(("BEGIN", "UPDATE", "DELETE", "INSERT"))
        for statement in statements
    )


def test_scheduler_pages_past_newest_hundred_admissions_for_due_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, config, _ = _runtime(tmp_path)
    oldest_root = ""
    for index in range(105):
        session_id = f"paged-admission-{index:03d}"
        store.ensure_session(session_id)
        evidence_id = store.append_evidence(
            session_id,
            EvidenceInput(
                source_type="user_message",
                content=f"Admission evidence {index}.",
                source_locator=f"{session_id}:user:1",
            ),
        )
        if index == 0:
            store.add_observation(
                session_id=session_id,
                kind="preference",
                text="The oldest admission still has due work.",
                evidence_ids=[evidence_id],
            )
        spec = store.lineage_distill_spec(session_id)
        store.finalize_session(session_id)
        with store.transaction() as connection:
            root = connection.execute(
                "SELECT id FROM cognitive_jobs WHERE brain_id=? "
                "AND job_type='session_distill' AND input_hash=?",
                (store.brain_id, spec["input_hash"]),
            ).fetchone()
            assert root is not None
            connection.execute(
                "UPDATE cognitive_jobs SET state='succeeded', completed_at=? WHERE id=?",
                (f"2026-07-14T00:00:{index % 60:02d}Z", root["id"]),
            )
        if index == 0:
            oldest_root = str(root["id"])

    statements: list[str] = []
    original_connect = store.connect

    @contextmanager
    def traced_connect():
        with original_connect() as connection:
            connection.set_trace_callback(statements.append)
            try:
                yield connection
            finally:
                connection.set_trace_callback(None)

    monkeypatch.setattr(store, "connect", traced_connect)
    assert store.recover_missing_session_distill_roots(limit=1) == ()
    monkeypatch.setattr(store, "connect", original_connect)
    root_recovery_selects = [
        statement
        for statement in statements
        if statement.lstrip().upper().startswith("SELECT")
    ]
    assert len(root_recovery_selects) == 1

    inspected: list[int] = []
    original_page = store.session_distill_recovery_page

    def tracked_page(**kwargs):
        page = original_page(**kwargs)
        inspected.append(int(page["inspected_count"]))
        return page

    monkeypatch.setattr(store, "session_distill_recovery_page", tracked_page)
    schedule = CortexDreamSchedule(store, config)
    assert schedule.enqueue_recovery_jobs(limit=1) == ()
    recovered = schedule.enqueue_recovery_jobs(limit=1)

    assert len(recovered) == 1
    assert inspected == [100, 15]
    with store.connect() as connection:
        continuation = connection.execute(
            "SELECT parent_job_id, root_job_id FROM cognitive_jobs WHERE id=?",
            (recovered[0],),
        ).fetchone()
    assert continuation["parent_job_id"] == oldest_root
    assert continuation["root_job_id"] == oldest_root


def test_recovery_uses_admitted_snapshot_after_lineage_is_resumed(
    tmp_path: Path,
) -> None:
    store, config, _ = _runtime(tmp_path)
    schedule = CortexDreamSchedule(store, config)
    store.ensure_session("resumed-session")
    first_evidence = store.append_evidence(
        "resumed-session",
        EvidenceInput(
            source_type="user_message",
            content="First epoch.",
            source_locator="resumed-session:first",
        ),
    )
    store.finalize_session("resumed-session")
    with store.transaction() as connection:
        connection.execute(
            "DELETE FROM cognitive_jobs WHERE brain_id=? AND job_type='session_distill'",
            (store.brain_id,),
        )
    # A genuinely new evidence row reactivates the physical session. Recovery
    # must not infer another terminal boundary while the user is still active.
    second_evidence = store.append_evidence(
        "resumed-session",
        EvidenceInput(
            source_type="user_message",
            content="Second active epoch.",
            source_locator="resumed-session:second",
        ),
    )

    assert store.session_lineage("resumed-session")["state"] == "active"
    recovered = schedule.enqueue_recovery_jobs()
    assert len(recovered) == 1
    with store.connect() as connection:
        payload = json.loads(
            connection.execute(
                "SELECT input_json FROM cognitive_jobs WHERE id=?", (recovered[0],)
            ).fetchone()["input_json"]
        )
    assert payload["evidence_ids"] == [first_evidence]
    assert second_evidence not in payload["evidence_ids"]


def test_recovery_never_infers_semantic_admission_from_mutable_session_state(
    tmp_path: Path,
) -> None:
    store, config, _ = _runtime(tmp_path)
    evidence_id, _observation_id = _observation(
        store,
        session_id="state-only-session",
        content="A mutable session row is not boundary provenance.",
    )
    with store.transaction() as connection:
        connection.execute(
            "UPDATE sessions SET state='finalized', evidence_hash=?, finalized_at=?, "
            "updated_at=? WHERE id=? AND brain_id=?",
            (
                stable_hash(evidence_id),
                "2026-07-14T00:00:00Z",
                "2026-07-14T00:00:00Z",
                "state-only-session",
                store.brain_id,
            ),
        )

    assert CortexDreamSchedule(store, config).enqueue_recovery_jobs() == ()
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) AS n FROM session_distill_admissions WHERE brain_id=?",
                (store.brain_id,),
            ).fetchone()["n"]
            == 0
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) AS n FROM cognitive_jobs WHERE brain_id=? "
                "AND job_type='session_distill'",
                (store.brain_id,),
            ).fetchone()["n"]
            == 0
        )


def test_native_wake_cron_is_idempotent_and_never_constructs_an_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _store, config, _raw = _runtime(tmp_path)
    jobs: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []

    def fake_create_job(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        job = {"id": "cortex-wake", **kwargs, "enabled": True}
        jobs.append(job)
        return job

    monkeypatch.setattr(
        "altas.cortex.scheduler._profile_cron_jobs",
        lambda _home: SimpleNamespace(
            list_jobs=lambda include_disabled=False: list(jobs),
            create_job=fake_create_job,
        ),
    )

    assert ensure_cortex_wake_cron(config) == "cortex-wake"
    assert ensure_cortex_wake_cron(config) == "cortex-wake"
    assert len(calls) == 1
    assert calls[0]["name"] == WAKE_CRON_NAME
    assert calls[0]["no_agent"] is True
    assert calls[0]["prompt"] is None
    assert calls[0]["schedule"] == "*/15 * * * *"
    script = (config.profile_home / "scripts" / WAKE_CRON_SCRIPT).read_text(
        encoding="utf-8"
    )
    assert "run_cortex_wake" in script
    assert "build_profile_secret_scope" in script
    assert "AIAgent" not in script
    assert "call_llm" not in script


def test_cron_installation_is_anchored_to_each_profile_even_after_import(
    tmp_path: Path,
) -> None:
    from altas.cortex import scheduler as scheduler_module

    scheduler_module._CRON_MODULES.clear()
    home_a = tmp_path / "profile-a"
    home_b = tmp_path / "profile-b"
    raw_a = _raw_config(home_a)
    raw_b = _raw_config(home_b)
    config_a = CortexConfig.from_mapping(raw_a, home_a)
    config_b = CortexConfig.from_mapping(raw_b, home_b)

    job_a = ensure_cortex_wake_cron(config_a)
    job_b = ensure_cortex_wake_cron(config_b)

    assert job_a and job_b
    jobs_a = json.loads((home_a / "cron" / "jobs.json").read_text())["jobs"]
    jobs_b = json.loads((home_b / "cron" / "jobs.json").read_text())["jobs"]
    assert [job["name"] for job in jobs_a] == [WAKE_CRON_NAME]
    assert [job["name"] for job in jobs_b] == [WAKE_CRON_NAME]
    assert jobs_a[0]["id"] != jobs_b[0]["id"]


def test_no_agent_wake_drains_all_deterministic_jobs_without_model_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, config, _raw = _runtime(tmp_path)
    reconcile_job = store.enqueue_job(
        "session_reconcile",
        input_hash=stable_hash("wake-reconcile"),
        input_data={"session_id": "session-1", "source_row_ids": []},
    )
    checkpoint_job = store.enqueue_job(
        "session_checkpoint",
        input_hash=stable_hash("wake-checkpoint"),
        input_data={"session_id": "session-1", "reason": "compatibility"},
    )
    legacy_dream_job = store.enqueue_job(
        "dream",
        input_hash=stable_hash("wake-legacy-dream"),
        input_data={"session_id": "session-1"},
    )
    monkeypatch.setattr(
        "altas.cortex.scheduler.resolve_model_route",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            CortexRouteError("no local route")
        ),
    )

    run_cortex_wake(
        store,
        config,
        now=datetime(2026, 7, 14, 2, 0, tzinfo=timezone.utc),
    )

    with store.connect() as connection:
        states = {
            row["id"]: (row["state"], row["attempt"])
            for row in connection.execute(
                "SELECT id, state, attempt FROM cognitive_jobs WHERE id IN (?,?,?)",
                (reconcile_job, checkpoint_job, legacy_dream_job),
            ).fetchall()
        }
    assert states[reconcile_job][0] == "succeeded"
    assert states[checkpoint_job][0] == "succeeded"
    assert states[legacy_dream_job] == ("succeeded", 1)


def test_daily_recovery_marker_is_deterministic_even_with_managed_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from altas.cortex.dream import CortexModelRoute

    store, config, _raw = _runtime(tmp_path)
    recovery_job = store.enqueue_job(
        "dream_cycle",
        input_hash=stable_hash("managed-wake"),
        input_data={"scheduled_slot": "2026-07-14T04:00:00Z"},
    )
    monkeypatch.setattr(
        "altas.cortex.scheduler.resolve_model_route",
        lambda *_args, **_kwargs: CortexModelRoute(
            task="cortex_triage",
            provider="altas",
            model="managed-model",
            managed_approved=True,
        ),
    )

    run_cortex_wake(
        store,
        config,
        now=datetime(2026, 7, 14, 2, 0, tzinfo=timezone.utc),
    )

    with store.connect() as connection:
        row = connection.execute(
            "SELECT state, attempt FROM cognitive_jobs WHERE id=?", (recovery_job,)
        ).fetchone()
    assert (row["state"], row["attempt"]) == ("succeeded", 1)


def test_no_agent_wake_drains_model_queue_after_local_route_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from altas.cortex import scheduler as scheduler_module
    from altas.cortex.dream import CortexModelRoute

    store, config, _raw = _runtime(tmp_path)
    drains: list[tuple[str, ...]] = []

    class FakeWorker:
        def __init__(self, _store: Any, _config: Any) -> None:
            pass

        def runtime_scope(self) -> Any:
            return nullcontext()

        def drain(self, *, max_jobs: int, job_types: tuple[str, ...]) -> list[Any]:
            drains.append(job_types)
            return []

    monkeypatch.setattr(scheduler_module, "CortexDreamWorker", FakeWorker)
    monkeypatch.setattr(
        scheduler_module,
        "resolve_model_route",
        lambda *_args, **_kwargs: CortexModelRoute(
            task="cortex_triage",
            provider="openrouter",
            model="local-model",
        ),
    )

    run_cortex_wake(
        store,
        config,
        now=datetime(2026, 7, 14, 2, 0, tzinfo=timezone.utc),
    )

    assert drains == [
        scheduler_module.DETERMINISTIC_JOB_TYPES,
        scheduler_module.MODEL_JOB_TYPES,
    ]


def test_retention_runs_even_when_model_processing_fails(tmp_path: Path) -> None:
    raw = _raw_config(tmp_path)
    raw["cortex"]["capture"] = {"raw_evidence_retention_days": 1}
    config = CortexConfig.from_mapping(raw, tmp_path)
    store = CortexStore(config.database_path, owner_customer_id="customer-a")
    store.initialize()
    old_evidence, _old_observation = _observation(
        store,
        session_id="old-session",
        content="Old raw transcript that must expire.",
    )
    fresh_evidence, _fresh_observation = _observation(
        store,
        session_id="fresh-session",
        content="Fresh preference that reaches the model.",
    )
    with store.transaction() as connection:
        connection.execute(
            "UPDATE evidence_items SET ingested_at='2020-01-01T00:00:00Z' WHERE id=?",
            (old_evidence,),
        )
    _job(store, fresh_evidence, session_id="fresh-session", suffix="retention")

    def fail_model(**_kwargs: Any) -> Any:
        raise RuntimeError("provider unavailable")

    result = CortexDreamWorker(
        store,
        config,
        raw_config=raw,
        llm_call=fail_model,
        owner="retention-worker",
    ).run_once()

    assert result.status == "failed"
    assert result.retention_purged == 1
    with store.connect() as connection:
        expired = connection.execute(
            "SELECT content, tombstoned_at FROM evidence_items WHERE id=?",
            (old_evidence,),
        ).fetchone()
    assert expired["content"] == "[expired by retention policy]"
    assert expired["tombstoned_at"] is not None


def test_supervisor_thread_preserves_profile_home_and_secret_scope(
    tmp_path: Path,
) -> None:
    from agent.secret_scope import (
        get_secret,
        reset_secret_scope,
        set_multiplex_active,
        set_secret_scope,
    )
    from altas.cortex.models import DreamReport
    from altas.cortex.dream import DreamOutcome
    from hermes_constants import get_hermes_home

    store, config, _raw = _runtime(tmp_path)
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=profile-only\n")
    evidence_id, _ = _observation(store)
    _job(store, evidence_id, suffix="profile-scope")
    observed: dict[str, str] = {}
    called = threading.Event()

    class ScopedProcessor:
        def process(self, job: Mapping[str, Any], *, owner: str) -> DreamOutcome:
            observed["home"] = str(get_hermes_home().resolve())
            observed["secret"] = str(get_secret("OPENROUTER_API_KEY"))
            called.set()
            report = DreamReport(job_id=str(job["id"]), status="succeeded")
            return DreamOutcome(report, "test", "test", 0, 0, 0)

        def record_failure(self, _job_id: str, _error: BaseException) -> None:
            raise AssertionError("scoped processor should not fail")

    set_multiplex_active(True)
    wrong_scope = set_secret_scope({"OPENROUTER_API_KEY": "other-profile"})
    supervisor: CortexDreamSupervisor | None = None
    try:
        worker = CortexDreamWorker(store, config, processor=ScopedProcessor())
        supervisor = CortexDreamSupervisor(worker, poll_seconds=30)
        supervisor.start()
        assert called.wait(5)
    finally:
        if supervisor is not None:
            supervisor.stop(timeout=2)
        reset_secret_scope(wrong_scope)
        set_multiplex_active(False)

    assert observed == {
        "home": str(tmp_path.resolve()),
        "secret": "profile-only",
    }


def test_supervisor_wake_processes_finalized_work_without_waiting_for_long_poll() -> (
    None
):
    first_cycle = threading.Event()
    woken_cycle = threading.Event()

    class WakeWorker:
        def __init__(self) -> None:
            self.store = SimpleNamespace(brain_id="brain-supervisor-wake")
            self.cycles = 0

        def runtime_scope(self) -> Any:
            return nullcontext()

        def drain(self, *, max_jobs: int) -> list[Any]:
            assert max_jobs == 25
            self.cycles += 1
            if self.cycles == 1:
                first_cycle.set()
            elif self.cycles == 2:
                woken_cycle.set()
            return []

    worker = WakeWorker()
    supervisor = CortexDreamSupervisor(worker, poll_seconds=3_600)  # type: ignore[arg-type]
    try:
        supervisor.start()
        assert first_cycle.wait(2)
        supervisor.wake()
        assert woken_cycle.wait(2)
    finally:
        supervisor.stop(timeout=2)

    assert worker.cycles >= 2


def test_runtime_reconciliation_restarts_changed_and_stops_disabled_supervisors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from altas.cortex import scheduler as scheduler_module
    from altas.cortex.dream import CortexModelRoute

    store, config, _raw = _runtime(tmp_path)
    supervisors: list[Any] = []
    route_model: dict[str, str | None] = {"value": None}

    class FakeSupervisor:
        def __init__(
            self,
            worker: Any,
            *,
            poll_seconds: int,
            enqueue_due: Any,
            runtime_signature: str,
        ) -> None:
            self.worker = worker
            self.running = False
            self.runtime_signature = runtime_signature
            self.stopped = False
            supervisors.append(self)

        def start(self) -> None:
            self.running = True

        def stop(self, timeout: float = 5.0) -> None:
            self.running = False
            self.stopped = True

    monkeypatch.setattr(scheduler_module, "CortexDreamSupervisor", FakeSupervisor)
    monkeypatch.setattr(scheduler_module, "ensure_cortex_wake_cron", lambda _c: None)
    monkeypatch.setattr(
        scheduler_module.CortexDreamSchedule,
        "enqueue_recovery_jobs",
        lambda self: (),
    )
    monkeypatch.setattr(
        scheduler_module.CortexDreamSchedule,
        "enqueue_due",
        lambda self, **kwargs: None,
    )

    def resolve_route(*_args: Any, **_kwargs: Any) -> CortexModelRoute:
        if route_model["value"] is None:
            raise CortexRouteError("route unavailable")
        return CortexModelRoute(
            task="cortex_triage",
            provider="openrouter",
            model=str(route_model["value"]),
        )

    monkeypatch.setattr(scheduler_module, "resolve_model_route", resolve_route)
    stop_cortex_runtimes()
    try:
        assert reconcile_cortex_runtime(store, config) is None
        unavailable = store.health()
        assert unavailable["status"] == "degraded"
        assert "model_route_unavailable" in unavailable["unresolved_runtime_events"]

        store.record_health_event(
            status="degraded",
            code="managed_worker_not_configured",
        )
        store.record_health_event(
            status="degraded",
            code="managed_worker_failure",
        )

        route_model["value"] = "model-a"
        first = reconcile_cortex_runtime(store, config)
        assert store.health()["unresolved_runtime_events"] == []
        assert reconcile_cortex_runtime(store, config) is first
        route_model["value"] = "model-b"
        second = reconcile_cortex_runtime(store, config)
        assert second is not first
        assert supervisors[0].stopped is True

        for code in (
            "model_route_unavailable",
            "managed_worker_not_configured",
            "managed_worker_failure",
        ):
            store.record_health_event(status="degraded", code=code)
        disabled = replace(config, dream_enabled=False)
        assert reconcile_cortex_runtime(store, disabled) is None
        assert supervisors[1].stopped is True
        assert store.health()["unresolved_runtime_events"] == []
    finally:
        stop_cortex_runtimes()


def test_managed_supervisor_records_bounded_failure_and_resolves_after_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from altas.cortex.scheduler import _ManagedCortexSupervisor

    store, _config, _raw = _runtime(
        tmp_path,
        owner="managed:tenant-health:store-health:agent-health",
    )
    failure_recorded = threading.Event()
    failure_resolved = threading.Event()
    worker_closed = threading.Event()
    calls = 0

    original_record = store.record_health_event
    original_resolve = store.resolve_health_event

    def record_health_event(**kwargs: Any) -> str:
        report_id = original_record(**kwargs)
        if kwargs.get("code") == "managed_worker_failure":
            failure_recorded.set()
        return report_id

    def resolve_health_event(code: str, **kwargs: Any) -> str | None:
        report_id = original_resolve(code, **kwargs)
        if code == "managed_worker_failure":
            failure_resolved.set()
        return report_id

    monkeypatch.setattr(store, "record_health_event", record_health_event)
    monkeypatch.setattr(store, "resolve_health_event", resolve_health_event)

    class RecoveringWorker:
        def __init__(self, _settings: Any) -> None:
            pass

        def run_once(self, *, capability_filter: str | None = None) -> Any:
            nonlocal calls
            assert capability_filter == "cortex.memory_maintenance"
            calls += 1
            if calls == 1:
                raise RuntimeError("raw failure detail must not be stored")
            return SimpleNamespace(status="idle")

        def close(self) -> None:
            worker_closed.set()

    monkeypatch.setattr("altas.managed.worker.AltasWorker", RecoveringWorker)
    supervisor = _ManagedCortexSupervisor(
        store=store,
        profile_home=tmp_path,
        environment={
            "ATLAS_CONTROL_PLANE_URL": "https://control.example.test",
            "ATLAS_DEVICE_TOKEN": "device-token",
            "ATLAS_DEVICE_ID": "device-health",
            "ATLAS_TENANT_ID": "tenant-health",
            "ATLAS_STORE_ID": "store-health",
            "ATLAS_AGENT_ID": "agent-health",
        },
        poll_seconds=3_600,
        runtime_signature="managed-health-test",
    )
    try:
        supervisor.start()
        assert failure_recorded.wait(2)
        failed = store.health()
        assert failed["status"] == "degraded"
        assert "managed_worker_failure" in failed["unresolved_runtime_events"]
        latest = store._latest_runtime_health_event("managed_worker_failure")
        report = json.loads(str(latest["report_json"]))
        assert report["details"] == {"error_type": "RuntimeError"}
        assert "raw failure detail" not in json.dumps(report)

        supervisor.wake()
        assert failure_resolved.wait(2)
        recovered = store.health()
        assert recovered["status"] == "healthy"
        assert "managed_worker_failure" not in recovered["unresolved_runtime_events"]
    finally:
        supervisor.stop(timeout=2)
    assert worker_closed.is_set()


def test_managed_runtime_starts_native_claim_poller_without_ephemeral_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from altas.cortex import scheduler as scheduler_module

    home = tmp_path / "managed-profile"
    home.mkdir()
    stable_identity = {
        "ATLAS_TENANT_ID": "tenant-a",
        "ATLAS_STORE_ID": "store-a",
        "ATLAS_AGENT_ID": "agent-a",
        "ATLAS_DEVICE_ID": "device-a",
        "ATLAS_DEVICE_TOKEN": "device-token-a",
        "ATLAS_CONTROL_PLANE_URL": "https://control.example.test",
    }
    (home / ".env").write_text(
        "\n".join(f"{key}={value}" for key, value in stable_identity.items()) + "\n",
        encoding="utf-8",
    )
    (home / "config.yaml").write_text(
        "\n".join((
            "model:",
            "  provider: altas",
            "  default: altas-fixed-ops",
            "memory:",
            "  provider: cortex",
            "cortex:",
            "  enabled: true",
            "  dream:",
            "    enabled: true",
            "auxiliary:",
            "  cortex_triage:",
            "    provider: altas",
            "    model: atlas-cortex-memory",
            "",
        )),
        encoding="utf-8",
    )
    called = threading.Event()
    observed: dict[str, Any] = {}

    class FakeManagedWorker:
        def __init__(self, settings: Any) -> None:
            observed["settings"] = settings

        def run_once(self, *, capability_filter: str | None = None) -> Any:
            observed["capability_filter"] = capability_filter
            called.set()
            return SimpleNamespace(status="idle")

        def close(self) -> None:
            observed["closed"] = True

    monkeypatch.setattr("altas.managed.worker.AltasWorker", FakeManagedWorker)
    monkeypatch.setattr(scheduler_module, "ensure_cortex_wake_cron", lambda _c: None)
    monkeypatch.setattr(
        scheduler_module.CortexDreamSchedule,
        "enqueue_recovery_jobs",
        lambda self: (),
    )
    monkeypatch.setattr(
        scheduler_module.CortexDreamSchedule,
        "enqueue_due",
        lambda self, **kwargs: None,
    )
    stop_cortex_runtimes()
    try:
        assert recover_managed_cortex_runtimes((("managed", home),)) == ("managed",)
        assert called.wait(5)
        assert observed["settings"].tenant_id == "tenant-a"
        assert observed["settings"].profile_home == home
        assert not hasattr(observed["settings"], "claim_token")
        assert observed["capability_filter"] == "cortex.memory_maintenance"
    finally:
        stop_cortex_runtimes()
    assert observed["closed"] is True
