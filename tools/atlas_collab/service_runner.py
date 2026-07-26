"""LaunchAgent entry point; obtains credentials after process start."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sys

from .config import load_config
from .keychain import KeyringVault


def _buzz_acp() -> str:
    binary = shutil.which("buzz-acp")
    app_binary = Path("/Applications/Buzz.app/Contents/MacOS/buzz-acp")
    if binary:
        return binary
    if app_binary.exists():
        return str(app_binary)
    raise RuntimeError("buzz-acp is not installed")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--role", required=True)
    args = parser.parse_args(argv)
    config = load_config()
    role = config["roles"].get(args.role)
    if role is None:
        raise SystemExit(f"unknown configured role: {args.role}")
    private_key = KeyringVault().get(args.role)
    if not private_key:
        raise SystemExit(f"credential unavailable for role: {args.role}")
    runtime = role["runtime"]
    if runtime == "hermes-acp":
        agent_command = shutil.which("hermes")
        agent_args = ["acp"]
    elif runtime == "codex-acp":
        agent_command = "/Users/ethansandhu/Library/Application Support/Buzz/node-tools/bin/codex-acp"
        agent_args = []
    elif runtime == "claude-acp":
        agent_command = shutil.which("claude")
        agent_args = []
    else:
        raise SystemExit(f"unsupported live runtime: {runtime}")
    if not agent_command:
        raise SystemExit(f"runtime unavailable for role: {args.role}")
    owners = config["buzz"].get("owner_public_keys", [])
    if not owners:
        raise SystemExit("buzz.owner_public_keys is required for live service routing")
    env = os.environ.copy()
    env["BUZZ_PRIVATE_KEY"] = private_key
    env["BUZZ_RELAY_URL"] = config["buzz"]["relay_url"]
    command = [
        _buzz_acp(),
        "--relay-url",
        config["buzz"]["relay_url"],
        "--agent-owner",
        owners[0],
        "--agent-command",
        agent_command,
        "--subscribe",
        "mentions",
        "--heartbeat-interval",
        "0",
        "--respond-to",
        "allowlist",
        "--respond-to-allowlist",
        ",".join(owners),
        "--allowed-respond-to",
        "owner-only,allowlist",
        "--permission-mode",
        role.get("permission_mode", "dont-ask"),
        "--system-prompt-file",
        str(Path(__file__).with_name("roles") / f"{args.role}.md"),
    ]
    for value in agent_args:
        command.extend(["--agent-args", value])
    os.execvpe(command[0], command, env)
    return 1


if __name__ == "__main__":
    sys.exit(main())
