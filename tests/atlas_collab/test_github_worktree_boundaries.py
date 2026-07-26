from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from tools.atlas_collab.adapters.github import GitHubAdapter


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _write(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8")


def test_real_git_worktree_base_dirty_overlap_and_conflict_detection(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _git(checkout, "init", "-b", "main")
    _git(checkout, "config", "user.name", "Atlas Test")
    _git(checkout, "config", "user.email", "atlas-test@example.invalid")
    _write(checkout / "shared.txt", "base\n")
    _git(checkout, "add", "shared.txt")
    _git(checkout, "commit", "-m", "base")
    base_sha = _git(checkout, "rev-parse", "HEAD")

    left = tmp_path / "left"
    right = tmp_path / "right"
    _git(checkout, "worktree", "add", "-b", "left", str(left), base_sha)
    _git(checkout, "worktree", "add", "-b", "right", str(right), base_sha)
    _write(left / "shared.txt", "left\n")
    _git(left, "add", "shared.txt")
    _git(left, "commit", "-m", "left")
    _write(right / "shared.txt", "right\n")
    _git(right, "add", "shared.txt")
    _git(right, "commit", "-m", "right")

    adapter = GitHubAdapter("example/atlas", checkout)
    health = adapter.validate_worktree(
        branch="left",
        worktree=left,
        base_ref=base_sha,
    )
    assert health.valid
    assert health.clean
    assert health.base_is_ancestor
    assert health.branch_unique
    assert adapter.configure_worktree_identity(
        worktree=left,
        name="Atlas Implementer",
        email="atlas-implementer@users.noreply.github.com",
    ) == {
        "name": "Atlas Implementer",
        "email": "atlas-implementer@users.noreply.github.com",
    }
    assert adapter.configure_worktree_identity(
        worktree=right,
        name="Atlas Reviewer",
        email="atlas-reviewer@users.noreply.github.com",
    ) == {
        "name": "Atlas Reviewer",
        "email": "atlas-reviewer@users.noreply.github.com",
    }
    assert adapter.worktree_identity(left) != adapter.worktree_identity(right)
    assert adapter.overlap("left", "right", base_ref=base_sha) == ["shared.txt"]
    assert adapter.has_conflicts("left", "right")

    _write(left / "uncommitted.txt", "dirty\n")
    dirty = adapter.worktree_health(
        branch="left",
        worktree=left,
        base_ref=base_sha,
    )
    assert not dirty.valid
    assert "worktree is dirty" in dirty.problems
    with pytest.raises(RuntimeError, match="dirty"):
        adapter.validate_worktree(
            branch="left",
            worktree=left,
            base_ref=base_sha,
        )

    (left / "uncommitted.txt").unlink()
    wrong_base = adapter.worktree_health(
        branch="left",
        worktree=left,
        base_ref="right",
    )
    assert not wrong_base.base_is_ancestor
    assert not wrong_base.valid


def test_ensure_worktree_reuses_only_a_clean_unique_branch(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    _git(checkout, "init", "-b", "main")
    _git(checkout, "config", "user.name", "Atlas Test")
    _git(checkout, "config", "user.email", "atlas-test@example.invalid")
    _write(checkout / "base.txt", "base\n")
    _git(checkout, "add", "base.txt")
    _git(checkout, "commit", "-m", "base")
    base_sha = _git(checkout, "rev-parse", "HEAD")

    adapter = GitHubAdapter("example/atlas", checkout)
    worktree = tmp_path / "implementer"
    assert adapter.ensure_worktree(
        branch="implementer",
        worktree=worktree,
        base_ref=base_sha,
    )
    assert not adapter.ensure_worktree(
        branch="implementer",
        worktree=worktree,
        base_ref=base_sha,
    )
    _write(worktree / "dirty.txt", "dirty\n")
    with pytest.raises(RuntimeError, match="dirty"):
        adapter.ensure_worktree(
            branch="implementer",
            worktree=worktree,
            base_ref=base_sha,
        )
