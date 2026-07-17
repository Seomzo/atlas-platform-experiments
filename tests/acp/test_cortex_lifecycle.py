"""Focused ACP logical-session lifecycle coverage for Atlas Cortex."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from acp_adapter.session import SessionManager
from hermes_state import SessionDB


class RecordingMemoryManager:
    def __init__(self, events: list, *, fail_commit: bool = False):
        self.events = events
        self.fail_commit = fail_commit

    def commit_session_boundary_async(self, messages, **kwargs):
        self.events.append(("commit", list(messages), dict(kwargs)))
        if self.fail_commit:
            raise RuntimeError("cortex unavailable")

    def on_session_switch(self, new_session_id, **kwargs):
        self.events.append(("switch", new_session_id, dict(kwargs)))

    def on_session_finalize(self, messages, **kwargs):
        self.events.append(("finalize", list(messages), dict(kwargs)))

    def flush_pending(self, timeout):
        self.events.append(("flush", timeout))

    def shutdown_all(self):
        self.events.append(("manager_shutdown",))


class RecordingAgent:
    def __init__(
        self,
        events: list,
        *,
        manager: RecordingMemoryManager | None = None,
        model: str = "model-a",
    ):
        self.events = events
        self._memory_manager = manager
        self._cortex_memory_selected = manager is not None
        self.model = model
        self.provider = "altas"
        self.session_id = ""
        self._session_messages = []
        self._end_session_on_close = True

    def commit_memory_session(self, messages, *, checkpoint_memory=True):
        self.events.append(("context_end", list(messages), checkpoint_memory))

    def reset_session_state(self):
        self.events.append(("agent_reset", self.session_id))

    def shutdown_memory_provider(self, messages, *, finalize=False):
        self.events.append(("shutdown", list(messages), finalize))

    def close(self):
        self.events.append(("close", self._end_session_on_close))


def test_reset_commits_cortex_before_rebinding_and_clearing(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    events: list = []
    memory = RecordingMemoryManager(events)
    agent = RecordingAgent(events, manager=memory)
    manager = SessionManager(agent_factory=lambda: agent, db=db)
    state = manager.create_session(cwd="/work")
    old_id = state.session_id
    state.history = [{"role": "user", "content": "remember this"}]

    reset = manager.reset_session(state.session_id)

    assert reset is state
    commit = next(event for event in events if event[0] == "commit")
    assert commit[1] == [{"role": "user", "content": "remember this"}]
    assert commit[2]["reason"] == "reset"
    assert commit[2]["parent_session_id"] == old_id
    assert commit[2]["new_session_id"] == state.current_session_id
    assert state.current_session_id != old_id
    assert state.agent.session_id == state.current_session_id
    assert state.history == []
    assert events.index(commit) < next(
        idx for idx, event in enumerate(events) if event[0] == "agent_reset"
    )

    child = db.get_session(state.current_session_id)
    assert child["source"] == "acp_internal"
    assert child["parent_session_id"] == old_id
    route_meta = json.loads(db.get_session(old_id)["model_config"])
    assert route_meta["current_session_id"] == state.current_session_id


def test_reset_boundary_failure_leaves_old_session_unchanged(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    events: list = []
    memory = RecordingMemoryManager(events, fail_commit=True)
    agent = RecordingAgent(events, manager=memory)
    manager = SessionManager(agent_factory=lambda: agent, db=db)
    state = manager.create_session()
    old_id = state.session_id
    state.history = [{"role": "user", "content": "still live"}]

    with pytest.raises(RuntimeError, match="cortex unavailable"):
        manager.reset_session(state.session_id)

    new_id = next(event[2]["new_session_id"] for event in events if event[0] == "commit")
    assert state.current_session_id == old_id
    assert state.agent.session_id == old_id
    assert state.history == [{"role": "user", "content": "still live"}]
    assert db.get_session(new_id) is None


def test_fork_prepares_parent_lineage_without_finalizing_source(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    events: list = []
    memory = RecordingMemoryManager(events)
    parent = RecordingAgent(events, manager=memory)
    child = RecordingAgent(events, manager=RecordingMemoryManager(events))
    agents = iter([parent, child])
    manager = SessionManager(agent_factory=lambda: next(agents), db=db)
    source = manager.create_session()
    source.history = [{"role": "user", "content": "fork context"}]

    forked = manager.fork_session(source.session_id, cwd="/fork")

    assert forked is not None
    assert source.agent.session_id == source.session_id
    assert [event[0] for event in events].count("finalize") == 0
    switches = [event for event in events if event[0] == "switch"]
    assert switches == [
        (
            "switch",
            forked.session_id,
            {
                "parent_session_id": source.session_id,
                "reset": False,
                "reason": "branch",
            },
        ),
        (
            "switch",
            source.session_id,
            {"parent_session_id": "", "reset": False, "reason": "resume"},
        ),
    ]
    assert db.get_session(forked.session_id)["parent_session_id"] == source.session_id
    assert forked.history == source.history


def test_failed_fork_is_not_published_and_compensates_db(tmp_path, monkeypatch):
    db = SessionDB(tmp_path / "state.db")
    events: list = []
    parent = RecordingAgent(events, manager=RecordingMemoryManager(events))
    child = RecordingAgent(events, manager=RecordingMemoryManager(events))
    agents = iter([parent, child])
    manager = SessionManager(agent_factory=lambda: next(agents), db=db)
    source = manager.create_session()
    original_persist = manager._persist
    child_id: list[str] = []

    def fail_child_persist(state, *, raise_on_error=False):
        if state.session_id != source.session_id:
            child_id.append(state.session_id)
            raise RuntimeError("copy failed")
        return original_persist(state, raise_on_error=raise_on_error)

    monkeypatch.setattr(manager, "_persist", fail_child_persist)
    discard = MagicMock(return_value=True)
    monkeypatch.setattr(manager, "_discard_detached_branch", discard)

    assert manager.fork_session(source.session_id) is None
    assert child_id
    discard.assert_called_once_with(
        source.session_id,
        new_session_id=child_id[0],
    )
    assert manager.get_session(child_id[0]) is None
    assert db.get_session(child_id[0]) is None
    assert ("close", False) in events
    assert not any(event[0] == "finalize" for event in events)


def test_failed_cortex_fork_discards_child_without_distill_admission(
    tmp_path, monkeypatch
):
    from altas.cortex.config import CortexConfig
    from altas.cortex.store import CortexStore

    home = tmp_path / "profile"
    home.mkdir()
    config = CortexConfig.from_mapping(
        {
            "cortex": {
                "enabled": True,
                "storage": {"path": "cortex/cortex.db"},
                "capture": {"enabled": True},
            }
        },
        home,
    )
    store = CortexStore(
        config.database_path,
        owner_customer_id="customer-acp",
        redact_secrets=False,
    )
    store.initialize()

    db = SessionDB(tmp_path / "state.db")
    events: list = []
    parent_memory = RecordingMemoryManager(events)
    parent = RecordingAgent(events, manager=parent_memory)
    child = RecordingAgent(events, manager=RecordingMemoryManager(events))
    agents = iter([parent, child])
    manager = SessionManager(agent_factory=lambda: next(agents), db=db)
    source = manager.create_session()
    store.ensure_session(source.session_id)

    def switch_with_store(new_session_id, **kwargs):
        events.append(("switch", new_session_id, dict(kwargs)))
        if kwargs.get("reason") == "branch":
            store.ensure_session(
                new_session_id,
                parent_session_id=kwargs["parent_session_id"],
                logical_conversation_id=new_session_id,
            )
        elif kwargs.get("reason") == "resume":
            store.reopen_session(new_session_id)

    parent_memory.on_session_switch = switch_with_store
    child_id: list[str] = []
    original_persist = manager._persist

    def fail_child_persist(state, *, raise_on_error=False):
        if state.session_id != source.session_id:
            child_id.append(state.session_id)
            raise RuntimeError("publication failed")
        return original_persist(state, raise_on_error=raise_on_error)

    monkeypatch.setattr(manager, "_persist", fail_child_persist)
    monkeypatch.setattr(
        "altas.cortex.lifecycle.CortexConfig.load",
        lambda _home: config,
    )
    monkeypatch.setattr(
        "altas.cortex.lifecycle.open_cortex_store",
        lambda _home, _identity: (store, config),
    )

    assert manager.fork_session(source.session_id) is None
    assert child_id
    assert store.session_lineage(source.session_id)["state"] == "active"
    assert store.session_lineage(child_id[0])["state"] == "deleted"
    with store.connect() as connection:
        admissions = connection.execute(
            "SELECT COUNT(*) AS n FROM session_distill_admissions"
        ).fetchone()["n"]
        jobs = connection.execute(
            "SELECT COUNT(*) AS n FROM cognitive_jobs "
            "WHERE job_type='session_distill'"
        ).fetchone()["n"]
    assert admissions == 0
    assert jobs == 0
    assert not any(event[0] == "finalize" for event in events)


def test_model_replacement_closes_old_agent_without_ending_session(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    old_events: list = []
    new_events: list = []
    old = RecordingAgent(old_events, manager=None, model="old")
    new = RecordingAgent(new_events, manager=None, model="new")
    agents = iter([old, new])
    manager = SessionManager(agent_factory=lambda: next(agents), db=db)
    state = manager.create_session()
    state.history = [{"role": "user", "content": "same conversation"}]

    replaced = manager.replace_agent(state.session_id, model="new")

    assert replaced is state
    assert state.agent is new
    assert state.current_session_id == state.session_id
    assert ("shutdown", state.history, False) in old_events
    assert ("close", False) in old_events
    assert db.get_session(state.session_id)["ended_at"] is None


def test_remove_finalizes_before_nonfinalizing_close_and_preserves_row(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    events: list = []
    memory = RecordingMemoryManager(events)
    agent = RecordingAgent(events, manager=memory)
    manager = SessionManager(agent_factory=lambda: agent, db=db)
    state = manager.create_session()
    state.history = [{"role": "user", "content": "durable transcript"}]
    manager.save_session(state.session_id)

    assert manager.remove_session(state.session_id, reason="acp_close") is True

    names = [event[0] for event in events]
    assert names.count("finalize") == 1
    assert "commit" not in names
    assert "shutdown" not in names
    assert names.index("finalize") < names.index("manager_shutdown") < names.index("close")
    assert ("context_end", state.history, False) in events
    assert next(event for event in events if event[0] == "finalize")[2]["reason"] == "acp_close"
    row = db.get_session(state.session_id)
    assert row is not None
    assert row["end_reason"] == "acp_close"
    assert db.get_messages_as_conversation(state.session_id)[0]["content"] == "durable transcript"
    assert manager.get_session(state.session_id) is None


def test_process_cleanup_never_finalizes_or_ends_live_sessions(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    events: list = []
    memory = RecordingMemoryManager(events)
    agent = RecordingAgent(events, manager=memory)
    manager = SessionManager(agent_factory=lambda: agent, db=db)
    state = manager.create_session()
    state.history = [{"role": "user", "content": "resume me later"}]

    manager.cleanup()

    names = [event[0] for event in events]
    assert "finalize" not in names
    assert "commit" not in names
    assert "switch" not in names
    assert ("shutdown", state.history, False) in events
    assert ("close", False) in events
    row = db.get_session(state.session_id)
    assert row["ended_at"] is None
    assert row["end_reason"] is None
    assert manager._sessions == {}


def test_process_cleanup_does_not_touch_db_only_active_sessions(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    events: list = []
    agent = RecordingAgent(events, manager=RecordingMemoryManager(events))
    manager = SessionManager(agent_factory=lambda: agent, db=db)
    state = manager.create_session()
    state.history = [{"role": "user", "content": "detached and resumable"}]
    manager.save_session(state.session_id)
    with manager._lock:
        manager._sessions.pop(state.session_id)
    events.clear()

    manager.cleanup()

    assert events == []
    row = db.get_session(state.session_id)
    assert row["ended_at"] is None
    assert row["end_reason"] is None
    assert db.get_messages_as_conversation(state.session_id)[0]["content"] == (
        "detached and resumable"
    )


def test_restore_follows_compression_tip_even_when_public_root_is_ended(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    agents = iter([RecordingAgent([]), RecordingAgent([])])
    manager = SessionManager(agent_factory=lambda: next(agents), db=db)
    state = manager.create_session()
    root_id = state.session_id
    child_id = "compression-child"
    db.end_session(root_id, "compression")
    db.create_session(
        session_id=child_id,
        source="acp_internal",
        parent_session_id=root_id,
        model_config={"cwd": ".", "_acp_owner_session_id": root_id},
    )
    db.append_message(
        session_id=child_id,
        role="user",
        content="live continuation",
    )
    state.history = [{"role": "user", "content": "live continuation"}]
    state.agent.session_id = child_id
    manager.save_session(root_id)
    with manager._lock:
        manager._sessions.pop(root_id)

    restored = manager.get_session(root_id)

    assert restored is not None
    assert restored.current_session_id == child_id
    assert restored.history[0]["content"] == "live continuation"


def test_closed_rotated_head_cannot_be_restored_through_public_handle(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    events: list = []
    memory = RecordingMemoryManager(events)
    agent = RecordingAgent(events, manager=memory)
    manager = SessionManager(agent_factory=lambda: agent, db=db)
    state = manager.create_session()
    assert manager.reset_session(state.session_id) is state
    current_id = state.current_session_id

    assert manager.remove_session(state.session_id) is True
    assert db.get_session(current_id)["end_reason"] == "acp_close"
    assert manager.get_session(state.session_id) is None


def test_lifecycle_mutations_refuse_an_active_run(tmp_path):
    db = SessionDB(tmp_path / "state.db")
    events: list = []
    agent = RecordingAgent(events, manager=RecordingMemoryManager(events))
    manager = SessionManager(agent_factory=lambda: agent, db=db)
    state = manager.create_session()
    state.is_running = True

    assert manager.reset_session(state.session_id) is None
    assert manager.fork_session(state.session_id) is None
    assert manager.replace_agent(state.session_id, model="other") is None
    assert manager.remove_session(state.session_id) is False
    assert not any(event[0] in {"commit", "switch", "finalize"} for event in events)
