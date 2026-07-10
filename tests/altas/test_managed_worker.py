from __future__ import annotations

from typing import Any

import pytest

from altas.managed.client import Lease, PolicyDecision
from altas.managed.worker import AltasWorker, WorkerSettings


class RecordingClient:
    def __init__(self) -> None:
        self.heartbeat_calls = 0
        self.completions: list[dict[str, Any]] = []
        self.policy_calls: list[dict[str, Any]] = []
        self.chat_calls: list[dict[str, Any]] = []

    def heartbeat(self, **_kwargs: Any) -> Lease:
        self.heartbeat_calls += 1
        return Lease(
            token=f"lease-{self.heartbeat_calls}",
            expires_at="2099-01-01T00:00:00Z",
        )

    def next_job(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "id": "job-worker-renewal",
            "tenant_id": "tenant-1",
            "store_id": "store-sunrise-vw",
            "agent_id": "agent-1",
            "capability": "fixed_ops.daily_report",
            "claim_token": "claim-token-with-more-than-thirty-two-characters",
            "payload": {"report_date": "2026-07-08"},
        }

    def evaluate_policy(self, **kwargs: Any) -> PolicyDecision:
        self.policy_calls.append(kwargs)
        return PolicyDecision(allowed=True, reason_code="allowed")

    def chat_completion(self, **kwargs: Any) -> dict[str, Any]:
        self.chat_calls.append(kwargs)
        return {
            "choices": [
                {"message": {"content": "Synthetic fixed-ops summary for test data."}}
            ]
        }

    def complete_job(self, **kwargs: Any) -> dict[str, Any]:
        self.completions.append(kwargs)
        return {"job": {"status": kwargs["status"]}}


@pytest.mark.parametrize("execution_fails", [False, True])
def test_worker_renews_lease_before_success_or_failure_completion(
    monkeypatch: pytest.MonkeyPatch,
    execution_fails: bool,
) -> None:
    client = RecordingClient()
    worker = AltasWorker(
        WorkerSettings(
            control_plane_url="http://control-plane.invalid",
            device_token="device-token",
            device_id="device-1",
            tenant_id="tenant-1",
            store_id="store-sunrise-vw",
            agent_id="agent-1",
        ),
        client=client,  # type: ignore[arg-type]
    )

    def execute(**_kwargs: Any) -> dict[str, Any]:
        if execution_fails:
            raise RuntimeError("raw details must not escape")
        return {"report": "complete"}

    monkeypatch.setattr(worker, "_execute_job", execute)
    result = worker.run_once()

    assert client.heartbeat_calls == 2
    assert len(client.completions) == 1
    assert client.completions[0]["lease"] == "lease-2"
    assert client.completions[0]["status"] == (
        "failed" if execution_fails else "succeeded"
    )
    assert result.status == ("failed" if execution_fails else "succeeded")


def test_worker_passes_current_claim_to_policy_and_model_client() -> None:
    client = RecordingClient()
    worker = AltasWorker(
        WorkerSettings(
            control_plane_url="http://control-plane.invalid",
            device_token="device-token",
            device_id="device-1",
            tenant_id="tenant-1",
            store_id="store-sunrise-vw",
            agent_id="agent-1",
        ),
        client=client,  # type: ignore[arg-type]
    )

    result = worker.run_once()

    expected = "claim-token-with-more-than-thirty-two-characters"
    assert result.status == "succeeded"
    assert client.policy_calls[0]["claim_token"] == expected
    assert client.chat_calls[0]["claim_token"] == expected
    assert client.completions[0]["claim_token"] == expected
