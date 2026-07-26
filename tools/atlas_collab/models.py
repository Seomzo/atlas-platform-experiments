"""Versioned task and event contracts for development collaboration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import hashlib
import json
import re
from typing import Any
from uuid import uuid4

TASK_SCHEMA_VERSION = "atlas.collab.task.v1"
EVENT_SCHEMA_VERSION = "atlas.collab.event.v1"
AC_ID = re.compile(r"^AC-\d{2,}$")
TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,79}$")
EVENT_TYPES = frozenset({
    "TASK_CREATED",
    "CONTEXT_READY",
    "PLAN_PROPOSED",
    "PLAN_FEEDBACK",
    "PLAN_ACCEPTED",
    "WORK_CLAIMED",
    "STATUS_CHANGED",
    "QUESTION",
    "ANSWER",
    "CONTRACT_CHANGE_PROPOSED",
    "CONTRACT_CHANGE_ACCEPTED",
    "BLOCKED",
    "REPLAN_REQUESTED",
    "COMMIT_READY",
    "REVIEW_REQUESTED",
    "REVIEW_FINDING",
    "CHANGES_REQUESTED",
    "REVIEW_APPROVED",
    "INTEGRATION_STARTED",
    "TEST_EVIDENCE",
    "HUMAN_GATE_REQUIRED",
    "HANDOFF_READY",
    "TASK_COMPLETED",
    "TASK_CANCELED",
})
TERMINAL_EVENT_TYPES = frozenset({
    "ANSWER",
    "TEST_EVIDENCE",
    "HANDOFF_READY",
    "TASK_COMPLETED",
    "TASK_CANCELED",
})
ROLES = frozenset({
    "coordinator",
    "implementer",
    "reviewer",
    "integrator",
    "architecture-security",
})


class ContractError(ValueError):
    """A task or event contract is unsafe or incomplete."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _strings(value: Any, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ContractError(f"{name} must be a list of non-empty strings")
    return [item.strip() for item in value]


@dataclass(frozen=True)
class AcceptanceCriterion:
    id: str
    text: str
    required_evidence: str

    @classmethod
    def from_mapping(cls, raw: dict[str, Any], index: int) -> AcceptanceCriterion:
        identifier = str(raw.get("id") or f"AC-{index:02d}").strip()
        text = str(raw.get("text") or "").strip()
        evidence = str(raw.get("required_evidence") or "").strip()
        if not AC_ID.fullmatch(identifier):
            raise ContractError(f"invalid acceptance criterion id: {identifier!r}")
        if len(text) < 8:
            raise ContractError(f"{identifier} needs an observable requirement")
        if not evidence:
            raise ContractError(f"{identifier} needs required_evidence")
        return cls(identifier, text, evidence)


@dataclass(frozen=True)
class TaskContract:
    task_id: str
    workstream_id: str
    repository: str
    base_ref: str
    base_sha: str
    title: str
    goal: str
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    in_scope: tuple[str, ...] = ()
    out_of_scope: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    canonical_context: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    human_gates: tuple[str, ...] = ()
    requested_roles: tuple[str, ...] = ()
    schema_version: str = TASK_SCHEMA_VERSION

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> TaskContract:
        if raw.get("schema_version") != TASK_SCHEMA_VERSION:
            raise ContractError(f"schema_version must be {TASK_SCHEMA_VERSION}")
        task_id = str(raw.get("task_id") or "").strip()
        if not TASK_ID.fullmatch(task_id):
            raise ContractError("task_id is missing or invalid")
        criteria_raw = raw.get("acceptance_criteria")
        if not isinstance(criteria_raw, list) or not criteria_raw:
            raise ContractError(
                "at least one observable acceptance criterion is required"
            )
        criteria = tuple(
            AcceptanceCriterion.from_mapping(item, index)
            for index, item in enumerate(criteria_raw, start=1)
            if isinstance(item, dict)
        )
        if len(criteria) != len(criteria_raw):
            raise ContractError("acceptance_criteria entries must be mappings")
        identifiers = [criterion.id for criterion in criteria]
        if len(identifiers) != len(set(identifiers)):
            raise ContractError("acceptance criterion ids must be unique")
        roles = _strings(raw.get("requested_roles"), "requested_roles")
        unknown_roles = sorted(set(roles) - ROLES)
        if unknown_roles:
            raise ContractError(
                f"unsupported requested roles: {', '.join(unknown_roles)}"
            )
        required = {}
        for name in (
            "workstream_id",
            "repository",
            "base_ref",
            "base_sha",
            "title",
            "goal",
        ):
            required[name] = str(raw.get(name) or "").strip()
            if not required[name]:
                raise ContractError(f"{name} is required")
        return cls(
            task_id=task_id,
            acceptance_criteria=criteria,
            in_scope=tuple(_strings(raw.get("in_scope"), "in_scope")),
            out_of_scope=tuple(_strings(raw.get("out_of_scope"), "out_of_scope")),
            constraints=tuple(_strings(raw.get("constraints"), "constraints")),
            canonical_context=tuple(
                _strings(raw.get("canonical_context"), "canonical_context")
            ),
            dependencies=tuple(_strings(raw.get("dependencies"), "dependencies")),
            human_gates=tuple(_strings(raw.get("human_gates"), "human_gates")),
            requested_roles=tuple(roles),
            **required,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def digest(self) -> str:
        return content_hash(self.to_dict())


@dataclass(frozen=True)
class CollaborationEvent:
    task_id: str
    workstream_id: str
    actor_id: str
    actor_role: str
    event_type: str
    status: str
    summary: str
    base_sha: str
    branch: str = ""
    worktree_id: str = ""
    reply_to: str = ""
    causation_id: str = ""
    requested_from: tuple[str, ...] = ()
    intended_for: tuple[str, ...] = ()
    path_claims: tuple[str, ...] = ()
    interface_claims: tuple[str, ...] = ()
    dependency_ids: tuple[str, ...] = ()
    acceptance_criteria_ids: tuple[str, ...] = ()
    evidence_links: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    questions: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    next_action: str = ""
    hop_count: int = 0
    max_hops: int = 3
    event_id: str = field(default_factory=lambda: f"evt-{uuid4()}")
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    schema_version: str = EVENT_SCHEMA_VERSION

    def validate(self) -> None:
        if self.schema_version != EVENT_SCHEMA_VERSION:
            raise ContractError(f"schema_version must be {EVENT_SCHEMA_VERSION}")
        if self.event_type not in EVENT_TYPES:
            raise ContractError(f"unsupported event type: {self.event_type}")
        if self.actor_role not in ROLES:
            raise ContractError(f"unsupported actor role: {self.actor_role}")
        if not all((
            self.task_id,
            self.workstream_id,
            self.actor_id,
            self.status,
            self.summary,
        )):
            raise ContractError("event identity, status, and summary are required")
        if not 0 <= self.hop_count <= self.max_hops <= 10:
            raise ContractError("hop_count/max_hops are outside safe bounds")
        invalid = [
            item for item in self.acceptance_criteria_ids if not AC_ID.fullmatch(item)
        ]
        if invalid:
            raise ContractError(f"invalid acceptance criterion ids: {invalid}")
        if set(self.requested_from) & set(self.intended_for):
            raise ContractError("requester and intended recipient must be distinct")

    @property
    def can_trigger_turn(self) -> bool:
        return (
            self.event_type not in TERMINAL_EVENT_TYPES
            and self.actor_id not in self.intended_for
            and bool(self.intended_for)
            and self.hop_count < self.max_hops
        )

    @property
    def idempotency_key(self) -> str:
        identity = {
            "task_id": self.task_id,
            "event_id": self.event_id,
            "causation_id": self.causation_id,
            "event_type": self.event_type,
        }
        return content_hash(identity)

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return asdict(self)

    def render_buzz(self) -> str:
        payload = _canonical_json(self.to_dict())
        return (
            f"**{self.event_type} · {self.actor_role}**\n\n"
            f"{self.summary}\n\n"
            f"<!-- atlas-collab:{self.idempotency_key} -->\n"
            f"```json atlas-collab-event\n{payload}\n```"
        )
