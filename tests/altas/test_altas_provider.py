from __future__ import annotations

import os

from providers import get_provider_profile


def test_altas_model_provider_is_registered() -> None:
    profile = get_provider_profile("altas")

    assert profile is not None
    assert profile.display_name == "Atlas Gateway"
    assert profile.base_url.endswith("/v1")
    assert profile.fallback_models == ("altas-fixed-ops",)


def test_altas_provider_enables_managed_mode_only_when_used(monkeypatch) -> None:
    profile = get_provider_profile("altas")
    assert profile is not None
    # setenv registers a restoration even when the variable was initially
    # absent; the profile intentionally mutates the process environment.
    monkeypatch.setenv("ATLAS_MANAGED_MODE", "0")

    messages = [{"role": "user", "content": "run the report"}]
    assert profile.prepare_messages(messages) is messages

    assert os.environ["ATLAS_MANAGED_MODE"] == "1"


def test_altas_provider_builds_fresh_request_headers(monkeypatch) -> None:
    profile = get_provider_profile("altas")
    assert profile is not None
    assert profile.default_headers == {}

    monkeypatch.setenv("ATLAS_LEASE_TOKEN", "lease-one")
    monkeypatch.setenv("ATLAS_JOB_ID", "job-one")
    monkeypatch.setenv("ATLAS_CLAIM_TOKEN", "claim-one")
    _, first = profile.build_api_kwargs_extras()

    monkeypatch.setenv("ATLAS_LEASE_TOKEN", "lease-two")
    monkeypatch.setenv("ATLAS_JOB_ID", "job-two")
    monkeypatch.setenv("ATLAS_CLAIM_TOKEN", "claim-two")
    _, second = profile.build_api_kwargs_extras()

    assert first["extra_headers"]["X-Atlas-Lease"] == "lease-one"
    assert first["extra_headers"]["X-Atlas-Job-ID"] == "job-one"
    assert first["extra_headers"]["X-Atlas-Claim-Token"] == "claim-one"
    assert second["extra_headers"]["X-Atlas-Lease"] == "lease-two"
    assert second["extra_headers"]["X-Atlas-Job-ID"] == "job-two"
    assert second["extra_headers"]["X-Atlas-Claim-Token"] == "claim-two"


def test_altas_provider_uses_safe_configurable_default_max_tokens(monkeypatch) -> None:
    profile = get_provider_profile("altas")
    assert profile is not None

    monkeypatch.delenv("ATLAS_DEFAULT_MODEL_MAX_TOKENS", raising=False)
    assert profile.get_max_tokens("altas-fixed-ops") == 400

    monkeypatch.setenv("ATLAS_DEFAULT_MODEL_MAX_TOKENS", "256")
    assert profile.get_max_tokens("altas-fixed-ops") == 256

    monkeypatch.setenv("ATLAS_DEFAULT_MODEL_MAX_TOKENS", "not-a-number")
    assert profile.get_max_tokens("altas-fixed-ops") == 400
