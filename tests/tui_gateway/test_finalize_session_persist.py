"""
Integration test: verify _finalize_session persists messages on force-quit.

Tests the fix for TUI sessions losing conversation history when the
user interrupts and exits before the agent thread finishes flushing.

Scenarios:
  1. Normal interrupt (single Ctrl+C) — messages already in session["history"]
  2. Force-quit mid-tool (double Ctrl+C) — session["history"] has previous turns
  3. Empty session — no-op, no crash
  4. Agent with _persist_session missing — graceful no-op
"""

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(history=None, session_id="test_session_001"):
    """Build a mock AIAgent with enough surface for _finalize_session."""
    agent = MagicMock()
    agent._persist_session = MagicMock()
    agent.commit_memory_session = MagicMock()
    agent.shutdown_memory_provider = MagicMock()
    agent.close = MagicMock()
    agent.session_id = session_id
    agent.model = "test-model"
    agent.platform = "tui"
    # _session_messages must be explicitly absent (None), otherwise
    # MagicMock auto-creates it and getattr returns a truthy mock.
    agent._session_messages = None
    return agent


def _make_session(agent=None, history=None, session_key="test_key_001"):
    return {
        "agent": agent,
        "history": history or [],
        "history_lock": threading.Lock(),
        "session_key": session_key,
        "_finalized": False,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFinalizeSessionPersist:
    """Verify _finalize_session flushes messages via _persist_session."""

    def test_persist_called_with_history(self):
        """History from session is passed to agent._persist_session.

        When _session_messages is None (not yet set by any turn),
        the session["history"] is used as the snapshot.
        """
        from tui_gateway.server import _finalize_session

        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        agent = _make_agent()
        session = _make_session(agent=agent, history=history)

        _finalize_session(session, end_reason="test")

        agent._persist_session.assert_called_once()
        # snapshot = history (since _session_messages is None)
        called_with = agent._persist_session.call_args[0][0]
        assert called_with == history
        # conversation_history kwarg passed for correct flush indexing
        assert (
            agent._persist_session.call_args[1].get("conversation_history") == history
        )

    def test_persist_uses_session_messages_when_available(self):
        """agent._session_messages takes priority over session['history']."""
        from tui_gateway.server import _finalize_session

        history = [{"role": "user", "content": "old"}]
        session_msgs = [
            {"role": "user", "content": "old"},
            {"role": "assistant", "content": "newer"},
        ]
        agent = _make_agent()
        agent._session_messages = session_msgs
        session = _make_session(agent=agent, history=history)

        _finalize_session(session)

        agent._persist_session.assert_called_once()
        called_with = agent._persist_session.call_args[0][0]
        assert called_with == session_msgs  # _session_messages wins
        assert (
            agent._persist_session.call_args[1].get("conversation_history") == history
        )

    def test_commit_memory_still_called(self):
        """Existing memory commit path is preserved."""
        from tui_gateway.server import _finalize_session

        history = [{"role": "user", "content": "x"}]
        agent = _make_agent()
        session = _make_session(agent=agent, history=history)

        _finalize_session(session)

        agent.commit_memory_session.assert_called_once()

    def test_manager_finalize_is_not_followed_by_durable_checkpoint(self):
        """Context cleanup must not mutate the recorded Cortex boundary epoch."""
        from tui_gateway.server import _finalize_session

        history = [{"role": "user", "content": "terminal evidence"}]
        agent = _make_agent()
        agent._memory_manager = MagicMock()
        session = _make_session(agent=agent, history=history)

        _finalize_session(session, end_reason="tui_close")

        agent._memory_manager.on_session_finalize.assert_called_once_with(
            history,
            reason="tui_close",
        )
        agent.commit_memory_session.assert_called_once_with(
            history,
            checkpoint_memory=False,
        )
        assert session["_memory_finalize_pending"] is False

    def test_manager_finalize_failure_stays_pending_without_checkpoint(self):
        """A failed boundary retains its immutable recovery marker semantics."""
        from tui_gateway.server import _finalize_session

        history = [{"role": "user", "content": "terminal evidence"}]
        agent = _make_agent()
        agent._memory_manager = MagicMock()
        agent._memory_manager.on_session_finalize.side_effect = OSError("disk full")
        session = _make_session(agent=agent, history=history)

        _finalize_session(session, end_reason="tui_close")

        agent.commit_memory_session.assert_called_once_with(
            history,
            checkpoint_memory=False,
        )
        assert session["_memory_finalize_pending"] is True

    @patch("tui_gateway.server._notify_session_boundary")
    @patch("tui_gateway.server._get_db")
    def test_manager_finalize_failure_leaves_session_db_open(
        self, mock_get_db, mock_notify
    ):
        """A failed durable boundary remains retryable instead of looking closed."""
        from tui_gateway.server import _finalize_session

        mock_db = MagicMock()
        mock_db.get_session.return_value = None
        mock_get_db.return_value = mock_db
        history = [{"role": "user", "content": "terminal evidence"}]
        agent = _make_agent(session_id="sess_pending")
        agent._memory_manager = MagicMock()
        agent._memory_manager.on_session_finalize.side_effect = OSError("disk full")
        session = _make_session(agent=agent, history=history)

        _finalize_session(session, end_reason="tui_close")

        assert session["_memory_finalize_pending"] is True
        assert agent._end_session_on_close is False
        mock_db.end_session.assert_not_called()
        mock_notify.assert_not_called()

    @patch("tui_gateway.server._get_db", return_value=None)
    @patch("tui_gateway.server._notify_session_boundary")
    def test_teardown_excludes_durable_checkpoint_after_finalize(
        self, _notify, _get_db
    ):
        """Provider shutdown cannot mutate a successfully recorded boundary."""
        from tui_gateway.server import _teardown_session

        history = [{"role": "user", "content": "terminal evidence"}]
        agent = _make_agent()
        manager = MagicMock()
        agent._memory_manager = manager
        session = _make_session(agent=agent, history=history)

        _teardown_session(session, end_reason="tui_close")

        assert session["_memory_finalize_attempted"] is True
        manager.on_session_finalize.assert_called_once_with(
            history,
            reason="tui_close",
        )
        manager.on_session_end.assert_called_once_with(
            history,
            include_durable=False,
        )
        manager.shutdown_all.assert_called_once_with()
        agent.shutdown_memory_provider.assert_not_called()
        agent.close.assert_called_once_with()

    @patch("tui_gateway.server._get_db", return_value=None)
    @patch("tui_gateway.server._notify_session_boundary")
    def test_teardown_excludes_durable_checkpoint_after_failed_finalize(
        self, _notify, _get_db
    ):
        """A recovery marker remains valid when terminal capture is pending."""
        from tui_gateway.server import _teardown_session

        history = [{"role": "user", "content": "terminal evidence"}]
        agent = _make_agent()
        manager = MagicMock()
        manager.on_session_finalize.side_effect = OSError("disk full")
        agent._memory_manager = manager
        session = _make_session(agent=agent, history=history)

        _teardown_session(session, end_reason="tui_close")

        assert session["_memory_finalize_attempted"] is True
        assert session["_memory_finalize_pending"] is True
        assert agent._end_session_on_close is False
        manager.on_session_end.assert_called_once_with(
            history,
            include_durable=False,
        )
        manager.shutdown_all.assert_called_once_with()
        agent.shutdown_memory_provider.assert_not_called()
        agent.close.assert_called_once_with()

    def test_no_agent_no_crash(self):
        """Session with agent=None exits cleanly."""
        from tui_gateway.server import _finalize_session

        session = _make_session(agent=None, history=[{"role": "user", "content": "x"}])
        _finalize_session(session)  # must not raise

    def test_empty_history_skips_persist(self):
        """Empty history → _persist_session not called (guard)."""
        from tui_gateway.server import _finalize_session

        agent = _make_agent()
        session = _make_session(agent=agent, history=[])

        _finalize_session(session)

        agent._persist_session.assert_not_called()

    def test_no_persist_method_skips(self):
        """Agent without _persist_session attribute → graceful skip."""
        from tui_gateway.server import _finalize_session

        agent = _make_agent()
        del agent._persist_session  # simulate older agent without the method
        session = _make_session(
            agent=agent,
            history=[{"role": "user", "content": "x"}],
        )

        _finalize_session(session)  # must not raise

    def test_already_finalized_skips(self):
        """Double-finalize is a no-op."""
        from tui_gateway.server import _finalize_session

        agent = _make_agent()
        session = _make_session(agent=agent, history=[{"role": "user", "content": "x"}])
        session["_finalized"] = True

        _finalize_session(session)

        agent._persist_session.assert_not_called()

    def test_persist_exception_does_not_block(self):
        """If _persist_session raises, finalization continues."""
        from tui_gateway.server import _finalize_session

        agent = _make_agent()
        agent._persist_session.side_effect = RuntimeError("db is down")
        session = _make_session(
            agent=agent,
            history=[{"role": "user", "content": "x"}],
        )

        _finalize_session(session)  # must not raise
        # commit_memory_session should still be called
        agent.commit_memory_session.assert_called_once()

    @patch("tui_gateway.server._get_db")
    def test_db_end_session_still_called(self, mock_get_db):
        """Existing db.end_session() path is preserved after the new code."""
        from tui_gateway.server import _finalize_session

        mock_db = MagicMock()
        mock_get_db.return_value = mock_db

        agent = _make_agent(session_id="sess_123")
        session = _make_session(agent=agent, history=[{"role": "user", "content": "x"}])

        _finalize_session(session, end_reason="test")

        mock_db.end_session.assert_called_once_with("sess_123", "test")


class TestOnSessionEndHook:
    """Verify on_session_end plugin hook fires on finalize."""

    @patch("hermes_cli.plugins.invoke_hook")
    def test_hook_fired_with_interrupted_true(self, mock_invoke_hook):
        """on_session_end is called with interrupted=True when finalizing."""
        from tui_gateway.server import _finalize_session

        agent = _make_agent(session_id="hook_test_001")
        agent.model = "claude-sonnet-4"
        agent.platform = "tui"
        session = _make_session(
            agent=agent, history=[{"role": "user", "content": "test"}]
        )

        _finalize_session(session, end_reason="tui_close")

        mock_invoke_hook.assert_any_call(
            "on_session_end",
            session_id="hook_test_001",
            completed=False,
            interrupted=True,
            model="claude-sonnet-4",
            platform="tui",
        )

    @patch("hermes_cli.plugins.invoke_hook")
    def test_hook_exception_does_not_block(self, mock_invoke_hook):
        """Hook failure doesn't prevent session finalization."""
        from tui_gateway.server import _finalize_session

        mock_invoke_hook.side_effect = RuntimeError("plugin crash")
        agent = _make_agent()
        session = _make_session(agent=agent, history=[{"role": "user", "content": "x"}])

        _finalize_session(session)  # must not raise
        agent.commit_memory_session.assert_called_once()


class TestResourceOnlyTeardown:
    """Operational eviction checkpoints state without ending the conversation."""

    @pytest.mark.parametrize(
        "reason",
        [
            "tui_shutdown",
            "ws_disconnect",
            "ws_orphan_reap",
            "idle_timeout",
            "lru_evict",
        ],
    )
    @patch("tui_gateway.server._notify_session_boundary")
    @patch("tui_gateway.server._get_db")
    def test_operational_teardown_is_nonsemantic(
        self, mock_get_db, mock_notify, reason
    ):
        from tui_gateway.server import _teardown_session

        db = MagicMock()
        db.get_session.return_value = {"source": "tui"}
        mock_get_db.return_value = db
        history = [
            {"role": "user", "content": "keep this resumable"},
            {"role": "assistant", "content": "checkpointed"},
        ]
        agent = _make_agent(session_id="resumable_session")
        manager = MagicMock()
        agent._memory_manager = manager
        session = _make_session(agent=agent, history=history)

        _teardown_session(session, end_reason=reason, finalize=False)

        manager.on_session_finalize.assert_not_called()
        mock_notify.assert_not_called()
        db.end_session.assert_not_called()
        agent._persist_session.assert_called_once_with(
            history, conversation_history=history
        )
        agent.shutdown_memory_provider.assert_called_once_with(
            history,
            finalize=False,
            reason=reason,
        )
        assert agent._end_session_on_close is False
        agent.close.assert_called_once_with()


def test_branch_memory_binding_preserves_cortex_lineage_without_admission(tmp_path):
    from altas.cortex.provider import CortexMemoryProvider
    from altas.cortex.store import CortexStore
    from tui_gateway.server import _bind_branch_agent_memory

    parent_id = "parent-session"
    child_id = "child-session"
    store = CortexStore(
        tmp_path / "cortex.db",
        owner_customer_id="customer-1",
    )
    store.initialize()
    store.ensure_session(parent_id)

    # The live source uses its own provider and must remain bound to the active
    # parent while an unpublished child provider establishes branch lineage.
    source_provider = CortexMemoryProvider()
    source_provider._store = store
    source_provider._config = SimpleNamespace()
    source_provider._session_id = parent_id

    child_provider = CortexMemoryProvider()
    child_provider._store = store
    child_provider._config = SimpleNamespace()
    child_provider._session_id = parent_id
    manager = SimpleNamespace(
        on_session_switch=MagicMock(side_effect=child_provider.on_session_switch),
        on_session_finalize=MagicMock(),
    )
    agent = SimpleNamespace(
        session_id=parent_id,
        _parent_session_id=parent_id,
        _memory_manager=manager,
    )

    _bind_branch_agent_memory(
        agent,
        new_session_id=child_id,
        parent_session_id=parent_id,
    )

    assert store.session_lineage(parent_id) == {
        "session_id": parent_id,
        "logical_conversation_id": parent_id,
        "parent_session_id": "",
        "state": "active",
    }
    assert store.session_lineage(child_id) == {
        "session_id": child_id,
        "logical_conversation_id": child_id,
        "parent_session_id": parent_id,
        "state": "active",
    }
    assert source_provider._session_id == parent_id
    assert child_provider._session_id == child_id
    assert agent.session_id == child_id
    manager.on_session_switch.assert_called_once_with(
        child_id,
        parent_session_id=parent_id,
        reset=True,
        reason="branch",
    )
    manager.on_session_finalize.assert_not_called()
    with store.connect() as connection:
        admissions = connection.execute(
            "SELECT COUNT(*) FROM session_distill_admissions WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()[0]
    assert admissions == 0

@patch("tui_gateway.server._notify_session_boundary")
@patch("tui_gateway.server._get_db")
def test_explicit_owned_close_finalizes_exactly_once(mock_get_db, mock_notify):
    from tui_gateway.server import _teardown_session

    db = MagicMock()
    db.get_session.return_value = {"source": "tui"}
    mock_get_db.return_value = db
    history = [{"role": "user", "content": "done"}]
    agent = _make_agent(session_id="closed_session")
    manager = MagicMock()
    agent._memory_manager = manager
    session = _make_session(agent=agent, history=history)
    session["source"] = "tui"

    _teardown_session(session, end_reason="tui_close", finalize=True)

    manager.on_session_finalize.assert_called_once_with(
        history, reason="tui_close"
    )
    mock_notify.assert_called_once_with(
        "on_session_finalize", "closed_session", "tui"
    )
    db.end_session.assert_called_once_with("closed_session", "tui_close")
    manager.on_session_end.assert_called_once_with(
        history, include_durable=False
    )
    manager.shutdown_all.assert_called_once_with()
    agent.shutdown_memory_provider.assert_not_called()
    assert agent._end_session_on_close is False
    agent.close.assert_called_once_with()

    _teardown_session(session, end_reason="tui_close", finalize=True)
    manager.on_session_finalize.assert_called_once()
    db.end_session.assert_called_once()


@patch("tui_gateway.server._notify_session_boundary")
@patch("tui_gateway.server._get_db")
def test_explicit_gateway_viewer_close_only_releases_resources(
    mock_get_db, mock_notify
):
    from tui_gateway.server import _teardown_session

    db = MagicMock()
    db.get_session.return_value = {"source": "telegram"}
    mock_get_db.return_value = db
    agent = _make_agent(session_id="gateway_session")
    manager = MagicMock()
    agent._memory_manager = manager
    session = _make_session(
        agent=agent,
        history=[{"role": "user", "content": "gateway owns this"}],
    )

    _teardown_session(session, end_reason="tui_close", finalize=True)

    manager.on_session_finalize.assert_not_called()
    mock_notify.assert_not_called()
    db.end_session.assert_not_called()
    agent.shutdown_memory_provider.assert_called_once_with(
        [], finalize=False, reason="tui_close"
    )
    assert agent._end_session_on_close is False
    agent.close.assert_called_once_with()
