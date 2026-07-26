"""Non-secret configuration loading and default paths."""

from __future__ import annotations

from copy import deepcopy
import os
from pathlib import Path
from typing import Any

import yaml

from .redaction import assert_non_secret

DEFAULTS: dict[str, Any] = {
    "schema_version": "atlas.collab.config.v1",
    "protocol_version": "atlas.collab.protocol.v1",
    "repository": "Seomzo/atlas-platform-experiments",
    "base_ref": "main",
    "buzz": {
        "relay_url": "",
        "community": "",
        "owner_public_keys": [],
        "control_channel": "atlas-dev-control",
        "control_channel_id": "",
        "decisions_channel": "atlas-dev-decisions",
        "decisions_channel_id": "",
        "reviews_channel": "atlas-dev-reviews",
        "reviews_channel_id": "",
        "visibility": "private",
    },
    "github": {"remote": "origin", "draft_prs": True},
    "routing": {
        "mention_only": True,
        "max_hops": 3,
        "max_clarification_cycles": 2,
        "max_retries": 3,
        "task_turn_budget": 40,
        "task_cost_budget_usd": 20,
        "recovery_heartbeat_seconds": 0,
    },
    "roles": {
        "coordinator": {
            "display_name": "atlas-coordinator",
            "runtime": "hermes-acp",
            "profile": "atlas-collab-coordinator",
            "permission_mode": "plan",
            "git_name": "Atlas Coordinator",
            "git_email": "atlas-coordinator@users.noreply.github.com",
            "task_id": "",
            "branch": "",
            "worktree": "",
        },
        "implementer": {
            "display_name": "atlas-implementer",
            "runtime": "hermes-acp",
            "profile": "atlas-collab-implementer",
            "permission_mode": "accept-edits",
            "git_name": "Atlas Implementer",
            "git_email": "atlas-implementer@users.noreply.github.com",
            "task_id": "",
            "branch": "",
            "worktree": "",
        },
        "reviewer": {
            "display_name": "atlas-reviewer",
            "runtime": "hermes-acp",
            "profile": "atlas-collab-reviewer",
            "permission_mode": "dont-ask",
            "git_name": "Atlas Reviewer",
            "git_email": "atlas-reviewer@users.noreply.github.com",
            "task_id": "",
            "branch": "",
            "worktree": "",
        },
    },
}


def collab_home() -> Path:
    override = os.environ.get("ATLAS_COLLAB_HOME")
    return (
        Path(override).expanduser() if override else Path.home() / ".atlas" / "collab"
    )


def config_path() -> Path:
    override = os.environ.get("ATLAS_COLLAB_CONFIG")
    return Path(override).expanduser() if override else collab_home() / "config.yaml"


def state_path() -> Path:
    return collab_home() / "state.db"


def inventory_path() -> Path:
    return collab_home() / "inventory.json"


def logs_dir() -> Path:
    return collab_home() / "logs"


def _merge(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(left)
    for key, value in right.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Path | None = None) -> dict[str, Any]:
    selected = path or config_path()
    if selected.exists():
        raw = yaml.safe_load(selected.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError("atlas-collab config must be a YAML mapping")
    else:
        raw = {}
    assert_non_secret(raw)
    config = _merge(DEFAULTS, raw)
    if config["schema_version"] != "atlas.collab.config.v1":
        raise ValueError("unsupported atlas-collab config schema")
    return config


def write_default_config(path: Path, config: dict[str, Any]) -> bool:
    if path.exists():
        return False
    write_config(path, config)
    return True


def write_config(path: Path, config: dict[str, Any]) -> None:
    assert_non_secret(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    path.chmod(0o600)
