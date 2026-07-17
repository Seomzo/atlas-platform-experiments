"""Persistent Atlas Cortex wakes for every profile served by a multiplexer."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from altas.cortex.config import CortexConfig
from altas.cortex.runtime import open_cortex_store
from altas.cortex.scheduler import (
    _managed_profile_binding,
    _profile_cron_jobs,
    ensure_cortex_wake_cron,
    recover_managed_cortex_runtimes,
    run_multiplex_cortex_wake_tick,
    run_multiplex_cortex_wake_ticker,
)
from altas.cortex.store import stable_hash


def _write_profile_config(home: Path) -> CortexConfig:
    home.mkdir(parents=True, exist_ok=True)
    raw = {
        "model": {"provider": "openrouter", "default": "test-model"},
        "cortex": {
            "enabled": True,
            "timezone": "UTC",
            "storage": {"path": "cortex/cortex.db"},
            "dream": {
                "enabled": True,
                "local_time": "04:00",
                "startup_catchup": True,
                "poll_seconds": 30,
                "lease_seconds": 60,
                "max_batch": 20,
            },
            # Keep model jobs queued in this integration test. Deterministic
            # checkpoint work still drains without a provider route.
            "security": {"approved_model_providers": ["local-test-only"]},
        },
    }
    (home / "config.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    return CortexConfig.from_mapping(raw, home)


def _job_state(store: Any, job_id: str) -> tuple[str, int]:
    with store.connect() as connection:
        row = connection.execute(
            "SELECT state, attempt FROM cognitive_jobs WHERE id=?", (job_id,)
        ).fetchone()
    return str(row["state"]), int(row["attempt"])


def test_tick_fires_secondary_profile_and_honors_pause_and_due_hash(
    tmp_path: Path,
) -> None:
    default_home = tmp_path / "default"
    secondary_home = tmp_path / "profiles" / "dealer-a"
    default_config = _write_profile_config(default_home)
    secondary_config = _write_profile_config(secondary_home)

    default_wake_id = ensure_cortex_wake_cron(default_config)
    assert default_wake_id
    _profile_cron_jobs(default_home).pause_job(default_wake_id)
    assert ensure_cortex_wake_cron(secondary_config)

    default_store, _ = open_cortex_store(default_home)
    secondary_store, _ = open_cortex_store(secondary_home)
    default_checkpoint = default_store.enqueue_job(
        "session_checkpoint",
        input_hash=stable_hash("default-paused-checkpoint"),
        input_data={"session_id": "default-session", "reason": "test"},
    )
    secondary_checkpoint = secondary_store.enqueue_job(
        "session_checkpoint",
        input_hash=stable_hash("secondary-checkpoint"),
        input_data={"session_id": "secondary-session", "reason": "test"},
    )
    profiles = (
        ("default", default_home),
        ("dealer-a", secondary_home),
    )
    now = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

    assert run_multiplex_cortex_wake_tick(profiles, now=now, startup=True) == (
        "dealer-a",
    )
    # Repeating the same wake slot must not enqueue another nightly cycle.
    assert run_multiplex_cortex_wake_tick(profiles, now=now, startup=False) == (
        "dealer-a",
    )

    assert _job_state(default_store, default_checkpoint) == ("queued", 0)
    assert _job_state(secondary_store, secondary_checkpoint)[0] == "succeeded"
    with secondary_store.connect() as connection:
        dream_count = connection.execute(
            "SELECT COUNT(*) FROM cognitive_jobs "
            "WHERE brain_id=? AND job_type='dream_cycle'",
            (secondary_store.brain_id,),
        ).fetchone()[0]
    with default_store.connect() as connection:
        paused_dream_count = connection.execute(
            "SELECT COUNT(*) FROM cognitive_jobs "
            "WHERE brain_id=? AND job_type='dream_cycle'",
            (default_store.brain_id,),
        ).fetchone()[0]
    assert dream_count == 1
    assert paused_dream_count == 0


def test_tick_rebuilds_profile_scope_instead_of_reusing_request_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.secret_scope import (
        get_secret,
        reset_secret_scope,
        set_multiplex_active,
        set_secret_scope,
    )
    from hermes_constants import get_hermes_home

    homes = [tmp_path / "default", tmp_path / "profiles" / "dealer-a"]
    configs = {}
    for index, home in enumerate(homes):
        config = _write_profile_config(home)
        configs[home.resolve()] = config
        (home / ".env").write_text(
            f"OPENROUTER_API_KEY=profile-{index}\n", encoding="utf-8"
        )
        assert ensure_cortex_wake_cron(config)

    observed: list[tuple[str, str]] = []

    def fake_open(home: Path) -> tuple[SimpleNamespace, CortexConfig]:
        return SimpleNamespace(path=home), configs[Path(home).resolve()]

    def fake_wake(_store: Any, _config: CortexConfig, **_kwargs: Any) -> None:
        observed.append((
            str(get_hermes_home().resolve()),
            str(get_secret("OPENROUTER_API_KEY")),
        ))

    monkeypatch.setattr("altas.cortex.runtime.open_cortex_store", fake_open)
    monkeypatch.setattr("altas.cortex.scheduler.run_cortex_wake", fake_wake)
    set_multiplex_active(True)
    wrong_scope = set_secret_scope({"OPENROUTER_API_KEY": "wrong-request"})
    try:
        awakened = run_multiplex_cortex_wake_tick((
            ("default", homes[0]),
            ("dealer-a", homes[1]),
        ))
    finally:
        reset_secret_scope(wrong_scope)
        set_multiplex_active(False)

    assert awakened == ("default", "dealer-a")
    assert observed == [
        (str(homes[0].resolve()), "profile-0"),
        (str(homes[1].resolve()), "profile-1"),
    ]


def test_ticker_reenumerates_profiles_and_stops_cooperatively(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stop = threading.Event()
    enumerations: list[tuple[tuple[str, Path], ...]] = []
    startup_flags: list[bool] = []

    def profiles_provider() -> tuple[tuple[str, Path], ...]:
        suffix = len(enumerations)
        profiles = ((f"profile-{suffix}", Path(f"/profiles/{suffix}")),)
        enumerations.append(profiles)
        return profiles

    def fake_tick(
        profiles: Any,
        *,
        startup: bool,
        **_kwargs: Any,
    ) -> tuple[str, ...]:
        tuple(profiles)
        startup_flags.append(startup)
        if len(startup_flags) == 2:
            stop.set()
        return ()

    monkeypatch.setattr(
        "altas.cortex.scheduler.run_multiplex_cortex_wake_tick", fake_tick
    )

    run_multiplex_cortex_wake_ticker(
        stop,
        interval=0.01,
        profiles_provider=profiles_provider,
    )

    assert startup_flags == [True, False]
    assert len(enumerations) == 2
    assert enumerations[0] != enumerations[1]


def test_gateway_starts_exactly_one_cortex_ticker_only_for_multiplex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway import run as gateway_run

    created: list[Any] = []

    class FakeThread:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.started = 0
            created.append(self)

        def start(self) -> None:
            self.started += 1

    monkeypatch.setattr(gateway_run.threading, "Thread", FakeThread)
    stop = threading.Event()

    assert (
        gateway_run._start_cortex_multiplex_wake_thread(
            SimpleNamespace(multiplex_profiles=False), stop
        )
        is None
    )
    thread = gateway_run._start_cortex_multiplex_wake_thread(
        SimpleNamespace(multiplex_profiles=True), stop
    )

    assert thread is created[0]
    assert len(created) == 1
    assert created[0].started == 1
    assert created[0].kwargs["args"] == (stop,)
    assert created[0].kwargs["name"] == "atlas-cortex-multiplex-wake"
    assert created[0].kwargs["daemon"] is True


def test_managed_restart_binding_excludes_chat_and_request_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.secret_scope import set_multiplex_active

    home = tmp_path / "managed"
    home.mkdir()
    (home / ".env").write_text(
        "\n".join((
            "ATLAS_TENANT_ID=tenant-file",
            "ATLAS_STORE_ID=store-file",
            "ATLAS_AGENT_ID=agent-file",
            "ATLAS_DEVICE_ID=device-file",
            "ATLAS_DEVICE_TOKEN=device-token-file",
            "ATLAS_CONTROL_PLANE_URL=https://control.example.test",
            "OPENROUTER_API_KEY=chat-secret",
            "ATLAS_LEASE_TOKEN=stale-lease",
            "ATLAS_JOB_ID=stale-job",
            "ATLAS_CLAIM_TOKEN=stale-claim",
        ))
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ATLAS_TENANT_ID", "tenant-process")
    set_multiplex_active(True)
    try:
        binding = _managed_profile_binding(home)
    finally:
        set_multiplex_active(False)

    assert binding["ATLAS_TENANT_ID"] == "tenant-file"
    assert binding["ATLAS_DEVICE_TOKEN"] == "device-token-file"
    assert "OPENROUTER_API_KEY" not in binding
    assert "ATLAS_LEASE_TOKEN" not in binding
    assert "ATLAS_JOB_ID" not in binding
    assert "ATLAS_CLAIM_TOKEN" not in binding


def test_gateway_restart_recovery_starts_only_managed_profile_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent.secret_scope import current_secret_scope, set_multiplex_active
    from altas.cortex import scheduler as scheduler_module

    local_home = tmp_path / "local"
    managed_home = tmp_path / "managed"
    local_home.mkdir()
    managed_home.mkdir()
    stable_binding = {
        "ATLAS_TENANT_ID": "tenant-a",
        "ATLAS_STORE_ID": "store-a",
        "ATLAS_AGENT_ID": "agent-a",
        "ATLAS_DEVICE_ID": "device-a",
        "ATLAS_DEVICE_TOKEN": "device-token-a",
        "ATLAS_CONTROL_PLANE_URL": "https://control.example.test",
    }
    (managed_home / ".env").write_text(
        "\n".join(f"{key}={value}" for key, value in stable_binding.items())
        + "\nOPENROUTER_API_KEY=must-not-enter-startup-scope\n"
        + "ATLAS_CLAIM_TOKEN=must-not-be-replayed\n",
        encoding="utf-8",
    )
    configs = {
        local_home.resolve(): SimpleNamespace(enabled=True),
        managed_home.resolve(): SimpleNamespace(enabled=True),
    }
    observed_scopes: list[dict[str, str]] = []
    reconciled: list[Any] = []

    def fake_load(home: Path) -> Any:
        return configs[Path(home).resolve()]

    def fake_open(home: Path, *, environ: Any = None) -> tuple[Any, Any]:
        resolved = Path(home).resolve()
        observed_scopes.append(dict(current_secret_scope() or {}))
        owner = (
            "managed:tenant-a:store-a:agent-a"
            if resolved == managed_home.resolve()
            else "local:test"
        )
        return SimpleNamespace(owner_customer_id=owner), configs[resolved]

    managed_supervisor = object()

    def fake_reconcile(store: Any, config: Any) -> Any:
        reconciled.append((store, config, dict(current_secret_scope() or {})))
        return managed_supervisor

    monkeypatch.setattr(
        scheduler_module.CortexConfig,
        "load",
        classmethod(lambda _cls, home: fake_load(home)),
    )
    monkeypatch.setattr("altas.cortex.runtime.open_cortex_store", fake_open)
    monkeypatch.setattr(scheduler_module, "reconcile_cortex_runtime", fake_reconcile)
    set_multiplex_active(True)
    try:
        recovered = recover_managed_cortex_runtimes((
            ("local", local_home),
            ("managed", managed_home),
            ("managed-duplicate", managed_home),
        ))
    finally:
        set_multiplex_active(False)

    assert recovered == ("managed",)
    assert len(reconciled) == 1
    assert observed_scopes == [stable_binding]
    assert reconciled[0][2] == stable_binding


def test_gateway_restart_recovery_enumerates_active_or_multiplex_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gateway import run as gateway_run

    profiles = (("managed", Path("/profiles/managed")),)
    observed: list[Any] = []

    def fake_profiles_to_serve(multiplex: bool) -> Any:
        observed.append(("enumerate", multiplex))
        return profiles

    def fake_recover(value: Any) -> tuple[str, ...]:
        observed.append(("recover", tuple(value)))
        return ("managed",)

    monkeypatch.setattr(
        "hermes_cli.profiles.profiles_to_serve", fake_profiles_to_serve
    )
    monkeypatch.setattr(
        "altas.cortex.scheduler.recover_managed_cortex_runtimes", fake_recover
    )

    assert gateway_run._recover_managed_cortex_after_restart(
        SimpleNamespace(multiplex_profiles=True)
    ) == ("managed",)
    assert observed == [
        ("enumerate", True),
        ("recover", profiles),
    ]
