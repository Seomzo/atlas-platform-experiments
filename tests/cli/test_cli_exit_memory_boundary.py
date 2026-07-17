"""Classic interactive CLI logical-exit memory ordering regressions."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import cli as cli_mod


def _interactive_cli(*, delete: bool = False):
    transcript = [
        {
            "role": "user",
            "content": "remember my service preference",
            "_db_row_id": 41,
        },
        {
            "role": "assistant",
            "content": "Understood.",
            "_db_row_id": 42,
        },
    ]
    instance = object.__new__(cli_mod.HermesCLI)
    instance.conversation_history = transcript
    instance.session_id = "cli-session"
    instance.platform = "cli"
    instance._delete_session_on_exit = delete
    instance._discard_session_if_empty = MagicMock(return_value=False)
    return instance, transcript


def _disable_global_cleanup_side_effects(monkeypatch):
    monkeypatch.setattr(cli_mod, "_reset_terminal_input_modes_on_exit", lambda: None)
    monkeypatch.setattr(cli_mod, "_cleanup_all_terminals", lambda: None)
    monkeypatch.setattr(cli_mod, "_cleanup_all_browsers", lambda: None)
    monkeypatch.setattr("tools.async_delegation.interrupt_all", lambda **_kwargs: None)
    monkeypatch.setattr("tools.mcp_tool.shutdown_mcp_servers", lambda: None)
    monkeypatch.setattr("agent.auxiliary_client.shutdown_cached_clients", lambda: None)


def test_ui_teardown_only_closes_after_explicit_logical_intent():
    instance, _transcript = _interactive_cli()
    instance._logical_close_requested = True
    instance._resource_shutdown_requested = False
    instance._close_active_session_for_exit = MagicMock(return_value=True)
    instance._wait_for_active_turn_before_exit = MagicMock()
    instance._persist_active_session_before_close = MagicMock()

    assert instance._apply_interactive_exit_boundary() == (True, True)

    instance._close_active_session_for_exit.assert_called_once_with()
    instance._wait_for_active_turn_before_exit.assert_not_called()
    instance._persist_active_session_before_close.assert_not_called()


def test_signal_or_unexpected_ui_teardown_preserves_session_for_resume():
    instance, _transcript = _interactive_cli()
    # Even if normal close intent was staged, an operational signal owns this
    # unwind and cannot authorize semantic finalization.
    instance._logical_close_requested = True
    instance._resource_shutdown_requested = True
    instance._close_active_session_for_exit = MagicMock(return_value=True)
    instance._wait_for_active_turn_before_exit = MagicMock(return_value=True)
    instance._persist_active_session_before_close = MagicMock()

    assert instance._apply_interactive_exit_boundary() == (False, False)

    instance._close_active_session_for_exit.assert_not_called()
    instance._wait_for_active_turn_before_exit.assert_called_once_with()
    instance._persist_active_session_before_close.assert_called_once_with()


def test_broken_stdin_style_teardown_without_close_intent_never_finalizes():
    instance, _transcript = _interactive_cli()
    instance._logical_close_requested = False
    instance._resource_shutdown_requested = False
    instance._close_active_session_for_exit = MagicMock(return_value=True)
    instance._wait_for_active_turn_before_exit = MagicMock(return_value=False)
    instance._persist_active_session_before_close = MagicMock()

    assert instance._apply_interactive_exit_boundary() == (False, False)

    instance._close_active_session_for_exit.assert_not_called()
    instance._persist_active_session_before_close.assert_not_called()


def test_interactive_exit_finalizes_cortex_before_db_end_and_only_once(monkeypatch):
    events: list[str] = []
    instance, transcript = _interactive_cli()
    manager = SimpleNamespace(
        flush_pending=lambda timeout: events.append("flush") or True,
    )
    agent = SimpleNamespace(
        session_id="cli-session",
        platform="cli",
        _session_messages=transcript,
        _memory_manager=manager,
        _persist_session=lambda messages, history: (
            events.append("persist")
            if messages is transcript and history is transcript
            else None
        ),
    )

    def shutdown(messages, *, finalize, reason):
        assert messages is transcript
        assert finalize is True
        assert reason == "cli_close"
        events.append("cortex-finalize")

    agent.shutdown_memory_provider = MagicMock(side_effect=shutdown)
    instance.agent = agent
    session_db = MagicMock()
    session_db.end_session.side_effect = lambda *_args: events.append("db-end")
    instance._session_db = session_db
    monkeypatch.setattr(
        cli_mod,
        "_notify_session_finalize",
        lambda **_kwargs: events.append("plugin-finalize"),
    )

    assert instance._close_active_session_for_exit() is True

    assert events == [
        "persist",
        "flush",
        "cortex-finalize",
        "plugin-finalize",
        "db-end",
    ]
    session_db.end_session.assert_called_once_with("cli-session", "cli_close")
    session_db.delete_session.assert_not_called()

    # Process cleanup follows interactive teardown, but must not finalize the
    # exact same Cortex session a second time.
    _disable_global_cleanup_side_effects(monkeypatch)
    monkeypatch.setattr(cli_mod, "_active_agent_ref", agent)
    monkeypatch.setattr(cli_mod, "_cleanup_done", False)
    cli_mod._run_cleanup()

    agent.shutdown_memory_provider.assert_called_once()
    assert events.count("plugin-finalize") == 1


def test_interactive_exit_durable_failure_leaves_db_open_and_is_not_retried(
    monkeypatch,
):
    messages: list[str] = []
    instance, transcript = _interactive_cli()
    manager = SimpleNamespace(flush_pending=lambda timeout: True)
    agent = SimpleNamespace(
        session_id="cli-session",
        platform="cli",
        _session_messages=transcript,
        _memory_manager=manager,
    )
    agent.shutdown_memory_provider = MagicMock(side_effect=OSError("disk full"))
    instance.agent = agent
    instance._session_db = MagicMock()
    monkeypatch.setattr(cli_mod, "_cprint", messages.append)

    assert instance._close_active_session_for_exit() is False

    instance._session_db.end_session.assert_not_called()
    instance._session_db.delete_session.assert_not_called()
    assert "left open for recovery" in messages[-1]

    _disable_global_cleanup_side_effects(monkeypatch)
    monkeypatch.setattr(cli_mod, "_active_agent_ref", agent)
    monkeypatch.setattr(cli_mod, "_cleanup_done", False)
    cli_mod._run_cleanup()

    agent.shutdown_memory_provider.assert_called_once()


def test_interactive_exit_active_turn_timeout_never_finalizes_or_ends_db(monkeypatch):
    messages: list[str] = []
    instance, transcript = _interactive_cli()
    agent = SimpleNamespace(
        session_id="cli-session",
        platform="cli",
        _session_messages=transcript,
        _memory_manager=MagicMock(),
        shutdown_memory_provider=MagicMock(),
        _persist_session=MagicMock(),
    )
    instance.agent = agent
    instance._session_db = MagicMock()
    instance._wait_for_active_turn_before_exit = MagicMock(return_value=False)
    monkeypatch.setattr(cli_mod, "_cprint", messages.append)

    assert instance._close_active_session_for_exit() is False

    agent._persist_session.assert_not_called()
    agent.shutdown_memory_provider.assert_not_called()
    instance._session_db.end_session.assert_not_called()
    instance._session_db.delete_session.assert_not_called()
    assert "durability barrier" in messages[-1]
    assert cli_mod._cli_exit_memory_boundary_attempted(agent, "cli-session")


def test_exit_delete_reconciles_all_rows_then_deletes_without_distillation(
    monkeypatch,
):
    events: list[str] = []
    instance, transcript = _interactive_cli(delete=True)
    cortex = SimpleNamespace(name="cortex")

    class Manager:
        providers = [cortex]

        def flush_pending(self, timeout):
            events.append("flush")
            return True

        def get_provider(self, name):
            assert name == "cortex"
            return cortex

        def on_session_switch(self, session_id, **kwargs):
            assert session_id == "cli-session"
            assert kwargs == {
                "parent_session_id": "",
                "reset": False,
                "rewound": True,
                "rewound_row_ids": [39, 40, 41, 42],
            }
            events.append("cortex-reconcile")

        def shutdown_all(self):
            events.append("provider-shutdown")

    agent = SimpleNamespace(
        session_id="cli-session",
        platform="cli",
        _session_messages=transcript,
        _memory_manager=Manager(),
        _cortex_memory_active=True,
        shutdown_memory_provider=MagicMock(),
    )
    instance.agent = agent
    session_db = MagicMock()
    session_db.get_messages.return_value = [
        {"id": 39},
        {"id": 40},
        {"id": 41},
        {"id": 42},
    ]
    session_db.delete_session.side_effect = lambda *_args, **_kwargs: (
        events.append("transcript-delete") or True
    )
    instance._session_db = session_db
    plugin_finalize = MagicMock()
    monkeypatch.setattr(cli_mod, "_notify_session_finalize", plugin_finalize)

    assert instance._close_active_session_for_exit() is True

    assert events == [
        "flush",
        "cortex-reconcile",
        "provider-shutdown",
        "transcript-delete",
    ]
    session_db.end_session.assert_not_called()
    agent.shutdown_memory_provider.assert_not_called()
    plugin_finalize.assert_not_called()

    _disable_global_cleanup_side_effects(monkeypatch)
    monkeypatch.setattr(cli_mod, "_active_agent_ref", agent)
    monkeypatch.setattr(cli_mod, "_cleanup_done", False)
    cli_mod._run_cleanup()

    # The process cleanup safety net must not reinterpret deletion as a normal
    # semantic session end after the transcript is gone.
    assert events == [
        "flush",
        "cortex-reconcile",
        "provider-shutdown",
        "transcript-delete",
    ]
    agent.shutdown_memory_provider.assert_not_called()
    plugin_finalize.assert_not_called()


def test_exit_delete_reconciliation_failure_leaves_transcript_intact(monkeypatch):
    messages: list[str] = []
    instance, transcript = _interactive_cli(delete=True)
    cortex = SimpleNamespace(name="cortex")
    manager = MagicMock()
    manager.providers = [cortex]
    manager.flush_pending.return_value = True
    manager.get_provider.return_value = cortex
    manager.on_session_switch.side_effect = OSError("Cortex DB unavailable")
    agent = SimpleNamespace(
        session_id="cli-session",
        platform="cli",
        _session_messages=transcript,
        _memory_manager=manager,
        _cortex_memory_active=True,
        shutdown_memory_provider=MagicMock(),
    )
    instance.agent = agent
    instance._session_db = MagicMock()
    instance._session_db.get_messages.return_value = [{"id": 41}, {"id": 42}]
    monkeypatch.setattr(cli_mod, "_cprint", messages.append)

    assert instance._close_active_session_for_exit() is False

    manager.shutdown_all.assert_called_once_with()
    instance._session_db.end_session.assert_not_called()
    instance._session_db.delete_session.assert_not_called()
    agent.shutdown_memory_provider.assert_not_called()
    assert "left intact" in messages[-1]


def test_exit_delete_refuses_nonempty_transcript_without_row_provenance(monkeypatch):
    messages: list[str] = []
    instance, transcript = _interactive_cli(delete=True)
    for message in transcript:
        message.pop("_db_row_id", None)
    manager = MagicMock()
    manager.providers = [SimpleNamespace(name="cortex")]
    manager.flush_pending.return_value = True
    agent = SimpleNamespace(
        session_id="cli-session",
        platform="cli",
        _session_messages=transcript,
        _memory_manager=manager,
        _cortex_memory_active=True,
    )
    instance.agent = agent
    instance._session_db = MagicMock()
    instance._session_db.get_messages.return_value = []
    monkeypatch.setattr(cli_mod, "_cprint", messages.append)

    assert instance._close_active_session_for_exit() is False

    manager.on_session_switch.assert_not_called()
    manager.shutdown_all.assert_called_once_with()
    instance._session_db.delete_session.assert_not_called()
    assert "left intact" in messages[-1]


def test_exit_delete_refuses_provider_without_reconciliation_primitive(monkeypatch):
    messages: list[str] = []
    instance, transcript = _interactive_cli(delete=True)
    manager = MagicMock()
    manager.providers = [SimpleNamespace(name="external-memory")]
    manager.flush_pending.return_value = True
    agent = SimpleNamespace(
        session_id="cli-session",
        platform="cli",
        _session_messages=transcript,
        _memory_manager=manager,
        _cortex_memory_active=False,
    )
    instance.agent = agent
    instance._session_db = MagicMock()
    instance._session_db.get_messages.return_value = [{"id": 41}, {"id": 42}]
    monkeypatch.setattr(cli_mod, "_cprint", messages.append)

    assert instance._close_active_session_for_exit() is False

    manager.on_session_switch.assert_not_called()
    manager.shutdown_all.assert_called_once_with()
    instance._session_db.delete_session.assert_not_called()
    assert "left intact" in messages[-1]
