from hermes_cli import active_sessions
from hermes_cli.session_deletion import (
    delete_empty_sessions_with_cortex,
    delete_sessions_with_cortex,
)
from hermes_state import SessionDB


def test_explicit_delete_refuses_active_gateway_alias(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    db = SessionDB(db_path=home / "state.db")
    db.create_session(
        "durable-id",
        "telegram",
        session_key="telegram:chat:42",
    )
    lease, message = active_sessions.try_acquire_active_session(
        session_id="telegram:chat:42",
        surface="gateway:telegram",
        config={},
        hermes_home=home,
    )
    assert message is None
    assert lease is not None
    reconciled = []

    try:
        try:
            delete_sessions_with_cortex(
                db,
                home,
                ["durable-id"],
                reconciler=lambda _home, ids: reconciled.append(ids) or ids,
            )
        except active_sessions.ActiveSessionConflict:
            pass
        else:
            raise AssertionError("active gateway route must block deletion")

        assert reconciled == []
        assert db.get_session("durable-id") is not None
    finally:
        lease.release()
        db.close()


def test_explicit_delete_reconciles_before_removing_exact_scope(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    db = SessionDB(db_path=home / "state.db")
    db.create_session("parent", "cli")
    db.end_session("parent", "done")
    db.create_session(
        "unlisted-delegate",
        "tool",
        parent_session_id="parent",
        model_config={"_delegate_from": "parent"},
    )
    db.end_session("unlisted-delegate", "done")
    observed = []

    def reconcile(_home, ids):
        parent_exists = db._conn.execute(
            "SELECT 1 FROM sessions WHERE id='parent'"
        ).fetchone() is not None
        observed.append((tuple(ids), parent_exists))
        return ids

    delete_scope = db.get_session_delete_closure(["parent"])
    ids, deleted = delete_sessions_with_cortex(
        db,
        home,
        delete_scope,
        reconciler=reconcile,
    )

    assert ids == ("parent", "unlisted-delegate")
    assert deleted == 2
    assert observed == [(("parent", "unlisted-delegate"), True)]
    assert db.get_session("parent") is None
    assert db.get_session("unlisted-delegate") is None
    stale, message = active_sessions.try_acquire_active_session(
        session_id="parent",
        surface="tui",
        config={},
        hermes_home=home,
    )
    assert stale is None
    assert message == "This session was deleted and cannot be resumed."
    db.close()


def test_explicit_delete_keeps_delegate_out_of_compression_lineage(
    tmp_path, monkeypatch
):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    db = SessionDB(db_path=home / "state.db")
    db.create_session("root", "cli")
    db.create_session(
        "delegate",
        "tool",
        parent_session_id="root",
        model_config={"_delegate_from": "root"},
    )
    db.end_session("delegate", "done")
    db.end_session("root", "compression")
    db.create_session("tip", "cli", parent_session_id="root")
    db.end_session("tip", "done")
    observed = []

    delete_scope = db.get_session_delete_closure(["tip"])
    assert db.get_compression_lineage("tip") == ["root", "tip"]
    assert delete_scope == ["root", "tip", "delegate"]

    ids, deleted = delete_sessions_with_cortex(
        db,
        home,
        delete_scope,
        reconciler=lambda _home, exact: observed.append(tuple(exact)) or exact,
    )

    assert ids == ("root", "tip", "delegate")
    assert deleted == 3
    assert observed == [("root", "tip", "delegate")]
    assert db.get_session("root") is None
    assert db.get_session("tip") is None
    assert db.get_session("delegate") is None
    db.close()


def test_empty_delete_reconciles_only_revalidated_snapshot(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))
    db = SessionDB(db_path=home / "state.db")
    db.create_session("empty", "cli")
    db.end_session("empty", "done")
    observed = []

    ids, deleted = delete_empty_sessions_with_cortex(
        db,
        home,
        reconciler=lambda _home, exact: observed.append(tuple(exact)) or exact,
    )

    assert ids == ("empty",)
    assert deleted == 1
    assert observed == [("empty",)]
    assert db.get_session("empty") is None
    db.close()
