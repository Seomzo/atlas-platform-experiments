"""Daily enqueue, no-agent draining, and supervisor reconciliation for Cortex.

The persistent wake path never constructs the conversational agent. It runs
deterministic retention/recovery first and may resume only model jobs already
authorized by an immutable logical-session-end admission, after a
privacy-approved local route resolves. It cannot create a new semantic cycle.
Managed jobs remain queued for a freshly authorized managed worker.
"""

from __future__ import annotations

import atexit
import importlib.util
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, time as clock_time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import CortexConfig
from .dream import CortexRouteError, resolve_model_route
from .store import (
    SESSION_DISTILL_RECOVERY_CANDIDATE_ATTEMPTS,
    CortexStore,
    stable_hash,
)
from .worker import (
    DETERMINISTIC_JOB_TYPES,
    MODEL_JOB_TYPES,
    CortexDreamSupervisor,
    CortexDreamWorker,
    capture_profile_secret_scope,
    cortex_profile_runtime_scope,
)


logger = logging.getLogger(__name__)

SCHEDULE_VERSION = "atlas.cortex.schedule.v1"
WAKE_CRON_NAME = "Atlas Cortex Dream (system)"
WAKE_CRON_SCRIPT = "atlas_cortex_wake.py"
_WAKE_SCRIPT_MARKER = "# Atlas Cortex managed wake v1"
MULTIPLEX_WAKE_INTERVAL_SECONDS = 15 * 60
_MANAGED_BINDING_NAMES = (
    "ATLAS_CONTROL_PLANE_URL",
    "ATLAS_CUSTOMER_ID",
    "ATLAS_DEVICE_TOKEN",
    "ATLAS_DEVICE_ID",
    "ATLAS_TENANT_ID",
    "ATLAS_STORE_ID",
    "ATLAS_AGENT_ID",
    "ATLAS_MODEL_ID",
    "ATLAS_MANAGED_MODE",
)
_RUNTIMES: dict[str, Any] = {}
_RUNTIME_LOCK = threading.RLock()
_CRON_MODULES: dict[str, Any] = {}
_CRON_MODULE_LOCK = threading.RLock()


def _timezone(name: str) -> Any:
    normalized = str(name or "local").strip()
    if normalized.lower() in {"", "local", "system"}:
        return datetime.now().astimezone().tzinfo or timezone.utc
    try:
        return ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown Cortex timezone: {normalized}") from exc


def _scheduled_time(value: str) -> clock_time:
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
        return clock_time(hour=hour, minute=minute)
    except (TypeError, ValueError) as exc:
        raise ValueError("Cortex dream.local_time must be HH:MM") from exc


class CortexDreamSchedule:
    def __init__(self, store: CortexStore, config: CortexConfig) -> None:
        self.store = store
        self.config = config

    def enqueue_due(
        self,
        *,
        now: datetime | None = None,
        startup: bool = False,
    ) -> str | None:
        """Enqueue one deterministic daily recovery marker.

        This job never invokes a model. Semantic work is created only for a
        finalized logical conversation by ``finalize_session`` or recovery of
        that same scoped lineage.
        """
        if not self.config.dream_enabled:
            return None
        tz = _timezone(self.config.timezone)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            raise ValueError("Cortex scheduler requires a timezone-aware datetime")
        local_now = current.astimezone(tz)
        scheduled = _scheduled_time(self.config.dream_local_time)
        today_slot = datetime.combine(local_now.date(), scheduled, tzinfo=tz)
        if local_now >= today_slot:
            slot = today_slot
        elif startup and self.config.dream_startup_catchup:
            slot = datetime.combine(
                local_now.date() - timedelta(days=1), scheduled, tzinfo=tz
            )
        else:
            return None
        slot_utc = slot.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        input_hash = stable_hash(self.store.brain_id, SCHEDULE_VERSION, slot_utc)
        with self.store.connect() as connection:
            exists = connection.execute(
                "SELECT id FROM cognitive_jobs WHERE brain_id=? AND job_type='dream_cycle' "
                "AND input_hash=?",
                (self.store.brain_id, input_hash),
            ).fetchone()
        if exists:
            return None
        return self.store.enqueue_job(
            "dream_cycle",
            input_hash=input_hash,
            input_data={
                "scheduled_slot": slot_utc,
                "local_date": slot.date().isoformat(),
                "timezone": self.config.timezone,
                "schedule_version": SCHEDULE_VERSION,
            },
            scheduled_at=slot_utc,
        )

    def enqueue_recovery_jobs(self, *, limit: int = 100) -> tuple[str, ...]:
        """Recover only work authorized by an immutable boundary admission."""
        bounded = max(1, min(1_000, int(limit)))
        recovered = list(
            self.store.recover_missing_session_distill_roots(limit=bounded)
        )
        if len(recovered) >= bounded:
            return tuple(recovered)
        page = self.store.session_distill_recovery_page()
        admissions = page["admissions"]
        jobs = page["jobs"]
        jobs_by_admission: dict[str, list[Mapping[str, Any]]] = {}
        for job in jobs:
            jobs_by_admission.setdefault(str(job["admission_id"]), []).append(job)

        def repair_ineligible_candidate(
            admission_id: str,
            candidate: Mapping[str, Any] | None,
            attempts: int,
        ) -> tuple[Mapping[str, Any] | None, int, bool]:
            while (
                candidate is not None
                and str(candidate["state"]) in {"queued", "running"}
                and not bool(int(candidate.get("recovery_eligible", 1)))
            ):
                if attempts >= SESSION_DISTILL_RECOVERY_CANDIDATE_ATTEMPTS:
                    return candidate, attempts, False
                candidate_id = str(candidate["id"])
                repaired = self.store.repair_ineligible_session_distill_candidate(
                    candidate_id
                )
                attempts += 1
                replacement = self.store.session_distill_recovery_candidate(
                    admission_id
                )
                if (
                    not repaired
                    and replacement is not None
                    and str(replacement["id"]) == candidate_id
                    and str(replacement["state"]) in {"queued", "running"}
                    and not bool(int(replacement.get("recovery_eligible", 1)))
                ):
                    return replacement, attempts, False
                candidate = replacement
            return candidate, attempts, True

        page_complete = True
        for admission in admissions:
            try:
                input_data = json.loads(admission["snapshot_json"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(input_data, dict):
                continue
            admission_jobs = jobs_by_admission.get(str(admission["id"]), [])
            candidate: Mapping[str, Any] | None = (
                admission_jobs[0] if admission_jobs else None
            )
            candidate_attempts = 0
            candidate, candidate_attempts, candidate_ready = (
                repair_ineligible_candidate(
                    str(admission["id"]), candidate, candidate_attempts
                )
            )
            if not candidate_ready:
                page_complete = False
                continue
            if candidate is None or str(candidate["state"]) in {"queued", "running"}:
                continue
            if str(candidate["state"]) != "succeeded":
                continue
            session_ids = tuple(
                str(value) for value in input_data.get("session_ids", [])
            )
            due = self.store.pending_observations(
                limit=1_000,
                session_ids=session_ids,
                evidence_ids=tuple(
                    str(value) for value in input_data.get("evidence_ids", [])
                ),
                knowledge_space="personal",
            )
            if not due:
                continue
            due_signature = [
                f"{item['id']}:{item.get('triage_attempts', 0)}:"
                f"{item.get('next_triage_at') or ''}"
                for item in due
            ]
            while candidate is not None and str(candidate["state"]) == "succeeded":
                if len(recovered) >= bounded:
                    # Do not acknowledge a page containing actionable work we
                    # intentionally deferred to honor the per-wake output cap.
                    page_complete = False
                    break
                candidate_id = str(candidate["id"])
                try:
                    job_id = self.store.enqueue_session_distill_continuation(
                        candidate_id,
                        remaining_signature=due_signature,
                        recovered=True,
                    )
                except (LookupError, PermissionError, TypeError, ValueError):
                    if not self.store.quarantine_invalid_session_distill_job(
                        candidate_id
                    ):
                        replacement = self.store.session_distill_recovery_candidate(
                            str(admission["id"])
                        )
                        if replacement is not None and str(replacement["state"]) in {
                            "queued",
                            "running",
                        }:
                            break
                        raise
                    candidate_attempts += 1
                    candidate = self.store.session_distill_recovery_candidate(
                        str(admission["id"])
                    )
                    candidate, candidate_attempts, candidate_ready = (
                        repair_ineligible_candidate(
                            str(admission["id"]), candidate, candidate_attempts
                        )
                    )
                    if not candidate_ready:
                        page_complete = False
                        break
                    if (
                        candidate_attempts
                        >= SESSION_DISTILL_RECOVERY_CANDIDATE_ATTEMPTS
                        and candidate is not None
                        and str(candidate["state"]) == "succeeded"
                    ):
                        # Invalid branches are repaired incrementally without
                        # allowing a corrupted fanout to monopolize a wake.
                        page_complete = False
                        break
                    continue
                recovered.append(job_id)
                break
        if page_complete:
            self.store.ack_session_distill_recovery_page(page["cursor_token"])
        return tuple(recovered)


def enqueue_cortex_wake(
    store: CortexStore,
    config: CortexConfig,
    *,
    now: datetime | None = None,
    startup: bool = False,
) -> None:
    """Run deterministic wake maintenance without constructing an agent."""
    # Retention is a deterministic privacy boundary. It must not depend on a
    # configured model route, a successful model call, or a non-empty queue.
    store.prune_raw_evidence(config.raw_evidence_retention_days)
    if not config.dream_enabled:
        return
    schedule = CortexDreamSchedule(store, config)
    schedule.enqueue_recovery_jobs()
    schedule.enqueue_due(now=now, startup=startup)


def run_cortex_wake(
    store: CortexStore,
    config: CortexConfig,
    *,
    now: datetime | None = None,
    startup: bool = False,
    max_jobs: int = 25,
) -> None:
    """Wake, reconcile, and locally drain work without constructing ``AIAgent``.

    Rewind reconciliation and retention are deterministic and always safe to
    run. Previously admitted session-end jobs are leased only after a concrete
    route resolves; this wake path cannot authorize model work on its own.
    Managed routes are deliberately left queued for the request-authorized
    managed worker: a persistent cron process must never retain or replay an
    expired request credential.
    """
    enqueue_cortex_wake(store, config, now=now, startup=startup)
    if not config.dream_enabled:
        return

    worker = CortexDreamWorker(store, config)
    worker.drain(max_jobs=max_jobs, job_types=DETERMINISTIC_JOB_TYPES)
    with worker.runtime_scope():
        try:
            route = resolve_model_route("cortex_triage")
        except CortexRouteError:
            return
    if route.managed_approved:
        return
    worker.drain(max_jobs=max_jobs, job_types=MODEL_JOB_TYPES)


def _wake_script(profile_home: Path) -> str:
    return (
        f"{_WAKE_SCRIPT_MARKER}\n"
        "# No-agent maintenance plus recovery of already-admitted session-end jobs.\n"
        "import os\n"
        "from pathlib import Path\n"
        f"os.environ['HERMES_HOME'] = {str(profile_home)!r}\n"
        "os.environ['HERMES_PUBLIC_BRAND'] = 'atlas'\n"
        "from agent.secret_scope import build_profile_secret_scope, set_secret_scope\n"
        "from altas.cortex.config import CortexConfig\n"
        "from altas.cortex.runtime import open_cortex_store\n"
        "from altas.cortex.scheduler import run_cortex_wake\n"
        "home = os.environ['HERMES_HOME']\n"
        "set_secret_scope(build_profile_secret_scope(Path(home)))\n"
        "config = CortexConfig.load(home)\n"
        "if config.enabled:\n"
        "    store, config = open_cortex_store(home)\n"
        "    run_cortex_wake(store, config, startup=False)\n"
    )


def _profile_cron_jobs(profile_home: Path) -> Any:
    """Return a cron.jobs module whose frozen paths belong to one profile.

    ``cron.jobs`` intentionally freezes its storage constants at import time.
    A multiplex gateway may already have imported it for another profile, so
    merely installing a context-local HERMES_HOME override cannot redirect
    those constants. Load one isolated module instance per target profile;
    its public API and cross-process file lock remain unchanged, while its
    module globals cannot point at another customer's jobs file.
    """
    import cron.jobs as canonical_jobs

    home = profile_home.expanduser().resolve()
    expected = (home / "cron" / "jobs.json").resolve()
    if Path(canonical_jobs.JOBS_FILE).resolve() == expected:
        return canonical_jobs

    key = str(home)
    with _CRON_MODULE_LOCK:
        existing = _CRON_MODULES.get(key)
        if existing is not None:
            return existing
        module_path = Path(canonical_jobs.__file__).resolve()
        module_name = f"cron._atlas_cortex_jobs_{stable_hash(key)[:20]}"
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load profile-scoped cron storage")
        module = importlib.util.module_from_spec(spec)
        secret_scope = capture_profile_secret_scope(home)
        sys.modules[module_name] = module
        try:
            with cortex_profile_runtime_scope(home, secret_scope):
                spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
        if Path(module.JOBS_FILE).resolve() != expected:
            sys.modules.pop(module_name, None)
            raise RuntimeError("profile-scoped cron storage resolved the wrong home")
        _CRON_MODULES[key] = module
        return module


def ensure_cortex_wake_cron(config: CortexConfig) -> str | None:
    """Install one profile-scoped, no-agent recovery and retention wake.

    The cron fires every fifteen minutes so Cortex's own timezone-aware due
    calculation remains authoritative even when the configured timezone is
    not the machine timezone. Unique slot hashes produce one deterministic
    daily integrity marker; semantic work is still session-end-only. Existing
    user-authored scripts are never overwritten.
    """
    if not config.enabled:
        return None
    home = config.profile_home.expanduser().resolve()
    scripts = home / "scripts"
    if scripts.exists() and scripts.is_symlink():
        raise RuntimeError("Atlas Cortex cron scripts directory must not be a symlink")
    scripts.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = scripts / WAKE_CRON_SCRIPT
    if target.exists() and target.is_symlink():
        raise RuntimeError("Atlas Cortex wake script must not be a symlink")
    expected = _wake_script(home)
    if target.exists():
        current = target.read_text(encoding="utf-8")
        if not current.startswith(_WAKE_SCRIPT_MARKER):
            raise RuntimeError(f"refusing to overwrite non-Cortex script at {target}")
    else:
        current = ""
    if current != expected:
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        temporary.write_text(expected, encoding="utf-8")
        temporary.chmod(0o700)
        os.replace(temporary, target)
    try:
        target.chmod(0o700)
    except OSError:
        pass

    secret_scope = capture_profile_secret_scope(home)
    with cortex_profile_runtime_scope(home, secret_scope):
        cron_jobs = _profile_cron_jobs(home)
        jobs = cron_jobs.list_jobs(include_disabled=True)
        for job in jobs:
            if str(job.get("name") or "") == WAKE_CRON_NAME:
                # A disabled reserved job is an explicit user opt-out. Leave it
                # disabled instead of silently recreating or re-enabling it.
                return str(job.get("id") or "") or None
        job = cron_jobs.create_job(
            prompt=None,
            schedule="*/15 * * * *",
            name=WAKE_CRON_NAME,
            deliver="local",
            script=WAKE_CRON_SCRIPT,
            no_agent=True,
            attach_to_session=False,
        )
    return str(job["id"])


def _reserved_wake_job_enabled(profile_home: Path) -> bool:
    """Return whether this profile explicitly permits the native wake.

    The multiplex ticker is deliberately narrower than the generic cron
    scheduler: it only recognizes the exact no-agent job installed by Cortex.
    A paused job is an operator opt-out, and a same-name user-authored job is
    not treated as authorization to run Cortex maintenance directly.
    """
    cron_jobs = _profile_cron_jobs(profile_home)
    for job in cron_jobs.list_jobs(include_disabled=True):
        if str(job.get("name") or "") != WAKE_CRON_NAME:
            continue
        if (
            job.get("no_agent") is not True
            or str(job.get("script") or "") != WAKE_CRON_SCRIPT
        ):
            logger.warning(
                "Ignoring non-reserved cron job named %s in Cortex profile %s",
                WAKE_CRON_NAME,
                profile_home,
            )
            return False
        return (
            bool(job.get("enabled", True))
            and str(job.get("state") or "scheduled").lower() != "paused"
        )
    return False


def run_multiplex_cortex_wake_tick(
    profiles: Iterable[tuple[str, Path]],
    *,
    now: datetime | None = None,
    startup: bool = False,
    max_jobs: int = 1,
    stop_event: threading.Event | None = None,
) -> tuple[str, ...]:
    """Run one isolated no-agent wake for each eligible served profile.

    Profile enumeration belongs to the gateway, while all config, cron,
    database, and credential resolution happens inside that profile's runtime
    scope.  Credentials are rebuilt for the individual call and are never
    retained by the long-lived ticker.  ``run_cortex_wake`` itself refuses to
    drain managed model jobs, so a request-scoped managed claim can never be
    replayed by this persistent path.
    """
    awakened: list[str] = []
    seen_homes: set[Path] = set()
    for profile_name, raw_home in profiles:
        if stop_event is not None and stop_event.is_set():
            break
        home = Path(raw_home).expanduser().resolve()
        if home in seen_homes:
            continue
        seen_homes.add(home)
        try:
            # Always rebuild from the requested profile. In multiplex mode,
            # capture_profile_secret_scope intentionally ignores any current
            # request's context-local authorization mapping.
            secret_scope = capture_profile_secret_scope(home)
            with cortex_profile_runtime_scope(home, secret_scope):
                config = CortexConfig.load(home)
                if not config.enabled or not _reserved_wake_job_enabled(home):
                    continue
                from .runtime import open_cortex_store

                store, config = open_cortex_store(home)
                run_cortex_wake(
                    store,
                    config,
                    now=now,
                    startup=startup,
                    max_jobs=max_jobs,
                )
            awakened.append(str(profile_name or home.name))
        except Exception:
            # One corrupt/read-only profile must not starve the rest of a
            # customer's multiplexed gateway. Avoid logging config or secret
            # values; the resolved profile path is sufficient diagnostics.
            logger.warning(
                "Atlas Cortex multiplex wake failed for profile %s (%s)",
                profile_name,
                home,
                exc_info=True,
            )
    return tuple(awakened)


def run_multiplex_cortex_wake_ticker(
    stop_event: threading.Event,
    *,
    interval: float = MULTIPLEX_WAKE_INTERVAL_SECONDS,
    profiles_provider: Callable[[], Iterable[tuple[str, Path]]] | None = None,
    max_jobs: int = 1,
) -> None:
    """Continuously enumerate served profiles and run their native wakes.

    This is a blocking ticker entry point: the gateway owns exactly one daemon
    thread for it and supplies the lifecycle event.  Enumeration is repeated
    every cycle so profiles added between gateway restarts are not silently
    omitted.  The first pass enables configured startup catch-up.
    """
    if profiles_provider is None:
        from hermes_cli.profiles import profiles_to_serve

        profiles_provider = lambda: profiles_to_serve(multiplex=True)
    bounded_interval = max(0.01, float(interval))
    startup = True
    logger.info(
        "Atlas Cortex multiplex wake ticker started (interval=%ss)",
        f"{bounded_interval:g}",
    )
    while not stop_event.is_set():
        started = time.monotonic()
        try:
            run_multiplex_cortex_wake_tick(
                profiles_provider(),
                startup=startup,
                max_jobs=max_jobs,
                stop_event=stop_event,
            )
        except BaseException as exc:
            # Match the generic cron ticker's resilience: a bad provider or a
            # SystemExit raised by profile code must not kill scheduling for
            # every other profile without a visible error.
            logger.error(
                "Atlas Cortex multiplex wake tick failed: %s",
                exc,
                exc_info=True,
            )
        startup = False
        elapsed = max(0.0, time.monotonic() - started)
        stop_event.wait(max(0.01, bounded_interval - elapsed))
    logger.info("Atlas Cortex multiplex wake ticker stopped")


def _runtime_key(store: CortexStore) -> str:
    return f"{store.path.resolve()}::{store.brain_id}"


def _managed_profile_binding(profile_home: str | Path) -> dict[str, str]:
    """Return only stable device binding fields for a managed supervisor.

    Request claims and conversational-provider credentials are intentionally
    excluded. In a single-profile deployment, process-level binding values
    take precedence over the profile file; a multiplexed gateway may read only
    the requested profile's isolated ``.env`` mapping.
    """
    from agent.secret_scope import build_profile_secret_scope, is_multiplex_active

    profile = build_profile_secret_scope(Path(profile_home))
    binding = {
        name: str(profile.get(name) or "").strip()
        for name in _MANAGED_BINDING_NAMES
        if str(profile.get(name) or "").strip()
    }
    if not is_multiplex_active():
        for name in _MANAGED_BINDING_NAMES:
            value = str(os.environ.get(name) or "").strip()
            if value:
                binding[name] = value
    return binding


def _binding_selects_managed_runtime(binding: Mapping[str, str]) -> bool:
    explicit = str(binding.get("ATLAS_MANAGED_MODE") or "").lower()
    if explicit in {"1", "true", "yes", "on"}:
        return True
    return all(
        str(binding.get(name) or "").strip()
        for name in ("ATLAS_TENANT_ID", "ATLAS_STORE_ID", "ATLAS_AGENT_ID")
    )


def _stop_runtime(key: str) -> None:
    with _RUNTIME_LOCK:
        supervisor = _RUNTIMES.pop(key, None)
    if supervisor is not None:
        try:
            supervisor.stop(timeout=2.0)
        except Exception:
            logger.debug("Atlas Cortex stale supervisor stop failed", exc_info=True)


def _runtime_signature(
    config: CortexConfig,
    raw_config: Any,
    route: Any,
    secret_scope: Any,
) -> str:
    secret_fingerprint = stable_hash(
        *(f"{key}={value}" for key, value in sorted((secret_scope or {}).items()))
    )
    return stable_hash(
        config.profile_home,
        config.database_path,
        config.timezone,
        config.dream_local_time,
        config.dream_startup_catchup,
        config.dream_poll_seconds,
        config.dream_lease_seconds,
        config.dream_max_batch,
        config.raw_evidence_retention_days,
        repr(raw_config),
        route.provider,
        route.model,
        route.base_url,
        route.api_mode,
        route.timeout,
        route.max_tokens,
        secret_fingerprint,
    )


class _ManagedCortexSupervisor:
    """Profile-local CP poller for request-authorized Cortex maintenance.

    Atlas starts this from gateway recovery or the normal agent runtime when a
    profile has a complete managed device binding. Each semantic unit still
    receives a fresh server lease/claim; the long-lived thread retains only
    the device credential needed to request those short-lived authorizations.
    """

    def __init__(
        self,
        *,
        store: CortexStore,
        profile_home: Path,
        environment: Mapping[str, str],
        poll_seconds: int,
        runtime_signature: str,
    ) -> None:
        from altas.managed.worker import AltasWorker, WorkerSettings

        def required(name: str) -> str:
            value = str(environment.get(name) or "").strip()
            if not value:
                raise ValueError(f"{name} is required for managed Cortex")
            return value

        settings = WorkerSettings(
            control_plane_url=str(
                environment.get("ATLAS_CONTROL_PLANE_URL") or "http://127.0.0.1:8787"
            ).rstrip("/"),
            device_token=required("ATLAS_DEVICE_TOKEN"),
            device_id=required("ATLAS_DEVICE_ID"),
            tenant_id=required("ATLAS_TENANT_ID"),
            store_id=required("ATLAS_STORE_ID"),
            agent_id=required("ATLAS_AGENT_ID"),
            model_id=str(environment.get("ATLAS_MODEL_ID") or "altas-fixed-ops"),
            profile_home=profile_home,
            poll_interval_seconds=max(1.0, float(poll_seconds)),
        )
        self.store = store
        self.worker = AltasWorker(settings)
        self.runtime_signature = runtime_signature
        self.poll_seconds = max(30, min(3_600, int(poll_seconds)))
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = threading.Thread(
            target=self._run,
            name=f"cortex-managed-{settings.agent_id[-8:]}",
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
        self._wake.set()

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    # A cycle can create and complete one freshly authorized
                    # maintenance job. Drain a small burst so continuations do
                    # not wait for the next polling interval.
                    for _ in range(25):
                        result = self.worker.run_once(
                            capability_filter="cortex.memory_maintenance"
                        )
                        if result.status == "failed":
                            if not self._stop.is_set():
                                self.store.record_health_event(
                                    status="degraded",
                                    code="managed_worker_failure",
                                    details={"error_type": "ManagedWorkerCycleFailed"},
                                )
                            break
                        if result.status not in {"succeeded"}:
                            break
                    else:
                        result = None
                    if not self._stop.is_set() and (
                        result is None or result.status != "failed"
                    ):
                        self.store.resolve_health_event("managed_worker_failure")
                except Exception as exc:
                    logger.warning(
                        "Atlas managed Cortex supervisor cycle failed",
                        exc_info=True,
                    )
                    if not self._stop.is_set():
                        try:
                            self.store.record_health_event(
                                status="degraded",
                                code="managed_worker_failure",
                                details={"error_type": type(exc).__name__},
                            )
                        except Exception:
                            logger.warning(
                                "Atlas managed Cortex health persistence failed",
                                exc_info=True,
                            )
                self._wake.wait(self.poll_seconds)
                self._wake.clear()
        finally:
            self.worker.close()


def reconcile_cortex_runtime(
    store: CortexStore | None,
    config: CortexConfig | None,
) -> Any:
    """Idempotently enqueue catch-up work and ensure one local supervisor."""
    if store is None:
        return None
    key = _runtime_key(store)
    if config is None:
        _stop_runtime(key)
        return None
    # Retention remains enforceable when dreams are disabled or their model
    # route is temporarily unavailable.
    store.prune_raw_evidence(config.raw_evidence_retention_days)
    try:
        ensure_cortex_wake_cron(config)
    except Exception:
        # The in-process supervisor and startup catch-up still provide safe
        # progress if cron storage is unavailable or read-only.
        logger.warning("Atlas Cortex no-agent wake installation failed", exc_info=True)
    if not config.dream_enabled:
        _stop_runtime(key)
        for code in (
            "model_route_unavailable",
            "managed_worker_not_configured",
            "managed_worker_failure",
        ):
            store.resolve_health_event(code)
        return None
    schedule = CortexDreamSchedule(store, config)
    schedule.enqueue_recovery_jobs()
    schedule.enqueue_due(startup=True)
    managed_store = store.owner_customer_id.startswith("managed:")
    if managed_store:
        # Never retain a request-local lease/claim in the persistent poller.
        # Rebuild only the stable device binding; chat credentials and any
        # accidentally persisted request authorization are excluded.
        secret_scope = _managed_profile_binding(config.profile_home)
    else:
        secret_scope = capture_profile_secret_scope(config.profile_home)
    with cortex_profile_runtime_scope(config.profile_home, secret_scope):
        try:
            from hermes_cli.config import load_config

            raw_config = load_config()
            if managed_store:
                # Validate the configured managed route without borrowing a
                # real request authorization. These placeholders never leave
                # this function or enter a model request; the native poller
                # obtains fresh server-issued values for each unit.
                validation_environment = {
                    **dict(secret_scope or {}),
                    "ATLAS_MANAGED_MODE": "1",
                    "ATLAS_LEASE_TOKEN": "route-validation-lease",
                    "ATLAS_JOB_ID": "route-validation-job",
                    "ATLAS_CLAIM_TOKEN": "route-validation-claim-token-00000000",
                    "ATLAS_JOB_CAPABILITY": "cortex.memory_maintenance",
                }
                route = resolve_model_route(
                    "cortex_triage",
                    config=raw_config,
                    environ=validation_environment,
                )
            else:
                route = resolve_model_route("cortex_triage", config=raw_config)
        except CortexRouteError as exc:
            store.record_health_event(
                status="degraded",
                code="model_route_unavailable",
                details={"error_type": type(exc).__name__},
            )
            _stop_runtime(key)
            return None
    store.resolve_health_event("model_route_unavailable")
    # Managed semantic calls run through the same native supervisor surface,
    # but each unit is admitted by the control plane under a fresh job claim.
    if route.managed_approved:
        signature = _runtime_signature(config, raw_config, route, secret_scope)
        managed_environment = {
            name: str((secret_scope or {}).get(name) or "")
            for name in _MANAGED_BINDING_NAMES
        }
        try:
            supervisor = _ManagedCortexSupervisor(
                store=store,
                profile_home=config.profile_home,
                environment=managed_environment,
                poll_seconds=config.dream_poll_seconds,
                runtime_signature=signature,
            )
        except (TypeError, ValueError):
            store.record_health_event(
                status="degraded",
                code="managed_worker_not_configured",
                details={"required": "managed device binding"},
            )
            _stop_runtime(key)
            return None
        store.resolve_health_event("managed_worker_not_configured")
        with _RUNTIME_LOCK:
            current = _RUNTIMES.get(key)
            if (
                current is not None
                and current.running
                and current.runtime_signature == signature
            ):
                # The newly constructed candidate owns a client; close it.
                supervisor.worker.close()
                return current
            if current is not None:
                _RUNTIMES.pop(key, None)
                current.stop(timeout=2.0)
            _RUNTIMES[key] = supervisor
            supervisor.start()
            return supervisor
    signature = _runtime_signature(config, raw_config, route, secret_scope)
    with _RUNTIME_LOCK:
        current = _RUNTIMES.get(key)
        if (
            current is not None
            and current.running
            and current.runtime_signature == signature
        ):
            store.resolve_health_event("managed_worker_not_configured")
            store.resolve_health_event("managed_worker_failure")
            return current
        if current is not None:
            _RUNTIMES.pop(key, None)
            try:
                current.stop(timeout=2.0)
            except Exception:
                logger.debug(
                    "Atlas Cortex reconfigured supervisor stop failed",
                    exc_info=True,
                )
        # Resolve managed-only failures after the old poller has stopped so a
        # late in-flight cycle cannot overwrite the local-route recovery.
        store.resolve_health_event("managed_worker_not_configured")
        store.resolve_health_event("managed_worker_failure")
        worker = CortexDreamWorker(
            store,
            config,
            raw_config=raw_config,
            secret_scope=secret_scope,
        )

        def enqueue_recovery() -> None:
            schedule.enqueue_recovery_jobs()
            schedule.enqueue_due(startup=False)

        supervisor = CortexDreamSupervisor(
            worker,
            poll_seconds=config.dream_poll_seconds,
            enqueue_due=enqueue_recovery,
            runtime_signature=signature,
        )
        _RUNTIMES[key] = supervisor
        supervisor.start()
        return supervisor


def recover_managed_cortex_runtimes(
    profiles: Iterable[tuple[str, Path]],
    *,
    stop_event: threading.Event | None = None,
) -> tuple[str, ...]:
    """Start managed Cortex claim pollers once at gateway startup.

    This path does not claim or execute a semantic job itself. It reconstructs
    each served profile from its stable device binding and starts the native
    supervisor, whose every model unit still requires a new control-plane
    lease and exact ``cortex.memory_maintenance`` job claim. Local profiles are
    left to their normal foreground/no-agent lifecycle.
    """
    recovered: list[str] = []
    seen_homes: set[Path] = set()
    for profile_name, raw_home in profiles:
        if stop_event is not None and stop_event.is_set():
            break
        home = Path(raw_home).expanduser().resolve()
        if home in seen_homes:
            continue
        seen_homes.add(home)
        try:
            stable_binding = _managed_profile_binding(home)
            if not _binding_selects_managed_runtime(stable_binding):
                continue
            with cortex_profile_runtime_scope(home, stable_binding):
                config = CortexConfig.load(home)
                if not config.enabled:
                    continue
                from .runtime import open_cortex_store

                store, config = open_cortex_store(home, environ=stable_binding)
                if not store.owner_customer_id.startswith("managed:"):
                    continue
                supervisor = reconcile_cortex_runtime(store, config)
            if supervisor is not None:
                recovered.append(str(profile_name or home.name))
        except Exception:
            # One bad profile or temporarily unavailable binding must not stop
            # the gateway or suppress recovery for another managed customer.
            logger.warning(
                "Atlas managed Cortex startup recovery failed for profile %s (%s)",
                profile_name,
                home,
                exc_info=True,
            )
    return tuple(recovered)


def stop_cortex_runtimes() -> None:
    with _RUNTIME_LOCK:
        supervisors = list(_RUNTIMES.values())
        _RUNTIMES.clear()
    for supervisor in supervisors:
        try:
            supervisor.stop(timeout=2.0)
        except Exception:
            logger.debug("Atlas Cortex supervisor stop failed", exc_info=True)


atexit.register(stop_cortex_runtimes)


__all__ = [
    "CortexDreamSchedule",
    "MULTIPLEX_WAKE_INTERVAL_SECONDS",
    "SCHEDULE_VERSION",
    "WAKE_CRON_NAME",
    "WAKE_CRON_SCRIPT",
    "enqueue_cortex_wake",
    "ensure_cortex_wake_cron",
    "recover_managed_cortex_runtimes",
    "reconcile_cortex_runtime",
    "run_cortex_wake",
    "run_multiplex_cortex_wake_tick",
    "run_multiplex_cortex_wake_ticker",
    "stop_cortex_runtimes",
]
