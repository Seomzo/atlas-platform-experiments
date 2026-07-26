"""LaunchAgent entry point; obtains credentials after process start."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from .config import load_config, state_path
from .keychain import KeyringVault
from .state import StateStore


def _buzz_acp() -> str:
    binary = shutil.which("buzz-acp")
    app_binary = Path("/Applications/Buzz.app/Contents/MacOS/buzz-acp")
    if binary:
        return binary
    if app_binary.exists():
        return str(app_binary)
    raise RuntimeError("buzz-acp is not installed")


def _codex_acp() -> str:
    binary = shutil.which("codex-acp")
    bundled = (
        Path.home()
        / "Library"
        / "Application Support"
        / "Buzz"
        / "node-tools"
        / "bin"
        / "codex-acp"
    )
    if binary:
        return binary
    if bundled.exists():
        return str(bundled)
    raise RuntimeError("Codex ACP wrapper is not installed")


def _hermes() -> str:
    binary = shutil.which("hermes")
    sibling = Path(sys.executable).with_name("hermes")
    if binary:
        return binary
    if sibling.exists():
        return str(sibling)
    raise RuntimeError("Hermes ACP runtime is not installed")


def role_author_allowlist(config: dict, role_name: str) -> list[str]:
    owners = list(config["buzz"].get("owner_public_keys", []))
    if not owners:
        raise RuntimeError("buzz.owner_public_keys is required for live routing")
    coordinator_key = config["roles"]["coordinator"].get("public_key", "")
    if role_name == "coordinator":
        role_keys = [
            settings.get("public_key", "")
            for name, settings in config["roles"].items()
            if name != "coordinator"
        ]
    else:
        role_keys = [coordinator_key]
    ordered = []
    for value in [*owners, *role_keys]:
        if not re.fullmatch(r"[0-9a-f]{64}", str(value)):
            raise RuntimeError(
                "every live author allowlist entry must be a 64-character "
                "lowercase public key"
            )
        if value not in ordered:
            ordered.append(value)
    return ordered


def _task_channels(config: dict, role_name: str, task_channel_id: str) -> list[str]:
    buzz = config["buzz"]
    stable = {
        "coordinator": (
            "control_channel_id",
            "decisions_channel_id",
            "reviews_channel_id",
        ),
        "implementer": ("control_channel_id",),
        "reviewer": ("decisions_channel_id", "reviews_channel_id"),
    }
    values = [task_channel_id]
    values.extend(
        buzz.get(key, "") for key in stable.get(role_name, ("control_channel_id",))
    )
    return list(dict.fromkeys(value for value in values if value))


def _validate_binding(role_name: str, role: dict) -> tuple[str, str, Path]:
    task_id = str(role.get("task_id") or "")
    branch = str(role.get("branch") or "")
    worktree_value = str(role.get("worktree") or "")
    if not (task_id and branch and worktree_value):
        raise RuntimeError(
            f"role {role_name} must be bound to a task, branch, and worktree"
        )
    worktree = Path(worktree_value).expanduser().resolve()
    if not worktree.is_dir():
        raise RuntimeError(f"configured worktree does not exist: {worktree}")
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=worktree,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode or result.stdout.strip() != branch:
        raise RuntimeError(f"configured worktree is not on expected branch {branch}")
    return task_id, branch, worktree


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    args = parser.parse_args(argv)
    config = load_config()
    role = config["roles"].get(args.role)
    if role is None:
        raise SystemExit(f"unknown configured role: {args.role}")
    task_id, branch, worktree = _validate_binding(args.role, role)
    with StateStore(state_path()) as store:
        task = store.task(task_id)
    if task is None:
        raise SystemExit(f"bound task does not exist: {task_id}")
    task_channel_id = str(task.get("buzz_channel_id") or "")
    if not task_channel_id:
        raise SystemExit(f"bound task has no live Buzz channel: {task_id}")
    private_key = KeyringVault().get(args.role)
    if not private_key:
        raise SystemExit(f"credential unavailable for role: {args.role}")
    runtime = role["runtime"]
    if runtime == "hermes-acp":
        runtime_command = _hermes()
        runtime_args = ["acp"]
    elif runtime == "codex-acp":
        runtime_command = _codex_acp()
        runtime_args = []
    elif runtime == "claude-acp":
        runtime_command = shutil.which("claude")
        runtime_args = []
    else:
        raise SystemExit(f"unsupported live runtime: {runtime}")
    if not runtime_command:
        raise SystemExit(f"runtime unavailable for role: {args.role}")
    authors = role_author_allowlist(config, args.role)
    channels = _task_channels(config, args.role, task_channel_id)
    os.chdir(worktree)
    env = os.environ.copy()
    env["BUZZ_PRIVATE_KEY"] = private_key
    env["BUZZ_RELAY_URL"] = config["buzz"]["relay_url"]
    env["ATLAS_COLLAB_TASK_ID"] = task_id
    env["ATLAS_COLLAB_ROLE"] = args.role
    env["ATLAS_COLLAB_BRANCH"] = branch
    if runtime == "hermes-acp":
        from hermes_cli.profiles import resolve_profile_env

        env["HERMES_HOME"] = resolve_profile_env(role["profile"])
        env["HERMES_PROFILE"] = role["profile"]
    env["ATLAS_COLLAB_RUNTIME_COMMAND"] = runtime_command
    env["ATLAS_COLLAB_RUNTIME_ARGS_JSON"] = json.dumps(runtime_args)
    agent_command = sys.executable
    agent_args = ["-m", "tools.atlas_collab.runtime_shim"]
    command = [
        _buzz_acp(),
        "--relay-url",
        config["buzz"]["relay_url"],
        "--agent-owner",
        config["buzz"]["owner_public_keys"][0],
        "--agent-command",
        agent_command,
        "--subscribe",
        "mentions",
        "--channels",
        ",".join(channels),
        "--dedup",
        "queue",
        "--multiple-event-handling",
        "queue",
        "--heartbeat-interval",
        "0",
        "--idle-timeout",
        "600",
        "--max-turn-duration",
        "1800",
        "--context-message-limit",
        "12",
        "--max-turns-per-session",
        str(config["routing"]["task_turn_budget"]),
        "--no-memory",
        "--respond-to",
        "allowlist",
        "--respond-to-allowlist",
        ",".join(authors),
        "--allowed-respond-to",
        "owner-only,allowlist",
        "--permission-mode",
        role.get("permission_mode", "dont-ask"),
        "--system-prompt-file",
        str(Path(__file__).with_name("roles") / f"{args.role}.md"),
    ]
    for value in agent_args:
        command.append(f"--agent-args={value}")
    os.execvpe(command[0], command, env)
    return 1


if __name__ == "__main__":
    sys.exit(main())
