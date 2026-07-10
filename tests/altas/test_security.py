from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from altas.cli import _serve
from altas.control_plane import ControlPlaneSettings, create_app
from altas.control_plane.redaction import REDACTED, sanitize_for_storage
from altas.control_plane.security import InvalidLease, LeaseSigner


def test_hmac_lease_is_scoped_and_expires_without_sleeping() -> None:
    now = [1_000.0]
    signer = LeaseSigner(b"a sufficiently long lease signing key", clock=lambda: now[0])
    token, issued = signer.issue(
        device_id="device-1",
        tenant_id="tenant-1",
        store_id="store-1",
        agent_id="agent-1",
        capabilities=["model.chat", "model.chat"],
        ttl_seconds=30,
        nonce="deterministic-test-nonce",
    )

    verified = signer.verify(token)
    assert verified == issued
    assert verified.capabilities == ("model.chat",)

    now[0] = 1_030.0
    with pytest.raises(InvalidLease, match="lease_expired"):
        signer.verify(token)


def test_hmac_lease_rejects_modified_payload() -> None:
    signer = LeaseSigner(b"another sufficiently long signing key")
    token, _ = signer.issue(
        device_id="device-1",
        tenant_id="tenant-1",
        store_id="store-1",
        agent_id="agent-1",
        capabilities=["model.chat"],
        ttl_seconds=30,
        nonce="nonce",
    )
    prefix, payload, signature = token.split(".")
    modified = f"{prefix}.{payload[:-1]}A.{signature}"

    with pytest.raises(InvalidLease, match="lease_signature_invalid"):
        signer.verify(modified)


def test_storage_redaction_handles_camel_case_and_provider_key_values() -> None:
    payload = {
        "dealerKey": "dealer-secret-value",
        "accessToken": "access-secret-value",
        "nested": {"tekionCredential": "tekion-secret-value"},
        "note": (
            "provider sk-proj-1234567890abcdefghijklmnop and "
            "accessToken:should-not-survive"
        ),
        "url": "https://example.test/sync?api_key=should-not-survive&store=1",
    }

    sanitized = sanitize_for_storage(payload)

    assert sanitized["dealerKey"] == REDACTED
    assert sanitized["accessToken"] == REDACTED
    assert sanitized["nested"]["tekionCredential"] == REDACTED
    assert "sk-proj" not in sanitized["note"]
    assert "should-not-survive" not in sanitized["note"]
    assert "should-not-survive" not in sanitized["url"]


def test_demo_mode_rejects_real_provider_and_non_loopback_clients(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="demo seed data"):
        ControlPlaneSettings(
            database_path=tmp_path / "invalid.sqlite3",
            lease_signing_key=b"a sufficiently long lease signing key",
            admin_token="test-admin",
            seed_demo_data=True,
            mock_model=False,
            upstream_api_key="provider-secret",
        )

    app = create_app(
        ControlPlaneSettings(
            database_path=tmp_path / "demo.sqlite3",
            lease_signing_key=b"another sufficiently long signing key",
            admin_token="test-admin",
            seed_demo_data=True,
            mock_model=True,
        )
    )
    with TestClient(app, client=("198.51.100.10", 50000)) as remote_client:
        response = remote_client.get("/health")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "demo_loopback_only"


def test_non_demo_admin_and_ui_surfaces_are_loopback_only(tmp_path: Path) -> None:
    app = create_app(
        ControlPlaneSettings(
            database_path=tmp_path / "non-demo.sqlite3",
            lease_signing_key=b"non-demo sufficiently long signing key",
            admin_token="test-admin",
            seed_demo_data=False,
            mock_model=True,
        )
    )
    with TestClient(app, client=("198.51.100.10", 50000)) as remote_client:
        responses = [
            remote_client.get("/"),
            remote_client.get("/static/styles.css"),
            remote_client.get(
                "/api/v1/admin/overview",
                headers={"Authorization": "Bearer test-admin"},
            ),
        ]
        health = remote_client.get("/health")
        worker = remote_client.post("/api/v1/worker/heartbeat", json={})

    assert all(response.status_code == 403 for response in responses)
    assert all(
        response.json()["detail"]["code"] == "admin_loopback_only"
        for response in responses
    )
    assert health.status_code == 200
    assert worker.status_code == 401


@pytest.mark.parametrize("demo_enabled", ["true", "false"])
def test_prototype_cli_refuses_non_loopback_bind(
    monkeypatch: pytest.MonkeyPatch, demo_enabled: str
) -> None:
    monkeypatch.setenv("ALTAS_SEED_DEMO_DATA", demo_enabled)
    with pytest.raises(ValueError, match="loopback"):
        _serve(Namespace(host="0.0.0.0", port=8787, reload=False))
