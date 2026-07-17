"""Regression guards for durable capture of completed and interrupted turns.

Interrupted work still contains authoritative evidence that must survive a
crash or context rotation: the user's input and any completed tool results.
It must *not* persist a partial assistant stream as a completed answer, trigger
next-turn prefetch, or perform semantic interpretation.  Cortex therefore
syncs the turn with an empty assistant response and explicit interrupted
metadata; semantic consolidation remains a logical-session-end operation.

These tests exercise the helper directly on a bare ``AIAgent`` built
via ``__new__`` so the full ``run_conversation`` machinery isn't needed
— the method is pure logic and three state arguments.
"""

from unittest.mock import MagicMock

import pytest


def _bare_agent():
    """Build an ``AIAgent`` with only the attributes
    ``_sync_external_memory_for_turn`` touches — matches the bare-agent
    pattern used across ``tests/run_agent/test_interrupt_propagation.py``.
    """
    from run_agent import AIAgent

    agent = AIAgent.__new__(AIAgent)
    agent._memory_manager = MagicMock()
    # session_id is now propagated into sync_all / queue_prefetch_all so
    # providers that cache per-session state can update it mid-process
    # (see #6672).
    agent.session_id = "test_session_001"
    return agent


def _turn_metadata(*, interrupted: bool) -> dict:
    return {
        "session_id": "test_session_001",
        "turn_id": "",
        "task_id": "",
        "api_request_id": "",
        "platform": "",
        "completed": not interrupted,
        "interrupted": interrupted,
        "source_row_ids": [],
        "source_timestamps": [],
    }


class TestSyncExternalMemoryForTurn:
    # --- Interrupted evidence durability --------------------------------

    def test_interrupted_turn_captures_user_but_not_partial_assistant(self):
        """Persist the user row, but never bless the partial assistant text."""
        agent = _bare_agent()
        agent._sync_external_memory_for_turn(
            original_user_message="What time is it?",
            final_response="It is 3pm.",  # looks complete — but partial
            interrupted=True,
        )
        agent._memory_manager.sync_all.assert_called_once_with(
            "What time is it?",
            "",
            session_id="test_session_001",
            turn_metadata=_turn_metadata(interrupted=True),
        )
        agent._memory_manager.queue_prefetch_all.assert_not_called()

    def test_interrupted_turn_discards_even_seemingly_full_response(self):
        """A long, seemingly-complete assistant response is still
        partial if ``interrupted`` is True — an interrupt may have
        landed between the streamed reply and the next tool call.  The
        memory backend has no way to distinguish on its own, so we must
        gate at the source."""
        agent = _bare_agent()
        agent._sync_external_memory_for_turn(
            original_user_message="Plan a trip to Lisbon",
            final_response="Here's a detailed 7-day itinerary: [...]",
            interrupted=True,
        )
        agent._memory_manager.sync_all.assert_called_once_with(
            "Plan a trip to Lisbon",
            "",
            session_id="test_session_001",
            turn_metadata=_turn_metadata(interrupted=True),
        )
        agent._memory_manager.queue_prefetch_all.assert_not_called()

    # --- Normal completed turn still syncs ------------------------------

    def test_completed_turn_syncs_and_queues_prefetch(self):
        """Regression guard for the positive path: a normal completed
        turn must still trigger both ``sync_all`` AND
        ``queue_prefetch_all`` — otherwise the external memory backend
        never learns about anything and every user complains.
        """
        agent = _bare_agent()
        agent._sync_external_memory_for_turn(
            original_user_message="What's the weather in Paris?",
            final_response="It's sunny and 22°C.",
            interrupted=False,
        )
        agent._memory_manager.sync_all.assert_called_once_with(
            "What's the weather in Paris?",
            "It's sunny and 22°C.",
            session_id="test_session_001",
            turn_metadata=_turn_metadata(interrupted=False),
        )
        agent._memory_manager.queue_prefetch_all.assert_called_once_with(
            "What's the weather in Paris?",
            session_id="test_session_001",
        )

    def test_durable_provider_failure_is_not_reported_as_a_completed_sync(self):
        from agent.memory_manager import MemoryDurabilityError

        agent = _bare_agent()
        agent._memory_manager.sync_all.side_effect = MemoryDurabilityError(
            "cortex", "turn sync"
        )

        with pytest.raises(MemoryDurabilityError, match="cortex"):
            agent._sync_external_memory_for_turn(
                original_user_message="Persist this completed turn.",
                final_response="It is durable.",
                interrupted=False,
            )

        agent._memory_manager.queue_prefetch_all.assert_not_called()

    def test_best_effort_provider_failure_remains_non_blocking(self):
        agent = _bare_agent()
        agent._memory_manager.sync_all.side_effect = RuntimeError("offline")

        agent._sync_external_memory_for_turn(
            original_user_message="Best effort only.",
            final_response="No durable guarantee requested.",
            interrupted=False,
        )

        agent._memory_manager.queue_prefetch_all.assert_not_called()

    def test_shutdown_surfaces_durable_finalize_failure_after_cleanup(self):
        from agent.memory_manager import MemoryDurabilityError

        agent = _bare_agent()
        failure = MemoryDurabilityError("cortex", "session finalization")
        agent._memory_manager.on_session_finalize.side_effect = failure

        with pytest.raises(MemoryDurabilityError, match="cortex"):
            agent.shutdown_memory_provider(
                [{"role": "user", "content": "final transcript"}],
                finalize=True,
            )

        agent._memory_manager.on_session_end.assert_called_once_with(
            [{"role": "user", "content": "final transcript"}],
            include_durable=False,
        )
        agent._memory_manager.shutdown_all.assert_called_once()

    def test_completed_turn_syncs_messages_when_present(self):
        agent = _bare_agent()
        messages = [
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
                "content": "final Hermes-processed output",
            },
        ]

        agent._sync_external_memory_for_turn(
            original_user_message="run tests",
            final_response="tests passed",
            interrupted=False,
            messages=messages,
        )

        agent._memory_manager.sync_all.assert_called_once_with(
            "run tests",
            "tests passed",
            session_id="test_session_001",
            messages=messages,
            turn_metadata=_turn_metadata(interrupted=False),
        )

    def test_completed_skill_turn_keeps_original_message_for_memory_manager(self):
        """Provider-specific query shaping belongs inside the provider.

        The MemoryManager fan-out contract stays raw so non-OpenViking
        providers can decide for themselves whether slash-skill-expanded
        content is useful.
        """
        agent = _bare_agent()
        skill_message = (
            '[IMPORTANT: The user has invoked the "skill-creator" skill, indicating they want '
            "you to follow its instructions. The full skill content is loaded below.]\n\n"
            "# Skill Creator\n\n"
            "Large skill body that must not be searched or embedded.\n\n"
            "The user has provided the following instruction alongside the skill invocation: "
            "make a skill for release triage"
        )

        agent._sync_external_memory_for_turn(
            original_user_message=skill_message,
            final_response="Done.",
            interrupted=False,
        )

        agent._memory_manager.sync_all.assert_called_once_with(
            skill_message,
            "Done.",
            session_id="test_session_001",
            turn_metadata=_turn_metadata(interrupted=False),
        )
        agent._memory_manager.queue_prefetch_all.assert_called_once_with(
            skill_message,
            session_id="test_session_001",
        )

    # --- Edge cases (pre-existing behaviour preserved) ------------------

    def test_no_final_response_skips(self):
        """If the model produced no final_response (e.g. tool-only turn
        that never resolved), we must not fabricate an empty sync."""
        agent = _bare_agent()
        agent._sync_external_memory_for_turn(
            original_user_message="Hello",
            final_response=None,
            interrupted=False,
        )
        agent._memory_manager.sync_all.assert_not_called()

    def test_no_original_user_message_skips(self):
        """No user-origin message means this wasn't a user turn (e.g.
        a system-initiated refresh).  Don't sync an assistant-only
        exchange as if a user said something."""
        agent = _bare_agent()
        agent._sync_external_memory_for_turn(
            original_user_message=None,
            final_response="Proactive notification text",
            interrupted=False,
        )
        agent._memory_manager.sync_all.assert_not_called()

    def test_no_memory_manager_is_a_no_op(self):
        """Sessions without an external memory manager must not crash
        or try to call .sync_all on None."""
        from run_agent import AIAgent

        agent = AIAgent.__new__(AIAgent)
        agent._memory_manager = None

        # Must not raise.
        agent._sync_external_memory_for_turn(
            original_user_message="hi",
            final_response="hey",
            interrupted=False,
        )

    # --- Exception safety ----------------------------------------------

    def test_sync_exception_is_swallowed(self):
        """External memory providers are best-effort; a misconfigured
        or offline backend must not block the user from seeing their
        response by propagating the exception up."""
        agent = _bare_agent()
        agent._memory_manager.sync_all.side_effect = RuntimeError("backend unreachable")

        # Must not raise.
        agent._sync_external_memory_for_turn(
            original_user_message="hi",
            final_response="hey",
            interrupted=False,
        )
        # sync_all was attempted.
        agent._memory_manager.sync_all.assert_called_once()

    def test_prefetch_exception_is_swallowed(self):
        """Same best-effort contract applies to the prefetch step — a
        failure in queue_prefetch_all must not bubble out."""
        agent = _bare_agent()
        agent._memory_manager.queue_prefetch_all.side_effect = RuntimeError(
            "prefetch worker dead"
        )

        # Must not raise.
        agent._sync_external_memory_for_turn(
            original_user_message="hi",
            final_response="hey",
            interrupted=False,
        )
        # sync_all still happened before the prefetch blew up.
        agent._memory_manager.sync_all.assert_called_once()

    # --- Multimodal content flattening ----------------------------------

    def test_multimodal_user_message_is_flattened(self):
        """A turn with an attached image carries the user message as a
        list of typed parts.  Providers feed the content to regexes
        (sanitize_context), so a raw list raised ``expected string or
        bytes-like object, got 'list'`` and the turn silently never
        synced.  The boundary must flatten to text first."""
        agent = _bare_agent()
        agent._sync_external_memory_for_turn(
            original_user_message=[
                {"type": "text", "text": "what is in this screenshot?"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,abc"},
                },
            ],
            final_response="A terminal window showing a stack trace.",
            interrupted=False,
        )
        agent._memory_manager.sync_all.assert_called_once_with(
            "[1 image] what is in this screenshot?",
            "A terminal window showing a stack trace.",
            session_id="test_session_001",
            turn_metadata=_turn_metadata(interrupted=False),
        )
        agent._memory_manager.queue_prefetch_all.assert_called_once_with(
            "[1 image] what is in this screenshot?",
            session_id="test_session_001",
        )

    def test_multimodal_response_is_flattened(self):
        agent = _bare_agent()
        agent._sync_external_memory_for_turn(
            original_user_message="describe it",
            final_response=[{"type": "text", "text": "a cat"}],
            interrupted=False,
        )
        agent._memory_manager.sync_all.assert_called_once_with(
            "describe it",
            "a cat",
            session_id="test_session_001",
            turn_metadata=_turn_metadata(interrupted=False),
        )

    def test_multimodal_with_no_text_at_all_skips(self):
        """Unknown-typed parts flatten to an empty string — don't sync a
        turn with no recoverable text."""
        agent = _bare_agent()
        agent._sync_external_memory_for_turn(
            original_user_message=[{"type": "audio", "data": "..."}],
            final_response="noted",
            interrupted=False,
        )
        agent._memory_manager.sync_all.assert_not_called()
        agent._memory_manager.queue_prefetch_all.assert_not_called()

    # --- The specific matrix the reporter asked about ------------------

    @pytest.mark.parametrize(
        "interrupted,final,user,expect_sync",
        [
            (False, "resp", "user", True),  # normal completed → sync
            (True, "resp", "user", True),  # interrupted → user evidence only
            (False, None, "user", False),  # no response → skip
            (False, "resp", None, False),  # no user msg → skip
            (True, None, "user", True),  # interrupted user evidence is sufficient
            (True, "resp", None, False),  # interrupted + no user → skip
            (False, None, None, False),  # nothing → skip
            (True, None, None, False),  # interrupted + nothing → skip
        ],
    )
    def test_sync_matrix(self, interrupted, final, user, expect_sync):
        agent = _bare_agent()
        agent._sync_external_memory_for_turn(
            original_user_message=user,
            final_response=final,
            interrupted=interrupted,
        )
        if expect_sync:
            agent._memory_manager.sync_all.assert_called_once()
            if interrupted:
                assert agent._memory_manager.sync_all.call_args.args[1] == ""
                assert agent._memory_manager.sync_all.call_args.kwargs[
                    "turn_metadata"
                ] == _turn_metadata(interrupted=True)
                agent._memory_manager.queue_prefetch_all.assert_not_called()
            else:
                agent._memory_manager.queue_prefetch_all.assert_called_once()
        else:
            agent._memory_manager.sync_all.assert_not_called()
            agent._memory_manager.queue_prefetch_all.assert_not_called()
