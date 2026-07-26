from pathlib import Path
import subprocess

import pytest

from tools.atlas_collab.adapters.buzz import FakeBuzzAdapter
from tools.atlas_collab.adapters.github import FakeGitHubAdapter
from tools.atlas_collab.adapters.process import CommandError, CommandRunner
from tools.atlas_collab.models import CollaborationEvent, content_hash
from tools.atlas_collab.orchestrator import Orchestrator


def test_command_timeout_is_a_bounded_sanitized_result(monkeypatch):
    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", time_out)

    result = CommandRunner().run(["claude", "auth", "status"], check=False, timeout=15)
    assert result.code == 124
    assert result.stdout == ""
    assert result.stderr == "command timed out after 15 seconds"

    with pytest.raises(CommandError, match="timed out after 15 seconds"):
        CommandRunner().run(["claude", "auth", "status"], timeout=15)


def test_external_message_and_comment_writes_are_idempotent(store, contract):
    store.create_task(contract)
    buzz = FakeBuzzAdapter()
    github = FakeGitHubAdapter()
    channel, _ = buzz.ensure_channel(
        name="ws-22-dogfood",
        description="fixture",
    )
    event = CollaborationEvent(
        task_id=contract.task_id,
        workstream_id=contract.workstream_id,
        actor_id="agent-coordinator",
        actor_role="coordinator",
        event_type="PLAN_PROPOSED",
        status="planning",
        summary="One implementer path and independent review.",
        base_sha=contract.base_sha,
    )
    first, created = buzz.send_event(store, event, channel_id=channel["id"])
    second, duplicate = buzz.send_event(store, event, channel_id=channel["id"])
    assert first == second
    assert created and not duplicate
    assert len(buzz.messages) == 1

    first, created = github.ensure_comment(
        store, issue=22, marker="plan", body="Milestone plan accepted."
    )
    second, duplicate = github.ensure_comment(
        store, issue=22, marker="plan", body="Milestone plan accepted."
    )
    assert first == second
    assert created and not duplicate
    assert len(github.comments) == 1

    first, created = github.ensure_draft_pr(
        store,
        branch="codex/ws-22-dogfood",
        base="main",
        title="WS-22 dogfood",
        body="Reviewable evidence.",
    )
    second, duplicate = github.ensure_draft_pr(
        store,
        branch="codex/ws-22-dogfood",
        base="main",
        title="WS-22 dogfood",
        body="Reviewable evidence.",
    )
    assert first == second
    assert created and not duplicate
    assert len(github.pull_requests) == 1
    assert first["isDraft"]


def test_pending_external_writes_recover_by_marker_without_duplicates(store, contract):
    store.create_task(contract)
    buzz = FakeBuzzAdapter()
    github = FakeGitHubAdapter()
    event = CollaborationEvent(
        task_id=contract.task_id,
        workstream_id=contract.workstream_id,
        actor_id="agent-coordinator",
        actor_role="coordinator",
        event_type="PLAN_PROPOSED",
        status="planning",
        summary="Recover the accepted plan marker.",
        base_sha=contract.base_sha,
    )
    buzz_key = f"buzz:channel-1:{event.idempotency_key}"
    store.reserve_external_write(
        buzz_key,
        system="buzz",
        target="channel-1",
        payload_hash=content_hash(event.render_buzz()),
    )
    buzz.messages.append({
        "id": "existing-event",
        "channel_id": "channel-1",
        "idempotency_key": event.idempotency_key,
        "payload": event.to_dict(),
    })
    event_id, created = buzz.send_event(
        store,
        event,
        channel_id="channel-1",
    )
    assert event_id == "existing-event"
    assert not created
    assert len(buzz.messages) == 1

    marker = "accepted-plan"
    github_key = f"github:issue:22:{marker}"
    store.reserve_external_write(
        github_key,
        system="github",
        target="issue:22",
        payload_hash=content_hash("Accepted plan."),
    )
    github.comments.append({
        "url": "https://example.test/issues/22#comment-existing",
        "body": "Accepted plan.",
        "marker": marker,
    })
    url, created = github.ensure_comment(
        store,
        issue=22,
        marker=marker,
        body="Accepted plan.",
    )
    assert url.endswith("#comment-existing")
    assert not created
    assert len(github.comments) == 1


def test_fake_task_intake_creates_exactly_one_record_channel_canvas_and_event(
    tmp_path, store, contract, config, collab_home
):
    (tmp_path / "AGENTS.md").write_text("canonical", encoding="utf-8")
    buzz = FakeBuzzAdapter()
    github = FakeGitHubAdapter()
    orchestrator = Orchestrator(
        root=tmp_path,
        store=store,
        config=config,
        buzz=buzz,
        github=github,
    )
    result = orchestrator.create_task(contract, issue=22)
    assert result["created"]
    assert len(buzz.channels_by_name) == 1
    assert len(buzz.canvases) == 1
    assert len(buzz.messages) == 1
    assert len(github.comments) == 1
    replay = orchestrator.create_task(contract, issue=22)
    assert not replay["created"]
    assert len(buzz.channels_by_name) == 1
    assert len(buzz.messages) == 1
    assert len(github.comments) == 1
    assert (collab_home / "inventory.json").is_file()


def test_degraded_systems_fail_closed_without_duplicate_replay(
    tmp_path, store, contract, config
):
    (tmp_path / "AGENTS.md").write_text("canonical", encoding="utf-8")
    buzz = FakeBuzzAdapter()
    buzz.online = False
    orchestrator = Orchestrator(
        root=tmp_path,
        store=store,
        config=config,
        buzz=buzz,
    )
    with pytest.raises(ConnectionError, match="offline"):
        orchestrator.create_task(contract)
    assert store.task(contract.task_id) is not None
    assert store.task(contract.task_id)["buzz_channel_id"] is None
    buzz.online = True
    replay = orchestrator.create_task(contract)
    assert not replay["created"]
    assert replay["resumed"]
    assert len(buzz.messages) == 1


def test_needs_clarification_is_visible_and_authorizes_no_work(store, config, tmp_path):
    buzz = FakeBuzzAdapter()
    github = FakeGitHubAdapter()
    orchestrator = Orchestrator(
        root=tmp_path,
        store=store,
        config=config,
        buzz=buzz,
        github=github,
    )
    result = orchestrator.create_clarification(
        task_id="GH-22",
        workstream_id="WS-22",
        base_sha="0123456789",
        title="Vague request",
        intake={"issue": 22},
        reason="at least one observable acceptance criterion is required",
        issue=22,
    )
    assert result["state"] == "needs-clarification"
    assert store.task("GH-22")["state"] == "needs-clarification"
    assert len(buzz.messages) == 1
    assert len(github.comments) == 1


def test_task_transition_is_persisted_visible_and_idempotent(
    store, contract, config, tmp_path
):
    store.create_task(contract, state="executing", issue=22)
    store.update_task_refs(
        contract.task_id,
        buzz_channel_id="channel-1",
    )
    buzz = FakeBuzzAdapter()
    github = FakeGitHubAdapter()
    orchestrator = Orchestrator(
        root=tmp_path,
        store=store,
        config=config,
        buzz=buzz,
        github=github,
    )

    paused = orchestrator.transition_task(contract.task_id, "paused")
    replay = orchestrator.transition_task(contract.task_id, "paused")
    assert paused["changed"]
    assert not replay["changed"]
    assert paused["event"]["event_id"] == replay["event"]["event_id"]
    assert len(buzz.messages) == 1
    assert len(github.comments) == 1

    resumed = orchestrator.transition_task(contract.task_id, "resume")
    assert resumed["task"]["state"] == "executing"
    assert len(buzz.messages) == 2
    assert len(github.comments) == 2
    review = orchestrator.transition_task(contract.task_id, "review_requested")
    assert review["event"]["event_type"] == "REVIEW_REQUESTED"
    assert len(buzz.messages) == 3
    assert len(github.comments) == 3
