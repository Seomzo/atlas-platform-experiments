"""Mandatory fail-closed policy guard for managed engine tool dispatch."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from altas.credentials import SecretHandle, SystemKeyringVault
from altas.managed.client import AltasControlPlaneClient
from altas.managed.context import ManagedContext


@dataclass(frozen=True, slots=True)
class GuardResult:
    allowed: bool
    reason_code: str
    message: str


_TOOL_CAPABILITIES = {
    "run_daily_fixed_ops_report": "fixed_ops.daily_report",
    "fixed_ops_daily_report": "fixed_ops.daily_report",
    "pull_advisor_metrics": "fixed_ops.advisor_metrics",
    "pull_parts_summary": "fixed_ops.parts_summary",
    "draft_service_manager_email": "communications.manager_draft",
}


def managed_mode_enabled(env: Mapping[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    return values.get("ATLAS_MANAGED_MODE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ValueError(f"{key} is required in Atlas managed mode")
    return value


def _lease_token(env: Mapping[str, str]) -> str:
    direct = env.get("ATLAS_LEASE_TOKEN", "").strip()
    if direct:
        return direct
    lease_file = env.get("ATLAS_LEASE_FILE", "").strip()
    if not lease_file:
        raise ValueError("ATLAS_LEASE_TOKEN or ATLAS_LEASE_FILE is required")
    path = Path(lease_file).expanduser()
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("Atlas lease file is empty")
    return token


def _device_token(env: Mapping[str, str]) -> str:
    """Resolve the prototype device credential without logging it.

    `ATLAS_DEVICE_TOKEN` is supported only for local development and
    short-lived process injection. Managed installs should supply a keyring
    handle with the four-part form `tenant/store/kind/name`.
    """

    direct = env.get("ATLAS_DEVICE_TOKEN", "").strip()
    if direct:
        return direct
    raw_handle = env.get("ATLAS_DEVICE_TOKEN_HANDLE", "").strip()
    parts = raw_handle.split("/")
    if len(parts) != 4 or any(not part for part in parts):
        raise ValueError("ATLAS_DEVICE_TOKEN_HANDLE must be tenant/store/kind/name")
    return SystemKeyringVault().resolve(SecretHandle(*parts))


def _context(env: Mapping[str, str]) -> ManagedContext:
    return ManagedContext(
        tenant_id=_required(env, "ATLAS_TENANT_ID"),
        store_id=_required(env, "ATLAS_STORE_ID"),
        device_id=_required(env, "ATLAS_DEVICE_ID"),
        agent_id=_required(env, "ATLAS_AGENT_ID"),
        job_id=_required(env, "ATLAS_JOB_ID"),
        correlation_id=env.get("ATLAS_CORRELATION_ID", "").strip()
        or _required(env, "ATLAS_JOB_ID"),
        user_id=env.get("ATLAS_USER_ID", "").strip() or "system",
    )


def _argument_store_id(arguments: Mapping[str, Any]) -> str | None:
    for key in ("store_id", "storeId", "dealership_store_id"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def guard_tool_call(
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> GuardResult:
    """Authorize one engine tool call.

    Non-managed development sessions pass through. Managed sessions deny on
    missing context, scope mismatch, timeout, invalid response, or any other
    control-plane failure.
    """

    env = environ or os.environ
    if not managed_mode_enabled(env):
        return GuardResult(True, "NOT_MANAGED", "Managed policy is not enabled")

    try:
        context = _context(env)
        requested_store = _argument_store_id(arguments or {})
        if requested_store and requested_store != context.store_id:
            return GuardResult(
                False,
                "SCOPE_MISMATCH",
                "Atlas denied this action because its store scope does not match.",
            )

        capability = _TOOL_CAPABILITIES.get(tool_name, f"tool.{tool_name}")
        with AltasControlPlaneClient(
            base_url=_required(env, "ATLAS_CONTROL_PLANE_URL"),
            device_token=_device_token(env),
            timeout_seconds=float(env.get("ATLAS_POLICY_TIMEOUT_SECONDS", "3")),
        ) as client:
            decision = client.evaluate_policy(
                lease=_lease_token(env),
                context=context,
                claim_token=_required(env, "ATLAS_CLAIM_TOKEN"),
                capability=capability,
                tool_name=tool_name,
            )
        if decision.allowed:
            return GuardResult(True, decision.reason_code, "Allowed by Atlas policy")
        return GuardResult(
            False,
            decision.reason_code,
            f"Atlas policy denied this action ({decision.reason_code}).",
        )
    except Exception:
        # Intentionally avoid embedding exception details: HTTP client errors
        # can contain URLs or response snippets that do not belong in a model
        # tool result. Detailed diagnostics stay in the worker's safe log path.
        return GuardResult(
            False,
            "POLICY_UNAVAILABLE",
            "Atlas could not verify permission for this action. No tool was run.",
        )


def guard_tool_call_from_json(
    tool_name: str,
    arguments_json: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> GuardResult:
    """Convenience boundary for callers that still hold JSON arguments."""

    try:
        parsed = json.loads(arguments_json) if arguments_json else {}
    except json.JSONDecodeError:
        parsed = {}
    return guard_tool_call(
        tool_name,
        parsed if isinstance(parsed, dict) else {},
        environ=environ,
    )
