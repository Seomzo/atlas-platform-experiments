#!/usr/bin/env python3
"""Deterministic end-to-end smoke test for the Atlas walking skeleton."""

from __future__ import annotations

import json
import tempfile
import warnings
from pathlib import Path

warnings.filterwarnings(
    "ignore",
    message=r"Using `httpx` with `starlette\.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient

from altas.control_plane.app import create_app
from altas.control_plane.config import ControlPlaneSettings
from altas.control_plane.repository import (
    DEMO_AGENT_ID,
    DEMO_DEVICE_ID,
    DEMO_DEVICE_SECRET,
    DEMO_STORE_ID,
    DEMO_TENANT_ID,
    DEMO_UNENTITLED_STORE_ID,
)
from altas.fixed_ops import DailyFixedOpsReport


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="altas-smoke-") as directory:
        admin_token = "altas-smoke-admin"
        app = create_app(
            ControlPlaneSettings(
                database_path=Path(directory) / "control-plane.sqlite3",
                lease_signing_key=b"altas-smoke-signing-key-32-bytes-minimum",
                admin_token=admin_token,
                seed_demo_data=True,
                mock_model=True,
                lease_ttl_seconds=120,
            )
        )
        device = {"Authorization": f"Bearer {DEMO_DEVICE_SECRET}"}
        admin = {"Authorization": f"Bearer {admin_token}"}

        with TestClient(app) as client:
            heartbeat = client.post(
                "/api/v1/worker/heartbeat",
                headers=device,
                json={
                    "tenant_id": DEMO_TENANT_ID,
                    "store_id": DEMO_STORE_ID,
                    "agent_id": DEMO_AGENT_ID,
                    "worker_version": "0.1.0-smoke",
                    "health_status": "healthy",
                    "metadata": {"test": "smoke"},
                },
            )
            heartbeat.raise_for_status()
            lease = heartbeat.json()["lease"]["token"]
            scope = {
                **device,
                "X-Atlas-Lease": lease,
                "X-Atlas-Tenant-ID": DEMO_TENANT_ID,
                "X-Atlas-Store-ID": DEMO_STORE_ID,
                "X-Atlas-Agent-ID": DEMO_AGENT_ID,
            }

            next_job = client.get("/api/v1/worker/jobs/next", headers=scope)
            next_job.raise_for_status()
            job = next_job.json()["job"]
            assert job["capability"] == "fixed_ops.daily_report"
            job_scope = {
                **scope,
                "X-Atlas-Job-ID": job["id"],
                "X-Atlas-Claim-Token": job["claim_token"],
            }

            policy = client.post(
                "/api/v1/worker/policy/evaluate",
                headers=job_scope,
                json={
                    "store_id": DEMO_STORE_ID,
                    "agent_id": DEMO_AGENT_ID,
                    "capability": job["capability"],
                },
            )
            policy.raise_for_status()
            assert policy.json()["allowed"] is True

            denied = client.post(
                "/api/v1/worker/policy/evaluate",
                headers=job_scope,
                json={
                    "store_id": DEMO_UNENTITLED_STORE_ID,
                    "agent_id": DEMO_AGENT_ID,
                    "capability": job["capability"],
                },
            )
            denied.raise_for_status()
            assert denied.json()["allowed"] is False

            model = client.post(
                "/v1/chat/completions",
                headers=job_scope,
                json={
                    "model": "altas-fixed-ops",
                    "messages": [
                        {
                            "role": "user",
                            "content": "Summarize the supplied synthetic aggregates.",
                        }
                    ],
                    "stream": False,
                },
            )
            model.raise_for_status()
            mock_summary = model.json()["choices"][0]["message"]["content"]
            report = DailyFixedOpsReport(summarize=lambda _prompt: mock_summary).run(
                store_id=DEMO_STORE_ID,
                business_date=job["payload"]["report_date"],
            )

            complete = client.post(
                f"/api/v1/worker/jobs/{job['id']}/complete",
                headers=job_scope,
                json={
                    "tenant_id": DEMO_TENANT_ID,
                    "store_id": DEMO_STORE_ID,
                    "agent_id": DEMO_AGENT_ID,
                    "claim_token": job["claim_token"],
                    "status": "succeeded",
                    "result": report,
                    "error": None,
                },
            )
            complete.raise_for_status()

            overview = client.get("/api/v1/admin/overview", headers=admin)
            overview.raise_for_status()
            assert overview.json()["usage_tokens"] > 0
            assert overview.json()["running_jobs"] == 0

            disabled = client.post(
                f"/api/v1/admin/devices/{DEMO_DEVICE_ID}/toggle",
                headers=admin,
                json={"enabled": False, "reason": "smoke-test revocation"},
            )
            disabled.raise_for_status()
            revoked = client.post(
                "/api/v1/worker/heartbeat",
                headers=device,
                json={
                    "tenant_id": DEMO_TENANT_ID,
                    "store_id": DEMO_STORE_ID,
                    "agent_id": DEMO_AGENT_ID,
                    "worker_version": "0.1.0-smoke",
                    "health_status": "healthy",
                    "metadata": {},
                },
            )
            assert revoked.status_code == 403

        print(
            json.dumps(
                {
                    "status": "passed",
                    "device_id": DEMO_DEVICE_ID,
                    "job_id": job["id"],
                    "report_sales": report["kpis"]["total_sales"],
                    "unentitled_store_denied": True,
                    "device_revocation_verified": True,
                },
                indent=2,
                sort_keys=True,
            )
        )


if __name__ == "__main__":
    main()
