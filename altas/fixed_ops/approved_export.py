"""Deterministic approval-gated export fixture; never writes customer data."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from altas.managed.actions import ManagedAction
from altas.managed.context import ManagedActionAuthorization, ManagedContext


SYNTHETIC_EXPORT_CAPABILITY = "fixed_ops.synthetic_export"
SYNTHETIC_EXPORT_WORKFLOW = "fixed_ops.synthetic_export.v1"


@dataclass(frozen=True, slots=True)
class SyntheticApprovedExport:
    """Exercise exact approval without creating a file or external side effect."""

    report_id: str

    @classmethod
    def from_job(cls, job: dict[str, Any]) -> "SyntheticApprovedExport":
        payload = job.get("payload")
        if not isinstance(payload, dict) or set(payload) != {
            "workflow",
            "report_id",
            "relay_session_id",
        }:
            raise ValueError("SyntheticExportPayloadInvalid")
        if payload.get("workflow") != SYNTHETIC_EXPORT_WORKFLOW:
            raise ValueError("SyntheticExportWorkflowInvalid")
        report_id = str(payload.get("report_id") or "").strip()
        if (
            not report_id
            or len(report_id) > 160
            or any(not (char.isalnum() or char in "._:/-") for char in report_id)
        ):
            raise ValueError("SyntheticExportReportInvalid")
        return cls(report_id=report_id)

    def action(self) -> ManagedAction:
        return ManagedAction(
            kind="download_export",
            operation="export",
            summary="Export the synthetic fixed-operations report",
            target_type="synthetic_report",
            target_id=self.report_id,
            target_label=f"Synthetic report {self.report_id}",
        )

    def execute(
        self,
        *,
        context: ManagedContext,
        authorization: ManagedActionAuthorization,
    ) -> dict[str, Any]:
        action = self.action()
        authorization.authorize(action=action, context=context)
        return {
            "status": "synthetic_export_authorized",
            "report_id": self.report_id,
            "store_id": context.store_id,
            "approval_id": authorization.approval_id,
            "action_digest": authorization.action_digest,
            "job_attempt": authorization.job_attempt,
            "artifact_created": False,
            "external_write": False,
        }
