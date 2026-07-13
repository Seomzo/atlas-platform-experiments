from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest

from agent.agent_runtime_helpers import invoke_tool
from agent.tool_executor import _run_agent_tool_execution_middleware


class _MemoryManager:
    @staticmethod
    def has_tool(name: str) -> bool:
        return name == "mem0_search"


class _Agent:
    session_id = "session-a"
    _current_turn_id = "turn-a"
    _current_api_request_id = "request-a"
    _context_engine_tool_names = {"lcm_grep"}
    _memory_manager = _MemoryManager()


def _run_middleware_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    def run_inline(
        _name: str,
        arguments: dict[str, Any],
        execute: Any,
        **_kwargs: Any,
    ) -> Any:
        return execute(arguments)

    monkeypatch.setattr(
        "hermes_cli.middleware.run_tool_execution_middleware",
        run_inline,
    )


@pytest.mark.parametrize(
    "tool_name",
    [
        "memory",
        "session_search",
        "read_terminal",
        "delegate_task",
        "lcm_grep",
        "mem0_search",
    ],
)
def test_managed_agent_owned_tools_fail_closed_before_handler(
    monkeypatch: pytest.MonkeyPatch,
    tool_name: str,
) -> None:
    _run_middleware_inline(monkeypatch)
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

    handler_called = False

    def handler(_arguments: dict[str, Any]) -> str:
        nonlocal handler_called
        handler_called = True
        return "handler-ran"

    result, observed_args = _run_agent_tool_execution_middleware(
        _Agent(),
        function_name=tool_name,
        function_args={"query": "sensitive"},
        effective_task_id="task-a",
        tool_call_id="call-a",
        execute=handler,
    )

    assert handler_called is False
    assert observed_args == {"query": "sensitive"}
    assert json.loads(result) == {
        "error": "Atlas could not verify permission for this action. No tool was run.",
        "reason_code": "POLICY_UNAVAILABLE",
    }


def test_agent_owned_tools_preserve_upstream_behavior_outside_managed_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _run_middleware_inline(monkeypatch)
    monkeypatch.delenv("ATLAS_MANAGED_MODE", raising=False)

    result, observed_args = _run_agent_tool_execution_middleware(
        _Agent(),
        function_name="memory",
        function_args={"action": "read"},
        effective_task_id="task-a",
        tool_call_id="call-a",
        execute=lambda arguments: {"ran": arguments},
    )

    assert result == {"ran": {"action": "read"}}
    assert observed_args == {"action": "read"}


def test_concurrent_invoke_tool_path_cannot_bypass_managed_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _run_middleware_inline(monkeypatch)
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

    with patch(
        "tools.memory_tool.memory_tool",
        side_effect=AssertionError("managed tool handler must not run"),
    ) as memory_handler:
        result = invoke_tool(
            _Agent(),
            "memory",
            {"action": "read"},
            "task-a",
            pre_tool_block_checked=True,
            skip_tool_request_middleware=True,
        )

    memory_handler.assert_not_called()
    assert json.loads(result)["reason_code"] == "POLICY_UNAVAILABLE"
