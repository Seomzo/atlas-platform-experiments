"""AIAgent.close must not publish a Cortex logical-session boundary."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import run_agent
from run_agent import AIAgent


def _agent(*, cortex_selected: bool) -> AIAgent:
    agent = object.__new__(AIAgent)
    agent.session_id = "close-boundary-session"
    agent._session_db = MagicMock()
    agent._end_session_on_close = True
    agent._cortex_memory_selected = cortex_selected
    agent._cortex_memory_active = False
    agent._active_children_lock = threading.Lock()
    agent._active_children = set()
    agent.client = None
    return agent


def test_close_cannot_end_a_cortex_session_without_durable_boundary(monkeypatch):
    monkeypatch.setattr(run_agent, "cleanup_vm", lambda _task_id: None)
    monkeypatch.setattr(run_agent, "cleanup_browser", lambda _task_id: None)
    agent = _agent(cortex_selected=True)

    agent.close()

    agent._session_db.end_session.assert_not_called()


def test_close_preserves_non_cortex_compatibility(monkeypatch):
    monkeypatch.setattr(run_agent, "cleanup_vm", lambda _task_id: None)
    monkeypatch.setattr(run_agent, "cleanup_browser", lambda _task_id: None)
    agent = _agent(cortex_selected=False)

    agent.close()

    agent._session_db.end_session.assert_called_once_with(
        "close-boundary-session", "agent_close"
    )
