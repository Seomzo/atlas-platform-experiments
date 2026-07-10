from __future__ import annotations

import json

import model_tools


def test_managed_guard_blocks_before_upstream_tool_dispatch(monkeypatch) -> None:
    monkeypatch.setenv("ALTAS_MANAGED_MODE", "1")
    for name in (
        "ALTAS_CONTROL_PLANE_URL",
        "ALTAS_DEVICE_TOKEN",
        "ALTAS_LEASE_TOKEN",
        "ALTAS_TENANT_ID",
        "ALTAS_STORE_ID",
        "ALTAS_DEVICE_ID",
        "ALTAS_AGENT_ID",
        "ALTAS_JOB_ID",
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
        "error": "Altas could not verify permission for this action. No tool was run.",
        "reason_code": "POLICY_UNAVAILABLE",
    }
