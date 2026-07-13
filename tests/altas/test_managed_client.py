from __future__ import annotations

import httpx

from altas.managed.client import AltasControlPlaneClient
from altas.managed.context import ManagedContext


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
