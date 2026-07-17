from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hermes_cli import oneshot


def test_finalize_oneshot_orders_cortex_before_db_end_and_cleanup(monkeypatch):
    events: list[str] = []
    agent = MagicMock()
    agent.session_id = "oneshot-session"
    agent._session_messages = [{"role": "user", "content": "remember this"}]
    agent.shutdown_memory_provider.side_effect = lambda *_args, **_kwargs: (
        events.append("cortex")
    )
    agent.close.side_effect = lambda: events.append("agent-close")
    db = MagicMock()
    db.end_session.side_effect = lambda *_args: events.append("db-end")
    db.close.side_effect = lambda: events.append("db-close")
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_k: None)

    oneshot._finalize_oneshot_agent(agent, db, reason="oneshot_complete")

    assert events == ["cortex", "db-end", "agent-close", "db-close"]
    agent.shutdown_memory_provider.assert_called_once_with(
        agent._session_messages,
        finalize=True,
        reason="oneshot_complete",
    )
    db.end_session.assert_called_once_with("oneshot-session", "oneshot_complete")
    assert agent._end_session_on_close is False


def test_finalize_oneshot_failure_never_publishes_db_end():
    agent = MagicMock()
    agent.session_id = "oneshot-session"
    agent._session_messages = [{"role": "user", "content": "retain safely"}]
    agent.shutdown_memory_provider.side_effect = OSError("disk full")
    db = MagicMock()

    with pytest.raises(OSError, match="disk full"):
        oneshot._finalize_oneshot_agent(agent, db, reason="oneshot_complete")

    db.end_session.assert_not_called()
    agent.close.assert_called_once_with()
    db.close.assert_called_once_with()
    assert agent._end_session_on_close is False


def test_finalize_oneshot_does_not_retry_an_internal_type_error():
    calls = 0

    class Agent:
        session_id = "oneshot-session"
        _session_messages = []

        def shutdown_memory_provider(self, _messages, *, finalize, reason):
            nonlocal calls
            calls += 1
            assert finalize is True
            assert reason == "oneshot_complete"
            raise TypeError("internal provider bug")

        def close(self):
            pass

    db = MagicMock()

    with pytest.raises(TypeError, match="internal provider bug"):
        oneshot._finalize_oneshot_agent(
            Agent(),
            db,
            reason="oneshot_complete",
        )

    assert calls == 1
    db.end_session.assert_not_called()


def test_run_agent_finalizes_real_one_shot_boundary(monkeypatch):
    events: list[str] = []

    class FakeAgent:
        def __init__(self, **_kwargs):
            self.session_id = "oneshot-session"
            self._session_messages = []
            self.suppress_status_output = False
            self.stream_delta_callback = object()
            self.tool_gen_callback = object()

        def run_conversation(self, prompt):
            self._session_messages = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": "done"},
            ]
            events.append("turn")
            return {"final_response": "done", "completed": True}

        def shutdown_memory_provider(self, messages, *, finalize, reason):
            assert messages == self._session_messages
            assert finalize is True
            assert reason == "oneshot_complete"
            events.append("cortex")

        def close(self):
            events.append("agent-close")

    db = MagicMock()
    db.end_session.side_effect = lambda *_args: events.append("db-end")
    db.close.side_effect = lambda: events.append("db-close")

    monkeypatch.setattr("run_agent.AIAgent", FakeAgent)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"model": {}})
    monkeypatch.setattr(
        "hermes_cli.runtime_provider.resolve_runtime_provider",
        lambda **_kwargs: {"provider": "test", "api_key": "test"},
    )
    monkeypatch.setattr(oneshot, "_create_session_db_for_oneshot", lambda: db)
    monkeypatch.setattr(oneshot, "get_fallback_chain", lambda _cfg: [])
    monkeypatch.setattr("hermes_cli.plugins.invoke_hook", lambda *_a, **_k: None)

    response, result = oneshot._run_agent(
        "finish this",
        model="test-model",
        provider="test",
        use_config_toolsets=False,
    )

    assert response == "done"
    assert result["completed"] is True
    assert events == ["turn", "cortex", "db-end", "agent-close", "db-close"]
