"""Task-scoped process registration and cancellation."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib

import psutil

from .state import StateStore


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TaskProcessController:
    """Kill only explicitly registered processes with matching create time."""

    def __init__(self, store: StateStore):
        self.store = store

    def register(self, task_id: str, pid: int, command_label: str) -> None:
        process = psutil.Process(pid)
        fingerprint = hashlib.sha256(command_label.encode()).hexdigest()
        self.store.connection.execute(
            """
            INSERT OR IGNORE INTO task_processes(
                task_id, pid, create_time, command_fingerprint, registered_at
            ) VALUES(?, ?, ?, ?, ?)
            """,
            (task_id, pid, process.create_time(), fingerprint, _now()),
        )
        self.store.connection.commit()

    def cancel_task(self, task_id: str, *, timeout: float = 3.0) -> list[int]:
        rows = self.store.connection.execute(
            """
            SELECT pid, create_time FROM task_processes
            WHERE task_id = ? AND stopped_at IS NULL
            """,
            (task_id,),
        ).fetchall()
        matched: list[psutil.Process] = []
        for row in rows:
            try:
                process = psutil.Process(row["pid"])
                if abs(process.create_time() - row["create_time"]) > 0.01:
                    continue
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            matched.extend(process.children(recursive=True))
            matched.append(process)
        unique = {process.pid: process for process in matched}
        for process in reversed(list(unique.values())):
            try:
                process.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        _, alive = psutil.wait_procs(list(unique.values()), timeout=timeout)
        for process in alive:
            try:
                process.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        stopped = sorted(unique)
        if rows:
            self.store.connection.execute(
                """
                UPDATE task_processes SET stopped_at = ?
                WHERE task_id = ? AND stopped_at IS NULL
                """,
                (_now(), task_id),
            )
            self.store.connection.commit()
        return stopped
