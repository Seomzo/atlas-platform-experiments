from __future__ import annotations

from typing import Any

import pytest

from altas.managed.client import Lease, PolicyDecision
from altas.managed.worker import AltasWorker, WorkerSettings


def _finalize_semantic_job(store, session_id: str) -> str:
    from altas.cortex.models import EvidenceInput

    store.ensure_session(session_id)
    store.append_evidence(
        session_id,
        EvidenceInput(
            source_type="user_message",
            content="Durable managed-worker session boundary.",
            source_locator=f"{session_id}:user:1",
        ),
    )
    store.finalize_session(session_id)
    with store.connect() as connection:
        row = connection.execute(
            "SELECT id FROM cognitive_jobs WHERE brain_id=? "
            "AND job_type='session_distill' ORDER BY created_at DESC LIMIT 1",
            (store.brain_id,),
        ).fetchone()
    assert row is not None
    return str(row["id"])


class RecordingClient:
    def __init__(self) -> None:
        self.heartbeat_calls = 0
        self.completions: list[dict[str, Any]] = []
        self.policy_calls: list[dict[str, Any]] = []
        self.chat_calls: list[dict[str, Any]] = []
        self.ensure_calls: list[dict[str, Any]] = []
        self.capabilities: tuple[str, ...] = ()
        self.job_capability = "fixed_ops.daily_report"
        self.claimed_by_device_id: str | None = "device-1"
        self.job_authorization_lease: Lease | None = None

    def heartbeat(self, **_kwargs: Any) -> Lease:
        self.heartbeat_calls += 1
        return Lease(
            token=f"lease-{self.heartbeat_calls}",
            expires_at="2099-01-01T00:00:00Z",
            capabilities=self.capabilities,
        )

    def next_job(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "id": "job-worker-renewal",
            "tenant_id": "tenant-1",
            "store_id": "store-sunrise-vw",
            "agent_id": "agent-1",
            "claimed_by_device_id": self.claimed_by_device_id,
            "capability": self.job_capability,
            "claim_token": "claim-token-with-more-than-thirty-two-characters",
            "payload": {"report_date": "2026-07-08"},
        }

    def ensure_cortex_maintenance(self, **kwargs: Any) -> dict[str, Any]:
        self.ensure_calls.append(kwargs)
        return {"job_id": "job-worker-renewal", "created": True}

    def take_job_authorization_lease(self) -> Lease | None:
        lease = self.job_authorization_lease
        self.job_authorization_lease = None
        return lease

    def evaluate_policy(self, **kwargs: Any) -> PolicyDecision:
        self.policy_calls.append(kwargs)
        return PolicyDecision(allowed=True, reason_code="allowed")

    def chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        self.chat_calls.append(kwargs)
        return {
            "choices": [
                {"message": {"content": "Synthetic fixed-ops summary for test data."}}
            ]
        }

    def complete_job(self, **kwargs: Any) -> dict[str, Any]:
        self.completions.append(kwargs)
        return {"job": {"status": kwargs["status"]}}


@pytest.mark.parametrize("execution_fails", [False, True])
def test_worker_renews_lease_before_success_or_failure_completion(
    monkeypatch: pytest.MonkeyPatch,
    execution_fails: bool,
) -> None:
    client = RecordingClient()
    worker = AltasWorker(
        WorkerSettings(
            control_plane_url="http://control-plane.invalid",
            device_token="device-token",
            device_id="device-1",
            tenant_id="tenant-1",
            store_id="store-sunrise-vw",
            agent_id="agent-1",
        ),
        client=client,  # type: ignore[arg-type]
    )

    def execute(**_kwargs: Any) -> dict[str, Any]:
        if execution_fails:
            raise RuntimeError("raw details must not escape")
        return {"report": "complete"}

    monkeypatch.setattr(worker, "_execute_job", execute)
    result = worker.run_once()

    assert client.heartbeat_calls == 2
    assert len(client.completions) == 1
    assert client.completions[0]["lease"] == "lease-2"
    assert client.completions[0]["status"] == (
        "failed" if execution_fails else "succeeded"
    )
    assert result.status == ("failed" if execution_fails else "succeeded")


def test_worker_passes_current_claim_to_policy_and_model_client() -> None:
    client = RecordingClient()
    worker = AltasWorker(
        WorkerSettings(
            control_plane_url="http://control-plane.invalid",
            device_token="device-token",
            device_id="device-1",
            tenant_id="tenant-1",
            store_id="store-sunrise-vw",
            agent_id="agent-1",
        ),
        client=client,  # type: ignore[arg-type]
    )

    result = worker.run_once()

    expected = "claim-token-with-more-than-thirty-two-characters"
    assert result.status == "succeeded"
    assert client.policy_calls[0]["claim_token"] == expected
    assert client.chat_calls[0]["claim_token"] == expected
    assert client.completions[0]["claim_token"] == expected


def test_worker_uses_claim_bound_job_lease_for_policy_and_execution() -> None:
    client = RecordingClient()
    client.job_authorization_lease = Lease(
        token="longer-job-bound-lease",
        expires_at="2099-01-01T00:00:00Z",
        capabilities=("fixed_ops.daily_report", "model.chat"),
    )
    worker = AltasWorker(
        WorkerSettings(
            control_plane_url="http://control-plane.invalid",
            device_token="device-token",
            device_id="device-1",
            tenant_id="tenant-1",
            store_id="store-sunrise-vw",
            agent_id="agent-1",
        ),
        client=client,  # type: ignore[arg-type]
    )
    worker._execute_job = lambda **kwargs: {  # type: ignore[method-assign]
        "lease": kwargs["authorization"].lease_token
    }

    result = worker.run_once()

    assert result.status == "succeeded"
    assert client.policy_calls[0]["lease"] == "longer-job-bound-lease"
    assert client.completions[0]["result"] == {"lease": "longer-job-bound-lease"}


@pytest.mark.parametrize(
    "claimed_by_device_id",
    [None, "", "device-2", " device-1 "],
)
def test_worker_rejects_missing_or_mismatched_claimed_device_before_policy(
    claimed_by_device_id: str | None,
) -> None:
    from altas.managed.errors import PolicyDenied

    client = RecordingClient()
    client.claimed_by_device_id = claimed_by_device_id
    worker = AltasWorker(
        WorkerSettings(
            control_plane_url="http://control-plane.invalid",
            device_token="device-token",
            device_id="device-1",
            tenant_id="tenant-1",
            store_id="store-sunrise-vw",
            agent_id="agent-1",
        ),
        client=client,  # type: ignore[arg-type]
    )

    with pytest.raises(PolicyDenied) as exc_info:
        worker.run_once()

    assert exc_info.value.reason_code == "SCOPE_MISMATCH"
    assert client.policy_calls == []
    assert client.chat_calls == []
    assert client.completions == []


def test_worker_rejects_cortex_profile_device_mismatch_before_policy(
    tmp_path,
) -> None:
    from altas.managed.errors import PolicyDenied

    profile = tmp_path / "atlas-profile"
    profile.mkdir()
    (profile / ".env").write_text(
        "\n".join((
            "ATLAS_TENANT_ID=tenant-1",
            "ATLAS_STORE_ID=store-sunrise-vw",
            "ATLAS_AGENT_ID=agent-1",
            "ATLAS_DEVICE_ID=device-other",
            "",
        )),
        encoding="utf-8",
    )
    client = RecordingClient()
    client.job_capability = "cortex.memory_maintenance"
    worker = AltasWorker(
        WorkerSettings(
            control_plane_url="http://control-plane.invalid",
            device_token="device-token",
            device_id="device-1",
            tenant_id="tenant-1",
            store_id="store-sunrise-vw",
            agent_id="agent-1",
            profile_home=profile,
        ),
        client=client,  # type: ignore[arg-type]
    )

    with pytest.raises(PolicyDenied) as exc_info:
        worker.run_once()

    assert exc_info.value.reason_code == "SCOPE_MISMATCH"
    assert client.policy_calls == []
    assert client.chat_calls == []
    assert client.completions == []


@pytest.mark.parametrize(
    ("cortex_status", "expected_cycle_status"),
    [("succeeded", "succeeded"), ("locked", "failed")],
)
def test_worker_dispatches_cortex_with_exact_claim_and_dedicated_capability(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    cortex_status: str,
    expected_cycle_status: str,
) -> None:
    from altas.cortex.managed_dispatch import CortexDispatchAdmission
    from altas.cortex.worker import WorkerResult
    from altas.managed.worker import PreparedCortexDispatch

    profile = tmp_path / "atlas-profile"
    profile.mkdir()
    (profile / ".env").write_text(
        "\n".join((
            "ATLAS_TENANT_ID=tenant-1",
            "ATLAS_STORE_ID=store-sunrise-vw",
            "ATLAS_AGENT_ID=agent-1",
            "ATLAS_DEVICE_ID=device-1",
            "ATLAS_DEVICE_TOKEN=device-token",
            "ATLAS_CONTROL_PLANE_URL=http://control-plane.invalid",
            "",
        )),
        encoding="utf-8",
    )
    (profile / "config.yaml").write_text(
        "\n".join((
            "cortex:",
            "  enabled: true",
            "  dream:",
            "    enabled: true",
            "auxiliary:",
            "  cortex_triage:",
            "    provider: altas",
            "    model: atlas-cortex-memory",
            "  cortex_reasoning:",
            "    provider: altas",
            "    model: atlas-cortex-memory",
            "",
        )),
        encoding="utf-8",
    )

    client = RecordingClient()
    client.capabilities = ("cortex.memory_maintenance",)
    client.job_capability = "cortex.memory_maintenance"
    worker = AltasWorker(
        WorkerSettings(
            control_plane_url="http://control-plane.invalid",
            device_token="device-token",
            device_id="device-1",
            tenant_id="tenant-1",
            store_id="store-sunrise-vw",
            agent_id="agent-1",
            profile_home=profile,
        ),
        client=client,  # type: ignore[arg-type]
    )
    admission, dispatch_key = CortexDispatchAdmission.create(
        local_job_id="job_local_cortex",
        admission_id="admission_local_cortex",
        root_job_id="job_local_cortex",
        canonical_input_hash="a" * 64,
        attempt=0,
        due_at="2026-07-14T00:00:00Z",
    )
    prepared = PreparedCortexDispatch(admission, dispatch_key)
    monkeypatch.setattr(worker, "_prepare_cortex_dispatch", lambda: prepared)
    monkeypatch.setattr(
        worker, "_validate_claimed_cortex_dispatch", lambda _job: prepared
    )
    observed: dict[str, Any] = {}

    class FakeCortexWorker:
        def __init__(self, _store, _config, **kwargs: Any) -> None:
            observed.update(kwargs)

        def run_exact_managed_dispatch(self, supplied_admission):
            observed["exact_admission"] = supplied_admission
            return WorkerResult(status=cortex_status, job_id="local-cortex-job")

    monkeypatch.setattr("altas.cortex.worker.CortexDreamWorker", FakeCortexWorker)

    result = worker.run_once()

    assert result.status == expected_cycle_status
    assert client.ensure_calls == [
        {
            "lease": "lease-1",
            "tenant_id": "tenant-1",
            "store_id": "store-sunrise-vw",
            "agent_id": "agent-1",
            "dispatch_key": dispatch_key,
            "dispatch_admission": admission,
        }
    ]
    assert observed["environ"]["ATLAS_CLAIM_TOKEN"].startswith("claim-token-")
    assert observed["environ"]["ATLAS_JOB_CAPABILITY"] == ("cortex.memory_maintenance")
    assert observed["raw_config"]["auxiliary"]["cortex_triage"]["model"] == (
        "atlas-cortex-memory"
    )
    assert observed["exact_admission"] == admission
    assert client.completions[0]["status"] == expected_cycle_status


def test_worker_derives_dispatch_signal_from_due_profile_local_cortex_job(
    tmp_path,
) -> None:
    from altas.cortex.runtime import open_cortex_store

    profile = tmp_path / "atlas-profile"
    profile.mkdir()
    identity = {
        "ATLAS_TENANT_ID": "tenant-1",
        "ATLAS_STORE_ID": "store-sunrise-vw",
        "ATLAS_AGENT_ID": "agent-1",
        "ATLAS_DEVICE_ID": "device-1",
        "ATLAS_DEVICE_TOKEN": "device-token",
        "ATLAS_CONTROL_PLANE_URL": "http://control-plane.invalid",
    }
    (profile / ".env").write_text(
        "\n".join(f"{key}={value}" for key, value in identity.items()) + "\n",
        encoding="utf-8",
    )
    (profile / "config.yaml").write_text(
        "\n".join((
            "cortex:",
            "  enabled: true",
            "  dream:",
            "    enabled: true",
            "auxiliary:",
            "  cortex_triage:",
            "    provider: altas",
            "    model: atlas-cortex-memory",
            "",
        )),
        encoding="utf-8",
    )
    store, _config = open_cortex_store(profile, environ=identity)
    _finalize_semantic_job(store, "managed-worker-session")
    worker = AltasWorker(
        WorkerSettings(
            control_plane_url="http://control-plane.invalid",
            device_token="device-token",
            device_id="device-1",
            tenant_id="tenant-1",
            store_id="store-sunrise-vw",
            agent_id="agent-1",
            profile_home=profile,
        ),
        client=RecordingClient(),  # type: ignore[arg-type]
    )

    first = worker._prepare_cortex_dispatch_key()
    assert store.acquire_named_lease(
        "dream-cycle", owner="other-worker", lease_seconds=60
    )
    while_locked = worker._prepare_cortex_dispatch_key()
    store.release_named_lease("dream-cycle", owner="other-worker")
    second = worker._prepare_cortex_dispatch_key()

    assert first is not None and len(first) == 64
    assert while_locked is None
    assert second == first
    assert "managed-worker-due-job" not in first


def test_managed_dispatch_skips_blocked_child_and_matches_store_lease(
    tmp_path,
) -> None:
    from altas.cortex.runtime import open_cortex_store
    from altas.cortex.worker import MODEL_JOB_TYPES

    profile = tmp_path / "atlas-profile-parent-barrier"
    profile.mkdir()
    identity = {
        "ATLAS_TENANT_ID": "tenant-1",
        "ATLAS_STORE_ID": "store-sunrise-vw",
        "ATLAS_AGENT_ID": "agent-1",
        "ATLAS_DEVICE_ID": "device-1",
        "ATLAS_DEVICE_TOKEN": "device-token",
        "ATLAS_CONTROL_PLANE_URL": "http://control-plane.invalid",
    }
    (profile / ".env").write_text(
        "\n".join(f"{key}={value}" for key, value in identity.items()) + "\n",
        encoding="utf-8",
    )
    (profile / "config.yaml").write_text(
        "\n".join((
            "cortex:",
            "  enabled: true",
            "  dream:",
            "    enabled: true",
            "auxiliary:",
            "  cortex_triage:",
            "    provider: altas",
            "    model: atlas-cortex-memory",
            "",
        )),
        encoding="utf-8",
    )
    store, _config = open_cortex_store(profile, environ=identity)
    root_id = _finalize_semantic_job(store, "managed-parent-barrier")
    root = store.lease_job(
        job_types=MODEL_JOB_TYPES,
        owner="parent-owner",
        lease_seconds=60,
    )
    assert root is not None and root["id"] == root_id
    child_id = store.enqueue_session_distill_continuation(
        root_id,
        remaining_signature=["blocked-child"],
        owner="parent-owner",
    )
    store.fail_job(root_id, owner="parent-owner", error="retry parent", retry=True)
    child_due = "2000-01-01T00:00:01Z"
    root_due = "2000-01-01T00:00:02Z"
    with store.transaction() as connection:
        connection.execute(
            "UPDATE cognitive_jobs SET next_attempt_at=? WHERE id=?",
            (child_due, child_id),
        )
        connection.execute(
            "UPDATE cognitive_jobs SET next_attempt_at=? WHERE id=?",
            (root_due, root_id),
        )
        root_attempt = int(
            connection.execute(
                "SELECT attempt FROM cognitive_jobs WHERE id=?", (root_id,)
            ).fetchone()["attempt"]
        )

    worker = AltasWorker(
        WorkerSettings(
            control_plane_url="http://control-plane.invalid",
            device_token="device-token",
            device_id="device-1",
            tenant_id="tenant-1",
            store_id="store-sunrise-vw",
            agent_id="agent-1",
            profile_home=profile,
        ),
        client=RecordingClient(),  # type: ignore[arg-type]
    )

    dispatch = worker._prepare_cortex_dispatch()
    assert dispatch is not None
    leased = store.lease_exact_managed_job(
        dispatch.admission,
        owner="managed-executor",
        lease_seconds=60,
    )

    assert dispatch.admission.local_job_id == root_id
    assert dispatch.admission.attempt == root_attempt
    assert dispatch.admission.due_at == root_due
    assert dispatch.admission.matches_dispatch_key(dispatch.dispatch_key)
    assert leased is not None and leased["id"] == root_id
    with store.connect() as connection:
        child = connection.execute(
            "SELECT state, attempt FROM cognitive_jobs WHERE id=?", (child_id,)
        ).fetchone()
    assert dict(child) == {"state": "queued", "attempt": 0}


def test_preclaim_wake_never_drains_model_jobs_through_profile_override(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from altas.cortex.runtime import open_cortex_store
    from altas.cortex.worker import DETERMINISTIC_JOB_TYPES

    profile = tmp_path / "atlas-profile-preclaim"
    profile.mkdir()
    identity = {
        "ATLAS_TENANT_ID": "tenant-1",
        "ATLAS_STORE_ID": "store-sunrise-vw",
        "ATLAS_AGENT_ID": "agent-1",
        "ATLAS_DEVICE_ID": "device-1",
        "ATLAS_DEVICE_TOKEN": "device-token",
        "ATLAS_CONTROL_PLANE_URL": "http://control-plane.invalid",
        "OPENAI_API_KEY": "test-openai-key",
    }
    (profile / ".env").write_text(
        "\n".join(f"{key}={value}" for key, value in identity.items()) + "\n",
        encoding="utf-8",
    )
    (profile / "config.yaml").write_text(
        "\n".join((
            "cortex:",
            "  enabled: true",
            "  dream:",
            "    enabled: true",
            "auxiliary:",
            "  cortex_triage:",
            "    provider: openai",
            "    model: cheap-local-override",
            "",
        )),
        encoding="utf-8",
    )
    store, _ = open_cortex_store(profile, environ=identity)
    semantic_job = _finalize_semantic_job(store, "managed-session")
    observed: list[dict[str, Any]] = []

    class DeterministicOnlyWorker:
        def __init__(self, opened_store, _config, **kwargs):
            assert opened_store.brain_id == store.brain_id
            observed.append({"kwargs": kwargs})

        def drain(self, **kwargs):
            observed[-1]["drain"] = kwargs
            return []

    monkeypatch.setattr(
        "altas.cortex.worker.CortexDreamWorker", DeterministicOnlyWorker
    )
    worker = AltasWorker(
        WorkerSettings(
            control_plane_url="http://control-plane.invalid",
            device_token="device-token",
            device_id="device-1",
            tenant_id="tenant-1",
            store_id="store-sunrise-vw",
            agent_id="agent-1",
            profile_home=profile,
        ),
        client=RecordingClient(),  # type: ignore[arg-type]
    )

    assert worker._prepare_cortex_dispatch_key() is not None
    assert observed[0]["drain"] == {
        "max_jobs": 25,
        "job_types": DETERMINISTIC_JOB_TYPES,
    }
    with store.connect() as connection:
        row = connection.execute(
            "SELECT state, attempt FROM cognitive_jobs WHERE id=?", (semantic_job,)
        ).fetchone()
    assert dict(row) == {"state": "queued", "attempt": 0}
