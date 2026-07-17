from __future__ import annotations

import asyncio
import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agent.secret_scope import (
    current_secret_scope,
    reset_secret_scope,
    set_secret_scope,
)
from altas.cortex.managed_dispatch import CortexDispatchAdmission
from altas.managed.context import ManagedContext, ManagedRequestAuthorization
from altas.managed.request_scope import (
    ManagedRequestScopeError,
    build_managed_request_secret_scope,
    managed_request_scope,
)


def _authorization(
    *,
    job_id: str = "job-a",
    correlation_id: str = "correlation-a",
    capability: str = "cortex.memory_maintenance",
    lease_token: str = "lease-a",
    claim_token: str = "claim-a",
) -> ManagedRequestAuthorization:
    dispatch_admission = None
    dispatch_key = None
    if capability == "cortex.memory_maintenance":
        dispatch_admission, dispatch_key = CortexDispatchAdmission.create(
            local_job_id="job_local_a",
            admission_id="admission_a",
            root_job_id="job_local_a",
            canonical_input_hash="a" * 64,
            attempt=0,
            due_at="2026-07-14T00:00:00Z",
        )
    return ManagedRequestAuthorization(
        context=ManagedContext(
            tenant_id="tenant-a",
            store_id="store-a",
            device_id="device-a",
            agent_id="agent-a",
            job_id=job_id,
            correlation_id=correlation_id,
        ),
        capability=capability,
        control_plane_url="https://control.example.test",
        lease_token=lease_token,
        claim_token=claim_token,
        cortex_dispatch_admission=dispatch_admission,
        cortex_dispatch_key=dispatch_key,
    )


def _write_profile(
    home: Path, *, identity_override: dict[str, str] | None = None
) -> bytes:
    values = {
        "ATLAS_TENANT_ID": "tenant-a",
        "ATLAS_STORE_ID": "store-a",
        "ATLAS_AGENT_ID": "agent-a",
        "ATLAS_DEVICE_ID": "device-a",
        "ATLAS_DEVICE_TOKEN": "profile-device-token",
        "ATLAS_CONTROL_PLANE_URL": "https://control.example.test",
        "ATLAS_LEASE_TOKEN": "stale-lease-must-be-overlaid",
        "ATLAS_JOB_ID": "stale-job-must-be-overlaid",
        "ATLAS_CLAIM_TOKEN": "stale-claim-must-be-overlaid",
        "PROFILE_ONLY_SECRET": "profile-only",
    }
    values.update(identity_override or {})
    home.mkdir(parents=True)
    payload = "".join(f"{key}={value}\n" for key, value in values.items()).encode()
    (home / ".env").write_bytes(payload)
    return payload


def test_authorization_is_frozen_and_tokens_are_not_represented() -> None:
    authorization = _authorization(
        lease_token=" super-secret-lease ",
        claim_token=" super-secret-claim ",
    )

    rendered = repr(authorization)
    assert "super-secret-lease" not in rendered
    assert "super-secret-claim" not in rendered
    assert authorization.lease_token == "super-secret-lease"
    assert authorization.claim_token == "super-secret-claim"
    assert authorization.capability == "cortex.memory_maintenance"
    assert authorization.control_plane_url == "https://control.example.test"
    with pytest.raises(FrozenInstanceError):
        authorization.claim_token = "replacement"  # type: ignore[misc]


def test_request_scope_rejects_profile_control_plane_mismatch(tmp_path: Path) -> None:
    home = tmp_path / "profile"
    _write_profile(
        home,
        identity_override={
            "ATLAS_CONTROL_PLANE_URL": "https://different-control.example.test"
        },
    )

    with pytest.raises(ManagedRequestScopeError, match="endpoint mismatch"):
        build_managed_request_secret_scope(home, _authorization())


@pytest.mark.parametrize("field_name", ["lease_token", "claim_token"])
def test_authorization_requires_each_ephemeral_token(field_name: str) -> None:
    kwargs = {"lease_token": "lease", "claim_token": "claim"}
    kwargs[field_name] = "  "

    with pytest.raises(ValueError, match=field_name):
        _authorization(**kwargs)


def test_authorization_requires_job_capability() -> None:
    with pytest.raises(ValueError, match="capability"):
        _authorization(capability="  ")


def test_scope_binds_profile_overlays_request_and_restores_outer_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_home = tmp_path / "profile"
    original_env_file = _write_profile(profile_home)
    authorization = _authorization()
    monkeypatch.setenv("ATLAS_TENANT_ID", "poison-global-tenant")
    monkeypatch.setenv("ATLAS_LEASE_TOKEN", "poison-global-lease")
    outer = {"OUTER_SCOPE": "preserve-me"}
    outer_token = set_secret_scope(outer)
    try:
        with managed_request_scope(profile_home, authorization) as scoped:
            assert current_secret_scope() is scoped
            assert scoped["ATLAS_MANAGED_MODE"] == "1"
            assert scoped["ATLAS_TENANT_ID"] == "tenant-a"
            assert scoped["ATLAS_LEASE_TOKEN"] == "lease-a"
            assert scoped["ATLAS_JOB_ID"] == "job-a"
            assert scoped["ATLAS_JOB_CAPABILITY"] == "cortex.memory_maintenance"
            assert scoped["ATLAS_CLAIM_TOKEN"] == "claim-a"
            assert scoped["ATLAS_CORRELATION_ID"] == "correlation-a"
            assert scoped["ATLAS_CORTEX_DISPATCH_KEY"]
            assert scoped["ATLAS_CORTEX_DISPATCH_ADMISSION"]
            assert scoped["PROFILE_ONLY_SECRET"] == "profile-only"
            with pytest.raises(TypeError):
                scoped["ATLAS_CLAIM_TOKEN"] = "mutated"  # type: ignore[index]
        assert current_secret_scope() is outer

        with pytest.raises(RuntimeError, match="inside request"):
            with managed_request_scope(profile_home, authorization):
                raise RuntimeError("inside request")
        assert current_secret_scope() is outer
    finally:
        reset_secret_scope(outer_token)

    assert current_secret_scope() is None
    assert (profile_home / ".env").read_bytes() == original_env_file
    assert os.environ["ATLAS_TENANT_ID"] == "poison-global-tenant"
    assert os.environ["ATLAS_LEASE_TOKEN"] == "poison-global-lease"


@pytest.mark.parametrize(
    ("env_name", "profile_value"),
    [
        ("ATLAS_TENANT_ID", ""),
        ("ATLAS_TENANT_ID", "tenant-b"),
        ("ATLAS_STORE_ID", ""),
        ("ATLAS_STORE_ID", "store-b"),
        ("ATLAS_AGENT_ID", ""),
        ("ATLAS_AGENT_ID", "agent-b"),
        ("ATLAS_DEVICE_ID", ""),
        ("ATLAS_DEVICE_ID", "device-b"),
    ],
)
def test_scope_rejects_missing_or_mismatched_profile_identity(
    tmp_path: Path,
    env_name: str,
    profile_value: str,
) -> None:
    profile_home = tmp_path / f"profile-{env_name.lower()}-{profile_value or 'missing'}"
    _write_profile(profile_home, identity_override={env_name: profile_value})

    with pytest.raises(ManagedRequestScopeError, match=env_name):
        build_managed_request_secret_scope(profile_home, _authorization())
    assert current_secret_scope() is None


def test_concurrent_request_scopes_do_not_share_rotating_claims(
    tmp_path: Path,
) -> None:
    profile_home = tmp_path / "profile"
    _write_profile(profile_home)
    first = _authorization(
        job_id="job-first",
        correlation_id="correlation-first",
        lease_token="lease-first",
        claim_token="claim-first",
    )
    second = _authorization(
        job_id="job-second",
        correlation_id="correlation-second",
        lease_token="lease-second",
        claim_token="claim-second",
    )

    async def observe(authorization: ManagedRequestAuthorization) -> tuple[str, str]:
        with managed_request_scope(profile_home, authorization):
            await asyncio.sleep(0)
            scoped = current_secret_scope()
            assert scoped is not None
            return scoped["ATLAS_JOB_ID"], scoped["ATLAS_CLAIM_TOKEN"]

    async def run_both() -> list[tuple[str, str]]:
        return list(await asyncio.gather(observe(first), observe(second)))

    assert asyncio.run(run_both()) == [
        ("job-first", "claim-first"),
        ("job-second", "claim-second"),
    ]
    assert current_secret_scope() is None
