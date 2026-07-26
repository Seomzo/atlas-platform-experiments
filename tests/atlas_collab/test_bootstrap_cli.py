from __future__ import annotations

import hashlib
import json
import stat

import pytest

from tools.atlas_collab.cli import main
from tools.atlas_collab.config import config_path, inventory_path, state_path
from tools.atlas_collab.state import StateStore


def _digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_clean_local_bootstrap_apply_is_idempotent(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("ATLAS_COLLAB_HOME", str(tmp_path / "collab"))

    assert main(["doctor"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert not doctor["config"]["exists"]
    assert not doctor["state"]["exists"]

    assert main(["bootstrap", "--dry-run"]) == 0
    dry_run = json.loads(capsys.readouterr().out)
    assert not dry_run["apply"]
    assert not config_path().exists()
    assert not state_path().exists()

    assert main(["bootstrap", "--apply"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["apply"]
    first_config_hash = _digest(config_path())
    first_inventory_hash = _digest(inventory_path())
    for path in (config_path(), inventory_path(), state_path()):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with StateStore(state_path()) as store:
        first_counts = {
            table: store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[
                0
            ]
            for table in ("tasks", "agents", "events", "external_writes", "services")
        }

    assert main(["bootstrap", "--apply"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["apply"]
    assert _digest(config_path()) == first_config_hash
    assert _digest(inventory_path()) == first_inventory_hash
    with StateStore(state_path()) as store:
        second_counts = {
            table: store.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[
                0
            ]
            for table in ("tasks", "agents", "events", "external_writes", "services")
        }
    assert second_counts == first_counts


def test_task_handoff_preview_is_non_mutating(tmp_path, monkeypatch, capsys, contract):
    monkeypatch.setenv("ATLAS_COLLAB_HOME", str(tmp_path / "collab"))
    with StateStore(state_path()) as store:
        store.create_task(contract)
    body = tmp_path / "handoff.md"
    body.write_text("Review the exact acceptance evidence.", encoding="utf-8")

    assert (
        main([
            "task",
            "handoff",
            contract.task_id,
            "--branch",
            "codex/ws-22-dogfood",
            "--title",
            "WS-22 dogfood",
            "--body-file",
            str(body),
        ])
        == 0
    )
    preview = json.loads(capsys.readouterr().out)
    assert not preview["apply"]
    assert preview["branch"] == "codex/ws-22-dogfood"
    with StateStore(state_path()) as store:
        assert store.task(contract.task_id)["github_pr"] is None
        assert store.external_write("github:pr:codex/ws-22-dogfood") is None


def test_services_refuse_to_start_before_doctor_is_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_COLLAB_HOME", str(tmp_path / "collab"))
    with pytest.raises(RuntimeError, match="doctor is not ready"):
        main(["services", "start", "--apply"])
