import json

import pytest
import yaml

from tools.atlas_collab.models import (
    CollaborationEvent,
    ContractError,
    TaskContract,
)
from tools.atlas_collab.redaction import SecretMaterialError, assert_non_secret, redact
from tools.atlas_collab.validation import validate_task_file


def test_task_requires_observable_unique_acceptance_criteria(contract):
    raw = contract.to_dict()
    raw["acceptance_criteria"] = []
    with pytest.raises(ContractError, match="at least one"):
        TaskContract.from_mapping(raw)

    raw = contract.to_dict()
    raw["acceptance_criteria"] = list(raw["acceptance_criteria"])
    raw["acceptance_criteria"].append(raw["acceptance_criteria"][0])
    with pytest.raises(ContractError, match="unique"):
        TaskContract.from_mapping(raw)


def test_task_file_semantic_validation(tmp_path, contract):
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump(contract.to_dict()), encoding="utf-8")
    assert validate_task_file(path) == []


def test_event_is_readable_and_versioned(contract):
    event = CollaborationEvent(
        task_id=contract.task_id,
        workstream_id=contract.workstream_id,
        actor_id="agent-coordinator",
        actor_role="coordinator",
        event_type="QUESTION",
        status="executing",
        summary="Which fixture field owns the stable identifier?",
        intended_for=("agent-implementer",),
        base_sha=contract.base_sha,
        questions=("Confirm the identifier contract.",),
    )
    rendered = event.render_buzz()
    assert "**QUESTION · coordinator**" in rendered
    assert "atlas-collab-event" in rendered
    payload = json.loads(
        rendered.split("```json atlas-collab-event\n", 1)[1].split("\n```", 1)[0]
    )
    assert payload["schema_version"] == "atlas.collab.event.v1"
    assert event.can_trigger_turn


def test_self_trigger_hop_limit_and_terminal_events_do_not_wake(contract):
    common = {
        "task_id": contract.task_id,
        "workstream_id": contract.workstream_id,
        "actor_id": "agent-coordinator",
        "actor_role": "coordinator",
        "status": "executing",
        "summary": "bounded event",
        "base_sha": contract.base_sha,
    }
    assert not CollaborationEvent(
        **common,
        event_type="QUESTION",
        intended_for=("agent-coordinator",),
    ).can_trigger_turn
    assert not CollaborationEvent(
        **common,
        event_type="QUESTION",
        intended_for=("agent-implementer",),
        hop_count=3,
        max_hops=3,
    ).can_trigger_turn
    assert not CollaborationEvent(
        **common,
        event_type="TASK_COMPLETED",
        intended_for=("agent-implementer",),
    ).can_trigger_turn


def test_sentinel_secrets_are_rejected_and_redacted():
    sentinel = "sk-test_SENTINEL_1234567890"
    assert "[REDACTED]" in redact(f"failure {sentinel}")
    with pytest.raises(SecretMaterialError):
        assert_non_secret({"note": sentinel})
    with pytest.raises(SecretMaterialError):
        assert_non_secret({"private_key": "not-even-a-real-key"})
    # Public keys are identifiers and may be recorded.
    assert_non_secret({"public_key": "a" * 64})
