"""Cross-process active chat session leases.

The session database records persisted conversations.  This module records
currently open chat surfaces, including idle CLI/TUI sessions that have not
written a transcript row yet.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)


def coerce_max_concurrent_sessions(value: Any, key: str = "max_concurrent_sessions") -> Optional[int]:
    """Return a positive integer cap, or None when disabled/invalid."""
    if value is None:
        return None
    if isinstance(value, bool):
        logger.warning(
            "Ignoring invalid %s=%r (expected a positive integer; 0/null disables)",
            key,
            value,
        )
        return None
    try:
        if isinstance(value, float):
            if not value.is_integer():
                raise ValueError(value)
            parsed = int(value)
        elif isinstance(value, str):
            parsed = int(value.strip(), 10)
        else:
            parsed = int(value)
    except (TypeError, ValueError):
        logger.warning(
            "Ignoring invalid %s=%r (expected a positive integer; 0/null disables)",
            key,
            value,
        )
        return None
    if parsed <= 0:
        return None
    return parsed


def resolve_max_concurrent_sessions(config: Any) -> Optional[int]:
    """Resolve top-level max_concurrent_sessions with gateway.* fallback."""
    raw: Any = None
    key = "max_concurrent_sessions"
    if isinstance(config, dict):
        if "max_concurrent_sessions" in config:
            raw = config.get("max_concurrent_sessions")
        else:
            gateway_cfg = config.get("gateway")
            if isinstance(gateway_cfg, dict):
                raw = gateway_cfg.get("max_concurrent_sessions")
                key = "gateway.max_concurrent_sessions"
    else:
        raw = getattr(config, "max_concurrent_sessions", None)
    return coerce_max_concurrent_sessions(raw, key=key)


def active_session_limit_message(active_count: int, max_sessions: int) -> str:
    return (
        f"Hermes is at the active session limit ({active_count}/{max_sessions}). "
        "Try again when another session finishes."
    )


def _registry_home(hermes_home: str | Path | None = None) -> Path:
    return Path(hermes_home or get_hermes_home()).expanduser().resolve()


def _state_dir(hermes_home: str | Path | None = None) -> Path:
    return _registry_home(hermes_home) / "runtime"


def _state_path(hermes_home: str | Path | None = None) -> Path:
    return _state_dir(hermes_home) / "active_sessions.json"


def _deleted_path(hermes_home: str | Path | None = None) -> Path:
    return _state_dir(hermes_home) / "deleted_sessions.json"


def _lock_path(hermes_home: str | Path | None = None) -> Path:
    return _state_dir(hermes_home) / "active_sessions.lock"


class _FileLock:
    def __init__(self, path: Path):
        self.path = path
        self._fh = None

    def __enter__(self):
        _ensure_private_dir(self.path.parent)
        _reject_unsafe_file(self.path)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(self.path, flags, 0o600)
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            self._fh = os.fdopen(fd, "a+b")
        except BaseException:
            os.close(fd)
            raise
        if os.name == "nt":
            try:
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_LOCK, 1)
            except Exception as exc:
                self._fh.close()
                self._fh = None
                raise RuntimeError("active session file lock unavailable") from exc
        else:
            try:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX)
            except Exception as exc:
                self._fh.close()
                self._fh = None
                raise RuntimeError("active session file lock unavailable") from exc
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._fh is None:
            return
        if os.name == "nt":
            try:
                import msvcrt

                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            except Exception:
                pass
        else:
            try:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
        try:
            self._fh.close()
        finally:
            self._fh = None


def _ensure_private_dir(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError(f"active session runtime directory cannot be a symlink: {path}")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        if os.name != "nt":
            raise


def _reject_unsafe_file(path: Path) -> None:
    if path.is_symlink():
        raise RuntimeError(f"active session registry cannot be a symlink: {path}")
    if path.exists() and not path.is_file():
        raise RuntimeError(f"active session registry is not a regular file: {path}")


def _private_open_new(path: Path):
    _ensure_private_dir(path.parent)
    _reject_unsafe_file(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        return os.fdopen(fd, "w", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise


def _private_read_json(path: Path) -> Any:
    _reject_unsafe_file(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RuntimeError(f"active session registry is not a regular file: {path}")
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "r", encoding="utf-8") as fh:
            fd = -1
            return json.load(fh)
    finally:
        if fd >= 0:
            os.close(fd)


def _read_entries(path: Path, *, strict: bool = False) -> list[dict[str, Any]]:
    _reject_unsafe_file(path)
    try:
        data = _private_read_json(path)
    except FileNotFoundError:
        return []
    except Exception as exc:
        if strict:
            raise RuntimeError(f"active session registry is unreadable: {path}") from exc
        logger.warning("Ignoring corrupt active session registry at %s", path)
        return []
    entries = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        if strict:
            raise RuntimeError(f"active session registry is invalid: {path}")
        return []
    if strict and any(not isinstance(entry, dict) for entry in entries):
        raise RuntimeError(f"active session registry is invalid: {path}")
    return [entry for entry in entries if isinstance(entry, dict)]


def _write_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    _ensure_private_dir(path.parent)
    _reject_unsafe_file(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with _private_open_new(tmp) as fh:
        json.dump({"entries": entries}, fh, sort_keys=True)
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        if os.name != "nt":
            raise


def _read_deleted_sessions(path: Path) -> dict[str, float]:
    _reject_unsafe_file(path)
    try:
        data = _private_read_json(path)
    except FileNotFoundError:
        return {}
    except Exception:
        # A corrupt privacy tombstone registry cannot be treated as empty:
        # doing so could let a stale process recreate a deleted transcript.
        raise RuntimeError(f"deleted session registry is unreadable: {path}")
    rows = data.get("sessions") if isinstance(data, dict) else None
    if not isinstance(rows, dict):
        raise RuntimeError(f"deleted session registry is invalid: {path}")
    result: dict[str, float] = {}
    for session_id, deleted_at in rows.items():
        if not isinstance(session_id, str) or not session_id:
            continue
        try:
            result[session_id] = float(deleted_at)
        except (TypeError, ValueError):
            result[session_id] = 0.0
    return result


def _write_deleted_sessions(path: Path, sessions: dict[str, float]) -> None:
    _ensure_private_dir(path.parent)
    _reject_unsafe_file(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    with _private_open_new(tmp) as fh:
        json.dump({"sessions": sessions}, fh, sort_keys=True)
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        if os.name != "nt":
            raise


def _process_start_time(pid: int) -> Optional[float]:
    # Pair pid with process create_time when psutil can read it, so a recycled
    # pid does not keep a stale lease alive indefinitely.
    try:
        import psutil  # type: ignore

        return float(psutil.Process(pid).create_time())
    except Exception:
        return None


def _optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pid_alive(pid: Any, process_start_time: Any = None) -> bool:
    try:
        pid_int = int(pid)
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    try:
        from gateway.status import _pid_exists

        exists = bool(_pid_exists(pid_int))
    except Exception:
        return False
    if not exists:
        return False
    expected_start = _optional_float(process_start_time)
    if expected_start is None:
        return True
    current_start = _process_start_time(pid_int)
    if current_start is None:
        return True
    return abs(current_start - expected_start) < 0.001


def _prune_dead(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        entry
        for entry in entries
        if _pid_alive(entry.get("pid"), entry.get("process_start_time"))
    ]


@dataclass
class ActiveSessionLease:
    lease_id: str
    session_id: str
    surface: str
    hermes_home: str = ""
    enabled: bool = True
    released: bool = False

    def release(self) -> None:
        if self.released or not self.enabled:
            return
        release_active_session(self)


class ActiveSessionConflict(RuntimeError):
    """Raised when privacy deletion overlaps a live cross-process lease."""

    def __init__(self, session_ids: Iterable[str]):
        self.session_ids = tuple(dict.fromkeys(str(item) for item in session_ids if item))
        detail = ", ".join(self.session_ids) or "unknown"
        super().__init__(f"session is active and cannot be deleted: {detail}")


class SessionDeletionLease:
    """Hold the registry lock across Cortex reconciliation and DB deletion.

    ``active_aliases`` may include gateway routing keys in addition to durable
    SessionDB ids. Only canonical ``session_ids`` are permanently tombstoned,
    so deleting one gateway conversation does not disable its reusable route.
    """

    def __init__(
        self,
        session_ids: Iterable[str],
        *,
        active_aliases: Iterable[str] = (),
        hermes_home: str | Path | None = None,
        ignore_lease_ids: Iterable[str] = (),
    ) -> None:
        self.session_ids = tuple(
            dict.fromkeys(str(item) for item in session_ids if item)
        )
        self.active_aliases = frozenset(
            (*self.session_ids, *(str(item) for item in active_aliases if item))
        )
        self.ignore_lease_ids = frozenset(
            str(item) for item in ignore_lease_ids if item
        )
        self.hermes_home = str(_registry_home(hermes_home))
        self._lock: _FileLock | None = None
        self._sealed = False

    def __enter__(self) -> "SessionDeletionLease":
        lock = _FileLock(_lock_path(self.hermes_home))
        lock.__enter__()
        self._lock = lock
        try:
            state_path = _state_path(self.hermes_home)
            raw_entries = _read_entries(state_path, strict=True)
            entries = _prune_dead(raw_entries)
            if len(entries) != len(raw_entries):
                _write_entries(state_path, entries)
            conflicts = [
                str(entry.get("session_id") or "")
                for entry in entries
                if str(entry.get("lease_id") or "") not in self.ignore_lease_ids
                and str(entry.get("session_id") or "") in self.active_aliases
            ]
            if conflicts:
                raise ActiveSessionConflict(conflicts)
            return self
        except BaseException:
            lock.__exit__(None, None, None)
            self._lock = None
            raise

    def seal(self, session_ids: Iterable[str] | None = None) -> None:
        """Publish durable no-revival tombstones after Cortex succeeds."""
        if self._lock is None:
            raise RuntimeError("session deletion lease is not active")
        if self._sealed:
            return
        path = _deleted_path(self.hermes_home)
        deleted = _read_deleted_sessions(path)
        now = time.time()
        selected = tuple(
            dict.fromkeys(
                str(item)
                for item in (self.session_ids if session_ids is None else session_ids)
                if item and str(item) in self.session_ids
            )
        )
        for session_id in selected:
            deleted.setdefault(session_id, now)
        _write_deleted_sessions(path, deleted)
        self._sealed = True

    def __exit__(self, exc_type, exc, tb) -> None:
        lock = self._lock
        self._lock = None
        if lock is not None:
            lock.__exit__(exc_type, exc, tb)


def claim_session_deletion(
    session_ids: Iterable[str],
    *,
    active_aliases: Iterable[str] = (),
    hermes_home: str | Path | None = None,
    ignore_lease_ids: Iterable[str] = (),
) -> SessionDeletionLease:
    """Reserve an inactive deletion set until both durable stores agree."""
    return SessionDeletionLease(
        session_ids,
        active_aliases=active_aliases,
        hermes_home=hermes_home,
        ignore_lease_ids=ignore_lease_ids,
    )


def try_acquire_active_session(
    *,
    session_id: str,
    surface: str,
    config: Any,
    metadata: Optional[dict[str, Any]] = None,
    hermes_home: str | Path | None = None,
) -> tuple[Optional[ActiveSessionLease], Optional[str]]:
    """Acquire an active-session slot.

    Returns ``(lease, None)`` on success. Leases are recorded even when the
    concurrency cap is disabled because privacy deletion uses the same registry
    as a cross-process ownership barrier.
    """
    max_sessions = resolve_max_concurrent_sessions(config)
    lease_id = uuid.uuid4().hex
    now = time.time()
    entry = {
        "lease_id": lease_id,
        "session_id": str(session_id),
        "surface": str(surface),
        "pid": os.getpid(),
        "process_start_time": _process_start_time(os.getpid()),
        "started_at": now,
        "updated_at": now,
    }
    if metadata:
        entry["metadata"] = {
            str(k): v for k, v in metadata.items() if isinstance(k, str)
        }

    registry_home = str(_registry_home(hermes_home))
    state_path = _state_path(registry_home)
    with _FileLock(_lock_path(registry_home)):
        deleted = _read_deleted_sessions(_deleted_path(registry_home))
        if str(session_id) in deleted:
            return None, "This session was deleted and cannot be resumed."
        raw_entries = _read_entries(state_path, strict=True)
        entries = _prune_dead(raw_entries)
        pruned = len(raw_entries) - len(entries)
        if pruned:
            logger.info("Pruned %d stale active session lease(s)", pruned)
        active_count = len(entries)
        if max_sessions is not None and active_count >= max_sessions:
            _write_entries(state_path, entries)
            logger.info(
                "Active session limit reached: active=%d max=%d surface=%s",
                active_count,
                max_sessions,
                surface,
            )
            return None, active_session_limit_message(active_count, max_sessions)
        entries.append(entry)
        _write_entries(state_path, entries)

    return ActiveSessionLease(
        lease_id=lease_id,
        session_id=str(session_id),
        surface=str(surface),
        hermes_home=registry_home,
    ), None


def release_active_session(lease: ActiveSessionLease) -> None:
    state_path = _state_path(lease.hermes_home or None)
    try:
        with _FileLock(_lock_path(lease.hermes_home or None)):
            entries = _prune_dead(_read_entries(state_path, strict=True))
            kept = [
                entry
                for entry in entries
                if str(entry.get("lease_id") or "") != lease.lease_id
            ]
            if len(kept) != len(entries):
                _write_entries(state_path, kept)
    finally:
        lease.released = True


def transfer_active_session(
    lease: ActiveSessionLease,
    *,
    session_id: str,
    metadata: Optional[dict[str, Any]] = None,
) -> bool:
    """Move an existing lease to a new session id without dropping the slot."""
    new_session_id = str(session_id or "")
    if not new_session_id:
        return False
    if lease.released:
        return False
    state_path = _state_path(lease.hermes_home or None)
    with _FileLock(_lock_path(lease.hermes_home or None)):
        deleted = _read_deleted_sessions(_deleted_path(lease.hermes_home or None))
        if new_session_id in deleted:
            return False
        entries = _prune_dead(_read_entries(state_path, strict=True))
        updated = False
        for entry in entries:
            if str(entry.get("lease_id") or "") != lease.lease_id:
                continue
            entry["session_id"] = new_session_id
            entry["updated_at"] = time.time()
            if metadata:
                entry["metadata"] = {
                    str(k): v for k, v in metadata.items() if isinstance(k, str)
                }
            updated = True
            break
        if updated:
            _write_entries(state_path, entries)
            lease.session_id = new_session_id
        return updated


def active_session_registry_snapshot(
    hermes_home: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Return the pruned active-session registry for diagnostics/tests."""
    state_path = _state_path(hermes_home)
    with _FileLock(_lock_path(hermes_home)):
        entries = _prune_dead(_read_entries(state_path, strict=True))
        _write_entries(state_path, entries)
        return entries
