"""Gateway integration coverage for Atlas Task Thread RPCs."""

from __future__ import annotations

from altas.task_threads.store import TaskThreadStore
from hermes_constants import (
    reset_hermes_home_override,
    set_hermes_home_override,
)
from tools import approval as approval_module
from tui_gateway import server


def test_task_thread_rpc_vertical_slice(monkeypatch, tmp_path):
    home = tmp_path / ".hermes"
    home.mkdir()
    token = set_hermes_home_override(home)
    store = TaskThreadStore(db_path=home / "kanban.db")
    frames: list[dict] = []
    session_ids: list[str] = []

    def fake_session_create(rid, _params):
        index = len(session_ids)
        runtime_id = f"runtime-{index}"
        stored_id = f"stored-{index}"
        session_ids.append(runtime_id)
        server._sessions[runtime_id] = {
            "_finalized": False,
            "session_key": stored_id,
        }
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {
                "session_id": runtime_id,
                "stored_session_id": stored_id,
            },
        }

    def fake_ok(rid, _params):
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"status": "started"},
        }

    monkeypatch.setattr(server, "_task_thread_store", store)
    monkeypatch.setattr(server, "write_json", frames.append)
    monkeypatch.setitem(
        server._methods, "session.create", fake_session_create
    )
    monkeypatch.setitem(server._methods, "prompt.submit", fake_ok)
    monkeypatch.setitem(server._methods, "session.interrupt", fake_ok)

    try:
        created: list[dict] = []
        for index in range(3):
            response = server._methods["threads.create"](
                f"create-{index}",
                {
                    "goal": f"Complete task {index}",
                    "idempotency_key": f"create-{index}",
                    "title": f"Thread {index}",
                    "voice_workspace_id": "voice-demo",
                    "worker_profile_id": "worker",
                },
            )
            assert "error" not in response
            created.append(response["result"]["thread"])

        assert {thread["status"] for thread in created} == {"starting"}
        listed = server._methods["threads.list"]("list", {})
        assert len(listed["result"]["threads"]) == 3
        initial_cursor = listed["result"]["cursor"]

        first = created[0]
        server._emit("message.start", first["runtime_session_id"])
        server._emit(
            "message.delta",
            first["runtime_session_id"],
            {"text": "working"},
        )
        server._emit(
            "message.complete",
            first["runtime_session_id"],
            {"text": "finished"},
        )
        assert store.get_thread(first["id"])["status"] == "completed"
        assert store.get_thread(first["id"])["last_summary"] == "finished"

        focused = server._methods["threads.focus"](
            "focus",
            {
                "thread_id": created[1]["id"],
                "voice_workspace_id": "voice-demo",
            },
        )
        assert focused["result"] == {
            "focused_thread_id": created[1]["id"]
        }

        interrupted = server._methods["threads.interrupt"](
            "interrupt",
            {
                "idempotency_key": "interrupt-2",
                "thread_id": created[2]["id"],
            },
        )
        assert interrupted["result"]["thread"]["status"] == "interrupted"

        replay = server._methods["threads.events"](
            "events",
            {"after_sequence": initial_cursor},
        )["result"]
        sequences = [event["sequence"] for event in replay["events"]]
        assert sequences == sorted(sequences)
        assert len(sequences) == len(set(sequences))
        assert {
            "thread.completed",
            "thread.focused",
            "thread.interrupted",
            "worker.message_completed",
            "worker.message_delta",
        } <= {event["name"] for event in replay["events"]}
        assert any(
            frame.get("params", {}).get("type") == "threads.event"
            for frame in frames
        )
    finally:
        for session_id in session_ids:
            server._sessions.pop(session_id, None)
        reset_hermes_home_override(token)


def test_gateway_restart_interrupts_stale_work_then_resumes_explicitly(
    monkeypatch,
    tmp_path,
):
    home = tmp_path / ".hermes"
    home.mkdir()
    token = set_hermes_home_override(home)
    monkeypatch.setenv("HERMES_KANBAN_DB", str(home / "kanban.db"))
    store = TaskThreadStore(db_path=home / "kanban.db")
    frames: list[dict] = []
    resume_calls: list[dict] = []
    submit_calls: list[dict] = []

    thread = store.create_thread(
        idempotency_key="restart-thread",
        title="Restart-safe thread",
        goal="Finish the durable work",
        worker_profile_id="worker",
    )
    turn, _ = store.create_turn(
        thread["id"],
        kind="initial",
        instruction=thread["goal"],
        idempotency_key="restart-initial-turn",
    )
    store.bind_runtime(
        thread["id"],
        stored_session_id="stored-before-restart",
        runtime_session_id="runtime-before-restart",
    )
    store.set_turn_status(thread["id"], "running", turn_id=turn["id"])
    store.set_status(thread["id"], "running", turn_id=turn["id"])
    _, before_restart = store.list_events()

    def fake_session_resume(rid, params):
        resume_calls.append(dict(params))
        server._sessions["runtime-after-restart"] = {
            "_finalized": False,
            "session_key": "stored-before-restart",
        }
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"session_id": "runtime-after-restart"},
        }

    def fake_prompt_submit(rid, params):
        submit_calls.append(dict(params))
        return {
            "jsonrpc": "2.0",
            "id": rid,
            "result": {"status": "started"},
        }

    monkeypatch.setattr(server, "_task_thread_store", None)
    monkeypatch.setattr(server, "write_json", frames.append)
    monkeypatch.setitem(
        server._methods, "session.resume", fake_session_resume
    )
    monkeypatch.setitem(
        server._methods, "prompt.submit", fake_prompt_submit
    )
    server._sessions.pop("runtime-before-restart", None)

    try:
        restarted_store = server._get_task_thread_store()
        interrupted = restarted_store.get_thread(thread["id"])
        assert interrupted["status"] == "interrupted"
        assert interrupted["runtime_session_id"] is None
        assert "Gateway restarted" in interrupted["blocker"]

        params = {
            "thread_id": thread["id"],
            "instruction": "Continue after the restart",
            "idempotency_key": "restart-followup",
        }
        response = server._methods["threads.send"]("send", params)

        assert "error" not in response
        resumed = response["result"]["thread"]
        assert response["result"]["accepted"] == "started"
        assert resumed["status"] == "running"
        assert resumed["blocker"] is None
        assert resumed["stored_session_id"] == "stored-before-restart"
        assert resumed["runtime_session_id"] == "runtime-after-restart"
        assert resume_calls == [
            {
                "profile": "worker",
                "session_id": "stored-before-restart",
                "source": "desktop",
            }
        ]
        assert submit_calls == [
            {
                "session_id": "runtime-after-restart",
                "text": "Continue after the restart",
            }
        ]

        duplicate = server._methods["threads.send"](
            "send-duplicate", params
        )
        assert "error" not in duplicate
        assert len(resume_calls) == 1
        assert len(submit_calls) == 1

        replay = server._methods["threads.events"](
            "events",
            {
                "after_sequence": before_restart,
                "thread_id": thread["id"],
            },
        )["result"]
        sequences = [event["sequence"] for event in replay["events"]]
        assert sequences == sorted(sequences)
        assert {
            "thread.interrupted",
            "turn.queued",
            "thread.status_changed",
        } <= {event["name"] for event in replay["events"]}
    finally:
        server._sessions.pop("runtime-after-restart", None)
        reset_hermes_home_override(token)


def test_approval_rpc_resolves_exact_task_thread_request(
    monkeypatch,
    tmp_path,
):
    home = tmp_path / ".hermes"
    home.mkdir()
    token = set_hermes_home_override(home)
    store = TaskThreadStore(db_path=home / "kanban.db")
    runtime_id = "approval-runtime"
    session_key = "approval-stored"
    server._sessions[runtime_id] = {
        "_finalized": False,
        "session_key": session_key,
    }
    monkeypatch.setattr(server, "_task_thread_store", store)
    monkeypatch.setattr(server, "write_json", lambda _frame: None)

    try:
        thread = store.create_thread(
            idempotency_key="approval-thread",
            title="Approval thread",
            goal="Request two approvals",
            worker_profile_id="worker",
        )
        store.bind_runtime(
            thread["id"],
            stored_session_id=session_key,
            runtime_session_id=runtime_id,
        )
        first_id = "approval-first"
        second_id = "approval-second"
        for approval_id in (first_id, second_id):
            recorded = store.record_approval(
                runtime_id,
                approval_id=approval_id,
                command=f"rm {approval_id}",
                description="test approval",
                allow_permanent=False,
            )
            assert recorded is not None

        first_entry = approval_module._ApprovalEntry(
            {"approval_id": first_id}
        )
        second_entry = approval_module._ApprovalEntry(
            {"approval_id": second_id}
        )
        approval_module._gateway_queues[session_key] = [
            first_entry,
            second_entry,
        ]

        response = server._methods["approvals.respond"](
            "respond",
            {"approval_id": second_id, "choice": "once"},
        )

        assert response["result"]["approval"]["approval_id"] == second_id
        assert response["result"]["approval"]["choice"] == "once"
        assert first_entry.event.is_set() is False
        assert second_entry.event.is_set() is True
        assert [
            entry.approval_id
            for entry in approval_module._gateway_queues[session_key]
        ] == [first_id]
        approvals = {
            item["approval_id"]: item for item in store.list_approvals()
        }
        assert approvals[first_id]["status"] == "pending"
        assert approvals[second_id]["status"] == "resolved"
        thread_after_second = store.get_thread(thread["id"])
        assert thread_after_second["status"] == "waiting_approval"
        assert thread_after_second["pending_approval_id"] == first_id

        first_response = server._methods["approvals.respond"](
            "respond-first",
            {"approval_id": first_id, "choice": "deny"},
        )

        assert first_response["result"]["approval"]["approval_id"] == first_id
        assert store.get_thread(thread["id"])["status"] == "running"
    finally:
        approval_module._gateway_queues.pop(session_key, None)
        server._sessions.pop(runtime_id, None)
        reset_hermes_home_override(token)
