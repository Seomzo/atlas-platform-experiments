"""Subprocess boundary with deterministic JSON and sanitized failures."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any

from ..redaction import redact


class CommandError(RuntimeError):
    def __init__(self, command: tuple[str, ...], code: int, detail: str):
        self.command = command
        self.code = code
        self.detail = redact(detail)
        super().__init__(f"{' '.join(command)} exited {code}: {self.detail}")


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    code: int
    stdout: str
    stderr: str

    def json(self) -> Any:
        return json.loads(self.stdout) if self.stdout.strip() else {}


class CommandRunner:
    def run(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        stdin: str | None = None,
        check: bool = True,
        timeout: int = 30,
    ) -> CommandResult:
        process_env = os.environ.copy()
        if env:
            process_env.update(env)
        result = subprocess.run(
            command,
            cwd=cwd,
            env=process_env,
            input=stdin,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        wrapped = CommandResult(
            tuple(command),
            result.returncode,
            redact(result.stdout),
            redact(result.stderr),
        )
        if check and wrapped.code:
            raise CommandError(wrapped.command, wrapped.code, wrapped.stderr)
        return wrapped
