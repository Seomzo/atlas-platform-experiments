"""Atlas Cortex graph/API contracts: scope, privacy, stability, and jobs."""

from __future__ import annotations

import base64
import json

import pytest

from altas.cortex.graph import (
    DETAIL_DTO_VERSION,
    GRAPH_DTO_VERSION,
    HEALTH_DTO_VERSION,
    JOB_DTO_VERSION,
    build_graph_overview,
    build_health,
    build_job_status,
    build_node_detail,
    enqueue_dream,
)
from altas.cortex.models import EvidenceInput
from altas.cortex.runtime import resolve_owner_customer_id
from altas.cortex.store import CortexStore, new_id, utc_now


@pytest.fixture
def populated_store(tmp_path):
    store = CortexStore(
        tmp_path / "profile" / "cortex" / "cortex.db",
        owner_customer_id="customer-graph-test",
        display_name="Graph Test Atlas",
    )
    store.initialize()
    store.ensure_session("session_graph", title="Service lane planning")
    evidence_body = "PRIVATE-EVIDENCE-BODY: customer prefers text updates after 3 PM"
    evidence_id = store.append_evidence(
        "session_graph",
        EvidenceInput(
            source_type="user_message",
            content=evidence_body,
            source_locator="session:session_graph:turn:1:user",
        ),
    )
    memory_id, _created = store.promote_memory(
        statement="The customer prefers text updates after 3 PM.",
        kind="preference",
        evidence_ids=[evidence_id],
    )
    customer_id, _ = store.upsert_entity(
        entity_type="person",
        canonical_name="Jordan Customer",
        description="A service-lane customer",
        evidence_id=evidence_id,
    )
    vehicle_id, _ = store.upsert_entity(
        entity_type="vehicle",
        canonical_name="Blue Atlas SUV",
        evidence_id=evidence_id,
    )
    relation_id, _ = store.upsert_relation(
        subject_entity_id=customer_id,
        predicate="owns",
        object_entity_id=vehicle_id,
        evidence_ids=[evidence_id],
    )
    store.ensure_session("cortex-capability-catalog")
    tool_evidence_id = store.append_evidence(
        "cortex-capability-catalog",
        EvidenceInput(
            source_type="system_event",
            content="Atlas tool: inventory",
            source_locator="atlas:tool:inventory",
            knowledge_space="atlas-capabilities",
        ),
    )
    tool_entity_id, _ = store.upsert_entity(
        entity_type="tool",
        canonical_name="inventory",
        description="Installed Atlas tool",
        evidence_id=tool_evidence_id,
        knowledge_space="atlas-capabilities",
    )
    store.append_work_event(
        session_id="session_graph",
        event_type="tool_call",
        summary="Called Atlas tool inventory",
        evidence_id=evidence_id,
        metadata={"tool_name": "inventory", "tool_call_id": "call-1"},
    )

    document_id = new_id("document")
    community_id = new_id("community")
    document_body = "PRIVATE-GRAPHRAG-BODY: internal Tekion workflow source text"
    with store.transaction() as connection:
        index_id = new_id("graphrag")
        connection.execute(
            "INSERT INTO graphrag_indexes(id, brain_id, version, path, manifest_hash, state, "
            "document_count, published_at, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                index_id,
                store.brain_id,
                "tekion-test-v1",
                "fixture/index",
                "fixture-hash",
                "active",
                1,
                utc_now(),
                utc_now(),
            ),
        )
        connection.execute(
            "INSERT INTO graphrag_documents(id, brain_id, index_id, knowledge_space_id, "
            "document_id, title, text, source_uri, entity_ids_json, community_ids_json, "
            "metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                document_id,
                store.brain_id,
                index_id,
                store.space_id("tekion", connection=connection),
                "tekion-workflow-1",
                "Create a repair order",
                document_body,
                "atlas://tekion/workflows/repair-order",
                json.dumps([vehicle_id]),
                "[]",
                "{}",
            ),
        )
        connection.execute(
            "INSERT INTO communities(id, brain_id, knowledge_space_id, level, label, "
            "report, algorithm_version, member_ids_json, stale, generated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                community_id,
                store.brain_id,
                store.space_id("personal", connection=connection),
                0,
                "Customer vehicle context",
                "Connected customer and vehicle entities.",
                "test:fixture:v1",
                json.dumps([customer_id, vehicle_id]),
                0,
                utc_now(),
            ),
        )

    return {
        "store": store,
        "evidence_id": evidence_id,
        "evidence_body": evidence_body,
        "memory_id": memory_id,
        "customer_id": customer_id,
        "vehicle_id": vehicle_id,
        "relation_id": relation_id,
        "tool_entity_id": tool_entity_id,
        "document_id": document_id,
        "community_id": community_id,
        "document_body": document_body,
    }


def test_graph_overview_is_stable_typed_bounded_and_body_free(populated_store):
    store = populated_store["store"]
    first = build_graph_overview(store, limit=50)
    second = build_graph_overview(store, limit=50)

    assert first["version"] == GRAPH_DTO_VERSION
    assert "brain_id" not in first
    assert "database_path" not in first
    serialized = json.dumps(first)
    assert populated_store["evidence_body"] not in serialized
    assert populated_store["document_body"] not in serialized
    assert first["redaction_summary"]["raw_evidence_bodies_hidden"] >= 1
    assert first["redaction_summary"]["document_bodies_hidden"] >= 1
    assert first["aggregates"] == {
        "relation_count": 1,
        "total_nodes": 10,
        "types": [
            {"type": "memory", "count": 1, "uncommunitied_count": 1},
            {"type": "session", "count": 2, "uncommunitied_count": 2},
            {"type": "community", "count": 1, "uncommunitied_count": 0},
            {"type": "document", "count": 1, "uncommunitied_count": 1},
            {"type": "entity", "count": 3, "uncommunitied_count": 1},
            {"type": "evidence", "count": 2, "uncommunitied_count": 2},
        ],
    }

    node_ids = {node["id"] for node in first["nodes"]}
    assert populated_store["evidence_id"] in node_ids
    assert populated_store["document_id"] in node_ids
    assert populated_store["community_id"] in node_ids
    assert {node["type"] for node in first["nodes"]} == {
        "community",
        "document",
        "entity",
        "evidence",
        "memory",
        "session",
    }
    assert all(len(node["summary"]) <= 420 for node in first["nodes"])
    assert all(edge["direction"] == "directed" for edge in first["edges"])
    assert populated_store["relation_id"] in {edge["id"] for edge in first["edges"]}
    assert any(
        edge["type"] == "used_tool"
        and edge["source"] == "session_graph"
        and edge["target"] == populated_store["tool_entity_id"]
        for edge in first["edges"]
    )
    assert [edge["id"] for edge in first["edges"]] == [
        edge["id"] for edge in second["edges"]
    ]

    page_one = build_graph_overview(store, limit=2)
    assert len(page_one["nodes"]) == 2
    assert page_one["next_cursor"]
    page_two = build_graph_overview(store, limit=2, cursor=page_one["next_cursor"])
    assert {node["id"] for node in page_one["nodes"]}.isdisjoint({
        node["id"] for node in page_two["nodes"]
    })

    community_page = build_graph_overview(
        store,
        limit=1,
        node_types=["entity"],
        community_id=populated_store["community_id"],
    )
    community_next = build_graph_overview(
        store,
        limit=1,
        cursor=community_page["next_cursor"],
        node_types=["entity"],
        community_id=populated_store["community_id"],
    )
    assert {node["id"] for node in community_page["nodes"] + community_next["nodes"]} == {
        populated_store["customer_id"],
        populated_store["vehicle_id"],
    }

    uncommunitied = build_graph_overview(
        store, limit=10, node_types=["entity"], uncommunitied=True
    )
    assert [node["id"] for node in uncommunitied["nodes"]] == [
        populated_store["tool_entity_id"]
    ]

    deep_cursor = base64.urlsafe_b64encode(
        json.dumps({"offset": 10_001}, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    assert build_graph_overview(store, limit=1, cursor=deep_cursor)["nodes"] == []


def test_graph_overview_first_page_keeps_small_memory_layer_complete(tmp_path):
    store = CortexStore(
        tmp_path / "stratified" / "cortex.db",
        owner_customer_id="customer-stratified-graph",
    )
    store.initialize()
    store.ensure_session("session-stratified")
    support_id = store.append_evidence(
        "session-stratified",
        EvidenceInput(
            source_type="manual",
            content="Shared support for durable memories",
            source_locator="test:stratified:support",
        ),
    )
    memory_ids = {
        store.promote_memory(
            statement=f"Durable memory {index}",
            kind="fact",
            evidence_ids=[support_id],
        )[0]
        for index in range(40)
    }
    for index in range(300):
        store.append_evidence(
            "session-stratified",
            EvidenceInput(
                source_type="manual",
                content=f"Newer high-volume evidence {index}",
                source_locator=f"test:stratified:evidence:{index}",
            ),
        )

    first_page = build_graph_overview(store, limit=100)

    returned_memory_ids = {
        node["id"] for node in first_page["nodes"] if node["type"] == "memory"
    }
    assert returned_memory_ids == memory_ids
    assert any(node["type"] == "evidence" for node in first_page["nodes"])

    seen_ids: set[str] = set()
    cursor = None
    while True:
        page = build_graph_overview(store, limit=37, cursor=cursor)
        page_ids = {str(node["id"]) for node in page["nodes"]}
        assert seen_ids.isdisjoint(page_ids)
        seen_ids.update(page_ids)
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert memory_ids.issubset(seen_ids)


def test_node_detail_loads_selected_source_only_and_enforces_brain_scope(
    populated_store, tmp_path
):
    store = populated_store["store"]
    detail = build_node_detail(store, populated_store["evidence_id"])
    assert detail["version"] == DETAIL_DTO_VERSION
    assert detail["node"]["id"] == populated_store["evidence_id"]
    assert detail["content"] == populated_store["evidence_body"]
    assert "brain_id" not in json.dumps(detail)

    other = CortexStore(
        tmp_path / "other" / "cortex.db",
        owner_customer_id="different-customer",
    )
    other.initialize()
    with pytest.raises(LookupError, match="not found"):
        build_node_detail(other, populated_store["evidence_id"])


def test_rewind_removes_stale_content_from_graph_overview_and_detail(tmp_path):
    store = CortexStore(
        tmp_path / "rewind-visibility" / "cortex.db",
        owner_customer_id="customer-rewind-visibility",
    )
    store.initialize()
    store.ensure_session("session-rewind")
    rewound_evidence_id = store.append_evidence(
        "session-rewind",
        EvidenceInput(
            source_type="user_message",
            content="UNDO-ME evidence body",
            source_locator="session-rewind:row:41:user",
            metadata={"source_row_id": 41},
        ),
    )
    retained_evidence_id = store.append_evidence(
        "session-rewind",
        EvidenceInput(
            source_type="user_message",
            content="KEEP-ME evidence body",
            source_locator="session-rewind:row:42:user",
            metadata={"source_row_id": 42},
        ),
    )
    memory_id, _ = store.promote_memory(
        statement="UNDO-ME durable memory",
        kind="preference",
        evidence_ids=[rewound_evidence_id],
    )
    subject_id, _ = store.upsert_entity(
        entity_type="person",
        canonical_name="Jordan Customer",
        aliases=("UNDO-ME alias",),
        evidence_id=rewound_evidence_id,
    )
    same_subject_id, _ = store.upsert_entity(
        entity_type="person",
        canonical_name="Jordan Customer",
        aliases=("Kept alias",),
        evidence_id=retained_evidence_id,
    )
    assert same_subject_id == subject_id
    object_id, _ = store.upsert_entity(
        entity_type="vehicle",
        canonical_name="Blue Atlas SUV",
        evidence_ids=(rewound_evidence_id, retained_evidence_id),
    )
    relation_id, _ = store.upsert_relation(
        subject_entity_id=subject_id,
        predicate="undo_me_relation",
        object_entity_id=object_id,
        evidence_ids=(rewound_evidence_id,),
    )
    community_id = new_id("community")
    finalized_at = utc_now()
    with store.transaction() as connection:
        connection.execute(
            "INSERT INTO communities(id, brain_id, knowledge_space_id, level, label, "
            "report, algorithm_version, member_ids_json, stale, generated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                community_id,
                store.brain_id,
                store.space_id("personal", connection=connection),
                0,
                "UNDO-ME community",
                "UNDO-ME community report",
                "test-v1",
                json.dumps([subject_id, memory_id]),
                0,
                finalized_at,
            ),
        )
        connection.execute(
            "UPDATE sessions SET state='finalized', summary=?, evidence_hash=?, "
            "finalized_at=?, updated_at=? WHERE id=? AND brain_id=?",
            (
                "UNDO-ME session summary",
                "stale-boundary-hash",
                finalized_at,
                finalized_at,
                "session-rewind",
                store.brain_id,
            ),
        )

    before = build_graph_overview(store, limit=100)
    before_ids = {node["id"] for node in before["nodes"]}
    assert {rewound_evidence_id, memory_id, community_id}.issubset(before_ids)
    assert relation_id in {edge["id"] for edge in before["edges"]}
    assert "UNDO-ME alias" in build_node_detail(store, subject_id)["detail"]["aliases"]
    assert build_node_detail(store, "session-rewind")["content"] == (
        "UNDO-ME session summary"
    )

    assert store.reconcile_rewind("session-rewind", [41]) == 1

    after = build_graph_overview(store, limit=100)
    serialized_after = json.dumps(after)
    after_ids = {node["id"] for node in after["nodes"]}
    assert rewound_evidence_id not in after_ids
    assert memory_id not in after_ids
    assert community_id not in after_ids
    assert relation_id not in {edge["id"] for edge in after["edges"]}
    assert "UNDO-ME" not in serialized_after

    subject_detail = build_node_detail(store, subject_id)
    assert "UNDO-ME alias" not in subject_detail["detail"]["aliases"]
    assert "Kept alias" in subject_detail["detail"]["aliases"]
    assert relation_id not in {edge["id"] for edge in subject_detail["edges"]}
    assert rewound_evidence_id not in {
        item["id"] for item in subject_detail["evidence"]
    }

    session_detail = build_node_detail(store, "session-rewind")
    assert session_detail["content"] == ""
    assert session_detail["node"]["summary"] == "Conversation session"
    assert rewound_evidence_id not in {
        item["id"] for item in session_detail["evidence"]
    }
    assert retained_evidence_id in {
        item["id"] for item in session_detail["evidence"]
    }
    with store.connect() as connection:
        session = connection.execute(
            "SELECT state, summary, evidence_hash, finalized_at FROM sessions "
            "WHERE id=? AND brain_id=?",
            ("session-rewind", store.brain_id),
        ).fetchone()
    assert dict(session) == {
        "state": "active",
        "summary": None,
        "evidence_hash": None,
        "finalized_at": None,
    }

    for hidden_id in (rewound_evidence_id, memory_id, community_id):
        with pytest.raises(LookupError, match="not found"):
            build_node_detail(store, hidden_id)

    # A SessionDB row can be rewound before Cortex captured it. The session
    # summary is still derived from the old transcript and must be cleared even
    # when no evidence row matches the stable source-row locator.
    with store.transaction() as connection:
        connection.execute(
            "UPDATE sessions SET state='finalized', summary=?, evidence_hash=?, "
            "finalized_at=? WHERE id=? AND brain_id=?",
            (
                "UNDO-ME uncaptured summary",
                "uncaptured-boundary-hash",
                utc_now(),
                "session-rewind",
                store.brain_id,
            ),
        )
    assert store.reconcile_rewind("session-rewind", [999]) == 0
    unmatched_detail = build_node_detail(store, "session-rewind")
    assert unmatched_detail["content"] == ""
    assert "UNDO-ME" not in json.dumps(unmatched_detail)


def test_session_graph_detail_never_exposes_secrets_from_title_or_workspace(tmp_path):
    store = CortexStore(
        tmp_path / "redacted-session" / "cortex.db",
        owner_customer_id="customer-redacted-session",
        redact_secrets=True,
    )
    store.initialize()
    provider_key = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
    github_key = "ghp_abcdefghijklmnopqrstuvwxyz0123456789"
    store.ensure_session(
        "session-with-secret-metadata",
        title=f"Debug API_KEY={provider_key}",
        workspace=f"/tmp/customer/{github_key}",
    )

    detail = build_node_detail(store, "session-with-secret-metadata")
    serialized = json.dumps(detail)

    assert provider_key not in serialized
    assert github_key not in serialized
    assert "[REDACTED]" in serialized


def test_health_and_dream_status_omit_internal_identity_and_job_input(populated_store):
    store = populated_store["store"]
    queued = enqueue_dream(store, source="test")
    status = build_job_status(store, queued["job"]["id"])
    health = build_health(store)

    assert queued["version"] == JOB_DTO_VERSION
    assert status["job"]["type"] == "dream"
    assert status["job"]["status"] == "queued"
    assert "input" not in status["job"]
    assert health["version"] == HEALTH_DTO_VERSION
    assert health["jobs"]["pending"] >= 1
    serialized = json.dumps(health)
    assert store.brain_id not in serialized
    assert str(store.path) not in serialized

    with store.transaction() as connection:
        connection.execute(
            "UPDATE cognitive_jobs SET next_attempt_at='2000-01-01T00:00:00Z' WHERE id=?",
            (queued["job"]["id"],),
        )
    stale = build_health(store)
    assert stale["status"] == "degraded"
    assert stale["jobs"]["stale"] is True
    assert stale["jobs"]["oldest_pending_age_seconds"] >= 900

    with store.transaction() as connection:
        connection.execute(
            "UPDATE cognitive_jobs SET state='dead_letter', dead_letter_at=? WHERE id=?",
            (utc_now(), queued["job"]["id"]),
        )
    degraded = build_health(store)
    assert degraded["status"] == "degraded"
    assert degraded["jobs"]["failed"] == 1


def test_health_staleness_tracks_due_and_lease_deadlines_not_job_age(tmp_path):
    store = CortexStore(
        tmp_path / "health" / "cortex.db",
        owner_customer_id="customer-health-deadlines",
    )
    store.initialize()
    future_job = store.enqueue_job(
        "dream",
        input_hash="future-retry",
        input_data={},
        scheduled_at="2999-01-01T00:00:00Z",
    )
    running_job = store.enqueue_job(
        "session_checkpoint",
        input_hash="valid-running-lease",
        input_data={"session_id": "session-health"},
    )
    leased = store.lease_job(
        job_types=("session_checkpoint",),
        owner="health-test-worker",
        lease_seconds=3_600,
    )
    assert leased and leased["id"] == running_job
    with store.transaction() as connection:
        connection.execute(
            "UPDATE cognitive_jobs SET created_at='2000-01-01T00:00:00Z', "
            "lease_expires_at='2999-01-01T00:00:00Z' WHERE id IN (?,?)",
            (future_job, running_job),
        )

    healthy = build_health(store)
    assert healthy["status"] == "healthy"
    assert healthy["jobs"]["stale"] is False
    assert healthy["jobs"]["oldest_overdue_age_seconds"] == 0
    assert healthy["jobs"]["expired_running"] == 0

    with store.transaction() as connection:
        connection.execute(
            "UPDATE cognitive_jobs SET next_attempt_at='2000-01-01T00:00:00Z' "
            "WHERE id=?",
            (future_job,),
        )
    overdue = build_health(store)
    assert overdue["status"] == "degraded"
    assert overdue["jobs"]["stale"] is True
    assert overdue["jobs"]["oldest_overdue_age_seconds"] >= 900

    with store.transaction() as connection:
        connection.execute(
            "UPDATE cognitive_jobs SET next_attempt_at='2999-01-01T00:00:00Z' "
            "WHERE id=?",
            (future_job,),
        )
        connection.execute(
            "UPDATE cognitive_jobs SET lease_expires_at='2000-01-01T00:00:00Z' "
            "WHERE id=?",
            (running_job,),
        )
    expired = build_health(store)
    assert expired["status"] == "degraded"
    assert expired["jobs"]["stale"] is True
    assert expired["jobs"]["expired_running"] == 1


def _make_profile_store(home, memory_statement):
    store = CortexStore(
        home / "cortex" / "cortex.db",
        owner_customer_id=resolve_owner_customer_id(home),
    )
    store.initialize()
    store.ensure_session("session_api")
    evidence_id = store.append_evidence(
        "session_api",
        EvidenceInput(
            source_type="manual",
            content=memory_statement,
            source_locator=f"test:{home.name}:memory",
        ),
    )
    memory_id, _ = store.promote_memory(
        statement=memory_statement,
        kind="fact",
        evidence_ids=[evidence_id],
    )
    return store, memory_id


def test_cognitive_routes_use_requested_profile_and_never_client_brain_id(
    monkeypatch, _isolate_hermes_home
):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    from hermes_constants import get_hermes_home
    from hermes_cli import profiles
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    default_home = get_hermes_home()
    profiles_root = default_home / "profiles"
    worker_home = profiles_root / "worker_graph"
    empty_worker_home = profiles_root / "worker_empty"
    for home in (default_home, worker_home, empty_worker_home):
        home.mkdir(parents=True, exist_ok=True)
        (home / "config.yaml").write_text(
            "cortex:\n  enabled: true\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(profiles, "_get_default_hermes_home", lambda: default_home)
    monkeypatch.setattr(profiles, "_get_profiles_root", lambda: profiles_root)

    default_store, _ = _make_profile_store(default_home, "DEFAULT-PROFILE-MEMORY")
    worker_store, worker_memory_id = _make_profile_store(
        worker_home, "WORKER-PROFILE-MEMORY"
    )
    assert default_store.brain_id != worker_store.brain_id
    worker_evidence_id = worker_store.append_evidence(
        "session_api",
        EvidenceInput(
            source_type="manual",
            content="Worker graph relationship source",
            source_locator="test:worker:relationship",
        ),
    )
    worker_person_id, _ = worker_store.upsert_entity(
        entity_type="person",
        canonical_name="Worker Person",
        evidence_id=worker_evidence_id,
    )
    worker_vehicle_id, _ = worker_store.upsert_entity(
        entity_type="vehicle",
        canonical_name="Worker Vehicle",
        evidence_id=worker_evidence_id,
    )
    worker_store.upsert_relation(
        subject_entity_id=worker_person_id,
        predicate="owns",
        object_entity_id=worker_vehicle_id,
        evidence_ids=[worker_evidence_id],
    )
    worker_community_id = new_id("community")
    with worker_store.transaction() as connection:
        worker_index_id = new_id("graphrag")
        now = utc_now()
        connection.execute(
            "INSERT INTO graphrag_indexes(id, brain_id, version, path, manifest_hash, state, "
            "document_count, published_at, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                worker_index_id,
                worker_store.brain_id,
                "worker-fixture-v1",
                "fixture/worker-index",
                "worker-fixture-hash",
                "active",
                1,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO graphrag_documents(id, brain_id, index_id, knowledge_space_id, "
            "document_id, title, text, source_uri, entity_ids_json, community_ids_json, "
            "metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                new_id("document"),
                worker_store.brain_id,
                worker_index_id,
                worker_store.space_id("tekion", connection=connection),
                "worker-doc",
                "Worker document",
                "Private worker document body",
                "atlas://fixture/worker-doc",
                json.dumps([worker_vehicle_id]),
                "[]",
                "{}",
            ),
        )
        connection.execute(
            "INSERT INTO communities(id, brain_id, knowledge_space_id, level, label, "
            "report, algorithm_version, member_ids_json, stale, generated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                worker_community_id,
                worker_store.brain_id,
                worker_store.space_id("personal", connection=connection),
                0,
                "Worker community",
                "Connected worker entities",
                "test:worker:v1",
                json.dumps([worker_person_id, worker_vehicle_id]),
                0,
                now,
            ),
        )

    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    unauthenticated = TestClient(app)
    assert unauthenticated.get("/api/cognitive/health").status_code == 401
    worker_graph = client.get(
        "/api/cognitive/graph",
        params={
            "profile": "worker_graph",
            "brain_id": default_store.brain_id,
        },
    )
    assert worker_graph.status_code == 200
    payload = worker_graph.json()
    serialized = json.dumps(payload)
    assert "WORKER-PROFILE-MEMORY" in serialized
    assert "DEFAULT-PROFILE-MEMORY" not in serialized
    assert default_store.brain_id not in serialized
    assert {node["type"] for node in payload["nodes"]} == {
        "community",
        "document",
        "entity",
        "evidence",
        "memory",
        "session",
    }

    empty_graph = client.get(
        "/api/cognitive/graph", params={"profile": "worker_empty"}
    )
    assert empty_graph.status_code == 200
    assert empty_graph.json()["nodes"] == []

    detail = client.get(
        f"/api/cognitive/node/{worker_memory_id}",
        params={"profile": "worker_graph", "brain_id": default_store.brain_id},
    )
    assert detail.status_code == 200
    assert detail.json()["content"] == "WORKER-PROFILE-MEMORY"

    rejected_identity = client.post(
        "/api/cognitive/dream/run",
        json={"profile": "worker_graph", "brain_id": default_store.brain_id},
    )
    assert rejected_identity.status_code == 422

    queued = client.post("/api/cognitive/dream/run", json={"profile": "worker_graph"})
    assert queued.status_code == 200
    job_id = queued.json()["job"]["id"]
    job = client.get(
        f"/api/cognitive/dream/{job_id}", params={"profile": "worker_graph"}
    )
    assert job.status_code == 200
    assert job.json()["job"]["type"] == "dream"

    query_routed = client.post(
        "/api/cognitive/dream/run",
        params={"profile": "worker_graph"},
        json={},
    )
    assert query_routed.status_code == 200
    query_job_id = query_routed.json()["job"]["id"]
    assert (
        client.get(
            f"/api/cognitive/dream/{query_job_id}",
            params={"profile": "worker_graph"},
        ).status_code
        == 200
    )
    assert client.get(f"/api/cognitive/dream/{query_job_id}").status_code == 404

    mismatched = client.post(
        "/api/cognitive/dream/run",
        params={"profile": "worker_graph"},
        json={"profile": "default"},
    )
    assert mismatched.status_code == 400


def test_cognitive_openapi_declares_strict_public_response_contracts():
    try:
        from hermes_cli.web_server import app
    except ImportError:
        pytest.skip("fastapi/pydantic not installed")

    schema = app.openapi()
    expected = {
        ("/api/cognitive/graph", "get"): (
            "CortexGraphResponse",
            {
                "aggregates",
                "communities",
                "edges",
                "facets",
                "generated_at",
                "layout_seed",
                "next_cursor",
                "nodes",
                "projection",
                "redaction_summary",
                "retrieval_run_id",
                "timeline_window",
                "version",
            },
        ),
        ("/api/cognitive/node/{node_id}", "get"): (
            "CortexNodeDetailResponse",
            {
                "content",
                "detail",
                "edges",
                "evidence",
                "generated_at",
                "neighbors",
                "node",
                "redaction_summary",
                "version",
            },
        ),
        ("/api/cognitive/health", "get"): (
            "CortexHealthResponse",
            {
                "capabilities",
                "counts",
                "generated_at",
                "graphrag",
                "jobs",
                "name",
                "quality",
                "schema_version",
                "status",
                "version",
            },
        ),
        ("/api/cognitive/dream/run", "post"): (
            "CortexJobResponse",
            {"job", "version"},
        ),
        ("/api/cognitive/dream/{job_id}", "get"): (
            "CortexJobResponse",
            {"job", "version"},
        ),
    }
    components = schema["components"]["schemas"]
    for (path, method), (component_name, fields) in expected.items():
        response_schema = schema["paths"][path][method]["responses"]["200"]["content"][
            "application/json"
        ]["schema"]
        assert response_schema == {"$ref": f"#/components/schemas/{component_name}"}
        component = components[component_name]
        assert set(component["properties"]) == fields
        assert set(component["required"]) == fields
        assert component["additionalProperties"] is False

    public_schema = json.dumps({
        name: value for name, value in components.items() if name.startswith("Cortex")
    })
    assert "brain_id" not in public_schema
    assert "database_path" not in public_schema
    assert "lease_token" not in public_schema


def test_cognitive_http_contract_blocks_private_and_overview_body_fields(
    monkeypatch, _isolate_hermes_home
):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    import altas.cortex.graph as graph_module
    from hermes_constants import get_hermes_home
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    home = get_hermes_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "cortex:\n  enabled: true\n",
        encoding="utf-8",
    )
    _store, memory_id = _make_profile_store(home, "SELECTED-DETAIL-BODY")

    client = TestClient(app, raise_server_exceptions=False)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    detail = client.get(f"/api/cognitive/node/{memory_id}")
    assert detail.status_code == 200
    assert detail.json()["content"] == "SELECTED-DETAIL-BODY"

    real_build_graph = graph_module.build_graph_overview
    private_marker = "PRIVATE-BRAIN-ID-MARKER"

    def graph_with_private_identity(*args, **kwargs):
        result = real_build_graph(*args, **kwargs)
        result["brain_id"] = private_marker
        return result

    monkeypatch.setattr(
        graph_module, "build_graph_overview", graph_with_private_identity
    )
    blocked_identity = client.get("/api/cognitive/graph", params={"types": "memory"})
    assert blocked_identity.status_code == 500
    assert private_marker not in blocked_identity.text

    raw_marker = "PRIVATE-OVERVIEW-BODY-MARKER"

    def graph_with_raw_body(*args, **kwargs):
        result = real_build_graph(*args, **kwargs)
        result["nodes"][0]["metadata"]["content"] = raw_marker
        return result

    monkeypatch.setattr(graph_module, "build_graph_overview", graph_with_raw_body)
    blocked_body = client.get("/api/cognitive/graph", params={"types": "memory"})
    assert blocked_body.status_code == 500
    assert raw_marker not in blocked_body.text
