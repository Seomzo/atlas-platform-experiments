"""Memory isolation and teardown for the TUI one-shot background helper."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from tui_gateway import server


class _InlineThread:
    """Run a background target inline so its lifecycle is deterministic."""

    def __init__(self, *, target, daemon=False, **_kwargs):
        self.target = target
        self.daemon = daemon

    def start(self):
        self.target()


def _parent_agent():
    return SimpleNamespace(
        model="test-model",
        provider="test-provider",
        enabled_toolsets=["file"],
        request_overrides={},
        _fallback_chain=[],
    )


def test_background_agent_kwargs_disable_primary_memory(monkeypatch):
    monkeypatch.setattr(server, "_load_cfg", lambda: {"max_turns": 8})
    monkeypatch.setattr(server, "_get_db", lambda: None)

    kwargs = server._background_agent_kwargs(_parent_agent(), "bg_test")

    assert kwargs["skip_memory"] is True
    assert kwargs["session_id"] == "bg_test"
    assert kwargs["platform"] == "tui"


@pytest.mark.parametrize("raises", [False, True], ids=["success", "failure"])
def test_prompt_background_always_closes_ephemeral_agent(monkeypatch, raises):
    parent = _parent_agent()
    session = {"agent": parent, "cwd": "/tmp/atlas-background-test"}
    emitted = []
    constructed = {}

    child = MagicMock()
    if raises:
        child.run_conversation.side_effect = RuntimeError("boom")
    else:
        child.run_conversation.return_value = {"final_response": "done"}

    def fake_agent(**kwargs):
        constructed.update(kwargs)
        return child

    monkeypatch.setattr(server, "_sess", lambda _params, _rid: (session, None))
    monkeypatch.setattr(server, "_load_cfg", lambda: {"max_turns": 8})
    monkeypatch.setattr(server, "_get_db", lambda: None)
    monkeypatch.setattr(server, "_set_session_context", lambda *_a, **_kw: ["token"])
    clear_context = MagicMock()
    monkeypatch.setattr(server, "_clear_session_context", clear_context)
    monkeypatch.setattr(
        server,
        "_emit",
        lambda event, sid, payload: emitted.append((event, sid, payload)),
    )
    monkeypatch.setattr(server.threading, "Thread", _InlineThread)

    import run_agent

    monkeypatch.setattr(run_agent, "AIAgent", fake_agent)

    response = server._methods["prompt.background"](
        "rid", {"session_id": "parent-session", "text": "do work"}
    )

    assert response["result"]["task_id"].startswith("bg_")
    assert constructed["skip_memory"] is True
    child.shutdown_memory_provider.assert_called_once_with([], finalize=False)
    child.close.assert_called_once_with()
    clear_context.assert_called_once_with(["token"])
    assert emitted[0][0:2] == ("background.complete", "parent-session")
    if raises:
        assert "error: boom" in emitted[0][2]["text"]
    else:
        assert emitted[0][2]["text"] == "done"
