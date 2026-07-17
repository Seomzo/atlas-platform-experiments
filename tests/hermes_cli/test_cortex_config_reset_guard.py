from __future__ import annotations

import asyncio
import copy
from contextlib import nullcontext

import pytest
from fastapi import HTTPException


MEMORY_MODEL = "google/gemini-3.1-flash-lite"


def _valid_config() -> dict:
    return {
        "model": {
            "provider": "openrouter",
            "default": "anthropic/claude-sonnet-4.6",
        },
        "cortex": {
            "enabled": True,
            "security": {"approved_model_providers": ["openrouter"]},
        },
        "memory": {"provider": "cortex"},
        "auxiliary": {
            "cortex_triage": {
                "provider": "openrouter",
                "model": MEMORY_MODEL,
                "fallback_chain": [],
            },
            "cortex_reasoning": {
                "provider": "openrouter",
                "model": MEMORY_MODEL,
                "fallback_chain": [],
            },
        },
    }


def _catalog() -> dict:
    capability = {
        "selectable": True,
        "structured_json": True,
        "unavailable_reason": "",
    }
    return {
        "providers": [
            {
                "slug": "openrouter",
                "authenticated": True,
                "models": [MEMORY_MODEL],
                "memory_capabilities": {MEMORY_MODEL: capability},
            }
        ]
    }


def test_generic_config_guard_rejects_resetting_active_cortex_routes(monkeypatch):
    from hermes_cli import web_server

    existing = _valid_config()
    candidate = copy.deepcopy(existing)
    for task in ("cortex_triage", "cortex_reasoning"):
        candidate["auxiliary"][task].update({"provider": "auto", "model": ""})

    monkeypatch.setattr(web_server, "_build_active_cortex_memory_catalog", lambda _cfg: _catalog())

    with pytest.raises(HTTPException) as exc:
        web_server._assert_valid_cortex_boundary_config_update(existing, candidate)

    assert exc.value.status_code == 409
    assert "dedicated model route" in str(exc.value.detail)


def test_generic_config_guard_rejects_chat_model_collision(monkeypatch):
    from hermes_cli import web_server

    existing = _valid_config()
    candidate = copy.deepcopy(existing)
    candidate["model"]["default"] = MEMORY_MODEL
    monkeypatch.setattr(web_server, "_build_active_cortex_memory_catalog", lambda _cfg: _catalog())

    with pytest.raises(HTTPException) as exc:
        web_server._assert_valid_cortex_boundary_config_update(existing, candidate)

    assert exc.value.status_code == 409
    assert "separate from the conversational model" in str(exc.value.detail)


def test_generic_config_guard_rejects_hidden_cortex_route_changes(monkeypatch):
    from hermes_cli import web_server

    existing = _valid_config()
    candidate = copy.deepcopy(existing)
    candidate["auxiliary"]["cortex_triage"]["base_url"] = "https://redirect.invalid/v1"

    def unexpected_catalog(_cfg):
        raise AssertionError("hidden route fields must fail before catalog validation")

    monkeypatch.setattr(web_server, "_build_active_cortex_memory_catalog", unexpected_catalog)

    with pytest.raises(HTTPException) as exc:
        web_server._assert_valid_cortex_boundary_config_update(existing, candidate)

    assert exc.value.status_code == 409
    assert "Generic settings cannot change a Cortex endpoint" in str(exc.value.detail)


def test_generic_config_endpoint_does_not_persist_invalid_cortex_reset(monkeypatch):
    from hermes_cli import web_server

    existing = _valid_config()
    candidate = copy.deepcopy(existing)
    for task in ("cortex_triage", "cortex_reasoning"):
        candidate["auxiliary"][task].update({"provider": "auto", "model": ""})
    saved: list[dict] = []

    monkeypatch.setattr(web_server, "_profile_scope", lambda _profile: nullcontext())
    monkeypatch.setattr(web_server, "_denormalize_config_from_web", lambda value: copy.deepcopy(value))
    monkeypatch.setattr(web_server, "load_config", lambda: copy.deepcopy(existing))
    monkeypatch.setattr(web_server, "read_raw_config", lambda: copy.deepcopy(existing))
    monkeypatch.setattr(web_server, "save_config", lambda value: saved.append(copy.deepcopy(value)))
    monkeypatch.setattr(web_server, "_build_active_cortex_memory_catalog", lambda _cfg: _catalog())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(web_server.update_config(web_server.ConfigUpdate(config=candidate)))

    assert exc.value.status_code == 409
    assert saved == []


def test_unrelated_save_does_not_block_an_older_invalid_profile(monkeypatch):
    from hermes_cli import web_server

    existing = _valid_config()
    for task in ("cortex_triage", "cortex_reasoning"):
        existing["auxiliary"][task].update({"provider": "auto", "model": ""})
    candidate = copy.deepcopy(existing)
    candidate["agent"] = {"max_turns": 75}

    def unexpected_catalog(_cfg):
        raise AssertionError("unchanged Cortex boundary must not be revalidated")

    monkeypatch.setattr(web_server, "_build_active_cortex_memory_catalog", unexpected_catalog)
    web_server._assert_valid_cortex_boundary_config_update(existing, candidate)
