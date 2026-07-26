"""Git and GitHub durable-ledger adapter."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import shutil
from typing import Any

from ..models import content_hash
from ..state import StateStore
from .process import CommandRunner


@dataclass(frozen=True)
class GitHubHealth:
    git: bool
    gh: bool
    authenticated: bool
    detail: str


class GitHubAdapter:
    def __init__(
        self,
        repository: str,
        checkout: Path,
        runner: CommandRunner | None = None,
    ):
        self.repository = repository
        self.checkout = checkout
        self.runner = runner or CommandRunner()

    def probe(self) -> GitHubHealth:
        has_git = bool(shutil.which("git"))
        has_gh = bool(shutil.which("gh"))
        if not (has_git and has_gh):
            return GitHubHealth(has_git, has_gh, False, "git or gh missing")
        status = self.runner.run(["gh", "auth", "status"], check=False, timeout=15)
        return GitHubHealth(has_git, has_gh, status.code == 0, status.stderr or "ready")

    def base_sha(self, ref: str = "main") -> str:
        self.runner.run(["git", "fetch", "--prune", "origin"], cwd=self.checkout)
        return self.runner.run(
            ["git", "rev-parse", f"origin/{ref}"], cwd=self.checkout
        ).stdout.strip()

    def issue(self, number: int) -> dict[str, Any]:
        result = self.runner.run(
            [
                "gh",
                "issue",
                "view",
                str(number),
                "--repo",
                self.repository,
                "--json",
                "number,title,body,url,state",
            ],
            cwd=self.checkout,
        )
        return result.json()

    def ensure_comment(
        self,
        store: StateStore,
        *,
        issue: int,
        marker: str,
        body: str,
    ) -> tuple[str, bool]:
        key = f"github:issue:{issue}:{marker}"
        existing = store.external_write(key)
        if existing and existing["status"] == "complete":
            return str(existing["external_id"]), False
        if not existing:
            store.reserve_external_write(
                key,
                system="github",
                target=f"issue:{issue}",
                payload_hash=content_hash(body),
            )
        decorated = f"{body}\n\n<!-- atlas-collab:{marker} -->"
        result = self.runner.run(
            [
                "gh",
                "issue",
                "comment",
                str(issue),
                "--repo",
                self.repository,
                "--body",
                decorated,
            ],
            cwd=self.checkout,
        )
        url = result.stdout.strip()
        store.finish_external_write(key, url)
        return url, True

    def ensure_worktree(
        self,
        *,
        branch: str,
        worktree: Path,
        base_ref: str = "origin/main",
    ) -> bool:
        if worktree.exists():
            current = self.runner.run(
                ["git", "branch", "--show-current"], cwd=worktree
            ).stdout.strip()
            if current != branch:
                raise RuntimeError(
                    f"worktree {worktree} belongs to {current}, expected {branch}"
                )
            return False
        branch_check = self.runner.run(
            ["git", "show-ref", "--verify", f"refs/heads/{branch}"],
            cwd=self.checkout,
            check=False,
        )
        args = ["git", "worktree", "add", str(worktree)]
        if branch_check.code == 0:
            args.append(branch)
        else:
            args.extend(["-b", branch, base_ref])
        self.runner.run(args, cwd=self.checkout)
        return True

    def changed_base(self, recorded: str, ref: str = "origin/main") -> bool:
        current = self.runner.run(
            ["git", "rev-parse", ref], cwd=self.checkout
        ).stdout.strip()
        return current != recorded

    def overlap(self, left_ref: str, right_ref: str) -> list[str]:
        left = set(
            self.runner.run(
                ["git", "diff", "--name-only", f"origin/main...{left_ref}"],
                cwd=self.checkout,
            ).stdout.splitlines()
        )
        right = set(
            self.runner.run(
                ["git", "diff", "--name-only", f"origin/main...{right_ref}"],
                cwd=self.checkout,
            ).stdout.splitlines()
        )
        return sorted(left & right)


class FakeGitHubAdapter:
    def __init__(self):
        self.online = True
        self.comments: list[dict[str, Any]] = []
        self.worktrees: dict[str, str] = {}

    def ensure_comment(
        self,
        store: StateStore,
        *,
        issue: int,
        marker: str,
        body: str,
    ) -> tuple[str, bool]:
        if not self.online:
            raise ConnectionError("fake GitHub offline")
        key = f"github:issue:{issue}:{marker}"
        existing = store.external_write(key)
        if existing and existing["status"] == "complete":
            return str(existing["external_id"]), False
        if not existing:
            store.reserve_external_write(
                key,
                system="github",
                target=f"issue:{issue}",
                payload_hash=content_hash(body),
            )
        url = f"https://example.test/issues/{issue}#comment-{len(self.comments) + 1}"
        self.comments.append({"url": url, "body": body, "marker": marker})
        store.finish_external_write(key, url)
        return url, True
