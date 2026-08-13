from __future__ import annotations

import base64
import secrets
import time
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from altas.control_plane import ControlPlaneSettings, create_app
from altas.control_plane.repository import (
    DEMO_AGENT_ID,
    DEMO_DEVICE_ID,
    DEMO_STORE_ID,
    DEMO_USER_SUBJECT,
)
from altas.control_plane.security import device_session_challenge


ADMIN_TOKEN = "relay-test-admin-token"


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
    return tmp_path / "mobile-relay.sqlite3"


@pytest.fixture
def app(database_path: Path):
    application = create_app(
        ControlPlaneSettings(
            database_path=database_path,
            lease_signing_key=b"mobile-relay-test-signing-key-v1",
            admin_token=ADMIN_TOKEN,
            seed_demo_data=True,
            mock_model=True,
            lease_ttl_seconds=120,
            relay_max_pending_commands=2,
            relay_max_inflight_commands=2,
        )
    )
    # The demo seed predates device enrollment and binds the single agent to a
    # legacy worker. Free that fixture agent so this test can enroll an
    # Ed25519-bound worker through the public contract.
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


def _enroll_device(
    client: TestClient,
    *,
    account_token: str,
    device_class: str,
    name: str,
) -> dict[str, Any]:
    private_key, public_key = _key_pair()
    enrollment_body: dict[str, Any] = {
        "store_id": DEMO_STORE_ID,
        "device_class": device_class,
        "device_name": name,
    }
    if device_class == "worker":
        enrollment_body["agent_id"] = DEMO_AGENT_ID
    enrollment = client.post(
        "/api/v1/account/enrollments",
        headers={"Authorization": f"Bearer {account_token}"},
        json=enrollment_body,
    )
    assert enrollment.status_code == 201, enrollment.text
    redeemed = client.post(
        "/api/v1/device/enrollments/redeem",
        json={
            "enrollment_token": enrollment.json()["redemption"]["token"],
            "public_key": public_key,
            "platform": "ios" if device_class == "phone" else "macos",
            "platform_version": "26.6",
            "app_version": "relay-test",
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
    return {
        "device": device,
        "private_key": private_key,
        "session": session.json()["access_token"],
    }


def _setup_relay(client: TestClient) -> dict[str, Any]:
    account_token = _account_token(client)
    phone = _enroll_device(
        client,
        account_token=account_token,
        device_class="phone",
        name="Relay Test Phone",
    )
    worker = _enroll_device(
        client,
        account_token=account_token,
        device_class="worker",
        name="Relay Test Worker",
    )
    phone_headers = {
        "Authorization": f"Bearer {account_token}",
        "X-Atlas-Device-Session": phone["session"],
    }
    pairing_response = client.post(
        "/api/v1/mobile/relay/pairings",
        headers=phone_headers,
        json={"worker_device_id": worker["device"]["id"]},
    )
    assert pairing_response.status_code == 201, pairing_response.text
    return {
        "account_token": account_token,
        "phone": phone,
        "phone_headers": phone_headers,
        "worker": worker,
        "worker_headers": {"Authorization": f"Bearer {worker['session']}"},
        "pairing": pairing_response.json()["pairing"],
    }


def _create_session(
    client: TestClient,
    relay: dict[str, Any],
    *,
    idempotency_key: str = "create-session-1",
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/mobile/relay/sessions",
        headers={**relay["phone_headers"], "Idempotency-Key": idempotency_key},
        json={"pairing_id": relay["pairing"]["id"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_offline_queue_durable_ack_replay_and_encrypted_storage(
    client: TestClient,
    database_path: Path,
) -> None:
    relay = _setup_relay(client)
    created = _create_session(client, relay)
    assert created["worker"]["status"] == "offline"
    assert created["command"]["status"] == "queued"
    session_id = created["session"]["id"]
    create_command_id = created["command"]["id"]

    secret_text = "private mobile prompt 4f7d6b1e"
    message_headers = {
        **relay["phone_headers"],
        "Idempotency-Key": "turn-1",
    }
    submitted = client.post(
        f"/api/v1/mobile/relay/sessions/{session_id}/messages",
        headers=message_headers,
        json={"text": secret_text},
    )
    assert submitted.status_code == 202, submitted.text
    assert submitted.json()["delivery"] == "queued"
    prompt_command_id = submitted.json()["command"]["id"]

    replayed = client.post(
        f"/api/v1/mobile/relay/sessions/{session_id}/messages",
        headers=message_headers,
        json={"text": secret_text},
    )
    assert replayed.status_code == 202
    assert replayed.json()["idempotent_replay"] is True
    assert replayed.json()["command"]["id"] == prompt_command_id
    conflict = client.post(
        f"/api/v1/mobile/relay/sessions/{session_id}/messages",
        headers=message_headers,
        json={"text": "different text"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "relay_idempotency_conflict"

    database_bytes = b"".join(
        path.read_bytes()
        for path in database_path.parent.glob(f"{database_path.name}*")
        if path.is_file()
    )
    assert secret_text.encode() not in database_bytes

    with client.websocket_connect(
        "/api/v1/mobile/relay/worker/connect",
        headers=relay["worker_headers"],
    ) as socket:
        hello = socket.receive_json()
        assert hello["type"] == "hello"
        assert hello["durable_ack_required"] is True
        create_frame = socket.receive_json()
        prompt_frame = socket.receive_json()
        assert create_frame["command"]["id"] == create_command_id
        assert create_frame["command"]["command_type"] == "session.create"
        assert prompt_frame["command"]["id"] == prompt_command_id
        assert prompt_frame["command"]["payload"] == {"text": secret_text}

        socket.send_json({"type": "command.ack", "command_id": create_command_id})
        assert socket.receive_json() == {
            "type": "command.acknowledged",
            "command_id": create_command_id,
        }
        socket.send_json({
            "type": "event",
            "source_event_id": "worker:event:create:1",
            "session_id": session_id,
            "command_id": create_command_id,
            "event_type": "session.created",
            "payload": {"gateway_session_id": "gateway-local-1"},
        })
        create_event_ack = socket.receive_json()
        assert create_event_ack["type"] == "event.ack"

        socket.send_json({"type": "command.ack", "command_id": prompt_command_id})
        assert socket.receive_json()["type"] == "command.acknowledged"
        for source_id, event_type, payload in (
            ("worker:event:message:1", "message.start", {}),
            ("worker:event:message:2", "message.delta", {"text": "Hello"}),
            (
                "worker:event:message:3",
                "message.complete",
                {"text": "Hello from the worker", "status": "complete"},
            ),
        ):
            socket.send_json({
                "type": "event",
                "source_event_id": source_id,
                "session_id": session_id,
                "command_id": prompt_command_id,
                "event_type": event_type,
                "payload": payload,
            })
            ack = socket.receive_json()
            assert ack["type"] == "event.ack"
            if event_type == "message.complete":
                completed_ack = ack

        socket.send_json({
            "type": "event",
            "source_event_id": "worker:event:message:3",
            "session_id": session_id,
            "command_id": prompt_command_id,
            "event_type": "message.complete",
            "payload": {"text": "Hello from the worker", "status": "complete"},
        })
        duplicate_ack = socket.receive_json()
        assert duplicate_ack["event_id"] == completed_ack["event_id"]
        assert duplicate_ack["sequence"] == completed_ack["sequence"]

    session = client.get(
        f"/api/v1/mobile/relay/sessions/{session_id}",
        headers=relay["phone_headers"],
    )
    assert session.status_code == 200
    assert session.json()["session"]["status"] == "active"
    assert session.json()["session"]["gateway_session_id"] == "gateway-local-1"

    command = client.get(
        f"/api/v1/mobile/relay/commands/{prompt_command_id}",
        headers=relay["phone_headers"],
    )
    assert command.status_code == 200
    assert command.json()["command"]["status"] == "succeeded"

    first_page = client.get(
        f"/api/v1/mobile/relay/sessions/{session_id}/events",
        headers=relay["phone_headers"],
        params={"limit": 2},
    )
    assert first_page.status_code == 200
    first_payload = first_page.json()
    assert [item["sequence"] for item in first_payload["items"]] == [1, 2]
    assert first_payload["has_more"] is True
    second_page = client.get(
        f"/api/v1/mobile/relay/sessions/{session_id}/events",
        headers=relay["phone_headers"],
        params={"cursor": first_payload["next_cursor"], "limit": 20},
    )
    assert second_page.status_code == 200
    second_items = second_page.json()["items"]
    assert [item["sequence"] for item in second_items] == [3, 4, 5, 6]
    assert second_items[-1]["payload"]["text"] == "Hello from the worker"


def test_dispatched_command_replays_after_disconnect_and_scope_is_phone_bound(
    client: TestClient,
) -> None:
    relay = _setup_relay(client)
    created = _create_session(client, relay)
    command_id = created["command"]["id"]
    session_id = created["session"]["id"]

    with client.websocket_connect(
        "/api/v1/mobile/relay/worker/connect",
        headers=relay["worker_headers"],
    ) as socket:
        assert socket.receive_json()["type"] == "hello"
        first_delivery = socket.receive_json()
        assert first_delivery["command"]["id"] == command_id

    with client.websocket_connect(
        "/api/v1/mobile/relay/worker/connect",
        headers=relay["worker_headers"],
    ) as socket:
        assert socket.receive_json()["type"] == "hello"
        replay = socket.receive_json()
        assert replay["command"]["id"] == command_id
        socket.send_json({"type": "command.ack", "command_id": command_id})
        assert socket.receive_json()["type"] == "command.acknowledged"

    second_phone = _enroll_device(
        client,
        account_token=relay["account_token"],
        device_class="phone",
        name="Other Test Phone",
    )
    other_headers = {
        "Authorization": f"Bearer {relay['account_token']}",
        "X-Atlas-Device-Session": second_phone["session"],
    }
    denied = client.get(
        f"/api/v1/mobile/relay/sessions/{session_id}",
        headers=other_headers,
    )
    assert denied.status_code == 404
    missing_phone_proof = client.get(
        f"/api/v1/mobile/relay/sessions/{session_id}",
        headers={"Authorization": f"Bearer {relay['account_token']}"},
    )
    assert missing_phone_proof.status_code == 401

    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect(
            "/api/v1/mobile/relay/worker/connect",
            headers={"Authorization": f"Bearer {relay['phone']['session']}"},
        ) as socket:
            socket.receive_json()
    assert rejected.value.code == 4401


def test_queue_backpressure_and_worker_revocation_cancel_relay_scope(
    client: TestClient,
    app,
) -> None:
    relay = _setup_relay(client)
    created = _create_session(client, relay)
    session_id = created["session"]["id"]
    first = client.post(
        f"/api/v1/mobile/relay/sessions/{session_id}/messages",
        headers={**relay["phone_headers"], "Idempotency-Key": "queued-turn"},
        json={"text": "fills second bounded slot"},
    )
    assert first.status_code == 202
    full = client.post(
        f"/api/v1/mobile/relay/sessions/{session_id}/messages",
        headers={**relay["phone_headers"], "Idempotency-Key": "overflow-turn"},
        json={"text": "must not enter an unbounded queue"},
    )
    assert full.status_code == 429
    assert full.headers["retry-after"] == "3"

    revoked = client.post(
        f"/api/v1/account/devices/{relay['worker']['device']['id']}/revoke",
        headers={"Authorization": f"Bearer {relay['account_token']}"},
        json={"reason": "relay test"},
    )
    assert revoked.status_code == 200, revoked.text
    with app.state.database.connect() as connection:
        pairing = connection.execute(
            "SELECT status FROM relay_pairings WHERE id = ?",
            (relay["pairing"]["id"],),
        ).fetchone()
        session = connection.execute(
            "SELECT status FROM relay_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        states = {
            row["status"]
            for row in connection.execute(
                "SELECT status FROM relay_commands WHERE session_id = ?",
                (session_id,),
            ).fetchall()
        }
    assert pairing["status"] == "revoked"
    assert session["status"] == "closed"
    assert states == {"canceled"}
