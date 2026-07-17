import sys

import pytest


def test_sessions_delete_accepts_unique_id_prefix(monkeypatch, capsys):
    import altas.cortex.lifecycle as cortex_lifecycle
    import hermes_cli.main as main_mod
    import hermes_state

    captured = {}
    calls = []

    class FakeDB:
        def resolve_session_id(self, session_id):
            captured["resolved_from"] = session_id
            return "20260315_092437_c9a6ff"

        def get_session_delete_closure(self, session_ids):
            calls.append(("closure", tuple(session_ids)))
            return ["root", "20260315_092437_c9a6ff", "delegate"]

        def delete_sessions(self, session_ids, **kwargs):
            kwargs["before_delete"](tuple(session_ids))
            calls.append(("delete", tuple(session_ids)))
            captured["deleted"] = tuple(session_ids)
            return len(session_ids)

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(hermes_state, "SessionDB", lambda: FakeDB())

    def _reconcile(_home, session_ids):
        calls.append(("reconcile", tuple(session_ids)))
        return tuple(session_ids)

    monkeypatch.setattr(
        cortex_lifecycle,
        "reconcile_detached_session_deletions",
        _reconcile,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["hermes", "sessions", "delete", "20260315_092437_c9a6", "--yes"],
    )

    main_mod.main()

    output = capsys.readouterr().out
    assert captured == {
        "resolved_from": "20260315_092437_c9a6",
        "deleted": ("root", "20260315_092437_c9a6ff", "delegate"),
        "closed": True,
    }
    assert calls == [
        ("closure", ("20260315_092437_c9a6ff",)),
        ("reconcile", ("root", "20260315_092437_c9a6ff", "delegate")),
        ("delete", ("root", "20260315_092437_c9a6ff", "delegate")),
    ]
    assert "Deleted session '20260315_092437_c9a6ff'." in output


def test_sessions_delete_leaves_transcripts_when_cortex_reconciliation_fails(
    monkeypatch, capsys
):
    import altas.cortex.lifecycle as cortex_lifecycle
    import hermes_cli.main as main_mod
    import hermes_state

    captured = {"closed": False}

    class FakeDB:
        def resolve_session_id(self, _session_id):
            return "s1"

        def get_session_delete_closure(self, session_ids):
            assert session_ids == ["s1"]
            return ["s1", "delegate"]

        def delete_sessions(self, session_ids, **kwargs):
            kwargs["before_delete"](tuple(session_ids))
            raise AssertionError("SessionDB must remain unchanged on Cortex failure")

        def close(self):
            captured["closed"] = True

    def _fail_reconcile(_home, session_ids):
        assert session_ids == ["s1", "delegate"]
        raise RuntimeError("semantic job is still running")

    monkeypatch.setattr(hermes_state, "SessionDB", lambda: FakeDB())
    monkeypatch.setattr(
        cortex_lifecycle,
        "reconcile_detached_session_deletions",
        _fail_reconcile,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["hermes", "sessions", "delete", "s1", "--yes"],
    )

    main_mod.main()

    output = capsys.readouterr().out
    assert "Delete aborted before transcript removal" in output
    assert "semantic job is still running" in output
    assert captured["closed"] is True


def test_sessions_delete_refuses_cross_process_active_session(monkeypatch, capsys):
    import hermes_cli.main as main_mod
    import hermes_state
    from hermes_cli.active_sessions import try_acquire_active_session

    class FakeDB:
        def resolve_session_id(self, _session_id):
            return "s1"

        def get_session_delete_closure(self, _session_ids):
            return ["s1"]

        def delete_sessions(self, *_args, **_kwargs):
            raise AssertionError("active transcript must not be deleted")

        def close(self):
            pass

    monkeypatch.setattr(hermes_state, "SessionDB", lambda: FakeDB())
    lease, message = try_acquire_active_session(
        session_id="s1",
        surface="tui",
        config={},
    )
    assert message is None
    assert lease is not None
    monkeypatch.setattr(
        sys,
        "argv",
        ["hermes", "sessions", "delete", "s1", "--yes"],
    )
    try:
        main_mod.main()
    finally:
        lease.release()

    output = capsys.readouterr().out
    assert "Delete aborted before transcript removal" in output
    assert "session is active" in output


def test_sessions_delete_reports_not_found_when_prefix_is_unknown(monkeypatch, capsys):
    import hermes_cli.main as main_mod
    import hermes_state

    class FakeDB:
        def resolve_session_id(self, session_id):
            return None

        def delete_session(self, session_id, **kwargs):
            raise AssertionError("delete_session should not be called when resolution fails")

        def close(self):
            pass

    monkeypatch.setattr(hermes_state, "SessionDB", lambda: FakeDB())
    monkeypatch.setattr(
        sys,
        "argv",
        ["hermes", "sessions", "delete", "missing-prefix", "--yes"],
    )

    main_mod.main()

    output = capsys.readouterr().out
    assert "Session 'missing-prefix' not found." in output


def test_sessions_delete_handles_eoferror_on_confirm(monkeypatch, capsys):
    """sessions delete should not crash when stdin is closed (non-TTY)."""
    import hermes_cli.main as main_mod
    import hermes_state

    class FakeDB:
        def resolve_session_id(self, session_id):
            return "20260315_092437_c9a6ff"

        def delete_session(self, session_id, **kwargs):
            raise AssertionError("delete_session should not be called when cancelled")

        def close(self):
            pass

    monkeypatch.setattr(hermes_state, "SessionDB", lambda: FakeDB())
    monkeypatch.setattr(
        sys, "argv",
        ["hermes", "sessions", "delete", "20260315_092437_c9a6"],
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": (_ for _ in ()).throw(EOFError))

    main_mod.main()

    output = capsys.readouterr().out
    assert "Cancelled" in output


def test_sessions_prune_handles_eoferror_on_confirm(monkeypatch, capsys):
    """sessions prune should not crash when stdin is closed (non-TTY)."""
    import hermes_cli.main as main_mod
    import hermes_state

    class FakeDB:
        def list_prune_candidates(self, **kwargs):
            return [
                {
                    "id": "20260315_092437_c9a6ff",
                    "source": "cli",
                    "title": "old session",
                    "started_at": 0.0,
                    "ended_at": 1.0,
                    "message_count": 3,
                    "archived": 0,
                }
            ]

        def prune_sessions(self, **kwargs):
            raise AssertionError("prune_sessions should not be called when cancelled")

        def close(self):
            pass

    monkeypatch.setattr(hermes_state, "SessionDB", lambda: FakeDB())
    monkeypatch.setattr(
        sys, "argv",
        ["hermes", "sessions", "prune"],
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": (_ for _ in ()).throw(EOFError))

    main_mod.main()

    output = capsys.readouterr().out
    assert "Cancelled" in output


def _run_prune(monkeypatch, capsys, argv_tail, candidates=None):
    """Run `hermes sessions prune <argv_tail>` against a FakeDB, capturing
    the filter kwargs passed to list_prune_candidates. Auto-confirms."""
    import hermes_cli.main as main_mod
    import hermes_state

    seen = {}
    rows = candidates if candidates is not None else [
        {
            "id": "20260101_000000_aaaaaa",
            "source": "cron",
            "title": "oldest run",
            "started_at": 1_600_000_000.0,
            "ended_at": 1_600_000_100.0,
            "message_count": 2,
            "archived": 0,
        },
        {
            "id": "20260601_000000_bbbbbb",
            "source": "cron",
            "title": "newest run",
            "started_at": 1_700_000_000.0,
            "ended_at": 1_700_000_100.0,
            "message_count": 4,
            "archived": 0,
        },
    ]

    class FakeDB:
        def list_prune_candidates(self, **kwargs):
            seen.update(kwargs)
            return rows

        def prune_sessions(self, **kwargs):
            return len(rows)

        def close(self):
            pass

    monkeypatch.setattr(hermes_state, "SessionDB", lambda: FakeDB())
    monkeypatch.setattr(
        sys, "argv", ["hermes", "sessions", "prune", *argv_tail]
    )
    monkeypatch.setattr("builtins.input", lambda _prompt="": "y")
    main_mod.main()
    return seen, capsys.readouterr().out


def test_sessions_prune_bare_keeps_90_day_default(monkeypatch, capsys):
    """A truly bare `hermes sessions prune` keeps the implicit 90-day cutoff."""
    import time as _time

    filters, _out = _run_prune(monkeypatch, capsys, [])
    assert filters["started_before"] is not None
    assert filters["started_before"] == pytest.approx(
        _time.time() - 90 * 86400, abs=60
    )


def test_sessions_prune_source_matches_all_ages(monkeypatch, capsys):
    """--source alone suppresses the implicit 90-day cutoff (all ages)."""
    filters, _out = _run_prune(monkeypatch, capsys, ["--source", "cron"])
    assert filters["started_before"] is None
    assert filters["started_after"] is None
    assert filters["source"] == "cron"


def test_sessions_prune_source_with_explicit_time_respected(monkeypatch, capsys):
    """--source + explicit --older-than keeps the user's bound."""
    import time as _time

    filters, _out = _run_prune(
        monkeypatch, capsys, ["--source", "cron", "--older-than", "30"]
    )
    assert filters["started_before"] == pytest.approx(
        _time.time() - 30 * 86400, abs=60
    )
    assert filters["source"] == "cron"


def test_sessions_prune_preview_shows_oldest_newest(monkeypatch, capsys):
    """Confirmation preview surfaces count + oldest/newest session times."""
    from hermes_cli.session_filters import format_epoch

    _filters, out = _run_prune(monkeypatch, capsys, ["--source", "cron"])
    assert "2 session(s) match" in out
    assert f"oldest {format_epoch(1_600_000_000.0)}" in out
    assert f"newest {format_epoch(1_700_000_000.0)}" in out
