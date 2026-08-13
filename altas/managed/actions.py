"""Canonical managed-action vocabulary and approval policy."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Mapping


ACTION_SCHEMA_VERSION = "atlas.managed-action.v1"
ACTION_POLICY_VERSION = "atlas.managed-action-policy.v1"

ActionKind = Literal[
    "read",
    "navigate",
    "analyze",
    "draft",
    "download_export",
    "send_message",
    "submit",
    "mutate",
    "credential",
    "administrative",
]

ACTION_KINDS = frozenset({
    "read",
    "navigate",
    "analyze",
    "draft",
    "download_export",
    "send_message",
    "submit",
    "mutate",
    "credential",
    "administrative",
})
APPROVAL_REQUIRED_ACTION_KINDS = frozenset({
    "download_export",
    "send_message",
    "submit",
    "mutate",
    "credential",
    "administrative",
})

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$")


def _required_identifier(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(text):
        raise ValueError(f"{field_name} is invalid")
    return text


def _required_display(value: Any, field_name: str, *, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if not text or len(text) > limit or any(ord(char) < 32 for char in text):
        raise ValueError(f"{field_name} is invalid")
    return text


@dataclass(frozen=True, slots=True)
class ManagedAction:
    """A bounded human-display action with a mutation-detecting digest."""

    kind: ActionKind
    operation: str
    summary: str
    target_type: str
    target_id: str
    target_label: str
    schema_version: str = ACTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ACTION_SCHEMA_VERSION:
            raise ValueError("managed action schema version is unsupported")
        if self.kind not in ACTION_KINDS:
            raise ValueError("managed action kind is invalid")
        object.__setattr__(
            self,
            "operation",
            _required_identifier(self.operation, "operation"),
        )
        object.__setattr__(
            self,
            "summary",
            _required_display(self.summary, "summary", limit=240),
        )
        object.__setattr__(
            self,
            "target_type",
            _required_identifier(self.target_type, "target_type"),
        )
        object.__setattr__(
            self,
            "target_id",
            _required_identifier(self.target_id, "target_id"),
        )
        object.__setattr__(
            self,
            "target_label",
            _required_display(self.target_label, "target_label", limit=160),
        )

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ManagedAction":
        if set(value) != {
            "schema_version",
            "kind",
            "operation",
            "summary",
            "target_type",
            "target_id",
            "target_label",
        }:
            raise ValueError("managed action fields are invalid")
        return cls(
            schema_version=str(value["schema_version"]),
            kind=str(value["kind"]),  # type: ignore[arg-type]
            operation=str(value["operation"]),
            summary=str(value["summary"]),
            target_type=str(value["target_type"]),
            target_id=str(value["target_id"]),
            target_label=str(value["target_label"]),
        )

    def to_mapping(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "operation": self.operation,
            "summary": self.summary,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "target_label": self.target_label,
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_mapping(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def digest(self) -> str:
        return hashlib.sha256(
            ("atlas-managed-action-digest-v1\x00" + self.canonical_json()).encode(
                "utf-8"
            )
        ).hexdigest()

    @property
    def requires_approval(self) -> bool:
        return self.kind in APPROVAL_REQUIRED_ACTION_KINDS


_TOOL_ACTIONS: dict[str, tuple[ActionKind, str, str, str]] = {
    "run_daily_fixed_ops_report": (
        "analyze",
        "analyze",
        "store_report",
        "Analyze the daily fixed-operations report",
    ),
    "fixed_ops_daily_report": (
        "analyze",
        "analyze",
        "store_report",
        "Analyze the daily fixed-operations report",
    ),
    "pull_advisor_metrics": (
        "read",
        "read",
        "store_metrics",
        "Read advisor performance metrics",
    ),
    "pull_parts_summary": (
        "read",
        "read",
        "store_metrics",
        "Read the parts summary",
    ),
    "draft_service_manager_email": (
        "draft",
        "draft",
        "message_draft",
        "Draft a service manager email without sending it",
    ),
    "export_synthetic_fixed_ops_report": (
        "download_export",
        "export",
        "synthetic_report",
        "Export the synthetic fixed-operations report",
    ),
}


def classify_tool_action(
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    *,
    store_id: str,
) -> ManagedAction:
    """Project tool input into an allowlisted display object, never raw args."""

    name = _required_identifier(tool_name, "tool_name")
    definition = _TOOL_ACTIONS.get(name)
    if definition is None:
        raise ValueError("managed tool action is not classified")
    kind, operation, target_type, summary = definition
    target_id = store_id
    target_label = f"Store {store_id}"
    if name == "export_synthetic_fixed_ops_report":
        requested = (arguments or {}).get("report_id")
        target_id = _required_identifier(requested, "report_id")
        target_label = f"Synthetic report {target_id}"
    return ManagedAction(
        kind=kind,
        operation=operation,
        summary=summary,
        target_type=target_type,
        target_id=target_id,
        target_label=target_label,
    )
