"""Gateway resource cleanup checkpoints without ending logical sessions.

Before the fix, ``_cleanup_agent_resources`` called
``agent.shutdown_memory_provider()`` with no arguments, which in turn
invoked ``on_session_end([])`` on every memory provider. Providers with
an empty-guard (Holographic, Hindsight, etc.) exited early and never
persisted the session's facts, so the next gateway start-up surfaced no
memories from the prior conversation.

The cleanup reads ``agent._session_messages`` and forwards it explicitly with
``finalize=False``. Signals, restarts, cache cleanup, and process exit are not
customer-owned session ends and therefore cannot admit Cortex semantic work.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_dotenv(monkeypatch):
    """gateway.run imports dotenv at module load; stub so tests run bare."""
    fake = types.ModuleType("dotenv")
    fake.load_dotenv = lambda *a, **kw: None
    monkeypatch.setitem(sys.modules, "dotenv", fake)


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    return runner


# A lightweight stand-in for AIAgent so ``isinstance(..., list)`` correctly
# discriminates between "attribute set to a list" and "attribute absent /
# MagicMock auto-synthesised". Using MagicMock directly for the agent
# would also work for the populated case, but attribute access on a
# MagicMock always yields a child MagicMock — we want a real Python
# object we can shape per-test.
class _FakeAgent:
    def __init__(self, session_messages=None, has_shutdown=True):
        if session_messages is not None:
            self._session_messages = session_messages
        if has_shutdown:
            self.shutdown_memory_provider = MagicMock()
        self.close = MagicMock()


class TestCleanupAgentResourcesPassesMessages:
    """_cleanup_agent_resources forwards the agent's session messages."""

    def test_populated_messages_forwarded(self):
        """Real-world path: an agent that ran a turn has a populated
        ``_session_messages`` list and the cleanup call forwards it."""
        runner = _make_runner()
        transcript = [
            {"role": "user", "content": "remember my dog is named Biscuit"},
            {"role": "assistant", "content": "Got it — Biscuit."},
        ]
        agent = _FakeAgent(session_messages=transcript)

        runner._cleanup_agent_resources(agent)

        # The fix must call shutdown_memory_provider with the exact list
        # identity — providers iterate it to extract facts.
        agent.shutdown_memory_provider.assert_called_once_with(
            transcript,
            finalize=False,
            reason="resource_cleanup",
        )

    def test_empty_list_still_forwarded(self):
        """An agent that initialised but ran no turns has an empty list
        on ``_session_messages``. Forwarding it (rather than falling
        through to the no-arg path) makes the absence of content
        explicit to providers and matches the pre-fix observable
        behaviour (``on_session_end([])``)."""
        runner = _make_runner()
        agent = _FakeAgent(session_messages=[])

        runner._cleanup_agent_resources(agent)

        agent.shutdown_memory_provider.assert_called_once_with(
            [],
            finalize=False,
            reason="resource_cleanup",
        )

    def test_missing_attribute_falls_back_to_no_arg(self):
        """Test stubs built via ``object.__new__(AIAgent)`` skip
        ``__init__`` and therefore have no ``_session_messages``
        attribute. The fix must not explode — it falls back to the
        legacy no-arg call so existing suites keep passing."""
        runner = _make_runner()
        agent = _FakeAgent(session_messages=None)  # attribute not set

        runner._cleanup_agent_resources(agent)

        agent.shutdown_memory_provider.assert_called_once_with(
            finalize=False,
            reason="resource_cleanup",
        )

    def test_non_list_attribute_falls_back_to_no_arg(self):
        """A MagicMock-based agent auto-synthesises ``_session_messages``
        as a nested MagicMock. ``isinstance(mock, list)`` is False, so
        we fall back to the no-arg path rather than passing a garbage
        value to providers that expect ``List[Dict]``."""
        runner = _make_runner()
        agent = MagicMock()
        # No explicit _session_messages assignment — MagicMock will
        # synthesise one on access.

        runner._cleanup_agent_resources(agent)

        agent.shutdown_memory_provider.assert_called_once_with(
            finalize=False,
            reason="resource_cleanup",
        )

    def test_provider_exception_is_swallowed(self):
        """Provider teardown must be best-effort — a raising
        ``shutdown_memory_provider`` must not prevent ``close()`` from
        running (tool resource leak is worse than a missed memory
        flush)."""
        runner = _make_runner()
        agent = _FakeAgent(session_messages=[{"role": "user", "content": "x"}])
        agent.shutdown_memory_provider.side_effect = RuntimeError("boom")

        # Must not raise.
        runner._cleanup_agent_resources(agent)

        # close() still invoked after the swallowed exception.
        agent.close.assert_called_once()

    def test_none_agent_is_noop(self):
        """Defensive: None agent short-circuits (idle sweeps may
        observe a None entry in the cache during eviction races)."""
        runner = _make_runner()
        # Must not raise.
        runner._cleanup_agent_resources(None)

    def test_agent_without_shutdown_method_is_tolerated(self):
        """An agent without ``shutdown_memory_provider`` (old test
        stub, partial mock) must still have ``close()`` called."""
        runner = _make_runner()
        agent = _FakeAgent(has_shutdown=False)
        # No _session_messages either, to exercise the hasattr guard.

        runner._cleanup_agent_resources(agent)

        agent.close.assert_called_once()
