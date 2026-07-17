"""Regression coverage for inbound auto-reset Cortex boundaries."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import (
    GatewayConfig,
    Platform,
    PlatformConfig,
    SessionResetPolicy,
)
from gateway.session import SessionEntry, SessionSource, SessionStore
from gateway.slash_commands import GatewaySlashCommandsMixin


def _source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="chat-1",
        chat_type="dm",
        user_id="customer-1",
        profile="dealer-a",
    )


def _entry(*, previous_session_id: str | None = "sess-old") -> SessionEntry:
    now = datetime.now()
    return SessionEntry(
        session_key="agent:dealer-a:telegram:dm:chat-1",
        session_id="sess-new",
        created_at=now,
        updated_at=now,
        origin=_source(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
        was_auto_reset=True,
        previous_session_id=previous_session_id,
    )


def _store(tmp_path) -> SessionStore:
    return SessionStore(
        sessions_dir=tmp_path,
        config=GatewayConfig(
            default_reset_policy=SessionResetPolicy(
                mode="idle",
                idle_minutes=1,
            )
        ),
    )


def test_auto_reset_persists_previous_session_id_until_acknowledged(tmp_path) -> None:
    store = _store(tmp_path)
    source = _source()
    expired = store.get_or_create_session(source)
    expired_id = expired.session_id
    expired.updated_at = datetime.now() - timedelta(minutes=5)
    store._save()

    fresh = store.get_or_create_session(source)

    assert fresh.session_id != expired_id
    assert fresh.previous_session_id == expired_id

    # A restart must retain the exact prior identity even if the presentation
    # flag was already consumed by a slash-command path.
    fresh.was_auto_reset = False
    store._save()
    store._loaded = False
    store._entries.clear()
    store._ensure_loaded()
    reloaded = store._entries[fresh.session_key]
    assert reloaded.was_auto_reset is False
    assert reloaded.previous_session_id == expired_id

    assert store.clear_previous_session_id(
        reloaded.session_key,
        current_session_id=reloaded.session_id,
        previous_session_id=expired_id,
    )
    assert reloaded.previous_session_id is None


def test_pending_boundary_survives_a_second_idle_window(tmp_path) -> None:
    store = _store(tmp_path)
    source = _source()
    expired = store.get_or_create_session(source)
    original_id = expired.session_id
    expired.updated_at = datetime.now() - timedelta(minutes=5)
    store._save()

    fresh = store.get_or_create_session(source)
    fresh_id = fresh.session_id
    assert fresh.previous_session_id == original_id

    # Simulate a long outage after the first boundary failed. A second reset
    # must not overwrite the only durable pointer to the original session.
    fresh.updated_at = datetime.now() - timedelta(minutes=5)
    store._save()
    retried = store.get_or_create_session(source)

    assert retried.session_id == fresh_id
    assert retried.previous_session_id == original_id


def test_ended_fresh_row_preserves_pending_boundary_across_restart(tmp_path) -> None:
    store = _store(tmp_path)
    source = _source()
    expired = store.get_or_create_session(source)
    original_id = expired.session_id
    expired.updated_at = datetime.now() - timedelta(minutes=5)
    store._save()

    fresh = store.get_or_create_session(source)
    assert fresh.previous_session_id == original_id
    assert store._db is not None
    store._db.end_session(fresh.session_id, "agent_close")

    restarted = _store(tmp_path)
    preserved = restarted.get_or_create_session(source)

    assert preserved.session_id == fresh.session_id
    assert preserved.previous_session_id == original_id


def test_previous_session_id_rejects_path_traversal() -> None:
    payload = _entry().to_dict()
    payload["previous_session_id"] = "../another-profile/session"

    with pytest.raises(ValueError, match="previous_session_id"):
        SessionEntry.from_dict(payload)


def test_routing_replacement_refuses_pending_previous_boundary(tmp_path) -> None:
    store = _store(tmp_path)
    current = store.get_or_create_session(_source())
    current.previous_session_id = "sess-still-pending"
    store._save()

    assert store.switch_session(current.session_key, "resume-target") is None
    assert store.reset_session(current.session_key) is None
    assert store._entries[current.session_key] is current
    assert current.previous_session_id == "sess-still-pending"


def test_routing_replacement_rejects_stale_expected_session_id(tmp_path) -> None:
    store = _store(tmp_path)
    current = store.get_or_create_session(_source())
    current_id = current.session_id

    assert (
        store.reset_session(
            current.session_key,
            expected_session_id="session-that-lost-the-race",
        )
        is None
    )
    assert (
        store.switch_session(
            current.session_key,
            "resume-target",
            expected_session_id="session-that-lost-the-race",
        )
        is None
    )

    assert store.peek_session_id(current.session_key) == current_id
    assert store._db is not None
    row = store._db.get_session(current_id)
    assert row is not None
    assert row["ended_at"] is None


def test_explicit_reset_publishes_durable_predecessor_before_finalize(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    current = store.get_or_create_session(_source())

    fresh = store.reset_session(
        current.session_key,
        expected_session_id=current.session_id,
        defer_memory_finalize_reason="new_session",
        end_reason="session_reset",
    )

    assert fresh is not None
    assert fresh.previous_session_id == current.session_id
    assert fresh.previous_finalize_reason == "new_session"
    assert store._db is not None
    ended = store._db.get_session(current.session_id)
    assert ended is not None
    assert ended["end_reason"] == "session_reset"

    store._loaded = False
    store._entries.clear()
    store._ensure_loaded()
    reloaded = store._entries[current.session_key]
    assert reloaded.previous_session_id == current.session_id
    assert reloaded.previous_finalize_reason == "new_session"

    assert store.clear_previous_session_id(
        reloaded.session_key,
        current_session_id=reloaded.session_id,
        previous_session_id=current.session_id,
    )
    assert reloaded.previous_session_id is None
    assert reloaded.previous_finalize_reason is None


class _BoundaryRunner(GatewaySlashCommandsMixin):
    profile_home = Path("/profiles/dealer-a")

    def _resolve_profile_home_for_source(self, _source: SessionSource) -> Path:
        return self.profile_home

    async def _run_in_executor_with_context(self, func, *args):
        return func(*args)


def test_pending_boundary_targets_prior_id_then_clears_retry_token() -> None:
    runner = _BoundaryRunner()
    runner.session_store = MagicMock()
    runner.session_store.clear_previous_session_id.return_value = True
    runner._commit_memory_boundary_before_session_switch = AsyncMock(return_value=True)
    entry = _entry()

    assert asyncio.run(
        runner._finalize_previous_session_before_turn(
            source=_source(),
            session_entry=entry,
        )
    )

    boundary_entry = (
        runner._commit_memory_boundary_before_session_switch.call_args.kwargs[
            "session_entry"
        ]
    )
    assert boundary_entry.session_id == "sess-old"
    assert boundary_entry.previous_session_id is None
    runner.session_store.clear_previous_session_id.assert_called_once_with(
        entry.session_key,
        current_session_id="sess-new",
        previous_session_id="sess-old",
    )


def test_failed_pending_boundary_keeps_retry_token() -> None:
    runner = _BoundaryRunner()
    runner.session_store = MagicMock()
    runner._commit_memory_boundary_before_session_switch = AsyncMock(return_value=False)
    entry = _entry()

    assert not asyncio.run(
        runner._finalize_previous_session_before_turn(
            source=_source(),
            session_entry=entry,
        )
    )

    assert entry.previous_session_id == "sess-old"
    runner.session_store.clear_previous_session_id.assert_not_called()


def test_cached_current_agent_cannot_finalize_pending_predecessor(
    tmp_path, monkeypatch
) -> None:
    from altas.cortex.config import CortexConfig
    from altas.cortex.store import CortexStore

    config = CortexConfig.from_mapping(
        {
            "cortex": {
                "enabled": True,
                "capture": {"enabled": False},
                "dream": {"enabled": False},
            }
        },
        tmp_path,
    )
    store = CortexStore(config.database_path, owner_customer_id="customer-a")
    store.initialize()
    store.ensure_session("sess-old")
    monkeypatch.setattr(
        "altas.cortex.lifecycle.CortexConfig.load",
        lambda _home: config,
    )
    monkeypatch.setattr(
        "altas.cortex.lifecycle.open_cortex_store",
        lambda _home, _identity: (store, config),
    )

    runner = _BoundaryRunner()
    runner.profile_home = tmp_path
    runner.session_store = MagicMock()
    runner.session_store.clear_previous_session_id.return_value = True
    manager = MagicMock()
    runner._agent_cache = {
        _entry().session_key: SimpleNamespace(
            session_id="sess-new",
            _memory_manager=manager,
            _session_messages=[{"role": "user", "content": "current turn"}],
        )
    }
    runner._agent_cache_lock = None

    assert asyncio.run(
        runner._finalize_previous_session_before_turn(
            source=_source(),
            session_entry=_entry(),
        )
    )

    manager.on_session_finalize.assert_not_called()
    assert store.session_lineage("sess-old")["state"] == "finalized"


def test_inbound_turn_fails_closed_before_routing_or_cache_work() -> None:
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    entry = _entry()
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = entry
    runner._recover_telegram_topic_thread_id = MagicMock(return_value=None)
    runner._finalize_previous_session_before_turn = AsyncMock(return_value=False)
    runner._cache_session_source = MagicMock()
    runner._evict_cached_agent = MagicMock()
    event = SimpleNamespace(text="new customer request", source=_source())

    result = asyncio.run(
        runner._handle_message_with_agent(
            event,
            event.source,
            entry.session_key,
            1,
        )
    )

    assert "didn't process this message" in result
    runner._cache_session_source.assert_not_called()
    runner._evict_cached_agent.assert_not_called()


def test_inbound_re_resolves_session_after_clearing_pending_boundary() -> None:
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    pending = _entry()
    healed = _entry(previous_session_id=None)
    healed.session_id = "sess-healed"
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.side_effect = [pending, healed]
    # Stop on the second preflight so the large downstream turn pipeline is
    # irrelevant; reaching it proves the post-clear routing re-resolution.
    runner._finalize_previous_session_before_turn = AsyncMock(side_effect=[True, False])
    runner._recover_telegram_topic_thread_id = MagicMock(return_value=None)
    runner._cache_session_source = MagicMock()
    event = SimpleNamespace(text="new customer request", source=_source())

    result = asyncio.run(
        runner._handle_message_with_agent(
            event,
            event.source,
            pending.session_key,
            1,
        )
    )

    assert "didn't process this message" in result
    assert runner.session_store.get_or_create_session.call_count == 2
    assert (
        runner._finalize_previous_session_before_turn.call_args_list[1].kwargs[
            "session_entry"
        ]
        is healed
    )
    runner._cache_session_source.assert_not_called()


def test_handoff_cannot_switch_away_from_pending_boundary() -> None:
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    adapter = SimpleNamespace(create_handoff_thread=AsyncMock(return_value=None))
    home = SimpleNamespace(chat_id="-10001", thread_id=None, name="Home")
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner.config = SimpleNamespace(
        get_home_channel=lambda _platform: home,
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, extra={})},
    )
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = _entry()
    runner._finalize_previous_session_before_turn = AsyncMock(return_value=False)

    with pytest.raises(RuntimeError, match="prior Atlas memory boundary"):
        asyncio.run(
            runner._process_handoff({
                "id": "cli-session",
                "title": "CLI work",
                "handoff_platform": "telegram",
            })
        )

    runner.session_store.switch_session.assert_not_called()


def test_handoff_destination_rebind_is_topology_only() -> None:
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    adapter = SimpleNamespace(create_handoff_thread=AsyncMock(return_value=None))
    home = SimpleNamespace(chat_id="-10001", thread_id=None, name="Home")
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner.config = SimpleNamespace(
        get_home_channel=lambda _platform: home,
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, extra={})},
    )
    runner.session_store = MagicMock()
    destination = _entry(previous_session_id=None)
    runner.session_store.get_or_create_session.return_value = destination
    runner._finalize_previous_session_before_turn = AsyncMock(return_value=True)
    runner._commit_memory_boundary_before_session_switch = AsyncMock()
    switched = _entry(previous_session_id=None)
    switched.session_id = "cli-session"
    runner._rebind_existing_session_route = MagicMock(return_value=switched)
    runner._release_running_agent_state = MagicMock()
    runner._handle_message = AsyncMock(return_value=None)

    asyncio.run(
        runner._process_handoff(
            {
                "id": "cli-session",
                "title": "CLI work",
                "handoff_platform": "telegram",
            }
        )
    )

    runner._rebind_existing_session_route.assert_called_once_with(
        "agent:main:telegram:dm:-10001",
        "cli-session",
        expected_session_id=destination.session_id,
    )
    runner._commit_memory_boundary_before_session_switch.assert_not_awaited()


def test_existing_session_rebind_is_topology_only_and_evicts_source_runtime() -> None:
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.session_store = MagicMock()
    target = _entry(previous_session_id=None)
    target.session_id = "sess-target"
    runner.session_store.switch_session.return_value = target
    runner._evict_cached_agent = MagicMock()
    semantic_boundary = AsyncMock()
    runner._commit_memory_boundary_before_session_switch = semantic_boundary

    switched = runner._rebind_existing_session_route(
        target.session_key,
        target.session_id,
        expected_session_id="sess-source",
    )

    assert switched is target
    runner.session_store.switch_session.assert_called_once_with(
        target.session_key,
        "sess-target",
        expected_session_id="sess-source",
        end_current=False,
    )
    runner._evict_cached_agent.assert_called_once_with(target.session_key)
    semantic_boundary.assert_not_awaited()


def test_delegation_rebind_stops_when_switch_cas_loses() -> None:
    """A routing CAS loss neither injects nor finalizes either session."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    entry = _entry(previous_session_id=None)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = entry
    runner.session_store.switch_session.return_value = None
    runner._recover_telegram_topic_thread_id = MagicMock(return_value=None)
    runner._finalize_previous_session_before_turn = AsyncMock(return_value=True)
    runner._cache_session_source = MagicMock()
    runner._evict_cached_agent = MagicMock()
    runner._commit_memory_boundary_before_session_switch = AsyncMock()
    runner._session_db = MagicMock()
    runner._session_db.get_session = AsyncMock(
        return_value={"id": "sess-pinned", "ended_at": None}
    )
    event = SimpleNamespace(
        text="delegation result",
        source=_source(),
        metadata={"gateway_session_id": "sess-pinned"},
    )

    result = asyncio.run(
        runner._handle_message_with_agent(event, event.source, entry.session_key, 1)
    )

    assert result is None
    runner.session_store.switch_session.assert_called_once_with(
        entry.session_key,
        "sess-pinned",
        expected_session_id="sess-new",
        end_current=False,
    )
    runner._commit_memory_boundary_before_session_switch.assert_not_awaited()
    runner._evict_cached_agent.assert_not_called()
    runner._cache_session_source.assert_not_called()


def test_topic_rebind_stops_when_switch_cas_loses() -> None:
    """A concurrent topic-route winner cannot inherit the incoming turn."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    entry = _entry(previous_session_id=None)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = entry
    runner.session_store.switch_session.return_value = None
    runner._recover_telegram_topic_thread_id = MagicMock(return_value=None)
    runner._finalize_previous_session_before_turn = AsyncMock(return_value=True)
    runner._cache_session_source = MagicMock()
    runner._is_telegram_topic_lane = MagicMock(return_value=True)
    runner._evict_cached_agent = MagicMock()
    runner._commit_memory_boundary_before_session_switch = AsyncMock()
    runner._session_db = MagicMock()
    runner._session_db.get_telegram_topic_binding = AsyncMock(
        return_value={"session_id": "sess-bound"}
    )
    runner._session_db.get_compression_tip = AsyncMock(return_value="sess-bound")
    event = SimpleNamespace(text="new customer request", source=_source(), metadata={})

    result = asyncio.run(
        runner._handle_message_with_agent(event, event.source, entry.session_key, 1)
    )

    assert "route changed" in result
    runner.session_store.switch_session.assert_called_once_with(
        entry.session_key,
        "sess-bound",
        expected_session_id="sess-new",
        end_current=False,
    )
    runner._commit_memory_boundary_before_session_switch.assert_not_awaited()
    runner._evict_cached_agent.assert_not_called()
