from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.auxiliary_client import (
    _client_cache,
    _get_cached_client,
    call_llm,
    shutdown_cached_clients,
)
from agent.secret_scope import (
    UnscopedSecretError,
    reset_secret_scope,
    set_multiplex_active,
    set_secret_scope,
)
from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from providers import get_provider_profile


def test_altas_model_provider_is_registered() -> None:
    profile = get_provider_profile("altas")

    assert profile is not None
    assert profile.display_name == "Atlas Gateway"
    assert profile.base_url.endswith("/v1")
    assert profile.fallback_models == ("altas-fixed-ops",)


def test_altas_provider_does_not_mutate_process_managed_mode(monkeypatch) -> None:
    profile = get_provider_profile("altas")
    assert profile is not None
    monkeypatch.setenv("ATLAS_MANAGED_MODE", "0")

    messages = [{"role": "user", "content": "run the report"}]
    assert profile.prepare_messages(messages) is messages

    assert os.environ["ATLAS_MANAGED_MODE"] == "0"


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


def test_altas_provider_uses_profile_secret_scope_in_multiplex(monkeypatch) -> None:
    profile = get_provider_profile("altas")
    assert profile is not None
    monkeypatch.setenv("ATLAS_LEASE_TOKEN", "wrong-global-lease")
    monkeypatch.setenv("ATLAS_STORE_ID", "wrong-global-store")

    set_multiplex_active(True)
    try:
        first_token = set_secret_scope({
            "ATLAS_LEASE_TOKEN": "lease-a",
            "ATLAS_STORE_ID": "store-a",
            "ATLAS_JOB_ID": "job-a",
            "ATLAS_CLAIM_TOKEN": "claim-a",
        })
        try:
            _, first = profile.build_api_kwargs_extras()
        finally:
            reset_secret_scope(first_token)

        second_token = set_secret_scope({
            "ATLAS_LEASE_TOKEN": "lease-b",
            "ATLAS_STORE_ID": "store-b",
            "ATLAS_JOB_ID": "job-b",
            "ATLAS_CLAIM_TOKEN": "claim-b",
        })
        try:
            _, second = profile.build_api_kwargs_extras()
        finally:
            reset_secret_scope(second_token)
    finally:
        set_multiplex_active(False)

    assert first["extra_headers"] == {
        "X-Atlas-Lease": "lease-a",
        "X-Atlas-Store-ID": "store-a",
        "X-Atlas-Job-ID": "job-a",
        "X-Atlas-Claim-Token": "claim-a",
    }
    assert second["extra_headers"] == {
        "X-Atlas-Lease": "lease-b",
        "X-Atlas-Store-ID": "store-b",
        "X-Atlas-Job-ID": "job-b",
        "X-Atlas-Claim-Token": "claim-b",
    }


def test_altas_provider_fails_closed_without_multiplex_secret_scope(
    monkeypatch,
) -> None:
    profile = get_provider_profile("altas")
    assert profile is not None
    monkeypatch.setenv("ATLAS_CLAIM_TOKEN", "another-profile-claim")

    set_multiplex_active(True)
    try:
        with pytest.raises(UnscopedSecretError):
            profile.build_api_kwargs_extras()
    finally:
        set_multiplex_active(False)


def test_auxiliary_calls_apply_fresh_altas_headers_without_caching(
    monkeypatch,
) -> None:
    client = MagicMock()
    client.base_url = "http://control.test/v1"
    client.chat.completions.create.return_value = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]
    )
    monkeypatch.setenv("ATLAS_LEASE_TOKEN", "wrong-global-lease")
    monkeypatch.setenv("ATLAS_JOB_ID", "wrong-global-job")

    resolve = patch(
        "agent.auxiliary_client._resolve_task_provider_model",
        return_value=("altas", "altas-fixed-ops", None, "device-token", None),
    )
    cached = patch(
        "agent.auxiliary_client._get_cached_client",
        return_value=(client, "altas-fixed-ops"),
    )

    set_multiplex_active(True)
    try:
        with resolve, cached:
            first_token = set_secret_scope({
                "ATLAS_LEASE_TOKEN": "lease-a",
                "ATLAS_JOB_ID": "job-a",
                "ATLAS_CLAIM_TOKEN": "claim-a",
            })
            try:
                call_llm(
                    provider="altas",
                    model="altas-fixed-ops",
                    messages=[{"role": "user", "content": "first"}],
                    fallback_policy="none",
                )
            finally:
                reset_secret_scope(first_token)

            second_token = set_secret_scope({
                "ATLAS_LEASE_TOKEN": "lease-b",
                "ATLAS_JOB_ID": "job-b",
                "ATLAS_CLAIM_TOKEN": "claim-b",
            })
            try:
                call_llm(
                    provider="altas",
                    model="altas-fixed-ops",
                    messages=[{"role": "user", "content": "second"}],
                    fallback_policy="none",
                )
            finally:
                reset_secret_scope(second_token)
    finally:
        set_multiplex_active(False)

    first_headers = client.chat.completions.create.call_args_list[0].kwargs[
        "extra_headers"
    ]
    second_headers = client.chat.completions.create.call_args_list[1].kwargs[
        "extra_headers"
    ]
    assert first_headers == {
        "X-Atlas-Lease": "lease-a",
        "X-Atlas-Job-ID": "job-a",
        "X-Atlas-Claim-Token": "claim-a",
    }
    assert second_headers == {
        "X-Atlas-Lease": "lease-b",
        "X-Atlas-Job-ID": "job-b",
        "X-Atlas-Claim-Token": "claim-b",
    }


def test_managed_auxiliary_client_cache_isolated_by_real_profile_scopes(
    tmp_path,
) -> None:
    """Two scoped device credentials must never share one cached bearer client."""

    class _FakeClient:
        def __init__(self, *, api_key: str, base_url: str) -> None:
            self.api_key = api_key
            self.base_url = base_url
            self.closed = False

        def close(self) -> None:
            self.closed = True

    created: list[_FakeClient] = []

    def _build_client(*, api_key: str, base_url: str, **_kwargs) -> _FakeClient:
        client = _FakeClient(api_key=api_key, base_url=base_url)
        created.append(client)
        return client

    profile_a = tmp_path / "profile-a"
    profile_b = tmp_path / "profile-b"
    profile_a.mkdir()
    profile_b.mkdir()

    def _resolve_in_scope(profile_home, secrets):
        home_token = set_hermes_home_override(profile_home)
        secret_token = set_secret_scope(secrets)
        try:
            return _get_cached_client("altas", model="altas-fixed-ops")[0]
        finally:
            reset_secret_scope(secret_token)
            reset_hermes_home_override(home_token)

    secrets_a = {
        "ATLAS_DEVICE_TOKEN": "device-token-profile-a",
        "ATLAS_CLAIM_TOKEN": "claim-token-profile-a",
    }
    secrets_b = {
        "ATLAS_DEVICE_TOKEN": "device-token-profile-b",
        "ATLAS_CLAIM_TOKEN": "claim-token-profile-b",
    }

    shutdown_cached_clients()
    set_multiplex_active(True)
    try:
        with patch(
            "agent.auxiliary_client._create_openai_client",
            side_effect=_build_client,
        ):
            client_a = _resolve_in_scope(profile_a, secrets_a)
            client_b = _resolve_in_scope(profile_b, secrets_b)
            client_a_again = _resolve_in_scope(profile_a, secrets_a)

        assert client_a is not client_b
        assert client_a_again is client_a
        assert client_a.api_key == "device-token-profile-a"
        assert client_b.api_key == "device-token-profile-b"
        assert len(created) == 2

        cache_keys = repr(tuple(_client_cache))
        for raw_value in (*secrets_a.values(), *secrets_b.values()):
            assert raw_value not in cache_keys
        assert str(profile_a) not in cache_keys
        assert str(profile_b) not in cache_keys
    finally:
        set_multiplex_active(False)
        shutdown_cached_clients()


def test_altas_provider_uses_safe_configurable_default_max_tokens(monkeypatch) -> None:
    profile = get_provider_profile("altas")
    assert profile is not None

    monkeypatch.delenv("ATLAS_DEFAULT_MODEL_MAX_TOKENS", raising=False)
    assert profile.get_max_tokens("altas-fixed-ops") == 400

    monkeypatch.setenv("ATLAS_DEFAULT_MODEL_MAX_TOKENS", "256")
    assert profile.get_max_tokens("altas-fixed-ops") == 256

    monkeypatch.setenv("ATLAS_DEFAULT_MODEL_MAX_TOKENS", "not-a-number")
    assert profile.get_max_tokens("altas-fixed-ops") == 400
