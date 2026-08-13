from __future__ import annotations

from typing import Any

import pytest

from agent.secret_scope import (
    reset_secret_scope,
    set_multiplex_active,
    set_secret_scope,
)
from altas.managed.client import PolicyDecision
from altas.managed.policy_guard import guard_tool_call


def _managed_env() -> dict[str, str]:
    return {
        "ATLAS_MANAGED_MODE": "true",
        "ATLAS_CONTROL_PLANE_URL": "http://control.test",
        "ATLAS_DEVICE_TOKEN": "device-token",
        "ATLAS_LEASE_TOKEN": "lease-token",
        "ATLAS_TENANT_ID": "tenant-a",
        "ATLAS_STORE_ID": "store-a",
        "ATLAS_DEVICE_ID": "device-a",
        "ATLAS_AGENT_ID": "agent-a",
        "ATLAS_JOB_ID": "job-a",
        "ATLAS_CLAIM_TOKEN": "claim-token-with-more-than-thirty-two-characters",
    }


@pytest.fixture(autouse=True)
def _reset_multiplex_mode():
    set_multiplex_active(False)
    yield
    set_multiplex_active(False)


def test_non_managed_session_passes_through(monkeypatch: Any) -> None:
    for key, value in _managed_env().items():
        monkeypatch.setenv(key, f"poison-{value}")
    result = guard_tool_call("terminal", {}, environ={})

    assert result.allowed is True
    assert result.reason_code == "NOT_MANAGED"


def test_managed_scope_mismatch_denies_before_network() -> None:
    result = guard_tool_call(
        "run_daily_fixed_ops_report",
        {"store_id": "store-b"},
        environ=_managed_env(),
    )

    assert result.allowed is False
    assert result.reason_code == "SCOPE_MISMATCH"


def test_missing_managed_context_fails_closed() -> None:
    result = guard_tool_call(
        "run_daily_fixed_ops_report",
        {},
        environ={"ATLAS_MANAGED_MODE": "1"},
    )

    assert result.allowed is False
    assert result.reason_code == "POLICY_UNAVAILABLE"


def test_remote_allow_is_required(monkeypatch: Any) -> None:
    seen: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            seen["init"] = kwargs

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def evaluate_policy(self, **kwargs: Any) -> PolicyDecision:
            seen["policy"] = kwargs
            return PolicyDecision(True, "allowed")

    monkeypatch.setattr(
        "altas.managed.policy_guard.AltasControlPlaneClient",
        FakeClient,
    )
    result = guard_tool_call(
        "run_daily_fixed_ops_report",
        {"store_id": "store-a"},
        environ=_managed_env(),
    )

    assert result.allowed is True
    assert seen["policy"]["capability"] == "fixed_ops.daily_report"
    assert seen["policy"]["claim_token"].startswith("claim-token-")


def test_current_profile_scope_beats_poisoned_process_environment(
    monkeypatch: Any,
) -> None:
    seen: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            seen["init"] = kwargs

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def evaluate_policy(self, **kwargs: Any) -> PolicyDecision:
            seen["policy"] = kwargs
            return PolicyDecision(True, "allowed")

    monkeypatch.setattr(
        "altas.managed.policy_guard.AltasControlPlaneClient",
        FakeClient,
    )
    for key, value in _managed_env().items():
        monkeypatch.setenv(key, f"poison-{value}")

    set_multiplex_active(True)
    token = set_secret_scope(_managed_env())
    try:
        result = guard_tool_call(
            "run_daily_fixed_ops_report",
            {"store_id": "store-a"},
        )
    finally:
        reset_secret_scope(token)

    assert result.allowed is True
    assert seen["init"]["base_url"] == "http://control.test"
    assert seen["init"]["device_token"] == "device-token"
    assert seen["policy"]["context"].store_id == "store-a"
    assert seen["policy"]["claim_token"].startswith("claim-token-")


def test_unscoped_multiplex_policy_call_fails_closed(monkeypatch: Any) -> None:
    for key, value in _managed_env().items():
        monkeypatch.setenv(key, f"poison-{value}")
    set_multiplex_active(True)

    result = guard_tool_call("terminal", {})

    assert result.allowed is False
    assert result.reason_code == "POLICY_UNAVAILABLE"


def test_control_plane_exception_fails_closed(monkeypatch: Any) -> None:
    class BrokenClient:
        def __init__(self, **_kwargs: Any) -> None:
            raise TimeoutError("sensitive upstream detail")

    monkeypatch.setattr(
        "altas.managed.policy_guard.AltasControlPlaneClient",
        BrokenClient,
    )
    result = guard_tool_call(
        "run_daily_fixed_ops_report",
        {},
        environ=_managed_env(),
    )

    assert result.allowed is False
    assert result.reason_code == "POLICY_UNAVAILABLE"
    assert "sensitive" not in result.message


def test_unclassified_managed_tool_denies_before_network(monkeypatch: Any) -> None:
    class UnexpectedClient:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("unclassified tool must not reach policy")

    monkeypatch.setattr(
        "altas.managed.policy_guard.AltasControlPlaneClient",
        UnexpectedClient,
    )
    result = guard_tool_call(
        "terminal",
        {"command": "ignored"},
        environ=_managed_env(),
    )

    assert result.allowed is False
    assert result.reason_code == "ACTION_UNCLASSIFIED"


def test_consequential_tool_requires_exact_consumed_approval(
    monkeypatch: Any,
) -> None:
    seen: dict[str, Any] = {}

    class FakeClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def evaluate_policy(self, **_kwargs: Any) -> PolicyDecision:
            return PolicyDecision(True, "allowed")

        def consume_managed_approval(self, **kwargs: Any) -> object:
            seen.update(kwargs)
            return object()

    monkeypatch.setattr(
        "altas.managed.policy_guard.AltasControlPlaneClient",
        FakeClient,
    )
    environment = {
        **_managed_env(),
        "ATLAS_ACTION_APPROVAL_ID": "managed_approval_exact",
        "ATLAS_ACTION_APPROVAL_VERSION": "2",
    }
    result = guard_tool_call(
        "export_synthetic_fixed_ops_report",
        {"report_id": "report-demo-1"},
        environ=environment,
    )

    assert result.allowed is True
    assert seen["approval_id"] == "managed_approval_exact"
    assert seen["expected_version"] == 2
    assert seen["action"].target_id == "report-demo-1"


def test_consequential_tool_without_exact_approval_fails_before_execution(
    monkeypatch: Any,
) -> None:
    class FakeClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def evaluate_policy(self, **_kwargs: Any) -> PolicyDecision:
            return PolicyDecision(True, "allowed")

        def consume_managed_approval(self, **_kwargs: Any) -> object:
            raise AssertionError("approval must not be guessed")

    monkeypatch.setattr(
        "altas.managed.policy_guard.AltasControlPlaneClient",
        FakeClient,
    )
    result = guard_tool_call(
        "export_synthetic_fixed_ops_report",
        {"report_id": "report-demo-1"},
        environ=_managed_env(),
    )

    assert result.allowed is False
    assert result.reason_code == "APPROVAL_REQUIRED"
