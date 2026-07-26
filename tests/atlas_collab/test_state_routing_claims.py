from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time

import psutil
import pytest

from tools.atlas_collab.adapters.runtime import FakeRuntime
from tools.atlas_collab.claims import ClaimConflict, acquire_claim, release_claims
from tools.atlas_collab.models import CollaborationEvent, ContractError
from tools.atlas_collab.process_control import TaskProcessController
from tools.atlas_collab.routing import Router, RoutingRejected


def _question(contract, *, actor_role="coordinator", actor_id="agent-coordinator"):
    return CollaborationEvent(
        task_id=contract.task_id,
        workstream_id=contract.workstream_id,
        actor_id=actor_id,
        actor_role=actor_role,
        event_type="QUESTION",
        status="executing",
        summary="Confirm the stable fixture contract.",
        intended_for=("agent-implementer",),
        base_sha=contract.base_sha,
    )


def test_only_coordinator_transitions_and_restart_replays(store, contract):
    assert store.create_task(contract)
    with pytest.raises(ContractError, match="only the coordinator"):
        store.transition("DOGFOOD-1", "context_sync", actor_role="implementer")
    assert store.transition("DOGFOOD-1", "context_sync", actor_role="coordinator")
    assert not store.transition("DOGFOOD-1", "context_sync", actor_role="coordinator")
    event = _question(contract)
    assert store.record_event(event)
    assert not store.record_event(event)
    path = store.path
    store.close()
    from tools.atlas_collab.state import StateStore

    reopened = StateStore(path)
    assert reopened.task("DOGFOOD-1")["state"] == "context_sync"
    assert [item["event_id"] for item in reopened.events("DOGFOOD-1")] == [
        event.event_id
    ]
    reopened.close()


def test_agent_binding_survives_restart(store, contract):
    store.create_task(contract)
    store.register_agent(
        agent_id="agent-implementer",
        role="implementer",
        display_name="atlas-implementer",
        public_key="2" * 64,
        runtime="hermes-acp",
        profile="atlas-collab-implementer",
    )
    store.bind_agent(
        task_id=contract.task_id,
        agent_id="agent-implementer",
        branch="codex/ws-22-implementer",
        worktree="/safe/example/worktree",
        session_id="DOGFOOD-1:implementer",
        git_name="Atlas Implementer",
        git_email="atlas-implementer@users.noreply.github.com",
    )
    binding = store.task_agents(contract.task_id)[0]
    assert binding["branch"] == "codex/ws-22-implementer"
    assert binding["session_id"] == "DOGFOOD-1:implementer"
    assert binding["git_name"] == "Atlas Implementer"
    assert {
        row["name"]
        for row in store.connection.execute("PRAGMA table_info(task_agents)")
    } >= {"branch", "worktree", "session_id", "git_name", "git_email"}


def test_legacy_claim_constraint_migrates_without_losing_history(tmp_path):
    from tools.atlas_collab.state import StateStore

    path = tmp_path / "legacy.db"
    with StateStore(path):
        pass
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        ALTER TABLE claims RENAME TO claims_new;
        CREATE TABLE claims (
            claim_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES tasks(task_id),
            agent_id TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('path', 'interface')),
            resource TEXT NOT NULL,
            acquired_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            released_at TEXT,
            UNIQUE(task_id, kind, resource, released_at)
        );
        INSERT INTO claims SELECT * FROM claims_new;
        DROP TABLE claims_new;
        """
    )
    connection.commit()
    connection.close()

    with StateStore(path) as migrated:
        sql = migrated.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'claims'"
        ).fetchone()["sql"]
        assert "UNIQUE(task_id, kind, resource, released_at)" not in sql
        assert (
            migrated.connection.execute(
                "SELECT value FROM metadata WHERE key = 'schema_version'"
            ).fetchone()["value"]
            == "4"
        )


def test_context_acknowledgement_requires_every_requested_role(store, contract):
    store.create_task(contract)
    store.update_task_refs(contract.task_id, context_hash="context-hash")
    for index, role in enumerate(
        ("coordinator", "implementer", "reviewer"),
        start=1,
    ):
        store.register_agent(
            agent_id=f"agent-{role}",
            role=role,
            display_name=f"atlas-{role}",
            public_key=f"{index:064x}",
            runtime="hermes-acp",
            profile=f"atlas-collab-{role}",
        )
    store.acknowledge(
        contract.task_id,
        "agent-coordinator",
        contract.digest,
        "context-hash",
    )
    assert not store.all_acknowledged(contract.task_id)
    for role in ("implementer", "reviewer"):
        store.acknowledge(
            contract.task_id,
            f"agent-{role}",
            contract.digest,
            "context-hash",
        )
    assert store.all_acknowledged(contract.task_id)


def test_path_and_interface_claim_conflicts(store, contract):
    store.create_task(contract)
    first = acquire_claim(
        store,
        task_id=contract.task_id,
        agent_id="agent-implementer",
        kind="path",
        resource="tools/atlas_collab",
    )
    assert first.startswith("claim-")
    with pytest.raises(ClaimConflict):
        acquire_claim(
            store,
            task_id=contract.task_id,
            agent_id="agent-reviewer",
            kind="path",
            resource="tools/atlas_collab/models.py",
        )
    assert release_claims(store, contract.task_id, "agent-implementer") == 1
    acquire_claim(
        store,
        task_id=contract.task_id,
        agent_id="agent-reviewer",
        kind="path",
        resource="tools/atlas_collab/models.py",
    )


def test_claim_expiry_and_concurrent_race_fail_closed(store, contract):
    store.create_task(contract)
    start = datetime(2026, 7, 25, tzinfo=UTC)
    acquire_claim(
        store,
        task_id=contract.task_id,
        agent_id="agent-implementer",
        kind="interface",
        resource="TaskContract",
        ttl_seconds=60,
        now=start,
    )
    acquire_claim(
        store,
        task_id=contract.task_id,
        agent_id="agent-reviewer",
        kind="interface",
        resource="TaskContract",
        ttl_seconds=60,
        now=start + timedelta(seconds=61),
    )
    release_claims(store, contract.task_id)

    barrier = threading.Barrier(2)

    def attempt(agent_id):
        from tools.atlas_collab.state import StateStore

        with StateStore(store.path) as competing:
            barrier.wait(timeout=5)
            try:
                acquire_claim(
                    competing,
                    task_id=contract.task_id,
                    agent_id=agent_id,
                    kind="path",
                    resource="tools/atlas_collab",
                )
            except ClaimConflict:
                return "conflict"
            return "acquired"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ("agent-a", "agent-b")))
    assert sorted(results) == ["acquired", "conflict"]


def test_router_enforces_worker_mediation_one_turn_and_failure_breaker(
    store, contract, tmp_path
):
    store.create_task(contract, state="executing")
    router = Router(store, turn_budget=3, max_failures=1)
    runtime = FakeRuntime(["contract confirmed"])
    event = _question(contract)
    result = router.run(
        runtime,
        event,
        target_agent_id="agent-implementer",
        target_role="implementer",
        session_id="session-1",
        worktree=tmp_path,
        prompt="Use only the compact task event.",
    )
    assert result["response"] == "contract confirmed"

    worker_event = _question(
        contract,
        actor_role="reviewer",
        actor_id="agent-reviewer",
    )
    with pytest.raises(RoutingRejected, match="through coordinator"):
        router.authorize(
            worker_event,
            target_agent_id="agent-implementer",
            target_role="implementer",
        )

    runtime.fail_next = True
    with pytest.raises(RuntimeError, match="injected"):
        router.run(
            runtime,
            event,
            target_agent_id="agent-implementer",
            target_role="implementer",
            session_id="session-2",
            worktree=tmp_path,
            prompt="fail safely",
        )
    with pytest.raises(RoutingRejected, match="circuit breaker"):
        router.authorize(
            event,
            target_agent_id="agent-implementer",
            target_role="implementer",
        )


def test_runtime_retries_use_bounded_exponential_backoff(store, contract, tmp_path):
    class FlakyRuntime(FakeRuntime):
        def __init__(self):
            super().__init__(["recovered"])
            self.failures_remaining = 2

        def start_turn(self, **kwargs):
            if self.failures_remaining:
                self.failures_remaining -= 1
                raise RuntimeError("transient runtime failure")
            return super().start_turn(**kwargs)

    store.create_task(contract, state="executing")
    delays = []
    result = Router(
        store,
        turn_budget=4,
        max_failures=3,
    ).run_with_retry(
        FlakyRuntime(),
        _question(contract),
        target_agent_id="agent-implementer",
        target_role="implementer",
        session_id="persistent-session",
        worktree=tmp_path,
        prompt="Recover within the same accepted task context.",
        max_retries=2,
        base_delay_seconds=0.5,
        sleep=delays.append,
    )
    assert result["response"] == "recovered"
    assert delays == [0.5, 1.0]
    task = store.task(contract.task_id)
    assert task["failure_count"] == 2
    assert task["turn_count"] == 3


def test_pause_blocks_turn_and_resume_restores_state(store, contract):
    store.create_task(contract, state="executing")
    store.transition(contract.task_id, "paused", actor_role="coordinator")
    router = Router(store)
    with pytest.raises(RoutingRejected, match="paused"):
        router.authorize(
            _question(contract),
            target_agent_id="agent-implementer",
            target_role="implementer",
        )
    store.transition(contract.task_id, "resume", actor_role="coordinator")
    assert store.task(contract.task_id)["state"] == "executing"


def test_cost_and_repeated_clarification_budgets_fail_closed(store, contract):
    store.create_task(contract, state="executing")
    assert not store.record_clarification(contract.task_id, limit=2)
    assert store.record_clarification(contract.task_id, limit=2)
    store.add_cost(contract.task_id, 0.01)
    with pytest.raises(RoutingRejected, match="cost budget"):
        Router(store, cost_budget_usd=0.01).authorize(
            _question(contract),
            target_agent_id="agent-implementer",
            target_role="implementer",
        )


@pytest.mark.live_system_guard_bypass
def test_cancel_kills_registered_task_process_and_child_tree(store, contract, tmp_path):
    store.create_task(contract, state="executing")
    child_pid_file = tmp_path / "child.pid"
    script = (
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        '"import time; time.sleep(60)"])\n'
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid), encoding='utf-8')\n"
        "time.sleep(60)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(child_pid_file)],
        start_new_session=True,
    )
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    controller = TaskProcessController(store)
    child_pid = 0
    try:
        deadline = time.monotonic() + 5
        while not child_pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))
        controller.register(contract.task_id, process.pid, "fake-runtime-sleep")
        stopped = controller.cancel_task(contract.task_id)
        assert process.pid in stopped
        assert child_pid in stopped
        process.wait(timeout=5)
        assert process.poll() is not None
        assert not psutil.pid_exists(child_pid)
        assert unrelated.poll() is None
    finally:
        if process.poll() is None:
            process.kill()
        if child_pid and psutil.pid_exists(child_pid):
            psutil.Process(child_pid).kill()
        if unrelated.poll() is None:
            unrelated.kill()
            unrelated.wait(timeout=5)
