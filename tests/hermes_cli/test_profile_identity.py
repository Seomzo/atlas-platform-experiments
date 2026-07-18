"""Behavior tests for per-profile worker identity and avatar APIs."""

import base64
import json
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture()
def profile_env(tmp_path, monkeypatch, _isolate_hermes_home):
    """Isolate both HOME-anchored profile discovery and HERMES_HOME."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    default_home = tmp_path / ".hermes"
    default_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HERMES_HOME", str(default_home))
    return default_home


@pytest.fixture()
def client(monkeypatch, profile_env):
    try:
        from starlette.testclient import TestClient
    except ImportError:
        pytest.skip("fastapi/starlette not installed")

    import hermes_state
    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    monkeypatch.setattr(hermes_state, "DEFAULT_DB_PATH", profile_env / "state.db")
    test_client = TestClient(app)
    test_client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return test_client


def _profile_home(default_home: Path, name: str) -> Path:
    if name == "default":
        return default_home
    home = default_home / "profiles" / name
    home.mkdir(parents=True, exist_ok=True)
    return home


@pytest.mark.parametrize("name", ["default", "writer"])
def test_identity_defaults_when_file_is_missing(client, profile_env, name):
    _profile_home(profile_env, name)

    response = client.get(f"/api/profiles/{name}/identity")

    assert response.status_code == 200
    assert response.json() == {
        "display_name": name,
        "role": "",
        "tagline": "",
        "avatar": None,
    }


def test_identity_patch_round_trip_strips_and_persists(client, profile_env):
    home = _profile_home(profile_env, "writer")

    updated = client.patch(
        "/api/profiles/writer/identity",
        json={
            "display_name": "  Riley  ",
            "role": "  Service Writer  ",
            "tagline": "  Keeps every repair order moving.  ",
        },
    )

    expected = {
        "display_name": "Riley",
        "role": "Service Writer",
        "tagline": "Keeps every repair order moving.",
        "avatar": None,
    }
    assert updated.status_code == 200
    assert updated.json() == expected
    assert client.get("/api/profiles/writer/identity").json() == expected
    assert json.loads((home / "identity.json").read_text(encoding="utf-8")) == expected


def test_partial_patch_preserves_unspecified_fields(client, profile_env):
    _profile_home(profile_env, "advisor")
    first = client.patch(
        "/api/profiles/advisor/identity",
        json={
            "display_name": "Avery",
            "role": "Advisor",
            "tagline": "Explains the work clearly.",
        },
    )
    assert first.status_code == 200

    second = client.patch(
        "/api/profiles/advisor/identity",
        json={"role": "Senior Advisor"},
    )

    assert second.status_code == 200
    assert second.json() == {
        "display_name": "Avery",
        "role": "Senior Advisor",
        "tagline": "Explains the work clearly.",
        "avatar": None,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"display_name": "x" * 81},
        {"role": "x" * 81},
        {"tagline": "x" * 201},
        {"role": 42},
        {"tagline": ["not", "a", "string"]},
        {"avatar": "avatars/avatar.png"},
    ],
)
def test_identity_patch_rejects_invalid_values(client, profile_env, payload):
    _profile_home(profile_env, "writer")

    response = client.patch("/api/profiles/writer/identity", json=payload)

    assert response.status_code == 400


def test_corrupt_identity_falls_back_without_breaking_listing(client, profile_env):
    home = _profile_home(profile_env, "writer")
    (home / "identity.json").write_text("{not valid json", encoding="utf-8")

    identity = client.get("/api/profiles/writer/identity")
    listing = client.get("/api/profiles")

    assert identity.status_code == 200
    assert identity.json() == {
        "display_name": "writer",
        "role": "",
        "tagline": "",
        "avatar": None,
    }
    assert listing.status_code == 200
    writer = next(item for item in listing.json()["profiles"] if item["name"] == "writer")
    assert writer["display_name"] == "writer"
    assert writer["role"] == ""
    assert writer["has_avatar"] is False


def test_avatar_upload_and_get_preserve_bytes_and_content_type(client, profile_env):
    home = _profile_home(profile_env, "writer")
    image = b"\x89PNG\r\n\x1a\n" + b"worker-avatar"

    uploaded = client.put(
        "/api/profiles/writer/avatar",
        files={"file": ("portrait.png", image, "image/png")},
    )
    served = client.get("/api/profiles/writer/avatar")

    assert uploaded.status_code == 200
    assert uploaded.json()["identity"]["avatar"] == "avatars/avatar.png"
    assert (home / "avatars" / "avatar.png").read_bytes() == image
    assert served.status_code == 200
    assert served.content == image
    assert served.headers["content-type"] == "image/png"


def test_avatar_get_returns_404_when_unset(client, profile_env):
    _profile_home(profile_env, "writer")

    response = client.get("/api/profiles/writer/avatar")

    assert response.status_code == 404


def test_avatar_get_accepts_query_token_for_image_elements(client, profile_env):
    from hermes_cli.web_server import _SESSION_HEADER_NAME, _SESSION_TOKEN

    _profile_home(profile_env, "writer")
    image = b"worker-avatar"
    assert client.put(
        "/api/profiles/writer/avatar",
        files={"file": ("portrait.webp", image, "image/webp")},
    ).status_code == 200

    del client.headers[_SESSION_HEADER_NAME]
    response = client.get(
        "/api/profiles/writer/avatar",
        params={"token": _SESSION_TOKEN, "v": "1"},
    )

    assert response.status_code == 200
    assert response.content == image


def test_avatar_upload_rejects_unsupported_type_and_oversize(client, profile_env):
    home = _profile_home(profile_env, "writer")

    wrong_type = client.put(
        "/api/profiles/writer/avatar",
        files={"file": ("portrait.txt", b"not an image", "text/plain")},
    )
    oversize = client.put(
        "/api/profiles/writer/avatar",
        files={"file": ("portrait.webp", b"x" * (5 * 1024 * 1024 + 1), "image/webp")},
    )

    assert wrong_type.status_code == 415
    assert oversize.status_code == 413
    assert not (home / "identity.json").exists()
    assert not list((home / "avatars").glob(".avatar.*"))


def test_avatar_upload_replaces_a_previous_extension(client, profile_env):
    home = _profile_home(profile_env, "writer")
    png = client.put(
        "/api/profiles/writer/avatar",
        files={"file": ("portrait.png", b"png", "image/png")},
    )
    jpeg = client.put(
        "/api/profiles/writer/avatar",
        files={"file": ("portrait.jpg", b"jpeg", "image/jpeg")},
    )

    assert png.status_code == 200
    assert jpeg.status_code == 200
    assert not (home / "avatars" / "avatar.png").exists()
    assert (home / "avatars" / "avatar.jpg").read_bytes() == b"jpeg"
    assert client.get("/api/profiles/writer/identity").json()["avatar"] == "avatars/avatar.jpg"


def test_avatar_upload_accepts_desktop_json_transport(client, profile_env):
    home = _profile_home(profile_env, "writer")
    content = b"desktop-worker-avatar"

    response = client.put(
        "/api/profiles/writer/avatar",
        json={
            "content_type": "image/png",
            "data_base64": base64.b64encode(content).decode("ascii"),
        },
    )

    assert response.status_code == 200
    assert response.json()["identity"]["avatar"] == "avatars/avatar.png"
    assert (home / "avatars" / "avatar.png").read_bytes() == content


def test_avatar_generate_uses_active_provider_and_persists_result(
    client,
    profile_env,
    monkeypatch,
    tmp_path,
):
    home = _profile_home(profile_env, "writer")
    generated = tmp_path / "generated.webp"
    generated.write_bytes(b"generated-worker-avatar")
    provider = Mock()
    provider.generate.return_value = {
        "success": True,
        "image": str(generated),
        "provider": "test",
    }

    import agent.image_gen_registry as registry
    import hermes_cli.plugins as plugins

    monkeypatch.setattr(plugins, "_ensure_plugins_discovered", Mock())
    monkeypatch.setattr(registry, "get_active_provider", lambda: provider)

    response = client.post(
        "/api/profiles/writer/avatar/generate",
        json={"prompt": "A warm, professional service advisor portrait"},
    )

    assert response.status_code == 200
    assert response.json()["identity"]["avatar"] == "avatars/avatar.webp"
    assert (home / "avatars" / "avatar.webp").read_bytes() == b"generated-worker-avatar"
    assert json.loads((home / "identity.json").read_text(encoding="utf-8"))["avatar"] == (
        "avatars/avatar.webp"
    )
    provider.generate.assert_called_once_with(
        prompt="A warm, professional service advisor portrait",
        aspect_ratio="square",
    )


def test_avatar_generate_returns_409_without_provider(client, profile_env, monkeypatch):
    home = _profile_home(profile_env, "writer")

    import agent.image_gen_registry as registry
    import hermes_cli.plugins as plugins

    monkeypatch.setattr(plugins, "_ensure_plugins_discovered", Mock())
    monkeypatch.setattr(registry, "get_active_provider", lambda: None)

    response = client.post(
        "/api/profiles/writer/avatar/generate",
        json={"prompt": "A dealership concierge"},
    )

    assert response.status_code == 409
    assert "image generation provider" in response.json()["detail"].lower()
    assert not (home / "identity.json").exists()


def test_avatar_generate_returns_404_for_unknown_profile(client, monkeypatch):
    provider = Mock()

    import agent.image_gen_registry as registry

    monkeypatch.setattr(registry, "get_active_provider", lambda: provider)

    response = client.post(
        "/api/profiles/missing/avatar/generate",
        json={"prompt": "A dealership concierge"},
    )

    assert response.status_code == 404
    provider.generate.assert_not_called()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"prompt": ""},
        {"prompt": "   "},
        {"prompt": 42},
        {"prompt": ["not", "text"]},
    ],
)
def test_avatar_generate_rejects_bad_payload(client, profile_env, payload):
    _profile_home(profile_env, "writer")

    response = client.post("/api/profiles/writer/avatar/generate", json=payload)

    assert response.status_code == 400


def test_profile_listing_includes_identity_summary(client, profile_env):
    _profile_home(profile_env, "writer")
    assert client.patch(
        "/api/profiles/writer/identity",
        json={"display_name": "Riley", "role": "Service Writer"},
    ).status_code == 200
    assert client.put(
        "/api/profiles/writer/avatar",
        files={"file": ("portrait.webp", b"webp", "image/webp")},
    ).status_code == 200

    response = client.get("/api/profiles")

    assert response.status_code == 200
    writer = next(item for item in response.json()["profiles"] if item["name"] == "writer")
    assert writer["name"] == "writer"
    assert writer["display_name"] == "Riley"
    assert writer["role"] == "Service Writer"
    assert writer["has_avatar"] is True


def test_identities_are_isolated_per_profile(client, profile_env):
    _profile_home(profile_env, "sales")
    _profile_home(profile_env, "service")

    assert client.patch(
        "/api/profiles/sales/identity",
        json={"display_name": "Sam", "role": "Sales"},
    ).status_code == 200
    assert client.patch(
        "/api/profiles/service/identity",
        json={"display_name": "Taylor", "role": "Service"},
    ).status_code == 200

    sales = client.get("/api/profiles/sales/identity").json()
    service = client.get("/api/profiles/service/identity").json()
    assert (sales["display_name"], sales["role"]) == ("Sam", "Sales")
    assert (service["display_name"], service["role"]) == ("Taylor", "Service")
    assert json.loads(
        (profile_env / "profiles" / "sales" / "identity.json").read_text(encoding="utf-8")
    )["display_name"] == "Sam"
    assert json.loads(
        (profile_env / "profiles" / "service" / "identity.json").read_text(encoding="utf-8")
    )["display_name"] == "Taylor"


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("get", "/api/profiles/missing/identity", {}),
        ("patch", "/api/profiles/missing/identity", {"json": {"role": "x"}}),
        (
            "put",
            "/api/profiles/missing/avatar",
            {"files": {"file": ("portrait.png", b"png", "image/png")}},
        ),
        ("get", "/api/profiles/missing/avatar", {}),
    ],
)
def test_identity_endpoints_return_404_for_unknown_profile(client, method, path, kwargs):
    response = getattr(client, method)(path, **kwargs)

    assert response.status_code == 404
