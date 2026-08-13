from __future__ import annotations

import json
import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from fastapi.testclient import TestClient

from altas.control_plane import ControlPlaneSettings, create_app
from altas.control_plane.repository import (
    CortexDispatchLimitExceeded,
    DEMO_AGENT_ID,
    DEMO_CAPABILITIES,
    DEMO_DEVICE_ID,
    DEMO_DEVICE_SECRET,
    DEMO_STORE_ID,
    DEMO_TENANT_ID,
    DEMO_UNENTITLED_STORE_ID,
)
from altas.control_plane.security import hash_secret
from altas.cortex.managed_dispatch import CortexDispatchAdmission


ADMIN_TOKEN = "test-admin-token"


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "control-plane.sqlite3"


@pytest.fixture
def app(database_path: Path):
    return create_app(
        ControlPlaneSettings(
            database_path=database_path,
            lease_signing_key=b"test-lease-signing-key-material!",
            admin_token=ADMIN_TOKEN,
            seed_demo_data=True,
            mock_model=True,
            lease_ttl_seconds=120,
        )
    )


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def _device_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {DEMO_DEVICE_SECRET}"}


def _admin_auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _heartbeat(client: TestClient) -> str:
    response = client.post(
        "/api/v1/worker/heartbeat",
        headers=_device_auth(),
        json={
            "tenant_id": DEMO_TENANT_ID,
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "worker_version": "0.1.0-test",
            "health_status": "healthy",
            "metadata": {"browser": "ready"},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["lease"]["token"]


def _worker_headers(lease: str) -> dict[str, str]:
    return {
        **_device_auth(),
        "X-Atlas-Lease": lease,
        "X-Atlas-Tenant-ID": DEMO_TENANT_ID,
        "X-Atlas-Store-ID": DEMO_STORE_ID,
        "X-Atlas-Agent-ID": DEMO_AGENT_ID,
    }


def _claim_job(client: TestClient, lease: str) -> dict[str, Any]:
    response = client.get("/api/v1/worker/jobs/next", headers=_worker_headers(lease))
    assert response.status_code == 200, response.text
    job = response.json()["job"]
    assert job is not None
    return job


def _policy_headers(lease: str, job: dict[str, Any]) -> dict[str, str]:
    return {
        **_device_auth(),
        "X-Atlas-Lease": lease,
        "X-Atlas-Job-ID": str(job["id"]),
        "X-Atlas-Claim-Token": str(job["claim_token"]),
    }


def _model_headers(lease: str, job: dict[str, Any]) -> dict[str, str]:
    return {
        **_worker_headers(lease),
        "X-Atlas-Job-ID": str(job["id"]),
        "X-Atlas-Claim-Token": str(job["claim_token"]),
    }


def _cortex_dispatch(label: str) -> tuple[CortexDispatchAdmission, str]:
    digest = hashlib.sha256(label.encode("utf-8")).hexdigest()
    return CortexDispatchAdmission.create(
        local_job_id=f"job_local_{digest[:24]}",
        admission_id=f"admission_{digest[24:48]}",
        root_job_id=f"job_local_{digest[:24]}",
        canonical_input_hash=digest,
        attempt=0,
        due_at="2026-07-14T00:00:00Z",
    )


def _cortex_ensure_payload(label: str) -> dict[str, Any]:
    admission, dispatch_key = _cortex_dispatch(label)
    return {
        "tenant_id": DEMO_TENANT_ID,
        "store_id": DEMO_STORE_ID,
        "agent_id": DEMO_AGENT_ID,
        "dispatch_key": dispatch_key,
        "dispatch_admission": admission.to_mapping(),
    }


def _cortex_model_headers(
    lease: str, job: dict[str, Any], label: str
) -> dict[str, str]:
    admission, dispatch_key = _cortex_dispatch(label)
    return {
        **_model_headers(lease, job),
        "X-Atlas-Cortex-Dispatch-Key": dispatch_key,
        "X-Atlas-Cortex-Dispatch-Admission": admission.to_header(),
    }


def test_demo_seed_is_idempotent_and_stores_only_device_hash(
    app, database_path: Path
) -> None:
    app.state.repository.seed_demo()
    app.state.repository.seed_demo()

    with sqlite3.connect(database_path) as connection:
        tenant_count = connection.execute("SELECT COUNT(*) FROM tenants").fetchone()[0]
        store_count = connection.execute("SELECT COUNT(*) FROM stores").fetchone()[0]
        device_row = connection.execute(
            "SELECT secret_hash FROM devices WHERE id = ?", (DEMO_DEVICE_ID,)
        ).fetchone()
        entitlement_capabilities = {
            row[0]
            for row in connection.execute(
                "SELECT capability FROM entitlements"
            ).fetchall()
        }

    assert tenant_count == 1
    assert store_count == 2
    assert entitlement_capabilities == set(DEMO_CAPABILITIES)
    assert device_row[0] == hash_secret(DEMO_DEVICE_SECRET)
    assert device_row[0] != DEMO_DEVICE_SECRET
    assert DEMO_DEVICE_SECRET.encode() not in database_path.read_bytes()


def test_heartbeat_requires_device_auth_and_issues_scoped_lease(
    client: TestClient,
) -> None:
    unauthenticated = client.post(
        "/api/v1/worker/heartbeat",
        json={
            "tenant_id": DEMO_TENANT_ID,
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "worker_version": "0.1.0-test",
        },
    )
    assert unauthenticated.status_code == 401

    lease = _heartbeat(client)
    job = _claim_job(client, lease)
    response = client.post(
        "/api/v1/worker/policy/evaluate",
        headers=_policy_headers(lease, job),
        json={
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "capability": "model.chat",
        },
    )
    assert response.status_code == 200
    assert response.json() == {
        "allowed": True,
        "code": "allowed",
        "tenant_id": DEMO_TENANT_ID,
        "store_id": DEMO_STORE_ID,
        "agent_id": DEMO_AGENT_ID,
        "capability": "model.chat",
    }


def test_policy_endpoint_derives_tenant_and_audits_allow_and_deny(
    client: TestClient,
) -> None:
    lease = _heartbeat(client)
    job = _claim_job(client, lease)
    policy_headers = _policy_headers(lease, job)

    allowed = client.post(
        "/api/v1/worker/policy/evaluate",
        headers=policy_headers,
        json={
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "capability": "fixed_ops.daily_report",
        },
    )
    denied = client.post(
        "/api/v1/worker/policy/evaluate",
        headers=policy_headers,
        json={
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "capability": "shell.unrestricted",
        },
    )
    injected_tenant = client.post(
        "/api/v1/worker/policy/evaluate",
        headers=policy_headers,
        json={
            "tenant_id": "tenant_attacker",
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "capability": "model.chat",
        },
    )

    assert allowed.json()["allowed"] is True
    assert denied.json()["allowed"] is False
    assert denied.json()["code"] == "job_capability_mismatch"
    assert injected_tenant.status_code == 422

    audit = client.get("/api/v1/admin/audit_logs", headers=_admin_auth()).json()[
        "items"
    ]
    decisions = [item for item in audit if item["action"] == "policy.evaluate"]
    assert {item["outcome"] for item in decisions} == {"allowed", "denied"}


def test_tampered_lease_fails_closed(client: TestClient) -> None:
    lease = _heartbeat(client)
    job = _claim_job(client, lease)
    tampered = lease[:-1] + ("A" if lease[-1] != "A" else "B")
    response = client.post(
        "/api/v1/worker/policy/evaluate",
        headers=_policy_headers(tampered, job),
        json={
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "capability": "model.chat",
        },
    )
    assert response.status_code == 200
    assert response.json()["allowed"] is False
    assert response.json()["code"] == "lease_signature_invalid"


def test_active_but_unentitled_second_store_is_denied(client: TestClient, app) -> None:
    store = client.get(
        f"/api/v1/admin/stores?tenant_id={DEMO_TENANT_ID}",
        headers=_admin_auth(),
    ).json()["items"]
    harbor = next(item for item in store if item["id"] == DEMO_UNENTITLED_STORE_ID)
    assert harbor["status"] == "active"
    assert (
        app.state.repository.list_active_capabilities(
            DEMO_TENANT_ID, DEMO_UNENTITLED_STORE_ID
        )
        == []
    )

    response = client.post(
        "/api/v1/worker/heartbeat",
        headers=_device_auth(),
        json={
            "tenant_id": DEMO_TENANT_ID,
            "store_id": DEMO_UNENTITLED_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "worker_version": "0.1.0-test",
        },
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "device_context_mismatch"


@pytest.mark.parametrize(
    ("resource", "resource_id", "expected_code"),
    [
        ("devices", DEMO_DEVICE_ID, "job_context_mismatch"),
        ("stores", DEMO_STORE_ID, "store_inactive"),
        ("subscriptions", "subscription_demo_professional", "subscription_inactive"),
    ],
)
def test_admin_disable_revokes_an_already_issued_lease(
    client: TestClient,
    resource: str,
    resource_id: str,
    expected_code: str,
) -> None:
    lease = _heartbeat(client)
    job = _claim_job(client, lease)
    toggled = client.post(
        f"/api/v1/admin/{resource}/{resource_id}/toggle",
        headers=_admin_auth(),
        json={"enabled": False, "reason": "revocation test"},
    )
    assert toggled.status_code == 200, toggled.text
    if resource == "devices":
        assert toggled.json()["resource"]["terminated_job_count"] == 1
        assert _job_status(client, job["id"]) == "canceled"

    decision = client.post(
        "/api/v1/worker/policy/evaluate",
        headers=_policy_headers(lease, job),
        json={
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "capability": "model.chat",
        },
    ).json()
    assert decision == {
        "allowed": False,
        "code": expected_code,
        "tenant_id": DEMO_TENANT_ID,
        "store_id": DEMO_STORE_ID,
        "agent_id": DEMO_AGENT_ID,
        "capability": "model.chat",
    }


def _job_status(client: TestClient, job_id: str) -> str:
    jobs = client.get("/api/v1/admin/jobs", headers=_admin_auth()).json()["items"]
    return next(item["status"] for item in jobs if item["id"] == job_id)


def test_policy_job_binding_rejects_an_unrelated_entitled_capability(
    client: TestClient,
) -> None:
    lease = _heartbeat(client)
    job = _claim_job(client, lease)

    response = client.post(
        "/api/v1/worker/policy/evaluate",
        headers=_policy_headers(lease, job),
        json={
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            # jobs.complete is in both the entitlement and lease, but is not a
            # dependency that this policy-decision endpoint may grant.
            "capability": "jobs.complete",
        },
    )

    assert response.status_code == 200
    assert response.json()["allowed"] is False
    assert response.json()["code"] == "job_capability_mismatch"


def test_post_claim_job_policy_denial_releases_the_claim(
    client: TestClient,
) -> None:
    lease = _heartbeat(client)
    job = _claim_job(client, lease)
    disabled = client.post(
        "/api/v1/admin/entitlements/entitlement_demo_fixed_ops_daily_report/toggle",
        headers=_admin_auth(),
        json={"enabled": False, "reason": "policy race"},
    )
    assert disabled.status_code == 200

    decision = client.post(
        "/api/v1/worker/policy/evaluate",
        headers=_policy_headers(lease, job),
        json={
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "capability": "fixed_ops.daily_report",
        },
    )

    assert decision.status_code == 200
    assert decision.json()["code"] == "entitlement_inactive"
    assert _job_status(client, job["id"]) == "queued"


def test_device_has_one_claim_and_stale_claim_invalidates_old_token(
    client: TestClient,
    database_path: Path,
) -> None:
    queued = client.post(
        "/api/v1/admin/jobs",
        headers=_admin_auth(),
        json={
            "tenant_id": DEMO_TENANT_ID,
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "capability": "fixed_ops.daily_report",
            "payload": {"report_date": "2026-07-09"},
            "idempotency_key": "claim-limit-second-job",
        },
    )
    assert queued.status_code == 201
    lease = _heartbeat(client)
    first = _claim_job(client, lease)

    blocked = client.get("/api/v1/worker/jobs/next", headers=_worker_headers(lease))
    assert blocked.status_code == 200
    assert blocked.json()["job"] is None

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE jobs SET started_at = ?, updated_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00Z", "2000-01-01T00:00:00Z", first["id"]),
        )
        connection.commit()

    reclaimed = _claim_job(client, lease)
    assert reclaimed["id"] == first["id"]
    assert reclaimed["attempt_count"] == 2
    assert reclaimed["claim_token"] != first["claim_token"]

    stale_completion = client.post(
        f"/api/v1/worker/jobs/{first['id']}/complete",
        headers={**_device_auth(), "X-Atlas-Lease": lease},
        json={
            "tenant_id": DEMO_TENANT_ID,
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "claim_token": first["claim_token"],
            "status": "succeeded",
            "result": {"stale": True},
        },
    )
    assert stale_completion.status_code == 409
    assert _job_status(client, first["id"]) == "running"

    stale_policy = client.post(
        "/api/v1/worker/policy/evaluate",
        headers=_policy_headers(lease, first),
        json={
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "capability": "model.chat",
        },
    )
    stale_model = client.post(
        "/v1/chat/completions",
        headers=_model_headers(lease, first),
        json={
            "model": "altas-fixed-ops",
            "messages": [{"role": "user", "content": "Stale attempt."}],
            "max_tokens": 20,
        },
    )
    current_model = client.post(
        "/v1/chat/completions",
        headers=_model_headers(lease, reclaimed),
        json={
            "model": "altas-fixed-ops",
            "messages": [{"role": "user", "content": "Current attempt."}],
            "max_tokens": 20,
        },
    )

    assert stale_policy.status_code == 200
    assert stale_policy.json()["code"] == "job_claim_invalid"
    assert stale_model.status_code == 403
    assert stale_model.json()["detail"]["code"] == "job_claim_invalid"
    assert current_model.status_code == 200


def test_device_disable_terminalizes_running_and_queued_jobs(
    client: TestClient,
) -> None:
    queued = client.post(
        "/api/v1/admin/jobs",
        headers=_admin_auth(),
        json={
            "tenant_id": DEMO_TENANT_ID,
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "capability": "fixed_ops.daily_report",
            "payload": {"report_date": "2026-07-09"},
            "idempotency_key": "disable-device-queued-job",
        },
    ).json()["job"]
    lease = _heartbeat(client)
    running = _claim_job(client, lease)

    toggled = client.post(
        f"/api/v1/admin/devices/{DEMO_DEVICE_ID}/toggle",
        headers=_admin_auth(),
        json={"enabled": False, "reason": "security shutdown"},
    )

    assert toggled.status_code == 200
    assert toggled.json()["resource"]["terminated_job_count"] == 2
    assert _job_status(client, running["id"]) == "canceled"
    assert _job_status(client, queued["id"]) == "canceled"


def test_worker_job_lifecycle_and_admin_idempotent_queue(client: TestClient) -> None:
    lease = _heartbeat(client)
    worker_headers = _worker_headers(lease)

    seeded = client.get("/api/v1/worker/jobs/next", headers=worker_headers)
    assert seeded.status_code == 200
    seeded_job = seeded.json()["job"]
    assert seeded_job["capability"] == "fixed_ops.daily_report"
    assert seeded_job["payload"]["report_date"] == "2026-07-08"
    assert seeded_job["status"] == "running"

    completed = client.post(
        f"/api/v1/worker/jobs/{seeded_job['id']}/complete",
        headers={**_device_auth(), "X-Atlas-Lease": lease},
        json={
            "tenant_id": DEMO_TENANT_ID,
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "claim_token": seeded_job["claim_token"],
            "status": "succeeded",
            "result": {"report_url": "local://demo-report"},
        },
    )
    assert completed.status_code == 200
    assert completed.json()["job"]["status"] == "succeeded"

    queue_payload: dict[str, Any] = {
        "tenant_id": DEMO_TENANT_ID,
        "store_id": DEMO_STORE_ID,
        "agent_id": DEMO_AGENT_ID,
        "capability": "fixed_ops.daily_report",
        "payload": {"report_date": "2026-07-09"},
        "idempotency_key": "daily-2026-07-09",
    }
    first = client.post("/api/v1/admin/jobs", headers=_admin_auth(), json=queue_payload)
    duplicate = client.post(
        "/api/v1/admin/jobs", headers=_admin_auth(), json=queue_payload
    )
    assert first.status_code == 201
    assert duplicate.status_code == 201
    assert first.json()["created"] is True
    assert duplicate.json()["created"] is False
    assert first.json()["job"]["id"] == duplicate.json()["job"]["id"]

    next_job = client.get("/api/v1/worker/jobs/next", headers=worker_headers).json()[
        "job"
    ]
    assert next_job["id"] == first.json()["job"]["id"]


def test_worker_can_idempotently_signal_dedicated_cortex_maintenance(
    client: TestClient,
    app,
) -> None:
    from altas.control_plane.app import _job_allows_capability

    lease = _heartbeat(client)
    payload = _cortex_ensure_payload("idempotent-a")
    headers = {**_device_auth(), "X-Atlas-Lease": lease}

    first = client.post(
        "/api/v1/worker/jobs/cortex/ensure", headers=headers, json=payload
    )
    duplicate = client.post(
        "/api/v1/worker/jobs/cortex/ensure", headers=headers, json=payload
    )

    assert first.status_code == 200, first.text
    assert duplicate.status_code == 200, duplicate.text
    assert first.json()["created"] is True
    assert duplicate.json() == {
        "job_id": first.json()["job_id"],
        "created": False,
        "requeued": False,
    }
    assert first.json()["requeued"] is False
    job = app.state.repository.get_job(first.json()["job_id"])
    assert job is not None
    assert job["capability"] == "cortex.memory_maintenance"
    assert job["device_id"] == DEMO_DEVICE_ID
    assert job["payload"] == {"dispatch_admission": payload["dispatch_admission"]}
    assert _job_allows_capability(job["capability"], "model.chat") is True
    assert _job_allows_capability(job["capability"], "fixed_ops.daily_report") is False

    with app.state.repository.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE jobs SET status='failed', error='CortexMaintenanceFailed' WHERE id=?",
            (job["id"],),
        )
    retry = client.post(
        "/api/v1/worker/jobs/cortex/ensure", headers=headers, json=payload
    )
    assert retry.status_code == 200
    assert retry.json() == {
        "job_id": job["id"],
        "created": False,
        "requeued": True,
    }
    assert app.state.repository.get_job(job["id"])["status"] == "queued"

    with app.state.repository.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE jobs SET status='canceled', error='DeviceDisabled' WHERE id=?",
            (job["id"],),
        )
    resumed_after_reenable = client.post(
        "/api/v1/worker/jobs/cortex/ensure", headers=headers, json=payload
    )
    assert resumed_after_reenable.status_code == 200
    assert resumed_after_reenable.json() == {
        "job_id": job["id"],
        "created": False,
        "requeued": True,
    }
    assert app.state.repository.get_job(job["id"])["status"] == "queued"

    malformed = client.post(
        "/api/v1/worker/jobs/cortex/ensure",
        headers=headers,
        json={**payload, "dispatch_key": "not-a-digest"},
    )
    cross_tenant = client.post(
        "/api/v1/worker/jobs/cortex/ensure",
        headers=headers,
        json={**payload, "tenant_id": "tenant-attacker"},
    )
    assert malformed.status_code == 422
    assert cross_tenant.status_code == 403


def test_generic_admin_queue_and_requeue_cannot_authorize_cortex(
    client: TestClient,
) -> None:
    lease = _heartbeat(client)
    ensured = client.post(
        "/api/v1/worker/jobs/cortex/ensure",
        headers={**_device_auth(), "X-Atlas-Lease": lease},
        json=_cortex_ensure_payload("admin-route-rejection"),
    )
    assert ensured.status_code == 200

    generic_queue = client.post(
        "/api/v1/admin/jobs",
        headers=_admin_auth(),
        json={
            "tenant_id": DEMO_TENANT_ID,
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "device_id": DEMO_DEVICE_ID,
            "capability": "cortex.memory_maintenance",
            "payload": {},
        },
    )
    generic_requeue = client.post(
        f"/api/v1/admin/jobs/{ensured.json()['job_id']}/requeue",
        headers=_admin_auth(),
        json={"reason": "must use the owner-local admission path"},
    )

    assert generic_queue.status_code == 409
    assert generic_queue.json()["detail"]["code"] == (
        "cortex_dispatch_requires_dedicated_admission"
    )
    assert generic_requeue.status_code == 409
    assert generic_requeue.json()["detail"]["code"] == (
        "cortex_dispatch_requires_dedicated_admission"
    )


def test_invalid_claimed_cortex_payload_is_quarantined_and_cannot_poison_queue(
    client: TestClient,
    app,
) -> None:
    lease = _heartbeat(client)
    headers = {**_device_auth(), "X-Atlas-Lease": lease}
    ensured = client.post(
        "/api/v1/worker/jobs/cortex/ensure",
        headers=headers,
        json=_cortex_ensure_payload("tampered-claim"),
    )
    job_id = ensured.json()["job_id"]
    with app.state.repository.database.transaction(immediate=True) as connection:
        connection.execute("UPDATE jobs SET payload_json='{}' WHERE id=?", (job_id,))

    claimed = client.get(
        "/api/v1/worker/jobs/next",
        headers=_worker_headers(lease),
        params={"capability": "cortex.memory_maintenance"},
    )

    assert claimed.status_code == 403
    assert claimed.json()["detail"]["code"] == ("cortex_dispatch_admission_invalid")
    quarantined = app.state.repository.get_job(job_id)
    assert quarantined is not None
    assert quarantined["status"] == "canceled"
    assert quarantined["error"] == "CortexDispatchAdmissionInvalid"

    replacement = client.post(
        "/api/v1/worker/jobs/cortex/ensure",
        headers=headers,
        json=_cortex_ensure_payload("replacement-after-tamper"),
    )
    assert replacement.status_code == 200, replacement.text


def test_database_upgrade_quarantines_legacy_active_cortex_job(app) -> None:
    repository = app.state.repository
    now = "2026-07-14T00:00:00Z"
    with repository.database.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO jobs(id, tenant_id, store_id, agent_id, device_id, "
            "capability, status, payload_json, requested_by, idempotency_key, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, "
            "'cortex.memory_maintenance', 'queued', '{}', 'legacy-worker', ?, ?, ?)",
            (
                "job_legacy_cortex_without_provenance",
                DEMO_TENANT_ID,
                DEMO_STORE_ID,
                DEMO_AGENT_ID,
                DEMO_DEVICE_ID,
                "legacy-cortex-without-provenance",
                now,
                now,
            ),
        )

    repository.database.initialize()

    migrated = repository.get_job("job_legacy_cortex_without_provenance")
    assert migrated is not None
    assert migrated["status"] == "canceled"
    assert migrated["error"] == "CortexDispatchAdmissionInvalid"


def test_capability_filtered_poll_claims_cortex_and_rejects_unleased_filter(
    client: TestClient,
    app,
) -> None:
    lease = _heartbeat(client)
    worker_headers = _worker_headers(lease)
    ensured = client.post(
        "/api/v1/worker/jobs/cortex/ensure",
        headers={**_device_auth(), "X-Atlas-Lease": lease},
        json=_cortex_ensure_payload("filtered-7"),
    )
    assert ensured.status_code == 200, ensured.text
    cortex_job_id = ensured.json()["job_id"]

    fixed_ops_job = app.state.repository.get_job("job_demo_daily_report")
    cortex_job = app.state.repository.get_job(cortex_job_id)
    assert fixed_ops_job is not None
    assert cortex_job is not None
    assert fixed_ops_job["created_at"] < cortex_job["created_at"]

    unleased_capability = "fixed_ops.unleased"
    lease_claims = app.state.lease_signer.verify(lease)
    assert unleased_capability not in lease_claims.capabilities
    denied = client.get(
        "/api/v1/worker/jobs/next",
        headers=worker_headers,
        params={"capability": unleased_capability},
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == {"code": "capability_not_leased"}
    assert app.state.repository.get_job("job_demo_daily_report")["status"] == "queued"
    assert app.state.repository.get_job(cortex_job_id)["status"] == "queued"

    claimed = client.get(
        "/api/v1/worker/jobs/next",
        headers=worker_headers,
        params={"capability": "cortex.memory_maintenance"},
    )
    assert claimed.status_code == 200, claimed.text
    payload = claimed.json()
    assert payload["job"]["id"] == cortex_job_id
    assert payload["job"]["capability"] == "cortex.memory_maintenance"
    assert payload["authorization_lease"] is not None
    assert app.state.repository.get_job(cortex_job_id)["status"] == "running"
    assert app.state.repository.get_job("job_demo_daily_report")["status"] == "queued"


def test_cortex_dispatch_admission_is_server_bounded(
    tmp_path: Path,
) -> None:
    limited_app = create_app(
        ControlPlaneSettings(
            database_path=tmp_path / "cortex-admission.sqlite3",
            lease_signing_key=b"cortex-admission-signing-key-material!",
            admin_token=ADMIN_TOKEN,
            seed_demo_data=True,
            mock_model=True,
            lease_ttl_seconds=120,
            cortex_max_jobs_per_device_per_24h=2,
        )
    )
    with TestClient(limited_app) as limited_client:
        lease = _heartbeat(limited_client)
        seeded = _claim_job(limited_client, lease)
        seeded_completion = limited_client.post(
            f"/api/v1/worker/jobs/{seeded['id']}/complete",
            headers={**_device_auth(), "X-Atlas-Lease": lease},
            json={
                "tenant_id": DEMO_TENANT_ID,
                "store_id": DEMO_STORE_ID,
                "agent_id": DEMO_AGENT_ID,
                "claim_token": seeded["claim_token"],
                "status": "succeeded",
                "result": {},
            },
        )
        assert seeded_completion.status_code == 200
        headers = {**_device_auth(), "X-Atlas-Lease": lease}

        first = limited_client.post(
            "/api/v1/worker/jobs/cortex/ensure",
            headers=headers,
            json=_cortex_ensure_payload("bounded-1"),
        )
        active_denial = limited_client.post(
            "/api/v1/worker/jobs/cortex/ensure",
            headers=headers,
            json=_cortex_ensure_payload("bounded-2"),
        )
        duplicate = limited_client.post(
            "/api/v1/worker/jobs/cortex/ensure",
            headers=headers,
            json=_cortex_ensure_payload("bounded-1"),
        )

        assert first.status_code == 200
        assert active_denial.status_code == 409
        assert active_denial.json()["detail"]["code"] == (
            "cortex_dispatch_already_active"
        )
        assert duplicate.status_code == 200
        assert duplicate.json()["job_id"] == first.json()["job_id"]
        with limited_app.state.repository.database.transaction(
            immediate=True
        ) as connection:
            connection.execute(
                "UPDATE jobs SET status='succeeded', completed_at=updated_at "
                "WHERE id=?",
                (first.json()["job_id"],),
            )

        second = limited_client.post(
            "/api/v1/worker/jobs/cortex/ensure",
            headers=headers,
            json=_cortex_ensure_payload("bounded-2"),
        )
        assert second.status_code == 200
        with limited_app.state.repository.database.transaction(
            immediate=True
        ) as connection:
            connection.execute(
                "UPDATE jobs SET status='succeeded', completed_at=updated_at "
                "WHERE id=?",
                (second.json()["job_id"],),
            )
        daily_denial = limited_client.post(
            "/api/v1/worker/jobs/cortex/ensure",
            headers=headers,
            json=_cortex_ensure_payload("bounded-3"),
        )

    assert daily_denial.status_code == 429
    assert daily_denial.json()["detail"]["code"] == (
        "cortex_dispatch_daily_limit_exceeded"
    )


def test_cortex_terminal_retry_cannot_requeue_beside_an_active_dispatch(
    client: TestClient,
    app,
) -> None:
    """Recovering an old idempotency key preserves one-active-job admission."""

    lease = _heartbeat(client)
    headers = {**_device_auth(), "X-Atlas-Lease": lease}

    def ensure(label: str):
        return client.post(
            "/api/v1/worker/jobs/cortex/ensure",
            headers=headers,
            json=_cortex_ensure_payload(label),
        )

    first = ensure("terminal-a")
    assert first.status_code == 200
    with app.state.repository.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE jobs SET status='failed', error='CortexMaintenanceFailed' "
            "WHERE id=?",
            (first.json()["job_id"],),
        )

    second = ensure("terminal-b")
    retry_first = ensure("terminal-a")

    assert second.status_code == 200
    assert retry_first.status_code == 409
    assert retry_first.json()["detail"]["code"] == ("cortex_dispatch_already_active")
    with app.state.repository.database.connect() as connection:
        active = connection.execute(
            "SELECT id FROM jobs WHERE tenant_id=? AND store_id=? AND agent_id=? "
            "AND device_id=? AND capability='cortex.memory_maintenance' "
            "AND status IN ('queued','running')",
            (DEMO_TENANT_ID, DEMO_STORE_ID, DEMO_AGENT_ID, DEMO_DEVICE_ID),
        ).fetchall()
    assert [row["id"] for row in active] == [second.json()["job_id"]]


def test_cortex_terminal_requeues_consume_rolling_admission_budget(
    tmp_path: Path,
) -> None:
    limited_app = create_app(
        ControlPlaneSettings(
            database_path=tmp_path / "cortex-requeue-admission.sqlite3",
            lease_signing_key=b"cortex-requeue-signing-key-material!",
            admin_token=ADMIN_TOKEN,
            seed_demo_data=True,
            mock_model=True,
            lease_ttl_seconds=120,
            cortex_max_jobs_per_device_per_24h=3,
        )
    )
    with TestClient(limited_app) as limited_client:
        lease = _heartbeat(limited_client)
        headers = {**_device_auth(), "X-Atlas-Lease": lease}
        payload = _cortex_ensure_payload("requeue-budget-e")

        first = limited_client.post(
            "/api/v1/worker/jobs/cortex/ensure", headers=headers, json=payload
        )
        assert first.status_code == 200
        job_id = first.json()["job_id"]

        for terminal_status in ("failed", "canceled"):
            with limited_app.state.repository.database.transaction(
                immediate=True
            ) as connection:
                connection.execute(
                    "UPDATE jobs SET status=?, error='terminal' WHERE id=?",
                    (terminal_status, job_id),
                )
            retry = limited_client.post(
                "/api/v1/worker/jobs/cortex/ensure", headers=headers, json=payload
            )
            assert retry.status_code == 200
            assert retry.json() == {
                "job_id": job_id,
                "created": False,
                "requeued": True,
            }

        with limited_app.state.repository.database.transaction(
            immediate=True
        ) as connection:
            connection.execute(
                "UPDATE jobs SET status='failed', error='terminal' WHERE id=?",
                (job_id,),
            )
        exhausted = limited_client.post(
            "/api/v1/worker/jobs/cortex/ensure", headers=headers, json=payload
        )

    assert exhausted.status_code == 429
    assert exhausted.json()["detail"]["code"] == (
        "cortex_dispatch_daily_limit_exceeded"
    )
    with limited_app.state.repository.database.connect() as connection:
        admissions = connection.execute(
            "SELECT admission_kind, COUNT(*) AS count "
            "FROM cortex_dispatch_admissions WHERE job_id=? "
            "GROUP BY admission_kind",
            (job_id,),
        ).fetchall()
        job = connection.execute(
            "SELECT status FROM jobs WHERE id=?", (job_id,)
        ).fetchone()
    assert {row["admission_kind"]: row["count"] for row in admissions} == {
        "created": 1,
        "requeued": 2,
    }
    assert job is not None and job["status"] == "failed"


def test_concurrent_terminal_retries_record_exactly_one_admission(app) -> None:
    repository = app.state.repository
    dispatch_admission, dispatch_key = _cortex_dispatch("concurrent-f")
    job, created, _ = repository.ensure_cortex_maintenance_job(
        tenant_id=DEMO_TENANT_ID,
        store_id=DEMO_STORE_ID,
        agent_id=DEMO_AGENT_ID,
        device_id=DEMO_DEVICE_ID,
        dispatch_key=dispatch_key,
        dispatch_admission=dispatch_admission.to_mapping(),
        max_jobs_per_24h=2,
    )
    assert created is True
    with repository.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE jobs SET status='failed', error='terminal' WHERE id=?",
            (job["id"],),
        )

    callers = 8
    barrier = Barrier(callers)

    def retry() -> tuple[bool, bool]:
        barrier.wait()
        _, retry_created, requeued = repository.ensure_cortex_maintenance_job(
            tenant_id=DEMO_TENANT_ID,
            store_id=DEMO_STORE_ID,
            agent_id=DEMO_AGENT_ID,
            device_id=DEMO_DEVICE_ID,
            dispatch_key=dispatch_key,
            dispatch_admission=dispatch_admission.to_mapping(),
            max_jobs_per_24h=2,
        )
        return retry_created, requeued

    with ThreadPoolExecutor(max_workers=callers) as executor:
        outcomes = list(executor.map(lambda _: retry(), range(callers)))

    assert outcomes.count((False, True)) == 1
    assert outcomes.count((False, False)) == callers - 1
    with repository.database.connect() as connection:
        admission_count = connection.execute(
            "SELECT COUNT(*) FROM cortex_dispatch_admissions WHERE job_id=?",
            (job["id"],),
        ).fetchone()[0]
        active_count = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE id=? AND status='queued'",
            (job["id"],),
        ).fetchone()[0]
    assert admission_count == 2
    assert active_count == 1

    with repository.database.transaction(immediate=True) as connection:
        connection.execute("UPDATE jobs SET status='failed' WHERE id=?", (job["id"],))
    with pytest.raises(
        CortexDispatchLimitExceeded,
        match="^cortex_dispatch_daily_limit_exceeded$",
    ):
        repository.ensure_cortex_maintenance_job(
            tenant_id=DEMO_TENANT_ID,
            store_id=DEMO_STORE_ID,
            agent_id=DEMO_AGENT_ID,
            device_id=DEMO_DEVICE_ID,
            dispatch_key=dispatch_key,
            dispatch_admission=dispatch_admission.to_mapping(),
            max_jobs_per_24h=2,
        )


def test_cortex_requeue_admission_budget_uses_a_rolling_window(app) -> None:
    repository = app.state.repository
    dispatch_admission, dispatch_key = _cortex_dispatch("rolling-0")
    job, _, _ = repository.ensure_cortex_maintenance_job(
        tenant_id=DEMO_TENANT_ID,
        store_id=DEMO_STORE_ID,
        agent_id=DEMO_AGENT_ID,
        device_id=DEMO_DEVICE_ID,
        dispatch_key=dispatch_key,
        dispatch_admission=dispatch_admission.to_mapping(),
        max_jobs_per_24h=1,
    )
    with repository.database.transaction(immediate=True) as connection:
        connection.execute("UPDATE jobs SET status='failed' WHERE id=?", (job["id"],))

    with pytest.raises(CortexDispatchLimitExceeded):
        repository.ensure_cortex_maintenance_job(
            tenant_id=DEMO_TENANT_ID,
            store_id=DEMO_STORE_ID,
            agent_id=DEMO_AGENT_ID,
            device_id=DEMO_DEVICE_ID,
            dispatch_key=dispatch_key,
            dispatch_admission=dispatch_admission.to_mapping(),
            max_jobs_per_24h=1,
        )

    with repository.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE cortex_dispatch_admissions SET admitted_at='2000-01-01T00:00:00Z' "
            "WHERE job_id=?",
            (job["id"],),
        )
    retried_job, created, requeued = repository.ensure_cortex_maintenance_job(
        tenant_id=DEMO_TENANT_ID,
        store_id=DEMO_STORE_ID,
        agent_id=DEMO_AGENT_ID,
        device_id=DEMO_DEVICE_ID,
        dispatch_key=dispatch_key,
        dispatch_admission=dispatch_admission.to_mapping(),
        max_jobs_per_24h=1,
    )
    assert retried_job["id"] == job["id"]
    assert created is False
    assert requeued is True


def test_cortex_admission_ledger_backfill_is_restart_idempotent(app) -> None:
    repository = app.state.repository
    dispatch_admission, dispatch_key = _cortex_dispatch("backfill-9")
    job, _, _ = repository.ensure_cortex_maintenance_job(
        tenant_id=DEMO_TENANT_ID,
        store_id=DEMO_STORE_ID,
        agent_id=DEMO_AGENT_ID,
        device_id=DEMO_DEVICE_ID,
        dispatch_key=dispatch_key,
        dispatch_admission=dispatch_admission.to_mapping(),
        max_jobs_per_24h=2,
    )
    with repository.database.transaction(immediate=True) as connection:
        connection.execute(
            "DELETE FROM cortex_dispatch_admissions WHERE job_id=?", (job["id"],)
        )

    repository.database.initialize()
    repository.database.initialize()

    with repository.database.connect() as connection:
        admissions = connection.execute(
            "SELECT id, admission_kind FROM cortex_dispatch_admissions WHERE job_id=?",
            (job["id"],),
        ).fetchall()
    assert [dict(row) for row in admissions] == [
        {"id": f"cortex-create:{job['id']}", "admission_kind": "created"}
    ]


def test_cortex_job_uses_only_the_dedicated_memory_model_alias(
    client: TestClient,
) -> None:
    lease = _heartbeat(client)
    seeded = _claim_job(client, lease)
    completed = client.post(
        f"/api/v1/worker/jobs/{seeded['id']}/complete",
        headers={**_device_auth(), "X-Atlas-Lease": lease},
        json={
            "tenant_id": DEMO_TENANT_ID,
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "claim_token": seeded["claim_token"],
            "status": "succeeded",
            "result": {},
        },
    )
    assert completed.status_code == 200

    ensured = client.post(
        "/api/v1/worker/jobs/cortex/ensure",
        headers={**_device_auth(), "X-Atlas-Lease": lease},
        json=_cortex_ensure_payload("model-alias-c"),
    )
    assert ensured.status_code == 200
    job = _claim_job(client, lease)
    assert job["capability"] == "cortex.memory_maintenance"
    headers = _cortex_model_headers(lease, job, "model-alias-c")

    wrong_model = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "altas-fixed-ops",
            "messages": [{"role": "user", "content": "classify"}],
            "max_tokens": 100,
        },
    )
    memory_model = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "atlas-cortex-memory",
            "messages": [{"role": "user", "content": "classify"}],
            "max_tokens": 4000,
            "response_format": {"type": "json_object"},
        },
    )
    models = client.get("/v1/models", headers=_worker_headers(lease))

    assert wrong_model.status_code == 404
    assert memory_model.status_code == 200, memory_model.text
    assert memory_model.json()["model"] == "atlas-cortex-memory"
    mock_payload = json.loads(memory_model.json()["choices"][0]["message"]["content"])
    assert mock_payload == {
        "schema_version": "atlas.cortex.triage.v1",
        "operations": [],
    }

    # The dedicated maintenance budget permits the bounded worst-case Dream
    # path (five batches, with triage/repair and review/repair calls) without
    # widening the much smaller conversational-job allowance.
    for _ in range(19):
        allowed = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "atlas-cortex-memory",
                "messages": [{"role": "user", "content": "classify"}],
                "max_tokens": 4000,
                "response_format": {"type": "json_object"},
            },
        )
        assert allowed.status_code == 200, allowed.text
    over_budget = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "atlas-cortex-memory",
            "messages": [{"role": "user", "content": "classify"}],
            "max_tokens": 4000,
            "response_format": {"type": "json_object"},
        },
    )
    assert over_budget.status_code == 429
    assert over_budget.json()["detail"]["code"] == "model_request_limit_exceeded"
    # Internal maintenance aliases are callable only with a claimed Cortex
    # job and never advertised to ordinary chat model discovery.
    assert {item["id"] for item in models.json()["data"]} == {"altas-fixed-ops"}


def test_cortex_model_requires_exact_dispatch_headers_before_usage_reservation(
    client: TestClient,
    app,
) -> None:
    lease = _heartbeat(client)
    seeded = _claim_job(client, lease)
    completed = client.post(
        f"/api/v1/worker/jobs/{seeded['id']}/complete",
        headers={**_device_auth(), "X-Atlas-Lease": lease},
        json={
            "tenant_id": DEMO_TENANT_ID,
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "claim_token": seeded["claim_token"],
            "status": "succeeded",
            "result": {},
        },
    )
    assert completed.status_code == 200
    label = "model-provenance-binding"
    ensured = client.post(
        "/api/v1/worker/jobs/cortex/ensure",
        headers={**_device_auth(), "X-Atlas-Lease": lease},
        json=_cortex_ensure_payload(label),
    )
    assert ensured.status_code == 200
    job = _claim_job(client, lease)
    request = {
        "model": "atlas-cortex-memory",
        "messages": [{"role": "user", "content": "classify"}],
        "max_tokens": 100,
        "response_format": {"type": "json_object"},
    }

    missing = client.post(
        "/v1/chat/completions",
        headers=_model_headers(lease, job),
        json=request,
    )
    wrong = client.post(
        "/v1/chat/completions",
        headers={
            **_model_headers(lease, job),
            **{
                key: value
                for key, value in _cortex_model_headers(
                    lease, job, "different-local-admission"
                ).items()
                if key.startswith("X-Atlas-Cortex-")
            },
        },
        json=request,
    )
    with app.state.repository.database.connect() as connection:
        reserved_before_valid = connection.execute(
            "SELECT COUNT(*) FROM usage_events WHERE job_id=?", (job["id"],)
        ).fetchone()[0]
    valid = client.post(
        "/v1/chat/completions",
        headers=_cortex_model_headers(lease, job, label),
        json=request,
    )

    assert missing.status_code == 403
    assert missing.json()["detail"]["code"] == "cortex_dispatch_admission_invalid"
    assert wrong.status_code == 403
    assert wrong.json()["detail"]["code"] == "cortex_dispatch_admission_invalid"
    assert reserved_before_valid == 0
    assert valid.status_code == 200, valid.text


def test_cortex_mock_returns_grounded_schema_valid_discard_operations(
    client: TestClient,
) -> None:
    lease = _heartbeat(client)
    seeded = _claim_job(client, lease)
    completed = client.post(
        f"/api/v1/worker/jobs/{seeded['id']}/complete",
        headers={**_device_auth(), "X-Atlas-Lease": lease},
        json={
            "tenant_id": DEMO_TENANT_ID,
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "claim_token": seeded["claim_token"],
            "status": "succeeded",
            "result": {},
        },
    )
    assert completed.status_code == 200
    ensured = client.post(
        "/api/v1/worker/jobs/cortex/ensure",
        headers={**_device_auth(), "X-Atlas-Lease": lease},
        json=_cortex_ensure_payload("mock-schema-d"),
    )
    assert ensured.status_code == 200
    job = _claim_job(client, lease)
    prompt = json.dumps({
        "candidates": [
            {
                "observation_id": "observation-1",
                "authoritative_evidence_ids": ["evidence-1"],
            }
        ]
    })
    response = client.post(
        "/v1/chat/completions",
        headers=_cortex_model_headers(lease, job, "mock-schema-d"),
        json={
            "model": "atlas-cortex-memory",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4000,
            "response_format": {"type": "json_object"},
        },
    )

    assert response.status_code == 200, response.text
    payload = json.loads(response.json()["choices"][0]["message"]["content"])
    assert payload["schema_version"] == "atlas.cortex.triage.v1"
    assert payload["operations"] == [
        {
            "observation_id": "observation-1",
            "action": "defer_unresolved",
            "memory_kind": "event",
            "statement": "",
            "evidence_ids": ["evidence-1"],
            "target_memory_id": None,
            "entities": [],
            "valid_from": None,
            "valid_until": None,
            "rationale": "Deterministic development mock; decision deferred.",
            "sensitivity": "restricted",
            "retention": "short",
            "missing_information": "",
        }
    ]


def test_mock_chat_is_deterministic_and_records_usage(client: TestClient) -> None:
    lease = _heartbeat(client)
    headers = _worker_headers(lease)
    job = client.get("/api/v1/worker/jobs/next", headers=headers).json()["job"]
    headers["X-Atlas-Job-ID"] = job["id"]
    headers["X-Atlas-Claim-Token"] = job["claim_token"]
    payload = {
        "model": "altas-fixed-ops",
        "messages": [{"role": "user", "content": "Summarize today's ROs."}],
        "temperature": 0,
    }

    first = client.post("/v1/chat/completions", headers=headers, json=payload)
    second = client.post("/v1/chat/completions", headers=headers, json=payload)
    assert first.status_code == 200, first.text
    assert first.json() == second.json()
    assert first.json()["object"] == "chat.completion"
    assert first.json()["usage"]["total_tokens"] > 0

    usage = client.get(
        f"/api/v1/admin/usage_events?tenant_id={DEMO_TENANT_ID}",
        headers=_admin_auth(),
    ).json()["items"]
    assert len(usage) == 2
    assert all(item["provider"] == "altas-mock" for item in usage)
    assert all(item["job_id"] == job["id"] for item in usage)


def test_auxiliary_altas_call_satisfies_control_plane_header_contract(
    client: TestClient,
    monkeypatch,
) -> None:
    """The auxiliary transport must forward the live scoped job claim.

    A deliberately invalid process-global claim verifies that the managed
    profile reads the active multiplex scope rather than another profile's
    environment.  The captured kwargs are then sent to the real control-plane
    route, whose normal lease/job/claim checks provide the contract assertion.
    """
    from types import SimpleNamespace
    from unittest.mock import patch

    from agent.auxiliary_client import call_llm
    from agent.secret_scope import (
        reset_secret_scope,
        set_multiplex_active,
        set_secret_scope,
    )

    lease = _heartbeat(client)
    job = _claim_job(client, lease)
    observed_headers: dict[str, str] = {}
    observed_body: dict[str, Any] = {}

    class _ControlPlaneCompletions:
        def create(self, **kwargs):
            observed_headers.update(kwargs.get("extra_headers") or {})
            request_body = {
                "model": kwargs["model"],
                "messages": kwargs["messages"],
                **(kwargs.get("extra_body") or {}),
            }
            observed_body.update(request_body)
            response = client.post(
                "/v1/chat/completions",
                headers={
                    **_device_auth(),
                    **(kwargs.get("extra_headers") or {}),
                },
                json=request_body,
            )
            assert response.status_code == 200, response.text
            body = response.json()
            message = body["choices"][0]["message"]
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            role=message["role"],
                            content=message["content"],
                        )
                    )
                ]
            )

    auxiliary_client = SimpleNamespace(
        base_url="http://control.test/v1",
        chat=SimpleNamespace(completions=_ControlPlaneCompletions()),
    )
    scoped_secrets = {
        "ATLAS_LEASE_TOKEN": lease,
        "ATLAS_TENANT_ID": DEMO_TENANT_ID,
        "ATLAS_STORE_ID": DEMO_STORE_ID,
        "ATLAS_AGENT_ID": DEMO_AGENT_ID,
        "ATLAS_JOB_ID": str(job["id"]),
        "ATLAS_CLAIM_TOKEN": str(job["claim_token"]),
    }
    monkeypatch.setenv("ATLAS_LEASE_TOKEN", "wrong-global-lease")
    monkeypatch.setenv("ATLAS_CLAIM_TOKEN", "wrong-global-claim")

    set_multiplex_active(True)
    scope_token = set_secret_scope(scoped_secrets)
    try:
        with (
            patch(
                "agent.auxiliary_client._resolve_task_provider_model",
                return_value=(
                    "altas",
                    "altas-fixed-ops",
                    None,
                    DEMO_DEVICE_SECRET,
                    None,
                ),
            ),
            patch(
                "agent.auxiliary_client._get_cached_client",
                return_value=(auxiliary_client, "altas-fixed-ops"),
            ),
        ):
            response = call_llm(
                provider="altas",
                model="altas-fixed-ops",
                messages=[{"role": "user", "content": "Summarize today's ROs."}],
                extra_body={"response_format": {"type": "json_object"}},
                fallback_policy="none",
            )
    finally:
        reset_secret_scope(scope_token)
        set_multiplex_active(False)

    assert response.choices[0].message.content
    assert observed_body["response_format"] == {"type": "json_object"}
    assert observed_headers == {
        "X-Atlas-Lease": lease,
        "X-Atlas-Tenant-ID": DEMO_TENANT_ID,
        "X-Atlas-Store-ID": DEMO_STORE_ID,
        "X-Atlas-Agent-ID": DEMO_AGENT_ID,
        "X-Atlas-Job-ID": str(job["id"]),
        "X-Atlas-Claim-Token": str(job["claim_token"]),
    }


@pytest.mark.parametrize(
    "unsafe_fields",
    [
        {"n": 100},
        {"extra_body": {"provider": {"allow_fallbacks": True}}},
        {"response_format": {"type": "text"}},
        {"response_format": {"type": "json_object", "schema": {}}},
        {"unsupported_cost_option": True},
    ],
)
def test_chat_schema_rejects_cost_amplifying_or_unknown_options(
    client: TestClient,
    unsafe_fields: dict[str, Any],
) -> None:
    lease = _heartbeat(client)
    job = _claim_job(client, lease)
    response = client.post(
        "/v1/chat/completions",
        headers=_model_headers(lease, job),
        json={
            "model": "altas-fixed-ops",
            "messages": [{"role": "user", "content": "Bounded prompt."}],
            "max_tokens": 20,
            **unsafe_fields,
        },
    )

    assert response.status_code == 422


def test_chat_schema_rejects_oversized_prompt(client: TestClient) -> None:
    lease = _heartbeat(client)
    job = _claim_job(client, lease)
    response = client.post(
        "/v1/chat/completions",
        headers=_model_headers(lease, job),
        json={
            "model": "altas-fixed-ops",
            "messages": [{"role": "user", "content": "x" * (65 * 1024)}],
            "max_tokens": 20,
        },
    )

    assert response.status_code == 422


def test_model_gateway_rejects_a_job_without_declared_model_dependency(
    client: TestClient,
    database_path: Path,
) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE jobs SET capability = 'jobs.poll' WHERE id = ?",
            ("job_demo_daily_report",),
        )
        connection.commit()
    lease = _heartbeat(client)
    job = _claim_job(client, lease)
    headers = _model_headers(lease, job)

    response = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "altas-fixed-ops",
            "messages": [{"role": "user", "content": "Not permitted."}],
            "max_tokens": 20,
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "job_capability_mismatch"


def test_per_job_model_request_limit_is_persisted_and_enforced(
    tmp_path: Path,
) -> None:
    limited_app = create_app(
        ControlPlaneSettings(
            database_path=tmp_path / "request-limit.sqlite3",
            lease_signing_key=b"request-limit-signing-key-material!",
            admin_token=ADMIN_TOKEN,
            seed_demo_data=True,
            mock_model=True,
            lease_ttl_seconds=120,
            max_model_requests_per_job=1,
            max_requested_tokens_per_job=1000,
        )
    )
    with TestClient(limited_app) as limited_client:
        lease = _heartbeat(limited_client)
        job = _claim_job(limited_client, lease)
        headers = _model_headers(lease, job)
        payload = {
            "model": "altas-fixed-ops",
            "messages": [{"role": "user", "content": "Summarize."}],
            "max_tokens": 50,
        }

        first = limited_client.post(
            "/v1/chat/completions", headers=headers, json=payload
        )
        denied = limited_client.post(
            "/v1/chat/completions", headers=headers, json=payload
        )
        usage = limited_client.get(
            "/api/v1/admin/usage_events", headers=_admin_auth()
        ).json()["items"]

    assert first.status_code == 200
    assert denied.status_code == 429
    assert denied.json()["detail"]["code"] == "model_request_limit_exceeded"
    assert len(usage) == 1
    assert usage[0]["requested_tokens"] == 50


def test_per_job_requested_token_limit_uses_persisted_reservations(
    tmp_path: Path,
) -> None:
    limited_app = create_app(
        ControlPlaneSettings(
            database_path=tmp_path / "token-limit.sqlite3",
            lease_signing_key=b"token-limit-signing-key-material-32!",
            admin_token=ADMIN_TOKEN,
            seed_demo_data=True,
            mock_model=True,
            lease_ttl_seconds=120,
            max_model_requests_per_job=5,
            max_requested_tokens_per_job=100,
            default_model_max_tokens=50,
        )
    )
    with TestClient(limited_app) as limited_client:
        lease = _heartbeat(limited_client)
        job = _claim_job(limited_client, lease)
        headers = _model_headers(lease, job)
        base_payload = {
            "model": "altas-fixed-ops",
            "messages": [{"role": "user", "content": "Summarize."}],
        }

        first = limited_client.post(
            "/v1/chat/completions",
            headers=headers,
            json={**base_payload, "max_tokens": 60},
        )
        denied = limited_client.post(
            "/v1/chat/completions",
            headers=headers,
            json={**base_payload, "max_tokens": 50},
        )
        usage = limited_client.get(
            "/api/v1/admin/usage_events", headers=_admin_auth()
        ).json()["items"]

    assert first.status_code == 200
    assert denied.status_code == 429
    assert denied.json()["detail"]["code"] == "model_requested_token_limit_exceeded"
    assert len(usage) == 1
    assert usage[0]["requested_tokens"] == 60


def test_admin_routes_require_separate_admin_bearer(client: TestClient) -> None:
    assert client.get("/api/v1/admin/overview").status_code == 401
    assert (
        client.get(
            "/api/v1/admin/overview", headers={"Authorization": "Bearer wrong"}
        ).status_code
        == 401
    )
    overview = client.get("/api/v1/admin/overview", headers=_admin_auth())
    assert overview.status_code == 200
    assert overview.json()["tenants"] == 1

    devices = client.get("/api/v1/admin/devices", headers=_admin_auth()).json()["items"]
    assert "secret_hash" not in devices[0]
