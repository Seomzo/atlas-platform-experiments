from __future__ import annotations

import json

import httpx
import pytest

from altas.managed.client import AltasControlPlaneClient
from altas.managed.context import ManagedContext
from altas.cortex.managed_dispatch import CortexDispatchAdmission


def _cortex_dispatch() -> tuple[CortexDispatchAdmission, str]:
    return CortexDispatchAdmission.create(
        local_job_id="job_local_a",
        admission_id="admission_a",
        root_job_id="job_local_a",
        canonical_input_hash="a" * 64,
        attempt=0,
        due_at="2026-07-14T00:00:00Z",
    )


def test_policy_and_model_requests_send_claim_token_header() -> None:
    observed: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append((request.url.path, request.headers.get("X-Atlas-Claim-Token")))
        if request.url.path.endswith("/policy/evaluate"):
            return httpx.Response(200, json={"allowed": True, "code": "allowed"})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"role": "assistant", "content": "safe"}}]},
        )

    context = ManagedContext(
        tenant_id="tenant-a",
        store_id="store-a",
        device_id="device-a",
        agent_id="agent-a",
        job_id="job-a",
        correlation_id="job-a",
    )
    claim_token = "claim-token-with-more-than-thirty-two-characters"
    with AltasControlPlaneClient(
        base_url="http://control.test",
        device_token="device-token",
        transport=httpx.MockTransport(handler),
    ) as client:
        client.evaluate_policy(
            lease="lease-token",
            context=context,
            claim_token=claim_token,
            capability="fixed_ops.daily_report",
        )
        client.chat_completion(
            lease="lease-token",
            context=context,
            claim_token=claim_token,
            model="altas-fixed-ops",
            messages=[{"role": "user", "content": "summary"}],
        )

    assert observed == [
        ("/api/v1/worker/policy/evaluate", claim_token),
        ("/v1/chat/completions", claim_token),
    ]


def test_client_repr_does_not_expose_credentials() -> None:
    client = AltasControlPlaneClient(
        base_url="http://control.test",
        device_token="highly-sensitive-device-token",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={})),
    )
    try:
        assert "highly-sensitive-device-token" not in repr(client)
    finally:
        client.close()


def test_cortex_maintenance_signal_uses_lease_and_opaque_dispatch_key() -> None:
    observed: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        observed["path"] = request.url.path
        observed["lease"] = request.headers.get("X-Atlas-Lease")
        observed["claim"] = request.headers.get("X-Atlas-Claim-Token")
        observed["body"] = json.loads(request.content)
        return httpx.Response(200, json={"job_id": "job-cortex", "created": True})

    with AltasControlPlaneClient(
        base_url="http://control.test",
        device_token="device-token",
        transport=httpx.MockTransport(handler),
    ) as client:
        dispatch_admission, dispatch_key = _cortex_dispatch()
        result = client.ensure_cortex_maintenance(
            lease="lease-token",
            tenant_id="tenant-a",
            store_id="store-a",
            agent_id="agent-a",
            dispatch_key=dispatch_key,
            dispatch_admission=dispatch_admission,
        )

    assert result == {"job_id": "job-cortex", "created": True}
    assert observed == {
        "path": "/api/v1/worker/jobs/cortex/ensure",
        "lease": "lease-token",
        "claim": None,
        "body": {
            "tenant_id": "tenant-a",
            "store_id": "store-a",
            "agent_id": "agent-a",
            "dispatch_key": dispatch_key,
            "dispatch_admission": dispatch_admission.to_mapping(),
        },
    }


@pytest.mark.parametrize(
    ("status_code", "code"),
    [
        (409, "cortex_dispatch_already_active"),
        (429, "cortex_dispatch_daily_limit_exceeded"),
    ],
)
def test_cortex_admission_denial_is_a_nonfatal_typed_result(
    status_code: int,
    code: str,
) -> None:
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            status_code,
            json={"detail": {"code": code}},
        )
    )
    with AltasControlPlaneClient(
        base_url="http://control.test",
        device_token="device-token",
        transport=transport,
    ) as client:
        dispatch_admission, dispatch_key = _cortex_dispatch()
        result = client.ensure_cortex_maintenance(
            lease="lease-token",
            tenant_id="tenant-a",
            store_id="store-a",
            agent_id="agent-a",
            dispatch_key=dispatch_key,
            dispatch_admission=dispatch_admission,
        )

    assert result == {
        "admitted": False,
        "created": False,
        "requeued": False,
        "code": code,
    }


def test_client_exposes_claim_bound_job_lease_once() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "job": {"id": "job-cortex"},
                "authorization_lease": {
                    "token": "job-bound-lease",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "capabilities": ["cortex.memory_maintenance", "model.chat"],
                },
            },
        )

    with AltasControlPlaneClient(
        base_url="http://control.test",
        device_token="device-token",
        transport=httpx.MockTransport(handler),
    ) as client:
        job = client.next_job(
            lease="poll-lease",
            tenant_id="tenant-a",
            store_id="store-a",
            agent_id="agent-a",
        )
        lease = client.take_job_authorization_lease()

        assert job == {"id": "job-cortex"}
        assert lease is not None
        assert lease.token == "job-bound-lease"
        assert lease.capabilities == (
            "cortex.memory_maintenance",
            "model.chat",
        )
        assert client.take_job_authorization_lease() is None
