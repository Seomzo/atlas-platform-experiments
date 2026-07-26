"""Credential-free repository contract checks used locally and in CI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import yaml

from .models import TaskContract
from .redaction import redact

REQUIRED_HANDOFF_SECTIONS = (
    "## Status",
    "## Goal and scope",
    "## Decisions made",
    "## Deferred decisions",
    "## Files changed",
    "## Contracts and migrations",
    "## Validation",
    "## Security and privacy checks",
    "## Visual evidence",
    "## Known risks and limitations",
    "## Integration order and conflicts",
    "## Exact next actions",
)
CONFLICT_MARKER = re.compile(r"^(<<<<<<<|=======|>>>>>>>)", re.MULTILINE)


def _parse_added_text(diff: str) -> dict[str, str]:
    """Return only introduced lines, keyed by repository-relative path."""
    added: dict[str, list[str]] = {}
    current_path = ""
    for line in diff.splitlines():
        if line.startswith("+++ "):
            candidate = line[4:]
            current_path = (
                candidate[2:]
                if candidate.startswith("b/")
                else candidate
                if candidate != "/dev/null"
                else ""
            )
            continue
        if current_path and line.startswith("+") and not line.startswith("+++"):
            added.setdefault(current_path, []).append(line[1:])
    return {path: "\n".join(lines) for path, lines in added.items()}


def _introduced_text(root: Path, base_ref: str) -> dict[str, str]:
    introduced: dict[str, list[str]] = {}
    commands = (
        ["git", "diff", "--no-ext-diff", "--unified=0", f"{base_ref}...HEAD", "--"],
        ["git", "diff", "--no-ext-diff", "--unified=0", "--"],
        ["git", "diff", "--cached", "--no-ext-diff", "--unified=0", "--"],
    )
    for command in commands:
        result = subprocess.run(
            command,
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
        )
        for path, text in _parse_added_text(result.stdout).items():
            introduced.setdefault(path, []).append(text)
    return {path: "\n".join(parts) for path, parts in introduced.items()}


def validate_task_file(path: Path) -> list[str]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return [f"{path}: contract must be a mapping"]
        TaskContract.from_mapping(raw)
    except Exception as exc:
        return [f"{path}: {exc}"]
    return []


def validate_handoff(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    return [
        f"{path}: missing {section}"
        for section in REQUIRED_HANDOFF_SECTIONS
        if section not in text
    ]


def validate_repository(root: Path, base_ref: str = "origin/main") -> list[str]:
    errors: list[str] = []
    for path in sorted(root.glob("config/tasks/*.yaml")):
        errors.extend(validate_task_file(path))
    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    changed = set(result.stdout.splitlines())
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    status_lines = [line for line in status.stdout.splitlines() if len(line) > 3]
    changed.update(line[3:] for line in status_lines)
    untracked = {line[3:] for line in status_lines if line.startswith("?? ")}
    introduced = _introduced_text(root, base_ref)
    paths = [root / item for item in changed if (root / item).is_file()]
    for path in paths:
        if path.match("*/docs/altas/workstreams/WS-*-HANDOFF.md"):
            errors.extend(validate_handoff(path))
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if CONFLICT_MARKER.search(text):
            errors.append(f"{path}: unresolved conflict marker")
        relative_path = path.relative_to(root).as_posix()
        candidate = (
            text if relative_path in untracked else introduced.get(relative_path, "")
        )
        if redact(candidate) != candidate:
            errors.append(f"{path}: secret-like sentinel pattern")
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    ).stdout.strip()
    if (
        branch
        and branch != "main"
        and not (branch.startswith("codex/") or branch.startswith("product/"))
    ):
        errors.append(f"branch {branch!r} violates Atlas workstream naming policy")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--task", action="append", type=Path, default=[])
    parser.add_argument("--handoff", action="append", type=Path, default=[])
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    errors = []
    for path in args.task:
        errors.extend(validate_task_file(path))
    for path in args.handoff:
        errors.extend(validate_handoff(path))
    if not args.task and not args.handoff:
        errors.extend(validate_repository(args.root))
    payload: dict[str, Any] = {"ok": not errors, "errors": errors}
    print(json.dumps(payload, indent=2) if args.json else "\n".join(errors) or "ok")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
