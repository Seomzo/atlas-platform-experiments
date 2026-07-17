"""CLI contract tests for native Atlas Cortex administration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from altas.cortex.cli import cortex_command
from hermes_cli.subcommands.cortex import build_cortex_parser


_MODEL_KEY_ENV = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "NOUS_API_KEY",
    "ATLAS_CORTEX_GRAPHRAG_PUBLIC_KEYS",
)


def _finalize_semantic_job(store, session_id: str) -> str:
    from altas.cortex.models import EvidenceInput

    store.ensure_session(session_id)
    store.append_evidence(
        session_id,
        EvidenceInput(
            source_type="user_message",
            content="Durable CLI session boundary.",
            source_locator=f"{session_id}:user:1",
        ),
    )
    store.finalize_session(session_id)
    with store.connect() as connection:
        row = connection.execute(
            "SELECT id FROM cognitive_jobs WHERE brain_id=? "
            "AND job_type='session_distill' ORDER BY created_at DESC LIMIT 1",
            (store.brain_id,),
        ).fetchone()
    assert row is not None
    return str(row["id"])


@pytest.fixture
def atlas_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Provide a clean Atlas profile with no model or index signing keys."""
    from hermes_cli import config as config_module
    from hermes_cli import managed_scope

    home = tmp_path / "atlas-profile"
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("HERMES_PUBLIC_BRAND", "atlas")
    monkeypatch.delenv("ATLAS_MANAGED_MODE", raising=False)
    for name in _MODEL_KEY_ENV:
        monkeypatch.delenv(name, raising=False)
    config_module._LOAD_CONFIG_CACHE.clear()
    config_module._LAST_EXPANDED_CONFIG_BY_PATH.clear()
    managed_scope.invalidate_managed_cache()
    yield home
    config_module._LOAD_CONFIG_CACHE.clear()
    config_module._LAST_EXPANDED_CONFIG_BY_PATH.clear()
    managed_scope.invalidate_managed_cache()


def _parser(handler) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atlas")
    subparsers = parser.add_subparsers(dest="command")
    build_cortex_parser(subparsers, cmd_cortex=handler)
    return parser


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["cortex"], {"cortex_command": None}),
        (["cortex", "status"], {"cortex_command": "status"}),
        (
            ["cortex", "dream"],
            {"cortex_command": "dream", "run_now": False},
        ),
        (
            ["cortex", "dream", "--run-now"],
            {"cortex_command": "dream", "run_now": True},
        ),
        (
            ["cortex", "index"],
            {"cortex_command": "index", "cortex_index_command": None},
        ),
        (
            ["cortex", "index", "list"],
            {"cortex_command": "index", "cortex_index_command": "list"},
        ),
        (
            ["cortex", "index", "import", "release-2026", "--publish"],
            {
                "cortex_command": "index",
                "cortex_index_command": "import",
                "path": "release-2026",
                "publish": True,
            },
        ),
        (
            ["cortex", "index", "publish", "v2"],
            {
                "cortex_command": "index",
                "cortex_index_command": "publish",
                "version": "v2",
            },
        ),
        (
            ["cortex", "index", "rollback", "v1"],
            {
                "cortex_command": "index",
                "cortex_index_command": "rollback",
                "version": "v1",
            },
        ),
    ],
)
def test_cortex_parser_wires_every_command_to_one_handler(
    argv: list[str], expected: dict[str, object]
) -> None:
    handler = object()

    args = _parser(handler).parse_args(argv)

    assert args.command == "cortex"
    assert args.func is handler
    for name, value in expected.items():
        assert getattr(args, name) == value


def test_status_initializes_and_reports_healthy_without_model_keys(
    atlas_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = argparse.Namespace(cortex_command="status")

    assert cortex_command(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["name"] == "Atlas Cortex"
    assert payload["status"] == "healthy"
    assert payload["capabilities"] == {
        "full_text_search": True,
        "graphrag": False,
        "temporal_memory": True,
        "typed_graph": True,
    }
    assert payload["graphrag"] is None
    assert payload["jobs"]["pending"] == 0
    assert (atlas_home / "cortex" / "cortex.db").is_file()


def test_manual_run_now_never_leases_an_older_semantic_job(
    atlas_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from altas.cortex.runtime import open_cortex_store

    store, _config = open_cortex_store(atlas_home)
    semantic_job = _finalize_semantic_job(store, "session-old")

    assert cortex_command(argparse.Namespace(cortex_command="dream", run_now=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["processed"]["status"] == "succeeded"
    with store.connect() as connection:
        row = connection.execute(
            "SELECT state, attempt FROM cognitive_jobs WHERE id=?", (semantic_job,)
        ).fetchone()
    assert dict(row) == {"state": "queued", "attempt": 0}


def test_manual_run_now_drains_older_deterministic_work_until_its_own_job(
    atlas_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from altas.cortex.runtime import open_cortex_store

    store, _config = open_cortex_store(atlas_home)
    older_job = store.enqueue_job(
        "session_checkpoint",
        input_hash="older-deterministic-job",
        input_data={"session_id": "session-older"},
    )

    assert cortex_command(argparse.Namespace(cortex_command="dream", run_now=True)) == 0

    payload = json.loads(capsys.readouterr().out)
    target_id = payload["queued"]["job"]["id"]
    assert payload["drained"] == 2
    assert payload["processed"]["job_id"] == target_id
    assert payload["target"]["job"]["id"] == target_id
    assert payload["target"]["job"]["status"] == "succeeded"
    with store.connect() as connection:
        rows = connection.execute(
            "SELECT id, state FROM cognitive_jobs WHERE id IN (?,?)",
            (older_job, target_id),
        ).fetchall()
    assert {row["id"]: row["state"] for row in rows} == {
        older_job: "succeeded",
        target_id: "succeeded",
    }


def test_manual_run_now_returns_nonzero_when_its_job_does_not_succeed(
    atlas_home: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from altas.cortex.worker import WorkerResult

    monkeypatch.setattr(
        "altas.cortex.worker.CortexDreamWorker.run_once",
        lambda self, **kwargs: WorkerResult("locked"),
    )

    assert cortex_command(argparse.Namespace(cortex_command="dream", run_now=True)) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["processed"]["status"] == "locked"
    assert payload["target"]["job"]["status"] == "queued"


@pytest.mark.parametrize(
    "args",
    [
        argparse.Namespace(cortex_command="index", cortex_index_command=None),
        argparse.Namespace(cortex_command="index", cortex_index_command="list"),
    ],
)
def test_index_list_is_empty_and_usable_without_model_or_signing_keys(
    atlas_home: Path,
    capsys: pytest.CaptureFixture[str],
    args: argparse.Namespace,
) -> None:
    assert cortex_command(args) == 0

    assert json.loads(capsys.readouterr().out) == {
        "active": None,
        "indexes": [],
    }
    assert (atlas_home / "cortex" / "cortex.db").is_file()
