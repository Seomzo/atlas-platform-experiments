"""Durable Atlas Cortex dream worker and process-local supervisor."""

from __future__ import annotations

import logging
import os
import socket
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .config import CortexConfig
from .dream import DreamProcessor
from .managed_dispatch import CortexDispatchAdmission
from .store import CortexStore


logger = logging.getLogger(__name__)

SUPPORTED_JOB_TYPES = (
    "dream",
    "dream_cycle",
    "session_distill",
    "precompress_distill",
    "session_checkpoint",
    "session_reconcile",
)
DETERMINISTIC_JOB_TYPES = (
    "session_reconcile",
    "session_checkpoint",
    "precompress_distill",
    "dream",
    "dream_cycle",
)
MODEL_JOB_TYPES = ("session_distill",)

_UNSET_SCOPE = object()


def capture_profile_secret_scope(
    profile_home: str | Path,
) -> Mapping[str, str] | None:
    """Capture credentials for a worker that may outlive the current turn.

    Multiplexed gateways fail closed on an unscoped credential read.  Worker
    threads do not inherit contextvars, so retain the current authoritative
    profile mapping, or rebuild it from that profile's ``.env`` when the
    worker is created outside a turn scope.  Single-profile processes keep
    their legacy dynamic ``os.environ`` behavior.
    """
    from agent.secret_scope import (
        build_profile_secret_scope,
        current_secret_scope,
        is_multiplex_active,
    )

    if is_multiplex_active():
        # Persistent workers must bind to the requested profile, not whichever
        # request context happened to create them. In particular, never retain
        # a multiplexed request's managed token past its authorization window.
        return build_profile_secret_scope(Path(profile_home))
    current = current_secret_scope()
    if current is not None:
        return dict(current)
    return None


@contextmanager
def cortex_profile_runtime_scope(
    profile_home: str | Path,
    secret_scope: Mapping[str, str] | None = None,
):
    """Install one profile's home and credentials for background work."""
    from agent.secret_scope import reset_secret_scope, set_secret_scope
    from hermes_constants import reset_hermes_home_override, set_hermes_home_override

    home_token = set_hermes_home_override(str(Path(profile_home).resolve()))
    secret_token = set_secret_scope(secret_scope) if secret_scope is not None else None
    try:
        yield
    finally:
        if secret_token is not None:
            reset_secret_scope(secret_token)
        reset_hermes_home_override(home_token)


@dataclass(frozen=True)
class WorkerResult:
    status: str
    job_id: str | None = None
    error_type: str | None = None
    retention_purged: int = 0


class _LeaseHeartbeat:
    """Keep the job and per-brain leases live during a bounded LLM call."""

    def __init__(
        self,
        store: CortexStore,
        *,
        job_id: str,
        owner: str,
        lease_seconds: int,
    ) -> None:
        self.store = store
        self.job_id = job_id
        self.owner = owner
        self.lease_seconds = lease_seconds
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"cortex-lease-{job_id[-8:]}",
            daemon=True,
        )

    def __enter__(self) -> "_LeaseHeartbeat":
        self._thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def _run(self) -> None:
        interval = max(5.0, min(60.0, self.lease_seconds / 3.0))
        while not self._stop.wait(interval):
            try:
                self.store.heartbeat_job(
                    self.job_id,
                    owner=self.owner,
                    lease_seconds=self.lease_seconds,
                )
                renewed = self.store.renew_named_lease(
                    "dream-cycle",
                    owner=self.owner,
                    lease_seconds=self.lease_seconds,
                )
                if not renewed:
                    self._lost.set()
                    return
            except Exception:
                logger.warning(
                    "Atlas Cortex lost a dream-cycle lease heartbeat",
                    exc_info=True,
                )
                self._lost.set()
                return


class CortexDreamWorker:
    """Lease and process at most one durable job per ``run_once`` call."""

    def __init__(
        self,
        store: CortexStore,
        config: CortexConfig,
        *,
        processor: DreamProcessor | None = None,
        raw_config: Mapping[str, Any] | None = None,
        environ: Mapping[str, str] | None = None,
        llm_call: Any = None,
        owner: str | None = None,
        secret_scope: Mapping[str, str] | None | object = _UNSET_SCOPE,
    ) -> None:
        self.store = store
        self.config = config
        self.owner = owner or self._new_owner()
        self._secret_scope = (
            capture_profile_secret_scope(config.profile_home)
            if secret_scope is _UNSET_SCOPE
            else (dict(secret_scope) if secret_scope is not None else None)
        )
        self.processor = processor or DreamProcessor(
            store,
            config,
            raw_config=raw_config,
            environ=environ,
            llm_call=llm_call,
        )

    @staticmethod
    def _new_owner() -> str:
        host = socket.gethostname().split(".", 1)[0][:40] or "host"
        return f"cortex-{host}-{os.getpid()}-{uuid.uuid4().hex}"

    @contextmanager
    def runtime_scope(self):
        with cortex_profile_runtime_scope(
            self.config.profile_home,
            self._secret_scope,
        ):
            yield

    def run_once(
        self,
        *,
        job_types: tuple[str, ...] | None = None,
        prune_retention: bool = True,
    ) -> WorkerResult:
        with self.runtime_scope():
            return self._run_once(
                job_types=job_types,
                prune_retention=prune_retention,
                exact_dispatch=None,
            )

    def run_exact_managed_dispatch(
        self,
        admission: CortexDispatchAdmission,
    ) -> WorkerResult:
        """Process exactly one control-plane-bound local semantic job."""

        if not isinstance(admission, CortexDispatchAdmission):
            raise TypeError("admission must be a CortexDispatchAdmission")
        with self.runtime_scope():
            return self._run_once(
                job_types=MODEL_JOB_TYPES,
                prune_retention=True,
                exact_dispatch=admission,
            )

    def _run_once(
        self,
        *,
        job_types: tuple[str, ...] | None,
        prune_retention: bool,
        exact_dispatch: CortexDispatchAdmission | None,
    ) -> WorkerResult:
        retention_purged = 0
        if prune_retention:
            try:
                retention_purged = self.store.prune_raw_evidence(
                    self.config.raw_evidence_retention_days
                )
            except Exception as exc:
                logger.error(
                    "Atlas Cortex raw-evidence retention failed",
                    exc_info=True,
                )
                return WorkerResult(
                    "retention_failed",
                    error_type=type(exc).__name__,
                )
        if not self.config.dream_enabled:
            return WorkerResult("disabled", retention_purged=retention_purged)
        selected_job_types = tuple(job_types or SUPPORTED_JOB_TYPES)
        if not selected_job_types or any(
            job_type not in SUPPORTED_JOB_TYPES for job_type in selected_job_types
        ):
            raise ValueError("Cortex worker received an unsupported job type filter")
        if not self.store.acquire_named_lease(
            "dream-cycle",
            owner=self.owner,
            lease_seconds=self.config.dream_lease_seconds,
        ):
            return WorkerResult("locked", retention_purged=retention_purged)
        job: dict[str, Any] | None = None
        try:
            if exact_dispatch is None:
                job = self.store.lease_job(
                    job_types=selected_job_types,
                    owner=self.owner,
                    lease_seconds=self.config.dream_lease_seconds,
                )
            else:
                job = self.store.lease_exact_managed_job(
                    exact_dispatch,
                    owner=self.owner,
                    lease_seconds=self.config.dream_lease_seconds,
                )
            if job is None:
                return WorkerResult("idle", retention_purged=retention_purged)
            job_id = str(job["id"])
            try:
                with _LeaseHeartbeat(
                    self.store,
                    job_id=job_id,
                    owner=self.owner,
                    lease_seconds=self.config.dream_lease_seconds,
                ) as heartbeat:
                    outcome = self.processor.process(job, owner=self.owner)
                    if heartbeat.lost:
                        raise RuntimeError(
                            "Cortex dream lease was lost during processing"
                        )
                output = outcome.report.to_dict()
                output["retention_purged"] = retention_purged
                self.store.complete_job(
                    job_id,
                    owner=self.owner,
                    output=output,
                    model=outcome.model_label,
                    prompt_version=outcome.prompt_version,
                    input_tokens=outcome.input_tokens,
                    output_tokens=outcome.output_tokens,
                    cost_micros=outcome.cost_micros,
                )
                return WorkerResult(
                    "succeeded",
                    job_id=job_id,
                    retention_purged=retention_purged,
                )
            except Exception as exc:
                try:
                    self.processor.record_failure(job_id, exc)
                except Exception:
                    logger.warning(
                        "Atlas Cortex could not persist a failed health report",
                        exc_info=True,
                    )
                try:
                    self.store.fail_job(
                        job_id,
                        owner=self.owner,
                        error=type(exc).__name__,
                        retry=True,
                    )
                except Exception:
                    logger.warning(
                        "Atlas Cortex could not transition the failed job",
                        exc_info=True,
                    )
                return WorkerResult(
                    "failed",
                    job_id=job_id,
                    error_type=type(exc).__name__,
                    retention_purged=retention_purged,
                )
        finally:
            try:
                self.store.release_named_lease("dream-cycle", owner=self.owner)
            except Exception:
                logger.warning(
                    "Atlas Cortex could not release the dream-cycle lease",
                    exc_info=True,
                )

    def drain(
        self,
        *,
        max_jobs: int = 25,
        job_types: tuple[str, ...] | None = None,
    ) -> list[WorkerResult]:
        results: list[WorkerResult] = []
        for index in range(max(1, min(1_000, int(max_jobs)))):
            result = self.run_once(
                job_types=job_types,
                prune_retention=index == 0,
            )
            results.append(result)
            if result.status not in {"succeeded"}:
                break
        return results


class CortexDreamSupervisor:
    """Long-lived, profile-scoped local supervisor for approved routes."""

    def __init__(
        self,
        worker: CortexDreamWorker,
        *,
        poll_seconds: int,
        enqueue_due: Any = None,
        runtime_signature: str = "",
    ) -> None:
        self.worker = worker
        self.poll_seconds = max(30, min(3_600, int(poll_seconds)))
        self.enqueue_due = enqueue_due
        self.runtime_signature = runtime_signature
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"cortex-supervisor-{worker.store.brain_id[-8:]}",
            daemon=True,
        )

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    def wake(self) -> None:
        """Coalesce a profile-local notification for newly finalized work."""

        self._wake.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                # Thread context does not inherit the profile contextvars from
                # the turn that created it. Scope the complete supervisor
                # cycle, including config/model resolution inside the worker.
                with self.worker.runtime_scope():
                    if self.enqueue_due is not None:
                        self.enqueue_due()
                    self.worker.drain(max_jobs=25)
            except Exception:
                logger.warning("Atlas Cortex supervisor cycle failed", exc_info=True)
            self._wake.wait(self.poll_seconds)
            self._wake.clear()


__all__ = [
    "DETERMINISTIC_JOB_TYPES",
    "MODEL_JOB_TYPES",
    "CortexDreamSupervisor",
    "CortexDreamWorker",
    "SUPPORTED_JOB_TYPES",
    "WorkerResult",
    "capture_profile_secret_scope",
    "cortex_profile_runtime_scope",
]
