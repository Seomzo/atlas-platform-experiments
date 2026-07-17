"""ACP session manager — maps ACP sessions to Hermes AIAgent instances.

Sessions are persisted to the shared SessionDB (``~/.hermes/state.db``) so they
survive process restarts and appear in ``session_search``.  When the editor
reconnects after idle/restart, the ``load_session`` / ``resume_session`` calls
find the persisted session in the database and restore the full conversation
history.
"""
from __future__ import annotations

from hermes_constants import get_hermes_home

import copy
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _win_path_to_wsl(path: str) -> str | None:
    """Convert a Windows drive path to its WSL /mnt/<drive>/... equivalent."""
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", path)
    if not match:
        return None
    drive = match.group(1).lower()
    tail = match.group(2).replace("\\", "/")
    return f"/mnt/{drive}/{tail}"


def _translate_acp_cwd(cwd: str) -> str:
    """Translate Windows ACP cwd values when Hermes itself is running in WSL.

    Windows ACP clients can launch ``hermes acp`` inside WSL while still sending
    editor workspaces as Windows drive paths such as ``E:\\Projects``. Store
    and execute against the WSL mount path so agents, tools, and persisted ACP
    sessions all agree on the usable workspace. Native Linux/macOS keeps the
    original cwd unchanged.
    """
    from hermes_constants import is_wsl

    if not is_wsl():
        return cwd
    translated = _win_path_to_wsl(str(cwd))
    return translated if translated is not None else cwd


def _normalize_cwd_for_compare(cwd: str | None) -> str:
    raw = str(cwd or ".").strip()
    if not raw:
        raw = "."
    expanded = os.path.expanduser(raw)

    # Normalize Windows drive paths into the equivalent WSL mount form so
    # ACP history filters match the same workspace across Windows and WSL.
    translated = _win_path_to_wsl(expanded)
    if translated is not None:
        expanded = translated
    elif re.match(r"^/mnt/[A-Za-z]/", expanded):
        expanded = f"/mnt/{expanded[5].lower()}/{expanded[7:]}"

    return os.path.normpath(expanded)


def _build_session_title(title: Any, preview: Any, cwd: str | None) -> str:
    explicit = str(title or "").strip()
    if explicit:
        return explicit
    preview_text = str(preview or "").strip()
    if preview_text:
        return preview_text
    leaf = os.path.basename(str(cwd or "").rstrip("/\\"))
    return leaf or "New thread"


def _format_updated_at(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str) and value.strip():
        return value
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _updated_at_sort_key(value: Any) -> float:
    if value is None:
        return float("-inf")
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip()
    if not raw:
        return float("-inf")
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
    except Exception:
        try:
            return float(raw)
        except Exception:
            return float("-inf")


def _acp_stderr_print(*args, **kwargs) -> None:
    """Best-effort human-readable output sink for ACP stdio sessions.

    ACP reserves stdout for JSON-RPC frames, so any incidental CLI/status output
    from AIAgent must be redirected away from stdout. Route it to stderr instead.
    """
    kwargs = dict(kwargs)
    kwargs.setdefault("file", sys.stderr)
    print(*args, **kwargs)


def _register_task_cwd(task_id: str, cwd: str) -> None:
    """Bind a task/session id to the editor's working directory for tools.

    Zed can launch Hermes from a Windows workspace while the ACP process runs
    inside WSL. In that case ACP sends cwd as e.g. ``E:\\Projects\\POTI``;
    local tools need the WSL mount equivalent or subprocess creation fails
    before the command can run.
    """
    if not task_id:
        return
    try:
        from tools.terminal_tool import register_task_env_overrides
        register_task_env_overrides(task_id, {"cwd": _translate_acp_cwd(cwd)})
    except Exception:
        logger.debug("Failed to register ACP task cwd override", exc_info=True)


def _expand_acp_enabled_toolsets(
    toolsets: List[str] | None = None,
    mcp_server_names: List[str] | None = None,
) -> List[str]:
    """Return ACP toolsets plus explicit MCP server toolsets for this session."""
    expanded: List[str] = []
    for name in list(toolsets or ["hermes-acp"]):
        if name and name not in expanded:
            expanded.append(name)

    for server_name in list(mcp_server_names or []):
        toolset_name = f"mcp-{server_name}"
        if server_name and toolset_name not in expanded:
            expanded.append(toolset_name)

    return expanded


def _clear_task_cwd(task_id: str) -> None:
    """Remove task-specific cwd overrides for an ACP session."""
    if not task_id:
        return
    try:
        from tools.terminal_tool import clear_task_env_overrides
        clear_task_env_overrides(task_id)
    except Exception:
        logger.debug("Failed to clear ACP task cwd override", exc_info=True)


@dataclass
class SessionState:
    """Tracks per-session state for an ACP-managed Hermes agent."""

    session_id: str
    agent: Any  # AIAgent instance
    cwd: str = "."
    model: str = ""
    history: List[Dict[str, Any]] = field(default_factory=list)
    cancel_event: Any = None  # threading.Event
    is_running: bool = False
    queued_prompts: List[str] = field(default_factory=list)
    runtime_lock: Any = field(default_factory=Lock)
    current_prompt_text: str = ""
    interrupted_prompt_text: str = ""
    # ACP exposes a stable editor-facing handle, but Hermes may rotate the
    # physical session underneath it (context compression and /reset both do
    # this).  Persist the live head explicitly so a restarted ACP process does
    # not accidentally reopen the finalized root.
    current_session_id: str = ""
    parent_session_id: str = ""


class SessionManager:
    """Thread-safe manager for ACP sessions backed by Hermes AIAgent instances.

    Sessions are held in-memory for fast access **and** persisted to the
    shared SessionDB so they survive process restarts and are searchable
    via ``session_search``.
    """

    def __init__(self, agent_factory=None, db=None):
        """
        Args:
            agent_factory: Optional callable that creates an AIAgent-like object.
                           Used by tests. When omitted, a real AIAgent is created
                           using the current Hermes runtime provider configuration.
            db:            Optional SessionDB instance. When omitted, the default
                           SessionDB (``~/.hermes/state.db``) is lazily created.
        """
        self._sessions: Dict[str, SessionState] = {}
        self._lock = Lock()
        self._agent_factory = agent_factory
        self._db_instance = db  # None → lazy-init on first use

    # ---- public API ---------------------------------------------------------

    def create_session(self, cwd: str = ".") -> SessionState:
        """Create a new session with a unique ID and a fresh AIAgent."""
        import threading

        cwd = _translate_acp_cwd(cwd)
        session_id = str(uuid.uuid4())
        agent = self._make_agent(session_id=session_id, cwd=cwd)
        state = SessionState(
            session_id=session_id,
            agent=agent,
            cwd=cwd,
            model=getattr(agent, "model", "") or "",
            cancel_event=threading.Event(),
            current_session_id=session_id,
        )
        with self._lock:
            self._sessions[session_id] = state
        _register_task_cwd(session_id, cwd)
        self._persist(state)
        logger.info("Created ACP session %s (cwd=%s)", session_id, cwd)
        return state

    def get_session(self, session_id: str) -> Optional[SessionState]:
        """Return the session for *session_id*, or ``None``.

        If the session is not in memory but exists in the database (e.g. after
        a process restart), it is transparently restored.
        """
        with self._lock:
            state = self._sessions.get(session_id)
        if state is not None:
            return state
        # Attempt to restore from database.
        return self._restore(session_id)

    def remove_session(self, session_id: str, *, reason: str = "acp_close") -> bool:
        """Close an ACP logical session after its durable memory boundary.

        This deliberately preserves the SessionDB transcript.  ACP removal is
        a lifecycle event, not a privacy erasure request: Cortex must observe
        the true end boundary, and the ended transcript remains available to
        history/search instead of being silently deleted.
        """
        with self._lock:
            state = self._sessions.get(session_id)

        db = self._get_db()
        row = None
        if db is not None:
            try:
                row = db.get_session(session_id)
            except Exception:
                logger.debug("Failed to inspect ACP session %s", session_id, exc_info=True)
        if state is None and (
            row is None
            or str(row.get("end_reason") or "") in {"acp_close", "acp_shutdown"}
        ):
            return False
        if state is None:
            state = self._restore(session_id)

        if state is not None:
            with state.runtime_lock:
                if state.is_running:
                    logger.warning("Refusing to close running ACP session %s", session_id)
                    return False
                current_id = self._current_session_id(state)
                snapshot = copy.deepcopy(state.history)
                manager = self._memory_manager(state.agent)
                if manager is not None:
                    manager.on_session_finalize(snapshot, reason=reason)
                    if hasattr(manager, "flush_pending"):
                        manager.flush_pending(timeout=10)
                elif bool(getattr(state.agent, "_cortex_memory_selected", False)):
                    if not self._finalize_detached(current_id, reason=reason):
                        return False
        else:
            current_id = session_id
            if db is not None:
                try:
                    current_id = db.get_compression_tip(session_id) or session_id
                except Exception:
                    pass
            if not self._finalize_detached(current_id, reason=reason):
                return False

        # Publish the terminal SessionDB state only after Cortex accepted the
        # boundary.  The public ACP handle and a rotated internal head can be
        # distinct rows, so close both (end_session is idempotent).
        if db is not None:
            try:
                db.end_session(current_id, reason)
                if current_id != session_id:
                    db.end_session(session_id, reason)
            except Exception:
                logger.warning(
                    "Could not close SessionDB rows for ACP session %s",
                    session_id,
                    exc_info=True,
                )
                return False

        with self._lock:
            removed = self._sessions.pop(session_id, None)
        if removed is not None:
            self._shutdown_agent_nonfinalizing(
                removed.agent,
                snapshot,
                boundary_finalized=True,
            )
        _clear_task_cwd(session_id)
        if current_id != session_id:
            _clear_task_cwd(current_id)
        return True

    def fork_session(self, session_id: str, cwd: str = ".") -> Optional[SessionState]:
        """Deep-copy a session while preserving durable parent lineage."""
        import threading

        cwd = _translate_acp_cwd(cwd)
        original = self.get_session(session_id)  # checks DB too
        if original is None:
            return None

        with original.runtime_lock:
            if original.is_running:
                logger.warning("Refusing to fork running ACP session %s", session_id)
                return None

            parent_id = self._current_session_id(original)
            new_id = str(uuid.uuid4())
            child_agent = None
            lineage_prepared = False
            source_binding_safe = True
            db = self._get_db()
            try:
                if db is not None:
                    db.create_session(
                        session_id=new_id,
                        source="acp",
                        model=str(original.model) if original.model else None,
                        model_config={
                            "cwd": cwd,
                            "current_session_id": new_id,
                            "_branched_from": parent_id,
                        },
                        parent_session_id=parent_id,
                    )

                manager = self._memory_manager(original.agent)
                if manager is not None:
                    # A fork does not end its source.  Use the provider's
                    # switch contract only to create the child lineage, then
                    # immediately restore the source binding before publishing
                    # either state.  The runtime lock keeps turns out while the
                    # provider is transiently rebound.
                    try:
                        manager.on_session_switch(
                            new_id,
                            parent_session_id=parent_id,
                            reset=False,
                            reason="branch",
                        )
                        lineage_prepared = True
                    finally:
                        try:
                            manager.on_session_switch(
                                parent_id,
                                parent_session_id="",
                                reset=False,
                                reason="resume",
                            )
                        except Exception:
                            source_binding_safe = False
                            raise
                elif bool(getattr(original.agent, "_cortex_memory_selected", False)):
                    raise RuntimeError(
                        "Cortex fork lineage unavailable for the live ACP agent"
                    )

                child_agent = self._make_agent(
                    session_id=new_id,
                    cwd=cwd,
                    model=original.model or None,
                    parent_session_id=parent_id,
                )
                state = SessionState(
                    session_id=new_id,
                    agent=child_agent,
                    cwd=cwd,
                    model=getattr(child_agent, "model", original.model) or original.model,
                    history=copy.deepcopy(original.history),
                    cancel_event=threading.Event(),
                    current_session_id=new_id,
                    parent_session_id=parent_id,
                )
                self._persist(state, raise_on_error=True)
                self._prime_agent_persistence(child_agent, db, len(state.history))
            except Exception:
                logger.warning(
                    "Failed to durably fork ACP session %s",
                    session_id,
                    exc_info=True,
                )
                if child_agent is not None:
                    self._shutdown_agent_nonfinalizing(child_agent, [])
                branch_discarded = True
                if lineage_prepared:
                    branch_discarded = self._discard_detached_branch(
                        parent_id,
                        new_session_id=new_id,
                    )
                    if not branch_discarded:
                        logger.error(
                            "Could not hide unpublished ACP Cortex fork %s",
                            new_id,
                        )
                if not source_binding_safe:
                    # The old provider could still be pointed at the rejected
                    # child. Evict that runtime non-finalizingly; a later prompt
                    # restores a clean agent at the persisted parent head.
                    with self._lock:
                        self._sessions.pop(session_id, None)
                    self._shutdown_agent_nonfinalizing(
                        original.agent,
                        copy.deepcopy(original.history),
                    )
                if db is not None and branch_discarded:
                    try:
                        db.delete_session(new_id)
                    except Exception:
                        logger.debug(
                            "Could not discard unpublished ACP fork %s",
                            new_id,
                            exc_info=True,
                        )
                return None

            # Publication is last: neither the in-memory route nor cwd/tool
            # binding becomes visible until Cortex lineage and SessionDB copy
            # have both succeeded.
            with self._lock:
                self._sessions[new_id] = state
            _register_task_cwd(new_id, cwd)
            logger.info("Forked ACP session %s -> %s", session_id, new_id)
            return state

    def list_sessions(self, cwd: str | None = None) -> List[Dict[str, Any]]:
        """Return lightweight info dicts for all sessions (memory + database)."""
        normalized_cwd = _normalize_cwd_for_compare(cwd) if cwd else None
        db = self._get_db()
        persisted_rows: dict[str, dict[str, Any]] = {}

        if db is not None:
            try:
                for row in db.list_sessions_rich(source="acp", limit=1000):
                    persisted_rows[str(row["id"])] = dict(row)
            except Exception:
                logger.debug("Failed to load ACP sessions from DB", exc_info=True)

        # Collect in-memory sessions first.
        with self._lock:
            seen_ids = set(self._sessions.keys())
            results = []
            for s in self._sessions.values():
                history_len = len(s.history)
                if history_len <= 0:
                    continue
                if normalized_cwd and _normalize_cwd_for_compare(s.cwd) != normalized_cwd:
                    continue
                persisted = persisted_rows.get(s.session_id, {})
                preview = next(
                    (
                        str(msg.get("content") or "").strip()
                        for msg in s.history
                        if msg.get("role") == "user" and str(msg.get("content") or "").strip()
                    ),
                    persisted.get("preview") or "",
                )
                results.append(
                    {
                        "session_id": s.session_id,
                        "cwd": s.cwd,
                        "model": s.model,
                        "history_len": history_len,
                        "title": _build_session_title(persisted.get("title"), preview, s.cwd),
                        "updated_at": _format_updated_at(
                            persisted.get("last_active") or persisted.get("started_at") or time.time()
                        ),
                    }
                )

        # Merge any persisted sessions not currently in memory.
        for sid, row in persisted_rows.items():
            if sid in seen_ids:
                continue
            message_count = int(row.get("message_count") or 0)
            if message_count <= 0:
                continue
            # Extract cwd from model_config JSON.
            session_cwd = "."
            mc = row.get("model_config")
            if mc:
                try:
                    session_cwd = json.loads(mc).get("cwd", ".")
                except (json.JSONDecodeError, TypeError):
                    pass
            if normalized_cwd and _normalize_cwd_for_compare(session_cwd) != normalized_cwd:
                continue
            results.append({
                "session_id": sid,
                "cwd": session_cwd,
                "model": row.get("model") or "",
                "history_len": message_count,
                "title": _build_session_title(row.get("title"), row.get("preview"), session_cwd),
                "updated_at": _format_updated_at(row.get("last_active") or row.get("started_at")),
            })

        results.sort(key=lambda item: _updated_at_sort_key(item.get("updated_at")), reverse=True)
        return results

    def update_cwd(self, session_id: str, cwd: str) -> Optional[SessionState]:
        """Update the working directory for a session and its tool overrides."""
        cwd = _translate_acp_cwd(cwd)
        state = self.get_session(session_id)  # checks DB too
        if state is None:
            return None
        state.cwd = cwd
        _register_task_cwd(session_id, cwd)
        self._persist(state)
        return state

    def reset_session(self, session_id: str) -> Optional[SessionState]:
        """Commit a true logical reset while retaining the stable ACP handle.

        Cortex receives an atomic old-head -> new-head boundary before the
        transcript or agent identity is mutated.  The cheap semantic worker is
        merely queued by that commit; it never blocks this reset.
        """
        state = self.get_session(session_id)
        if state is None:
            return None

        with state.runtime_lock:
            if state.is_running:
                logger.warning("Refusing to reset running ACP session %s", session_id)
                return None

            old_id = self._current_session_id(state)
            new_id = str(uuid.uuid4())
            snapshot = copy.deepcopy(state.history)
            db = self._get_db()

            # Prepare the private physical row first. It is not discoverable as
            # an ACP session and is discarded if the memory boundary fails.
            if db is not None:
                db.create_session(
                    session_id=new_id,
                    source="acp_internal",
                    model=str(state.model) if state.model else None,
                    model_config={
                        "cwd": state.cwd,
                        "_acp_owner_session_id": state.session_id,
                        "_reset_from": old_id,
                    },
                    parent_session_id=old_id,
                )

            manager = self._memory_manager(state.agent)
            try:
                if manager is not None:
                    manager.commit_session_boundary_async(
                        snapshot,
                        new_session_id=new_id,
                        parent_session_id=old_id,
                        reason="reset",
                        reset=True,
                    )
                    if hasattr(manager, "flush_pending"):
                        manager.flush_pending(timeout=10)
                elif bool(getattr(state.agent, "_cortex_memory_selected", False)):
                    if not self._commit_detached_boundary(
                        old_id,
                        new_session_id=new_id,
                        parent_session_id=old_id,
                        reason="reset",
                        reset=True,
                    ):
                        raise RuntimeError("detached Cortex reset boundary failed")
            except Exception:
                if db is not None:
                    try:
                        db.delete_session(new_id)
                    except Exception:
                        logger.debug(
                            "Could not discard unpublished ACP reset target %s",
                            new_id,
                            exc_info=True,
                        )
                raise

            # The durable provider now owns the new head. From this point on we
            # complete the local rebind rather than pretending the old session
            # remained usable if a best-effort cleanup operation fails.
            try:
                if hasattr(state.agent, "commit_memory_session"):
                    state.agent.commit_memory_session(
                        snapshot,
                        checkpoint_memory=False,
                    )
            except Exception:
                logger.debug("ACP reset context-engine close failed", exc_info=True)

            state.current_session_id = new_id
            try:
                state.agent.session_id = new_id
            except Exception:
                pass
            try:
                state.agent.session_start = datetime.now(timezone.utc)
            except Exception:
                pass
            try:
                state.agent.reset_session_state()
            except Exception:
                logger.debug("ACP agent reset_session_state failed", exc_info=True)
            try:
                state.agent._session_messages = []
                state.agent._last_flushed_db_idx = 0
                state.agent._session_db_created = db is not None
            except Exception:
                pass
            state.history.clear()
            state.queued_prompts.clear()
            state.current_prompt_text = ""
            state.interrupted_prompt_text = ""

            # Persist the stable-handle -> live-head mapping before the caller
            # publishes the /reset response.  The child row already exists, so
            # this is an idempotent local metadata update.
            self._persist(state, raise_on_error=True)
            if db is not None and old_id != state.session_id:
                db.end_session(old_id, "reset")
            _register_task_cwd(new_id, state.cwd)
            logger.info(
                "Reset ACP logical session %s (%s -> %s)",
                state.session_id,
                old_id,
                new_id,
            )
            return state

    def replace_agent(
        self,
        session_id: str,
        *,
        model: str,
        requested_provider: str | None = None,
        base_url: str | None = None,
        api_mode: str | None = None,
    ) -> Optional[SessionState]:
        """Replace an ACP model runtime without ending the logical session."""
        state = self.get_session(session_id)
        if state is None:
            return None
        with state.runtime_lock:
            if state.is_running:
                logger.warning(
                    "Refusing to replace model for running ACP session %s",
                    session_id,
                )
                return None
            current_id = self._current_session_id(state)
            old_agent = state.agent
            replacement = self._make_agent(
                session_id=current_id,
                cwd=state.cwd,
                model=model,
                requested_provider=requested_provider,
                base_url=base_url,
                api_mode=api_mode,
                parent_session_id=state.parent_session_id,
            )
            old_model = state.model
            state.agent = replacement
            state.model = model
            state.current_session_id = current_id
            try:
                self._persist(state, raise_on_error=True)
            except Exception:
                state.agent = old_agent
                state.model = old_model
                self._shutdown_agent_nonfinalizing(replacement, [])
                raise
            self._prime_agent_persistence(
                replacement,
                self._get_db(),
                len(state.history),
            )
            self._shutdown_agent_nonfinalizing(old_agent, copy.deepcopy(state.history))
            return state

    def cleanup(self) -> None:
        """Persist and release process-local ACP resources without finalizing.

        Server/process teardown is not evidence that the customer's logical
        conversation ended.  Keep SessionDB and Cortex sessions resumable and
        use only the non-finalizing compatibility checkpoint while closing live
        runtimes.  ``remove_session`` is the explicit true-close surface.
        """
        with self._lock:
            states = list(self._sessions.values())
            self._sessions.clear()
        for state in states:
            snapshot = copy.deepcopy(state.history)
            try:
                self._persist(state)
            except Exception:
                logger.debug(
                    "Failed to persist ACP session %s during process cleanup",
                    state.session_id,
                    exc_info=True,
                )
            self._shutdown_agent_nonfinalizing(state.agent, snapshot)
            _clear_task_cwd(state.session_id)
            current_id = self._current_session_id(state)
            if current_id != state.session_id:
                _clear_task_cwd(current_id)

    def save_session(self, session_id: str) -> None:
        """Persist the current state of a session to the database.

        Called by the server after prompt completion, slash commands that
        mutate history, and model switches.
        """
        with self._lock:
            state = self._sessions.get(session_id)
        if state is not None:
            self._persist(state)

    # ---- persistence via SessionDB ------------------------------------------

    def _get_db(self):
        """Lazily initialise and return the SessionDB instance.

        Returns ``None`` if the DB is unavailable (e.g. import error in a
        minimal test environment).

        Note: we resolve ``HERMES_HOME`` dynamically rather than relying on
        the module-level ``DEFAULT_DB_PATH`` constant, because that constant
        is evaluated at import time and won't reflect env-var changes made
        later (e.g. by the test fixture ``_isolate_hermes_home``).
        """
        if self._db_instance is not None:
            return self._db_instance
        try:
            from hermes_state import SessionDB
            hermes_home = get_hermes_home()
            self._db_instance = SessionDB(db_path=hermes_home / "state.db")
            return self._db_instance
        except Exception:
            logger.debug("SessionDB unavailable for ACP persistence", exc_info=True)
            return None

    def _persist(self, state: SessionState, *, raise_on_error: bool = False) -> bool:
        """Write session state to the database.

        Creates the session record if it doesn't exist, then replaces all
        stored messages with the current in-memory history.
        """
        db = self._get_db()
        if db is None:
            return True

        # Ensure model is a plain string (not a MagicMock or other proxy).
        model_str = str(state.model) if state.model else None
        current_session_id = self._current_session_id(state)
        state.current_session_id = current_session_id
        session_meta = {
            "cwd": state.cwd,
            "current_session_id": current_session_id,
        }
        if state.parent_session_id:
            session_meta["_branched_from"] = state.parent_session_id
        provider = getattr(state.agent, "provider", None)
        base_url = getattr(state.agent, "base_url", None)
        api_mode = getattr(state.agent, "api_mode", None)
        if isinstance(provider, str) and provider.strip():
            session_meta["provider"] = provider.strip()
        if isinstance(base_url, str) and base_url.strip():
            session_meta["base_url"] = base_url.strip()
        if isinstance(api_mode, str) and api_mode.strip():
            session_meta["api_mode"] = api_mode.strip()
        cwd_json = json.dumps(session_meta)

        try:
            # Ensure the session record exists.
            existing = db.get_session(state.session_id)
            if existing is None:
                db.create_session(
                    session_id=state.session_id,
                    source="acp",
                    model=model_str,
                    model_config=session_meta,
                    parent_session_id=state.parent_session_id or None,
                )
            else:
                # Update model_config (contains cwd) if changed.
                try:
                    db.update_session_meta(state.session_id, cwd_json, model_str)
                except Exception:
                    logger.debug("Failed to update ACP session metadata", exc_info=True)
                    if raise_on_error:
                        raise

            # A reset keeps the public ACP handle stable while rotating the
            # physical Hermes head. Ensure that private head exists, but keep it
            # out of ACP list/load discovery as a separate editor session.
            if current_session_id != state.session_id:
                current_row = db.get_session(current_session_id)
                if current_row is None:
                    db.create_session(
                        session_id=current_session_id,
                        source="acp_internal",
                        model=model_str,
                        model_config={
                            "cwd": state.cwd,
                            "_acp_owner_session_id": state.session_id,
                        },
                    )

            # When the agent owns persistence to this same SessionDB it has
            # already flushed the live transcript incrementally during
            # run_conversation (append_message), and it preserves pre-compaction
            # turns non-destructively via archive_and_compact() — keeping them on
            # disk as searchable active=0/compacted=1 rows. Calling
            # replace_messages() here would then be a redundant double-write that
            # DELETEs exactly those archived rows (and, after a compression-driven
            # id rotation where agent.session_id no longer equals
            # state.session_id, clobbers the ended parent transcript) — silent
            # data loss for any ACP conversation long enough to compress.
            #
            # Only fall back to the destructive atomic replace when the agent is
            # NOT persisting itself to this DB (e.g. a test agent factory, or a
            # fresh create/fork whose copied history the agent has not flushed
            # yet). That path still rolls back on a mid-rewrite failure so the
            # previously persisted conversation survives (salvaged from #13675).
            agent = state.agent
            agent_db = getattr(agent, "_session_db", None)
            agent_owns_persistence = (
                agent_db is not None
                and agent_db is db
                and bool(getattr(agent, "_session_db_created", False))
            )
            if not agent_owns_persistence:
                # Even when the current agent doesn't "own" persistence, the
                # session on disk may already carry compaction-archived rows —
                # e.g. after a model switch or a /restore, both of which mint a
                # fresh agent with _session_db_created=False (so the check above
                # is False) yet leave the durable archived transcript in place.
                # A full-history replace would DELETE those archived rows just
                # like the owned-agent case. Guard against it: when archived
                # rows exist, replace ONLY the live (active=1) set and leave the
                # archived turns untouched; otherwise the destructive replace is
                # safe (fresh create/fork with no archived history to lose).
                try:
                    has_archived = db.has_archived_messages(current_session_id)
                except Exception:
                    has_archived = False
                db.replace_messages(
                    current_session_id, state.history, active_only=has_archived
                )
            return True
        except Exception:
            logger.warning("Failed to persist ACP session %s", state.session_id, exc_info=True)
            if raise_on_error:
                raise
            return False

    def _restore(self, session_id: str) -> Optional[SessionState]:
        """Load a session from the database into memory, recreating the AIAgent."""
        import threading

        db = self._get_db()
        if db is None:
            return None

        try:
            row = db.get_session(session_id)
        except Exception:
            logger.debug("Failed to query DB for ACP session %s", session_id, exc_info=True)
            return None

        if row is None:
            return None

        # Only restore ACP sessions.
        if row.get("source") != "acp":
            return None

        # Extract cwd from model_config.
        cwd = "."
        requested_provider = row.get("billing_provider")
        restored_base_url = row.get("billing_base_url")
        restored_api_mode = None
        mc = row.get("model_config")
        current_session_id = session_id
        if mc:
            try:
                meta = json.loads(mc)
                if isinstance(meta, dict):
                    cwd = meta.get("cwd", ".")
                    current_session_id = str(
                        meta.get("current_session_id") or session_id
                    )
                    requested_provider = meta.get("provider") or requested_provider
                    restored_base_url = meta.get("base_url") or restored_base_url
                    restored_api_mode = meta.get("api_mode") or restored_api_mode
            except (json.JSONDecodeError, TypeError):
                pass

        try:
            current_session_id = (
                db.get_compression_tip(current_session_id) or current_session_id
            )
        except Exception:
            pass

        # Compression ends the stable root while moving the live transcript to
        # a continuation, so an ended public row alone is not terminal.  An ACP
        # close/shutdown marker on either the route or its current head is.
        terminal_reasons = {"acp_close", "acp_shutdown"}
        if str(row.get("end_reason") or "") in terminal_reasons:
            return None
        if current_session_id != session_id:
            try:
                current_row = db.get_session(current_session_id)
            except Exception:
                current_row = None
            if current_row and str(current_row.get("end_reason") or "") in terminal_reasons:
                return None

        model = row.get("model") or None

        # Load conversation history.
        try:
            history = db.get_messages_as_conversation(current_session_id)
        except Exception:
            logger.warning("Failed to load messages for ACP session %s", session_id, exc_info=True)
            history = []

        try:
            agent = self._make_agent(
                session_id=current_session_id,
                cwd=cwd,
                model=model,
                requested_provider=requested_provider,
                base_url=restored_base_url,
                api_mode=restored_api_mode,
            )
        except Exception:
            logger.warning("Failed to recreate agent for ACP session %s", session_id, exc_info=True)
            return None

        state = SessionState(
            session_id=session_id,
            agent=agent,
            cwd=cwd,
            model=model or getattr(agent, "model", "") or "",
            history=history,
            cancel_event=threading.Event(),
            current_session_id=current_session_id,
            parent_session_id=str(row.get("parent_session_id") or ""),
        )
        self._prime_agent_persistence(agent, db, len(history))
        with self._lock:
            self._sessions[session_id] = state
        _register_task_cwd(session_id, cwd)
        logger.info("Restored ACP session %s from DB (%d messages)", session_id, len(history))
        return state

    # ---- internal -----------------------------------------------------------

    @staticmethod
    def _memory_manager(agent: Any) -> Any | None:
        manager = getattr(agent, "_memory_manager", None)
        # Test doubles frequently expose arbitrary attributes as mocks.  A
        # usable manager must at least provide one lifecycle method.
        if manager is None or not (
            callable(getattr(manager, "on_session_finalize", None))
            or callable(getattr(manager, "commit_session_boundary_async", None))
        ):
            return None
        return manager

    @staticmethod
    def _current_session_id(state: SessionState) -> str:
        agent_id = getattr(state.agent, "session_id", None)
        if isinstance(agent_id, str) and agent_id.strip():
            return agent_id.strip()
        if isinstance(state.current_session_id, str) and state.current_session_id.strip():
            return state.current_session_id.strip()
        return state.session_id

    @staticmethod
    def _shutdown_agent_nonfinalizing(
        agent: Any,
        messages: List[Dict[str, Any]],
        *,
        boundary_finalized: bool = False,
    ) -> None:
        """Release one runtime without publishing a logical session end."""
        if agent is None:
            return
        try:
            agent._end_session_on_close = False
        except Exception:
            pass
        if boundary_finalized:
            # The true boundary already captured the terminal evidence epoch.
            # Close context/provider resources without a second durable
            # checkpoint, which would otherwise write after finalization.
            try:
                commit_context = getattr(agent, "commit_memory_session", None)
                if callable(commit_context):
                    commit_context(
                        copy.deepcopy(messages or []),
                        checkpoint_memory=False,
                    )
            except Exception:
                logger.debug("ACP context resource close failed", exc_info=True)
            try:
                manager = getattr(agent, "_memory_manager", None)
                if manager is not None and hasattr(manager, "shutdown_all"):
                    manager.shutdown_all()
                agent._memory_manager = None
            except Exception:
                logger.warning("ACP memory provider shutdown failed", exc_info=True)
        else:
            try:
                shutdown = getattr(agent, "shutdown_memory_provider", None)
                if callable(shutdown):
                    shutdown(copy.deepcopy(messages or []), finalize=False)
            except Exception:
                logger.warning("ACP non-finalizing memory shutdown failed", exc_info=True)
        try:
            close = getattr(agent, "close", None)
            if callable(close):
                close()
        except Exception:
            logger.debug("ACP agent close failed", exc_info=True)

    @staticmethod
    def _prime_agent_persistence(agent: Any, db: Any, history_len: int) -> None:
        """Tell a replacement/restored agent which DB prefix already exists."""
        if agent is None or db is None:
            return
        try:
            if getattr(agent, "_session_db", None) is db:
                agent._session_db_created = True
                agent._last_flushed_db_idx = max(0, int(history_len))
        except Exception:
            pass

    @staticmethod
    def _finalize_detached(session_id: str, *, reason: str) -> bool:
        try:
            from altas.cortex.lifecycle import finalize_detached_session

            return bool(
                finalize_detached_session(
                    get_hermes_home(),
                    session_id,
                    reason=reason,
                )
            )
        except Exception:
            logger.warning(
                "Detached Cortex finalization failed for ACP session %s",
                session_id,
                exc_info=True,
            )
            return False

    @staticmethod
    def _commit_detached_boundary(
        session_id: str,
        *,
        new_session_id: str,
        parent_session_id: str,
        reason: str,
        reset: bool,
    ) -> bool:
        try:
            from altas.cortex.lifecycle import commit_detached_session_boundary

            return bool(
                commit_detached_session_boundary(
                    get_hermes_home(),
                    session_id,
                    new_session_id=new_session_id,
                    parent_session_id=parent_session_id,
                    reason=reason,
                    reset=reset,
                )
            )
        except Exception:
            logger.warning(
                "Detached Cortex boundary failed for ACP session %s",
                session_id,
                exc_info=True,
            )
            return False

    @staticmethod
    def _discard_detached_branch(
        session_id: str,
        *,
        new_session_id: str,
    ) -> bool:
        try:
            from altas.cortex.lifecycle import discard_detached_session_branch

            return bool(
                discard_detached_session_branch(
                    get_hermes_home(),
                    session_id,
                    new_session_id=new_session_id,
                )
            )
        except Exception:
            logger.warning(
                "Detached Cortex branch discard failed for ACP child %s",
                new_session_id,
                exc_info=True,
            )
            return False

    def _make_agent(
        self,
        *,
        session_id: str,
        cwd: str,
        model: str | None = None,
        requested_provider: str | None = None,
        base_url: str | None = None,
        api_mode: str | None = None,
        parent_session_id: str | None = None,
    ):
        if self._agent_factory is not None:
            agent = self._agent_factory()
            try:
                existing_id = getattr(agent, "session_id", None)
                if not isinstance(existing_id, str) or not existing_id.strip():
                    agent.session_id = session_id
            except Exception:
                pass
            return agent

        from run_agent import AIAgent
        from hermes_cli.config import load_config
        from hermes_cli.runtime_provider import resolve_runtime_provider

        config = load_config()
        model_cfg = config.get("model")
        default_model = ""
        config_provider = None
        if isinstance(model_cfg, dict):
            default_model = str(model_cfg.get("default") or default_model)
            config_provider = model_cfg.get("provider")
        elif isinstance(model_cfg, str) and model_cfg.strip():
            default_model = model_cfg.strip()

        configured_mcp_servers = [
            name
            for name, cfg in (config.get("mcp_servers") or {}).items()
            if not isinstance(cfg, dict) or cfg.get("enabled", True) is not False
        ]

        kwargs = {
            "platform": "acp",
            "enabled_toolsets": _expand_acp_enabled_toolsets(
                ["hermes-acp"],
                mcp_server_names=configured_mcp_servers,
            ),
            "quiet_mode": True,
            "session_id": session_id,
            "session_db": self._get_db(),
            "model": model or default_model,
            "parent_session_id": parent_session_id,
        }

        try:
            runtime = resolve_runtime_provider(requested=requested_provider or config_provider)
            kwargs.update(
                {
                    "provider": runtime.get("provider"),
                    "api_mode": api_mode or runtime.get("api_mode"),
                    "base_url": base_url or runtime.get("base_url"),
                    "api_key": runtime.get("api_key"),
                    "command": runtime.get("command"),
                    "args": list(runtime.get("args") or []),
                }
            )
        except Exception:
            logger.debug("ACP session falling back to default provider resolution", exc_info=True)

        _register_task_cwd(session_id, cwd)
        agent = AIAgent(**kwargs)
        # Codex app-server sessions are spawned lazily on the first turn. Stamp
        # the ACP workspace onto the agent so the Codex runtime starts from the
        # editor/session cwd instead of the Hermes daemon's process cwd.
        agent.session_cwd = cwd
        # ACP stdio transport requires stdout to remain protocol-only JSON-RPC.
        # Route any incidental human-readable agent output to stderr instead.
        agent._print_fn = _acp_stderr_print
        return agent
