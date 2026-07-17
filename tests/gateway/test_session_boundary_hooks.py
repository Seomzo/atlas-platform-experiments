"""Tests that on_session_finalize and on_session_reset plugin hooks fire in the gateway."""

from datetime import datetime
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource, build_session_key


def _install_policy_reset(runner, old_entry, *, order=None):
    """Make a mocked SessionStore perform the production routing CAS."""

    def reset_session(session_key, **kwargs):
        assert kwargs == {
            "expected_session_id": old_entry.session_id,
            "defer_memory_finalize_reason": "session_expired",
            "end_reason": "session_expired",
            "auto_reset_reason": "idle",
        }
        if order is not None:
            order.append("reset")
        replacement = SessionEntry(
            session_key=session_key,
            session_id=f"fresh-{old_entry.session_id}",
            created_at=datetime.now(),
            updated_at=datetime.now(),
            origin=old_entry.origin,
            platform=old_entry.platform,
            chat_type=old_entry.chat_type,
            previous_session_id=old_entry.session_id,
            previous_finalize_reason="session_expired",
        )
        runner.session_store._entries[session_key] = replacement
        return replacement

    def clear_previous(session_key, *, current_session_id, previous_session_id):
        replacement = runner.session_store._entries[session_key]
        assert replacement.session_id == current_session_id
        assert replacement.previous_session_id == previous_session_id
        if order is not None:
            order.append("ack")
        replacement.previous_session_id = None
        replacement.previous_finalize_reason = None
        return True

    runner.session_store.reset_session.side_effect = reset_session
    runner.session_store.clear_previous_session_id.side_effect = clear_previous


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(text=text, source=_make_source(), message_id="m1")


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    adapter = MagicMock()
    adapter.send = AsyncMock()
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner._session_model_overrides = {}
    runner._pending_model_notes = {}
    runner._background_tasks = set()

    session_key = build_session_key(_make_source())
    session_entry = SessionEntry(
        session_key=session_key,
        session_id="sess-old",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    new_session_entry = SessionEntry(
        session_key=session_key,
        session_id="sess-new",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = new_session_entry
    runner.session_store.reset_session.return_value = new_session_entry
    runner.session_store._entries = {session_key: session_entry}
    runner.session_store._generate_session_key.return_value = session_key
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._agent_cache_lock = threading.RLock()
    runner._is_user_authorized = lambda _source: True
    runner._format_session_info = lambda: ""

    return runner


@pytest.mark.asyncio
@patch("hermes_cli.plugins.invoke_hook")
async def test_reset_fires_finalize_hook(mock_invoke_hook):
    """/new must fire on_session_finalize with the OLD session id."""
    runner = _make_runner()

    await runner._handle_reset_command(_make_event("/new"))

    assert any(
        c.args == ("on_session_finalize",)
        and c.kwargs["session_id"] == "sess-old"
        and c.kwargs["platform"] == "telegram"
        and c.kwargs["old_session_id"] == "sess-old"
        and c.kwargs["new_session_id"] == "sess-new"
        for c in mock_invoke_hook.call_args_list
    )


@pytest.mark.asyncio
@patch("hermes_cli.plugins.invoke_hook")
async def test_reset_fires_reset_hook(mock_invoke_hook):
    """/new must fire on_session_reset with the NEW session id."""
    runner = _make_runner()

    await runner._handle_reset_command(_make_event("/new"))

    assert any(
        c.args == ("on_session_reset",)
        and c.kwargs["session_id"] == "sess-new"
        and c.kwargs["platform"] == "telegram"
        and c.kwargs["old_session_id"] == "sess-old"
        and c.kwargs["new_session_id"] == "sess-new"
        for c in mock_invoke_hook.call_args_list
    )


@pytest.mark.asyncio
@patch("hermes_cli.plugins.invoke_hook")
async def test_finalize_before_reset(mock_invoke_hook):
    """on_session_finalize must fire before on_session_reset."""
    runner = _make_runner()

    await runner._handle_reset_command(_make_event("/new"))

    calls = [
        c
        for c in mock_invoke_hook.call_args_list
        if c[0][0] in {"on_session_finalize", "on_session_reset"}
    ]
    hook_names = [c[0][0] for c in calls]
    assert hook_names == ["on_session_finalize", "on_session_reset"]


@pytest.mark.asyncio
@patch("hermes_cli.plugins.invoke_hook")
async def test_shutdown_does_not_finalize_active_logical_sessions(mock_invoke_hook):
    """Process shutdown releases resources without declaring conversations ended."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._background_tasks = set()
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._shutdown_event = MagicMock()
    runner.adapters = {}
    runner._exit_reason = "test"
    runner._exit_code = None
    runner._draining = False
    runner._restart_requested = False
    runner._restart_task_started = False
    runner._restart_detached = False
    runner._restart_via_service = False
    runner._restart_drain_timeout = 0.0
    runner._stop_task = None
    runner._running_agents_ts = {}
    runner._update_runtime_status = MagicMock()

    agent1 = MagicMock()
    agent1.session_id = "sess-a"
    agent2 = MagicMock()
    agent2.session_id = "sess-b"
    runner._running_agents = {"key-a": agent1, "key-b": agent2}

    with (
        patch("gateway.status.remove_pid_file"),
        patch("gateway.status.write_runtime_status"),
    ):
        await runner.stop()

    finalize_calls = [
        c for c in mock_invoke_hook.call_args_list if c[0][0] == "on_session_finalize"
    ]
    assert finalize_calls == []


@pytest.mark.asyncio
@patch("hermes_cli.plugins.invoke_hook", side_effect=Exception("boom"))
async def test_hook_error_does_not_break_reset(mock_invoke_hook):
    """Plugin hook errors must not prevent /new from completing."""
    runner = _make_runner()

    result = await runner._handle_reset_command(_make_event("/new"))

    # Should still return a success message despite hook errors
    assert "Session reset" in result or "New session" in result


@pytest.mark.asyncio
@patch("hermes_cli.plugins.invoke_hook")
async def test_idle_expiry_fires_finalize_hook(mock_invoke_hook):
    """Regression test for #14981.

    When ``_session_expiry_watcher`` sweeps a session that has aged past
    its reset policy (idle timeout, scheduled reset), it must fire
    ``on_session_finalize`` so plugin providers get the same final-pass
    extraction opportunity they'd get from /new or CLI shutdown.  Before
    the fix, the expiry path evicted the agent but silently skipped the
    hook.
    """
    from datetime import datetime, timedelta

    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._running_agents = {}
    runner._agent_cache = {}
    runner._agent_cache_lock = None
    runner._last_session_store_prune_ts = 0.0

    session_key = "agent:main:telegram:dm:42"
    expired_entry = SessionEntry(
        session_key=session_key,
        session_id="sess-expired",
        created_at=datetime.now() - timedelta(hours=2),
        updated_at=datetime.now() - timedelta(hours=2),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    expired_entry.expiry_finalized = False

    runner.session_store = MagicMock()
    runner.session_store._ensure_loaded = MagicMock()
    runner.session_store._entries = {session_key: expired_entry}
    runner.session_store._is_session_expired = MagicMock(return_value=True)
    runner.session_store._lock = MagicMock()
    runner.session_store._lock.__enter__ = MagicMock(return_value=None)
    runner.session_store._lock.__exit__ = MagicMock(return_value=None)
    runner.session_store._save = MagicMock()
    _install_policy_reset(runner, expired_entry)

    runner._evict_cached_agent = MagicMock()
    runner._cleanup_agent_resources = MagicMock()
    runner._sweep_idle_cached_agents = MagicMock(return_value=0)

    # The watcher starts with `await asyncio.sleep(60)` and loops while
    # `self._running`.  Patch sleep so the 60s initial delay is instant, and
    # make the expiry hook invocation flip `_running` false so the loop
    # exits cleanly after one pass.
    _orig_sleep = __import__("asyncio").sleep

    async def _fast_sleep(_):
        await _orig_sleep(0)

    def _hook_and_stop(*a, **kw):
        runner._running = False
        return None

    mock_invoke_hook.side_effect = _hook_and_stop

    with patch("gateway.run.asyncio.sleep", side_effect=_fast_sleep):
        await runner._session_expiry_watcher(interval=0)

    # Look for the finalize call targeting the expired session.
    finalize_calls = [
        c
        for c in mock_invoke_hook.call_args_list
        if c[0] and c[0][0] == "on_session_finalize"
    ]
    session_ids = {c[1].get("session_id") for c in finalize_calls}
    assert "sess-expired" in session_ids, (
        f"on_session_finalize was not fired during idle expiry; "
        f"got session_ids={session_ids} (regression of #14981)"
    )


@pytest.mark.asyncio
@patch("hermes_cli.plugins.invoke_hook")
async def test_idle_expiry_defers_while_turn_is_running(mock_invoke_hook):
    """Expiry retries later instead of snapshotting a mid-turn Cortex epoch."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._agent_cache = {}
    runner._agent_cache_lock = None
    runner._last_session_store_prune_ts = 0.0
    runner._sweep_idle_cached_agents = MagicMock(return_value=0)
    runner.config = SimpleNamespace(session_store_max_age_days=0)

    session_key = "agent:main:telegram:dm:active-expiry"
    entry = SessionEntry(
        session_key=session_key,
        session_id="sess-active-expiry",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    entry.expiry_finalized = False
    runner._running_agents = {session_key: MagicMock()}
    runner.session_store = MagicMock()
    runner.session_store._entries = {session_key: entry}
    runner.session_store._ensure_loaded = MagicMock()

    def expire_once(_entry):
        runner._running = False
        return True

    runner.session_store._is_session_expired.side_effect = expire_once
    original_sleep = __import__("asyncio").sleep

    async def fast_sleep(_seconds):
        await original_sleep(0)

    with patch("gateway.run.asyncio.sleep", side_effect=fast_sleep):
        await runner._session_expiry_watcher(interval=0)

    mock_invoke_hook.assert_not_called()
    runner.session_store.set_expiry_finalized.assert_not_called()
    assert entry.expiry_finalized is False
    assert session_key in runner._running_agents


@pytest.mark.asyncio
@patch("hermes_cli.plugins.invoke_hook")
async def test_policy_expiry_lost_reset_cas_starts_no_semantic_work(
    mock_invoke_hook,
) -> None:
    """Time passage is only a candidate; a lost route CAS has no authority."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._running_agents = {}
    runner._agent_cache_lock = threading.RLock()
    runner._last_session_store_prune_ts = 0.0
    runner.config = SimpleNamespace(session_store_max_age_days=0)
    runner._sweep_idle_cached_agents = MagicMock(return_value=0)
    runner._evict_cached_agent = MagicMock()

    session_key = "agent:main:telegram:dm:lost-expiry-cas"
    entry = SessionEntry(
        session_key=session_key,
        session_id="sess-lost-expiry-cas",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    manager = MagicMock()
    runner._agent_cache = {
        session_key: SimpleNamespace(_memory_manager=manager, _session_messages=[])
    }
    runner.session_store = MagicMock()
    runner.session_store._entries = {session_key: entry}
    runner.session_store._ensure_loaded = MagicMock()
    runner.session_store.reset_session.return_value = None

    def expire_once(_entry):
        runner._running = False
        return True

    runner.session_store._is_session_expired.side_effect = expire_once
    original_sleep = __import__("asyncio").sleep

    async def fast_sleep(_seconds):
        await original_sleep(0)

    with patch("gateway.run.asyncio.sleep", side_effect=fast_sleep):
        await runner._session_expiry_watcher(interval=0)

    manager.on_session_finalize.assert_not_called()
    mock_invoke_hook.assert_not_called()
    runner.session_store.clear_previous_session_id.assert_not_called()
    runner._evict_cached_agent.assert_not_called()
    assert runner.session_store._entries[session_key] is entry


@pytest.mark.asyncio
@patch("hermes_cli.plugins.invoke_hook")
async def test_idle_expiry_never_marks_failed_durable_boundary_finalized(
    mock_invoke_hook,
) -> None:
    """Repeated Cortex failures remain pending instead of becoming data loss."""

    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._running_agents = {}
    runner._agent_cache_lock = threading.RLock()
    runner._last_session_store_prune_ts = 0.0
    runner.config = SimpleNamespace(session_store_max_age_days=0)
    runner._sweep_idle_cached_agents = MagicMock(return_value=0)

    session_key = "agent:main:telegram:dm:durable"
    entry = SessionEntry(
        session_key=session_key,
        session_id="sess-durable-failure",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    entry.expiry_finalized = False
    manager = MagicMock()
    cached_agent = SimpleNamespace(_memory_manager=manager, _session_messages=[])
    runner._agent_cache = {session_key: cached_agent}
    runner.session_store = MagicMock()
    runner.session_store._entries = {session_key: entry}
    runner.session_store._ensure_loaded = MagicMock()
    def expire_once(_entry):
        runner._running = False
        return True

    runner.session_store._is_session_expired.side_effect = expire_once
    _install_policy_reset(runner, entry)

    async def run_inline(func, *args):
        return func(*args)

    runner._run_in_executor_with_context = run_inline
    attempts = 0

    def fail_boundary(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        raise OSError("simulated durable Cortex failure")

    manager.on_session_finalize.side_effect = fail_boundary

    original_sleep = __import__("asyncio").sleep

    async def fast_sleep(_seconds):
        await original_sleep(0)

    with patch("gateway.run.asyncio.sleep", side_effect=fast_sleep):
        await runner._session_expiry_watcher(interval=0)

    assert attempts == 1
    assert entry.expiry_finalized is False
    runner.session_store.set_expiry_finalized.assert_not_called()
    replacement = runner.session_store._entries[session_key]
    assert replacement.previous_session_id == entry.session_id
    assert session_key not in runner._agent_cache


@pytest.mark.asyncio
@patch("hermes_cli.plugins.invoke_hook")
async def test_idle_expiry_clears_last_resolved_model(mock_invoke_hook):
    """Regression test for #58403.

    ``_session_expiry_watcher`` permanently finalizes an expired session and
    already drops ``_session_model_overrides`` / the reasoning override /
    ``_pending_model_notes`` — a resumed conversation must not inherit stale
    per-session state. It missed ``_last_resolved_model``: without clearing
    it, a resumed session could serve a cached model from before it went
    idle on a transient config-cache miss, exactly the #58403 class the
    /new and compression-exhausted-reset paths already guard against.
    """
    from datetime import datetime, timedelta

    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._running_agents = {}
    runner._agent_cache = {}
    runner._agent_cache_lock = None
    runner._last_session_store_prune_ts = 0.0

    session_key = "agent:main:telegram:dm:42"
    expired_entry = SessionEntry(
        session_key=session_key,
        session_id="sess-expired",
        created_at=datetime.now() - timedelta(hours=2),
        updated_at=datetime.now() - timedelta(hours=2),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    expired_entry.expiry_finalized = False

    runner.session_store = MagicMock()
    runner.session_store._ensure_loaded = MagicMock()
    runner.session_store._entries = {session_key: expired_entry}
    runner.session_store._is_session_expired = MagicMock(return_value=True)
    runner.session_store._lock = MagicMock()
    runner.session_store._lock.__enter__ = MagicMock(return_value=None)
    runner.session_store._lock.__exit__ = MagicMock(return_value=None)
    runner.session_store._save = MagicMock()
    _install_policy_reset(runner, expired_entry)

    runner._evict_cached_agent = MagicMock()
    runner._cleanup_agent_resources = MagicMock()
    runner._sweep_idle_cached_agents = MagicMock(return_value=0)
    runner._session_model_overrides = {}
    runner._pending_model_notes = {}
    runner._last_resolved_model = {
        session_key: "gpt-5",
        "agent:main:telegram:dm:other": "keep-me",
    }

    _orig_sleep = __import__("asyncio").sleep

    async def _fast_sleep(_):
        await _orig_sleep(0)

    def _hook_and_stop(*a, **kw):
        runner._running = False
        return None

    mock_invoke_hook.side_effect = _hook_and_stop

    with patch("gateway.run.asyncio.sleep", side_effect=_fast_sleep):
        await runner._session_expiry_watcher(interval=0)

    assert session_key not in runner._last_resolved_model, (
        "session-expiry finalization did not clear the expired session's "
        "_last_resolved_model entry (#58403)"
    )
    assert runner._last_resolved_model["agent:main:telegram:dm:other"] == "keep-me", (
        "session-expiry finalization must only clear the expired session's "
        "own key, not unrelated sessions' cached entries"
    )


@pytest.mark.asyncio
async def test_expiry_lifecycle_claim_blocks_inbound_handler() -> None:
    """An inbound turn cannot enter while expiry owns the exact route."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    session_key = build_session_key(_make_source())
    entry = SessionEntry(
        session_key=session_key,
        session_id="sess-expiry-claim",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store = SimpleNamespace(_entries={session_key: entry})
    runner._running_agents = {}
    runner._session_key_for_source = lambda _source: session_key

    token = runner._claim_expiry_session_lifecycle(session_key, entry)
    assert token is not None

    result = await GatewayRunner._handle_message(runner, _make_event("new work"))

    assert "end-of-session memory save" in result
    assert session_key not in getattr(runner, "_inbound_session_lifecycle_claims", {})
    runner._release_expiry_session_lifecycle(session_key, token)


def test_inbound_lifecycle_claim_defers_expiry() -> None:
    """The pre-sentinel inbound window is protected from the expiry sweep."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    session_key = build_session_key(_make_source())
    entry = SessionEntry(
        session_key=session_key,
        session_id="sess-inbound-claim",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store = SimpleNamespace(_entries={session_key: entry})
    runner._running_agents = {}

    inbound_token = runner._claim_inbound_session_lifecycle(session_key)
    assert inbound_token is not None
    assert runner._claim_expiry_session_lifecycle(session_key, entry) is None

    runner._release_inbound_session_lifecycle(session_key, inbound_token)
    expiry_token = runner._claim_expiry_session_lifecycle(session_key, entry)
    assert expiry_token is not None
    runner._release_expiry_session_lifecycle(session_key, expiry_token)


@pytest.mark.asyncio
@patch("hermes_cli.plugins.invoke_hook")
async def test_idle_expiry_orders_boundary_db_end_cleanup_and_ack(
    mock_invoke_hook,
) -> None:
    """SQLite closes as session_expired only after the durable boundary succeeds."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._running_agents = {}
    runner._agent_cache_lock = threading.RLock()
    runner._last_session_store_prune_ts = 0.0
    runner.config = SimpleNamespace(session_store_max_age_days=0)
    runner._sweep_idle_cached_agents = MagicMock(return_value=0)
    runner._session_model_overrides = {}
    runner._pending_model_notes = {}
    runner._last_resolved_model = {}
    runner._pending_approvals = {}
    runner._set_session_reasoning_override = MagicMock()
    runner._evict_cached_agent = MagicMock()

    session_key = "agent:main:telegram:dm:ordered-expiry"
    entry = SessionEntry(
        session_key=session_key,
        session_id="sess-ordered-expiry",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    entry.expiry_finalized = False
    order: list[str] = []
    manager = MagicMock()
    manager.on_session_finalize.side_effect = lambda *_a, **_kw: order.append(
        "boundary"
    )
    cached_agent = SimpleNamespace(_memory_manager=manager, _session_messages=[])
    runner._agent_cache = {session_key: cached_agent}

    runner.session_store = MagicMock()
    runner.session_store._entries = {session_key: entry}
    runner.session_store._ensure_loaded = MagicMock()

    def expire_once(_entry):
        runner._running = False
        return True

    runner.session_store._is_session_expired.side_effect = expire_once
    _install_policy_reset(runner, entry, order=order)

    async def run_inline(func, *args):
        return func(*args)

    async def end_session(session_id, reason):
        order.append("db")
        assert session_id == entry.session_id
        assert reason == "session_expired"

    async def cleanup(agent, *, context):
        order.append("cleanup")
        assert agent is cached_agent
        assert context == "session expiry"

    runner._run_in_executor_with_context = run_inline
    runner._session_db = SimpleNamespace(end_session=end_session)
    runner._cleanup_agent_resources_off_loop = cleanup

    original_sleep = __import__("asyncio").sleep

    async def fast_sleep(_seconds):
        await original_sleep(0)

    with (
        patch("gateway.run.asyncio.sleep", side_effect=fast_sleep),
        patch(
            "gateway.run.asyncio.wait_for",
            side_effect=AssertionError(
                "expiry semantic work must retain ownership until completion"
            ),
        ),
    ):
        await runner._session_expiry_watcher(interval=0)

    assert order == ["reset", "boundary", "ack", "cleanup"]
    assert cached_agent._end_session_on_close is False
    runner.session_store.set_expiry_finalized.assert_not_called()
    runner._evict_cached_agent.assert_called_once_with(session_key)


@pytest.mark.asyncio
@patch("hermes_cli.plugins.invoke_hook")
async def test_idle_expiry_rejects_unowned_detached_boundary(
    mock_invoke_hook, tmp_path
) -> None:
    """A false detached-finalizer result leaves DB and expiry state untouched."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._running_agents = {}
    runner._agent_cache = {}
    runner._agent_cache_lock = None
    runner._last_session_store_prune_ts = 0.0
    runner.config = SimpleNamespace(session_store_max_age_days=0)
    runner._sweep_idle_cached_agents = MagicMock(return_value=0)
    runner._session_model_overrides = {}
    runner._pending_model_notes = {}
    runner._set_session_reasoning_override = MagicMock()
    runner._evict_cached_agent = MagicMock()

    session_key = "agent:main:telegram:dm:detached-unowned"
    entry = SessionEntry(
        session_key=session_key,
        session_id="sess-detached-unowned",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    entry.expiry_finalized = False
    runner.session_store = MagicMock()
    runner.session_store._entries = {session_key: entry}
    runner.session_store._ensure_loaded = MagicMock()
    runner.session_store._profile_from_session_key.return_value = "default"

    def expire_once(_entry):
        runner._running = False
        return True

    runner.session_store._is_session_expired.side_effect = expire_once
    _install_policy_reset(runner, entry)
    end_session = AsyncMock()
    runner._session_db = SimpleNamespace(end_session=end_session)
    original_sleep = __import__("asyncio").sleep

    async def fast_sleep(_seconds):
        await original_sleep(0)

    with (
        patch("gateway.run.asyncio.sleep", side_effect=fast_sleep),
        patch("hermes_cli.profiles.get_profile_dir", return_value=tmp_path),
        patch(
            "altas.cortex.lifecycle.finalize_detached_session",
            return_value=False,
        ),
    ):
        await runner._session_expiry_watcher(interval=0)

    end_session.assert_not_awaited()
    runner.session_store.set_expiry_finalized.assert_not_called()
    runner._evict_cached_agent.assert_called_once_with(session_key)
    assert entry.expiry_finalized is False
    assert (
        runner.session_store._entries[session_key].previous_session_id
        == entry.session_id
    )
