from __future__ import annotations

import json

import model_tools


def test_managed_guard_blocks_before_upstream_tool_dispatch(monkeypatch) -> None:
    monkeypatch.setenv("ATLAS_MANAGED_MODE", "1")
    for name in (
        "ATLAS_CONTROL_PLANE_URL",
        "ATLAS_DEVICE_TOKEN",
        "ATLAS_LEASE_TOKEN",
        "ATLAS_TENANT_ID",
        "ATLAS_STORE_ID",
        "ATLAS_DEVICE_ID",
        "ATLAS_AGENT_ID",
        "ATLAS_JOB_ID",
    ):
        monkeypatch.delenv(name, raising=False)

    result = json.loads(
        model_tools.handle_function_call(
            "terminal",
            {"command": "this must never execute"},
            skip_pre_tool_call_hook=True,
            skip_tool_request_middleware=True,
        )
    )

    assert result == {
        "error": "Atlas could not verify permission for this action. No tool was run.",
        "reason_code": "POLICY_UNAVAILABLE",
    }
