"""Contracts for the dedicated Atlas Cortex utility-model inventory."""

from __future__ import annotations

import copy
import json
import time

import pytest
from fastapi import HTTPException


def _context(*, provider: str = "openrouter", model: str = "anthropic/claude-sonnet-4.6"):
    from hermes_cli.inventory import ConfigContext

    return ConfigContext(
        current_provider=provider,
        current_model=model,
        current_base_url="",
        user_providers={},
        custom_providers=[],
    )


def _clear_managed_binding(monkeypatch) -> None:
    for name in (
        "ATLAS_CONTROL_PLANE_URL",
        "ATLAS_DEVICE_TOKEN",
        "ATLAS_TENANT_ID",
        "ATLAS_STORE_ID",
        "ATLAS_AGENT_ID",
        "ATLAS_MANAGED_MODE",
    ):
        monkeypatch.delenv(name, raising=False)


def test_cortex_catalog_is_stable_structured_and_tool_independent(monkeypatch):
    import hermes_cli.auth as auth
    import hermes_cli.inventory as inventory

    _clear_managed_binding(monkeypatch)
    monkeypatch.setattr(auth, "get_auth_status", lambda provider: {"logged_in": provider == "openrouter"})
    inventory._cortex_openrouter_metadata_cache = None

    payload = inventory.build_cortex_memory_models_payload(_context())

    assert payload["recommended"] == {
        "provider": "openrouter",
        "model": "google/gemini-3.1-flash-lite",
    }
    assert payload["constraints"]["tool_calling_required"] is False
    assert [row["slug"] for row in payload["providers"]] == ["openrouter"]
    row = payload["providers"][0]
    assert row["models"] == [
        "google/gemini-3.1-flash-lite",
        "google/gemini-2.5-flash-lite",
    ]
    assert all(
        capability["structured_json"]
        and capability["tool_calling_required"] is False
        and capability["recommended"]
        for capability in row["memory_capabilities"].values()
    )


def test_cortex_catalog_uses_live_structured_metadata_without_requiring_tools(monkeypatch):
    import hermes_cli.auth as auth
    import hermes_cli.inventory as inventory

    _clear_managed_binding(monkeypatch)
    monkeypatch.setattr(auth, "get_auth_status", lambda _provider: {"logged_in": True})
    inventory._cortex_openrouter_metadata_cache = (
        time.monotonic(),
        {
            # No tools advertised: still valid for bounded Cortex JSON work.
            "google/gemini-3.1-flash-lite": {"supported_parameters": ["response_format"]},
            # Tools alone are insufficient when live metadata explicitly omits
            # structured output support.
            "google/gemini-2.5-flash-lite": {"supported_parameters": ["tools"]},
        },
    )

    payload = inventory.build_cortex_memory_models_payload(_context())
    row = payload["providers"][0]

    assert row["models"] == ["google/gemini-3.1-flash-lite"]
    assert row["memory_capabilities"]["google/gemini-3.1-flash-lite"]["selectable"] is True
    assert row["memory_capabilities"]["google/gemini-2.5-flash-lite"]["selectable"] is False


def test_validated_live_omission_survives_followup_ordinary_load(monkeypatch):
    import hermes_cli.auth as auth
    import hermes_cli.inventory as inventory

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "data": [
                        {
                            "id": "google/gemini-3.1-flash-lite",
                            "supported_parameters": ["response_format"],
                        }
                    ]
                }
            ).encode()

    _clear_managed_binding(monkeypatch)
    monkeypatch.setattr(auth, "get_auth_status", lambda _provider: {"logged_in": True})
    monkeypatch.setattr(inventory.urllib.request, "urlopen", lambda *_args, **_kwargs: Response())
    inventory._cortex_openrouter_metadata_cache = None

    refreshed = inventory.build_cortex_memory_models_payload(_context(), refresh=True)
    ordinary = inventory.build_cortex_memory_models_payload(_context(), refresh=False)

    assert refreshed["providers"][0]["models"] == ["google/gemini-3.1-flash-lite"]
    assert ordinary["providers"][0]["models"] == ["google/gemini-3.1-flash-lite"]
    assert (
        ordinary["providers"][0]["memory_capabilities"]
        ["google/gemini-2.5-flash-lite"]["unavailable_reason"]
        == "not listed by OpenRouter"
    )


def test_cortex_catalog_honors_main_separation_and_approved_provider_policy(monkeypatch):
    import hermes_cli.auth as auth
    import hermes_cli.inventory as inventory

    _clear_managed_binding(monkeypatch)
    monkeypatch.setattr(auth, "get_auth_status", lambda _provider: {"logged_in": True})
    inventory._cortex_openrouter_metadata_cache = None

    payload = inventory.build_cortex_memory_models_payload(
        _context(model="google/gemini-3.1-flash-lite")
    )
    assert payload["recommended"]["model"] == "google/gemini-2.5-flash-lite"
    assert payload["providers"][0]["models"] == ["google/gemini-2.5-flash-lite"]

    disallowed = inventory.build_cortex_memory_models_payload(
        _context(),
        approved_model_providers=["anthropic"],
    )
    assert disallowed["providers"] == []
    assert disallowed["recommended"] == {"provider": "", "model": ""}


def test_managed_alias_requires_concrete_control_plane_binding(monkeypatch):
    import hermes_cli.auth as auth
    import hermes_cli.inventory as inventory

    _clear_managed_binding(monkeypatch)
    monkeypatch.setattr(auth, "get_auth_status", lambda _provider: {"logged_in": False})
    inventory._cortex_openrouter_metadata_cache = None
    monkeypatch.setenv("ATLAS_MANAGED_MODE", "1")

    local = inventory.build_cortex_memory_models_payload(_context())
    assert [row["slug"] for row in local["providers"]] == ["openrouter"]
    assert local["recommended"] == {"provider": "", "model": ""}

    for name, value in {
        "ATLAS_CONTROL_PLANE_URL": "https://control.example.test",
        "ATLAS_DEVICE_TOKEN": "device-token",
        "ATLAS_TENANT_ID": "tenant",
        "ATLAS_STORE_ID": "store",
        "ATLAS_AGENT_ID": "agent",
    }.items():
        monkeypatch.setenv(name, value)

    managed = inventory.build_cortex_memory_models_payload(_context())
    assert [row["slug"] for row in managed["providers"]] == ["altas"]
    assert managed["recommended"] == {
        "provider": "altas",
        "model": "atlas-cortex-memory",
    }


def _patch_config_io(monkeypatch, config: dict):
    import hermes_cli.web_server as web_server

    writes: list[dict] = []
    monkeypatch.setattr(web_server, "load_config", lambda: copy.deepcopy(config))
    monkeypatch.setattr(web_server, "save_config", lambda value: writes.append(copy.deepcopy(value)))
    return web_server, writes


def test_atomic_cortex_save_writes_both_slots_once_and_clears_hidden_routes(monkeypatch):
    import hermes_cli.auth as auth

    _clear_managed_binding(monkeypatch)
    monkeypatch.setattr(auth, "get_auth_status", lambda provider: {"logged_in": provider == "openrouter"})
    config = {
        "model": {"provider": "openrouter", "default": "anthropic/claude-sonnet-4.6"},
        "cortex": {"security": {"approved_model_providers": ["openrouter"]}},
        "auxiliary": {
            "vision": {"provider": "auto", "model": ""},
            "cortex_triage": {
                "provider": "openrouter",
                "model": "old-model",
                "base_url": "https://hidden.example.test/v1",
                "api_key": "secret",
                "fallback_chain": ["main"],
            },
            "cortex_reasoning": {
                "provider": "anthropic",
                "model": "old-model",
                "api": "legacy-secret",
            },
        },
    }
    web_server, writes = _patch_config_io(monkeypatch, config)

    result = web_server._apply_cortex_memory_assignment_sync(
        "openrouter", "google/gemini-3.1-flash-lite"
    )

    assert len(writes) == 1
    saved = writes[0]
    for task in ("cortex_triage", "cortex_reasoning"):
        slot = saved["auxiliary"][task]
        assert slot["provider"] == "openrouter"
        assert slot["model"] == "google/gemini-3.1-flash-lite"
        assert slot["fallback_chain"] == []
        assert "base_url" not in slot
        assert "api_key" not in slot
        assert "api" not in slot
    assert saved["auxiliary"]["vision"] == config["auxiliary"]["vision"]
    assert result["triage"] == result["reasoning"] == {
        "provider": "openrouter",
        "model": "google/gemini-3.1-flash-lite",
    }


def test_atomic_cortex_save_requires_profile_authenticated_catalog_provider(monkeypatch):
    import hermes_cli.auth as auth

    _clear_managed_binding(monkeypatch)
    monkeypatch.setattr(auth, "get_auth_status", lambda _provider: {"logged_in": False})
    config = {
        "model": {"provider": "nous", "default": "hermes-4"},
        "cortex": {"security": {"approved_model_providers": ["openrouter"]}},
        "auxiliary": {},
    }
    web_server, writes = _patch_config_io(monkeypatch, config)

    with pytest.raises(HTTPException, match="not authenticated") as raised:
        web_server._apply_cortex_memory_assignment_sync(
            "openrouter", "google/gemini-3.1-flash-lite"
        )

    assert raised.value.status_code == 401
    assert writes == []


def test_atomic_cortex_save_rejects_model_outside_reviewed_catalog(monkeypatch):
    import hermes_cli.auth as auth

    _clear_managed_binding(monkeypatch)
    monkeypatch.setattr(auth, "get_auth_status", lambda _provider: {"logged_in": True})
    config = {
        "model": {"provider": "nous", "default": "hermes-4"},
        "cortex": {"security": {"approved_model_providers": ["openrouter"]}},
        "auxiliary": {},
    }
    web_server, writes = _patch_config_io(monkeypatch, config)

    with pytest.raises(HTTPException, match="not selectable") as raised:
        web_server._apply_cortex_memory_assignment_sync(
            "openrouter", "anthropic/claude-haiku-4.5"
        )

    assert raised.value.status_code == 400
    assert writes == []


def test_atomic_cortex_save_rejects_catalog_model_without_structured_output(monkeypatch):
    import hermes_cli.auth as auth
    import hermes_cli.inventory as inventory

    _clear_managed_binding(monkeypatch)
    monkeypatch.setattr(auth, "get_auth_status", lambda _provider: {"logged_in": True})
    monkeypatch.setattr(
        inventory,
        "_cortex_openrouter_metadata_cache",
        (
            time.monotonic(),
            {
                "google/gemini-3.1-flash-lite": {
                    "supported_parameters": ["tools"]
                },
            },
        ),
    )
    config = {
        "model": {"provider": "nous", "default": "hermes-4"},
        "cortex": {"security": {"approved_model_providers": ["openrouter"]}},
        "auxiliary": {},
    }
    web_server, writes = _patch_config_io(monkeypatch, config)

    with pytest.raises(HTTPException, match="structured JSON") as raised:
        web_server._apply_cortex_memory_assignment_sync(
            "openrouter", "google/gemini-3.1-flash-lite"
        )

    assert raised.value.status_code == 400
    assert writes == []


def test_atomic_cortex_save_accepts_bound_managed_catalog_alias(monkeypatch):
    _clear_managed_binding(monkeypatch)
    for name, value in {
        "ATLAS_CONTROL_PLANE_URL": "https://control.example.test",
        "ATLAS_DEVICE_TOKEN": "device-token",
        "ATLAS_TENANT_ID": "tenant",
        "ATLAS_STORE_ID": "store",
        "ATLAS_AGENT_ID": "agent",
    }.items():
        monkeypatch.setenv(name, value)
    config = {
        "model": {"provider": "openrouter", "default": "anthropic/claude-sonnet-4.6"},
        "cortex": {"security": {"approved_model_providers": []}},
        "auxiliary": {},
    }
    web_server, writes = _patch_config_io(monkeypatch, config)

    result = web_server._apply_cortex_memory_assignment_sync(
        "altas", "atlas-cortex-memory"
    )

    assert len(writes) == 1
    assert result["provider"] == "altas"
    assert result["model"] == "atlas-cortex-memory"


@pytest.mark.parametrize(
    ("provider", "model", "status"),
    [
        ("", "google/gemini-3.1-flash-lite", 400),
        ("auto", "google/gemini-3.1-flash-lite", 400),
        ("main", "google/gemini-3.1-flash-lite", 400),
        ("openrouter", "", 400),
        ("openrouter", "auto", 400),
        ("openrouter", "anthropic/claude-sonnet-4.6", 409),
    ],
)
def test_atomic_cortex_save_rejects_invalid_or_chat_routes_without_writing(
    monkeypatch,
    provider,
    model,
    status,
):
    config = {
        "model": {"provider": "openrouter", "default": "anthropic/claude-sonnet-4.6"},
        "cortex": {"security": {"approved_model_providers": ["openrouter"]}},
        "auxiliary": {},
    }
    web_server, writes = _patch_config_io(monkeypatch, config)

    with pytest.raises(HTTPException) as raised:
        web_server._apply_cortex_memory_assignment_sync(provider, model)

    assert raised.value.status_code == status
    assert writes == []


def test_cortex_save_rejects_provider_outside_security_policy(monkeypatch):
    config = {
        "model": {"provider": "openrouter", "default": "anthropic/claude-sonnet-4.6"},
        "cortex": {"security": {"approved_model_providers": ["anthropic"]}},
        "auxiliary": {},
    }
    web_server, writes = _patch_config_io(monkeypatch, config)

    with pytest.raises(HTTPException, match="not approved") as raised:
        web_server._apply_cortex_memory_assignment_sync(
            "openrouter", "google/gemini-3.1-flash-lite"
        )

    assert raised.value.status_code == 400
    assert writes == []


def test_bound_managed_profile_rejects_external_cortex_route(monkeypatch):
    config = {
        "model": {"provider": "openrouter", "default": "anthropic/claude-sonnet-4.6"},
        "cortex": {"security": {"approved_model_providers": []}},
        "auxiliary": {},
    }
    web_server, writes = _patch_config_io(monkeypatch, config)
    for name, value in {
        "ATLAS_CONTROL_PLANE_URL": "https://control.example.test",
        "ATLAS_DEVICE_TOKEN": "device-token",
        "ATLAS_TENANT_ID": "tenant",
        "ATLAS_STORE_ID": "store",
        "ATLAS_AGENT_ID": "agent",
    }.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(HTTPException, match="Atlas-approved") as raised:
        web_server._apply_cortex_memory_assignment_sync(
            "openrouter", "google/gemini-3.1-flash-lite"
        )

    assert raised.value.status_code == 400
    assert writes == []


def test_generic_cortex_assignment_cannot_bypass_catalog_authentication(monkeypatch):
    import hermes_cli.auth as auth

    _clear_managed_binding(monkeypatch)
    monkeypatch.setattr(auth, "get_auth_status", lambda _provider: {"logged_in": False})
    config = {
        "model": {"provider": "nous", "default": "hermes-4"},
        "cortex": {"security": {"approved_model_providers": ["openrouter"]}},
        "auxiliary": {
            "vision": {"provider": "auto", "model": ""},
        },
    }
    web_server, writes = _patch_config_io(monkeypatch, config)

    with pytest.raises(HTTPException, match="not authenticated") as raised:
        web_server._apply_model_assignment_sync(
            "auxiliary",
            "openrouter",
            "google/gemini-3.1-flash-lite",
            "cortex_triage",
            "",
        )

    assert raised.value.status_code == 401
    assert writes == []


def test_cortex_current_status_is_profile_scoped_and_secret_free(monkeypatch):
    import hermes_cli.auth as auth

    _clear_managed_binding(monkeypatch)
    config = {
        "model": {"provider": "nous", "default": "hermes-4"},
        "cortex": {"security": {"approved_model_providers": ["openrouter"]}},
        "auxiliary": {
            "cortex_triage": {
                "provider": "openrouter",
                "model": "google/gemini-3.1-flash-lite",
                "api_key": "must-not-leak",
            },
            "cortex_reasoning": {
                "provider": "openrouter",
                "model": "google/gemini-3.1-flash-lite",
            },
        },
    }
    web_server, _writes = _patch_config_io(monkeypatch, config)
    monkeypatch.setattr(auth, "get_auth_status", lambda _provider: {"logged_in": True})
    catalog = web_server._build_active_cortex_memory_catalog(config)

    current = web_server._cortex_current_route_status(config, catalog)

    assert current["configured"] is True
    assert current["valid"] is True
    assert current["triage"] == {
        "provider": "openrouter",
        "model": "google/gemini-3.1-flash-lite",
        "configured": True,
        "valid": True,
        "runtime_valid": True,
        "catalog_selectable": True,
        "advanced_route": False,
        "unavailable_reason": "",
    }
    assert "must-not-leak" not in repr(current)

    monkeypatch.setattr(auth, "get_auth_status", lambda _provider: {"logged_in": False})
    unauthenticated_catalog = web_server._build_active_cortex_memory_catalog(config)
    invalid = web_server._cortex_current_route_status(config, unauthenticated_catalog)
    assert invalid["configured"] is True
    assert invalid["valid"] is False
    assert "not authenticated" in invalid["reasoning"]["unavailable_reason"]


def test_cortex_current_status_preserves_runtime_valid_advanced_route(monkeypatch):
    import hermes_cli.auth as auth

    _clear_managed_binding(monkeypatch)
    monkeypatch.setattr(auth, "get_auth_status", lambda _provider: {"logged_in": False})
    config = {
        "model": {"provider": "openrouter", "default": "anthropic/claude-sonnet-4.6"},
        "cortex": {"security": {"approved_model_providers": ["anthropic"]}},
        "auxiliary": {
            task: {"provider": "anthropic", "model": "claude-haiku-4-5-20251001"}
            for task in ("cortex_triage", "cortex_reasoning")
        },
    }
    web_server, _writes = _patch_config_io(monkeypatch, config)
    catalog = web_server._build_active_cortex_memory_catalog(config)

    current = web_server._cortex_current_route_status(config, catalog)

    assert catalog["providers"] == []
    assert current["valid"] is True
    assert current["triage"]["runtime_valid"] is True
    assert current["triage"]["catalog_selectable"] is False
    assert current["triage"]["advanced_route"] is True
    assert current["triage"]["unavailable_reason"] == ""


def test_cortex_current_status_rejects_unbound_branding_default(monkeypatch):
    import hermes_cli.auth as auth

    _clear_managed_binding(monkeypatch)
    monkeypatch.setattr(auth, "get_auth_status", lambda _provider: {"logged_in": False})
    config = {
        "model": {"provider": "nous", "default": "hermes-4"},
        "cortex": {"security": {"approved_model_providers": []}},
        "auxiliary": {
            task: {"provider": "altas", "model": "atlas-cortex-memory"}
            for task in ("cortex_triage", "cortex_reasoning")
        },
    }
    web_server, _writes = _patch_config_io(monkeypatch, config)
    catalog = web_server._build_active_cortex_memory_catalog(config)

    current = web_server._cortex_current_route_status(config, catalog)

    assert current["configured"] is True
    assert current["valid"] is False
    assert "bound control-plane identity" in current["triage"]["unavailable_reason"]


def test_generic_aux_reset_preserves_cortex_and_main_save_cannot_collide(monkeypatch):
    config = {
        "model": {"provider": "openrouter", "default": "anthropic/claude-sonnet-4.6"},
        "auxiliary": {
            "vision": {"provider": "anthropic", "model": "claude-haiku"},
            "cortex_triage": {
                "provider": "openrouter",
                "model": "google/gemini-3.1-flash-lite",
                "fallback_chain": [],
            },
            "cortex_reasoning": {
                "provider": "openrouter",
                "model": "google/gemini-3.1-flash-lite",
                "fallback_chain": [],
            },
        },
    }
    web_server, writes = _patch_config_io(monkeypatch, config)

    reset = web_server._apply_model_assignment_sync(
        "auxiliary", "openrouter", "anthropic/claude-sonnet-4.6", "__reset__", ""
    )
    assert reset["preserved_tasks"] == ["cortex_triage", "cortex_reasoning"]
    assert writes[0]["auxiliary"]["vision"]["provider"] == "auto"
    assert writes[0]["auxiliary"]["cortex_triage"] == config["auxiliary"]["cortex_triage"]
    assert writes[0]["auxiliary"]["cortex_reasoning"] == config["auxiliary"]["cortex_reasoning"]

    writes.clear()
    with pytest.raises(HTTPException) as raised:
        web_server._apply_model_assignment_sync(
            "main", "openrouter", "google/gemini-3.1-flash-lite", "", ""
        )
    assert raised.value.status_code == 409
    assert writes == []


def test_cortex_memory_api_routes_are_registered():
    from hermes_cli.web_server import app

    methods_by_path = {
        route.path: getattr(route, "methods", set())
        for route in app.routes
        if hasattr(route, "path")
    }
    assert "GET" in methods_by_path["/api/model/cortex-memory/options"]
    assert "PUT" in methods_by_path["/api/model/cortex-memory"]
