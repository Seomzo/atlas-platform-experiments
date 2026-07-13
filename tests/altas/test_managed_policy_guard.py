from __future__ import annotations

from typing import Any

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


def test_non_managed_session_passes_through() -> None:
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
