"""Contract and persistence tests for native Atlas Task Threads."""

from __future__ import annotations

import subprocess
import threading

import pytest

from altas.task_threads.store import ApprovalNotFoundError, TaskThreadStore
from hermes_cli import kanban_db as kb
from hermes_cli import projects_db as pdb
from tools import approval as approval_module


@pytest.fixture
def task_thread_store(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    return TaskThreadStore(db_path=home / "kanban.db")


def _create_thread(
    store: TaskThreadStore,
    index: int,
    *,
    voice_workspace_id: str = "voice-demo",
) -> dict:
    return store.create_thread(
        idempotency_key=f"create-{index}",
        title=f"Thread {index}",
        goal=f"Complete task {index}",
        worker_profile_id="worker",
        voice_workspace_id=voice_workspace_id,
    )


def test_three_threads_are_durable_idempotent_and_not_dispatchable(
    task_thread_store,
):
    created = [_create_thread(task_thread_store, index) for index in range(3)]

    repeated = _create_thread(task_thread_store, 1)
    threads, cursor = task_thread_store.list_threads()

    assert repeated["id"] == created[1]["id"]
    assert [thread["title"] for thread in threads] == [
        "Thread 0",
        "Thread 1",
        "Thread 2",
    ]
    assert {thread["status"] for thread in threads} == {"queued"}
    assert cursor > 0

    with kb.connect_closing(task_thread_store._db_path) as conn:
        tasks = kb.list_tasks(conn)
        dispatch = kb.dispatch_once(
            conn,
            spawn_fn=lambda *_args, **_kwargs: pytest.fail(
                "interactive Task Thread reached the Kanban dispatcher"
            ),
        )

    assert {task.execution_mode for task in tasks} == {"interactive"}
    assert dispatch.spawned == []


def test_events_focus_turns_and_approvals_replay_by_sequence(
    task_thread_store,
):
    thread = _create_thread(task_thread_store, 0)
    initial_events, initial_cursor = task_thread_store.list_events()
    assert [event["name"] for event in initial_events] == [
        "thread.created",
        "thread.status_changed",
    ]

    turn, was_created = task_thread_store.create_turn(
        thread["id"],
        kind="initial",
        instruction=thread["goal"],
        idempotency_key="turn-0",
    )
    assert was_created is True
    task_thread_store.set_turn_status(
        thread["id"], "running", turn_id=turn["id"]
    )
    task_thread_store.set_status(
        thread["id"], "running", turn_id=turn["id"]
    )
    focused, _ = task_thread_store.focus_thread(
        "voice-demo", thread["id"]
    )
    assert focused == thread["id"]

    approval, _ = task_thread_store.record_approval(
        "missing-runtime",
        approval_id="unused",
        command="rm example",
        description="dangerous example",
        allow_permanent=False,
    ) or (None, None)
    assert approval is None

    task_thread_store.bind_runtime(
        thread["id"],
        stored_session_id="stored-0",
        runtime_session_id="runtime-0",
    )
    recorded = task_thread_store.record_approval(
        "runtime-0",
        approval_id="approval-0",
        command="rm example",
        description="dangerous example",
        allow_permanent=False,
    )
    assert recorded is not None
    approval, requested_event = recorded
    assert approval["approval_id"] == "approval-0"
    assert requested_event["name"] == "approval.requested"
    assert task_thread_store.get_thread(thread["id"])[
        "pending_approval_id"
    ] == "approval-0"
    assert task_thread_store.get_thread(thread["id"])[
        "status"
    ] == "waiting_approval"

    resolved, resolved_event = task_thread_store.resolve_approval(
        "approval-0", "once"
    )
    assert resolved["choice"] == "once"
    assert resolved_event["name"] == "approval.resolved"
    assert task_thread_store.get_thread(thread["id"])[
        "pending_approval_id"
    ] is None
    assert task_thread_store.get_thread(thread["id"])["status"] == "running"
    with pytest.raises(ApprovalNotFoundError):
        task_thread_store.resolve_approval("approval-0", "deny")

    task_thread_store.set_turn_status(
        thread["id"], "completed", turn_id=turn["id"]
    )
    completed, _ = task_thread_store.set_status(
        thread["id"],
        "completed",
        summary="finished",
        turn_id=turn["id"],
    )
    assert completed["status"] == "completed"
    reopened, _ = task_thread_store.set_status(
        thread["id"],
        "running",
        turn_id=turn["id"],
        allow_reopen=True,
    )
    assert reopened["status"] == "running"

    replay, replay_cursor = task_thread_store.list_events(
        after_sequence=initial_cursor,
        thread_id=thread["id"],
    )
    sequences = [event["sequence"] for event in replay]
    assert sequences == sorted(sequences)
    assert len(sequences) == len(set(sequences))
    assert replay_cursor == sequences[-1]
    assert {
        "turn.queued",
        "turn.started",
        "thread.focused",
        "approval.requested",
        "approval.resolved",
        "turn.completed",
        "thread.completed",
    } <= {event["name"] for event in replay}


def test_gateway_restart_reconciliation_is_fail_closed_and_idempotent(
    task_thread_store,
):
    interrupted = _create_thread(task_thread_store, 0)
    interrupted_turn, _ = task_thread_store.create_turn(
        interrupted["id"],
        kind="initial",
        instruction=interrupted["goal"],
        idempotency_key="restart-turn",
    )
    task_thread_store.bind_runtime(
        interrupted["id"],
        stored_session_id="stored-dead",
        runtime_session_id="runtime-dead",
    )
    task_thread_store.set_turn_status(
        interrupted["id"],
        "running",
        turn_id=interrupted_turn["id"],
    )
    task_thread_store.set_status(
        interrupted["id"],
        "running",
        turn_id=interrupted_turn["id"],
    )
    task_thread_store.record_approval(
        "runtime-dead",
        approval_id="approval-dead",
        command="rm example",
        description="approval waiter is process-local",
        allow_permanent=False,
    )

    live = _create_thread(task_thread_store, 1)
    task_thread_store.bind_runtime(
        live["id"],
        stored_session_id="stored-live",
        runtime_session_id="runtime-live",
    )
    task_thread_store.set_status(live["id"], "running")

    completed = _create_thread(task_thread_store, 2)
    task_thread_store.bind_runtime(
        completed["id"],
        stored_session_id="stored-complete",
        runtime_session_id="runtime-complete",
    )
    task_thread_store.set_status(
        completed["id"],
        "completed",
        summary="already done",
    )
    _, before_reconcile = task_thread_store.list_events()

    reconciled = task_thread_store.reconcile_orphaned_runtimes(
        {"runtime-live"}
    )

    assert [thread["id"] for thread in reconciled] == [interrupted["id"]]
    interrupted_after = task_thread_store.get_thread(interrupted["id"])
    assert interrupted_after["status"] == "interrupted"
    assert interrupted_after["runtime_session_id"] is None
    assert interrupted_after["stored_session_id"] == "stored-dead"
    assert "Gateway restarted" in interrupted_after["blocker"]
    approval = task_thread_store.list_approvals(
        thread_id=interrupted["id"]
    )[0]
    assert approval["status"] == "resolved"
    assert approval["choice"] == "deny"

    live_after = task_thread_store.get_thread(live["id"])
    assert live_after["status"] == "running"
    assert live_after["runtime_session_id"] == "runtime-live"
    completed_after = task_thread_store.get_thread(completed["id"])
    assert completed_after["status"] == "completed"
    assert completed_after["runtime_session_id"] is None

    with kb.connect_closing(task_thread_store._db_path) as conn:
        turn = conn.execute(
            "SELECT status FROM atlas_task_turns WHERE id = ?",
            (interrupted_turn["id"],),
        ).fetchone()
    assert turn["status"] == "interrupted"

    replay, after_reconcile = task_thread_store.list_events(
        after_sequence=before_reconcile,
        thread_id=interrupted["id"],
    )
    assert {
        "approval.resolved",
        "turn.failed",
        "thread.status_changed",
        "thread.interrupted",
    } <= {event["name"] for event in replay}
    assert any(
        event["payload"].get("reason") == "gateway_restart"
        for event in replay
    )

    assert task_thread_store.reconcile_orphaned_runtimes(
        {"runtime-live"}
    ) == []
    _, final_cursor = task_thread_store.list_events()
    assert final_cursor == after_reconcile


def test_isolated_project_worktree_materializes_and_rejects_branch_collision(
    task_thread_store,
    tmp_path,
):
    repo = tmp_path / "project"
    repo.mkdir()
    subprocess.run(
        ["git", "init", "-b", "main", str(repo)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Atlas Test"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "config",
            "user.email",
            "atlas@example.com",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    (repo / "README.md").write_text("atlas\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "README.md"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "initial"],
        check=True,
        capture_output=True,
        text=True,
    )
    with pdb.connect_closing() as conn:
        project_id = pdb.create_project(
            conn,
            name="Task Thread Project",
            folders=[str(repo)],
        )

    thread = task_thread_store.create_thread(
        idempotency_key="worktree-thread",
        title="Implement feature",
        goal="Build the isolated change",
        worker_profile_id="worker",
        project_id=project_id,
        workspace_mode="isolated_worktree",
    )
    assert thread["workspace_kind"] == "worktree"
    assert thread["workspace_path"] != str(repo)

    materialized = task_thread_store.materialize_workspace(thread["id"])
    worktree = materialized["workspace_path"]
    assert worktree
    actual_branch = subprocess.run(
        ["git", "-C", worktree, "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert actual_branch == materialized["branch"]

    subprocess.run(
        ["git", "-C", worktree, "branch", "-m", "unexpected-collision"],
        check=True,
        capture_output=True,
        text=True,
    )
    with pytest.raises(RuntimeError, match="refusing to reuse task worktree"):
        task_thread_store.materialize_workspace(thread["id"])


def test_gateway_approval_id_targets_exact_pending_request(monkeypatch):
    session_key = "task-thread-approval-session"
    notified: list[dict] = []
    hooks: list[tuple[str, dict]] = []
    result: dict = {}
    notified_event = threading.Event()

    monkeypatch.setattr(
        approval_module,
        "_get_approval_config",
        lambda: {"gateway_timeout": 5},
    )
    monkeypatch.setattr(
        approval_module,
        "_fire_approval_hook",
        lambda name, **payload: hooks.append((name, payload)),
    )

    def notify(payload):
        notified.append(dict(payload))
        notified_event.set()

    def await_decision():
        result.update(
            approval_module._await_gateway_decision(
                session_key,
                notify,
                {
                    "command": "rm example",
                    "description": "dangerous example",
                    "pattern_key": "example",
                    "pattern_keys": ["example"],
                },
            )
        )

    waiter = threading.Thread(target=await_decision, daemon=True)
    waiter.start()
    assert notified_event.wait(timeout=2)
    approval_id = notified[0]["approval_id"]

    wrong = approval_module.resolve_gateway_approval(
        session_key,
        "deny",
        approval_id="approval-wrong",
    )
    assert wrong == 0
    assert waiter.is_alive()

    resolved = approval_module.resolve_gateway_approval(
        session_key,
        "once",
        approval_id=approval_id,
    )
    waiter.join(timeout=2)

    assert resolved == 1
    assert not waiter.is_alive()
    assert result == {"resolved": True, "choice": "once", "reason": None}
    assert hooks[0][1]["approval_id"] == approval_id
    assert hooks[-1][1]["approval_id"] == approval_id
