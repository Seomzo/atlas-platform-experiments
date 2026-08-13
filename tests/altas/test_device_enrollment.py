from __future__ import annotations

import base64
import secrets
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from altas.control_plane import ControlPlaneSettings, create_app
from altas.control_plane.database import Database
from altas.control_plane.identity import DeterministicIdentityProvider
from altas.control_plane.repository import (
    DEMO_IDENTITY_ISSUER,
    DEMO_MEMBERSHIP_ID,
    DEMO_STORE_ID,
    DEMO_TENANT_ID,
    DEMO_UNENTITLED_STORE_ID,
    DEMO_USER_ID,
    DEMO_USER_SUBJECT,
)
from altas.control_plane.security import (
    DeviceSessionSigner,
    InvalidDeviceSession,
    device_key_rotation_challenge,
    device_session_challenge,
    hash_secret,
)


ADMIN_TOKEN = "test-admin-token"


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _key_pair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, _b64encode(public_key)


def _sign(private_key: Ed25519PrivateKey, message: bytes) -> str:
    return _b64encode(private_key.sign(message))


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    return tmp_path / "device-enrollment.sqlite3"


@pytest.fixture
def app(database_path: Path):
    return create_app(
        ControlPlaneSettings(
            database_path=database_path,
            lease_signing_key=b"device-enrollment-test-signing-key!",
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


def _admin_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


def _account_token(client: TestClient, subject: str = DEMO_USER_SUBJECT) -> str:
    response = client.post(
        "/api/v1/dev/identity/token",
        headers=_admin_headers(),
        json={"subject": subject},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def _account_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _create_phone_enrollment(
    client: TestClient,
    account_token: str,
    *,
    store_id: str = DEMO_STORE_ID,
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/account/enrollments",
        headers=_account_headers(account_token),
        json={
            "store_id": store_id,
            "device_class": "phone",
            "device_name": "Test iPhone",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _redeem(
    client: TestClient,
    enrollment_token: str,
    public_key: str,
) -> Any:
    return client.post(
        "/api/v1/device/enrollments/redeem",
        json={
            "enrollment_token": enrollment_token,
            "public_key": public_key,
            "platform": "ios",
            "platform_version": "26.6",
            "app_version": "0.1-test",
        },
    )


def _device_session(
    client: TestClient,
    *,
    device_id: str,
    private_key: Ed25519PrivateKey,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> Any:
    proof_timestamp = int(time.time()) if timestamp is None else timestamp
    proof_nonce = nonce or secrets.token_urlsafe(24)
    signature = _sign(
        private_key,
        device_session_challenge(
            device_id=device_id,
            timestamp=proof_timestamp,
            nonce=proof_nonce,
        ),
    )
    return client.post(
        "/api/v1/device/sessions",
        json={
            "device_id": device_id,
            "timestamp": proof_timestamp,
            "nonce": proof_nonce,
            "signature": signature,
        },
    )


def test_account_context_and_phone_enrollment_are_server_scoped(
    client: TestClient,
    database_path: Path,
) -> None:
    account_token = _account_token(client)
    headers = _account_headers(account_token)

    context = client.get("/api/v1/account/context", headers=headers)
    assert context.status_code == 200
    assert context.json()["memberships"] == [
        {
            "id": DEMO_MEMBERSHIP_ID,
            "role": "owner",
            "tenant": {
                "id": DEMO_TENANT_ID,
                "name": "Atlas Demo Dealer Group",
                "slug": "atlas-demo",
            },
            "stores": [{"id": DEMO_STORE_ID, "name": "Sunrise Volkswagen"}],
        }
    ]

    injected_tenant = client.post(
        "/api/v1/account/enrollments",
        headers=headers,
        json={
            "tenant_id": "tenant_attacker",
            "store_id": DEMO_STORE_ID,
            "device_class": "phone",
            "device_name": "Injected phone",
        },
    )
    ungranted_store = client.post(
        "/api/v1/account/enrollments",
        headers=headers,
        json={
            "store_id": DEMO_UNENTITLED_STORE_ID,
            "device_class": "phone",
            "device_name": "Wrong store phone",
        },
    )
    assert injected_tenant.status_code == 422
    assert ungranted_store.status_code == 403
    assert ungranted_store.json()["detail"]["code"] == "store_access_denied"

    created = _create_phone_enrollment(client, account_token)
    enrollment = created["enrollment"]
    enrollment_token = created["redemption"]["token"]
    assert enrollment["tenant_id"] == DEMO_TENANT_ID
    assert enrollment["store_id"] == DEMO_STORE_ID
    assert created["redemption"]["one_time"] is True

    with sqlite3.connect(database_path) as connection:
        stored = connection.execute(
            "SELECT token_hash FROM device_enrollments WHERE id = ?",
            (enrollment["id"],),
        ).fetchone()[0]
    assert stored == hash_secret(enrollment_token)
    assert stored != enrollment_token
    assert enrollment_token.encode() not in database_path.read_bytes()

    private_key, public_key = _key_pair()
    redeemed = _redeem(client, enrollment_token, public_key)
    assert redeemed.status_code == 201, redeemed.text
    device = redeemed.json()["device"]
    assert device["device_class"] == "phone"
    assert device["credential_kind"] == "ed25519"
    assert "public_key_b64" not in device
    assert "secret_hash" not in device

    nonce = secrets.token_urlsafe(24)
    session = _device_session(
        client,
        device_id=device["id"],
        private_key=private_key,
        nonce=nonce,
    )
    replay = _device_session(
        client,
        device_id=device["id"],
        private_key=private_key,
        nonce=nonce,
    )
    assert session.status_code == 200, session.text
    assert replay.status_code == 401
    assert replay.json()["detail"]["code"] == "device_proof_invalid"

    phone_as_worker = client.post(
        "/api/v1/worker/heartbeat",
        headers={"Authorization": f"Bearer {session.json()['access_token']}"},
        json={
            "tenant_id": DEMO_TENANT_ID,
            "store_id": DEMO_STORE_ID,
            "agent_id": "agent_not_phone_controlled",
            "worker_version": "0.1-test",
        },
    )
    assert phone_as_worker.status_code == 403
    assert phone_as_worker.json()["detail"]["code"] == "worker_device_required"

    audit = client.get(
        f"/api/v1/admin/audit_logs?tenant_id={DEMO_TENANT_ID}",
        headers=_admin_headers(),
    ).json()["items"]
    lifecycle = [
        item
        for item in audit
        if item["action"] in {"device.enrollment.create", "device.enrollment.redeem"}
    ]
    assert len(lifecycle) == 2
    assert {item["correlation_id"] for item in lifecycle} == {
        enrollment["correlation_id"]
    }
    assert all(item["user_id"] == DEMO_USER_ID for item in lifecycle)


def test_enrolled_worker_uses_device_proof_then_existing_scoped_lease(
    client: TestClient,
    app,
) -> None:
    agent_id = "agent_enrollment_test"
    now = "2026-08-12T00:00:00Z"
    with app.state.repository.database.transaction(immediate=True) as connection:
        connection.execute(
            """
            INSERT INTO agents
                (id, tenant_id, store_id, device_id, name, status,
                 created_at, updated_at)
            VALUES (?, ?, ?, NULL, 'Enrollment worker', 'active', ?, ?)
            """,
            (agent_id, DEMO_TENANT_ID, DEMO_STORE_ID, now, now),
        )
    account_token = _account_token(client)
    created = client.post(
        "/api/v1/account/enrollments",
        headers=_account_headers(account_token),
        json={
            "store_id": DEMO_STORE_ID,
            "agent_id": agent_id,
            "device_class": "worker",
            "device_name": "Replacement Mac",
        },
    )
    assert created.status_code == 201, created.text
    private_key, public_key = _key_pair()
    redeemed = _redeem(
        client,
        created.json()["redemption"]["token"],
        public_key,
    )
    assert redeemed.status_code == 201, redeemed.text
    device_id = redeemed.json()["device"]["id"]

    session = _device_session(
        client,
        device_id=device_id,
        private_key=private_key,
    )
    assert session.status_code == 200, session.text
    heartbeat = client.post(
        "/api/v1/worker/heartbeat",
        headers={"Authorization": f"Bearer {session.json()['access_token']}"},
        json={
            "tenant_id": DEMO_TENANT_ID,
            "store_id": DEMO_STORE_ID,
            "agent_id": agent_id,
            "worker_version": "0.2-enrollment-test",
            "health_status": "healthy",
        },
    )
    assert heartbeat.status_code == 200, heartbeat.text
    lease = heartbeat.json()["lease"]
    claims = app.state.lease_signer.verify(lease["token"])
    assert claims.device_id == device_id
    assert claims.tenant_id == DEMO_TENANT_ID
    assert claims.store_id == DEMO_STORE_ID
    assert claims.agent_id == agent_id

    with app.state.repository.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE agents SET device_id = NULL WHERE id = ?", (agent_id,)
        )
    unbound_agent = client.post(
        "/api/v1/worker/heartbeat",
        headers={"Authorization": f"Bearer {session.json()['access_token']}"},
        json={
            "tenant_id": DEMO_TENANT_ID,
            "store_id": DEMO_STORE_ID,
            "agent_id": agent_id,
            "worker_version": "0.2-enrollment-test",
        },
    )
    assert unbound_agent.status_code == 403
    assert unbound_agent.json()["detail"]["code"] == "agent_inactive"


def test_enrollment_expiry_replay_and_revoked_user_fail_closed(
    client: TestClient,
    app,
) -> None:
    account_token = _account_token(client)
    expired = _create_phone_enrollment(client, account_token)
    with app.state.repository.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE device_enrollments SET expires_at = ? WHERE id = ?",
            ("2000-01-01T00:00:00Z", expired["enrollment"]["id"]),
        )
    _, expired_public_key = _key_pair()
    expired_response = _redeem(
        client,
        expired["redemption"]["token"],
        expired_public_key,
    )
    assert expired_response.status_code == 409
    assert expired_response.json()["detail"]["code"] == ("enrollment_not_redeemable")

    single_use = _create_phone_enrollment(client, account_token)
    _, public_key = _key_pair()
    first = _redeem(client, single_use["redemption"]["token"], public_key)
    replay = _redeem(client, single_use["redemption"]["token"], public_key)
    assert first.status_code == 201
    assert replay.status_code == 409
    assert replay.json()["detail"] == {"code": "enrollment_not_redeemable"}

    revoked = _create_phone_enrollment(client, account_token)
    with app.state.repository.database.transaction(immediate=True) as connection:
        connection.execute(
            "UPDATE users SET status = 'revoked' WHERE id = ?", (DEMO_USER_ID,)
        )
    context = client.get(
        "/api/v1/account/context", headers=_account_headers(account_token)
    )
    _, revoked_public_key = _key_pair()
    revoked_redemption = _redeem(
        client,
        revoked["redemption"]["token"],
        revoked_public_key,
    )
    assert context.status_code == 401
    assert revoked_redemption.status_code == 409
    assert revoked_redemption.json()["detail"]["code"] == ("enrollment_not_redeemable")


def test_wrong_tenant_store_role_and_device_revocation_are_live_checked(
    client: TestClient,
    app,
) -> None:
    now = "2026-08-12T00:00:00Z"
    member_user_id = "user_demo_member"
    member_subject = "atlas-demo-member"
    member_membership_id = "membership_demo_member"
    foreign_tenant_id = "tenant_foreign"
    foreign_store_id = "store_foreign"
    with app.state.repository.database.transaction(immediate=True) as connection:
        connection.execute(
            "INSERT INTO users(id, issuer, subject, display_name, status, "
            "created_at, updated_at) VALUES (?, ?, ?, 'Demo Member', 'active', ?, ?)",
            (member_user_id, DEMO_IDENTITY_ISSUER, member_subject, now, now),
        )
        connection.execute(
            "INSERT INTO memberships(id, user_id, tenant_id, role, status, "
            "created_at, updated_at) VALUES (?, ?, ?, 'member', 'active', ?, ?)",
            (
                member_membership_id,
                member_user_id,
                DEMO_TENANT_ID,
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO membership_store_grants(membership_id, store_id, status, "
            "created_at, updated_at) VALUES (?, ?, 'active', ?, ?)",
            (member_membership_id, DEMO_STORE_ID, now, now),
        )
        connection.execute(
            "INSERT INTO tenants(id, name, slug, status, created_at, updated_at) "
            "VALUES (?, 'Foreign tenant', 'foreign', 'active', ?, ?)",
            (foreign_tenant_id, now, now),
        )
        connection.execute(
            "INSERT INTO stores(id, tenant_id, name, status, created_at, updated_at) "
            "VALUES (?, ?, 'Foreign store', 'active', ?, ?)",
            (foreign_store_id, foreign_tenant_id, now, now),
        )

    owner_token = _account_token(client)
    member_token = _account_token(client, member_subject)
    cross_tenant = client.post(
        "/api/v1/account/enrollments",
        headers=_account_headers(owner_token),
        json={
            "store_id": foreign_store_id,
            "device_class": "phone",
            "device_name": "Cross tenant phone",
        },
    )
    member_phone = _create_phone_enrollment(client, member_token)
    member_worker = client.post(
        "/api/v1/account/enrollments",
        headers=_account_headers(member_token),
        json={
            "store_id": DEMO_STORE_ID,
            "agent_id": "agent_any",
            "device_class": "worker",
            "device_name": "Unauthorized worker",
        },
    )
    assert cross_tenant.status_code == 403
    assert cross_tenant.json()["detail"]["code"] == "store_access_denied"
    assert member_worker.status_code == 403
    assert member_worker.json()["detail"]["code"] == "role_not_allowed"

    private_key, public_key = _key_pair()
    redeemed = _redeem(
        client,
        member_phone["redemption"]["token"],
        public_key,
    )
    device_id = redeemed.json()["device"]["id"]
    session = _device_session(
        client,
        device_id=device_id,
        private_key=private_key,
    )
    member_revoke = client.post(
        f"/api/v1/account/devices/{device_id}/revoke",
        headers=_account_headers(member_token),
        json={"reason": "member should not revoke"},
    )
    owner_revoke = client.post(
        f"/api/v1/account/devices/{device_id}/revoke",
        headers=_account_headers(owner_token),
        json={"reason": "lost test phone"},
    )
    assert member_revoke.status_code == 404
    assert owner_revoke.status_code == 200
    assert owner_revoke.json()["device"]["status"] == "disabled"

    proof_after_revocation = _device_session(
        client,
        device_id=device_id,
        private_key=private_key,
    )
    rotate_after_revocation = client.post(
        "/api/v1/device/credentials/rotate",
        headers={"Authorization": f"Bearer {session.json()['access_token']}"},
        json={
            "new_public_key": _key_pair()[1],
            "timestamp": int(time.time()),
            "nonce": secrets.token_urlsafe(24),
            "signature": "A" * 86,
        },
    )
    assert proof_after_revocation.status_code == 401
    assert rotate_after_revocation.status_code == 401


def test_malformed_stale_and_wrong_key_proofs_are_indistinguishable(
    client: TestClient,
) -> None:
    account_token = _account_token(client)
    created = _create_phone_enrollment(client, account_token)
    private_key, public_key = _key_pair()
    redeemed = _redeem(client, created["redemption"]["token"], public_key)
    device_id = redeemed.json()["device"]["id"]

    wrong_private_key, _ = _key_pair()
    wrong_key = _device_session(
        client,
        device_id=device_id,
        private_key=wrong_private_key,
    )
    stale = _device_session(
        client,
        device_id=device_id,
        private_key=private_key,
        timestamp=0,
    )
    malformed = client.post(
        "/api/v1/device/sessions",
        json={
            "device_id": device_id,
            "timestamp": int(time.time()),
            "nonce": secrets.token_urlsafe(24),
            "signature": "not-base64",
        },
    )
    assert wrong_key.status_code == 401
    assert stale.status_code == 401
    assert (
        wrong_key.json()["detail"]
        == stale.json()["detail"]
        == {"code": "device_proof_invalid"}
    )
    assert malformed.status_code == 422


def test_concurrent_enrollment_redemption_creates_exactly_one_device(
    client: TestClient,
    app,
) -> None:
    account_token = _account_token(client)
    created = _create_phone_enrollment(client, account_token)
    enrollment_id = created["enrollment"]["id"]
    enrollment_token = created["redemption"]["token"]
    _, public_key = _key_pair()
    callers = 8
    barrier = Barrier(callers)

    def redeem_once() -> int:
        barrier.wait()
        return int(_redeem(client, enrollment_token, public_key).status_code)

    with ThreadPoolExecutor(max_workers=callers) as executor:
        statuses = list(executor.map(lambda _: redeem_once(), range(callers)))

    assert statuses.count(201) == 1
    assert statuses.count(409) == callers - 1
    with app.state.repository.database.connect() as connection:
        row = connection.execute(
            "SELECT status, redeemed_device_id FROM device_enrollments WHERE id = ?",
            (enrollment_id,),
        ).fetchone()
        device_count = connection.execute(
            "SELECT COUNT(*) FROM devices WHERE id = ?",
            (row["redeemed_device_id"],),
        ).fetchone()[0]
    assert row["status"] == "redeemed"
    assert device_count == 1


def test_key_rotation_invalidates_old_session_and_requires_new_key(
    client: TestClient,
) -> None:
    account_token = _account_token(client)
    created = _create_phone_enrollment(client, account_token)
    old_private_key, old_public_key = _key_pair()
    redeemed = _redeem(
        client,
        created["redemption"]["token"],
        old_public_key,
    )
    device_id = redeemed.json()["device"]["id"]
    old_session = _device_session(
        client,
        device_id=device_id,
        private_key=old_private_key,
    ).json()["access_token"]

    new_private_key, new_public_key = _key_pair()
    timestamp = int(time.time())
    nonce = secrets.token_urlsafe(24)
    rotation = client.post(
        "/api/v1/device/credentials/rotate",
        headers={"Authorization": f"Bearer {old_session}"},
        json={
            "new_public_key": new_public_key,
            "timestamp": timestamp,
            "nonce": nonce,
            "signature": _sign(
                new_private_key,
                device_key_rotation_challenge(
                    device_id=device_id,
                    new_public_key_b64=new_public_key,
                    timestamp=timestamp,
                    nonce=nonce,
                ),
            ),
        },
    )
    assert rotation.status_code == 200, rotation.text
    assert rotation.json()["device"]["credential_version"] == 2

    old_key_proof = _device_session(
        client,
        device_id=device_id,
        private_key=old_private_key,
    )
    new_key_proof = _device_session(
        client,
        device_id=device_id,
        private_key=new_private_key,
    )
    assert old_key_proof.status_code == 401
    assert new_key_proof.status_code == 200


def test_development_identity_assertion_expires_without_sleeping(
    tmp_path: Path,
) -> None:
    clock = [1_000.0]
    signing_key = b"identity-expiry-test-signing-key-material"
    provider = DeterministicIdentityProvider(signing_key, clock=lambda: clock[0])
    app = create_app(
        ControlPlaneSettings(
            database_path=tmp_path / "identity-expiry.sqlite3",
            lease_signing_key=signing_key,
            admin_token=ADMIN_TOKEN,
            seed_demo_data=True,
            mock_model=True,
            account_session_ttl_seconds=15,
        ),
        identity_verifier=provider,
    )
    with TestClient(app) as test_client:
        token = _account_token(test_client)
        allowed = test_client.get(
            "/api/v1/account/context", headers=_account_headers(token)
        )
        clock[0] = 1_015.0
        expired = test_client.get(
            "/api/v1/account/context", headers=_account_headers(token)
        )
    assert allowed.status_code == 200
    assert expired.status_code == 401


def test_device_session_signature_and_expiry_fail_closed_without_sleeping() -> None:
    clock = [2_000.0]
    signer = DeviceSessionSigner(
        b"device-session-unit-test-signing-key!",
        clock=lambda: clock[0],
    )
    token, issued = signer.issue(
        device_id="device-unit",
        credential_version=3,
        ttl_seconds=30,
        nonce="server-session-nonce",
    )
    assert signer.verify(token) == issued

    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")
    with pytest.raises(InvalidDeviceSession, match="device_session_invalid"):
        signer.verify(tampered)

    clock[0] = 2_030.0
    with pytest.raises(InvalidDeviceSession, match="device_session_expired"):
        signer.verify(token)


def test_existing_device_table_is_upgraded_in_place(tmp_path: Path) -> None:
    database_path = tmp_path / "legacy-control-plane.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE tenants (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE stores (
                id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, name TEXT NOT NULL,
                external_ref TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, UNIQUE (tenant_id, external_ref)
            );
            CREATE TABLE devices (
                id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, store_id TEXT NOT NULL,
                name TEXT NOT NULL, secret_hash TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL, worker_version TEXT, last_heartbeat_at TEXT,
                health_status TEXT, metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            """
        )
    Database(database_path).initialize()
    with sqlite3.connect(database_path) as connection:
        device_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(devices)")
        }
        audit_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(audit_logs)")
        }
        enrollment_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='device_enrollments'"
        ).fetchone()
    assert {
        "device_class",
        "credential_kind",
        "public_key_b64",
        "public_key_thumbprint",
        "credential_version",
        "credential_expires_at",
        "credential_revoked_at",
        "enrolled_by_user_id",
    } <= device_columns
    assert {"user_id", "correlation_id"} <= audit_columns
    assert enrollment_table is not None
