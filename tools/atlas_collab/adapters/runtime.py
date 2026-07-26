"""Narrow runtime discovery and fake execution adapter."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
from typing import Protocol

from .process import CommandRunner


@dataclass(frozen=True)
class RuntimeCapability:
    name: str
    command: tuple[str, ...]
    installed: bool
    authenticated: bool
    detail: str


class RuntimeAdapter(Protocol):
    name: str

    def start_turn(
        self, *, session_id: str, worktree: Path, prompt: str
    ) -> dict[str, str]: ...

    def cancel(self, *, session_id: str) -> None: ...


class RuntimeInventory:
    def __init__(self, runner: CommandRunner | None = None):
        self.runner = runner or CommandRunner()

    def detect(self) -> list[RuntimeCapability]:
        return [
            self._hermes(),
            self._codex(),
            self._claude(),
            RuntimeCapability(
                "fake",
                ("python", "-m", "tools.atlas_collab"),
                True,
                True,
                "deterministic test runtime",
            ),
        ]

    def _hermes(self) -> RuntimeCapability:
        executable = shutil.which("hermes")
        if not executable:
            return RuntimeCapability(
                "hermes-acp", ("hermes", "acp"), False, False, "missing"
            )
        help_result = self.runner.run(
            [executable, "acp", "--help"], check=False, timeout=15
        )
        status = self.runner.run([executable, "status"], check=False, timeout=20)
        authenticated = status.code == 0 and "configured" in status.stdout.lower()
        return RuntimeCapability(
            "hermes-acp",
            (executable, "acp"),
            help_result.code == 0,
            authenticated,
            "installed; authentication inferred from sanitized hermes status",
        )

    def _codex(self) -> RuntimeCapability:
        executable = shutil.which("codex")
        if not executable:
            return RuntimeCapability("codex-acp", ("codex",), False, False, "missing")
        status = self.runner.run(
            [executable, "login", "status"], check=False, timeout=15
        )
        # ChatGPT auth proves Codex CLI availability, not Buzz ACP API-key auth.
        detail = status.stdout.strip() or status.stderr.strip()
        buzz_api_auth = "api key" in detail.lower()
        return RuntimeCapability(
            "codex-acp",
            (executable,),
            True,
            buzz_api_auth,
            (
                "Codex CLI available; Buzz ACP API-key auth proven"
                if buzz_api_auth
                else "Codex CLI session exists, but Buzz ACP API-key auth is unproven"
            ),
        )

    def _claude(self) -> RuntimeCapability:
        executable = shutil.which("claude")
        if not executable:
            return RuntimeCapability("claude-acp", ("claude",), False, False, "missing")
        status = self.runner.run(
            [executable, "auth", "status"], check=False, timeout=15
        )
        authenticated = False
        try:
            payload = json.loads(status.stdout)
            authenticated = bool(payload.get("loggedIn"))
        except json.JSONDecodeError:
            authenticated = status.code == 0 and "logged in" in status.stdout.lower()
        return RuntimeCapability(
            "claude-acp",
            (executable,),
            True,
            authenticated,
            "authenticated" if authenticated else "installed but not authenticated",
        )


class FakeRuntime:
    name = "fake"

    def __init__(self, scripted: list[str] | None = None):
        self.scripted = scripted or ["acknowledged"]
        self.turns: list[dict[str, str]] = []
        self.canceled: set[str] = set()
        self.fail_next = False

    def start_turn(
        self, *, session_id: str, worktree: Path, prompt: str
    ) -> dict[str, str]:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("injected runtime failure")
        response = self.scripted[min(len(self.turns), len(self.scripted) - 1)]
        turn = {
            "session_id": session_id,
            "worktree": str(worktree),
            "prompt": prompt,
            "response": response,
        }
        self.turns.append(turn)
        return turn

    def cancel(self, *, session_id: str) -> None:
        self.canceled.add(session_id)
