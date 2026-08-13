from __future__ import annotations

import base64
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from altas.control_plane import ControlPlaneSettings, create_app
from altas.control_plane.repository import (
    DEMO_AGENT_ID,
    DEMO_DEVICE_ID,
    DEMO_STORE_ID,
    DEMO_SUBSCRIPTION_ID,
    DEMO_TENANT_ID,
    DEMO_USER_SUBJECT,
)
from altas.control_plane.security import device_session_challenge
from altas.fixed_ops import SYNTHETIC_EXPORT_CAPABILITY, SYNTHETIC_EXPORT_WORKFLOW
from altas.managed.actions import ManagedAction


ADMIN_TOKEN = "approval-test-admin-token"


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _key_pair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, _encode(public_key)


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "managed-approvals.sqlite3"


@pytest.fixture
def app(database_path: Path):
    application = create_app(
        ControlPlaneSettings(
            database_path=database_path,
            lease_signing_key=b"managed-approval-test-signing-key",
            admin_token=ADMIN_TOKEN,
            seed_demo_data=True,
            mock_model=True,
            lease_ttl_seconds=120,
            managed_approval_ttl_seconds=60,
        )
    )
    with application.state.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE agents SET device_id = NULL WHERE id = ?", (DEMO_AGENT_ID,)
        )
        connection.execute(
            "UPDATE devices SET status = 'disabled' WHERE id = ?", (DEMO_DEVICE_ID,)
        )
    return application


@pytest.fixture
def client(app) -> TestClient:
    with TestClient(app) as test_client:
        yield test_client


def _account_token(client: TestClient) -> str:
    response = client.post(
        "/api/v1/dev/identity/token",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json={"subject": DEMO_USER_SUBJECT},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _enroll(
    client: TestClient,
    *,
    account_token: str,
    device_class: str,
    name: str,
) -> dict[str, Any]:
    private_key, public_key = _key_pair()
    request: dict[str, Any] = {
        "store_id": DEMO_STORE_ID,
        "device_class": device_class,
        "device_name": name,
    }
    if device_class == "worker":
        request["agent_id"] = DEMO_AGENT_ID
    enrollment = client.post(
        "/api/v1/account/enrollments",
        headers={"Authorization": f"Bearer {account_token}"},
        json=request,
    )
    assert enrollment.status_code == 201, enrollment.text
    redeemed = client.post(
        "/api/v1/device/enrollments/redeem",
        json={
            "enrollment_token": enrollment.json()["redemption"]["token"],
            "public_key": public_key,
            "platform": "ios" if device_class == "phone" else "macos",
            "platform_version": "26.6",
            "app_version": "approval-test",
        },
    )
    assert redeemed.status_code == 201, redeemed.text
    device = redeemed.json()["device"]
    timestamp = int(time.time())
    nonce = secrets.token_urlsafe(24)
    signature = _encode(
        private_key.sign(
            device_session_challenge(
                device_id=device["id"],
                timestamp=timestamp,
                nonce=nonce,
            )
        )
    )
    session = client.post(
        "/api/v1/device/sessions",
        json={
            "device_id": device["id"],
            "timestamp": timestamp,
            "nonce": nonce,
            "signature": signature,
        },
    )
    assert session.status_code == 200, session.text
    return {"device": device, "session": session.json()["access_token"]}


def _setup(client: TestClient) -> dict[str, Any]:
    account_token = _account_token(client)
    phone = _enroll(
        client,
        account_token=account_token,
        device_class="phone",
        name="Approval Phone",
    )
    worker = _enroll(
        client,
        account_token=account_token,
        device_class="worker",
        name="Approval Worker",
    )
    phone_headers = {
        "Authorization": f"Bearer {account_token}",
        "X-Atlas-Device-Session": phone["session"],
    }
    pairing = client.post(
        "/api/v1/mobile/relay/pairings",
        headers=phone_headers,
        json={"worker_device_id": worker["device"]["id"]},
    )
    assert pairing.status_code == 201, pairing.text
    relay_session = client.post(
        "/api/v1/mobile/relay/sessions",
        headers={**phone_headers, "Idempotency-Key": "approval-relay-session"},
        json={"pairing_id": pairing.json()["pairing"]["id"]},
    )
    assert relay_session.status_code == 201, relay_session.text
    session_id = relay_session.json()["session"]["id"]
    app = client.app
    app.state.relay_repository.append_worker_event(
        worker_device_id=worker["device"]["id"],
        session_id=session_id,
        source_event_id="approval-test:session-created",
        event_type="session.created",
        command_id=relay_session.json()["command"]["id"],
        payload={"gateway_session_id": "approval-local-session"},
    )
    with app.state.database.transaction(immediate=True) as connection:
        timestamp = (
            datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        )
        job_id = f"job_synthetic_approved_export_{secrets.token_hex(4)}"
        connection.execute(
            """
            INSERT INTO jobs
                (id, tenant_id, store_id, agent_id, device_id, capability,
                 status, payload_json, requested_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, 'approval-test', ?, ?)
            """,
            (
                job_id,
                DEMO_TENANT_ID,
                DEMO_STORE_ID,
                DEMO_AGENT_ID,
                worker["device"]["id"],
                SYNTHETIC_EXPORT_CAPABILITY,
                (
                    '{"relay_session_id":"'
                    + session_id
                    + '","report_id":"report-demo-1","workflow":"'
                    + SYNTHETIC_EXPORT_WORKFLOW
                    + '"}'
                ),
                timestamp,
                timestamp,
            ),
        )
    worker_base = {"Authorization": f"Bearer {worker['session']}"}
    heartbeat = client.post(
        "/api/v1/worker/heartbeat",
        headers=worker_base,
        json={
            "tenant_id": DEMO_TENANT_ID,
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "worker_version": "approval-test",
            "health_status": "healthy",
            "metadata": {},
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text
    lease = heartbeat.json()["lease"]["token"]
    poll_headers = {
        **worker_base,
        "X-Atlas-Tenant-ID": DEMO_TENANT_ID,
        "X-Atlas-Store-ID": DEMO_STORE_ID,
        "X-Atlas-Agent-ID": DEMO_AGENT_ID,
        "X-Atlas-Lease": lease,
    }
    claimed = client.get(
        "/api/v1/worker/jobs/next",
        headers=poll_headers,
        params={"capability": SYNTHETIC_EXPORT_CAPABILITY},
    )
    assert claimed.status_code == 200, claimed.text
    job = claimed.json()["job"]
    worker_headers = {
        **worker_base,
        "X-Atlas-Tenant-ID": DEMO_TENANT_ID,
        "X-Atlas-Store-ID": DEMO_STORE_ID,
        "X-Atlas-Agent-ID": DEMO_AGENT_ID,
        "X-Atlas-Job-ID": job_id,
        "X-Atlas-Lease": lease,
        "X-Atlas-Claim-Token": job["claim_token"],
    }
    action = ManagedAction(
        kind="download_export",
        operation="export",
        summary="Export the synthetic fixed-operations report",
        target_type="synthetic_report",
        target_id="report-demo-1",
        target_label="Synthetic report report-demo-1",
    )
    return {
        "account_token": account_token,
        "phone": phone,
        "phone_headers": phone_headers,
        "worker": worker,
        "worker_headers": worker_headers,
        "session_id": session_id,
        "job": job,
        "action": action,
    }


def _request_approval(
    client: TestClient,
    setup: dict[str, Any],
    *,
    idempotency_key: str = "approval-request-one",
) -> Any:
    return client.post(
        "/api/v1/worker/approvals",
        headers={**setup["worker_headers"], "Idempotency-Key": idempotency_key},
        json={
            "relay_session_id": setup["session_id"],
            "action": setup["action"].to_mapping(),
        },
    )


def test_exact_approval_request_phone_decision_consume_and_replay_resistance(
    client: TestClient,
    database_path: Path,
) -> None:
    setup = _setup(client)
    requested = _request_approval(client, setup)
    assert requested.status_code == 201, requested.text
    approval = requested.json()["approval"]
    assert approval["status"] == "pending"
    assert approval["job_attempt"] == 1
    assert approval["action_digest"] == setup["action"].digest()
    assert approval["action"] == setup["action"].to_mapping()

    wrong_store = _request_approval(
        client,
        {
            **setup,
            "worker_headers": {
                **setup["worker_headers"],
                "X-Atlas-Store-ID": "store-outside-worker-scope",
            },
        },
        idempotency_key="approval-request-wrong-store",
    )
    assert wrong_store.status_code == 403

    wrong_job = _request_approval(
        client,
        {
            **setup,
            "worker_headers": {
                **setup["worker_headers"],
                "X-Atlas-Job-ID": "job-outside-worker-scope",
            },
        },
        idempotency_key="approval-request-wrong-job",
    )
    assert wrong_job.status_code == 403

    replayed = _request_approval(client, setup)
    assert replayed.status_code == 201
    assert replayed.json()["idempotent_replay"] is True
    assert replayed.json()["approval"]["id"] == approval["id"]
    modified = client.post(
        "/api/v1/worker/approvals",
        headers={
            **setup["worker_headers"],
            "Idempotency-Key": "approval-request-one",
        },
        json={
            "relay_session_id": setup["session_id"],
            "action": {
                **setup["action"].to_mapping(),
                "target_id": "report-mutated",
            },
        },
    )
    assert modified.status_code == 409

    viewed = client.get(
        f"/api/v1/mobile/relay/approvals/{approval['id']}",
        headers=setup["phone_headers"],
    )
    assert viewed.status_code == 200
    assert viewed.json()["approval"]["id"] == approval["id"]

    response_headers = {
        **setup["phone_headers"],
        "Idempotency-Key": "approval-decision-one",
    }
    bad_digest = client.post(
        f"/api/v1/mobile/relay/approvals/{approval['id']}/responses",
        headers=response_headers,
        json={
            "decision": "approve",
            "reason": "user_approved",
            "expected_version": approval["version"],
            "action_digest": "0" * 64,
        },
    )
    assert bad_digest.status_code == 409
    assert bad_digest.json()["detail"]["code"] == "managed_approval_action_modified"

    decided = client.post(
        f"/api/v1/mobile/relay/approvals/{approval['id']}/responses",
        headers=response_headers,
        json={
            "decision": "approve",
            "reason": "user_approved",
            "expected_version": approval["version"],
            "action_digest": approval["action_digest"],
        },
    )
    assert decided.status_code == 200, decided.text
    approved = decided.json()["approval"]
    assert approved["status"] == "approved"
    assert approved["version"] == 2
    same_decision = client.post(
        f"/api/v1/mobile/relay/approvals/{approval['id']}/responses",
        headers=response_headers,
        json={
            "decision": "approve",
            "reason": "user_approved",
            "expected_version": approval["version"],
            "action_digest": approval["action_digest"],
        },
    )
    assert same_decision.status_code == 200
    assert same_decision.json()["idempotent_replay"] is True

    consumed = client.post(
        f"/api/v1/worker/approvals/{approval['id']}/consume",
        headers=setup["worker_headers"],
        json={
            "expected_version": approved["version"],
            "action_digest": approved["action_digest"],
            "action": setup["action"].to_mapping(),
        },
    )
    assert consumed.status_code == 200, consumed.text
    authorization = consumed.json()["authorization"]
    assert authorization["approval_id"] == approval["id"]
    assert authorization["job_attempt"] == 1
    second_consume = client.post(
        f"/api/v1/worker/approvals/{approval['id']}/consume",
        headers=setup["worker_headers"],
        json={
            "expected_version": approved["version"],
            "action_digest": approved["action_digest"],
            "action": setup["action"].to_mapping(),
        },
    )
    assert second_consume.status_code == 409
    assert second_consume.json()["detail"]["code"] == (
        "managed_approval_already_consumed"
    )

    decision_retry_after_consume = client.post(
        f"/api/v1/mobile/relay/approvals/{approval['id']}/responses",
        headers=response_headers,
        json={
            "decision": "approve",
            "reason": "user_approved",
            "expected_version": approval["version"],
            "action_digest": approval["action_digest"],
        },
    )
    assert decision_retry_after_consume.status_code == 200
    assert decision_retry_after_consume.json()["idempotent_replay"] is True
    assert decision_retry_after_consume.json()["approval"]["status"] == "consumed"

    database_bytes = b"".join(
        path.read_bytes()
        for path in database_path.parent.glob(f"{database_path.name}*")
        if path.is_file()
    )
    assert b"Synthetic report report-demo-1" not in database_bytes

    events = client.get(
        f"/api/v1/mobile/relay/sessions/{setup['session_id']}/events",
        headers=setup["phone_headers"],
    )
    assert events.status_code == 200
    event_types = [item["event_type"] for item in events.json()["items"]]
    assert "approval.requested" in event_types
    assert "approval.resolved" in event_types
    assert "approval.consumed" in event_types


def test_concurrent_responses_have_one_winner_and_other_phone_cannot_see(
    client: TestClient,
) -> None:
    setup = _setup(client)
    approval = _request_approval(client, setup).json()["approval"]
    barrier = threading.Barrier(2)

    def decide(decision: str) -> tuple[int, str]:
        barrier.wait(timeout=5)
        response = client.post(
            f"/api/v1/mobile/relay/approvals/{approval['id']}/responses",
            headers={
                **setup["phone_headers"],
                "Idempotency-Key": f"race-{decision}",
            },
            json={
                "decision": decision,
                "reason": "user_approved" if decision == "approve" else "user_denied",
                "expected_version": approval["version"],
                "action_digest": approval["action_digest"],
            },
        )
        code = (
            str(response.json().get("detail", {}).get("code", ""))
            if response.status_code != 200
            else "winner"
        )
        return response.status_code, code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(decide, ["approve", "deny"]))
    assert sorted(status for status, _ in results) == [200, 409]
    assert {code for _, code in results} & {
        "managed_approval_race_lost",
        "managed_approval_already_resolved",
    }

    second_phone = _enroll(
        client,
        account_token=setup["account_token"],
        device_class="phone",
        name="Other Approval Phone",
    )
    other_headers = {
        "Authorization": f"Bearer {setup['account_token']}",
        "X-Atlas-Device-Session": second_phone["session"],
    }
    hidden = client.get(
        f"/api/v1/mobile/relay/approvals/{approval['id']}",
        headers=other_headers,
    )
    assert hidden.status_code == 404


def test_expiry_modified_action_superseded_attempt_and_revocation_fail_closed(
    client: TestClient,
    app,
) -> None:
    setup = _setup(client)
    approval = _request_approval(client, setup).json()["approval"]
    with app.state.database.transaction(immediate=True) as connection:
        expired_at = (
            (datetime.now(UTC) - timedelta(seconds=1))
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        connection.execute(
            "UPDATE managed_approvals SET expires_at = ? WHERE id = ?",
            (expired_at, approval["id"]),
        )
    late = client.post(
        f"/api/v1/mobile/relay/approvals/{approval['id']}/responses",
        headers={**setup["phone_headers"], "Idempotency-Key": "late-decision"},
        json={
            "decision": "approve",
            "reason": "user_approved",
            "expected_version": 1,
            "action_digest": approval["action_digest"],
        },
    )
    assert late.status_code == 410
    with app.state.database.connect() as connection:
        expired_row = connection.execute(
            "SELECT status, version FROM managed_approvals WHERE id = ?",
            (approval["id"],),
        ).fetchone()
    assert dict(expired_row) == {"status": "expired", "version": 2}

    # Fresh setup so the single demo agent has a new enrolled worker.
    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE agents SET device_id = NULL WHERE id = ?", (DEMO_AGENT_ID,)
        )
        connection.execute(
            "UPDATE devices SET status = 'disabled' WHERE id = ?",
            (setup["worker"]["device"]["id"],),
        )
    second = _setup(client)
    requested = _request_approval(client, second).json()["approval"]
    approved = client.post(
        f"/api/v1/mobile/relay/approvals/{requested['id']}/responses",
        headers={**second["phone_headers"], "Idempotency-Key": "approve-two"},
        json={
            "decision": "approve",
            "reason": "user_approved",
            "expected_version": 1,
            "action_digest": requested["action_digest"],
        },
    ).json()["approval"]
    mutated = client.post(
        f"/api/v1/worker/approvals/{requested['id']}/consume",
        headers=second["worker_headers"],
        json={
            "expected_version": approved["version"],
            "action_digest": requested["action_digest"],
            "action": {
                **second["action"].to_mapping(),
                "target_id": "report-changed",
            },
        },
    )
    assert mutated.status_code == 409
    assert mutated.json()["detail"]["code"] == "managed_approval_action_modified"

    renewed = client.post(
        "/api/v1/worker/heartbeat",
        headers={"Authorization": second["worker_headers"]["Authorization"]},
        json={
            "tenant_id": DEMO_TENANT_ID,
            "store_id": DEMO_STORE_ID,
            "agent_id": DEMO_AGENT_ID,
            "worker_version": "approval-test-renewed-lease",
            "health_status": "healthy",
            "metadata": {},
        },
    )
    assert renewed.status_code == 200, renewed.text
    stale_lease = client.post(
        f"/api/v1/worker/approvals/{requested['id']}/consume",
        headers={
            **second["worker_headers"],
            "X-Atlas-Lease": renewed.json()["lease"]["token"],
        },
        json={
            "expected_version": approved["version"],
            "action_digest": requested["action_digest"],
            "action": second["action"].to_mapping(),
        },
    )
    assert stale_lease.status_code == 409
    assert stale_lease.json()["detail"]["code"] == ("managed_approval_context_changed")

    superseded = _request_approval(
        client,
        second,
        idempotency_key="approval-request-superseded-attempt",
    ).json()["approval"]
    superseded_approved = client.post(
        f"/api/v1/mobile/relay/approvals/{superseded['id']}/responses",
        headers={
            **second["phone_headers"],
            "Idempotency-Key": "approve-superseded-attempt",
        },
        json={
            "decision": "approve",
            "reason": "user_approved",
            "expected_version": superseded["version"],
            "action_digest": superseded["action_digest"],
        },
    ).json()["approval"]

    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE jobs SET attempt_count = attempt_count + 1 WHERE id = ?",
            (second["job"]["id"],),
        )
    stale_attempt = client.post(
        f"/api/v1/worker/approvals/{superseded['id']}/consume",
        headers=second["worker_headers"],
        json={
            "expected_version": superseded_approved["version"],
            "action_digest": superseded["action_digest"],
            "action": second["action"].to_mapping(),
        },
    )
    assert stale_attempt.status_code == 409
    assert stale_attempt.json()["detail"]["code"] == (
        "managed_approval_context_changed"
    )

    with app.state.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE agents SET device_id = NULL WHERE id = ?", (DEMO_AGENT_ID,)
        )
        connection.execute(
            "UPDATE devices SET status = 'disabled' WHERE id = ?",
            (second["worker"]["device"]["id"],),
        )
    third = _setup(client)
    third_approval = _request_approval(client, third).json()["approval"]
    revoked = client.post(
        f"/api/v1/account/devices/{third['worker']['device']['id']}/revoke",
        headers={"Authorization": f"Bearer {third['account_token']}"},
        json={"reason": "approval revocation test"},
    )
    assert revoked.status_code == 200
    with app.state.database.connect() as connection:
        status_row = connection.execute(
            "SELECT status FROM managed_approvals WHERE id = ?",
            (third_approval["id"],),
        ).fetchone()
    assert status_row["status"] == "canceled"


def test_read_action_needs_policy_but_not_approval(client: TestClient) -> None:
    setup = _setup(client)
    read_action = ManagedAction(
        kind="read",
        operation="read",
        summary="Read advisor performance metrics",
        target_type="store_metrics",
        target_id=DEMO_STORE_ID,
        target_label=f"Store {DEMO_STORE_ID}",
    )
    response = client.post(
        "/api/v1/worker/approvals",
        headers={**setup["worker_headers"], "Idempotency-Key": "read-no-approval"},
        json={
            "relay_session_id": setup["session_id"],
            "action": read_action.to_mapping(),
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == (
        "managed_action_does_not_require_approval"
    )


@pytest.mark.parametrize(
    ("resource", "resource_id"),
    (
        ("agents", DEMO_AGENT_ID),
        ("stores", DEMO_STORE_ID),
        ("entitlements", "entitlement_demo_fixed_ops_synthetic_export"),
        ("subscriptions", DEMO_SUBSCRIPTION_ID),
    ),
)
def test_policy_resource_disable_cancels_pending_approval(
    client: TestClient,
    app,
    resource: str,
    resource_id: str,
) -> None:
    setup = _setup(client)
    approval = _request_approval(client, setup).json()["approval"]

    disabled = client.post(
        f"/api/v1/admin/{resource}/{resource_id}/toggle",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        json={"enabled": False, "reason": "managed approval policy test"},
    )
    assert disabled.status_code == 200, disabled.text

    with app.state.database.connect() as connection:
        status_row = connection.execute(
            "SELECT status, decision_reason FROM managed_approvals WHERE id = ?",
            (approval["id"],),
        ).fetchone()
    assert status_row["status"] == "canceled"
    assert status_row["decision_reason"] in {
        "agent_disabled",
        "store_disabled",
        "entitlement_disabled",
        "subscription_inactive",
    }
