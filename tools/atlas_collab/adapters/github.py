"""Git and GitHub durable-ledger adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
from typing import Any

from ..models import content_hash
from ..redaction import assert_non_secret
from ..state import StateStore
from .process import CommandError, CommandRunner


@dataclass(frozen=True)
class GitHubHealth:
    git: bool
    gh: bool
    authenticated: bool
    detail: str


@dataclass(frozen=True)
class WorktreeHealth:
    path: str
    expected_branch: str
    actual_branch: str
    head_sha: str
    base_sha: str
    clean: bool
    base_is_ancestor: bool
    branch_unique: bool
    valid: bool
    problems: tuple[str, ...]


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
        recovered = self._find_issue_comment(issue, marker)
        if recovered:
            if not existing:
                store.reserve_external_write(
                    key,
                    system="github",
                    target=f"issue:{issue}",
                    payload_hash=content_hash(body),
                )
            store.finish_external_write(key, recovered)
            return recovered, False
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

    def _find_issue_comment(self, issue: int, marker: str) -> str:
        result = self.runner.run(
            [
                "gh",
                "api",
                "--paginate",
                "--slurp",
                f"repos/{self.repository}/issues/{issue}/comments?per_page=100",
            ],
            cwd=self.checkout,
        )
        pages = result.json()
        if not isinstance(pages, list):
            raise RuntimeError("GitHub issue-comment response was not a JSON list")
        comments = [
            item
            for page in pages
            if isinstance(page, list)
            for item in page
            if isinstance(item, dict)
        ]
        needle = f"<!-- atlas-collab:{marker} -->"
        matches = [
            str(item.get("html_url") or "")
            for item in comments
            if needle in str(item.get("body") or "") and item.get("html_url")
        ]
        if len(matches) > 1:
            raise RuntimeError(
                f"multiple GitHub comments use idempotency marker {marker}"
            )
        return matches[0] if matches else ""

    def ensure_worktree(
        self,
        *,
        branch: str,
        worktree: Path,
        base_ref: str = "origin/main",
    ) -> bool:
        if worktree.exists():
            self.validate_worktree(
                branch=branch,
                worktree=worktree,
                base_ref=base_ref,
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
        self.validate_worktree(
            branch=branch,
            worktree=worktree,
            base_ref=base_ref,
        )
        return True

    def worktree_health(
        self,
        *,
        branch: str,
        worktree: Path,
        base_ref: str,
    ) -> WorktreeHealth:
        if not worktree.exists():
            return WorktreeHealth(
                path=str(worktree),
                expected_branch=branch,
                actual_branch="",
                head_sha="",
                base_sha="",
                clean=False,
                base_is_ancestor=False,
                branch_unique=False,
                valid=False,
                problems=("worktree does not exist",),
            )
        actual_branch = self.runner.run(
            ["git", "branch", "--show-current"], cwd=worktree
        ).stdout.strip()
        head_sha = self.runner.run(
            ["git", "rev-parse", "HEAD"], cwd=worktree
        ).stdout.strip()
        base_sha = self.runner.run(
            ["git", "rev-parse", base_ref], cwd=worktree
        ).stdout.strip()
        clean = not bool(
            self.runner.run(
                ["git", "status", "--porcelain"], cwd=worktree
            ).stdout.strip()
        )
        ancestry = self.runner.run(
            ["git", "merge-base", "--is-ancestor", base_sha, head_sha],
            cwd=worktree,
            check=False,
        )
        if ancestry.code not in {0, 1}:
            raise CommandError(ancestry.command, ancestry.code, ancestry.stderr)
        worktrees = self._worktrees()
        branch_ref = f"refs/heads/{branch}"
        matching_paths = [
            item["worktree"] for item in worktrees if item.get("branch") == branch_ref
        ]
        resolved = str(worktree.resolve())
        branch_unique = (
            len(matching_paths) == 1
            and str(Path(matching_paths[0]).resolve()) == resolved
        )
        problems = []
        if actual_branch != branch:
            problems.append(
                f"worktree branch is {actual_branch or 'detached'}, expected {branch}"
            )
        if not clean:
            problems.append("worktree is dirty")
        if ancestry.code != 0:
            problems.append(f"base {base_sha} is not an ancestor of {head_sha}")
        if not branch_unique:
            problems.append(
                f"branch {branch} is not uniquely assigned to this worktree"
            )
        return WorktreeHealth(
            path=resolved,
            expected_branch=branch,
            actual_branch=actual_branch,
            head_sha=head_sha,
            base_sha=base_sha,
            clean=clean,
            base_is_ancestor=ancestry.code == 0,
            branch_unique=branch_unique,
            valid=not problems,
            problems=tuple(problems),
        )

    def validate_worktree(
        self,
        *,
        branch: str,
        worktree: Path,
        base_ref: str,
    ) -> WorktreeHealth:
        health = self.worktree_health(
            branch=branch,
            worktree=worktree,
            base_ref=base_ref,
        )
        if not health.valid:
            raise RuntimeError("; ".join(health.problems))
        return health

    def configure_worktree_identity(
        self,
        *,
        worktree: Path,
        name: str,
        email: str,
    ) -> dict[str, str]:
        if not name.strip() or "\n" in name or "\r" in name:
            raise ValueError("Git identity name is missing or invalid")
        if not email.strip() or "@" not in email or "\n" in email or "\r" in email:
            raise ValueError("Git identity email is missing or invalid")
        self.runner.run(
            ["git", "config", "extensions.worktreeConfig", "true"],
            cwd=self.checkout,
        )
        self.runner.run(
            ["git", "config", "--worktree", "user.name", name],
            cwd=worktree,
        )
        self.runner.run(
            ["git", "config", "--worktree", "user.email", email],
            cwd=worktree,
        )
        identity = self.worktree_identity(worktree)
        if identity != {"name": name, "email": email}:
            raise RuntimeError("worktree-specific Git identity did not persist")
        return identity

    def worktree_identity(self, worktree: Path) -> dict[str, str]:
        return {
            "name": self.runner.run(
                ["git", "config", "--worktree", "--get", "user.name"],
                cwd=worktree,
            ).stdout.strip(),
            "email": self.runner.run(
                ["git", "config", "--worktree", "--get", "user.email"],
                cwd=worktree,
            ).stdout.strip(),
        }

    def _worktrees(self) -> list[dict[str, str]]:
        payload = self.runner.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=self.checkout,
        ).stdout
        records: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in payload.splitlines():
            if not line:
                if current:
                    records.append(current)
                    current = {}
                continue
            key, _, value = line.partition(" ")
            current[key] = value
        if current:
            records.append(current)
        return records

    def changed_base(self, recorded: str, ref: str = "origin/main") -> bool:
        current = self.runner.run(
            ["git", "rev-parse", ref], cwd=self.checkout
        ).stdout.strip()
        return current != recorded

    def overlap(
        self,
        left_ref: str,
        right_ref: str,
        *,
        base_ref: str = "origin/main",
    ) -> list[str]:
        left = set(
            self.runner.run(
                ["git", "diff", "--name-only", f"{base_ref}...{left_ref}"],
                cwd=self.checkout,
            ).stdout.splitlines()
        )
        right = set(
            self.runner.run(
                ["git", "diff", "--name-only", f"{base_ref}...{right_ref}"],
                cwd=self.checkout,
            ).stdout.splitlines()
        )
        return sorted(left & right)

    def has_conflicts(self, left_ref: str, right_ref: str) -> bool:
        for ref in (left_ref, right_ref):
            self.runner.run(["git", "rev-parse", "--verify", ref], cwd=self.checkout)
        result = self.runner.run(
            [
                "git",
                "merge-tree",
                "--write-tree",
                "--quiet",
                left_ref,
                right_ref,
            ],
            cwd=self.checkout,
            check=False,
        )
        if result.code not in {0, 1}:
            raise CommandError(result.command, result.code, result.stderr)
        return result.code == 1

    def ensure_draft_pr(
        self,
        store: StateStore,
        *,
        branch: str,
        base: str,
        title: str,
        body: str,
    ) -> tuple[dict[str, Any], bool]:
        assert_non_secret({"title": title, "body": body})
        marker = f"draft-pr:{branch}"
        decorated = f"{body.rstrip()}\n\n<!-- atlas-collab:{marker} -->"
        existing = self._open_prs(branch)
        if len(existing) > 1:
            raise RuntimeError(f"multiple open pull requests use branch {branch}")
        created = False
        if existing:
            pull = existing[0]
            if not pull.get("isDraft"):
                raise RuntimeError(
                    f"pull request #{pull['number']} is no longer a draft; "
                    "human review state will not be changed automatically"
                )
            if (
                pull.get("title") != title
                or pull.get("body") != decorated
                or pull.get("baseRefName") != base
            ):
                self.runner.run(
                    [
                        "gh",
                        "pr",
                        "edit",
                        str(pull["number"]),
                        "--repo",
                        self.repository,
                        "--base",
                        base,
                        "--title",
                        title,
                        "--body-file",
                        "-",
                    ],
                    cwd=self.checkout,
                    stdin=decorated,
                )
        else:
            self.runner.run(
                [
                    "gh",
                    "pr",
                    "create",
                    "--repo",
                    self.repository,
                    "--draft",
                    "--base",
                    base,
                    "--head",
                    branch,
                    "--title",
                    title,
                    "--body-file",
                    "-",
                ],
                cwd=self.checkout,
                stdin=decorated,
            )
            created = True
        pulls = self._open_prs(branch)
        if len(pulls) != 1:
            raise RuntimeError(
                f"expected one open pull request for {branch}, found {len(pulls)}"
            )
        pull = pulls[0]
        key = f"github:pr:{branch}"
        existing_write = store.external_write(key)
        if not existing_write:
            store.reserve_external_write(
                key,
                system="github",
                target=f"branch:{branch}",
                payload_hash=content_hash({
                    "base": base,
                    "title": title,
                    "body": decorated,
                }),
            )
        store.finish_external_write(key, str(pull["url"]))
        return pull, created

    def _open_prs(self, branch: str) -> list[dict[str, Any]]:
        result = self.runner.run(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                self.repository,
                "--head",
                branch,
                "--state",
                "open",
                "--json",
                "number,url,isDraft,title,body,baseRefName,headRefName",
            ],
            cwd=self.checkout,
        )
        payload = result.json()
        if not isinstance(payload, list):
            raise RuntimeError("GitHub pull-request response was not a JSON list")
        return payload


class FakeGitHubAdapter:
    def __init__(self):
        self.online = True
        self.comments: list[dict[str, Any]] = []
        self.worktrees: dict[str, str] = {}
        self.pull_requests: dict[str, dict[str, Any]] = {}

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
        recovered = next(
            (str(item["url"]) for item in self.comments if item["marker"] == marker),
            "",
        )
        if recovered:
            if not existing:
                store.reserve_external_write(
                    key,
                    system="github",
                    target=f"issue:{issue}",
                    payload_hash=content_hash(body),
                )
            store.finish_external_write(key, recovered)
            return recovered, False
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

    def ensure_draft_pr(
        self,
        store: StateStore,
        *,
        branch: str,
        base: str,
        title: str,
        body: str,
    ) -> tuple[dict[str, Any], bool]:
        if not self.online:
            raise ConnectionError("fake GitHub offline")
        assert_non_secret({"title": title, "body": body})
        marker = f"draft-pr:{branch}"
        decorated = f"{body.rstrip()}\n\n<!-- atlas-collab:{marker} -->"
        created = branch not in self.pull_requests
        if created:
            number = len(self.pull_requests) + 1
            self.pull_requests[branch] = {
                "number": number,
                "url": f"https://example.test/pull/{number}",
                "isDraft": True,
                "title": title,
                "body": decorated,
                "baseRefName": base,
                "headRefName": branch,
            }
        else:
            pull = self.pull_requests[branch]
            if not pull["isDraft"]:
                raise RuntimeError(
                    f"pull request #{pull['number']} is no longer a draft"
                )
            pull.update({
                "title": title,
                "body": decorated,
                "baseRefName": base,
            })
        pull = self.pull_requests[branch]
        key = f"github:pr:{branch}"
        if not store.external_write(key):
            store.reserve_external_write(
                key,
                system="github",
                target=f"branch:{branch}",
                payload_hash=content_hash({
                    "base": base,
                    "title": title,
                    "body": decorated,
                }),
            )
        store.finish_external_write(key, str(pull["url"]))
        return dict(pull), created
