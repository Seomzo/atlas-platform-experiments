"""Gateway Cortex boundaries when the soft agent cache has no provider."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource
from gateway.slash_commands import GatewaySlashCommandsMixin


class _Runner(GatewaySlashCommandsMixin):
    def __init__(self, *, source: SessionSource) -> None:
        self.config = GatewayConfig(
            platforms={
                Platform.TELEGRAM: PlatformConfig(enabled=True, token="***"),
            },
        )
        self.config.multiplex_profiles = True
        self.adapters = {}
        self.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
        self._session_model_overrides = {}
        self._pending_model_notes = {}
        self._background_tasks = set()
        self._running_agents = {}
        self._pending_messages = {}
        self._pending_approvals = {}
        self._session_db = None
        self._agent_cache = {}
        self._agent_cache_lock = None
        self._queued_events = {}
        self.evicted: list[str] = []

        self.session_key = f"agent:{source.profile}:telegram:dm:{source.chat_id}"
        self.old_entry = SessionEntry(
            session_key=self.session_key,
            session_id="sess-old",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            origin=source,
            platform=Platform.TELEGRAM,
            chat_type="dm",
        )
        self.new_entry = SessionEntry(
            session_key=self.session_key,
            session_id="sess-new",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            origin=source,
            platform=Platform.TELEGRAM,
            chat_type="dm",
        )
        self.session_store = MagicMock()
        self.session_store._entries = {self.session_key: self.old_entry}
        self.session_store._generate_session_key.return_value = self.session_key
        self.session_store.get_or_create_session.return_value = self.old_entry
        self.session_store.reset_session.return_value = self.new_entry

    def _invalidate_session_run_generation(self, *_args, **_kwargs) -> None:
        return None

    def _session_key_for_source(self, _source: SessionSource) -> str:
        return self.session_key

    def _release_running_agent_state(self, *_args, **_kwargs) -> None:
        return None

    def _evict_cached_agent(self, session_key: str) -> None:
        self.evicted.append(session_key)
        self._agent_cache.pop(session_key, None)

    def _set_session_reasoning_override(self, *_args, **_kwargs) -> None:
        return None

    def _clear_session_boundary_security_state(self, *_args, **_kwargs) -> None:
        return None

    def _reset_notice_session_info(self, _source: SessionSource) -> str:
        return ""

    def _telegram_topic_new_header(self, _source: SessionSource) -> str:
        return ""

    def _is_telegram_topic_lane(self, _source: SessionSource) -> bool:
        return False

    def _resolve_profile_home_for_source(self, source: SessionSource) -> Path:
        return Path("/profiles") / str(source.profile)

    async def _run_in_executor_with_context(self, func, *args):
        return func(*args)


def _source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="customer-1",
        chat_id="chat-1",
        chat_type="dm",
        profile="dealer-a",
    )


def _event(text: str) -> MessageEvent:
    return MessageEvent(text=text, source=_source(), message_id="message-1")


@pytest.mark.asyncio
async def test_reset_finalizes_detached_cortex_in_routed_profile() -> None:
    runner = _Runner(source=_source())

    with patch(
        "gateway.slash_commands._finalize_detached_cortex_boundary",
        return_value=True,
    ) as finalize:
        await runner._handle_reset_command(_event("/new"))

    finalize.assert_called_once_with(
        Path("/profiles/dealer-a"),
        "sess-old",
        "new_session",
    )
    assert runner.evicted == [runner.session_key]
    runner.session_store.reset_session.assert_called_once_with(
        runner.session_key,
        expected_session_id="sess-old",
        defer_memory_finalize_reason="new_session",
        end_reason="session_reset",
    )


@pytest.mark.asyncio
async def test_reset_route_cas_precedes_semantic_boundary() -> None:
    runner = _Runner(source=_source())
    order: list[str] = []

    def reset_route(*_args, **_kwargs):
        order.append("route")
        return runner.new_entry

    runner.session_store.reset_session.side_effect = reset_route
    with patch(
        "gateway.slash_commands._finalize_detached_cortex_boundary",
        side_effect=lambda *_args: order.append("semantic") or True,
    ):
        await runner._handle_reset_command(_event("/new"))

    assert order[:2] == ["route", "semantic"]


@pytest.mark.asyncio
async def test_semantic_boundary_waits_for_executor_without_wait_for_timeout() -> None:
    runner = _Runner(source=_source())

    with (
        patch(
            "gateway.slash_commands._finalize_detached_cortex_boundary",
            return_value=True,
        ),
        patch(
            "gateway.slash_commands.asyncio.wait_for",
            side_effect=AssertionError("semantic work must not be timeout-detached"),
        ),
    ):
        owned = await runner._commit_memory_boundary_before_session_switch(
            source=_source(),
            session_key=runner.session_key,
            session_entry=runner.old_entry,
            reason="new_session",
            require_cached_session_match=True,
        )

    assert owned is True


@pytest.mark.asyncio
async def test_reset_is_fail_closed_when_detached_cortex_finalization_fails() -> None:
    runner = _Runner(source=_source())

    with patch(
        "gateway.slash_commands._finalize_detached_cortex_boundary",
        side_effect=OSError("simulated full Cortex database"),
    ):
        result = await runner._handle_reset_command(_event("/new"))

    assert "new session is reserved" in str(result).lower()
    assert runner.evicted == [runner.session_key]
    runner.session_store.reset_session.assert_called_once()
    runner.session_store.clear_previous_session_id.assert_not_called()


@pytest.mark.asyncio
async def test_reset_is_fail_closed_when_required_detached_boundary_is_missing() -> None:
    """A clean ``False`` is a required-Cortex failure, not a no-op success."""

    runner = _Runner(source=_source())

    with patch(
        "gateway.slash_commands._finalize_detached_cortex_boundary",
        return_value=False,
    ):
        result = await runner._handle_reset_command(_event("/new"))

    assert "new session is reserved" in str(result).lower()
    assert runner.evicted == [runner.session_key]
    runner.session_store.reset_session.assert_called_once()
    runner.session_store.clear_previous_session_id.assert_not_called()


@pytest.mark.asyncio
async def test_reset_does_not_force_create_after_session_cas_refusal() -> None:
    runner = _Runner(source=_source())
    runner.session_store.reset_session.return_value = None

    with patch(
        "gateway.slash_commands._finalize_detached_cortex_boundary",
        return_value=True,
    ) as finalize:
        result = await runner._handle_reset_command(_event("/new"))

    assert "route changed" in str(result)
    runner.session_store.reset_session.assert_called_once_with(
        runner.session_key,
        expected_session_id="sess-old",
        defer_memory_finalize_reason="new_session",
        end_reason="session_reset",
    )
    finalize.assert_not_called()
    runner.session_store.get_or_create_session.assert_not_called()


@pytest.mark.asyncio
async def test_live_branch_boundary_atomically_prepares_cortex_parent_lineage() -> None:
    runner = _Runner(source=_source())
    manager = MagicMock()
    cached_agent = SimpleNamespace(
        _memory_manager=manager,
        _session_messages=[{"role": "user", "content": "branch this"}],
    )
    runner._agent_cache[runner.session_key] = cached_agent

    owned = await runner._commit_memory_boundary_before_session_switch(
        source=_source(),
        session_key=runner.session_key,
        session_entry=runner.old_entry,
        reason="branch",
        new_session_id="branch-session",
        parent_session_id="sess-old",
        reset=True,
    )

    assert owned is True
    manager.on_session_switch.assert_called_once_with(
        "branch-session",
        parent_session_id="sess-old",
        reset=False,
        reason="branch",
    )
    manager.commit_session_boundary_async.assert_not_called()
    manager.flush_pending.assert_called_once_with(timeout=10)


@pytest.mark.asyncio
async def test_detached_branch_boundary_preserves_parent_lineage_arguments() -> None:
    runner = _Runner(source=_source())

    with patch(
        "gateway.slash_commands._prepare_detached_cortex_branch",
        return_value=True,
    ) as prepare:
        owned = await runner._commit_memory_boundary_before_session_switch(
            source=_source(),
            session_key=runner.session_key,
            session_entry=runner.old_entry,
            reason="branch",
            new_session_id="branch-session",
            parent_session_id="sess-old",
            reset=True,
        )

    assert owned is True
    prepare.assert_called_once_with(
        Path("/profiles/dealer-a"),
        "sess-old",
        "branch-session",
        "sess-old",
        "branch",
        True,
    )


@pytest.mark.asyncio
async def test_resume_preflights_detached_cortex_without_finalizing_source() -> None:
    runner = _Runner(source=_source())
    manager = MagicMock()
    cached_agent = SimpleNamespace(
        session_id="sess-old",
        _memory_manager=manager,
        _session_messages=[{"role": "user", "content": "leave this open"}],
    )
    runner._agent_cache[runner.session_key] = cached_agent

    with patch(
        "gateway.slash_commands._prepare_detached_cortex_resume",
        return_value=True,
    ) as prepare:
        owned = await runner._commit_memory_boundary_before_session_switch(
            source=_source(),
            session_key=runner.session_key,
            session_entry=runner.old_entry,
            reason="resume",
            new_session_id="existing-session",
            reset=False,
        )

    assert owned is True
    prepare.assert_called_once_with(
        Path("/profiles/dealer-a"),
        "sess-old",
        "existing-session",
        "",
        "resume",
        False,
    )
    manager.on_session_switch.assert_not_called()
    manager.commit_session_boundary_async.assert_not_called()
    manager.on_session_finalize.assert_not_called()


@pytest.mark.asyncio
async def test_unpublished_branch_compensation_restores_source_and_hides_child(
    tmp_path,
) -> None:
    from altas.cortex.runtime import open_cortex_store

    home = tmp_path / "dealer-a"
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(
        "memory:\n  provider: cortex\ncortex:\n  enabled: true\n",
        encoding="utf-8",
    )
    store, _config = open_cortex_store(home, {})
    store.ensure_session("sess-old")
    store.ensure_session(
        "branch-session",
        parent_session_id="sess-old",
        logical_conversation_id="branch-session",
    )

    runner = _Runner(source=_source())
    runner._resolve_profile_home_for_source = lambda _source: home
    manager = MagicMock()
    cached_agent = SimpleNamespace(
        _memory_manager=manager,
        _memory_branch_binding=("sess-old", "branch-session"),
    )
    runner._agent_cache[runner.session_key] = cached_agent

    compensated = await runner._compensate_unpublished_memory_branch(
        source=_source(),
        session_key=runner.session_key,
        source_session_id="sess-old",
        child_session_id="branch-session",
    )

    assert compensated is True
    manager.on_session_switch.assert_called_once_with(
        "sess-old",
        parent_session_id="",
        reset=False,
        reason="resume",
    )
    assert not hasattr(cached_agent, "_memory_branch_binding")
    assert store.session_lineage("sess-old")["state"] == "active"
    assert store.session_lineage("branch-session")["state"] == "deleted"
    with store.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM session_distill_admissions WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM cognitive_jobs "
            "WHERE brain_id=? AND job_type='session_distill'",
            (store.brain_id,),
        ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_undo_reconciles_detached_cortex_and_evicts_profile_cache() -> None:
    runner = _Runner(source=_source())
    runner.session_store.rewind_session.return_value = {
        "rewound_count": 2,
        "rewound_message_ids": [41, 42],
        "turns_undone": 1,
        "target_text": "customer correction",
    }

    with patch(
        "gateway.slash_commands._reconcile_detached_cortex_rewind",
        return_value=True,
    ) as reconcile:
        await runner._handle_undo_command(_event("/undo"))

    reconcile.assert_called_once_with(
        Path("/profiles/dealer-a"),
        "sess-old",
        [41, 42],
    )
    assert runner.evicted == [runner.session_key]


@pytest.mark.asyncio
async def test_undo_falls_back_to_detached_reconcile_when_provider_hook_fails() -> None:
    runner = _Runner(source=_source())
    runner.session_store.rewind_session.return_value = {
        "rewound_count": 1,
        "rewound_message_ids": [99],
        "turns_undone": 1,
        "target_text": "rewind this",
    }
    manager = MagicMock()
    manager.on_session_switch.side_effect = RuntimeError("provider unavailable")
    runner._agent_cache[runner.session_key] = SimpleNamespace(
        _memory_manager=manager,
    )

    with patch(
        "gateway.slash_commands._reconcile_detached_cortex_rewind",
        return_value=True,
    ) as reconcile:
        await runner._handle_undo_command(_event("/undo"))

    reconcile.assert_called_once_with(
        Path("/profiles/dealer-a"),
        "sess-old",
        [99],
    )
    assert runner.evicted == [runner.session_key]


@pytest.mark.asyncio
async def test_undo_compensates_transcript_when_cortex_reconciliation_fails() -> None:
    runner = _Runner(source=_source())
    runner.old_entry.last_prompt_tokens = 321
    runner.session_store.rewind_session.return_value = {
        "rewound_count": 2,
        "rewound_message_ids": [71, 72],
        "turns_undone": 1,
        "target_text": "do not lose this",
    }
    runner.session_store.restore_rewind.return_value = True

    with patch(
        "gateway.slash_commands._reconcile_detached_cortex_rewind",
        side_effect=OSError("simulated Cortex write failure"),
    ):
        result = await runner._handle_undo_command(_event("/undo"))

    assert "left unchanged" in result
    runner.session_store.restore_rewind.assert_called_once_with("sess-old", [71, 72])
    assert runner.evicted == []
    assert runner.old_entry.last_prompt_tokens == 321


@pytest.mark.asyncio
async def test_undo_does_not_ignore_failed_cached_durable_reconciliation() -> None:
    runner = _Runner(source=_source())
    runner.session_store.rewind_session.return_value = {
        "rewound_count": 1,
        "rewound_message_ids": [88],
        "turns_undone": 1,
        "target_text": "keep me",
    }
    runner.session_store.restore_rewind.return_value = True
    manager = MagicMock()
    manager.on_session_switch.side_effect = RuntimeError("durable provider failed")
    runner._agent_cache[runner.session_key] = SimpleNamespace(
        _memory_manager=manager,
    )

    with patch(
        "gateway.slash_commands._reconcile_detached_cortex_rewind",
        return_value=False,
    ):
        result = await runner._handle_undo_command(_event("/undo"))

    assert "left unchanged" in result
    runner.session_store.restore_rewind.assert_called_once_with("sess-old", [88])
    assert runner.evicted == []
