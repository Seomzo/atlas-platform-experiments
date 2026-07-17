from __future__ import annotations

import time
from pathlib import Path

import pytest

from altas.cortex.config import CortexConfig
from altas.cortex.models import EvidenceInput
from altas.cortex.privacy import (
    list_prune_candidates_with_cortex,
    maybe_auto_prune_and_vacuum_with_cortex,
    prune_sessions_with_cortex,
)
from altas.cortex.store import CortexStore
from hermes_state import SessionDB


@pytest.fixture()
def retention_harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "profile"
    home.mkdir()
    config = CortexConfig.from_mapping(
        {
            "cortex": {
                "enabled": True,
                "storage": {"backend": "sqlite", "path": "cortex/cortex.db"},
                "dream": {"enabled": False},
                "graphrag": {"enabled": False},
                "security": {"redact_secrets": False},
            }
        },
        home,
    )
    store = CortexStore(
        config.database_path,
        owner_customer_id="customer:retention-tests",
        redact_secrets=False,
    )
    store.initialize()
    session_db = SessionDB(home / "state.db")
    monkeypatch.setattr(
        "altas.cortex.privacy.CortexConfig.load",
        lambda _home: config,
    )
    monkeypatch.setattr(
        "altas.cortex.privacy.open_cortex_store",
        lambda _home, _identity: (store, config),
    )
    yield home, session_db, store, config
    session_db.close()


def _backdate(session_db: SessionDB, *session_ids: str) -> None:
    old = time.time() - 200 * 86_400

    def _write(connection):
        connection.executemany(
            "UPDATE sessions SET started_at=? WHERE id=?",
            [(old, session_id) for session_id in session_ids],
        )

    session_db._execute_write(_write)


def _compression_lineage(
    session_db: SessionDB,
    store: CortexStore,
    *,
    tip_ended: bool,
) -> None:
    session_db.create_session("root", "cli")
    session_db.end_session("root", "compression")
    session_db.create_session("tip", "cli", parent_session_id="root")
    if tip_ended:
        session_db.end_session("tip", "done")
    _backdate(session_db, "root", "tip")
    store.ensure_session("root", logical_conversation_id="root")
    store.ensure_session(
        "tip",
        parent_session_id="root",
        logical_conversation_id="root",
    )


def test_retention_keeps_old_root_when_active_tip_survives(
    retention_harness,
) -> None:
    home, session_db, store, _ = retention_harness
    _compression_lineage(session_db, store, tip_ended=False)

    assert list_prune_candidates_with_cortex(
        home, session_db, older_than_days=90
    ) == []
    assert prune_sessions_with_cortex(home, session_db, older_than_days=90) == 0

    assert session_db.get_session("root") is not None
    assert session_db.get_session("tip") is not None
    assert store.session_lineage("root")["state"] == "active"
    assert store.session_lineage("tip")["state"] == "active"


def test_retention_deletes_complete_ended_lineage_without_semantic_work(
    retention_harness,
) -> None:
    home, session_db, store, _ = retention_harness
    _compression_lineage(session_db, store, tip_ended=True)
    store.append_evidence(
        "root",
        EvidenceInput(
            source_type="user_message",
            source_locator="session:root:row:1:user",
            content="A fact that must be tombstoned before retention deletion.",
        ),
    )

    assert {
        row["id"]
        for row in list_prune_candidates_with_cortex(
            home, session_db, older_than_days=90
        )
    } == {"root", "tip"}
    assert prune_sessions_with_cortex(home, session_db, older_than_days=90) == 2

    assert session_db.get_session("root") is None
    assert session_db.get_session("tip") is None
    assert store.session_lineage("root")["state"] == "deleted"
    assert store.session_lineage("tip")["state"] == "deleted"
    with store.connect() as connection:
        evidence = connection.execute(
            "SELECT tombstoned_at FROM evidence_items WHERE brain_id=?",
            (store.brain_id,),
        ).fetchall()
        admissions = connection.execute(
            "SELECT id FROM session_distill_admissions WHERE brain_id=?",
            (store.brain_id,),
        ).fetchall()
        model_jobs = connection.execute(
            "SELECT id FROM cognitive_jobs WHERE brain_id=? "
            "AND job_type='session_distill'",
            (store.brain_id,),
        ).fetchall()
    assert evidence and all(row["tombstoned_at"] for row in evidence)
    assert admissions == []
    assert model_jobs == []


def test_retention_deletes_standalone_session_with_existing_cortex_store(
    retention_harness,
) -> None:
    home, session_db, _store, _ = retention_harness
    session_db.create_session("standalone", "cli")
    session_db.end_session("standalone", "done")
    _backdate(session_db, "standalone")

    assert prune_sessions_with_cortex(home, session_db, older_than_days=90) == 1
    assert session_db.get_session("standalone") is None


def test_retention_preserves_branch_and_delegate_children_independently(
    retention_harness,
) -> None:
    home, session_db, store, _ = retention_harness
    session_db.create_session("parent", "cli")
    session_db.end_session("parent", "done")
    session_db.create_session(
        "branch",
        "cli",
        parent_session_id="parent",
        model_config={"_branched_from": "parent"},
    )
    session_db.end_session("branch", "done")
    session_db.create_session(
        "delegate",
        "cli",
        parent_session_id="parent",
        model_config={"_delegate_from": "parent"},
    )
    session_db.end_session("delegate", "done")
    _backdate(session_db, "parent")
    store.ensure_session("parent", logical_conversation_id="parent")
    store.ensure_session(
        "branch",
        parent_session_id="parent",
        logical_conversation_id="branch",
    )
    store.ensure_session(
        "delegate",
        parent_session_id="parent",
        logical_conversation_id="delegate",
    )

    assert prune_sessions_with_cortex(home, session_db, older_than_days=90) == 1

    assert session_db.get_session("parent") is None
    branch = session_db.get_session("branch")
    delegate = session_db.get_session("delegate")
    assert branch is not None and branch["parent_session_id"] is None
    assert delegate is not None and delegate["parent_session_id"] is None
    assert store.session_lineage("parent")["state"] == "deleted"
    assert store.session_lineage("branch")["state"] == "active"
    assert store.session_lineage("delegate")["state"] == "active"


def test_retention_reconciliation_failure_leaves_sessiondb_untouched(
    retention_harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, session_db, store, _ = retention_harness
    _compression_lineage(session_db, store, tip_ended=True)

    def _fail(_session_id: str) -> int:
        raise RuntimeError("simulated Cortex failure")

    monkeypatch.setattr(store, "reconcile_session_delete", _fail)
    with pytest.raises(RuntimeError, match="simulated Cortex failure"):
        prune_sessions_with_cortex(home, session_db, older_than_days=90)

    assert session_db.get_session("root") is not None
    assert session_db.get_session("tip") is not None


def test_cortex_auto_retention_mirrors_marker_skip_and_vacuum(
    retention_harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, session_db, store, _ = retention_harness
    _compression_lineage(session_db, store, tip_ended=True)
    vacuum_calls: list[bool] = []
    monkeypatch.setattr(session_db, "vacuum", lambda: vacuum_calls.append(True))

    first = maybe_auto_prune_and_vacuum_with_cortex(
        home,
        session_db,
        retention_days=90,
        min_interval_hours=24,
    )
    second = maybe_auto_prune_and_vacuum_with_cortex(
        home,
        session_db,
        retention_days=90,
        min_interval_hours=24,
    )

    assert first == {"skipped": False, "pruned": 2, "vacuumed": True}
    assert second == {"skipped": True, "pruned": 0, "vacuumed": False}
    assert vacuum_calls == [True]
    assert session_db.get_meta("last_auto_prune") is not None


def test_cortex_auto_retention_failure_never_raises_or_marks_success(
    retention_harness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home, session_db, store, _ = retention_harness
    _compression_lineage(session_db, store, tip_ended=True)

    def _fail(_session_id: str) -> int:
        raise RuntimeError("simulated auto-maintenance failure")

    monkeypatch.setattr(store, "reconcile_session_delete", _fail)
    result = maybe_auto_prune_and_vacuum_with_cortex(
        home,
        session_db,
        retention_days=90,
        min_interval_hours=24,
    )

    assert result["skipped"] is False
    assert result["pruned"] == 0
    assert result["vacuumed"] is False
    assert "simulated auto-maintenance failure" in result["error"]
    assert session_db.get_meta("last_auto_prune") is None
    assert session_db.get_session("root") is not None
    assert session_db.get_session("tip") is not None


def test_retention_without_cortex_database_uses_historical_prune(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "profile"
    home.mkdir()
    config = CortexConfig.from_mapping(
        {"cortex": {"storage": {"path": "cortex/missing.db"}}},
        home,
    )
    session_db = SessionDB(home / "state.db")
    monkeypatch.setattr(
        "altas.cortex.privacy.CortexConfig.load",
        lambda _home: config,
    )
    session_db.create_session("old", "cli")
    session_db.end_session("old", "done")
    _backdate(session_db, "old")
    try:
        assert prune_sessions_with_cortex(home, session_db, older_than_days=90) == 1
        assert session_db.get_session("old") is None
    finally:
        session_db.close()
