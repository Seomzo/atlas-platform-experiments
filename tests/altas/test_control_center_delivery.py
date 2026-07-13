from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from altas.control_plane import ControlPlaneSettings, create_app


def test_control_center_and_assets_are_served_with_security_headers(
    tmp_path: Path,
) -> None:
    app = create_app(
        ControlPlaneSettings(
            database_path=tmp_path / "control-plane.sqlite3",
            lease_signing_key=b"control-center-test-signing-key-32-bytes",
            admin_token="control-center-test-admin",
            seed_demo_data=True,
            mock_model=True,
        )
    )

    with TestClient(app) as client:
        page = client.get("/")
        stylesheet = client.get("/static/styles.css")
        script = client.get("/static/app.js")
        icon = client.get("/static/altas-mark.svg")

    assert page.status_code == 200
    assert "<title>Atlas Control Center</title>" in page.text
    assert page.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["x-frame-options"] == "DENY"
    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert script.status_code == 200
    assert "innerHTML" not in script.text
    assert icon.status_code == 200
    assert "image/svg+xml" in icon.headers["content-type"]
