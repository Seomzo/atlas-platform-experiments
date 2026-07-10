from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from altas.control_plane import ControlPlaneSettings, create_app
from altas.control_plane.repository import (
    DEMO_AGENT_ID,
    DEMO_DEVICE_ID,
    DEMO_DEVICE_SECRET,
    DEMO_STORE_ID,
    DEMO_TENANT_ID,
    DEMO_UNENTITLED_STORE_ID,
)
from altas.control_plane.security import hash_secret


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
        "X-Altas-Lease": lease,
        "X-Altas-Tenant-ID": DEMO_TENANT_ID,
        "X-Altas-Store-ID": DEMO_STORE_ID,
        "X-Altas-Agent-ID": DEMO_AGENT_ID,
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
        "X-Altas-Lease": lease,
        "X-Altas-Job-ID": str(job["id"]),
        "X-Altas-Claim-Token": str(job["claim_token"]),
    }


def _model_headers(lease: str, job: dict[str, Any]) -> dict[str, str]:
    return {
        **_worker_headers(lease),
        "X-Altas-Job-ID": str(job["id"]),
        "X-Altas-Claim-Token": str(job["claim_token"]),
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
        entitlement_count = connection.execute(
            "SELECT COUNT(*) FROM entitlements"
        ).fetchone()[0]

    assert tenant_count == 1
    assert store_count == 2
    assert entitlement_count == 4
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
        headers={**_device_auth(), "X-Altas-Lease": lease},
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
        headers={**_device_auth(), "X-Altas-Lease": lease},
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


def test_mock_chat_is_deterministic_and_records_usage(client: TestClient) -> None:
    lease = _heartbeat(client)
    headers = _worker_headers(lease)
    job = client.get("/api/v1/worker/jobs/next", headers=headers).json()["job"]
    headers["X-Altas-Job-ID"] = job["id"]
    headers["X-Altas-Claim-Token"] = job["claim_token"]
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


@pytest.mark.parametrize(
    "unsafe_fields",
    [
        {"n": 100},
        {"extra_body": {"provider": {"allow_fallbacks": True}}},
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
