"""Durable Cortex boundary for compression-exhaustion auto-reset."""

from __future__ import annotations

import ast
import asyncio
import inspect
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.config import Platform
from gateway.run import GatewayRunner
from gateway.session import SessionEntry, SessionSource
from gateway import run as gateway_run


def _source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="chat-1",
        chat_type="dm",
        user_id="customer-1",
        profile="dealer-a",
    )


def _entry(session_id: str) -> SessionEntry:
    now = datetime.now()
    return SessionEntry(
        session_key="agent:dealer-a:telegram:dm:chat-1",
        session_id=session_id,
        created_at=now,
        updated_at=now,
        origin=_source(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )


def _runner() -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner.session_store = MagicMock()
    runner._agent_cache = {}
    runner._agent_cache_lock = None
    runner._session_model_overrides = {"key": {"model": "old"}}
    runner._pending_model_notes = {"key": "old-note"}
    runner._last_resolved_model = {"key": "old-model"}
    runner._set_session_reasoning_override = MagicMock()
    runner._evict_cached_agent = MagicMock()
    runner._sync_telegram_topic_binding = MagicMock()
    runner._resolve_profile_home_for_source = MagicMock(
        return_value=Path("/profiles/dealer-a")
    )

    async def run_inline(func, *args):
        return func(*args)

    runner._run_in_executor_with_context = run_inline
    return runner


def test_live_memory_is_finalized_and_flushed_before_reset() -> None:
    runner = _runner()
    old_entry = _entry("oversized-child")
    new_entry = _entry("fresh-session")
    key = old_entry.session_key
    events: list[str] = []
    messages = [{"role": "user", "content": "oversized conversation"}]
    manager = MagicMock()
    manager.on_session_finalize.side_effect = lambda *_args, **_kwargs: events.append(
        "finalize"
    )
    manager.flush_pending.side_effect = lambda **_kwargs: events.append("flush")
    runner._agent_cache[key] = SimpleNamespace(
        session_id=old_entry.session_id,
        _memory_manager=manager,
        _session_messages=messages,
    )
    runner.session_store.reset_session.side_effect = lambda _key, **_kwargs: (
        events.append("reset") or new_entry
    )
    runner._evict_cached_agent.side_effect = lambda _key: events.append("evict")
    runner._session_model_overrides = {key: {"model": "old"}}
    runner._pending_model_notes = {key: "old-note"}
    runner._last_resolved_model = {key: "old-model"}

    result = asyncio.run(
        runner._reset_session_after_compression_exhaustion(
            source=_source(),
            session_entry=old_entry,
            session_key=key,
        )
    )

    assert result is new_entry
    assert events[:4] == ["reset", "finalize", "flush", "evict"]
    manager.on_session_finalize.assert_called_once_with(
        messages,
        reason="compression_exhausted",
    )
    manager.flush_pending.assert_called_once_with(timeout=10)
    assert key not in runner._session_model_overrides
    assert key not in runner._pending_model_notes
    assert key not in runner._last_resolved_model
    runner._sync_telegram_topic_binding.assert_called_once_with(
        _source(),
        new_entry,
        reason="compression-exhausted-reset",
    )


def test_detached_boundary_can_own_exhausted_reset() -> None:
    runner = _runner()
    old_entry = _entry("detached-oversized")
    new_entry = _entry("fresh-session")
    key = old_entry.session_key
    runner.session_store.reset_session.return_value = new_entry

    with patch(
        "gateway.slash_commands._finalize_detached_cortex_boundary",
        return_value=True,
    ) as finalize:
        result = asyncio.run(
            runner._reset_session_after_compression_exhaustion(
                source=_source(),
                session_entry=old_entry,
                session_key=key,
            )
        )

    assert result is new_entry
    finalize.assert_called_once_with(
        Path("/profiles/dealer-a"),
        "detached-oversized",
        "compression_exhausted",
    )
    runner.session_store.reset_session.assert_called_once_with(
        key,
        expected_session_id="detached-oversized",
        defer_memory_finalize_reason="compression_exhausted",
        end_reason="compression_exhausted",
    )


def test_failed_boundary_keeps_new_route_blocked_for_retry() -> None:
    runner = _runner()
    old_entry = _entry("oversized-session")
    new_entry = _entry("fresh-session")
    key = old_entry.session_key
    runner.session_store.reset_session.return_value = new_entry
    runner._session_model_overrides = {key: {"model": "old"}}
    runner._pending_model_notes = {key: "old-note"}
    runner._last_resolved_model = {key: "old-model"}
    runner._commit_memory_boundary_before_session_switch = AsyncMock(return_value=False)

    result = asyncio.run(
        runner._reset_session_after_compression_exhaustion(
            source=_source(),
            session_entry=old_entry,
            session_key=key,
        )
    )

    assert result is new_entry
    runner.session_store.reset_session.assert_called_once_with(
        key,
        expected_session_id="oversized-session",
        defer_memory_finalize_reason="compression_exhausted",
        end_reason="compression_exhausted",
    )
    runner.session_store.clear_previous_session_id.assert_not_called()
    runner._evict_cached_agent.assert_called_once_with(key)
    runner._set_session_reasoning_override.assert_called_once_with(key, None)
    runner._sync_telegram_topic_binding.assert_called_once()
    assert key not in runner._session_model_overrides
    assert key not in runner._pending_model_notes
    assert key not in runner._last_resolved_model


def test_guarded_reset_is_only_dispatched_for_compression_exhaustion() -> None:
    """Ordinary compression rotation remains a non-semantic continuation."""

    tree = ast.parse(inspect.getsource(gateway_run))
    guarded_calls = []
    guarded_parent_tests = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.if_stack: list[ast.If] = []

        def visit_If(self, node: ast.If) -> None:  # noqa: N802
            self.if_stack.append(node)
            self.generic_visit(node)
            self.if_stack.pop()

        def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "_reset_session_after_compression_exhaustion"
            ):
                guarded_calls.append(node)
                guarded_parent_tests.extend(parent.test for parent in self.if_stack)
            self.generic_visit(node)

    Visitor().visit(tree)

    assert len(guarded_calls) == 1
    assert any(
        "compression_exhausted"
        in {
            value.value
            for value in ast.walk(test)
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
        }
        for test in guarded_parent_tests
    )
