"""Invariant test: a completed webhook delivery closes its session.

Regression guard for the ghost-session leak.  Webhook deliveries create a
unique one-shot session (``delivery_id`` baked into the session key), but the
adapter historically fired ``handle_message`` without ever ending the session.
``SessionDB.prune_sessions`` only reaps rows where ``ended_at IS NOT NULL``, so
every webhook session stayed unprunable and state.db grew without bound (this
was the primary driver of the SQLite lock-contention gateway outage).

The invariant asserted here is a *behavior contract*, not a snapshot: once a
webhook delivery's agent run completes, the session row for that delivery must
have ``ended_at`` set — mirroring how a cron run closes its session with
``end_session(..., "cron_complete")``.

CRITICAL: these tests go through the REAL ``handle_message`` →
``_process_message_background`` → ``on_processing_complete`` pipeline (only the
runner-side ``_message_handler`` is stubbed, exactly the seam the live gateway
injects).  ``handle_message`` is fire-and-forget — it spawns the background
task and returns before the run starts — so any close bolted around
``handle_message`` itself runs BEFORE the session row exists and silently
no-ops.  A test that fakes ``handle_message`` to create the row synchronously
masks exactly that bug (the first version of this fix shipped that way).
"""

import asyncio

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.platforms.webhook import WebhookAdapter, _INSECURE_NO_AUTH
from gateway.session import SessionSource, SessionStore


def _make_adapter(routes, **extra_kw) -> WebhookAdapter:
    extra = {"host": "127.0.0.1", "port": 0, "routes": routes}
    extra.update(extra_kw)
    config = PlatformConfig(enabled=True, extra=extra)
    return WebhookAdapter(config)


class _FakeRunner:
    """Minimal gateway runner surface the webhook close path depends on.

    Wires a real ``SessionStore`` (which owns a real ``SessionDB``) and reuses
    that same ``SessionDB`` as ``_session_db`` so the row created at routing
    time is the row the close path ends — exactly the wiring the live gateway
    has (``self.session_store`` + ``self._session_db``).
    """

    def __init__(
        self,
        store: SessionStore,
        *,
        boundary_outcomes=(True,),
        profile_home=None,
    ):
        self.session_store = store
        self._session_db = store._db
        self._running_agents = {}
        self._agent_cache = {}
        self._agent_cache_lock = None
        self._boundary_outcomes = (
            None if boundary_outcomes is None else list(boundary_outcomes)
        )
        self._profile_home = profile_home or store.sessions_dir.parent
        self.boundary_calls = []
        self.on_boundary = None

    def _session_key_for_source(self, source: SessionSource) -> str:
        return self.session_store._generate_session_key(source)

    async def finalize_one_shot_session(self, **kwargs) -> bool:
        # Exercise the production runner implementation without constructing
        # the full gateway and all of its platform clients.
        from gateway.run import GatewayRunner

        return await GatewayRunner.finalize_one_shot_session(self, **kwargs)

    async def _finalize_one_shot_session_owned(self, **kwargs) -> bool:
        from gateway.run import GatewayRunner

        return await GatewayRunner._finalize_one_shot_session_owned(self, **kwargs)

    async def _retry_pending_one_shot_sessions(self) -> int:
        from gateway.run import GatewayRunner

        return await GatewayRunner._retry_pending_one_shot_sessions(self)

    async def _commit_memory_boundary_before_session_switch(self, **kwargs) -> bool:
        self.boundary_calls.append(kwargs)
        if callable(self.on_boundary):
            self.on_boundary(kwargs)
        if self._boundary_outcomes is None:
            # Native no-Cortex compatibility path: no cached manager and no
            # detached Cortex DB means there is no memory boundary to own, so
            # the existing production helper deliberately returns True.
            from gateway.slash_commands import GatewaySlashCommandsMixin

            return await (
                GatewaySlashCommandsMixin._commit_memory_boundary_before_session_switch(
                    self, **kwargs
                )
            )
        if not self._boundary_outcomes:
            raise AssertionError("unexpected extra boundary attempt")
        return self._boundary_outcomes.pop(0)

    async def _run_in_executor_with_context(self, func, *args):
        return await asyncio.to_thread(func, *args)

    def _resolve_profile_home_for_source(self, source: SessionSource):
        del source
        return self._profile_home


def _make_store(tmp_path) -> SessionStore:
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    config = GatewayConfig(
        platforms={Platform.WEBHOOK: PlatformConfig(enabled=True)}
    )
    store = SessionStore(sessions_dir=sessions_dir, config=config)
    assert store._db is not None, "test requires a real SessionDB"
    return store


def _make_event(adapter: WebhookAdapter, delivery_id: str, text: str) -> MessageEvent:
    session_chat_id = f"webhook:alerts:{delivery_id}"
    source = adapter.build_source(
        chat_id=session_chat_id,
        chat_name="webhook/alerts",
        chat_type="webhook",
        user_id="webhook:alerts",
        user_name="alerts",
    )
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=source,
        raw_message={"message": text},
        message_id=delivery_id,
    )


async def _drain_background_tasks(adapter: WebhookAdapter, timeout: float = 5.0) -> None:
    """Wait for the adapter's spawned processing task(s) to finish."""
    deadline = asyncio.get_event_loop().time() + timeout
    while adapter._background_tasks and asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.02)
    # One extra tick for done-callbacks to run.
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_completed_webhook_delivery_closes_its_session(tmp_path):
    """Cortex admission is durable and precedes DB end on REAL dispatch."""
    store = _make_store(tmp_path)
    runner = _FakeRunner(store, boundary_outcomes=None)
    order = []

    def _on_boundary(call):
        entry = call["session_entry"]
        # The retry token must be on disk before Cortex admission starts.
        persisted = store.lookup_by_session_id(entry.session_id)
        assert persisted is not None
        assert persisted.pending_finalize_reason == "webhook_complete"
        assert call["require_cached_session_match"] is True

    runner.on_boundary = _on_boundary
    real_end_session = store._db.end_session

    def _record_db_end(session_id, reason):
        order.append(("db", session_id))
        return real_end_session(session_id, reason)

    runner._session_db.end_session = _record_db_end

    adapter = _make_adapter(
        {
            "alerts": {
                "secret": _INSECURE_NO_AUTH,
                "prompt": "Alert: {message}",
                "deliver": "log",
            }
        }
    )
    adapter.gateway_runner = runner

    # Stub the RUNNER-side handler (the seam the live gateway injects) — the
    # adapter's own handle_message / _process_message_background pipeline runs
    # for real, including the fire-and-forget task spawn and the
    # on_processing_complete hook.  The handler creates the session row, just
    # like GatewayRunner._handle_message does at routing time.
    created = {}

    async def _message_handler(event: MessageEvent):
        entry = store.get_or_create_session(event.source)
        created["session_id"] = entry.session_id

        class _MemoryManager:
            _session_id = entry.session_id
            providers = ()

            def on_session_finalize(self, messages, *, reason):
                assert messages == [{"role": "user", "content": event.text}]
                assert reason == "webhook_complete"
                order.append(("cortex", entry.session_id))

            def flush_pending(self, *, timeout):
                assert timeout == 10

        class _Agent:
            session_id = entry.session_id
            _memory_manager = _MemoryManager()
            _session_messages = [{"role": "user", "content": event.text}]

        session_key = runner._session_key_for_source(event.source)
        runner._agent_cache[session_key] = (_Agent(), "test-signature")
        return ""  # webhook deliver=log — nothing to send back

    adapter._message_handler = _message_handler

    event = _make_event(adapter, "alert-close-001", "Alert: server on fire")

    # Exactly what _handle_webhook schedules.
    await adapter.handle_message(event)
    # handle_message is fire-and-forget: the session must NOT be expected to
    # exist yet.  (Guards against reintroducing a close wrapped around
    # handle_message itself, which ran before the row existed and no-op'd.)
    await _drain_background_tasks(adapter)

    session_id = created["session_id"]
    row = store._db.get_session(session_id)
    assert row is not None

    # INVARIANT: a completed webhook session must be closed so prune can reap it.
    assert row["ended_at"] is not None, (
        "webhook session was never closed — ended_at is NULL, so "
        "prune_sessions can never reap it (the ghost-session leak)"
    )
    assert row["end_reason"] == "webhook_complete"

    entry = store.lookup_by_session_id(session_id)
    assert entry is not None
    assert entry.pending_finalize_reason is None
    assert entry.expiry_finalized is True
    assert order == [("cortex", session_id), ("db", session_id)]
    assert runner.boundary_calls[0]["session_entry"].session_id == session_id

    # And the closed row is actually prunable, unlike the pre-fix leak.
    pruned = store._db.prune_sessions(older_than_days=0, source="webhook")
    assert pruned >= 1
    store._db.close()


@pytest.mark.asyncio
async def test_webhook_session_closed_even_when_agent_run_raises(tmp_path):
    """A failing agent run still closes the session (FAILURE hook path)."""
    store = _make_store(tmp_path)
    runner = _FakeRunner(store)

    adapter = _make_adapter(
        {"alerts": {"secret": _INSECURE_NO_AUTH, "prompt": "x", "deliver": "log"}}
    )
    adapter.gateway_runner = runner

    created = {}

    async def _boom(event: MessageEvent):
        # Row exists (routing happened) before the run blows up mid-turn.
        entry = store.get_or_create_session(event.source)
        created["session_id"] = entry.session_id
        raise RuntimeError("agent exploded mid-run")

    adapter._message_handler = _boom

    event = _make_event(adapter, "alert-fail-001", "x")

    await adapter.handle_message(event)
    await _drain_background_tasks(adapter)

    row = store._db.get_session(created["session_id"])
    assert row is not None
    assert row["ended_at"] is not None, (
        "session left open after a failed webhook run — the leak persists "
        "on the error path"
    )
    assert row["end_reason"] == "webhook_complete"
    store._db.close()


@pytest.mark.asyncio
async def test_boundary_failure_retains_live_session_and_retries_after_restart(
    tmp_path,
):
    """A failed admission never ends DB; the persisted intent survives restart."""
    store = _make_store(tmp_path)
    runner = _FakeRunner(store, boundary_outcomes=(False,))
    adapter = _make_adapter(
        {"alerts": {"secret": _INSECURE_NO_AUTH, "prompt": "x", "deliver": "log"}}
    )
    adapter.gateway_runner = runner

    created = {}

    async def _message_handler(event: MessageEvent):
        entry = store.get_or_create_session(event.source)
        created["session_id"] = entry.session_id
        return ""

    adapter._message_handler = _message_handler
    event = _make_event(adapter, "alert-retry-001", "x")

    await adapter.handle_message(event)
    await _drain_background_tasks(adapter)

    session_id = created["session_id"]
    row = store._db.get_session(session_id)
    assert row is not None
    assert row["ended_at"] is None, "DB end must fail closed behind Cortex admission"
    entry = store.lookup_by_session_id(session_id)
    assert entry is not None
    assert entry.pending_finalize_reason == "webhook_complete"
    assert entry.expiry_finalized is False

    # Simulate a full gateway restart: discard every in-memory store/runner
    # object, then rehydrate the route from state.db/sessions.json.
    sessions_dir = store.sessions_dir
    config = store.config
    store._db.close()
    restarted_store = SessionStore(sessions_dir=sessions_dir, config=config)
    restarted_runner = _FakeRunner(restarted_store, boundary_outcomes=(True,))

    pending = restarted_store.list_pending_terminal_finalizations()
    assert [(item.session_id, item.pending_finalize_reason) for item in pending] == [
        (session_id, "webhook_complete")
    ]
    assert await restarted_runner._retry_pending_one_shot_sessions() == 1

    retried_row = restarted_store._db.get_session(session_id)
    assert retried_row is not None
    assert retried_row["ended_at"] is not None
    assert retried_row["end_reason"] == "webhook_complete"
    retried_entry = restarted_store.lookup_by_session_id(session_id)
    assert retried_entry is not None
    assert retried_entry.pending_finalize_reason is None
    assert retried_entry.expiry_finalized is True
    assert restarted_runner.boundary_calls[0]["reason"] == "webhook_complete"
    restarted_store._db.close()


@pytest.mark.asyncio
async def test_post_db_ack_failure_retries_idempotently_after_restart(tmp_path):
    """A crash-window after DB end keeps the token and safely replays both steps."""
    store = _make_store(tmp_path)
    runner = _FakeRunner(store, boundary_outcomes=(True,))
    adapter = _make_adapter(
        {"alerts": {"secret": _INSECURE_NO_AUTH, "prompt": "x", "deliver": "log"}}
    )
    adapter.gateway_runner = runner
    created = {}

    async def _message_handler(event: MessageEvent):
        entry = store.get_or_create_session(event.source)
        created["session_id"] = entry.session_id
        return ""

    adapter._message_handler = _message_handler

    def _fail_ack(*args, **kwargs):
        del args, kwargs
        raise OSError("simulated crash before routing acknowledgement")

    store.complete_terminal_finalize = _fail_ack
    await adapter.handle_message(_make_event(adapter, "alert-ack-retry-001", "x"))
    await _drain_background_tasks(adapter)

    session_id = created["session_id"]
    row = store._db.get_session(session_id)
    assert row is not None and row["ended_at"] is not None
    entry = store.lookup_by_session_id(session_id)
    assert entry is not None
    assert entry.pending_finalize_reason == "webhook_complete"
    assert entry.expiry_finalized is False

    sessions_dir = store.sessions_dir
    config = store.config
    store._db.close()
    restarted_store = SessionStore(sessions_dir=sessions_dir, config=config)
    restarted_runner = _FakeRunner(restarted_store, boundary_outcomes=(True,))

    # Startup must retain the ended route because its acknowledgement is not
    # durable yet. Repeating Cortex admission and end_session is idempotent.
    pending = restarted_store.list_pending_terminal_finalizations()
    assert [item.session_id for item in pending] == [session_id]
    assert await restarted_runner._retry_pending_one_shot_sessions() == 1
    finalized = restarted_store.lookup_by_session_id(session_id)
    assert finalized is not None
    assert finalized.pending_finalize_reason is None
    assert finalized.expiry_finalized is True
    restarted_store._db.close()


@pytest.mark.asyncio
async def test_webhook_close_is_compatible_when_cortex_is_absent(tmp_path):
    """The native no-memory helper still allows the one-shot DB close."""
    store = _make_store(tmp_path)
    empty_profile = tmp_path / "profile-without-cortex"
    runner = _FakeRunner(
        store,
        boundary_outcomes=None,
        profile_home=empty_profile,
    )
    adapter = _make_adapter(
        {"alerts": {"secret": _INSECURE_NO_AUTH, "prompt": "x", "deliver": "log"}}
    )
    adapter.gateway_runner = runner

    created = {}

    async def _message_handler(event: MessageEvent):
        entry = store.get_or_create_session(event.source)
        created["session_id"] = entry.session_id
        return ""

    adapter._message_handler = _message_handler
    event = _make_event(adapter, "alert-no-cortex-001", "x")

    await adapter.handle_message(event)
    await _drain_background_tasks(adapter)

    row = store._db.get_session(created["session_id"])
    assert row is not None
    assert row["ended_at"] is not None
    assert row["end_reason"] == "webhook_complete"
    assert not (empty_profile / "cortex" / "cortex.db").exists()
    assert len(runner.boundary_calls) == 1
    store._db.close()


@pytest.mark.asyncio
async def test_end_webhook_session_awaits_async_session_db(tmp_path):
    """The close path handles the gateway's real AsyncSessionDB facade."""
    from hermes_state import AsyncSessionDB

    store = _make_store(tmp_path)
    runner = _FakeRunner(store)
    runner._session_db = AsyncSessionDB(store._db)

    adapter = _make_adapter(
        {"alerts": {"secret": _INSECURE_NO_AUTH, "prompt": "x", "deliver": "log"}}
    )
    adapter.gateway_runner = runner

    event = _make_event(adapter, "alert-async-001", "x")
    entry = store.get_or_create_session(event.source)

    await adapter._end_webhook_session(event, event.source.chat_id)

    row = store._db.get_session(entry.session_id)
    assert row["ended_at"] is not None
    assert row["end_reason"] == "webhook_complete"
    store._db.close()
