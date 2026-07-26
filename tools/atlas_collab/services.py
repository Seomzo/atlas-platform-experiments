"""User-level service definitions with runtime credential injection."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
from typing import Any

from .config import collab_home, load_config, logs_dir

SERVICE_PREFIX = "io.atlas.collab"


def service_names(config: dict[str, Any]) -> list[str]:
    return [f"{SERVICE_PREFIX}.{role}" for role in config["roles"]]


def _launch_agents_dir() -> Path:
    return Path.home() / "Library" / "LaunchAgents"


def _service_program(role: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "tools.atlas_collab.service_runner",
        "--role",
        role,
    ]


def launch_agent_definition(role: str) -> dict[str, Any]:
    label = f"{SERVICE_PREFIX}.{role}"
    return {
        "Label": label,
        "ProgramArguments": _service_program(role),
        "RunAtLoad": False,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Background",
        "StandardOutPath": str(logs_dir() / f"{role}.log"),
        "StandardErrorPath": str(logs_dir() / f"{role}.error.log"),
        "EnvironmentVariables": {
            "ATLAS_COLLAB_HOME": str(collab_home()),
            "PYTHONUNBUFFERED": "1",
        },
    }


def install_services(*, apply: bool) -> list[dict[str, Any]]:
    config = load_config()
    if platform.system() != "Darwin":
        return [
            {
                "status": "manual-pending",
                "detail": "systemd user service generation is documented but not installed",
            }
        ]
    actions = []
    for role in config["roles"]:
        definition = launch_agent_definition(role)
        payload = plistlib.dumps(definition, sort_keys=True)
        path = _launch_agents_dir() / f"{SERVICE_PREFIX}.{role}.plist"
        current = path.read_bytes() if path.exists() else None
        changed = current != payload
        actions.append({
            "service": f"{SERVICE_PREFIX}.{role}",
            "path": str(path),
            "changed": changed,
            "apply": apply,
            "fingerprint": hashlib.sha256(payload).hexdigest(),
        })
        if apply and changed:
            path.parent.mkdir(parents=True, exist_ok=True)
            logs_dir().mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            path.chmod(0o600)
    return actions


def service_action(action: str) -> list[dict[str, Any]]:
    if platform.system() != "Darwin":
        raise RuntimeError("service control currently supports macOS LaunchAgents")
    config = load_config()
    results = []
    gui_domain = f"gui/{os.getuid()}"
    for role in config["roles"]:
        label = f"{SERVICE_PREFIX}.{role}"
        path = _launch_agents_dir() / f"{label}.plist"
        if action == "status":
            process = subprocess.run(
                ["launchctl", "print", f"{gui_domain}/{label}"],
                text=True,
                capture_output=True,
                check=False,
            )
            results.append({
                "service": label,
                "installed": path.exists(),
                "loaded": process.returncode == 0,
            })
        elif action == "start":
            if not path.exists():
                raise RuntimeError(f"{label} is not installed")
            subprocess.run(
                ["launchctl", "bootstrap", gui_domain, str(path)],
                text=True,
                capture_output=True,
                check=False,
            )
            process = subprocess.run(
                ["launchctl", "kickstart", f"{gui_domain}/{label}"],
                text=True,
                capture_output=True,
                check=False,
            )
            results.append({"service": label, "started": process.returncode == 0})
        elif action == "stop":
            process = subprocess.run(
                ["launchctl", "bootout", f"{gui_domain}/{label}"],
                text=True,
                capture_output=True,
                check=False,
            )
            results.append({
                "service": label,
                "stopped": process.returncode in {0, 3},
            })
        else:
            raise ValueError(action)
    return results


def service_dependencies() -> dict[str, str]:
    buzz_acp = shutil.which("buzz-acp")
    if not buzz_acp and Path("/Applications/Buzz.app/Contents/MacOS/buzz-acp").exists():
        buzz_acp = "/Applications/Buzz.app/Contents/MacOS/buzz-acp"
    return {
        "buzz_acp": buzz_acp or "",
        "hermes": shutil.which("hermes") or "",
        "codex": shutil.which("codex") or "",
        "claude": shutil.which("claude") or "",
    }
