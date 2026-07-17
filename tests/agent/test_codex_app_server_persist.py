"""Regression for #49225 — codex app-server turns must reach the session DB
exactly once.

The codex app-server runtime (``run_codex_app_server_turn``) is an early-return
path that bypasses ``conversation_loop`` and therefore never runs the loop's
per-step ``_persist_session()`` flushes. Before the fix, the projected
assistant/tool messages were persisted *nowhere* (state.db got only
session_meta rows), leaving ``session_search`` (FTS) and conversation-distill
blind to real gateway conversations.

The fix has the codex runtime flush its own projected messages via
``_flush_messages_to_session_db()`` (idempotent through the intrinsic
``_DB_PERSISTED_MARKER``) and return ``agent_persisted=True`` so the gateway
skips its own ``append_to_transcript`` DB write. This is critical: the inbound
user turn is already flushed at turn start (``turn_context._persist_session``),
and ``append_message`` is a raw INSERT with no dedup — a gateway re-write would
duplicate the user turn (#860 / #42039). This test locks in:

1. ``run_codex_app_server_turn`` flushes projected messages and returns
   ``agent_persisted=True``.
2. Exactly-once persistence: the already-flushed user turn is NOT re-written,
   and the new projected assistant message lands once.
3. The gateway resolution expression preserves standard-runtime behaviour.
"""

import tempfile
from pathlib import Path
from types import MethodType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agent.codex_runtime import run_codex_app_server_turn
from agent.memory_manager import MemoryDurabilityError
from hermes_state import SessionDB
from run_agent import AIAgent


def _make_turn():
    return SimpleNamespace(
        interrupted=False,
        error=None,
        thread_id="thread-1",
        turn_id="turn-1",
        projected_messages=[{"role": "assistant", "content": "CODEX_ASSISTANT"}],
        tool_iterations=0,
        final_text="CODEX_ASSISTANT",
        should_retire=False,
    )


def _make_agent(session_db=None, session_id="sess-codex"):
    agent = MagicMock()
    # Pre-seed the session so run_codex_app_server_turn skips the spawn block.
    agent._codex_session = MagicMock()
    agent._codex_session.run_turn.return_value = _make_turn()
    agent.tool_progress_callback = None
    agent._iters_since_skill = 0
    agent._skill_nudge_interval = 0
    agent.valid_tool_names = set()
    agent._session_db = session_db
    agent._session_db_created = True
    agent.session_id = session_id
    return agent


def _attach_real_memory_sync(agent):
    """Bind the production sync helper to the otherwise-minimal mock agent."""
    manager = MagicMock()
    manager._tool_to_provider = {}
    manager.get_all_tool_schemas.return_value = []
    agent._memory_manager = manager
    agent._current_turn_id = "codex-turn"
    agent._current_task_id = "task-1"
    agent._current_api_request_id = "request-1"
    agent.platform = "test"
    agent._sync_external_memory_for_turn = MethodType(
        AIAgent._sync_external_memory_for_turn,
        agent,
    )
    return manager


def test_codex_success_flushes_and_reports_persisted():
    """Codex success turn must self-persist and return agent_persisted=True."""
    agent = _make_agent(session_db=None)  # no DB -> flush is a no-op, still True
    result = run_codex_app_server_turn(
        agent,
        user_message="hello",
        original_user_message="hello",
        messages=[{"role": "user", "content": "hello"}],
        effective_task_id="task-1",
    )
    assert result["completed"] is True
    # With the agent as sole persister, the gateway must SKIP its DB write.
    assert result["agent_persisted"] is True


def test_codex_turn_persists_each_message_exactly_once():
    """The user turn (flushed at turn start) must not be duplicated; the
    projected assistant message must land once.  Uses a real SessionDB and the
    real AIAgent._flush_messages_to_session_db to prove no #860/#42039
    duplicate-write regression on the codex path."""
    tmp = tempfile.mkdtemp(prefix="codex_persist_")
    try:
        db = SessionDB(Path(tmp) / "state.db")
        sid = "sess-codex-once"
        db.create_session(session_id=sid, source="telegram", model="codex")

        # Real agent bound to this DB/session, minimal construction.
        agent = AIAgent(
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            session_db=db,
            session_id=sid,
        )
        agent._session_db_created = True
        agent._codex_session = MagicMock()
        agent._codex_session.run_turn.return_value = _make_turn()
        agent.tool_progress_callback = None

        # Model the real flow: the inbound user turn is flushed at turn start
        # (turn_context._persist_session) on the SAME `messages` list the codex
        # path later reuses. That flush stamps _DB_PERSISTED_MARKER on the user
        # dict, so the codex-path flush skips it — no duplicate.
        user_msg = {"role": "user", "content": "USER_TURN"}
        messages = [user_msg]
        agent._flush_messages_to_session_db(messages)  # turn-start flush

        result = run_codex_app_server_turn(
            agent,
            user_message="USER_TURN",
            original_user_message="USER_TURN",
            messages=messages,
            effective_task_id="task-1",
            volatile_user_context=(
                "<memory-context>\nVOLATILE_CORTEX_RECALL\n</memory-context>"
            ),
        )
        assert result["agent_persisted"] is True

        agent._codex_session.run_turn.assert_called_once_with(
            user_input="USER_TURN",
            untrusted_context=(
                "<memory-context>\nVOLATILE_CORTEX_RECALL\n</memory-context>"
            ),
        )

        rows = db.get_messages(sid, include_inactive=True)
        contents = [r["content"] for r in rows]
        # Exactly one user turn, exactly one assistant turn — no duplicates.
        assert contents.count("USER_TURN") == 1, contents
        assert contents.count("CODEX_ASSISTANT") == 1, contents
        assert all("VOLATILE_CORTEX_RECALL" not in content for content in contents)
        # session_search can now see the codex conversation.
        hits = {r["session_id"] for r in db.search_messages("CODEX_ASSISTANT")}
        assert sid in hits
    finally:
        import shutil

        shutil.rmtree(tmp)


def test_codex_interrupted_turn_syncs_user_and_tools_without_partial_answer():
    """Codex interrupt handling must match standard turn finalization."""
    projected = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "terminal",
                        "arguments": '{"command":"pytest"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "name": "terminal",
            "tool_call_id": "call-1",
            "content": "1 passed",
        },
        {"role": "assistant", "content": "PARTIAL ANSWER"},
    ]
    agent = _make_agent()
    agent._codex_session.run_turn.return_value = SimpleNamespace(
        interrupted=True,
        error="user interrupted",
        thread_id="thread-1",
        turn_id="turn-1",
        projected_messages=projected,
        tool_iterations=1,
        final_text="PARTIAL ANSWER",
        should_retire=False,
    )
    manager = _attach_real_memory_sync(agent)
    messages = [{"role": "user", "content": "run tests"}]

    result = run_codex_app_server_turn(
        agent,
        user_message="run tests",
        original_user_message="run tests",
        messages=messages,
        effective_task_id="task-1",
    )

    assert result["partial"] is True
    sync_args, sync_kwargs = manager.sync_all.call_args
    # The shared helper never promotes the partial Codex answer as assistant
    # truth, while retaining the projected trajectory for completed tool
    # call/result capture by Cortex.
    assert sync_args == ("run tests", "")
    assert sync_kwargs["messages"] is messages
    assert projected[0] in messages
    assert projected[1] in messages
    manager.queue_prefetch_all.assert_not_called()


def test_codex_error_result_syncs_as_interrupted():
    """A returned Codex error is partial even if its interrupt flag is false."""
    agent = _make_agent()
    agent._codex_session.run_turn.return_value = SimpleNamespace(
        interrupted=False,
        error="app-server failed after tool completion",
        thread_id="thread-1",
        turn_id="turn-1",
        projected_messages=[
            {
                "role": "tool",
                "name": "terminal",
                "tool_call_id": "call-1",
                "content": "completed output",
            }
        ],
        tool_iterations=1,
        final_text="PARTIAL ANSWER",
        should_retire=False,
    )
    manager = _attach_real_memory_sync(agent)
    messages = [{"role": "user", "content": "inspect the repo"}]

    result = run_codex_app_server_turn(
        agent,
        user_message="inspect the repo",
        original_user_message="inspect the repo",
        messages=messages,
        effective_task_id="task-1",
    )

    assert result["partial"] is True
    sync_args, sync_kwargs = manager.sync_all.call_args
    assert sync_args == ("inspect the repo", "")
    assert sync_kwargs["turn_metadata"]["interrupted"] is True
    assert sync_kwargs["messages"] is messages
    manager.queue_prefetch_all.assert_not_called()


def test_codex_transport_exception_still_syncs_authoritative_user():
    """A thrown transport error must not discard the accepted user turn."""
    agent = _make_agent()
    agent._codex_session.run_turn.side_effect = RuntimeError("subprocess died")
    manager = _attach_real_memory_sync(agent)
    messages = [{"role": "user", "content": "remember this attempt"}]

    result = run_codex_app_server_turn(
        agent,
        user_message="remember this attempt",
        original_user_message="remember this attempt",
        messages=messages,
        effective_task_id="task-1",
    )

    assert result["partial"] is True
    sync_args, sync_kwargs = manager.sync_all.call_args
    assert sync_args == ("remember this attempt", "")
    assert sync_kwargs["turn_metadata"]["interrupted"] is True
    assert sync_kwargs["messages"] is messages
    manager.queue_prefetch_all.assert_not_called()


def test_codex_completed_turn_surfaces_durable_capture_failure():
    """Codex must not acknowledge a turn whose required Cortex write failed."""
    agent = _make_agent()
    manager = _attach_real_memory_sync(agent)
    manager.sync_all.side_effect = MemoryDurabilityError("cortex", "turn sync")

    with pytest.raises(MemoryDurabilityError, match="cortex"):
        run_codex_app_server_turn(
            agent,
            user_message="persist this",
            original_user_message="persist this",
            messages=[{"role": "user", "content": "persist this"}],
            effective_task_id="task-1",
        )


def test_codex_native_compaction_surfaces_precompress_durability_failure():
    """Native auto-compaction cannot hide a failed Cortex boundary capture."""
    agent = _make_agent()
    turn = _make_turn()
    turn.compacted = True
    agent._codex_session.run_turn.return_value = turn
    manager = _attach_real_memory_sync(agent)
    manager.on_pre_compress.side_effect = MemoryDurabilityError(
        "cortex", "pre-compression capture"
    )

    with pytest.raises(MemoryDurabilityError, match="cortex"):
        run_codex_app_server_turn(
            agent,
            user_message="preserve this before compaction",
            original_user_message="preserve this before compaction",
            messages=[
                {"role": "user", "content": "preserve this before compaction"}
            ],
            effective_task_id="task-1",
        )

    manager.on_pre_compress.assert_called_once()
    manager.sync_all.assert_not_called()


def test_codex_transport_error_surfaces_durable_user_capture_failure():
    """Even an already-failed transport cannot hide loss of its user row."""
    agent = _make_agent()
    agent._codex_session.run_turn.side_effect = RuntimeError("subprocess died")
    manager = _attach_real_memory_sync(agent)
    manager.sync_all.side_effect = MemoryDurabilityError("cortex", "turn sync")

    with pytest.raises(MemoryDurabilityError, match="cortex"):
        run_codex_app_server_turn(
            agent,
            user_message="persist the attempt",
            original_user_message="persist the attempt",
            messages=[{"role": "user", "content": "persist the attempt"}],
            effective_task_id="task-1",
        )


class TestGatewayPersistedResolution:
    """The gateway default must preserve standard-runtime skip-db behaviour."""

    @staticmethod
    def _resolve_persistence_block(agent_result, session_db_present):
        # gateway/run.py persistence block:
        #   agent_persisted = agent_result.get("agent_persisted", self._session_db is not None)
        return agent_result.get("agent_persisted", session_db_present)

    @staticmethod
    def _resolve_passthrough(result_holder0):
        # gateway/run.py result_holder passthrough:
        #   result_holder[0].get("agent_persisted", True) if result_holder[0] else True
        return result_holder0.get("agent_persisted", True) if result_holder0 else True

    def test_codex_result_keeps_gateway_skip(self):
        # Codex now self-persists → gateway must SKIP (agent_persisted True).
        codex = {"agent_persisted": True}
        assert self._resolve_persistence_block(codex, True) is True
        assert self._resolve_persistence_block(codex, False) is True
        assert self._resolve_passthrough(codex) is True

    def test_standard_runtime_preserves_skip_db(self):
        # Standard runtime omits the key → old behaviour: skip iff DB present.
        standard = {"final_response": "ok"}
        assert self._resolve_persistence_block(standard, True) is True
        assert self._resolve_persistence_block(standard, False) is False
        assert self._resolve_passthrough(standard) is True

    def test_missing_result_holder_defaults_persisted(self):
        assert self._resolve_passthrough(None) is True
