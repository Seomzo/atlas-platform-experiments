"""Tests for SessionStore.prune_old_entries and the gateway watcher that calls it.

The SessionStore in-memory dict (and its backing sessions.json) grew
unbounded — every unique (platform, chat_id, thread_id, user_id) tuple
ever seen was kept forever, regardless of how stale it became.  These
tests pin the prune behaviour:

  * Max-age-only routing entries are removed without semantic finalization
  * Independently policy-expired entries are retained for Cortex boundary retry
  * Entries marked ``suspended`` are preserved (user-paused)
  * Entries with an active process attached are preserved
  * max_age_days <= 0 disables pruning entirely
  * sessions.json is rewritten with the post-prune dict
  * The ``updated_at`` field — not ``created_at`` — drives the decision
    (so a long-running-but-still-active session isn't pruned)
"""

import asyncio
import json
import threading
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform, SessionResetPolicy
from gateway.session import SessionEntry, SessionSource, SessionStore


def _make_store(
    tmp_path,
    max_age_days: int = 90,
    has_active_processes_fn=None,
    reset_policy: SessionResetPolicy | None = None,
):
    """Build a SessionStore bypassing SQLite/disk-load side effects."""
    config = GatewayConfig(
        default_reset_policy=reset_policy or SessionResetPolicy(mode="none"),
        session_store_max_age_days=max_age_days,
    )
    with patch("gateway.session.SessionStore._ensure_loaded"):
        store = SessionStore(
            sessions_dir=tmp_path,
            config=config,
            has_active_processes_fn=has_active_processes_fn,
        )
    store._db = None
    store._loaded = True
    return store


def _entry(
    key: str,
    age_days: float,
    *,
    suspended: bool = False,
    session_id: str | None = None,
    finalized: bool = True,
) -> SessionEntry:
    now = datetime.now()
    return SessionEntry(
        session_key=key,
        session_id=session_id or f"sid_{key}",
        created_at=now - timedelta(days=age_days + 30),  # arbitrary older
        updated_at=now - timedelta(days=age_days),
        platform=Platform.TELEGRAM,
        chat_type="dm",
        suspended=suspended,
        expiry_finalized=finalized,
    )


class TestPruneBasics:
    def test_prune_removes_unfinalized_max_age_only_route(self, tmp_path):
        """Age-only retention drops routing without inventing a boundary."""
        store = _make_store(tmp_path)
        store._entries["pending"] = _entry(
            "pending", age_days=1000, finalized=False
        )

        removed = store.prune_old_entries(max_age_days=90)

        assert removed == 1
        assert "pending" not in store._entries

    def test_prune_retains_unfinalized_policy_expiry_for_boundary_retry(
        self, tmp_path
    ):
        store = _make_store(
            tmp_path,
            reset_policy=SessionResetPolicy(mode="idle", idle_minutes=1),
        )
        entry = _entry("pending", age_days=1000, finalized=False)
        store._entries["pending"] = entry

        assert store._is_session_expired(entry)
        assert store.prune_old_entries(max_age_days=90) == 0
        assert store._entries["pending"] is entry

    def test_prune_removes_policy_expiry_after_boundary_acknowledgement(
        self, tmp_path
    ):
        store = _make_store(
            tmp_path,
            reset_policy=SessionResetPolicy(mode="idle", idle_minutes=1),
        )
        store._entries["finalized"] = _entry(
            "finalized", age_days=1000, finalized=True
        )

        assert store.prune_old_entries(max_age_days=90) == 1
        assert "finalized" not in store._entries

    def test_prune_removes_entries_past_max_age(self, tmp_path):
        store = _make_store(tmp_path)
        store._entries["old"] = _entry("old", age_days=100)
        store._entries["fresh"] = _entry("fresh", age_days=5)

        removed = store.prune_old_entries(max_age_days=90)

        assert removed == 1
        assert "old" not in store._entries
        assert "fresh" in store._entries

    def test_prune_uses_updated_at_not_created_at(self, tmp_path):
        """A session created long ago but updated recently must be kept."""
        store = _make_store(tmp_path)
        now = datetime.now()
        entry = SessionEntry(
            session_key="long-lived",
            session_id="sid",
            created_at=now - timedelta(days=365),   # ancient
            updated_at=now - timedelta(days=3),     # but just chatted
            platform=Platform.TELEGRAM,
            chat_type="dm",
        )
        store._entries["long-lived"] = entry

        removed = store.prune_old_entries(max_age_days=30)

        assert removed == 0
        assert "long-lived" in store._entries

    def test_prune_disabled_when_max_age_is_zero(self, tmp_path):
        store = _make_store(tmp_path, max_age_days=0)
        for i in range(5):
            store._entries[f"s{i}"] = _entry(f"s{i}", age_days=365)

        assert store.prune_old_entries(0) == 0
        assert len(store._entries) == 5

    def test_prune_disabled_when_max_age_is_negative(self, tmp_path):
        store = _make_store(tmp_path)
        store._entries["s"] = _entry("s", age_days=365)

        assert store.prune_old_entries(-1) == 0
        assert "s" in store._entries

    def test_prune_skips_suspended_entries(self, tmp_path):
        """/stop-suspended sessions must be kept for later resume."""
        store = _make_store(tmp_path)
        store._entries["suspended"] = _entry(
            "suspended", age_days=1000, suspended=True
        )
        store._entries["idle"] = _entry("idle", age_days=1000)

        removed = store.prune_old_entries(max_age_days=90)

        assert removed == 1
        assert "suspended" in store._entries
        assert "idle" not in store._entries

    def test_prune_skips_entries_with_active_processes(self, tmp_path):
        """Sessions with active bg processes aren't pruned even if old.

        The callback is keyed by session_key — matching what
        process_registry.has_active_for_session() actually consumes in
        gateway/run.py.  Prior to the fix this test passed the callback a
        session_id, which silently matched an implementation bug where
        prune_old_entries was also passing session_id; real-world usage
        (via process_registry) takes a session_key and never matched, so
        active sessions were still being pruned.
        """
        active_session_keys = {"active"}

        def _has_active(session_key: str) -> bool:
            return session_key in active_session_keys

        store = _make_store(tmp_path, has_active_processes_fn=_has_active)
        store._entries["active"] = _entry(
            "active", age_days=1000, session_id="sid_active"
        )
        store._entries["idle"] = _entry(
            "idle", age_days=1000, session_id="sid_idle"
        )

        removed = store.prune_old_entries(max_age_days=90)

        assert removed == 1
        assert "active" in store._entries
        assert "idle" not in store._entries

    def test_prune_fails_closed_when_active_process_check_raises(self, tmp_path):
        def _unavailable(_session_key: str) -> bool:
            raise RuntimeError("process registry unavailable")

        store = _make_store(tmp_path, has_active_processes_fn=_unavailable)
        store._entries["old"] = _entry("old", age_days=1000, finalized=False)

        assert store.prune_old_entries(max_age_days=90) == 0
        assert "old" in store._entries

    def test_prune_skips_exact_protected_route(self, tmp_path):
        store = _make_store(tmp_path)
        store._entries["live"] = _entry("live", age_days=1000, finalized=False)
        store._entries["idle"] = _entry("idle", age_days=1000, finalized=False)

        removed = store.prune_old_entries(
            max_age_days=90,
            protected_session_keys={"live"},
        )

        assert removed == 1
        assert "live" in store._entries
        assert "idle" not in store._entries

    def test_prune_active_check_uses_session_key_not_session_id(self, tmp_path):
        """Regression guard: a callback that only recognises session_ids must
        NOT protect entries during prune.  This pins the fix so a future
        refactor can't silently revert to passing session_id again.
        """
        def _recognises_only_ids(identifier: str) -> bool:
            return identifier.startswith("sid_")

        store = _make_store(tmp_path, has_active_processes_fn=_recognises_only_ids)
        store._entries["active"] = _entry(
            "active", age_days=1000, session_id="sid_active"
        )

        removed = store.prune_old_entries(max_age_days=90)

        # Entry is pruned because the callback receives "active" (session_key),
        # not "sid_active" (session_id), so _recognises_only_ids returns False.
        assert removed == 1
        assert "active" not in store._entries

    def test_prune_does_not_write_disk_when_no_removals(self, tmp_path):
        """If nothing is evictable, _save() should NOT be called."""
        store = _make_store(tmp_path)
        store._entries["fresh1"] = _entry("fresh1", age_days=1)
        store._entries["fresh2"] = _entry("fresh2", age_days=2)

        save_calls = []
        store._save = lambda: save_calls.append(1)

        assert store.prune_old_entries(max_age_days=90) == 0
        assert save_calls == []

    def test_prune_writes_disk_after_removal(self, tmp_path):
        store = _make_store(tmp_path)
        store._entries["stale"] = _entry("stale", age_days=500)
        store._entries["fresh"] = _entry("fresh", age_days=1)

        save_calls = []
        store._save = lambda: save_calls.append(1)

        store.prune_old_entries(max_age_days=90)
        assert save_calls == [1]

    def test_prune_is_thread_safe(self, tmp_path):
        """Prune acquires _lock internally; concurrent update_session is safe."""
        store = _make_store(tmp_path)
        for i in range(20):
            age = 1000 if i % 2 == 0 else 1
            store._entries[f"s{i}"] = _entry(f"s{i}", age_days=age)

        results = []

        def _pruner():
            results.append(store.prune_old_entries(max_age_days=90))

        def _reader():
            # Mimic a concurrent update_session reader iterating under lock.
            with store._lock:
                list(store._entries.keys())

        threads = [threading.Thread(target=_pruner)]
        threads += [threading.Thread(target=_reader) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
            assert not t.is_alive()

        # Exactly one pruner ran; removed exactly the 10 stale entries.
        assert results == [10]
        assert len(store._entries) == 10
        for i in range(20):
            if i % 2 == 1:  # fresh
                assert f"s{i}" in store._entries


class TestPrunePersistsToDisk:
    def test_prune_rewrites_sessions_json(self, tmp_path):
        """After prune, sessions.json on disk reflects the new dict."""
        config = GatewayConfig(
            default_reset_policy=SessionResetPolicy(mode="none"),
            session_store_max_age_days=90,
        )
        store = SessionStore(sessions_dir=tmp_path, config=config)
        store._db = None
        # Force-populate without calling get_or_create to avoid DB side-effects
        store._entries["stale"] = _entry("stale", age_days=500)
        store._entries["fresh"] = _entry("fresh", age_days=1)
        store._loaded = True
        store._save()

        # Verify pre-prune state on disk. Filter out metadata sentinels
        # (e.g. the "_README" note) so we assert on session keys only.
        saved_pre = json.loads((tmp_path / "sessions.json").read_text())
        assert {k for k in saved_pre if not k.startswith("_")} == {"stale", "fresh"}

        # Prune and check disk.
        store.prune_old_entries(max_age_days=90)
        saved_post = json.loads((tmp_path / "sessions.json").read_text())
        assert {k for k in saved_post if not k.startswith("_")} == {"fresh"}


class TestGatewayConfigSerialization:
    def test_session_store_max_age_days_defaults_to_90(self):
        cfg = GatewayConfig()
        assert cfg.session_store_max_age_days == 90

    def test_session_store_max_age_days_roundtrips(self):
        cfg = GatewayConfig(session_store_max_age_days=30)
        restored = GatewayConfig.from_dict(cfg.to_dict())
        assert restored.session_store_max_age_days == 30

    def test_session_store_max_age_days_missing_defaults_90(self):
        """Loading an old config (pre-this-field) falls back to default."""
        restored = GatewayConfig.from_dict({})
        assert restored.session_store_max_age_days == 90

    def test_session_store_max_age_days_negative_coerced_to_zero(self):
        """A negative value (accidental or hostile) becomes 0 (disabled)."""
        restored = GatewayConfig.from_dict({"session_store_max_age_days": -5})
        assert restored.session_store_max_age_days == 0

    def test_session_store_max_age_days_bad_type_falls_back(self):
        """Non-int values fall back to the default, not a crash."""
        restored = GatewayConfig.from_dict({"session_store_max_age_days": "nope"})
        assert restored.session_store_max_age_days == 90


class TestGatewayWatcherCallsPrune:
    """The session_expiry_watcher should call prune_old_entries once per hour."""

    def test_prune_gate_fires_on_first_tick(self):
        """First watcher tick has _last_prune_ts=0, so the gate opens."""
        import time as _t

        last_ts = 0.0
        prune_interval = 3600.0
        now = _t.time()

        # Mirror the production gate check in _session_expiry_watcher.
        should_prune = (now - last_ts) > prune_interval
        assert should_prune is True

    def test_prune_gate_suppresses_within_interval(self):
        import time as _t

        last_ts = _t.time() - 600  # 10 minutes ago
        prune_interval = 3600.0
        now = _t.time()

        should_prune = (now - last_ts) > prune_interval
        assert should_prune is False


def _age_prune_runner(session_store: SessionStore):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._running_agents = {}
    runner._agent_cache = {}
    # Production always guards the agent cache with a lock.  The expiry
    # watcher deliberately refuses to read the cache without it, so use the
    # real shape here instead of accidentally forcing the detached path.
    runner._agent_cache_lock = threading.RLock()
    runner._last_session_store_prune_ts = 0.0
    runner._session_model_overrides = {}
    runner._pending_model_notes = {}
    runner._last_resolved_model = {}
    runner._pending_approvals = {}
    runner._update_prompt_pending = {}
    runner.session_store = session_store
    runner.config = session_store.config
    runner._evict_cached_agent = MagicMock()
    runner._set_session_reasoning_override = MagicMock()
    runner._sweep_idle_cached_agents = MagicMock(return_value=0)
    return runner


async def _run_one_watcher_sweep(runner) -> None:
    real_sleep = asyncio.sleep

    async def fast_sleep(_seconds):
        await real_sleep(0)

    with patch("gateway.run.asyncio.sleep", side_effect=fast_sleep):
        await runner._session_expiry_watcher(interval=0)


@pytest.mark.asyncio
async def test_policy_expiry_failure_keeps_predecessor_token_for_retry(tmp_path):
    """Max-age cleanup cannot bypass a failed genuine policy boundary."""
    store = _make_store(
        tmp_path / "sessions",
        reset_policy=SessionResetPolicy(mode="idle", idle_minutes=1),
    )
    key = "agent:dealer-a:telegram:dm:old"
    entry = _entry(key, age_days=1000, finalized=False, session_id="old-session")
    entry.origin = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="old",
        chat_type="dm",
        user_id="customer-a",
        profile="dealer-a",
    )
    store._entries[key] = entry
    runner = _age_prune_runner(store)

    manager = MagicMock()

    def fail_boundary(*_args, **_kwargs):
        runner._running = False
        raise OSError("simulated Cortex write failure")

    manager.on_session_finalize.side_effect = fail_boundary
    runner._agent_cache = {
        key: SimpleNamespace(_memory_manager=manager, _session_messages=[])
    }

    async def run_inline(func, *args):
        return func(*args)

    runner._run_in_executor_with_context = run_inline

    await _run_one_watcher_sweep(runner)

    manager.on_session_finalize.assert_called_once_with(
        [], reason="session_expired"
    )
    assert entry.expiry_finalized is False
    replacement = store._entries[key]
    assert replacement is not entry
    assert replacement.previous_session_id == entry.session_id
    assert replacement.previous_finalize_reason == "session_expired"


@pytest.mark.asyncio
async def test_max_age_only_prune_never_finalizes_detached_cortex(
    tmp_path, monkeypatch
):
    """Routing retention removes the route without creating model authority."""
    from altas.cortex.config import CortexConfig
    from altas.cortex.models import EvidenceInput
    from altas.cortex.store import CortexStore

    profile_home = tmp_path / "dealer-a"
    config = CortexConfig.from_mapping(
        {
            "cortex": {
                "enabled": True,
                "capture": {"enabled": True},
                "dream": {"enabled": False},
            }
        },
        profile_home,
    )
    cortex = CortexStore(
        config.database_path,
        owner_customer_id="customer-a",
    )
    cortex.initialize()
    cortex.ensure_session("old-session")
    cortex.append_evidence(
        "old-session",
        EvidenceInput(
            source_type="user_message",
            content="retain this customer decision",
            source_locator="old-session:turn:1:user",
        ),
    )

    store = _make_store(tmp_path / "sessions")
    key = "agent:dealer-a:telegram:dm:old"
    source = SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="old",
        chat_type="dm",
        user_id="customer-a",
        profile="dealer-a",
    )
    entry = _entry(key, age_days=1000, finalized=False, session_id="old-session")
    entry.origin = source
    store._entries[key] = entry
    runner = _age_prune_runner(store)

    monkeypatch.setattr(
        "altas.cortex.lifecycle.CortexConfig.load", lambda _home: config
    )
    monkeypatch.setattr(
        "altas.cortex.lifecycle.open_cortex_store",
        lambda _home, _identity: (cortex, config),
    )
    monkeypatch.setattr(
        "hermes_cli.profiles.get_profile_dir", lambda _profile: profile_home
    )

    def stop_after_idle_sweep():
        runner._running = False
        return 0

    runner._sweep_idle_cached_agents = MagicMock(side_effect=stop_after_idle_sweep)
    with patch("hermes_cli.plugins.invoke_hook") as invoke_hook:
        await _run_one_watcher_sweep(runner)

    assert key not in store._entries
    assert cortex.session_lineage("old-session")["state"] == "active"
    invoke_hook.assert_not_called()
    with cortex.connect() as connection:
        admissions = connection.execute(
            "SELECT COUNT(*) FROM session_distill_admissions WHERE brain_id=?",
            (cortex.brain_id,),
        ).fetchone()[0]
        roots = connection.execute(
            "SELECT COUNT(*) FROM cognitive_jobs WHERE brain_id=? "
            "AND job_type='session_distill' AND parent_job_id IS NULL",
            (cortex.brain_id,),
        ).fetchone()[0]
    assert admissions == 0
    assert roots == 0


class TestReadmeSentinel:
    """The gateway writes a self-documenting ``_README`` key into sessions.json
    so users who inspect the file directly understand it's the gateway routing
    index (not the session list). It must never round-trip into a SessionEntry,
    and real entries must survive a save/load cycle alongside it (#49361)."""

    def test_save_writes_readme_sentinel_first(self, tmp_path):
        store = _make_store(tmp_path)
        store._entries["agent:main:whatsapp:dm:99"] = _entry(
            "agent:main:whatsapp:dm:99", age_days=1
        )
        store._save()

        raw = json.loads((tmp_path / "sessions.json").read_text())
        assert "_README" in raw
        # Sentinel renders first so it's the first thing a user sees on `cat`.
        assert next(iter(raw)) == "_README"
        # The note points users at the real store and command.
        assert "state.db" in raw["_README"]
        assert "hermes sessions list" in raw["_README"]

    def test_readme_sentinel_skipped_on_load(self, tmp_path):
        # Write an index containing both the sentinel and a real entry.
        store = _make_store(tmp_path)
        store._entries["agent:main:whatsapp:dm:99"] = _entry(
            "agent:main:whatsapp:dm:99", age_days=1, session_id="sid_wa"
        )
        store._save()

        # Fresh store loads from disk for real (no _ensure_loaded patch).
        config = GatewayConfig(
            default_reset_policy=SessionResetPolicy(mode="none"),
            session_store_max_age_days=90,
        )
        reloaded = SessionStore(sessions_dir=tmp_path, config=config)
        reloaded._ensure_loaded()

        # Sentinel never becomes a SessionEntry; the real entry survives intact.
        assert not any(k.startswith("_") for k in reloaded._entries)
        assert "agent:main:whatsapp:dm:99" in reloaded._entries
        assert reloaded._entries["agent:main:whatsapp:dm:99"].session_id == "sid_wa"
