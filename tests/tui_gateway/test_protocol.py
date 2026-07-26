"""Tests for tui_gateway JSON-RPC protocol plumbing."""

import io
import json
import sys
import threading
import time
import types
from unittest.mock import MagicMock, patch

import pytest

_original_stdout = sys.stdout


@pytest.fixture(autouse=True)
def _restore_stdout():
    yield
    sys.stdout = _original_stdout


@pytest.fixture()
def server():
    with patch.dict("sys.modules", {
        "hermes_constants": MagicMock(get_hermes_home=MagicMock(return_value="/tmp/hermes_test")),
        "hermes_cli.env_loader": MagicMock(),
        "hermes_cli.banner": MagicMock(),
        "hermes_state": MagicMock(),
    }):
        import importlib
        mod = importlib.import_module("tui_gateway.server")
        yield mod
        # Reset module-level session state without re-importing. importlib.reload
        # would re-register the module's atexit hooks (ThreadPoolExecutor
        # shutdown, _shutdown_sessions); the duplicates race the stderr
        # buffer at interpreter shutdown and surface as Fatal Python error:
        # _enter_buffered_busy. Clearing the per-session dicts gives the
        # next test a clean slate; _methods is NOT cleared because it's
        # populated at module import time and re-registration only happens
        # via reload (which we don't do).
        mod._sessions.clear()
        mod._pending.clear()
        mod._pending_prompt_payloads.clear()
        mod._answers.clear()


@pytest.fixture()
def capture(server):
    """Redirect server's real stdout to a StringIO and return (server, buf)."""
    buf = io.StringIO()
    server._real_stdout = buf
    return server, buf


# ── JSON-RPC envelope ────────────────────────────────────────────────


def test_unknown_method(server):
    resp = server.handle_request({"id": "1", "method": "bogus"})
    assert resp["error"]["code"] == -32601


def test_ok_envelope(server):
    assert server._ok("r1", {"x": 1}) == {
        "jsonrpc": "2.0", "id": "r1", "result": {"x": 1},
    }


def test_err_envelope(server):
    assert server._err("r2", 4001, "nope") == {
        "jsonrpc": "2.0", "id": "r2", "error": {"code": 4001, "message": "nope"},
    }


# ── write_json ───────────────────────────────────────────────────────


def test_write_json(capture):
    server, buf = capture
    assert server.write_json({"test": True})
    assert json.loads(buf.getvalue()) == {"test": True}


def test_write_json_broken_pipe(server):
    class _Broken:
        def write(self, _): raise BrokenPipeError
        def flush(self): raise BrokenPipeError

    server._real_stdout = _Broken()
    assert server.write_json({"x": 1}) is False


def test_write_json_closed_stream_returns_false(server):
    """ValueError ('I/O on closed file') used to bubble up; treat as gone."""

    class _Closed:
        def write(self, _): raise ValueError("I/O operation on closed file")
        def flush(self): raise ValueError("I/O operation on closed file")

    server._real_stdout = _Closed()
    assert server.write_json({"x": 1}) is False


def test_write_json_unicode_encode_error_re_raises(server):
    """A non-UTF-8 stdout encoding raises UnicodeEncodeError (a ValueError
    subclass).  It must NOT be swallowed as 'peer gone' — that would let
    `entry.py` exit cleanly via the False path and hide the real config
    bug.  We re-raise so the existing crash-log infrastructure records it."""

    class _AsciiOnly:
        def write(self, line):
            line.encode("ascii")  # raises UnicodeEncodeError on non-ascii
        def flush(self): pass

    server._real_stdout = _AsciiOnly()
    with pytest.raises(UnicodeEncodeError):
        server.write_json({"msg": "héllo"})


def test_write_json_unrelated_value_error_re_raises(server):
    """Only ValueError('...closed file...') means peer gone.  Other
    ValueErrors are programming errors and must surface."""

    class _BadValue:
        def write(self, _): raise ValueError("something else entirely")
        def flush(self): pass

    server._real_stdout = _BadValue()
    with pytest.raises(ValueError, match="something else entirely"):
        server.write_json({"x": 1})


def test_write_json_non_serializable_payload_re_raises(server):
    """Non-JSON-safe payloads are programming errors — they must NOT be
    silently dropped via the False path (which would trigger a clean exit
    in entry.py and mask the real bug)."""
    import io

    server._real_stdout = io.StringIO()
    with pytest.raises(TypeError):
        server.write_json({"obj": object()})


def test_write_json_peer_gone_oserror_on_flush_returns_false(server):
    """A flush that raises a peer-gone OSError (EPIPE) must not strand
    the lock or crash; it returns False so the dispatcher exits cleanly."""
    import errno

    written = []

    class _FlushPeerGone:
        def write(self, line): written.append(line)
        def flush(self): raise OSError(errno.EPIPE, "broken pipe")

    server._real_stdout = _FlushPeerGone()
    assert server.write_json({"x": 1}) is False
    assert written and json.loads(written[0]) == {"x": 1}


def test_write_json_non_peer_gone_oserror_re_raises(server):
    """Host I/O failures (ENOSPC, EACCES, EIO …) are NOT peer-gone — they
    must re-raise so the crash log records them instead of looking like
    a clean disconnect via the False path."""
    import errno

    class _DiskFull:
        def write(self, _): raise OSError(errno.ENOSPC, "no space left")
        def flush(self): pass

    server._real_stdout = _DiskFull()
    with pytest.raises(OSError, match="no space"):
        server.write_json({"x": 1})


def test_write_json_skips_flush_when_disable_flush_true(monkeypatch):
    """`StdioTransport` skips flush when `_DISABLE_FLUSH` is true.

    Tests the runtime *behaviour* via direct module-attr patch.  The env
    var → module constant wiring is covered by the dedicated env test
    below; reloading server.py here would re-register atexit hooks and
    recreate the worker pool.
    """
    import importlib

    transport_mod = importlib.import_module("tui_gateway.transport")
    monkeypatch.setattr(transport_mod, "_DISABLE_FLUSH", True)

    flushed = {"count": 0}
    written = []

    class _Stream:
        def write(self, line): written.append(line)
        def flush(self): flushed["count"] += 1

    stream = _Stream()
    transport = transport_mod.StdioTransport(lambda: stream, threading.Lock())

    assert transport.write({"x": 1}) is True
    assert flushed["count"] == 0


def test_disable_flush_env_var_actually_wires_to_module_constant(monkeypatch):
    """End-to-end: setting `HERMES_TUI_GATEWAY_NO_FLUSH=1` and importing
    `tui_gateway.transport` fresh actually flips `_DISABLE_FLUSH` true.

    Reloads only the transport module — server.py is untouched so its
    atexit hooks/worker pool stay intact."""
    import importlib

    monkeypatch.setenv("HERMES_TUI_GATEWAY_NO_FLUSH", "1")
    transport_mod = importlib.reload(importlib.import_module("tui_gateway.transport"))

    try:
        assert transport_mod._DISABLE_FLUSH is True
    finally:
        # Restore the env-disabled state so other tests see the default.
        monkeypatch.delenv("HERMES_TUI_GATEWAY_NO_FLUSH", raising=False)
        importlib.reload(transport_mod)


# ── _emit ────────────────────────────────────────────────────────────


def test_emit_with_payload(capture):
    server, buf = capture
    server._emit("test.event", "s1", {"key": "val"})
    msg = json.loads(buf.getvalue())

    assert msg["method"] == "event"
    assert msg["params"]["type"] == "test.event"
    assert msg["params"]["session_id"] == "s1"
    assert msg["params"]["payload"]["key"] == "val"


def test_emit_without_payload(capture):
    server, buf = capture
    server._emit("ping", "s2")

    assert "payload" not in json.loads(buf.getvalue())["params"]


# ── Blocking prompt round-trip ───────────────────────────────────────


def test_block_and_respond(capture):
    server, _ = capture
    result = [None]

    threading.Thread(
        target=lambda: result.__setitem__(0, server._block("test.prompt", "s1", {"q": "?"}, timeout=5)),
    ).start()

    for _ in range(100):
        if server._pending:
            break
        threading.Event().wait(0.01)

    rid = next(iter(server._pending))
    server._answers[rid] = "my_answer"
    # _pending values are (sid, Event) tuples — unpack to set the Event
    _, ev = server._pending[rid]
    ev.set()

    threading.Event().wait(0.1)
    assert result[0] == "my_answer"


def test_clear_pending(server):
    ev = threading.Event()
    # _pending values are (sid, Event) tuples
    server._pending["r1"] = ("sid-x", ev)
    server._clear_pending()

    assert ev.is_set()
    assert server._answers["r1"] == ""


# ── Session lookup ───────────────────────────────────────────────────


def test_sess_missing(server):
    _, err = server._sess({"session_id": "nope"}, "r1")
    assert err["error"]["code"] == 4001


def test_sess_found(server):
    server._sessions["abc"] = {"agent": MagicMock()}
    s, err = server._sess({"session_id": "abc"}, "r1")

    assert s is not None
    assert err is None


# ── session.resume payload ────────────────────────────────────────────


def test_session_resume_returns_hydrated_messages(server, monkeypatch):
    class _DB:
        def get_session(self, _sid):
            return {"id": "20260409_010101_abc123"}

        def get_session_by_title(self, _title):
            return None

        def reopen_session(self, _sid):
            return None

        def get_messages_as_conversation(self, _sid, include_ancestors=False):
            return [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "yo", "reasoning": "thoughts"},
                {"role": "tool", "content": "searched"},
                {"role": "assistant", "content": "   "},
                {"role": "assistant", "content": None},
                {"role": "narrator", "content": "skip"},
            ]

    monkeypatch.setattr(server, "_get_db", lambda: _DB())
    monkeypatch.setattr(server, "_make_agent", lambda sid, key, session_id=None, session_db=None, **_kwargs: object())
    monkeypatch.setattr(server, "_init_session", lambda sid, key, agent, history, cols=80, **_kwargs: None)
    monkeypatch.setattr(server, "_session_info", lambda _agent, _session=None: {"model": "test/model"})

    resp = server.handle_request(
        {
            "id": "r1",
            "method": "session.resume",
            # eager_build: exercise the synchronous build path (this test
            # monkeypatches _make_agent/_init_session/_session_info).
            "params": {"session_id": "20260409_010101_abc123", "cols": 100, "eager_build": True},
        }
    )

    assert "error" not in resp
    assert resp["result"]["message_count"] == 3
    assert resp["result"]["messages"] == [
        {"role": "user", "text": "hello"},
        {"role": "assistant", "text": "yo", "reasoning": "thoughts"},
        {"role": "tool", "name": "tool", "context": ""},
    ]


def test_session_resume_defaults_to_deferred_build(server, monkeypatch):
    """A normal cold resume (no ``eager_build``) must return the full display
    transcript immediately and register an upgradable live session WITHOUT
    building the agent on the response path — that eager build is the
    multi-second switch latency. Deferred is the default; ``eager_build: true``
    opts back into the synchronous path."""

    target = "20260409_010101_abc123"

    class _DB:
        def get_session(self, _sid):
            return {
                "id": target,
                "model": "vendor/cool-model",
                "model_config": {"provider": "vendor"},
            }

        def get_session_by_title(self, _title):
            return None

        def resolve_resume_session_id(self, sid):
            return sid

        def reopen_session(self, _sid):
            return None

        def get_messages_as_conversation(self, _sid, include_ancestors=False):
            return [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "yo"},
            ]

    builds: list = []

    monkeypatch.setattr(server, "_get_db", lambda: _DB())
    # The response path must never call _make_agent; route the deferred timer
    # through a recorder so a 50ms fire can't build (or crash) under the test.
    monkeypatch.setattr(
        server, "_make_agent", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no eager build"))
    )
    monkeypatch.setattr(server, "_start_agent_build", lambda sid, session: builds.append(sid))
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)

    resp = server.handle_request(
        {
            "id": "r1",
            "method": "session.resume",
            "params": {"session_id": target, "cols": 100},
        }
    )

    assert "error" not in resp
    result = resp["result"]
    assert result["resumed"] == target
    assert result["session_key"] == target
    assert result["message_count"] == 2
    assert result["messages"] == [
        {"role": "user", "text": "hello"},
        {"role": "assistant", "text": "yo"},
    ]
    # Lazy info contract (same shape session.create returns), with the session's
    # persisted model/provider restored rather than the global default.
    assert result["info"]["lazy"] is True
    assert result["info"]["model"] == "vendor/cool-model"
    assert result["info"]["provider"] == "vendor"
    assert result["info"]["desktop_contract"] == server.DESKTOP_BACKEND_CONTRACT

    sid = result["session_id"]
    session = server._sessions[sid]
    # Registered but not built: agent is None and the resume key is carried so a
    # later prompt.submit / _sess() upgrade continues THIS stored conversation.
    assert session["agent"] is None
    assert session["resume_session_id"] == target
    assert not session["agent_ready"].is_set()
    # Not a watch spectator: a normal deferred resume is a real session.
    assert not session.get("lazy")
    # The persisted runtime identity is stashed for the deferred build so it
    # can't drop the provider ("No LLM provider configured").
    assert session["resume_runtime_overrides"]["model_override"]["model"] == "vendor/cool-model"
    assert server._find_live_session_by_key(target) == (sid, session)


def test_enforce_session_cap_evicts_oldest_detached_only(server, monkeypatch):
    """The LRU cap frees the least-recently-active DETACHED sessions when over
    the limit, and never a live-transport / running / mid-build one."""

    monkeypatch.setattr(server, "_load_cfg", lambda: {"max_live_sessions": 2})
    evicted: list[tuple[str, str | None, bool]] = []
    monkeypatch.setattr(
        server,
        "_close_session_by_id",
        lambda sid, end_reason=None, finalize=True: evicted.append(
            (sid, end_reason, finalize)
        ),
    )

    def _ready() -> threading.Event:
        ev = threading.Event()
        ev.set()
        return ev

    detached = server._detached_ws_transport
    live = object()  # no _closed attr -> live transport, never evictable

    server._sessions.clear()
    server._sessions.update(
        {
            "old_detached": {"transport": detached, "last_active": 100.0, "agent_ready": _ready()},
            "new_detached": {"transport": detached, "last_active": 300.0, "agent_ready": _ready()},
            "running_detached": {
                "transport": detached,
                "last_active": 50.0,
                "running": True,
                "agent_ready": _ready(),
            },
            "focused_live": {"transport": live, "last_active": 200.0, "agent_ready": _ready()},
        }
    )

    server._enforce_session_cap()

    # 4 sessions, cap 2 -> evict 2. Only detached+idle+built are eligible, oldest
    # first; the running one and the live-transport one are exempt.
    assert evicted == [
        ("old_detached", "lru_evict", False),
        ("new_detached", "lru_evict", False),
    ]


def test_enforce_session_cap_disabled_is_noop(server, monkeypatch):
    monkeypatch.setattr(server, "_load_cfg", lambda: {"max_live_sessions": 0})
    evicted: list[str] = []
    monkeypatch.setattr(
        server,
        "_close_session_by_id",
        lambda sid, end_reason=None, finalize=True: evicted.append(sid),
    )
    server._sessions.clear()
    server._sessions.update(
        {
            f"s{i}": {"transport": server._detached_ws_transport, "last_active": float(i)}
            for i in range(5)
        }
    )

    server._enforce_session_cap()

    assert evicted == []


def test_shutdown_sessions_uses_nonsemantic_teardown(server, monkeypatch):
    calls: list[tuple[str, str | None, bool]] = []
    server._sessions.clear()
    server._sessions["shutdown_sid"] = {"agent": None}
    monkeypatch.setattr(
        server,
        "_close_session_by_id",
        lambda sid, end_reason=None, finalize=True: calls.append(
            (sid, end_reason, finalize)
        ),
    )

    server._shutdown_sessions()

    assert calls == [("shutdown_sid", "tui_shutdown", False)]


def test_idle_reaper_uses_nonsemantic_teardown(server, monkeypatch):
    calls: list[tuple[str, str | None, bool]] = []
    server._sessions.clear()
    server._sessions["idle_sid"] = {"agent": None}
    monkeypatch.setattr(server, "_session_is_evictable", lambda *_args: True)
    monkeypatch.setattr(server, "_enforce_session_cap", lambda: None)
    monkeypatch.setattr(
        server,
        "_close_session_by_id",
        lambda sid, end_reason=None, finalize=True: calls.append(
            (sid, end_reason, finalize)
        ),
    )

    server._reap_idle_sessions()

    assert calls == [("idle_sid", "idle_timeout", False)]


def test_ws_disconnect_close_uses_nonsemantic_teardown(server, monkeypatch):
    calls: list[tuple[str, str | None, bool]] = []
    transport = object()
    server._sessions.clear()
    server._sessions["ws_sid"] = {
        "agent": None,
        "close_on_disconnect": True,
        "transport": transport,
    }
    monkeypatch.setattr(
        server,
        "_close_session_by_id",
        lambda sid, end_reason=None, finalize=True: calls.append(
            (sid, end_reason, finalize)
        ),
    )

    assert server._close_sessions_for_transport(transport) == (1, 0)

    assert calls == [("ws_sid", "ws_disconnect", False)]


def test_ws_orphan_reaper_uses_nonsemantic_teardown(server, monkeypatch):
    calls: list[tuple[str, str | None, bool]] = []
    server._sessions.clear()
    server._sessions["orphan_sid"] = {
        "agent": None,
        "running": False,
        "transport": server._detached_ws_transport,
    }
    monkeypatch.setattr(server, "_WS_ORPHAN_REAP_GRACE_S", 1.0)
    monkeypatch.setattr(
        server,
        "_close_session_by_id",
        lambda sid, end_reason=None, finalize=True: calls.append(
            (sid, end_reason, finalize)
        ),
    )

    class _ImmediateTimer:
        daemon = False

        def __init__(self, _delay, callback):
            self.callback = callback

        def start(self):
            self.callback()

    monkeypatch.setattr(server.threading, "Timer", _ImmediateTimer)

    server._schedule_ws_orphan_reap("orphan_sid")

    assert calls == [("orphan_sid", "ws_orphan_reap", False)]


def test_explicit_session_close_requests_semantic_teardown(server, monkeypatch):
    calls: list[tuple[str, str | None, bool]] = []
    monkeypatch.setattr(
        server,
        "_close_session_by_id",
        lambda sid, end_reason=None, finalize=True: calls.append(
            (sid, end_reason, finalize)
        )
        or True,
    )

    response = server.handle_request(
        {
            "id": "close",
            "method": "session.close",
            "params": {"session_id": "explicit_sid"},
        }
    )

    assert response["result"]["closed"] is True
    assert calls == [("explicit_sid", "tui_close", True)]


def _idle_close_session(**overrides):
    session = {
        "agent": object(),
        "history": [],
        "history_lock": threading.Lock(),
        "running": False,
        "session_key": "stored-session",
    }
    session.update(overrides)
    return session


def test_session_close_require_idle_claims_before_semantic_teardown(
    server, monkeypatch
):
    sid = "idle-semantic-close"
    session = _idle_close_session()
    server._sessions[sid] = session
    teardown = MagicMock()
    monkeypatch.setattr(server, "_teardown_session", teardown)

    response = server.handle_request(
        {
            "id": "close",
            "method": "session.close",
            "params": {"session_id": sid, "require_idle": True},
        }
    )

    assert response["result"]["closed"] is True
    assert sid not in server._sessions
    assert session["_close_claimed"] is True
    teardown.assert_called_once_with(
        session,
        end_reason="tui_close",
        finalize=True,
        checkpoint=True,
    )


def test_session_close_require_idle_rejects_running_runtime(server, monkeypatch):
    sid = "busy-semantic-close"
    session = _idle_close_session(running=True)
    server._sessions[sid] = session
    teardown = MagicMock()
    monkeypatch.setattr(server, "_teardown_session", teardown)

    response = server.handle_request(
        {
            "id": "close",
            "method": "session.close",
            "params": {"session_id": sid, "require_idle": True},
        }
    )

    assert response["error"]["code"] == 4023
    assert "current turn is still running" in response["error"]["message"]
    assert server._sessions[sid] is session
    assert "_close_claimed" not in session
    teardown.assert_not_called()


def test_session_close_default_preserves_existing_force_close_semantics(
    server, monkeypatch
):
    close = MagicMock(return_value=True)
    monkeypatch.setattr(server, "_close_session_by_id", close)

    response = server.handle_request(
        {
            "id": "close",
            "method": "session.close",
            "params": {"session_id": "legacy-caller"},
        }
    )

    assert response["result"]["closed"] is True
    close.assert_called_once_with("legacy-caller", end_reason="tui_close")


def test_session_close_require_idle_checks_pending_prompt_under_prompt_lock(
    server, monkeypatch
):
    sid = "prompting-semantic-close"
    session = _idle_close_session()
    server._sessions[sid] = session
    teardown = MagicMock()
    monkeypatch.setattr(server, "_teardown_session", teardown)
    pending_event = threading.Event()
    with server._prompt_lock:
        server._pending["approval-1"] = (sid, pending_event)
        server._pending_prompt_payloads["approval-1"] = ("secret.request", {})

    response = server.handle_request(
        {
            "id": "close",
            "method": "session.close",
            "params": {"session_id": sid, "require_idle": True},
        }
    )

    assert response["error"]["code"] == 4023
    assert "waiting for secret input" in response["error"]["message"]
    assert server._sessions[sid] is session
    teardown.assert_not_called()


def test_session_close_require_idle_check_is_atomic_with_running_transition(
    server, monkeypatch
):
    sid = "racing-semantic-close"
    history_lock = threading.Lock()
    session = _idle_close_session(history_lock=history_lock)
    server._sessions[sid] = session
    teardown = MagicMock()
    monkeypatch.setattr(server, "_teardown_session", teardown)
    response = {}

    history_lock.acquire()
    try:
        close_thread = threading.Thread(
            target=lambda: response.update(
                server.handle_request(
                    {
                        "id": "close",
                        "method": "session.close",
                        "params": {"session_id": sid, "require_idle": True},
                    }
                )
            )
        )
        close_thread.start()
        # The close is waiting to inspect the state under history_lock. A turn
        # transition that wins this lock must therefore be observed, not popped.
        time.sleep(0.02)
        session["running"] = True
    finally:
        history_lock.release()

    close_thread.join(timeout=1)
    assert not close_thread.is_alive()
    assert response["error"]["code"] == 4023
    assert server._sessions[sid] is session
    teardown.assert_not_called()


def test_session_close_require_idle_will_not_finalize_unbuilt_transcript(
    server, monkeypatch
):
    sid = "unbuilt-semantic-close"
    session = _idle_close_session(
        agent=None,
        history=[{"role": "user", "content": "remember this"}],
    )
    server._sessions[sid] = session
    teardown = MagicMock()
    monkeypatch.setattr(server, "_teardown_session", teardown)

    response = server.handle_request(
        {
            "id": "close",
            "method": "session.close",
            "params": {"session_id": sid, "require_idle": True},
        }
    )

    assert response["error"]["code"] == 4023
    assert "preparing its memory boundary" in response["error"]["message"]
    assert server._sessions[sid] is session
    teardown.assert_not_called()


def test_recovery_resume_require_unended_refuses_finalized_row(server, monkeypatch):
    class _DB:
        def get_session(self, session_id):
            assert session_id == "stored-ended"
            return {
                "cwd": None,
                "ended_at": 123.0,
                "id": session_id,
            }

        def get_session_by_title(self, _title):
            return None

        def resolve_resume_session_id(self, session_id):
            return session_id

    monkeypatch.setattr(server, "_get_db", lambda: _DB())

    response = server.handle_request(
        {
            "id": "resume",
            "method": "session.resume",
            "params": {
                "session_id": "stored-ended",
                "eager_build": True,
                "require_unended": True,
            },
        }
    )

    assert response["error"] == {
        "code": 4091,
        "message": "session already ended",
    }
    assert server._sessions == {}


def test_eager_recovery_resume_rechecks_ended_row_at_publication(
    server, monkeypatch
):
    state = {"ended": False, "reopened": False}

    class _DB:
        def get_session(self, session_id):
            assert session_id == "stored-racing"
            return {
                "cwd": "/tmp/project",
                "ended_at": 123.0 if state["ended"] else None,
                "id": session_id,
            }

        def get_session_by_title(self, _title):
            return None

        def get_messages_as_conversation(self, _session_id, **_kwargs):
            return []

        def reopen_session(self, _session_id):
            state["reopened"] = True

        def resolve_resume_session_id(self, session_id):
            return session_id

    class _Lease:
        released = 0

        def release(self):
            self.released += 1

    class _Agent:
        def __init__(self):
            self.closed = False
            self.memory_shutdown = []

        def close(self):
            self.closed = True

        def shutdown_memory_provider(self, history, *, finalize):
            self.memory_shutdown.append((history, finalize))

    db = _DB()
    lease = _Lease()
    agent = _Agent()
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(
        server,
        "_claim_active_session_slot",
        lambda *_args, **_kwargs: (lease, None),
    )
    monkeypatch.setattr(server, "_profile_configured_cwd", lambda _home: "/tmp/project")
    monkeypatch.setattr(server, "_stored_session_runtime_overrides", lambda _row: {})

    def _build_agent(*_args, **_kwargs):
        # Simulate another window completing semantic close while the eager
        # recovery agent is outside _session_resume_lock being constructed.
        state["ended"] = True
        return agent

    monkeypatch.setattr(server, "_make_agent", _build_agent)

    response = server.handle_request(
        {
            "id": "resume",
            "method": "session.resume",
            "params": {
                "session_id": "stored-racing",
                "eager_build": True,
                "require_unended": True,
            },
        }
    )

    assert response["error"] == {
        "code": 4091,
        "message": "session already ended",
    }
    assert state["reopened"] is False
    assert server._sessions == {}
    assert lease.released == 1
    assert agent.memory_shutdown == [([], False)]
    assert agent._end_session_on_close is False
    assert agent.closed is True


def test_deferred_recovery_claim_rechecks_ended_row_before_registration(server):
    class _DB:
        def get_session(self, session_id):
            assert session_id == "stored-ended-late"
            return {"ended_at": 321.0, "id": session_id}

    class _Lease:
        released = 0

        def release(self):
            self.released += 1

    lease = _Lease()
    record = {
        "history": [],
        "history_lock": threading.Lock(),
        "running": False,
        "session_key": "stored-ended-late",
    }

    with pytest.raises(server._ResumeSessionAlreadyEnded):
        server._claim_or_reuse_live(
            "runtime-loser",
            "stored-ended-late",
            record,
            lease,
            require_unended_db=_DB(),
        )

    assert lease.released == 1
    assert "runtime-loser" not in server._sessions


def test_session_release_is_nonsemantic_and_skips_checkpoint(server, monkeypatch):
    calls: list[tuple[str, str | None, bool, bool]] = []
    monkeypatch.setattr(
        server,
        "_close_session_by_id",
        lambda sid, end_reason=None, finalize=True, checkpoint=True: calls.append(
            (sid, end_reason, finalize, checkpoint)
        )
        or True,
    )

    response = server.handle_request(
        {
            "id": "release",
            "method": "session.release",
            "params": {"session_id": "switched-away"},
        }
    )

    assert response["result"]["released"] is True
    assert calls == [("switched-away", "tui_release", False, False)]


def test_session_release_rejects_a_busy_runtime(server, monkeypatch):
    sid = "busy-release"
    server._sessions[sid] = {"running": True}
    close = MagicMock()
    monkeypatch.setattr(server, "_close_session_by_id", close)

    response = server.handle_request(
        {"id": "release", "method": "session.release", "params": {"session_id": sid}}
    )

    assert response["error"]["code"] == 4023
    close.assert_not_called()


def test_session_delete_reconciles_full_lineage_before_db_delete(
    server, monkeypatch, tmp_path
):
    """Privacy delete tombstones roots, tips, and delegates before SessionDB."""
    from altas.cortex.models import EvidenceInput
    from altas.cortex.store import CortexStore

    root_id = "delete-root"
    tip_id = "delete-tip"
    delegate_id = "delete-delegate"
    branch_id = "independent-branch"
    delete_ids = [root_id, tip_id, delegate_id]
    row_ids = {root_id: 101, tip_id: 202, delegate_id: 303, branch_id: 404}
    store = CortexStore(tmp_path / "cortex.db", owner_customer_id="customer-1")
    store.initialize()
    store.ensure_session(root_id)
    store.ensure_session(
        tip_id,
        parent_session_id=root_id,
        logical_conversation_id=root_id,
    )
    store.ensure_session(delegate_id, parent_session_id=root_id)
    store.ensure_session(branch_id, parent_session_id=root_id)
    for session_id, row_id in row_ids.items():
        store.append_evidence(
            session_id,
            EvidenceInput(
                source_type="user_message",
                content=f"evidence for {session_id}",
                source_locator=f"{session_id}:row:{row_id}:user",
                metadata={"source_row_id": row_id},
            ),
        )

    events: list[str] = []

    class _DB:
        def resolve_session_id(self, session_id):
            return session_id if session_id == root_id else None

        def resolve_resume_session_id(self, _session_id):
            return tip_id

        def get_session_delete_closure(self, session_ids):
            assert session_ids == [root_id]
            return list(delete_ids)

        def get_messages(self, session_id, *, include_inactive=False):
            assert include_inactive is True
            events.append(f"rows:{session_id}")
            return [{"id": row_ids[session_id]}]

        def get_session(self, _session_id):
            return {"source": "tui"}

        def delete_sessions(self, session_ids, *, sessions_dir=None, **kwargs):
            assert list(session_ids) == delete_ids
            assert sessions_dir == tmp_path / "sessions"
            kwargs["before_delete"](tuple(session_ids))
            assert all(store.session_lineage(sid)["state"] == "deleted" for sid in delete_ids)
            assert store.session_lineage(branch_id)["state"] == "active"
            with store.connect() as connection:
                live = connection.execute(
                    "SELECT session_id FROM evidence_items WHERE brain_id=? "
                    "AND tombstoned_at IS NULL",
                    (store.brain_id,),
                ).fetchall()
            assert {row["session_id"] for row in live} == {branch_id}
            events.append("sessiondb-delete")
            return len(delete_ids)

    db = _DB()
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_cortex_store_for_session_delete", lambda *_a, **_k: store)
    monkeypatch.setattr(server, "get_hermes_home", lambda: tmp_path)

    response = server.handle_request(
        {
            "id": "privacy-delete",
            "method": "session.delete",
            "params": {"session_id": root_id},
        }
    )

    assert "error" not in response, response
    assert response["result"]["deleted_session_ids"] == delete_ids
    assert events == ["sessiondb-delete"]
    with store.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM session_distill_admissions WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM cognitive_jobs WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()[0] == 0


def test_active_session_delete_releases_without_finalize_or_checkpoint(
    server, monkeypatch, tmp_path
):
    from hermes_cli.active_sessions import try_acquire_active_session

    target = "active-delete"
    events: list[str] = []

    class _DB:
        def resolve_session_id(self, session_id):
            return session_id

        def resolve_resume_session_id(self, session_id):
            return session_id

        def get_session_delete_closure(self, _session_ids):
            return [target]

        def get_session(self, _session_id):
            return {"source": "tui"}

        def delete_sessions(self, session_ids, *, sessions_dir=None, **kwargs):
            assert session_ids == [target]
            kwargs["before_delete"](tuple(session_ids))
            events.append("sessiondb-delete")
            return 1

    manager = MagicMock()
    agent = types.SimpleNamespace(
        session_id=target,
        _session_messages=None,
        _memory_manager=manager,
        _persist_session=MagicMock(),
        shutdown_memory_provider=MagicMock(),
        close=MagicMock(side_effect=lambda: events.append("runtime-close")),
    )
    sid = "active-runtime"
    lease, message = try_acquire_active_session(
        session_id=target,
        surface="tui",
        config={},
        hermes_home=tmp_path,
    )
    assert message is None
    assert lease is not None
    server._sessions[sid] = {
        "agent": agent,
        "history": [{"role": "user", "content": "private"}],
        "history_lock": threading.Lock(),
        "running": False,
        "session_key": target,
        "profile_home": str(tmp_path),
        "source": "tui",
        "active_session_lease": lease,
    }
    db = _DB()
    reconcile = MagicMock(return_value={"evidence": 1, "sessions": 1})
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_session_db", lambda _session: server.contextlib.nullcontext(db))
    monkeypatch.setattr(server, "_reconcile_cortex_session_delete", reconcile)

    response = server.handle_request(
        {
            "id": "privacy-delete",
            "method": "session.delete",
            "params": {"session_id": target, "runtime_session_id": sid},
        }
    )

    assert "error" not in response, response
    assert sid not in server._sessions
    assert events == ["sessiondb-delete", "runtime-close"]
    reconcile.assert_called_once()
    assert reconcile.call_args.args == (db, [target])
    assert reconcile.call_args.kwargs["session"]["agent"] is agent
    assert reconcile.call_args.kwargs["hermes_home"] == tmp_path
    manager.on_session_finalize.assert_not_called()
    manager.on_session_end.assert_not_called()
    agent._persist_session.assert_not_called()
    agent.shutdown_memory_provider.assert_called_once_with(
        [],
        finalize=False,
        reason="privacy_delete",
    )
    assert lease.released is True


def test_session_delete_refuses_another_process_lease(
    server, monkeypatch, tmp_path
):
    from hermes_cli.active_sessions import try_acquire_active_session

    target = "other-process-active"

    class _DB:
        def resolve_session_id(self, session_id):
            return session_id

        def get_session_delete_closure(self, _session_ids):
            return [target]

        def get_session(self, _session_id):
            return {"id": target, "source": "cli"}

        def delete_sessions(self, *_args, **_kwargs):
            raise AssertionError("cross-process active session must survive")

    lease, message = try_acquire_active_session(
        session_id=target,
        surface="cli",
        config={},
        hermes_home=tmp_path,
    )
    assert message is None
    assert lease is not None
    monkeypatch.setattr(server, "_get_db", lambda: _DB())
    monkeypatch.setattr(server, "get_hermes_home", lambda: tmp_path)
    try:
        response = server.handle_request(
            {
                "id": "privacy-delete",
                "method": "session.delete",
                "params": {"session_id": target},
            }
        )
    finally:
        lease.release()

    assert response["error"]["code"] == 4023


def test_inactive_cross_profile_delete_uses_selected_profile_home(
    server, monkeypatch, tmp_path
):
    target = "cross-profile-delete"
    profile_home = tmp_path / "customer-profile"
    observed = {}

    class _DB:
        def resolve_session_id(self, session_id):
            return session_id

        def get_session_delete_closure(self, _session_ids):
            return [target]

        def get_session(self, _session_id):
            return {"id": target, "ended_at": 1}

        def delete_sessions(self, session_ids, **kwargs):
            kwargs["before_delete"](tuple(session_ids))
            return len(session_ids)

    db = _DB()
    monkeypatch.setattr(server, "_profile_home", lambda profile: profile_home)

    def session_db(session):
        observed["db_home"] = session.get("profile_home")
        return server.contextlib.nullcontext(db)

    def reconcile(*_args, **kwargs):
        observed["cortex_home"] = kwargs["hermes_home"]
        return {"evidence": 0, "sessions": 1}

    monkeypatch.setattr(server, "_session_db", session_db)
    monkeypatch.setattr(server, "_reconcile_cortex_session_delete", reconcile)

    response = server.handle_request(
        {
            "id": "privacy-delete",
            "method": "session.delete",
            "params": {"session_id": target, "profile": "customer"},
        }
    )

    assert "error" not in response, response
    assert observed["db_home"] == str(profile_home)
    assert observed["cortex_home"] == profile_home
    assert (profile_home / "runtime" / "deleted_sessions.json").exists()


def test_session_delete_failure_restores_claimed_runtime(server, monkeypatch, tmp_path):
    target = "retry-delete"
    sid = "retry-runtime"
    session = {
        "agent": types.SimpleNamespace(session_id=target),
        "history": [],
        "history_lock": threading.Lock(),
        "running": False,
        "session_key": target,
        "profile_home": str(tmp_path),
    }
    server._sessions[sid] = session
    db = MagicMock()
    db.resolve_session_id.return_value = target
    db.resolve_resume_session_id.return_value = target
    db.get_session_delete_closure.return_value = [target]
    db.delete_sessions.side_effect = lambda session_ids, **kwargs: kwargs[
        "before_delete"
    ](tuple(session_ids))
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_session_db", lambda _session: server.contextlib.nullcontext(db))
    monkeypatch.setattr(
        server,
        "_reconcile_cortex_session_delete",
        MagicMock(side_effect=OSError("cortex locked")),
    )

    response = server.handle_request(
        {
            "id": "privacy-delete",
            "method": "session.delete",
            "params": {"session_id": target, "runtime_session_id": sid},
        }
    )

    assert response["error"]["code"] == 5036
    assert server._sessions[sid] is session
    db.delete_sessions.assert_called_once()


def test_prompt_claim_wins_atomically_over_privacy_delete(
    server, monkeypatch, tmp_path
):
    """A turn claimed first stays live; delete must observe it as busy."""
    target = "prompt-delete-race"
    sid = "prompt-delete-runtime"
    entered_claim = threading.Event()
    release_claim = threading.Event()
    release_run = threading.Event()
    session = {
        "agent": None,
        "agent_ready": threading.Event(),
        "history": [],
        "history_lock": threading.Lock(),
        "running": False,
        "session_key": target,
        "profile_home": str(tmp_path),
        "transport": server._stdio_transport,
    }
    server._sessions[sid] = session

    class _DB:
        def resolve_session_id(self, session_id):
            return session_id

        def resolve_resume_session_id(self, session_id):
            return session_id

        def get_session_delete_closure(self, _session_ids):
            return [target]

    db = _DB()
    monkeypatch.setattr(
        server, "_session_db", lambda _session: server.contextlib.nullcontext(db)
    )
    monkeypatch.setattr(server, "_ensure_session_db_row", lambda _session: None)
    monkeypatch.setattr(server, "_persist_branch_seed", lambda _session: None)
    monkeypatch.setattr(server, "_start_agent_build", lambda *_args: None)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)

    def _claim(_session, _text):
        entered_claim.set()
        assert release_claim.wait(2)

    def _wait_agent(_session, _rid):
        assert release_run.wait(2)
        return server._err("prompt", 5000, "test turn stopped")

    monkeypatch.setattr(server, "_start_inflight_turn", _claim)
    monkeypatch.setattr(server, "_wait_agent", _wait_agent)

    responses: dict[str, dict] = {}
    prompt_thread = threading.Thread(
        target=lambda: responses.setdefault(
            "prompt",
            server.handle_request(
                {
                    "id": "prompt",
                    "method": "prompt.submit",
                    "params": {"session_id": sid, "text": "keep this turn"},
                }
            ),
        )
    )
    delete_thread = threading.Thread(
        target=lambda: responses.setdefault(
            "delete",
            server.handle_request(
                {
                    "id": "delete",
                    "method": "session.delete",
                    "params": {
                        "session_id": target,
                        "runtime_session_id": sid,
                    },
                }
            ),
        )
    )
    prompt_thread.start()
    assert entered_claim.wait(2)
    delete_thread.start()
    release_claim.set()
    prompt_thread.join(2)
    delete_thread.join(2)
    release_run.set()

    assert not prompt_thread.is_alive()
    assert not delete_thread.is_alive()
    assert responses["prompt"]["result"]["status"] == "streaming"
    assert responses["delete"]["error"]["code"] == 4023
    assert server._sessions[sid] is session


def test_privacy_delete_claim_prevents_late_prompt_resurrection(
    server, monkeypatch, tmp_path
):
    """Once deletion pops a runtime, a concurrent prompt sees not-found."""
    target = "delete-prompt-race"
    sid = "delete-prompt-runtime"
    entered_reconcile = threading.Event()
    release_reconcile = threading.Event()
    session = {
        "agent": None,
        "history": [],
        "history_lock": threading.Lock(),
        "running": False,
        "session_key": target,
        "profile_home": str(tmp_path),
        "transport": server._stdio_transport,
    }
    server._sessions[sid] = session

    class _DB:
        def resolve_session_id(self, session_id):
            return session_id

        def resolve_resume_session_id(self, session_id):
            return session_id

        def get_session_delete_closure(self, _session_ids):
            return [target]

        def delete_sessions(self, session_ids, *, sessions_dir=None, **kwargs):
            assert session_ids == [target]
            kwargs["before_delete"](tuple(session_ids))
            return 1

    db = _DB()
    monkeypatch.setattr(
        server, "_session_db", lambda _session: server.contextlib.nullcontext(db)
    )

    def _reconcile(*_args, **_kwargs):
        entered_reconcile.set()
        assert release_reconcile.wait(2)
        return {"evidence": 0, "sessions": 1}

    monkeypatch.setattr(server, "_reconcile_cortex_session_delete", _reconcile)
    responses: dict[str, dict] = {}
    delete_thread = threading.Thread(
        target=lambda: responses.setdefault(
            "delete",
            server.handle_request(
                {
                    "id": "delete",
                    "method": "session.delete",
                    "params": {
                        "session_id": target,
                        "runtime_session_id": sid,
                    },
                }
            ),
        )
    )
    delete_thread.start()
    assert entered_reconcile.wait(2)

    prompt = server.handle_request(
        {
            "id": "prompt",
            "method": "prompt.submit",
            "params": {"session_id": sid, "text": "too late"},
        }
    )
    release_reconcile.set()
    delete_thread.join(2)

    assert prompt["error"]["code"] == 4001
    assert not delete_thread.is_alive()
    assert responses["delete"]["result"]["deleted"] == target
    assert sid not in server._sessions


def test_session_resume_handles_multimodal_list_content(server, monkeypatch):
    """A user message persisted with list-shaped multimodal content used to
    crash session resume with ``'list' object has no attribute 'strip'``."""

    multimodal_user = {
        "role": "user",
        "content": [
            {"type": "text", "text": "describe this"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,AAAA"},
            },
        ],
    }
    text_only_assistant = {"role": "assistant", "content": "ok"}

    class _DB:
        def get_session(self, _sid):
            return {"id": "20260502_000000_listcontent"}

        def get_session_by_title(self, _title):
            return None

        def reopen_session(self, _sid):
            return None

        def get_messages_as_conversation(self, _sid, include_ancestors=False):
            return [multimodal_user, text_only_assistant]

    monkeypatch.setattr(server, "_get_db", lambda: _DB())
    monkeypatch.setattr(server, "_make_agent", lambda sid, key, session_id=None, session_db=None, **_kwargs: object())
    monkeypatch.setattr(server, "_init_session", lambda sid, key, agent, history, cols=80, **_kwargs: None)
    monkeypatch.setattr(server, "_session_info", lambda _agent, _session=None: {"model": "test/model"})

    resp = server.handle_request(
        {
            "id": "r1",
            "method": "session.resume",
            "params": {"session_id": "20260502_000000_listcontent", "cols": 100, "eager_build": True},
        }
    )

    assert "error" not in resp
    assert resp["result"]["message_count"] == 2
    # The image_url part is preserved as a raw data URL inside the text so
    # the desktop renderer (which extracts embedded images) sees the same
    # content the optimistic local cache returns. Otherwise the inline
    # image flashes during initial cache hydration and then vanishes when
    # the resume payload overwrites it with cleaned text.
    assert resp["result"]["messages"] == [
        {
            "role": "user",
            "text": "describe this\ndata:image/png;base64,AAAA",
        },
        {"role": "assistant", "text": "ok"},
    ]


def test_session_resume_lazy_registers_watch_session_without_agent(server, monkeypatch):
    """``lazy: true`` (subagent watch windows) must register the live session
    — keyed for the child mirror, on this transport — WITHOUT building an
    agent. The eager build is what made opening a subagent window contend
    with the already-running parent turn."""

    target = "20260612_000000_child99"

    class _DB:
        def get_session(self, _sid):
            return {"id": target}

        def get_session_by_title(self, _title):
            return None

        def reopen_session(self, _sid):
            return None

        def get_messages_as_conversation(self, _sid, include_ancestors=False):
            return [
                {"role": "user", "content": "delegated goal"},
            ]

    def _boom(*_args, **_kwargs):
        raise AssertionError("lazy resume must not build an agent")

    monkeypatch.setattr(server, "_get_db", lambda: _DB())
    monkeypatch.setattr(server, "_make_agent", _boom)

    resp = server.handle_request(
        {
            "id": "r1",
            "method": "session.resume",
            "params": {"session_id": target, "cols": 100, "lazy": True},
        }
    )

    assert "error" not in resp
    result = resp["result"]
    assert result["resumed"] == target
    assert result["session_key"] == target
    assert result["info"]["lazy"] is True
    assert result["info"]["desktop_contract"] == server.DESKTOP_BACKEND_CONTRACT
    assert result["messages"] == [{"role": "user", "text": "delegated goal"}]

    sid = result["session_id"]
    session = server._sessions[sid]
    assert session["agent"] is None
    # The child mirror finds the watch window by stored key.
    assert server._find_live_session_by_key(target) == (sid, session)
    # A later prompt.submit upgrade must continue THIS stored conversation.
    assert session["resume_session_id"] == target
    # No build started: the idle reaper must still be able to evict it, and
    # the live status must not report a never-ending "starting".
    assert not session["agent_ready"].is_set()
    assert server._session_live_status(sid, session) != "starting"
    session["transport"] = server._detached_ws_transport
    far_future = time.time() + 999999
    assert server._session_is_evictable(sid, session, far_future)

    # Resuming again (window refresh) reuses the same live session.
    resp2 = server.handle_request(
        {
            "id": "r2",
            "method": "session.resume",
            "params": {"session_id": target, "cols": 100, "lazy": True},
        }
    )
    assert "error" not in resp2
    assert resp2["result"]["session_id"] == sid
    assert len(server._sessions) == 1


def test_session_resume_lazy_reports_running_for_inflight_child(server, monkeypatch):
    """A watch window attaching to a child mid-delegation must learn the run is
    live from the resume response itself — the child can sit silent inside a
    long tool call, so waiting for the next stream event leaves the window
    looking dead."""

    target = "20260612_000000_child42"

    class _DB:
        def get_session(self, _sid):
            return {"id": target}

        def get_session_by_title(self, _title):
            return None

        def reopen_session(self, _sid):
            return None

        def get_messages_as_conversation(self, _sid, include_ancestors=False):
            return [{"role": "user", "content": "delegated goal"}]

    monkeypatch.setattr(server, "_get_db", lambda: _DB())
    monkeypatch.setattr(
        server, "_make_agent", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no build"))
    )
    server._active_child_runs[target] = time.time()
    try:
        resp = server.handle_request(
            {
                "id": "r1",
                "method": "session.resume",
                "params": {"session_id": target, "cols": 100, "lazy": True},
            }
        )
    finally:
        server._active_child_runs.pop(target, None)

    assert "error" not in resp
    assert resp["result"]["running"] is True
    assert resp["result"]["status"] == "streaming"


def test_session_resume_lazy_tolerates_missing_row_for_active_child(server, monkeypatch):
    """Race regression: a watch window opens on a freshly-spawned subagent and
    resumes BEFORE the child's first run_conversation() flushes its DB row.

    The child relays ``subagent.start`` (carrying child_session_id, which opens
    the window) before ``_ensure_db_session`` writes the row, so
    ``db.get_session(target)`` is momentarily empty. On slower hosts (WSL2) the
    window's lazy resume consistently lands in this gap. It used to hard-fail
    "session not found"; the frontend then 404'd on its REST messages fallback
    and the watch window spun forever. Since the child is provably live
    (``_child_run_active``), the lazy resume must instead register the live
    session with empty history so the mirror can stream the turn.
    """

    target = "20260616_131212_racey"

    class _DB:
        def get_session(self, _sid):
            # Row not flushed yet — the whole point of the race.
            return None

        def get_session_by_title(self, _title):
            return None

        def reopen_session(self, _sid):
            return None

        def get_messages_as_conversation(self, _sid, include_ancestors=False):
            # No rows for an unwritten session.
            return []

    monkeypatch.setattr(server, "_get_db", lambda: _DB())
    monkeypatch.setattr(
        server, "_make_agent", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no build"))
    )
    # Child is live in the relay registry even though its row isn't written.
    server._active_child_runs[target] = time.time()
    try:
        resp = server.handle_request(
            {
                "id": "r1",
                "method": "session.resume",
                "params": {"session_id": target, "cols": 100, "lazy": True},
            }
        )
    finally:
        server._active_child_runs.pop(target, None)

    # The resume must succeed (no "session not found") and register a live,
    # agent-less watch session the mirror can find by stored key.
    assert "error" not in resp
    result = resp["result"]
    assert result["resumed"] == target
    assert result["session_key"] == target
    assert result["info"]["lazy"] is True
    assert result["messages"] == []
    # Live for the mirror; reported running so the window shows a busy state.
    assert result["running"] is True
    assert result["status"] == "streaming"
    sid = result["session_id"]
    assert server._find_live_session_by_key(target) == (sid, server._sessions[sid])
    assert server._sessions[sid]["agent"] is None


def test_session_resume_missing_row_non_lazy_still_errors(server, monkeypatch):
    """The missing-row tolerance is scoped to lazy resumes of an ACTIVE child.
    A normal (non-lazy) resume of a genuinely unknown id must still fail fast
    with "session not found" rather than silently registering an empty session.
    """

    target = "20260616_000000_ghost"

    class _DB:
        def get_session(self, _sid):
            return None

        def get_session_by_title(self, _title):
            return None

    monkeypatch.setattr(server, "_get_db", lambda: _DB())

    # Non-lazy resume, no active child → hard error.
    resp = server.handle_request(
        {
            "id": "r1",
            "method": "session.resume",
            "params": {"session_id": target, "cols": 100},
        }
    )
    assert "error" in resp
    assert "session not found" in resp["error"]["message"].lower()

    # Lazy resume but the child is NOT live → still an error (no live mirror to
    # justify an empty session; this would just be a dead, sessionless window).
    resp2 = server.handle_request(
        {
            "id": "r2",
            "method": "session.resume",
            "params": {"session_id": target, "cols": 100, "lazy": True},
        }
    )
    assert "error" in resp2
    assert "session not found" in resp2["error"]["message"].lower()


def test_session_resume_reuses_existing_live_session(server, monkeypatch):
    """Repeated resume must not allocate duplicate live agents."""

    target = "20260409_010101_abc123"
    created_sids: list[str] = []
    closed_sids: list[str] = []
    shutdown_sids: list[str] = []
    close_end_session_flags: list[bool] = []
    first_agent_started = threading.Event()
    agent_can_finish = threading.Event()

    class _DB:
        def get_session(self, _sid):
            return {"id": target}

        def get_session_by_title(self, _title):
            return None

        def reopen_session(self, _sid):
            return None

        def get_messages_as_conversation(self, _sid, include_ancestors=False):
            return [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "yo"},
            ]

    class _Worker:
        def close(self):
            pass

    class _Agent:
        def __init__(self, sid, session_id):
            self.sid = sid
            self.model = "test/model"
            self.session_id = session_id
            self._end_session_on_close = True

        def shutdown_memory_provider(self, messages, *, finalize=False):
            assert messages == []
            assert finalize is False
            shutdown_sids.append(self.sid)

        def close(self):
            close_end_session_flags.append(self._end_session_on_close)
            closed_sids.append(self.sid)

    def make_agent(sid, key, session_id=None, session_db=None, **_kwargs):
        created_sids.append(sid)
        first_agent_started.set()
        assert agent_can_finish.wait(timeout=1)
        return _Agent(sid, session_id or key)

    monkeypatch.setattr(server, "_get_db", lambda: _DB())
    monkeypatch.setattr(server, "_make_agent", make_agent)
    monkeypatch.setattr(server, "_SlashWorker", lambda _key, _model: _Worker())
    monkeypatch.setattr(
        server,
        "_start_notification_poller",
        lambda _sid, _session: threading.Event(),
    )
    monkeypatch.setattr(server, "_notify_session_boundary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_wire_callbacks", lambda _sid: None)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        server,
        "_session_info",
        lambda _agent, _session=None: {"model": "test/model"},
    )

    fake_approval = types.SimpleNamespace(
        load_permanent_allowlist=lambda: None,
        register_gateway_notify=lambda *_args, **_kwargs: None,
    )

    with patch.dict(sys.modules, {"tools.approval": fake_approval}):
        first_holder = {}

        def resume_first():
            first_holder["resp"] = server.handle_request(
                {
                    "id": "first",
                    "method": "session.resume",
                    # eager_build: this test drives the synchronous build race +
                    # double-checked locking that only the eager path exercises.
                    "params": {"session_id": target, "cols": 100, "eager_build": True},
                }
            )

        first_thread = threading.Thread(target=resume_first)
        first_thread.start()
        assert first_agent_started.wait(timeout=1)

        second_holder = {}

        def resume_second():
            second_holder["resp"] = server.handle_request(
                {
                    "id": "second",
                    "method": "session.resume",
                    "params": {"session_id": target, "cols": 120, "eager_build": True},
                }
            )

        second_thread = threading.Thread(target=resume_second)
        second_thread.start()
        agent_can_finish.set()

        first_thread.join(timeout=1)
        second_thread.join(timeout=1)
        assert not first_thread.is_alive()
        assert not second_thread.is_alive()
        first = first_holder["resp"]
        second = second_holder["resp"]

    assert "error" not in first
    assert "error" not in second
    # Both resumes resolve to the SAME single live session — the core invariant.
    assert second["result"]["session_id"] == first["result"]["session_id"]
    assert len(server._sessions) == 1
    assert [s.get("session_key") for s in server._sessions.values()].count(target) == 1
    winner = first["result"]["session_id"]
    # The agent build happens outside the resume lock, so a racing resume may
    # build a redundant agent; double-checked locking keeps only one live
    # session and closes any loser's agent (no worker/poller is wired for it).
    assert winner in created_sids
    survivors = [sid for sid in created_sids if sid not in closed_sids]
    assert survivors == [winner]
    assert shutdown_sids == closed_sids
    assert close_end_session_flags == [False]
    assert all(sid == winner for sid in server._sessions)


def test_session_resume_reuses_live_agent_after_compression_rotation(server, monkeypatch):
    """Resume must match the live agent's current session_id, not stale session_key."""

    target = "20260409_020202_child"
    stale_parent = "20260409_010101_parent"
    sid = "live-rotated"
    server._sessions[sid] = {
        "agent": types.SimpleNamespace(model="test/model", session_id=target),
        "created_at": 123.0,
        "display_history_prefix": [],
        "history": [{"role": "assistant", "content": "live child"}],
        "history_lock": threading.RLock(),
        "last_active": 123.0,
        "running": False,
        "session_key": stale_parent,
        "transport": server._stdio_transport,
    }

    class _DB:
        def get_session(self, _sid):
            return {"id": target}

        def get_session_by_title(self, _title):
            return None

        def resolve_resume_session_id(self, _target):
            return target

    monkeypatch.setattr(server, "_get_db", lambda: _DB())
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        server,
        "_session_info",
        lambda _agent, _session=None: {"model": "test/model"},
    )

    result = server.handle_request(
        {
            "id": "r1",
            "method": "session.resume",
            "params": {"session_id": target, "cols": 100},
        }
    )

    assert "error" not in result
    assert result["result"]["session_id"] == sid
    assert result["result"]["session_key"] == target
    assert len(server._sessions) == 1


def test_sync_session_key_after_compress_reanchors_active_session_lease(
    server, monkeypatch, tmp_path
):
    home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(home))

    from hermes_cli.active_sessions import (
        active_session_registry_snapshot,
        try_acquire_active_session,
    )

    lease, message = try_acquire_active_session(
        session_id="session-old",
        surface="tui",
        config={"max_concurrent_sessions": 1},
        metadata={"live_session_id": "ui-1"},
        hermes_home=home,
    )
    assert message is None
    assert lease is not None

    session = {
        "active_session_lease": lease,
        "agent": types.SimpleNamespace(session_id="session-new"),
        "session_key": "session-old",
    }
    fake_approval = types.SimpleNamespace(
        disable_session_yolo=lambda *_args, **_kwargs: None,
        enable_session_yolo=lambda *_args, **_kwargs: None,
        is_session_yolo_enabled=lambda *_args, **_kwargs: False,
        register_gateway_notify=lambda *_args, **_kwargs: None,
        unregister_gateway_notify=lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(server, "_restart_slash_worker", lambda *_args, **_kwargs: None)

    with patch.dict(sys.modules, {"tools.approval": fake_approval}):
        server._sync_session_key_after_compress("ui-1", session)

    snapshot = active_session_registry_snapshot(home)
    assert session["session_key"] == "session-new"
    assert lease.session_id == "session-new"
    assert [entry["session_id"] for entry in snapshot] == ["session-new"]
    lease.release()


def test_session_resume_live_payload_uses_current_history_with_ancestors(server, monkeypatch):
    """Live resume should not reuse a stale ancestor-inclusive snapshot."""

    target = "20260409_010101_child"
    ancestor_history = [{"role": "user", "content": "ancestor"}]
    current_history = [
        {"role": "user", "content": "current"},
        {"role": "assistant", "content": "current reply"},
    ]

    class _DB:
        def get_session(self, _sid):
            return {"id": target}

        def get_session_by_title(self, _title):
            return None

        def reopen_session(self, _sid):
            return None

        def get_messages_as_conversation(self, _sid, include_ancestors=False):
            if include_ancestors:
                return ancestor_history + current_history
            return list(current_history)

    class _Worker:
        def close(self):
            pass

    monkeypatch.setattr(server, "_get_db", lambda: _DB())
    monkeypatch.setattr(
        server,
        "_make_agent",
        lambda _sid, key, session_id=None, session_db=None, **_kwargs: types.SimpleNamespace(
            model="test/model", session_id=session_id or key
        ),
    )
    monkeypatch.setattr(server, "_SlashWorker", lambda _key, _model: _Worker())
    monkeypatch.setattr(
        server,
        "_start_notification_poller",
        lambda _sid, _session: threading.Event(),
    )
    monkeypatch.setattr(server, "_notify_session_boundary", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(server, "_wire_callbacks", lambda _sid: None)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        server,
        "_session_info",
        lambda _agent, _session=None: {"model": "test/model"},
    )

    fake_approval = types.SimpleNamespace(
        load_permanent_allowlist=lambda: None,
        register_gateway_notify=lambda *_args, **_kwargs: None,
    )

    with patch.dict(sys.modules, {"tools.approval": fake_approval}):
        first = server.handle_request(
            {
                "id": "first",
                "method": "session.resume",
                "params": {"session_id": target, "cols": 100},
            }
        )

        assert "error" not in first
        sid = first["result"]["session_id"]
        assert first["result"]["messages"] == [
            {"role": "user", "text": "ancestor"},
            {"role": "user", "text": "current"},
            {"role": "assistant", "text": "current reply"},
        ]

        with server._sessions[sid]["history_lock"]:
            server._sessions[sid]["history"] = current_history + [
                {"role": "user", "content": "new live turn"},
                {"role": "assistant", "content": "new live reply"},
            ]

        second = server.handle_request(
            {
                "id": "second",
                "method": "session.resume",
                "params": {"session_id": target, "cols": 120},
            }
        )

    assert "error" not in second
    assert second["result"]["session_id"] == sid
    assert second["result"]["messages"] == [
        {"role": "user", "text": "ancestor"},
        {"role": "user", "text": "current"},
        {"role": "assistant", "text": "current reply"},
        {"role": "user", "text": "new live turn"},
        {"role": "assistant", "text": "new live reply"},
    ]


def test_session_activate_rebinds_orphaned_ws_session_to_current_transport(server, monkeypatch):
    """Reconnect + activate must reattach a parked live session before orphan reap."""

    class _Transport:
        def write(self, _obj):
            return True

    sid = "runtime01"
    old_transport = server._stdio_transport
    new_transport = _Transport()
    server._sessions[sid] = {
        "agent": types.SimpleNamespace(model="test/model"),
        "created_at": 123.0,
        "history": [],
        "history_lock": threading.RLock(),
        "last_active": 123.0,
        "running": False,
        "session_key": "20260409_010101_abc123",
        "transport": old_transport,
    }
    monkeypatch.setattr(server, "current_transport", lambda: new_transport)
    monkeypatch.setattr(server, "_get_db", lambda: None)
    monkeypatch.setattr(
        server,
        "_session_info",
        lambda _agent, _session=None: {"model": "test/model"},
    )

    resp = server.handle_request(
        {"id": "activate", "method": "session.activate", "params": {"session_id": sid}}
    )

    assert "error" not in resp
    assert resp["result"]["session_id"] == sid
    assert server._sessions[sid]["transport"] is new_transport
    assert not server._ws_session_is_orphaned(server._sessions[sid])


def test_session_branch_persists_branched_from_marker(server, monkeypatch):
    """TUI /branch must persist a _branched_from marker so the branch stays
    visible in /resume and /sessions.

    Regression for issue #20856: the TUI branch leaves the parent live (it
    never ends it with end_reason='branched'), so list_sessions_rich's legacy
    heuristic never surfaces it — the stable model_config marker is the only
    thing that keeps a TUI branch visible.
    """
    create_calls = []
    make_agent_calls = []

    class _DB:
        def get_session_title(self, _key):
            return "parent-title"

        def get_next_title_in_lineage(self, base):
            return f"{base} 2"

        def create_session(self, new_key, **kwargs):
            create_calls.append((new_key, kwargs))
            return new_key

        def append_message(self, **_kwargs):
            return None

        def set_session_title(self, _key, _title):
            return None

    monkeypatch.setattr(server, "_get_db", lambda: _DB())
    monkeypatch.setattr(server, "_resolve_model", lambda: "test/model")
    monkeypatch.setattr(server, "_new_session_key", lambda: "20260101_000001_child0")
    def _make_agent(_sid, key, session_id=None, session_db=None, **kwargs):
        make_agent_calls.append(
            {
                "key": key,
                "session_id": session_id,
                "session_db": session_db,
                **kwargs,
            }
        )
        return types.SimpleNamespace(model="test/model", session_id=session_id or key)

    monkeypatch.setattr(server, "_make_agent", _make_agent)
    monkeypatch.setattr(server, "_init_session", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_set_session_context", lambda *_a, **_k: [])
    monkeypatch.setattr(server, "_clear_session_context", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_session_cwd", lambda _s: "/tmp/branch-cwd")

    parent_sid = "parent01"
    parent_key = "20260101_000000_parent"
    source_manager = MagicMock()
    source_agent = types.SimpleNamespace(
        model="test/model",
        session_id=parent_key,
        _memory_manager=source_manager,
    )
    server._sessions[parent_sid] = {
        "agent": source_agent,
        "session_key": parent_key,
        "history": [{"role": "user", "content": "hello"}],
        "history_lock": threading.Lock(),
        "cols": 80,
    }

    resp = server.handle_request(
        {"id": "b1", "method": "session.branch", "params": {"session_id": parent_sid}}
    )

    assert "error" not in resp, resp
    assert len(create_calls) == 1
    new_key, kwargs = create_calls[0]
    assert new_key == "20260101_000001_child0"
    assert kwargs["parent_session_id"] == parent_key
    # The marker — without it the branch is invisible in /resume and /sessions.
    assert kwargs["model_config"] == {"_branched_from": parent_key}
    # The child provider bootstraps on the parent and is switched before the
    # branch is published, preserving Cortex lineage without ending the source.
    assert make_agent_calls == [
        {
            "key": "20260101_000001_child0",
            "session_id": parent_key,
            "session_db": None,
            "parent_session_id": parent_key,
            "platform_override": "tui",
        }
    ]
    assert source_agent.session_id == parent_key
    source_manager.on_session_switch.assert_not_called()
    source_manager.on_session_finalize.assert_not_called()


def test_session_branch_storage_failure_discards_partial_child(server, monkeypatch, tmp_path):
    parent_sid = "branch-parent-runtime"
    parent_key = "branch-parent"
    child_key = "branch-child"
    lease = MagicMock()
    discard = MagicMock(return_value=True)

    class _DB:
        def get_session_title(self, _key):
            return "parent"

        def get_next_title_in_lineage(self, title):
            return f"{title} 2"

        def create_session(self, *_args, **_kwargs):
            return child_key

        def append_message(self, **_kwargs):
            raise OSError("message insert failed")

    db = _DB()
    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_new_session_key", lambda: child_key)
    monkeypatch.setattr(server, "_session_cwd", lambda _session: str(tmp_path))
    monkeypatch.setattr(
        server,
        "_claim_active_session_slot",
        lambda *_args, **_kwargs: (lease, None),
    )
    monkeypatch.setattr(server, "_discard_unpublished_branch", discard)
    server._sessions[parent_sid] = {
        "agent": types.SimpleNamespace(session_id=parent_key),
        "history": [{"role": "user", "content": "copy me"}],
        "history_lock": threading.Lock(),
        "profile_home": str(tmp_path),
        "session_key": parent_key,
    }

    response = server.handle_request(
        {
            "id": "branch",
            "method": "session.branch",
            "params": {"session_id": parent_sid},
        }
    )

    assert response["error"]["code"] == 5008
    discard.assert_called_once_with(
        parent_session_id=parent_key,
        new_session_id=child_key,
        hermes_home=tmp_path,
        db=db,
        delete_created_row=True,
    )
    lease.release.assert_called_once_with()
    assert server._sessions[parent_sid]["agent"].session_id == parent_key


@pytest.mark.parametrize("delete_created_row", [False, True])
def test_discard_unpublished_branch_removes_sessiondb_only_after_cortex(
    server, monkeypatch, tmp_path, delete_created_row
):
    import altas.cortex.lifecycle as lifecycle

    events: list[str] = []

    def _discard(*_args, **_kwargs):
        events.append("cortex")
        return True

    class _DB:
        def get_session(self, session_id):
            assert session_id == "child"
            return {"id": session_id}

        def delete_session(self, session_id, *, sessions_dir=None):
            assert events == ["cortex"]
            assert session_id == "child"
            assert sessions_dir == tmp_path / "sessions"
            events.append("sessiondb-full")
            return True

        def delete_session_if_empty(self, session_id, *, sessions_dir=None):
            assert events == ["cortex"]
            assert session_id == "child"
            assert sessions_dir == tmp_path / "sessions"
            events.append("sessiondb-empty")
            return True

    monkeypatch.setattr(lifecycle, "discard_detached_session_branch", _discard)

    assert server._discard_unpublished_branch(
        parent_session_id="parent",
        new_session_id="child",
        hermes_home=tmp_path,
        db=_DB(),
        delete_created_row=delete_created_row,
    )
    assert events == [
        "cortex",
        "sessiondb-full" if delete_created_row else "sessiondb-empty",
    ]


def test_session_branch_publish_failure_removes_runtime_and_both_durable_rows(
    server, monkeypatch, tmp_path
):
    parent_sid = "branch-parent-runtime"
    parent_key = "branch-parent"
    child_key = "branch-child"
    lease = MagicMock()
    discard = MagicMock(return_value=True)
    manager = MagicMock()
    agent = types.SimpleNamespace(
        model="test/model",
        session_id=parent_key,
        _memory_manager=manager,
        _session_messages=None,
        shutdown_memory_provider=MagicMock(),
        close=MagicMock(),
    )

    class _DB:
        def get_session_title(self, _key):
            return "parent"

        def get_next_title_in_lineage(self, title):
            return f"{title} 2"

        def create_session(self, *_args, **_kwargs):
            return child_key

        def append_message(self, **_kwargs):
            return None

        def set_session_title(self, *_args):
            return True

        def get_session(self, _session_id):
            return {"source": "tui"}

    db = _DB()

    def _fail_after_publish(sid, key, child_agent, history, **_kwargs):
        server._sessions[sid] = {
            "agent": child_agent,
            "history": history,
            "history_lock": threading.Lock(),
            "running": False,
            "session_key": key,
            "source": "tui",
        }
        raise OSError("worker publication failed")

    monkeypatch.setattr(server, "_get_db", lambda: db)
    monkeypatch.setattr(server, "_new_session_key", lambda: child_key)
    monkeypatch.setattr(server, "_session_cwd", lambda _session: str(tmp_path))
    monkeypatch.setattr(server, "_resolve_model", lambda: "test/model")
    monkeypatch.setattr(server, "_make_agent", lambda *_args, **_kwargs: agent)
    monkeypatch.setattr(server, "_init_session", _fail_after_publish)
    monkeypatch.setattr(server, "_set_session_context", lambda *_args: [])
    monkeypatch.setattr(server, "_clear_session_context", lambda *_args: None)
    monkeypatch.setattr(
        server,
        "_claim_active_session_slot",
        lambda *_args, **_kwargs: (lease, None),
    )
    monkeypatch.setattr(server, "_discard_unpublished_branch", discard)
    server._sessions[parent_sid] = {
        "agent": types.SimpleNamespace(session_id=parent_key),
        "history": [{"role": "user", "content": "copy me"}],
        "history_lock": threading.Lock(),
        "profile_home": str(tmp_path),
        "session_key": parent_key,
    }

    response = server.handle_request(
        {
            "id": "branch",
            "method": "session.branch",
            "params": {"session_id": parent_sid},
        }
    )

    assert response["error"]["code"] == 5000
    assert set(server._sessions) == {parent_sid}
    manager.on_session_switch.assert_called_once_with(
        child_key,
        parent_session_id=parent_key,
        reset=True,
        reason="branch",
    )
    manager.on_session_finalize.assert_not_called()
    agent.shutdown_memory_provider.assert_called_once_with(
        [],
        finalize=False,
        reason="branch_publish_failed",
    )
    discard.assert_called_once_with(
        parent_session_id=parent_key,
        new_session_id=child_key,
        hermes_home=tmp_path,
        db=db,
        delete_created_row=True,
    )
    lease.release.assert_called_once_with()


def test_deferred_parented_build_failure_discards_unpublished_branch(
    server, monkeypatch, tmp_path
):
    parent_id = "deferred-parent"
    child_id = "deferred-child"
    lease = MagicMock()
    manager = MagicMock()
    manager.on_session_switch.side_effect = OSError("cortex switch failed")
    agent = types.SimpleNamespace(
        model="test/model",
        session_id=parent_id,
        _memory_manager=manager,
        shutdown_memory_provider=MagicMock(),
        close=MagicMock(),
    )
    discard = MagicMock(return_value=True)

    monkeypatch.setattr(server, "_new_session_key", lambda: child_id)
    monkeypatch.setattr(
        server,
        "_claim_active_session_slot",
        lambda *_args, **_kwargs: (lease, None),
    )
    monkeypatch.setattr(server, "_schedule_agent_build", lambda _sid: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda _session: None)
    monkeypatch.setattr(server, "_enable_gateway_prompts", lambda: None)
    monkeypatch.setattr(server, "_git_branch_for_cwd", lambda _cwd: "")
    monkeypatch.setattr(server, "_profile_home", lambda _profile: tmp_path)
    monkeypatch.setattr(server, "_make_agent", lambda *_args, **_kwargs: agent)
    monkeypatch.setattr(server, "_set_session_context", lambda *_args: [])
    monkeypatch.setattr(server, "_clear_session_context", lambda *_args: None)
    monkeypatch.setattr(server, "_discard_unpublished_branch", discard)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)

    response = server.handle_request(
        {
            "id": "create-child",
            "method": "session.create",
            "params": {
                "cwd": str(tmp_path),
                "messages": [{"role": "user", "content": "fork context"}],
                "parent_session_id": parent_id,
                "profile": "customer-profile",
                "source": "tui",
            },
        }
    )
    sid = response["result"]["session_id"]
    session = server._sessions[sid]

    server._start_agent_build(sid, session)
    assert session["agent_ready"].wait(timeout=2)

    assert "cortex switch failed" in session["agent_error"]
    discard.assert_called_once()
    assert discard.call_args.kwargs["parent_session_id"] == parent_id
    assert discard.call_args.kwargs["new_session_id"] == child_id
    assert discard.call_args.kwargs["hermes_home"] == tmp_path
    assert discard.call_args.kwargs["db"] is not None
    assert discard.call_args.kwargs["delete_created_row"] is False
    manager.on_session_finalize.assert_not_called()
    agent.shutdown_memory_provider.assert_called_once_with([], finalize=False)
    agent.close.assert_called_once_with()


def test_parented_session_create_deferred_build_binds_cortex_topology(
    server, monkeypatch, tmp_path
):
    """Desktop-created forks bind Cortex before the child becomes ready."""
    from altas.cortex.provider import CortexMemoryProvider
    from altas.cortex.store import CortexStore

    parent_id = "desktop-parent"
    child_id = "desktop-child"
    store = CortexStore(
        tmp_path / "cortex.db",
        owner_customer_id="customer-1",
    )
    store.initialize()
    store.ensure_session(parent_id)

    source_provider = CortexMemoryProvider()
    source_provider._store = store
    source_provider._config = types.SimpleNamespace()
    source_provider._session_id = parent_id

    child_provider = CortexMemoryProvider()
    child_provider._store = store
    child_provider._config = types.SimpleNamespace()
    child_provider._session_id = parent_id
    manager = types.SimpleNamespace(
        on_session_switch=MagicMock(side_effect=child_provider.on_session_switch),
        on_session_finalize=MagicMock(),
    )
    agent = types.SimpleNamespace(
        model="test/model",
        session_id=parent_id,
        _parent_session_id=parent_id,
        _memory_manager=manager,
    )
    make_calls = []

    def _make_agent(_sid, key, **kwargs):
        make_calls.append((key, kwargs))
        return agent

    class _Worker:
        def close(self):
            return None

    lease = MagicMock()
    monkeypatch.setattr(server, "_new_session_key", lambda: child_id)
    monkeypatch.setattr(
        server,
        "_claim_active_session_slot",
        lambda *_args, **_kwargs: (lease, None),
    )
    monkeypatch.setattr(server, "_schedule_agent_build", lambda _sid: None)
    monkeypatch.setattr(server, "_schedule_session_cap_enforcement", lambda: None)
    monkeypatch.setattr(server, "_register_session_cwd", lambda _session: None)
    monkeypatch.setattr(server, "_enable_gateway_prompts", lambda: None)
    monkeypatch.setattr(server, "_git_branch_for_cwd", lambda _cwd: "")
    monkeypatch.setattr(server, "_make_agent", _make_agent)
    monkeypatch.setattr(server, "_SlashWorker", lambda *_args, **_kwargs: _Worker())
    monkeypatch.setattr(server, "_set_session_context", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(server, "_clear_session_context", lambda *_args: None)
    monkeypatch.setattr(server, "_config_model_target", lambda: "test/model")
    monkeypatch.setattr(server, "_wire_callbacks", lambda _sid: None)
    monkeypatch.setattr(
        server,
        "_start_notification_poller",
        lambda _sid, _session: threading.Event(),
    )
    boundary = MagicMock()
    monkeypatch.setattr(server, "_notify_session_boundary", boundary)
    monkeypatch.setattr(server, "_emit", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        server,
        "_session_info",
        lambda _agent, _session=None: {"model": "test/model"},
    )
    monkeypatch.setattr(server, "_probe_config_health", lambda _cfg: None)
    monkeypatch.setattr(server, "_schedule_mcp_late_refresh", lambda *_args: None)

    import tools.approval as approval

    monkeypatch.setattr(approval, "register_gateway_notify", lambda *_args: None)
    monkeypatch.setattr(approval, "load_permanent_allowlist", lambda: None)

    response = server.handle_request(
        {
            "id": "create-child",
            "method": "session.create",
            "params": {
                "parent_session_id": parent_id,
                "messages": [{"role": "user", "content": "fork context"}],
                "source": "tui",
            },
        }
    )
    sid = response["result"]["session_id"]
    session = server._sessions[sid]
    assert session["parent_session_id"] == parent_id

    server._start_agent_build(sid, session)
    assert session["agent_ready"].wait(timeout=2)
    assert session.get("agent_error") is None

    assert len(make_calls) == 1
    key, kwargs = make_calls[0]
    assert key == child_id
    assert kwargs["session_id"] == parent_id
    assert kwargs["parent_session_id"] == parent_id
    assert agent.session_id == child_id
    assert agent._parent_session_id == parent_id
    manager.on_session_switch.assert_called_once_with(
        child_id,
        parent_session_id=parent_id,
        reset=True,
        reason="branch",
    )
    manager.on_session_finalize.assert_not_called()
    assert source_provider._session_id == parent_id
    assert child_provider._session_id == child_id
    assert store.session_lineage(parent_id)["state"] == "active"
    assert store.session_lineage(child_id) == {
        "session_id": child_id,
        "logical_conversation_id": child_id,
        "parent_session_id": parent_id,
        "state": "active",
    }
    with store.connect() as connection:
        admissions = connection.execute(
            "SELECT COUNT(*) FROM session_distill_admissions WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()[0]
        jobs = connection.execute(
            "SELECT COUNT(*) FROM cognitive_jobs WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()[0]
    assert admissions == 0
    assert jobs == 0
    boundary.assert_called_once_with("on_session_reset", child_id, "tui")


def test_make_agent_accepts_list_system_prompt(server, monkeypatch):
    captured = {}

    class _Agent:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.model = kwargs.get("model", "")

    monkeypatch.setitem(sys.modules, "run_agent", types.SimpleNamespace(AIAgent=_Agent))
    monkeypatch.setitem(
        sys.modules,
        "hermes_cli.runtime_provider",
        types.SimpleNamespace(
            resolve_runtime_provider=lambda **_kwargs: {
                "provider": "test",
                "base_url": None,
                "api_key": None,
                "api_mode": None,
            }
        ),
    )
    monkeypatch.setattr(server, "_load_cfg", lambda: {"agent": {"system_prompt": ["one", "two"]}})
    monkeypatch.setattr(server, "_resolve_startup_runtime", lambda: ("test/model", "test"))
    monkeypatch.setattr(server, "_get_db", lambda: None)

    server._make_agent("sid", "session-key", session_id="session-key")

    assert captured["ephemeral_system_prompt"] == "one\ntwo"


# ── Config I/O ───────────────────────────────────────────────────────


def test_config_load_missing(server, tmp_path):
    server._hermes_home = tmp_path
    assert server._load_cfg() == {}


def test_config_roundtrip(server, tmp_path):
    server._hermes_home = tmp_path
    server._save_cfg({"model": "test/model"})
    assert server._load_cfg()["model"] == "test/model"


# ── _cli_exec_blocked ────────────────────────────────────────────────


@pytest.mark.parametrize("argv", [
    [],
    ["setup"],
    ["gateway"],
    ["sessions", "browse"],
    ["config", "edit"],
])
def test_cli_exec_blocked(server, argv):
    assert server._cli_exec_blocked(argv) is not None


@pytest.mark.parametrize("argv", [
    ["version"],
    ["sessions", "list"],
])
def test_cli_exec_allowed(server, argv):
    assert server._cli_exec_blocked(argv) is None


# ── slash.exec skill command interception ────────────────────────────


def test_slash_exec_rejects_skill_commands(server):
    """slash.exec must reject skill commands so the TUI falls through to command.dispatch."""
    # Register a mock session
    sid = "test-session"
    server._sessions[sid] = {"session_key": sid, "agent": None}

    # Mock scan_skill_commands to return a known skill
    fake_skills = {"/hermes-agent-dev": {"name": "hermes-agent-dev", "description": "Dev workflow"}}

    with patch("agent.skill_commands.get_skill_commands", return_value=fake_skills):
        resp = server.handle_request({
            "id": "r1",
            "method": "slash.exec",
            "params": {"command": "hermes-agent-dev", "session_id": sid},
        })

    # Should return an error so the TUI's .catch() fires command.dispatch
    assert "error" in resp
    assert resp["error"]["code"] == 4018
    assert "skill command" in resp["error"]["message"]


def test_slash_exec_handles_plugin_commands_in_live_gateway(server):
    """Plugin slash commands return normal slash.exec output without using the worker."""
    sid = "test-session"

    class Worker:
        def __init__(self):
            self.calls = []

        def run(self, cmd):
            self.calls.append(cmd)
            return f"worker:{cmd}"

    worker = Worker()
    server._sessions[sid] = {"session_key": sid, "agent": None, "slash_worker": worker}

    with patch(
        "hermes_cli.plugins.get_plugin_command_handler",
        lambda name: (lambda arg: f"plugin:{arg}") if name == "plugin-cmd" else None,
    ):
        resp = server.handle_request({
            "id": "r-plugin-slash",
            "method": "slash.exec",
            "params": {"command": "plugin-cmd hello", "session_id": sid},
        })

    assert "error" not in resp
    assert resp["result"] == {"output": "plugin:hello"}
    assert worker.calls == []


def test_slash_exec_plugin_lookup_failure_falls_back_to_worker(server):
    """Plugin discovery failures must not break ordinary slash-worker commands."""
    sid = "test-session"

    class Worker:
        def __init__(self):
            self.calls = []

        def run(self, cmd):
            self.calls.append(cmd)
            return f"worker:{cmd}"

    worker = Worker()
    server._sessions[sid] = {"session_key": sid, "agent": None, "slash_worker": worker}

    with patch(
        "hermes_cli.plugins.get_plugin_command_handler",
        side_effect=RuntimeError("discovery boom"),
    ):
        resp = server.handle_request({
            "id": "r-plugin-lookup-failure",
            "method": "slash.exec",
            "params": {"command": "help", "session_id": sid},
        })

    assert "error" not in resp
    assert resp["result"] == {"output": "worker:help"}
    assert worker.calls == ["help"]


def test_slash_exec_plugin_handler_error_returns_output(server):
    """Plugin handler failures return slash output so the TUI does not redispatch."""
    sid = "test-session"

    class Worker:
        def __init__(self):
            self.calls = []

        def run(self, cmd):
            self.calls.append(cmd)
            return f"worker:{cmd}"

    def handler(arg):
        raise RuntimeError(f"handler boom: {arg}")

    worker = Worker()
    server._sessions[sid] = {"session_key": sid, "agent": None, "slash_worker": worker}

    with patch(
        "hermes_cli.plugins.get_plugin_command_handler",
        lambda name: handler if name == "plugin-cmd" else None,
    ):
        resp = server.handle_request({
            "id": "r-plugin-handler-error",
            "method": "slash.exec",
            "params": {"command": "plugin-cmd hello", "session_id": sid},
        })

    assert "error" not in resp
    assert resp["result"] == {"output": "Plugin command error: handler boom: hello"}
    assert worker.calls == []


@pytest.mark.parametrize("cmd", ["retry", "queue hello", "q hello", "steer fix the test", "plan", "learn create a skill from https://example.com/docs"])
def test_slash_exec_routes_pending_input_commands_to_dispatch(server, cmd):
    """slash.exec must route _pending_input commands to command.dispatch
    internally instead of returning the old 4018 "use command.dispatch"
    fallback error (#48848). Some TUI clients failed that client-side
    fallback, dropping the input and surfacing "empty command".

    The contract is that slash.exec produces exactly the response
    command.dispatch would for the same command — no fragile retry hop.
    """
    base, _, arg = cmd.partition(" ")

    def fresh_session():
        return {"session_key": "test-session", "agent": None}

    sid = "test-session"

    # Response from the (new) internal routing in slash.exec.
    server._sessions[sid] = fresh_session()
    routed = server.handle_request({
        "id": "r1",
        "method": "slash.exec",
        "params": {"command": cmd, "session_id": sid},
    })

    # Response from calling command.dispatch directly with the parsed parts.
    server._sessions[sid] = fresh_session()
    direct = server.handle_request({
        "id": "r1",
        "method": "command.dispatch",
        "params": {"name": base, "arg": arg, "session_id": sid},
    })

    # slash.exec must no longer emit the old client-fallback rejection.
    if "error" in routed:
        assert "pending-input command" not in routed["error"]["message"]

    # Internal routing must yield the same payload as command.dispatch.
    assert routed.get("result") == direct.get("result")
    assert routed.get("error") == direct.get("error")


def test_command_dispatch_queue_sends_message(server):
    """command.dispatch /queue returns {type: 'send', message: ...} for the TUI."""
    sid = "test-session"
    server._sessions[sid] = {"session_key": sid}

    resp = server.handle_request({
        "id": "r1",
        "method": "command.dispatch",
        "params": {"name": "queue", "arg": "tell me about quantum computing", "session_id": sid},
    })

    assert "error" not in resp
    result = resp["result"]
    assert result["type"] == "send"
    assert result["message"] == "tell me about quantum computing"


def test_command_dispatch_queue_requires_arg(server):
    """command.dispatch /queue without an argument returns an error."""
    sid = "test-session"
    server._sessions[sid] = {"session_key": sid}

    resp = server.handle_request({
        "id": "r2",
        "method": "command.dispatch",
        "params": {"name": "queue", "arg": "", "session_id": sid},
    })

    assert "error" in resp
    assert resp["error"]["code"] == 4004


def test_command_dispatch_learn_sends_built_prompt(server):
    """command.dispatch /learn returns {type: 'send', message: <built prompt>}
    so the TUI fires a real agent turn (#51829). The CLI handler queues onto
    _pending_input — a queue the TUI slash worker has no reader for — so the
    prompt was silently dropped after the ack. Routing through command.dispatch
    injects the standards-guided prompt as a normal turn instead.
    """
    from agent.learn_prompt import build_learn_prompt

    sid = "test-session"
    server._sessions[sid] = {"session_key": sid}

    arg = "create a skill from https://example.com/docs"
    resp = server.handle_request({
        "id": "r-learn",
        "method": "command.dispatch",
        "params": {"name": "learn", "arg": arg, "session_id": sid},
    })

    assert "error" not in resp
    result = resp["result"]
    assert result["type"] == "send"
    assert result["message"] == build_learn_prompt(arg)


def test_pending_input_commands_includes_learn(server):
    """Guard: _PENDING_INPUT_COMMANDS must list 'learn' — without it slash.exec
    routes /learn to the slash worker, which only prints the ack and drops the
    prompt onto the dead _pending_input queue (#51829)."""
    assert "learn" in server._PENDING_INPUT_COMMANDS


def test_skills_manage_search_uses_tools_hub_sources(server):
    result = type("Result", (), {
        "description": "Build better terminal demos",
        "name": "showroom",
    })()
    auth = MagicMock(return_value="auth")
    router = MagicMock(return_value=["source"])
    search = MagicMock(return_value=[result])
    fake_hub = types.SimpleNamespace(
        GitHubAuth=auth,
        create_source_router=router,
        unified_search=search,
    )

    with patch.dict(sys.modules, {"tools.skills_hub": fake_hub}):
        resp = server.handle_request({
            "id": "skills-search",
            "method": "skills.manage",
            "params": {"action": "search", "query": "showroom"},
        })

    assert "error" not in resp
    assert resp["result"] == {
        "results": [{"description": "Build better terminal demos", "name": "showroom"}]
    }
    auth.assert_called_once_with()
    router.assert_called_once_with("auth")
    search.assert_called_once_with("showroom", ["source"], source_filter="all", limit=20)


def test_command_dispatch_steer_fallback_sends_message(server):
    """command.dispatch /steer with no active agent falls back to send."""
    sid = "test-session"
    server._sessions[sid] = {"session_key": sid, "agent": None}

    resp = server.handle_request({
        "id": "r3",
        "method": "command.dispatch",
        "params": {"name": "steer", "arg": "focus on testing", "session_id": sid},
    })

    assert "error" not in resp
    result = resp["result"]
    assert result["type"] == "send"
    assert result["message"] == "focus on testing"


def test_command_dispatch_retry_finds_last_user_message(server):
    """command.dispatch /retry walks session['history'] to find the last user message."""
    sid = "test-session"
    history = [
        {"role": "user", "content": "first question"},
        {"role": "assistant", "content": "first answer"},
        {"role": "user", "content": "second question"},
        {"role": "assistant", "content": "second answer"},
    ]
    server._sessions[sid] = {
        "session_key": sid,
        "agent": None,
        "history": history,
        "history_lock": threading.Lock(),
        "history_version": 0,
    }

    resp = server.handle_request({
        "id": "r4",
        "method": "command.dispatch",
        "params": {"name": "retry", "session_id": sid},
    })

    assert "error" not in resp
    result = resp["result"]
    assert result["type"] == "send"
    assert result["message"] == "second question"
    # Verify history was truncated: everything from last user message onward removed
    assert len(server._sessions[sid]["history"]) == 2
    assert server._sessions[sid]["history"][-1]["role"] == "assistant"
    assert server._sessions[sid]["history_version"] == 1


def test_command_dispatch_retry_empty_history(server):
    """command.dispatch /retry with empty history returns error."""
    sid = "test-session"
    server._sessions[sid] = {
        "session_key": sid,
        "agent": None,
        "history": [],
        "history_lock": threading.Lock(),
        "history_version": 0,
    }

    resp = server.handle_request({
        "id": "r5",
        "method": "command.dispatch",
        "params": {"name": "retry", "session_id": sid},
    })

    assert "error" in resp
    assert resp["error"]["code"] == 4018


def test_command_dispatch_retry_handles_multipart_content(server):
    """command.dispatch /retry extracts text from multipart content lists."""
    sid = "test-session"
    history = [
        {"role": "user", "content": [
            {"type": "text", "text": "analyze this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
        ]},
        {"role": "assistant", "content": "I see the image."},
    ]
    server._sessions[sid] = {
        "session_key": sid,
        "agent": None,
        "history": history,
        "history_lock": threading.Lock(),
        "history_version": 0,
    }

    resp = server.handle_request({
        "id": "r6",
        "method": "command.dispatch",
        "params": {"name": "retry", "session_id": sid},
    })

    assert "error" not in resp
    result = resp["result"]
    assert result["type"] == "send"
    assert result["message"] == "analyze this"


def test_command_dispatch_returns_skill_payload(server):
    """command.dispatch returns structured skill payload for the TUI to send()."""
    sid = "test-session"
    server._sessions[sid] = {"session_key": sid}

    fake_skills = {"/hermes-agent-dev": {"name": "hermes-agent-dev", "description": "Dev workflow"}}
    fake_msg = "Loaded skill content here"

    with patch("agent.skill_commands.scan_skill_commands", return_value=fake_skills), \
         patch("agent.skill_commands.build_skill_invocation_message", return_value=fake_msg):
        resp = server.handle_request({
            "id": "r2",
            "method": "command.dispatch",
            "params": {"name": "hermes-agent-dev", "session_id": sid},
        })

    assert "error" not in resp
    result = resp["result"]
    assert result["type"] == "skill"
    assert result["message"] == fake_msg
    assert result["name"] == "hermes-agent-dev"


def test_command_dispatch_awaits_async_plugin_handler(server):
    async def _handler(arg):
        return f"async:{arg}"

    with patch(
        "hermes_cli.plugins.get_plugin_command_handler",
        lambda name: _handler if name == "async-cmd" else None,
    ):
        resp = server.handle_request({
            "id": "r-plugin",
            "method": "command.dispatch",
            "params": {"name": "async-cmd", "arg": "hello"},
        })

    assert "error" not in resp
    assert resp["result"] == {"type": "plugin", "output": "async:hello"}


# ── dispatch(): pool routing for long handlers (#12546) ──────────────


def test_dispatch_runs_short_handlers_inline(server):
    """Non-long handlers return their response synchronously from dispatch()."""
    server._methods["fast.ping"] = lambda rid, params: server._ok(rid, {"pong": True})

    resp = server.dispatch({"id": "r1", "method": "fast.ping", "params": {}})

    assert resp == {"jsonrpc": "2.0", "id": "r1", "result": {"pong": True}}


def test_dispatch_offloads_long_handlers_and_emits_via_stdout(capture):
    """Long handlers run on the pool and write their response via write_json."""
    server, buf = capture
    server._methods["slash.exec"] = lambda rid, params: server._ok(rid, {"output": "hi"})

    resp = server.dispatch({"id": "r2", "method": "slash.exec", "params": {}})
    assert resp is None

    for _ in range(50):
        if buf.getvalue():
            break
        time.sleep(0.01)

    written = json.loads(buf.getvalue())
    assert written == {"jsonrpc": "2.0", "id": "r2", "result": {"output": "hi"}}


def test_dispatch_long_handler_does_not_block_fast_handler(server):
    """A slow long handler must not prevent a concurrent fast handler from completing."""
    released = threading.Event()
    server._methods["slash.exec"] = lambda rid, params: (released.wait(timeout=5), server._ok(rid, {"done": True}))[1]
    server._methods["fast.ping"] = lambda rid, params: server._ok(rid, {"pong": True})

    t0 = time.monotonic()
    assert server.dispatch({"id": "slow", "method": "slash.exec", "params": {}}) is None

    fast_resp = server.dispatch({"id": "fast", "method": "fast.ping", "params": {}})
    fast_elapsed = time.monotonic() - t0

    assert fast_resp["result"] == {"pong": True}
    assert fast_elapsed < 0.5, f"fast handler blocked for {fast_elapsed:.2f}s behind slow handler"

    released.set()


def test_dispatch_session_compress_does_not_block_fast_handler(server):
    """Manual TUI compaction can take minutes, so it must not block the RPC loop."""
    released = threading.Event()

    def slow_compress(rid, params):
        released.wait(timeout=5)
        return server._ok(rid, {"done": True})

    server._methods["session.compress"] = slow_compress
    server._methods["fast.ping"] = lambda rid, params: server._ok(rid, {"pong": True})

    t0 = time.monotonic()
    assert server.dispatch({"id": "slow", "method": "session.compress", "params": {}}) is None

    fast_resp = server.dispatch({"id": "fast", "method": "fast.ping", "params": {}})
    fast_elapsed = time.monotonic() - t0

    assert fast_resp["result"] == {"pong": True}
    assert fast_elapsed < 0.5, f"fast handler blocked for {fast_elapsed:.2f}s behind session.compress"

    released.set()


def test_dispatch_long_handler_exception_produces_error_response(capture):
    """An exception inside a pool-dispatched handler still yields a JSON-RPC error."""
    server, buf = capture

    def boom(rid, params):
        raise RuntimeError("kaboom")

    server._methods["slash.exec"] = boom

    server.dispatch({"id": "r3", "method": "slash.exec", "params": {}})

    for _ in range(50):
        if buf.getvalue():
            break
        time.sleep(0.01)

    written = json.loads(buf.getvalue())
    assert written["id"] == "r3"
    assert written["error"]["code"] == -32000
    assert "kaboom" in written["error"]["message"]


def test_dispatch_unknown_long_method_still_goes_inline(server):
    """Method name not in _LONG_HANDLERS takes the sync path even if handler is slow."""
    server._methods["some.method"] = lambda rid, params: server._ok(rid, {"ok": True})

    resp = server.dispatch({"id": "r4", "method": "some.method", "params": {}})

    assert resp["result"] == {"ok": True}


@pytest.mark.parametrize("completion_method", ["complete.path", "complete.slash"])
def test_completion_handlers_are_pool_routed(completion_method, server):
    """complete.path/complete.slash must run on the pool, never the reader thread.

    Regression for #21123: completion ran inline, so a slow git ls-files /
    skill-scan blocked prompt.submit and froze the TUI for the 120s RPC timeout.
    """
    assert completion_method in server._LONG_HANDLERS


@pytest.mark.parametrize("completion_method", ["complete.path", "complete.slash"])
def test_slow_completion_does_not_block_fast_handler(completion_method, server):
    """A slow completion RPC must not block a concurrent fast handler (#21123)."""
    released = threading.Event()

    def slow_completion(rid, params):
        released.wait(timeout=5)
        return server._ok(rid, {"items": []})

    server._methods[completion_method] = slow_completion
    server._methods["fast.ping"] = lambda rid, params: server._ok(rid, {"pong": True})

    t0 = time.monotonic()
    assert server.dispatch({"id": "slow", "method": completion_method, "params": {}}) is None

    fast_resp = server.dispatch({"id": "fast", "method": "fast.ping", "params": {}})
    fast_elapsed = time.monotonic() - t0

    assert fast_resp["result"] == {"pong": True}
    assert fast_elapsed < 0.5, f"fast handler blocked for {fast_elapsed:.2f}s behind {completion_method}"

    released.set()
