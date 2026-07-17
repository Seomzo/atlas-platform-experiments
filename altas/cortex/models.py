"""Typed contracts shared by Atlas Cortex storage, retrieval, and UI."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal


EvidenceSource = Literal[
    "user_message",
    "assistant_message",
    "tool_call",
    "tool_result",
    "uploaded_file",
    "email",
    "meeting",
    "manual",
    "web_source",
    "correction",
    "system_event",
]

MemoryStatus = Literal[
    "active",
    "superseded",
    "disputed",
    "forgotten",
    "rewound",
    "deleted",
]
EpistemicStatus = Literal["reported", "inferred", "verified", "disputed"]


@dataclass(frozen=True)
class EvidenceInput:
    source_type: EvidenceSource
    content: str
    source_locator: str
    knowledge_space: str = "personal"
    actor_principal_id: str | None = None
    occurred_at: str | None = None
    sensitivity: str = "private"
    retention_class: str = "standard"
    parent_evidence_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RecallItem:
    id: str
    kind: str
    text: str
    knowledge_space: str
    source_label: str
    occurred_at: str | None
    evidence_locator: str | None
    status: str
    epistemic_status: str
    why_matched: str
    score: float
    evidence_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["evidence_ids"] = list(self.evidence_ids)
        return value


@dataclass(frozen=True)
class RecallResult:
    run_id: str
    query: str
    route: str
    items: tuple[RecallItem, ...]
    latency_ms: int
    degraded: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "query": self.query,
            "route": self.route,
            "items": [item.to_dict() for item in self.items],
            "latency_ms": self.latency_ms,
            "degraded": list(self.degraded),
        }


@dataclass(frozen=True)
class DreamReport:
    job_id: str
    status: str
    sessions_processed: int = 0
    observations_processed: int = 0
    promoted: int = 0
    merged: int = 0
    superseded: int = 0
    disputed: int = 0
    discarded: int = 0
    needs_review: int = 0
    entities_created: int = 0
    relations_created: int = 0
    failures: tuple[str, ...] = ()
    model: str = ""
    prompt_version: str = ""
    started_at: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["failures"] = list(self.failures)
        return value
