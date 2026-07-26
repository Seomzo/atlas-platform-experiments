from __future__ import annotations

import pytest

from tools.atlas_collab.adapters.process import CommandResult
from tools.atlas_collab.adapters.runtime import RuntimeInventory


class ProfileRunner:
    def __init__(self):
        self.exists = False
        self.create_count = 0

    def run(self, command, **_kwargs):
        wrapped = tuple(command)
        if command[1:3] == ["profile", "show"]:
            return CommandResult(
                wrapped,
                0 if self.exists else 1,
                "ready" if self.exists else "",
                "" if self.exists else "missing",
            )
        if command[1:3] == ["profile", "create"]:
            self.exists = True
            self.create_count += 1
            return CommandResult(wrapped, 0, "created", "")
        raise AssertionError(command)


def test_hermes_profile_provisioning_is_previewable_and_idempotent(monkeypatch):
    runner = ProfileRunner()
    monkeypatch.setattr(
        "tools.atlas_collab.adapters.runtime.shutil.which",
        lambda name: "/example/hermes" if name == "hermes" else None,
    )
    inventory = RuntimeInventory(runner=runner)

    preview = inventory.ensure_profile(
        runtime="hermes-acp",
        profile="atlas-collab-reviewer",
        description="Independent reviewer.",
        apply=False,
    )
    assert preview["status"] == "would-create"
    assert runner.create_count == 0

    created = inventory.ensure_profile(
        runtime="hermes-acp",
        profile="atlas-collab-reviewer",
        description="Independent reviewer.",
        apply=True,
    )
    assert created["created"]
    reused = inventory.ensure_profile(
        runtime="hermes-acp",
        profile="atlas-collab-reviewer",
        description="Independent reviewer.",
        apply=True,
    )
    assert not reused["created"]
    assert runner.create_count == 1


def test_missing_hermes_is_reported_for_preview_and_rejected_on_apply(monkeypatch):
    monkeypatch.setattr(
        "tools.atlas_collab.adapters.runtime.shutil.which",
        lambda _name: None,
    )
    inventory = RuntimeInventory()

    preview = inventory.ensure_profile(
        runtime="hermes-acp",
        profile="atlas-collab-reviewer",
        description="Independent reviewer.",
        apply=False,
    )
    assert preview["status"] == "unavailable"
    assert not preview["created"]

    with pytest.raises(RuntimeError, match="Hermes is required"):
        inventory.ensure_profile(
            runtime="hermes-acp",
            profile="atlas-collab-reviewer",
            description="Independent reviewer.",
            apply=True,
        )
