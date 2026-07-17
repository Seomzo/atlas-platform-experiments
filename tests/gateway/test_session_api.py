"""Focused tests for API server session-control endpoints."""

import asyncio
import threading
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gateway.config import PlatformConfig
from altas.cortex.models import EvidenceInput
from altas.cortex.runtime import open_cortex_store
from gateway.platforms.api_server import APIServerAdapter
from hermes_state import SessionDB


@pytest.fixture
def session_db(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    try:
        yield db
    finally:
        close = getattr(db, "close", None)
        if callable(close):
            close()


@pytest.fixture
def adapter(session_db):
    adapter = APIServerAdapter(PlatformConfig(enabled=True))
    adapter._session_db = session_db
    return adapter


@pytest.fixture
def auth_adapter(session_db):
    adapter = APIServerAdapter(PlatformConfig(enabled=True, extra={"key": "sk-test"}))
    adapter._session_db = session_db
    return adapter


def _create_session_app(adapter: APIServerAdapter) -> web.Application:
    app = web.Application()
    app.router.add_get("/v1/capabilities", adapter._handle_capabilities)
    app.router.add_get("/api/sessions", adapter._handle_list_sessions)
    app.router.add_post("/api/sessions", adapter._handle_create_session)
    app.router.add_get("/api/sessions/{session_id}", adapter._handle_get_session)
    app.router.add_patch("/api/sessions/{session_id}", adapter._handle_patch_session)
    app.router.add_delete("/api/sessions/{session_id}", adapter._handle_delete_session)
    app.router.add_get("/api/sessions/{session_id}/messages", adapter._handle_session_messages)
    app.router.add_post("/api/sessions/{session_id}/fork", adapter._handle_fork_session)
    app.router.add_post("/api/sessions/{session_id}/chat", adapter._handle_session_chat)
    app.router.add_post("/api/sessions/{session_id}/chat/stream", adapter._handle_session_chat_stream)
    return app


def _write_cortex_config(home, *, provider="cortex", enabled=True):
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        f"""
memory:
  provider: {provider}
cortex:
  enabled: {str(enabled).lower()}
  storage:
    backend: sqlite
    path: cortex/cortex.db
  capture:
    enabled: true
  dream:
    enabled: false
  graphrag:
    enabled: false
""".lstrip(),
        encoding="utf-8",
    )


def _seed_cortex_turn(home, session_id):
    store, _ = open_cortex_store(home, {})
    store.ensure_session(session_id)
    evidence_id = store.append_evidence(
        session_id,
        EvidenceInput(
            source_type="user_message",
            source_locator=f"{session_id}:turn:1:user",
            content="remember the customer prefers concise status updates",
        ),
    )
    store.add_observation(
        session_id=session_id,
        kind="preference",
        text="The customer prefers concise status updates",
        evidence_ids=[evidence_id],
        processing_state="pending",
    )
    return store


@pytest.mark.asyncio
async def test_capabilities_advertises_session_control_surface(adapter):
    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        resp = await cli.get("/v1/capabilities")
        assert resp.status == 200
        data = await resp.json()

    features = data["features"]
    assert features["session_resources"] is True
    assert features["session_chat"] is True
    assert features["session_chat_streaming"] is True
    assert features["session_fork"] is True
    assert features["admin_config_rw"] is False
    assert features["memory_write_api"] is False
    assert features["skills_api"] is True
    assert features["realtime_voice"] is False
    assert data["endpoints"]["sessions"] == {"method": "GET", "path": "/api/sessions"}
    assert data["endpoints"]["session_chat_stream"] == {
        "method": "POST",
        "path": "/api/sessions/{session_id}/chat/stream",
    }


@pytest.mark.asyncio
async def test_run_agent_binds_api_session_context_for_tool_env(adapter, monkeypatch):
    """API-server request sessions should reach tools and terminal subprocess env."""
    monkeypatch.setenv("HERMES_SESSION_ID", "stale-session")
    observed = {}

    class FakeAgent:
        session_prompt_tokens = 0
        session_completion_tokens = 0
        session_total_tokens = 0

        def __init__(self, session_id: str):
            self.session_id = session_id

        def run_conversation(self, user_message, conversation_history, task_id):
            from gateway.session_context import get_session_env
            from tools.environments.local import _make_run_env

            observed["task_id"] = task_id
            observed["context_session_id"] = get_session_env("HERMES_SESSION_ID")
            observed["context_platform"] = get_session_env("HERMES_SESSION_PLATFORM")
            observed["context_session_key"] = get_session_env("HERMES_SESSION_KEY")
            observed["child_session_id"] = _make_run_env({}).get("HERMES_SESSION_ID")
            return {"final_response": "ok"}

    def fake_create_agent(**kwargs):
        return FakeAgent(kwargs["session_id"])

    monkeypatch.setattr(adapter, "_create_agent", fake_create_agent)

    result, usage = await adapter._run_agent(
        user_message="hello",
        conversation_history=[],
        session_id="request-session",
        gateway_session_key="request-key",
    )

    assert result["session_id"] == "request-session"
    assert usage == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    assert observed == {
        "task_id": "request-session",
        "context_session_id": "request-session",
        "context_platform": "api_server",
        "context_session_key": "request-key",
        "child_session_id": "request-session",
    }


@pytest.mark.asyncio
async def test_session_crud_and_message_history(adapter, session_db):
    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        create_resp = await cli.post("/api/sessions", json={"title": "Mobile chat", "model": "test-model"})
        assert create_resp.status == 201
        created = await create_resp.json()
        session_id = created["session"]["id"]
        assert created["object"] == "hermes.session"
        assert created["session"]["title"] == "Mobile chat"

        session_db.append_message(session_id, "user", "hello from phone")
        session_db.append_message(session_id, "assistant", "hello from hermes")

        list_resp = await cli.get("/api/sessions?limit=10&offset=0")
        assert list_resp.status == 200
        listed = await list_resp.json()
        assert listed["object"] == "list"
        assert [s["id"] for s in listed["data"]] == [session_id]
        assert listed["data"][0]["message_count"] == 2

        get_resp = await cli.get(f"/api/sessions/{session_id}")
        assert get_resp.status == 200
        got = await get_resp.json()
        assert got["session"]["id"] == session_id
        assert got["session"]["message_count"] == 2

        messages_resp = await cli.get(f"/api/sessions/{session_id}/messages")
        assert messages_resp.status == 200
        messages = await messages_resp.json()
        assert messages["object"] == "list"
        assert [m["role"] for m in messages["data"]] == ["user", "assistant"]
        assert messages["data"][0]["content"] == "hello from phone"

        patch_resp = await cli.patch(f"/api/sessions/{session_id}", json={"title": "Renamed"})
        assert patch_resp.status == 200
        patched = await patch_resp.json()
        assert patched["session"]["title"] == "Renamed"

        delete_resp = await cli.delete(f"/api/sessions/{session_id}")
        assert delete_resp.status == 200
        deleted = await delete_resp.json()
        assert deleted == {"object": "hermes.session.deleted", "id": session_id, "deleted": True}
        assert session_db.get_session(session_id) is None


@pytest.mark.asyncio
async def test_patch_end_owns_cortex_admission_before_state_db(
    adapter, session_db, tmp_path, monkeypatch
):
    home = tmp_path / "atlas-profile"
    _write_cortex_config(home)
    session_id = session_db.create_session("cortex-end", "api_server")
    session_db.append_message(session_id, "user", "remember my preference")
    session_db.append_message(session_id, "assistant", "I will remember it")
    store = _seed_cortex_turn(home, session_id)
    monkeypatch.setattr(adapter, "_cortex_profile_home", lambda: home)

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        response = await cli.patch(
            f"/api/sessions/{session_id}",
            json={"end_reason": "customer_closed"},
        )
        assert response.status == 200, await response.text()

    state_session = session_db.get_session(session_id)
    assert state_session["end_reason"] == "customer_closed"
    assert store.session_lineage(session_id)["state"] == "finalized"
    with store.connect() as connection:
        admission_count = connection.execute(
            "SELECT COUNT(*) FROM session_distill_admissions WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()[0]
        root_count = connection.execute(
            "SELECT COUNT(*) FROM cognitive_jobs WHERE brain_id=? "
            "AND job_type='session_distill' AND parent_job_id IS NULL",
            (store.brain_id,),
        ).fetchone()[0]
    assert admission_count == 1
    assert root_count == 1


@pytest.mark.asyncio
async def test_patch_end_cortex_failure_leaves_all_state_db_fields_unchanged(
    adapter, session_db, monkeypatch
):
    session_id = session_db.create_session("end-failure", "api_server")
    session_db.set_session_title(session_id, "Original")
    session_db.append_message(session_id, "user", "durable content")
    boundary = AsyncMock(side_effect=RuntimeError("disk unavailable"))
    monkeypatch.setattr(adapter, "_commit_cortex_session_boundary", boundary)

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        response = await cli.patch(
            f"/api/sessions/{session_id}",
            json={"title": "Must not publish", "end_reason": "customer_closed"},
        )
        payload = await response.json()

    assert response.status == 503
    assert payload["error"]["code"] == "cortex_session_boundary_failed"
    unchanged = session_db.get_session(session_id)
    assert unchanged["title"] == "Original"
    assert unchanged["ended_at"] is None
    assert unchanged["end_reason"] is None


@pytest.mark.asyncio
async def test_patch_end_fails_closed_when_required_cortex_evidence_is_missing(
    adapter, session_db, tmp_path, monkeypatch
):
    home = tmp_path / "missing-evidence-profile"
    _write_cortex_config(home)
    session_id = session_db.create_session("missing-evidence", "api_server")
    session_db.append_message(session_id, "user", "retainable customer fact")
    store, _ = open_cortex_store(home, {})
    store.ensure_session(session_id)
    monkeypatch.setattr(adapter, "_cortex_profile_home", lambda: home)

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        response = await cli.patch(
            f"/api/sessions/{session_id}",
            json={"end_reason": "customer_closed"},
        )
        payload = await response.json()

    assert response.status == 503
    assert payload["error"]["code"] == "cortex_session_boundary_failed"
    assert session_db.get_session(session_id)["ended_at"] is None
    assert store.session_lineage(session_id)["state"] == "active"


@pytest.mark.asyncio
async def test_patch_end_allows_intentionally_unretained_session_without_job(
    adapter, session_db, tmp_path, monkeypatch
):
    home = tmp_path / "no-retention-profile"
    _write_cortex_config(home)
    session_id = session_db.create_session("no-retention", "api_server")
    session_db.append_message(
        session_id,
        "user",
        "Could you please not store anything from this conversation?",
    )
    session_db.append_message(session_id, "assistant", "I will not retain it.")
    store, _ = open_cortex_store(home, {})
    store.ensure_session(session_id)
    monkeypatch.setattr(adapter, "_cortex_profile_home", lambda: home)

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        response = await cli.patch(
            f"/api/sessions/{session_id}",
            json={"end_reason": "customer_closed"},
        )

    assert response.status == 200
    assert session_db.get_session(session_id)["end_reason"] == "customer_closed"
    assert store.session_lineage(session_id)["state"] == "finalized"
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM session_distill_admissions WHERE brain_id=?",
                (store.brain_id,),
            ).fetchone()[0]
            == 0
        )


@pytest.mark.asyncio
async def test_patch_end_rejects_an_exact_inflight_api_turn(
    adapter, session_db, monkeypatch
):
    session_id = session_db.create_session("active-session", "api_server")
    started = threading.Event()
    release = threading.Event()

    class BlockingAgent:
        session_prompt_tokens = 0
        session_completion_tokens = 0
        session_total_tokens = 0

        def __init__(self):
            self.session_id = session_id

        def run_conversation(self, **_kwargs):
            started.set()
            assert release.wait(timeout=5)
            return {"final_response": "done"}

    monkeypatch.setattr(adapter, "_create_agent", lambda **_kwargs: BlockingAgent())
    boundary = AsyncMock(return_value=True)
    monkeypatch.setattr(adapter, "_commit_cortex_session_boundary", boundary)
    run_task = asyncio.create_task(
        adapter._run_agent(
            user_message="still working",
            conversation_history=[],
            session_id=session_id,
        )
    )
    assert await asyncio.to_thread(started.wait, 2)

    try:
        app = _create_session_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            response = await cli.patch(
                f"/api/sessions/{session_id}",
                json={"end_reason": "customer_closed"},
            )
            payload = await response.json()
            delete_response = await cli.delete(f"/api/sessions/{session_id}")
            delete_payload = await delete_response.json()
        assert response.status == 409
        assert payload["error"]["code"] == "session_active"
        assert delete_response.status == 409
        assert delete_payload["error"]["code"] == "session_active"
        assert session_db.get_session(session_id)["ended_at"] is None
        boundary.assert_not_awaited()
    finally:
        release.set()
        await run_task


@pytest.mark.asyncio
async def test_cancelled_api_wrapper_stays_active_until_worker_thread_exits(
    adapter, session_db, monkeypatch
):
    session_id = session_db.create_session("cancelled-wrapper", "api_server")
    started = threading.Event()
    release = threading.Event()

    class BlockingAgent:
        session_prompt_tokens = 0
        session_completion_tokens = 0
        session_total_tokens = 0

        def __init__(self):
            self.session_id = session_id

        def run_conversation(self, **_kwargs):
            started.set()
            assert release.wait(timeout=5)
            return {"final_response": "done"}

    monkeypatch.setattr(adapter, "_create_agent", lambda **_kwargs: BlockingAgent())
    run_task = asyncio.create_task(
        adapter._run_agent(
            user_message="still writing",
            conversation_history=[],
            session_id=session_id,
        )
    )
    assert await asyncio.to_thread(started.wait, 2)
    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task

    assert adapter._session_has_active_api_request(session_id) is True
    try:
        app = _create_session_app(adapter)
        async with TestClient(TestServer(app)) as cli:
            response = await cli.patch(
                f"/api/sessions/{session_id}",
                json={"end_reason": "customer_closed"},
            )
            payload = await response.json()
        assert response.status == 409
        assert payload["error"]["code"] == "session_active"
    finally:
        release.set()

    for _ in range(50):
        if not adapter._session_has_active_api_request(session_id):
            break
        await asyncio.sleep(0.01)
    assert adapter._session_has_active_api_request(session_id) is False


@pytest.mark.asyncio
async def test_patch_end_barrier_rejects_a_new_turn_during_cortex_commit(
    adapter, session_db, monkeypatch
):
    session_id = session_db.create_session("boundary-race", "api_server")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_boundary(*_args, **_kwargs):
        entered.set()
        await release.wait()
        return False

    monkeypatch.setattr(adapter, "_commit_cortex_session_boundary", slow_boundary)
    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        patch_task = asyncio.create_task(
            cli.patch(
                f"/api/sessions/{session_id}",
                json={"end_reason": "customer_closed"},
            )
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        with pytest.raises(RuntimeError, match="lifecycle mutation in progress"):
            await adapter._run_agent(
                user_message="must not race",
                conversation_history=[],
                session_id=session_id,
            )
        release.set()
        response = await patch_task

    assert response.status == 200
    assert session_id not in adapter._session_lifecycle_mutations


@pytest.mark.asyncio
async def test_patch_end_preserves_non_cortex_profile_compatibility(
    adapter, session_db, tmp_path, monkeypatch
):
    home = tmp_path / "non-cortex-profile"
    _write_cortex_config(home, provider="", enabled=False)
    monkeypatch.setattr(adapter, "_cortex_profile_home", lambda: home)
    session_id = session_db.create_session("legacy-end", "api_server")
    session_db.append_message(session_id, "user", "legacy transcript")

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        response = await cli.patch(
            f"/api/sessions/{session_id}",
            json={"end_reason": "customer_closed"},
        )

    assert response.status == 200
    assert session_db.get_session(session_id)["end_reason"] == "customer_closed"
    assert not (home / "cortex" / "cortex.db").exists()


@pytest.mark.asyncio
async def test_delete_does_not_distill_content_being_deleted(
    adapter, session_db, monkeypatch
):
    session_id = session_db.create_session("delete-without-distill", "api_server")
    session_db.append_message(session_id, "user", "private transcript")
    boundary = AsyncMock(return_value=True)
    monkeypatch.setattr(adapter, "_commit_cortex_session_boundary", boundary)

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        response = await cli.delete(f"/api/sessions/{session_id}")

    assert response.status == 200
    assert session_db.get_session(session_id) is None
    boundary.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_revokes_cortex_semantics_before_removing_transcript(
    adapter, session_db, tmp_path, monkeypatch
):
    home = tmp_path / "delete-profile"
    _write_cortex_config(home)
    session_id = session_db.create_session("privacy-delete", "api_server")
    session_db.append_message(session_id, "user", "private customer detail")
    store = _seed_cortex_turn(home, session_id)
    store.finalize_session(session_id)
    monkeypatch.setattr(adapter, "_cortex_profile_home", lambda: home)

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        response = await cli.delete(f"/api/sessions/{session_id}")

    assert response.status == 200, await response.text()
    assert session_db.get_session(session_id) is None
    assert store.session_lineage(session_id)["state"] == "deleted"
    with store.connect() as connection:
        assert connection.execute(
            "SELECT tombstoned_at FROM evidence_items WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()["tombstoned_at"]
        admission = connection.execute(
            "SELECT revoked_at FROM session_distill_admissions WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()
        job = connection.execute(
            "SELECT state FROM cognitive_jobs WHERE brain_id=? "
            "AND job_type='session_distill'",
            (store.brain_id,),
        ).fetchone()
    assert admission["revoked_at"]
    assert job["state"] == "dead_letter"


@pytest.mark.asyncio
async def test_delete_removes_same_full_compression_lineage_from_both_stores(
    adapter, session_db, tmp_path, monkeypatch
):
    home = tmp_path / "delete-lineage-profile"
    _write_cortex_config(home)
    session_db.create_session("delete-root", "api_server")
    session_db.end_session("delete-root", "compression")
    session_db.create_session(
        "delete-tip", "api_server", parent_session_id="delete-root"
    )
    store, _ = open_cortex_store(home, {})
    store.ensure_session("delete-root")
    store.ensure_session(
        "delete-tip",
        parent_session_id="delete-root",
        logical_conversation_id="delete-root",
    )
    monkeypatch.setattr(adapter, "_cortex_profile_home", lambda: home)

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        response = await cli.delete("/api/sessions/delete-tip")

    assert response.status == 200, await response.text()
    assert session_db.get_session("delete-root") is None
    assert session_db.get_session("delete-tip") is None
    assert store.session_lineage("delete-root")["state"] == "deleted"
    assert store.session_lineage("delete-tip")["state"] == "deleted"


@pytest.mark.asyncio
async def test_delete_cortex_failure_leaves_sessiondb_unchanged(
    adapter, session_db, tmp_path, monkeypatch
):
    home = tmp_path / "delete-running-profile"
    _write_cortex_config(home)
    session_id = session_db.create_session("privacy-running", "api_server")
    session_db.append_message(session_id, "user", "must not race")
    store = _seed_cortex_turn(home, session_id)
    store.finalize_session(session_id)
    with store.transaction() as connection:
        connection.execute(
            "UPDATE cognitive_jobs SET state='running', lease_owner='worker', "
            "started_at=?, heartbeat_at=?, lease_expires_at=? "
            "WHERE brain_id=? AND job_type='session_distill'",
            (
                "2026-07-14T00:00:00Z",
                "2026-07-14T00:00:00Z",
                "2099-07-14T00:00:00Z",
                store.brain_id,
            ),
        )
    monkeypatch.setattr(adapter, "_cortex_profile_home", lambda: home)

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        response = await cli.delete(f"/api/sessions/{session_id}")
        payload = await response.json()

    assert response.status == 503
    assert payload["error"]["code"] == "cortex_session_delete_failed"
    assert session_db.get_session(session_id) is not None
    assert store.session_lineage(session_id)["state"] == "finalized"


@pytest.mark.asyncio
async def test_session_messages_follow_compression_tip(adapter, session_db):
    source_id = session_db.create_session("source-session", "api_server")
    session_db.append_message(source_id, "user", "before compression")
    session_db.end_session(source_id, "compression")
    session_db.create_session("tip-session", "api_server", parent_session_id=source_id)
    session_db.replace_messages(source_id, [])
    session_db.append_message("tip-session", "user", "after compression")

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        messages_resp = await cli.get(f"/api/sessions/{source_id}/messages")
        assert messages_resp.status == 200
        messages = await messages_resp.json()

    assert messages["object"] == "list"
    assert messages["session_id"] == "tip-session"
    assert [m["content"] for m in messages["data"]] == ["after compression"]


@pytest.mark.asyncio
async def test_session_fork_uses_current_sessiondb_branch_primitives(adapter, session_db):
    source_id = session_db.create_session("source-session", "api_server", model="test-model")
    session_db.set_session_title(source_id, "Original")
    session_db.append_message(source_id, "user", "first path")
    session_db.append_message(source_id, "assistant", "answer")

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        resp = await cli.post(f"/api/sessions/{source_id}/fork", json={"title": "Alternative"})
        assert resp.status == 201
        payload = await resp.json()

    fork = payload["session"]
    assert payload["object"] == "hermes.session"
    assert fork["id"] != source_id
    assert fork["parent_session_id"] == source_id
    assert fork["title"] == "Alternative"
    assert [m["content"] for m in session_db.get_messages(fork["id"])] == ["first path", "answer"]
    assert session_db.get_session(source_id)["end_reason"] is None
    assert session_db.get_session(source_id)["ended_at"] is None


@pytest.mark.asyncio
async def test_session_fork_prepares_cortex_child_without_ending_parent(
    adapter, session_db, tmp_path, monkeypatch
):
    home = tmp_path / "atlas-fork-profile"
    _write_cortex_config(home)
    source_id = session_db.create_session("cortex-parent", "api_server")
    session_db.append_message(source_id, "user", "take the alternate path")
    session_db.append_message(source_id, "assistant", "ready")
    store = _seed_cortex_turn(home, source_id)
    monkeypatch.setattr(adapter, "_cortex_profile_home", lambda: home)

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        response = await cli.post(
            f"/api/sessions/{source_id}/fork",
            json={"id": "cortex-child", "title": "Alternate"},
        )
        assert response.status == 201, await response.text()

    assert session_db.get_session(source_id)["end_reason"] is None
    assert session_db.get_session(source_id)["ended_at"] is None
    state_child = session_db.get_session("cortex-child")
    assert state_child["parent_session_id"] == source_id
    cortex_parent = store.session_lineage(source_id)
    cortex_child = store.session_lineage("cortex-child")
    assert cortex_parent["state"] == "active"
    assert cortex_child["state"] == "active"
    assert cortex_child["parent_session_id"] == source_id
    with store.connect() as connection:
        admission_count = connection.execute(
            "SELECT COUNT(*) FROM session_distill_admissions WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()[0]
    assert admission_count == 0


@pytest.mark.asyncio
async def test_session_fork_cortex_failure_does_not_end_or_create_state_rows(
    adapter, session_db, monkeypatch
):
    source_id = session_db.create_session("fork-failure-parent", "api_server")
    session_db.append_message(source_id, "user", "source content")
    boundary = AsyncMock(side_effect=RuntimeError("cortex write failed"))
    monkeypatch.setattr(adapter, "_commit_cortex_session_boundary", boundary)

    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        response = await cli.post(
            f"/api/sessions/{source_id}/fork",
            json={"id": "must-not-exist", "title": "Not published"},
        )
        payload = await response.json()

    assert response.status == 503
    assert payload["error"]["code"] == "cortex_session_boundary_failed"
    assert session_db.get_session(source_id)["ended_at"] is None
    assert session_db.get_session("must-not-exist") is None


@pytest.mark.asyncio
async def test_session_fork_title_race_compensates_child_and_retry_reuses_cortex(
    adapter, session_db, tmp_path, monkeypatch
):
    home = tmp_path / "atlas-fork-retry-profile"
    _write_cortex_config(home)
    source_id = session_db.create_session("retry-parent", "api_server")
    session_db.append_message(source_id, "user", "durable branch evidence")
    store = _seed_cortex_turn(home, source_id)
    monkeypatch.setattr(adapter, "_cortex_profile_home", lambda: home)

    real_set_title = session_db.set_session_title
    failed_once = False

    def fail_first_child_title(session_id, title):
        nonlocal failed_once
        if session_id == "retry-child" and not failed_once:
            failed_once = True
            raise ValueError("Title was concurrently claimed")
        return real_set_title(session_id, title)

    monkeypatch.setattr(session_db, "set_session_title", fail_first_child_title)
    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        first = await cli.post(
            f"/api/sessions/{source_id}/fork",
            json={"id": "retry-child", "title": "Retry branch"},
        )
        first_payload = await first.json()
        assert first.status == 409
        assert first_payload["error"]["code"] == "session_title_conflict"
        assert session_db.get_session(source_id)["ended_at"] is None
        assert session_db.get_session("retry-child") is None
        assert store.session_lineage("retry-child")["parent_session_id"] == source_id

        second = await cli.post(
            f"/api/sessions/{source_id}/fork",
            json={"id": "retry-child", "title": "Retry branch"},
        )
        assert second.status == 201, await second.text()

    assert session_db.get_session(source_id)["end_reason"] is None
    assert session_db.get_session(source_id)["ended_at"] is None
    assert session_db.get_session("retry-child")["parent_session_id"] == source_id
    with store.connect() as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM session_distill_admissions WHERE brain_id=?",
                (store.brain_id,),
            ).fetchone()[0]
            == 0
        )


@pytest.mark.asyncio
async def test_session_chat_loads_history_and_preserves_session_headers(auth_adapter, session_db):
    session_id = session_db.create_session("chat-session", "api_server")
    session_db.set_session_title(session_id, "Chat")
    session_db.append_message(session_id, "user", "earlier")
    session_db.append_message(session_id, "assistant", "prior answer")

    mock_run = AsyncMock(return_value=({"final_response": "fresh answer", "session_id": session_id}, {"total_tokens": 3}))
    app = _create_session_app(auth_adapter)
    with patch.object(auth_adapter, "_run_agent", mock_run):
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                f"/api/sessions/{session_id}/chat",
                json={"message": "next", "system_message": "stay focused"},
                headers={"Authorization": "Bearer sk-test", "X-Hermes-Session-Key": "client-42"},
            )
            assert resp.status == 200
            payload = await resp.json()

    assert resp.headers["X-Hermes-Session-Id"] == session_id
    assert resp.headers["X-Hermes-Session-Key"] == "client-42"
    assert payload["object"] == "hermes.session.chat.completion"
    assert payload["session_id"] == session_id
    assert payload["message"]["role"] == "assistant"
    assert payload["message"]["content"] == "fresh answer"
    mock_run.assert_awaited_once()
    _, kwargs = mock_run.call_args
    assert kwargs["session_id"] == session_id
    assert kwargs["gateway_session_key"] == "client-42"
    assert kwargs["ephemeral_system_prompt"] == "stay focused"
    history = kwargs["conversation_history"]
    assert len(history) == 2
    assert isinstance(history[0].pop("timestamp"), (int, float))
    assert isinstance(history[1].pop("timestamp"), (int, float))
    assert history == [
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "prior answer"},
    ]


@pytest.mark.asyncio
async def test_session_chat_accepts_multimodal_message(auth_adapter, session_db):
    session_id = session_db.create_session("image-session", "api_server")
    image_payload = [
        {"type": "input_text", "text": "What's in this image?"},
        {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
    ]
    expected_user_message = [
        {"type": "text", "text": "What's in this image?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]

    mock_run = AsyncMock(return_value=({"final_response": "A cat.", "session_id": session_id}, {"total_tokens": 4}))
    app = _create_session_app(auth_adapter)
    with patch.object(auth_adapter, "_run_agent", mock_run):
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                f"/api/sessions/{session_id}/chat",
                json={"message": image_payload},
                headers={"Authorization": "Bearer sk-test"},
            )
            assert resp.status == 200, await resp.text()

    _, kwargs = mock_run.call_args
    assert kwargs["user_message"] == expected_user_message


@pytest.mark.asyncio
async def test_session_chat_stream_accepts_multimodal_message(adapter, session_db):
    session_id = session_db.create_session("image-stream-session", "api_server")
    image_payload = [
        {"type": "input_text", "text": "What's in this image?"},
        {"type": "input_image", "image_url": "data:image/png;base64,AAAA"},
    ]
    expected_user_message = [
        {"type": "text", "text": "What's in this image?"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
    ]
    captured_kwargs = {}

    async def fake_run(**kwargs):
        captured_kwargs.update(kwargs)
        kwargs["stream_delta_callback"]("A cat.")
        return {"final_response": "A cat.", "session_id": session_id}, {"total_tokens": 4}

    app = _create_session_app(adapter)
    with patch.object(adapter, "_run_agent", side_effect=fake_run):
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                f"/api/sessions/{session_id}/chat/stream",
                json={"message": image_payload},
            )
            assert resp.status == 200, await resp.text()
            assert resp.headers["Content-Type"].startswith("text/event-stream")
            body = await resp.text()

    assert "event: assistant.completed" in body
    assert captured_kwargs["user_message"] == expected_user_message


@pytest.mark.asyncio
async def test_session_chat_stream_emits_lifecycle_events_and_keepalive_safe_shape(adapter, session_db):
    session_id = session_db.create_session("stream-session", "api_server")
    session_db.set_session_title(session_id, "Stream")

    async def fake_run(**kwargs):
        kwargs["stream_delta_callback"]("Hello")
        kwargs["stream_delta_callback"](" world")
        kwargs["tool_progress_callback"]("reasoning.available", tool_name="_thinking", preview="thinking")
        return {"final_response": "Hello world", "session_id": session_id}, {"total_tokens": 2}

    app = _create_session_app(adapter)
    with patch.object(adapter, "_run_agent", side_effect=fake_run):
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(f"/api/sessions/{session_id}/chat/stream", json={"message": "stream please"})
            assert resp.status == 200
            assert resp.headers["Content-Type"].startswith("text/event-stream")
            body = await resp.text()

    assert "event: run.started" in body
    assert "event: message.started" in body
    assert "event: assistant.delta" in body
    assert "Hello world" in body
    assert "event: tool.progress" in body
    assert "event: assistant.completed" in body
    assert "event: run.completed" in body
    assert "event: done" in body


@pytest.mark.asyncio
async def test_session_chat_stream_run_completed_carries_turn_transcript(adapter, session_db):
    """run.completed must include the full interleaved turn transcript so a
    client that lost intermediate (pre-tool-call) assistant text from the live
    delta stream can reconcile without a separate /messages fetch. Refs #34703.
    """
    import json as _json

    session_id = session_db.create_session("transcript-session", "api_server")

    async def fake_run(**kwargs):
        # Stream the intermediate planning text the way a real turn would.
        kwargs["stream_delta_callback"]("Let me search for that:")
        kwargs["stream_delta_callback"]("Here is the summary.")
        result = {
            "final_response": "Here is the summary.",
            "session_id": session_id,
            "messages": [
                {"role": "user", "content": "search then summarize"},
                {
                    "role": "assistant",
                    "content": "Let me search for that:",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "web_search", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "content": "results", "tool_call_id": "call_1", "tool_name": "web_search"},
                {"role": "assistant", "content": "Here is the summary."},
            ],
        }
        return result, {"total_tokens": 6}

    app = _create_session_app(adapter)
    with patch.object(adapter, "_run_agent", side_effect=fake_run):
        async with TestClient(TestServer(app)) as cli:
            resp = await cli.post(
                f"/api/sessions/{session_id}/chat/stream",
                json={"message": "search then summarize"},
            )
            assert resp.status == 200
            body = await resp.text()

    # Pull the run.completed event payload out of the SSE body.
    run_completed_payload = None
    for block in body.split("\n\n"):
        if "event: run.completed" in block:
            for line in block.splitlines():
                if line.startswith("data: "):
                    run_completed_payload = _json.loads(line[len("data: "):])
            break
    assert run_completed_payload is not None, body
    messages = run_completed_payload.get("messages")
    assert isinstance(messages, list) and messages, run_completed_payload

    # The colon-ended intermediate text that preceded the tool call must be present.
    contents = [m.get("content") for m in messages]
    assert "Let me search for that:" in contents
    assert "Here is the summary." in contents
    # No prior-turn user message should leak into the per-turn slice.
    assert all(m.get("role") in ("assistant", "tool") for m in messages)
    # The tool call is preserved alongside the intermediate text.
    assert any(m.get("tool_calls") for m in messages)



@pytest.mark.asyncio
async def test_session_endpoints_require_auth_when_key_configured(auth_adapter):
    app = _create_session_app(auth_adapter)
    async with TestClient(TestServer(app)) as cli:
        resp = await cli.get("/api/sessions")
        assert resp.status == 401
        body = await resp.json()
        assert body["error"]["code"] == "invalid_api_key"

        ok = await cli.get("/api/sessions", headers={"Authorization": "Bearer sk-test"})
        assert ok.status == 200
        data = await ok.json()
        assert data["object"] == "list"
        assert data["data"] == []


@pytest.mark.asyncio
async def test_session_header_rejected_without_api_key(adapter, session_db):
    session_id = session_db.create_session("unsafe-session", "api_server")
    app = _create_session_app(adapter)
    async with TestClient(TestServer(app)) as cli:
        resp = await cli.post(
            f"/api/sessions/{session_id}/chat",
            json={"message": "hello"},
            headers={"X-Hermes-Session-Key": "client-42"},
        )
        assert resp.status == 403
        data = await resp.json()
        assert "X-Hermes-Session-Key requires API key" in data["error"]["message"]
