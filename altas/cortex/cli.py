"""Command implementation for ``atlas cortex``."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from hermes_constants import get_hermes_home

from .graph import build_health, build_job_status, enqueue_dream
from .graphrag import open_graphrag_manager
from .runtime import open_cortex_store


_RUN_NOW_MAX_JOBS = 100
_TERMINAL_JOB_STATES = frozenset({"succeeded", "failed", "dead_letter"})


def _print(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def cortex_command(args: Any) -> int:
    store, config = open_cortex_store(get_hermes_home())
    command = getattr(args, "cortex_command", None) or "status"
    if command == "status":
        _print(build_health(store))
        return 0
    if command == "dream":
        # ``dream`` remains a compatibility command name. The queued job is a
        # deterministic recovery checkpoint; semantic jobs originate only at
        # logical session finalization.
        queued = enqueue_dream(store, source="cli")
        if getattr(args, "run_now", False):
            from .worker import DETERMINISTIC_JOB_TYPES, CortexDreamWorker

            worker = CortexDreamWorker(store, config)
            target_id = str(queued["job"]["id"])
            results: list[dict[str, Any]] = []
            target = build_job_status(store, target_id)
            for index in range(_RUN_NOW_MAX_JOBS):
                if target["job"]["status"] in _TERMINAL_JOB_STATES:
                    break
                result = worker.run_once(
                    job_types=DETERMINISTIC_JOB_TYPES,
                    prune_retention=index == 0,
                )
                results.append(asdict(result))
                target = build_job_status(store, target_id)
                if result.status != "succeeded":
                    break
            _print({
                "queued": queued,
                "processed": results[-1]
                if results
                else {"status": "not_run", "job_id": None},
                "drained": len(results),
                "target": target,
            })
            return 0 if target["job"]["status"] == "succeeded" else 1
        else:
            _print(queued)
        return 0
    if command != "index":
        raise ValueError(f"unknown Cortex command: {command}")

    manager = open_graphrag_manager(store, config)
    index_command = getattr(args, "cortex_index_command", None) or "list"
    if index_command == "list":
        _print({
            "active": (manager.active_provenance() if manager.active_index() else None),
            "indexes": [index.to_dict() for index in manager.list_indexes()],
        })
        return 0
    if index_command == "import":
        index = manager.import_artifact(getattr(args, "path"))
        if getattr(args, "publish", False):
            index = manager.publish(index.version)
        _print(index.to_dict())
        return 0
    version = str(getattr(args, "version", "") or "")
    if index_command == "publish":
        _print(manager.publish(version).to_dict())
        return 0
    if index_command == "rollback":
        _print(manager.rollback(version).to_dict())
        return 0
    raise ValueError(f"unknown Cortex index command: {index_command}")


__all__ = ["cortex_command"]
