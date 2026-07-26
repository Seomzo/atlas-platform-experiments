from pathlib import Path
import subprocess
import sys

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


def test_cancel_kills_only_registered_task_process(store, contract):
    store.create_task(contract, state="executing")
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    controller = TaskProcessController(store)
    try:
        controller.register(contract.task_id, process.pid, "fake-runtime-sleep")
        stopped = controller.cancel_task(contract.task_id)
        assert process.pid in stopped
        process.wait(timeout=5)
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            process.kill()
