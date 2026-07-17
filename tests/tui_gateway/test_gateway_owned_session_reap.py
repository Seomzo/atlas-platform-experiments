"""Tests for #60609: the TUI backend must not end gateway-owned sessions.

An explicit ``_finalize_session`` marks TUI-owned sessions ended in state.db,
while operational reapers are non-semantic resource cleanup. For sessions the
messaging gateway owns (telegram, discord, ...), an end write creates the
Groundhog Day routing loop described in #60609 — the gateway self-heal
drops the ended-but-routed entry, recovers hours-old parent context, and
loops.  The TUI is only a viewer of those sessions.
"""

from unittest.mock import MagicMock, patch

from tui_gateway.server import _finalize_session, _is_gateway_owned_source


class TestIsGatewayOwnedSource:
    def test_builtin_gateway_platforms_are_owned(self):
        for src in ("telegram", "discord", "whatsapp", "slack", "signal",
                    "matrix", "mattermost", "bluebubbles", "sms", "email"):
            assert _is_gateway_owned_source(src) is True, src

    def test_case_and_whitespace_normalized(self):
        assert _is_gateway_owned_source(" Telegram ") is True

    def test_tui_owned_sources_are_not(self):
        for src in ("tui", "cli", "webui", "desktop", "cron", "subagent",
                    "test", "acp", ""):
            assert _is_gateway_owned_source(src) is False, src

    def test_local_and_server_endpoints_are_not(self):
        # Platform enum members, but their sessions aren't owned by a remote
        # chat surface — reaping them keeps /resume clean.
        for src in ("local", "webhook", "api_server", "msgraph_webhook"):
            assert _is_gateway_owned_source(src) is False, src

    def test_arbitrary_strings_are_not(self):
        assert _is_gateway_owned_source("hermesbench-task-xyz") is False
        assert _is_gateway_owned_source(None) is False


def _make_session(session_id="sess_1"):
    agent = MagicMock()
    agent.session_id = session_id
    return {
        "agent": agent,
        "history": [{"role": "user", "content": "x"}],
        "history_lock": None,
        "session_key": session_id,
    }


class TestFinalizeSkipsGatewaySessions:
    @patch("tui_gateway.server._get_db")
    def test_gateway_session_not_ended(self, mock_get_db):
        db = MagicMock()
        db.get_session.return_value = {"id": "sess_1", "source": "telegram"}
        mock_get_db.return_value = db

        _finalize_session(_make_session(), end_reason="tui_close")

        db.end_session.assert_not_called()

    @patch("tui_gateway.server._get_db")
    def test_tui_session_still_ended(self, mock_get_db):
        db = MagicMock()
        db.get_session.return_value = {"id": "sess_1", "source": "tui"}
        mock_get_db.return_value = db

        _finalize_session(_make_session(), end_reason="tui_close")

        db.end_session.assert_called_once_with("sess_1", "tui_close")

    @patch("tui_gateway.server._get_db")
    def test_missing_row_still_ended(self, mock_get_db):
        """A session with no state.db row can't be gateway-owned — keep the
        pre-existing reap behavior."""
        db = MagicMock()
        db.get_session.return_value = None
        mock_get_db.return_value = db

        _finalize_session(_make_session(), end_reason="tui_close")

        db.end_session.assert_called_once_with("sess_1", "tui_close")

    def test_global_remote_session_ends_profile_row_not_launch_row(
        self, monkeypatch, tmp_path
    ):
        """A semantic close must follow ``profile_home`` to the owning DB."""
        from hermes_state import SessionDB

        launch_home = tmp_path / "launch"
        profile_home = tmp_path / "profiles" / "customer"
        launch_home.mkdir()
        profile_home.mkdir(parents=True)
        launch_db = SessionDB(db_path=launch_home / "state.db")
        profile_db = SessionDB(db_path=profile_home / "state.db")
        try:
            for db in (launch_db, profile_db):
                db.create_session("sess_1", source="tui", model="test")

            monkeypatch.setattr("tui_gateway.server._get_db", lambda: launch_db)
            monkeypatch.setattr(
                "tui_gateway.server._notify_session_boundary", lambda *_args: None
            )
            session = _make_session()
            session["profile_home"] = str(profile_home)

            _finalize_session(session, end_reason="tui_close")

            assert profile_db.get_session("sess_1")["end_reason"] == "tui_close"
            assert launch_db.get_session("sess_1")["ended_at"] is None
        finally:
            profile_db.close()
            launch_db.close()

    def test_global_remote_gateway_row_is_viewer_only(
        self, monkeypatch, tmp_path
    ):
        """Messaging ownership is read from the profile DB, never launch DB."""
        from hermes_state import SessionDB

        launch_home = tmp_path / "launch"
        profile_home = tmp_path / "profiles" / "customer"
        launch_home.mkdir()
        profile_home.mkdir(parents=True)
        launch_db = SessionDB(db_path=launch_home / "state.db")
        profile_db = SessionDB(db_path=profile_home / "state.db")
        try:
            launch_db.create_session("sess_1", source="tui", model="test")
            profile_db.create_session("sess_1", source="telegram", model="test")

            monkeypatch.setattr("tui_gateway.server._get_db", lambda: launch_db)
            session = _make_session()
            session["profile_home"] = str(profile_home)

            _finalize_session(session, end_reason="tui_close")

            assert session["_tui_owns_lifecycle"] is False
            assert profile_db.get_session("sess_1")["ended_at"] is None
            assert launch_db.get_session("sess_1")["ended_at"] is None
            session["agent"]._persist_session.assert_not_called()
            session["agent"].commit_memory_session.assert_not_called()
        finally:
            profile_db.close()
            launch_db.close()
