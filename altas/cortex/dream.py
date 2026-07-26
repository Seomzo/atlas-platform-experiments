"""Evidence-backed session-end consolidation for Atlas Cortex.

The model in this module is a bounded utility classifier.  It proposes a
versioned set of operations; deterministic code validates provenance,
authorization, temporal fields, and target records before any mutation.  The
model never receives permission to write the database directly.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from altas.control_plane.redaction import sanitize_for_storage

from .config import CortexConfig
from .models import DreamReport
from .store import CortexStore, new_id, stable_hash, utc_now


TRIAGE_SCHEMA_VERSION = "atlas.cortex.triage.v1"
TRIAGE_PROMPT_VERSION = "atlas.cortex.triage.prompt.v1"
REASONING_PROMPT_VERSION = "atlas.cortex.reasoning.prompt.v1"
RECONCILE_PROMPT_VERSION = "atlas.cortex.reconcile.v1"
CHECKPOINT_PROMPT_VERSION = "atlas.cortex.checkpoint.v1"
RECOVERY_PROMPT_VERSION = "atlas.cortex.recovery.v1"
COMMUNITY_ALGORITHM_VERSION = "cortex:connected-components:v1"

ACTIONS = frozenset({
    "defer_unresolved",
    "discard_transient",
    "retain_hot_until",
    "promote_new",
    "merge_existing",
    "supersede_existing",
    "mark_disputed",
    "needs_deeper_review",
})
FINAL_ACTIONS = ACTIONS - {"needs_deeper_review"}
MEMORY_KINDS = frozenset({
    "stable_fact",
    "preference",
    "decision",
    "commitment",
    "relationship",
    "workflow_signal",
    "correction",
    "pattern",
    "event",
})
ENTITY_TYPES = frozenset({
    "person",
    "organization",
    "dealership",
    "customer",
    "vehicle",
    "project",
    "workflow",
    "document",
    "location",
    "concept",
})
PERSONAL_RELATION_PREDICATES = frozenset({
    "applies_to",
    "co_mentioned_with",
    "committed_to",
    "decided",
    "derived_from",
    "explains",
    "knows",
    "located_at",
    "mentioned_in",
    "owns",
    "performed_step",
    "prefers",
    "produced_artifact",
    "related_to",
    "requires",
    "supersedes",
    "uses",
    "uses_skill",
    "works_at",
})
AUTHORITATIVE_SOURCE_TYPES = frozenset({
    "user_message",
    "correction",
    "manual",
})
ASSISTANT_SOURCE_TYPES = frozenset({"assistant_message"})
PROMOTION_ACTIONS = frozenset({
    "promote_new",
    "merge_existing",
    "supersede_existing",
    "mark_disputed",
})
TARGET_ACTIONS = frozenset({"merge_existing", "supersede_existing", "mark_disputed"})
SENSITIVITY_LEVELS = frozenset({"private", "sensitive", "restricted"})
SENSITIVITY_RANK = {"private": 0, "sensitive": 1, "restricted": 2}
RETENTION_CLASSES = frozenset({"standard", "short", "long"})

_MAX_CANDIDATES_PER_CALL = 20
_MAX_CANDIDATE_PROMPT_BYTES = 2_600
_MAX_PROMPT_MESSAGE_BYTES = 60 * 1024
_MAX_EVIDENCE_PER_CANDIDATE = 3
_MAX_EVIDENCE_CHARS = 1_500
_MAX_STATEMENT_CHARS = 8_000
_MAX_RATIONALE_CHARS = 1_000
_MANAGED_PROVIDERS = frozenset({"altas", "altas-gateway", "altas-managed"})
_MANAGED_REQUIRED_ENV = (
    "ATLAS_DEVICE_TOKEN",
    "ATLAS_LEASE_TOKEN",
    "ATLAS_TENANT_ID",
    "ATLAS_STORE_ID",
    "ATLAS_AGENT_ID",
    "ATLAS_JOB_ID",
    "ATLAS_CLAIM_TOKEN",
    "ATLAS_JOB_CAPABILITY",
    "ATLAS_CONTROL_PLANE_URL",
)
MANAGED_CORTEX_CAPABILITY = "cortex.memory_maintenance"


class CortexDreamError(RuntimeError):
    """Base class for bounded dream-cycle failures."""


class CortexRouteError(CortexDreamError):
    """No privacy-approved explicit model route is configured."""


class CortexRouteConflictError(CortexRouteError):
    """The Cortex utility route resolves to the conversational model."""


class CortexOutputError(CortexDreamError):
    """The model response failed strict schema or provenance validation."""


@dataclass(frozen=True)
class CortexModelRoute:
    task: str
    provider: str
    model: str
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False)
    api_mode: str | None = None
    timeout: float = 120.0
    max_tokens: int = 4_000
    managed_approved: bool = False


@dataclass
class UsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_micros: int = 0

    def add_response(self, response: Any) -> None:
        usage = _object_value(response, "usage", {})
        self.input_tokens += _usage_value(usage, "prompt_tokens", "input_tokens")
        self.output_tokens += _usage_value(usage, "completion_tokens", "output_tokens")
        self.cost_micros += _usage_value(usage, "cost_micros")


@dataclass(frozen=True)
class DreamCandidate:
    observation_id: str
    session_id: str
    knowledge_space: str
    kind: str
    text: str
    evidence: tuple[Mapping[str, Any], ...]
    authoritative_evidence_ids: frozenset[str]
    allowed_evidence_ids: frozenset[str]
    assistant_evidence_ids: frozenset[str]
    related_memories: tuple[Mapping[str, Any], ...]
    valid_from: str | None
    valid_until: str | None

    @property
    def allowed_target_memory_ids(self) -> frozenset[str]:
        return frozenset(str(item["id"]) for item in self.related_memories)

    @property
    def allowed_temporal_values(self) -> frozenset[str]:
        values = {self.valid_from, self.valid_until}
        values.update(str(item.get("occurred_at") or "") for item in self.evidence)
        return frozenset(value for value in values if value)

    def prompt_value(self) -> dict[str, Any]:
        safe_text = self.text
        if self.assistant_evidence_ids:
            # Observation text is a derived field and may have been assembled
            # by an older extractor. If any assistant evidence is attached,
            # rebuild the model-facing text solely from authoritative sources
            # so assistant prose cannot be laundered through that field.
            safe_text = "\n".join(
                str(item["content"])[:_MAX_EVIDENCE_CHARS]
                for item in self.evidence
                if str(item["id"]) in self.authoritative_evidence_ids
            )
        visible_evidence = [
            {
                "id": item["id"],
                "source_type": item["source_type"],
                "content": _truncate_utf8(str(item["content"]), _MAX_EVIDENCE_CHARS),
                "occurred_at": item["occurred_at"],
                "sensitivity": item["sensitivity"],
            }
            for item in self.evidence
            if item["source_type"] not in ASSISTANT_SOURCE_TYPES
        ]
        value = {
            "observation_id": self.observation_id,
            "session_id": _truncate_utf8(self.session_id, 200),
            "knowledge_space": self.knowledge_space,
            "kind": _truncate_utf8(self.kind, 100),
            "text": _truncate_utf8(safe_text, 750),
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "evidence": visible_evidence,
            "authoritative_evidence_ids": sorted(
                self.authoritative_evidence_ids.intersection(
                    str(item["id"]) for item in visible_evidence
                )
            ),
            "related_memories": [
                {
                    **dict(item),
                    "canonical_statement": _truncate_utf8(
                        str(item.get("canonical_statement") or ""), 300
                    ),
                }
                for item in self.related_memories[:2]
            ],
        }
        # Enforce the actual serialized byte budget, not a character estimate.
        # Shrink optional context first while retaining one authoritative source.
        while _utf8_size(_json(value)) > _MAX_CANDIDATE_PROMPT_BYTES:
            related = value["related_memories"]
            evidence = value["evidence"]
            if related:
                related.pop()
            elif len(evidence) > 1:
                evidence.pop()
                visible_ids = {str(item["id"]) for item in evidence}
                value["authoritative_evidence_ids"] = [
                    item
                    for item in value["authoritative_evidence_ids"]
                    if item in visible_ids
                ]
            elif evidence and _utf8_size(str(evidence[0]["content"])) > 256:
                current = _utf8_size(str(evidence[0]["content"]))
                evidence[0]["content"] = _truncate_utf8(
                    str(evidence[0]["content"]), max(256, current // 2)
                )
            elif _utf8_size(str(value["text"])) > 256:
                current = _utf8_size(str(value["text"]))
                value["text"] = _truncate_utf8(
                    str(value["text"]), max(256, current // 2)
                )
            else:
                raise CortexDreamError(
                    "Cortex candidate exceeds the safe prompt budget"
                )
        return value


@dataclass(frozen=True)
class TriageEntity:
    name: str
    entity_type: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class TriageRelation:
    subject_entity_index: int
    predicate: str
    object_entity_index: int


@dataclass(frozen=True)
class TriageOperation:
    observation_id: str
    action: str
    evidence_ids: tuple[str, ...]
    rationale: str
    memory_kind: str = "event"
    statement: str = ""
    target_memory_id: str | None = None
    valid_from: str | None = None
    valid_until: str | None = None
    sensitivity: str = "private"
    retention: str = "standard"
    missing_information: str = ""
    entities: tuple[TriageEntity, ...] = ()
    relations: tuple[TriageRelation, ...] = ()


@dataclass(frozen=True)
class DreamOutcome:
    report: DreamReport
    model_label: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    cost_micros: int


def _object_value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _usage_value(usage: Any, *names: str) -> int:
    for name in names:
        raw = _object_value(usage, name, None)
        if raw is None:
            continue
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        return max(0, value)
    return 0


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _job_session_ids(job_input: Mapping[str, Any]) -> tuple[str, ...]:
    values = job_input.get("session_ids", [])
    normalized = (
        [
            str(value).strip()
            for value in values
            if isinstance(values, list | tuple) and str(value).strip()
        ]
        if isinstance(values, list | tuple)
        else []
    )
    legacy = str(job_input.get("session_id") or "").strip()
    if legacy and legacy not in normalized:
        normalized.append(legacy)
    return tuple(dict.fromkeys(normalized))


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _bounded_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _raw_config(config: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if config is not None:
        return config
    from hermes_cli.config import load_config

    loaded = load_config()
    return loaded if isinstance(loaded, Mapping) else {}


def _canonical_provider_id(provider: str) -> str:
    """Match the provider identity used by the auxiliary transport."""

    from agent.auxiliary_client import _normalize_aux_provider

    return _normalize_aux_provider(provider)


def _canonical_model_id(model: str, provider: str) -> str:
    """Normalize an optional direct-provider prefix for route comparison."""

    normalized = str(model or "").strip().lower()
    if "/" not in normalized:
        return normalized
    prefix, remainder = normalized.split("/", 1)
    if _canonical_provider_id(prefix) == provider and remainder:
        return remainder
    return normalized


def cortex_model_routes_equal(
    left_provider: str,
    left_model: str,
    right_provider: str,
    right_model: str,
) -> bool:
    """Compare effective provider/model identities used by Cortex routing.

    Direct-provider prefixes are normalized and the managed Atlas provider
    aliases are treated as one gateway identity. Empty/automatic routes never
    compare equal to an explicit route.
    """
    left_provider_id = _canonical_provider_id(str(left_provider or "").strip())
    right_provider_id = _canonical_provider_id(str(right_provider or "").strip())
    if left_provider_id in {"", "auto", "main"} or right_provider_id in {
        "",
        "auto",
        "main",
    }:
        return False
    same_provider = left_provider_id == right_provider_id or {
        left_provider_id,
        right_provider_id,
    }.issubset(_MANAGED_PROVIDERS)
    if not same_provider:
        return False
    left_model_id = _canonical_model_id(str(left_model or ""), left_provider_id)
    right_model_id = _canonical_model_id(str(right_model or ""), right_provider_id)
    return bool(left_model_id and right_model_id and left_model_id == right_model_id)


def cortex_provider_is_approved(
    provider: str,
    approved_providers: Sequence[str] | None,
) -> bool:
    """Apply the same canonical provider allow-list semantics as runtime."""
    approved = {
        _canonical_provider_id(str(item).strip())
        for item in (approved_providers or ())
        if str(item).strip()
    }
    return (
        not approved or _canonical_provider_id(str(provider or "").strip()) in approved
    )


def cortex_provider_is_managed(provider: str) -> bool:
    """Return whether a provider id resolves through the Atlas model gateway."""
    return _canonical_provider_id(str(provider or "").strip()) in _MANAGED_PROVIDERS


def validate_cortex_model_selection(
    task: str,
    *,
    provider: str,
    model: str,
    main_provider: str,
    main_model: str,
    approved_providers: Sequence[str] | None = None,
) -> tuple[str, str]:
    """Validate the save/runtime invariants for one explicit Cortex route."""
    requested_provider = str(provider or "").strip().lower()
    requested_model = str(model or "").strip()
    if requested_provider in {"", "auto", "main"}:
        raise CortexRouteError(
            f"auxiliary.{task}.provider must name a dedicated explicit provider"
        )
    if not requested_model or requested_model.lower() == "auto":
        raise CortexRouteError(
            f"auxiliary.{task}.model must name a dedicated explicit model"
        )
    canonical_provider = _canonical_provider_id(requested_provider)
    if not canonical_provider or canonical_provider in {"auto", "main"}:
        raise CortexRouteError(
            f"auxiliary.{task} must resolve to an explicit provider and model"
        )
    if cortex_model_routes_equal(
        canonical_provider,
        requested_model,
        main_provider,
        main_model,
    ):
        raise CortexRouteConflictError(
            f"auxiliary.{task} must be separate from the conversational model"
        )
    if not cortex_provider_is_approved(canonical_provider, approved_providers):
        raise CortexRouteError(
            f"provider {canonical_provider!r} is not approved for Cortex personal evidence"
        )
    return canonical_provider, requested_model


def resolve_model_route(
    task: str,
    *,
    config: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> CortexModelRoute:
    """Resolve Cortex to one explicit utility provider/model without fallback.

    Cortex may reuse a local provider's endpoint and credential, but never the
    conversational route selection itself: ``auto``/``main``, a blank model,
    or the exact same provider+model as chat fail closed. Managed mode
    additionally requires the Atlas gateway and complete request-scoped job
    authorization.
    """
    if task not in {"cortex_triage", "cortex_reasoning"}:
        raise CortexRouteError(f"unsupported Cortex auxiliary task: {task}")
    raw = _raw_config(config)
    env = environ if environ is not None else os.environ
    auxiliary = _mapping(raw.get("auxiliary"))
    task_config = _mapping(auxiliary.get(task))
    main = _mapping(raw.get("model"))
    security = _mapping(_mapping(raw.get("cortex")).get("security"))
    approved_values = security.get("approved_model_providers", [])
    if not isinstance(approved_values, list | tuple | set):
        approved_values = []

    raw_main_provider = str(main.get("provider") or "").strip().lower()
    main_provider = _canonical_provider_id(raw_main_provider)
    inherited_model = str(main.get("default") or main.get("model") or "").strip()
    requested_provider_id = _canonical_provider_id(
        str(task_config.get("provider") or "").strip()
    )
    managed = (
        _truthy(env.get("ATLAS_MANAGED_MODE"))
        or main_provider in _MANAGED_PROVIDERS
        or requested_provider_id in _MANAGED_PROVIDERS
    )
    provider, model = validate_cortex_model_selection(
        task,
        provider=str(task_config.get("provider") or ""),
        model=str(task_config.get("model") or ""),
        main_provider=main_provider,
        main_model=inherited_model,
        approved_providers=() if managed else approved_values,
    )
    inherit_main_route = provider == main_provider or {
        provider,
        main_provider,
    }.issubset(_MANAGED_PROVIDERS)

    managed_approved = False
    managed_base_url: str | None = None
    if managed:
        if provider not in _MANAGED_PROVIDERS:
            raise CortexRouteError(
                "managed Cortex memory may only use the Atlas-approved model gateway"
            )
        missing = [
            name
            for name in _MANAGED_REQUIRED_ENV
            if not str(env.get(name) or "").strip()
        ]
        if missing:
            raise CortexRouteError(
                "managed Cortex model route lacks request-scoped Atlas authorization"
            )
        if str(env.get("ATLAS_JOB_CAPABILITY") or "").strip() != (
            MANAGED_CORTEX_CAPABILITY
        ):
            raise CortexRouteError(
                "managed Cortex model route requires a dedicated memory-maintenance job"
            )
        if any(
            str(value or "").strip()
            for value in (
                task_config.get("base_url"),
                task_config.get("api_key"),
                main.get("base_url"),
                main.get("api_key"),
            )
        ):
            raise CortexRouteError(
                "managed Cortex routes cannot override the Atlas gateway endpoint or credential"
            )
        control_plane_url = str(env.get("ATLAS_CONTROL_PLANE_URL") or "").strip()
        from urllib.parse import urlsplit

        parsed_control_plane = urlsplit(control_plane_url)
        if (
            parsed_control_plane.scheme not in {"http", "https"}
            or not parsed_control_plane.netloc
            or parsed_control_plane.username is not None
            or parsed_control_plane.password is not None
            or parsed_control_plane.query
            or parsed_control_plane.fragment
        ):
            raise CortexRouteError(
                "managed Cortex route has an invalid control-plane endpoint"
            )
        managed_base_url = f"{control_plane_url.rstrip('/')}/v1"
        managed_approved = True

    return CortexModelRoute(
        task=task,
        provider=provider,
        model=model,
        base_url=(
            managed_base_url
            or str(task_config.get("base_url") or "").strip()
            or (str(main.get("base_url") or "").strip() if inherit_main_route else "")
            or None
        ),
        api_key=(
            str(task_config.get("api_key") or "").strip()
            or (str(main.get("api_key") or "").strip() if inherit_main_route else "")
            or None
        ),
        api_mode=(
            str(task_config.get("api_mode") or "").strip()
            or (str(main.get("api_mode") or "").strip() if inherit_main_route else "")
            or None
        ),
        timeout=_bounded_float(
            task_config.get("timeout"),
            240.0 if task == "cortex_reasoning" else 120.0,
            10.0,
            900.0,
        ),
        max_tokens=_bounded_int(task_config.get("max_tokens"), 4_000, 256, 16_000),
        managed_approved=managed_approved,
    )


def _iso_or_none(value: Any, *, field_name: str) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 64:
        raise CortexOutputError(f"{field_name} must be an ISO-8601 string or null")
    candidate = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise CortexOutputError(f"{field_name} is not valid ISO-8601") from exc
    if parsed.tzinfo is None:
        raise CortexOutputError(f"{field_name} must include a timezone")
    return value


def _required_string(
    value: Any, field_name: str, *, maximum: int, allow_empty: bool = False
) -> str:
    if value is None and allow_empty:
        # Cheap models emit null where the contract wants "" — identical
        # meaning for an optional field, so normalize instead of failing.
        value = ""
    if not isinstance(value, str):
        raise CortexOutputError(f"{field_name} must be a string")
    result = " ".join(value.split()).strip()
    if not result and not allow_empty:
        raise CortexOutputError(f"{field_name} is required")
    if len(result) > maximum:
        raise CortexOutputError(f"{field_name} exceeds its size limit")
    return result


def _validate_entities(value: Any) -> tuple[TriageEntity, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 12:
        raise CortexOutputError("entities must be an array of at most 12 items")
    result: list[TriageEntity] = []
    seen: set[tuple[str, str]] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"name", "type", "aliases"}:
            raise CortexOutputError("entity has an invalid shape")
        name = _required_string(item.get("name"), "entity.name", maximum=500)
        raw_type = item.get("type")
        if (
            isinstance(raw_type, list)
            and len(raw_type) == 1
            and isinstance(raw_type[0], str)
        ):
            # The prompt contract lists the allowed type values; cheap models
            # copy that list shape for a single value. Unwrapping is lossless.
            raw_type = raw_type[0]
        entity_type = _required_string(raw_type, "entity.type", maximum=100).lower()
        if entity_type not in ENTITY_TYPES:
            raise CortexOutputError("entity.type is not in the Cortex vocabulary")
        aliases_value = item.get("aliases", [])
        if not isinstance(aliases_value, list) or len(aliases_value) > 20:
            raise CortexOutputError("entity.aliases must be a bounded array")
        aliases = tuple(
            _required_string(alias, "entity.alias", maximum=500)
            for alias in aliases_value
        )
        identity = (entity_type, " ".join(name.lower().split()))
        if identity in seen:
            raise CortexOutputError("entities must not contain duplicate identities")
        seen.add(identity)
        result.append(TriageEntity(name, entity_type, aliases))
    return tuple(result)


def _validate_relations(
    value: Any, entities: Sequence[TriageEntity]
) -> tuple[TriageRelation, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or len(value) > 24:
        raise CortexOutputError("relations must be an array of at most 24 items")
    result: list[TriageRelation] = []
    seen: set[tuple[int, str, int]] = set()
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {
            "subject_entity_index",
            "predicate",
            "object_entity_index",
        }:
            raise CortexOutputError("relation has an invalid shape")
        subject = item.get("subject_entity_index")
        object_ = item.get("object_entity_index")
        if type(subject) is not int or type(object_) is not int:
            raise CortexOutputError("relation endpoints must be integer entity indexes")
        if not (0 <= subject < len(entities)) or not (0 <= object_ < len(entities)):
            raise CortexOutputError(
                "relation endpoint is outside the operation entities"
            )
        if subject == object_:
            raise CortexOutputError("relation endpoints must be distinct")
        predicate = _required_string(
            item.get("predicate"), "relation.predicate", maximum=100
        ).lower()
        if predicate not in PERSONAL_RELATION_PREDICATES:
            raise CortexOutputError(
                "relation predicate is not in the Cortex vocabulary"
            )
        identity = (subject, predicate, object_)
        if identity in seen:
            raise CortexOutputError("relations must not contain duplicate edges")
        seen.add(identity)
        result.append(TriageRelation(subject, predicate, object_))
    return tuple(result)


def validate_triage_output(
    text: str,
    candidates: Sequence[DreamCandidate],
    *,
    allowed_actions: frozenset[str] = ACTIONS,
) -> tuple[TriageOperation, ...]:
    """Parse one exact JSON document and enforce the executable contract."""
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise CortexOutputError("model output is not one exact JSON document") from exc
    if isinstance(payload, list):
        # Cheap triage models routinely emit the operations array without the
        # wrapper object. Wrapping it is deterministic and lossless: every
        # operation below still passes the full executable contract.
        payload = {"schema_version": TRIAGE_SCHEMA_VERSION, "operations": payload}
    elif isinstance(payload, Mapping) and set(payload) == {"operations"}:
        # Same failure family: the envelope with schema_version omitted.
        payload = {
            "schema_version": TRIAGE_SCHEMA_VERSION,
            "operations": payload["operations"],
        }
    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version",
        "operations",
    }:
        raise CortexOutputError("model output has an invalid top-level shape")
    if payload.get("schema_version") != TRIAGE_SCHEMA_VERSION:
        raise CortexOutputError("model output schema version is not supported")
    raw_operations = payload.get("operations")
    if not isinstance(raw_operations, list) or len(raw_operations) != len(candidates):
        raise CortexOutputError("model must return exactly one operation per candidate")

    candidate_map = {candidate.observation_id: candidate for candidate in candidates}
    if len(candidate_map) != len(candidates):
        raise CortexOutputError("candidate observation IDs are not unique")
    seen: set[str] = set()
    result: list[TriageOperation] = []
    required_keys = {
        "observation_id",
        "action",
        "memory_kind",
        "statement",
        "evidence_ids",
        "target_memory_id",
        "entities",
        "valid_from",
        "valid_until",
        "rationale",
        "sensitivity",
        "retention",
        "missing_information",
    }
    optional_keys = {"relations"}
    for raw in raw_operations:
        if (
            not isinstance(raw, Mapping)
            or not required_keys.issubset(raw)
            or not set(raw).issubset(required_keys | optional_keys)
        ):
            raise CortexOutputError("triage operation has an invalid shape")
        observation_id = _required_string(
            raw.get("observation_id"), "observation_id", maximum=200
        )
        candidate = candidate_map.get(observation_id)
        if candidate is None or observation_id in seen:
            raise CortexOutputError(
                "operation references an unknown or duplicate observation"
            )
        seen.add(observation_id)
        action = _required_string(raw.get("action"), "action", maximum=100)
        if action not in allowed_actions:
            raise CortexOutputError(f"unsupported triage action: {action}")
        evidence_value = raw.get("evidence_ids")
        if not isinstance(evidence_value, list) or not evidence_value:
            raise CortexOutputError("every operation must cite evidence IDs")
        evidence_ids = tuple(
            _required_string(item, "evidence_id", maximum=200)
            for item in evidence_value
        )
        if len(evidence_ids) != len(set(evidence_ids)):
            raise CortexOutputError("operation contains duplicate evidence IDs")
        if not set(evidence_ids).issubset(candidate.allowed_evidence_ids):
            raise CortexOutputError("operation cites evidence outside its candidate")
        if set(evidence_ids).intersection(candidate.assistant_evidence_ids):
            raise CortexOutputError("assistant output cannot support a durable memory")
        if action in PROMOTION_ACTIONS and not set(evidence_ids).intersection(
            candidate.authoritative_evidence_ids
        ):
            raise CortexOutputError(
                "durable memory requires customer-authoritative source evidence"
            )

        statement = _required_string(
            raw.get("statement", ""),
            "statement",
            maximum=_MAX_STATEMENT_CHARS,
            allow_empty=action not in PROMOTION_ACTIONS,
        )
        kind = _required_string(
            raw.get("memory_kind", "event"), "memory_kind", maximum=100
        ).lower()
        if kind not in MEMORY_KINDS:
            raise CortexOutputError("memory_kind is not in the Cortex vocabulary")
        target = raw.get("target_memory_id")
        if target in (None, ""):
            target_id = None
        else:
            target_id = _required_string(target, "target_memory_id", maximum=200)
        if action in TARGET_ACTIONS:
            if not target_id or target_id not in candidate.allowed_target_memory_ids:
                raise CortexOutputError(
                    "operation target is not an authorized related memory"
                )
        elif target_id is not None:
            raise CortexOutputError("this action must not target an existing memory")
        valid_from = _iso_or_none(raw.get("valid_from"), field_name="valid_from")
        valid_until = _iso_or_none(raw.get("valid_until"), field_name="valid_until")
        if valid_from and valid_from not in candidate.allowed_temporal_values:
            raise CortexOutputError("valid_from is not grounded in candidate evidence")
        if (
            valid_until
            and action != "retain_hot_until"
            and valid_until not in candidate.allowed_temporal_values
        ):
            raise CortexOutputError("valid_until is not grounded in candidate evidence")
        if valid_from and valid_until:
            start = datetime.fromisoformat(valid_from.replace("Z", "+00:00"))
            end = datetime.fromisoformat(valid_until.replace("Z", "+00:00"))
            if end < start:
                raise CortexOutputError("valid_until precedes valid_from")
        if action == "retain_hot_until" and not valid_until:
            raise CortexOutputError("retain_hot_until requires valid_until")

        sensitivity = _required_string(
            raw.get("sensitivity"), "sensitivity", maximum=100
        ).lower()
        if sensitivity not in SENSITIVITY_LEVELS:
            raise CortexOutputError("sensitivity is not in the Cortex vocabulary")
        cited = {
            str(item["id"]): item
            for item in candidate.evidence
            if str(item["id"]) in evidence_ids
        }
        source_sensitivity = max(
            (
                "restricted"
                if str(item.get("sensitivity") or "").lower() == "confidential"
                else str(item.get("sensitivity") or "private").lower()
                for item in cited.values()
            ),
            key=lambda value: SENSITIVITY_RANK.get(value, 0),
            default="private",
        )
        if SENSITIVITY_RANK[sensitivity] < SENSITIVITY_RANK.get(source_sensitivity, 0):
            raise CortexOutputError(
                "operation sensitivity cannot downgrade its source evidence"
            )
        retention = _required_string(
            raw.get("retention"), "retention", maximum=100
        ).lower()
        if retention not in RETENTION_CLASSES:
            raise CortexOutputError("retention is not in the Cortex vocabulary")

        entities = _validate_entities(raw.get("entities", []))
        relations = _validate_relations(raw.get("relations", []), entities)
        if relations and action not in PROMOTION_ACTIONS:
            raise CortexOutputError(
                "durable relations require a durable-memory promotion action"
            )
        result.append(
            TriageOperation(
                observation_id=observation_id,
                action=action,
                evidence_ids=evidence_ids,
                rationale=_required_string(
                    raw.get("rationale"), "rationale", maximum=_MAX_RATIONALE_CHARS
                ),
                memory_kind=kind,
                statement=statement,
                target_memory_id=target_id,
                valid_from=valid_from,
                valid_until=valid_until,
                sensitivity=sensitivity,
                retention=retention,
                missing_information=_required_string(
                    raw.get("missing_information", ""),
                    "missing_information",
                    maximum=1_000,
                    allow_empty=True,
                ),
                entities=entities,
                relations=relations,
            )
        )
    if seen != set(candidate_map):
        raise CortexOutputError("model omitted one or more candidate observations")
    return tuple(result)


class DreamProcessor:
    """Run deterministic preparation, LLM triage, and validated operations."""

    def __init__(
        self,
        store: CortexStore,
        config: CortexConfig,
        *,
        raw_config: Mapping[str, Any] | None = None,
        environ: Mapping[str, str] | None = None,
        llm_call: Callable[..., Any] | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self.raw_config = raw_config
        self.environ = environ
        self._llm_call = llm_call

    def process(self, job: Mapping[str, Any], *, owner: str) -> DreamOutcome:
        job_type = str(job.get("job_type") or "")
        if job_type == "session_reconcile":
            return self._process_session_reconcile(job, owner=owner)
        if job_type == "session_checkpoint":
            return self._process_session_checkpoint(job, owner=owner)
        if job_type in {"precompress_distill", "dream", "dream_cycle"}:
            return self._process_recovery_checkpoint(job, owner=owner)
        if job_type != "session_distill":
            raise CortexDreamError(
                f"unsupported Cortex semantic job type: {job_type or '<empty>'}"
            )

        # The mutable session row is deliberately not consulted here: a true
        # boundary remains valid if the customer resumes before this async
        # worker runs. The immutable admission and parent/root chain are the
        # sole authority, and must be verified before even local preparation,
        # route resolution, or a model call.
        self.store.require_session_distill_admission(job)

        job_id = str(job["id"])
        started_at = utc_now()
        self._checkpoint(job_id, owner, "inventory", {"job_type": job["job_type"]})
        self._materialize_missing_observations(job)
        candidates, deterministic = self._load_candidates(job)
        usage = UsageTotals()
        operations: list[TriageOperation] = list(deterministic)
        model_labels: list[str] = []

        model_candidates = [
            candidate
            for candidate in candidates
            if candidate.authoritative_evidence_ids
        ]
        for batch in self._prompt_batches(
            model_candidates, allowed_actions=ACTIONS, review=False
        ):
            route = resolve_model_route(
                "cortex_triage", config=self.raw_config, environ=self.environ
            )
            batch_operations, model_label = self._call_and_validate(
                route,
                batch,
                usage,
                allowed_actions=ACTIONS,
                review=False,
            )
            model_labels.append(model_label)
            operations.extend(batch_operations)
            self._heartbeat(job_id, owner)

        escalated_ids = {
            operation.observation_id
            for operation in operations
            if operation.action == "needs_deeper_review"
        }
        if escalated_ids:
            operations = [
                operation
                for operation in operations
                if operation.observation_id not in escalated_ids
            ]
            escalated = [
                candidate
                for candidate in candidates
                if candidate.observation_id in escalated_ids
            ]
            for batch in self._prompt_batches(
                escalated, allowed_actions=FINAL_ACTIONS, review=True
            ):
                route = resolve_model_route(
                    "cortex_reasoning", config=self.raw_config, environ=self.environ
                )
                reviewed, model_label = self._call_and_validate(
                    route,
                    batch,
                    usage,
                    allowed_actions=FINAL_ACTIONS,
                    review=True,
                )
                model_labels.append(model_label)
                operations.extend(reviewed)
                self._heartbeat(job_id, owner)

        self._checkpoint(
            job_id,
            owner,
            "validated",
            {"candidates": len(candidates), "operations": len(operations)},
        )
        counts = {
            "promoted": 0,
            "merged": 0,
            "superseded": 0,
            "disputed": 0,
            "discarded": 0,
            "needs_review": 0,
            "entities_created": 0,
            "relations_created": 0,
        }
        for operation in operations:
            result = self._apply_operation(job_id, operation, owner=owner)
            for key, value in result.items():
                if key in counts:
                    counts[key] += int(value)

        continuation_job_id = self._enqueue_session_continuation(job, owner=owner)

        completed_at = utc_now()
        report = DreamReport(
            job_id=job_id,
            status="succeeded",
            sessions_processed=len(_job_session_ids(_mapping(job.get("input")))),
            observations_processed=len(operations),
            promoted=counts["promoted"],
            merged=counts["merged"],
            superseded=counts["superseded"],
            disputed=counts["disputed"],
            discarded=counts["discarded"],
            needs_review=counts["needs_review"],
            entities_created=counts["entities_created"],
            relations_created=counts["relations_created"],
            model=",".join(dict.fromkeys(model_labels)),
            prompt_version=TRIAGE_PROMPT_VERSION,
            started_at=started_at,
            completed_at=completed_at,
        )
        self._record_health(job_id, "succeeded", report.to_dict())
        report_value = report.to_dict()
        if continuation_job_id:
            report_value["continuation_job_id"] = continuation_job_id
        self._checkpoint(job_id, owner, "complete", report_value)
        return DreamOutcome(
            report=report,
            model_label=report.model,
            prompt_version=TRIAGE_PROMPT_VERSION,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_micros=usage.cost_micros,
        )

    def _enqueue_session_continuation(
        self, job: Mapping[str, Any], *, owner: str
    ) -> str | None:
        if str(job.get("job_type") or "") != "session_distill":
            return None
        job_input = _mapping(job.get("input"))
        session_ids = _job_session_ids(job_input)
        if not session_ids:
            return None
        remaining = self.store.pending_observations(
            limit=1_000,
            session_ids=session_ids,
            evidence_ids=tuple(
                str(value) for value in job_input.get("evidence_ids", [])
            ),
            knowledge_space="personal",
        )
        if not remaining:
            return None
        remaining_signature = [
            f"{item['id']}:{item.get('triage_attempts', 0)}:"
            f"{item.get('next_triage_at') or ''}"
            for item in remaining
        ]
        return self.store.enqueue_session_distill_continuation(
            str(job.get("id") or ""),
            remaining_signature=remaining_signature,
            owner=owner,
        )

    def _process_session_reconcile(
        self, job: Mapping[str, Any], *, owner: str
    ) -> DreamOutcome:
        """Replay a rewind deterministically without invoking a model.

        The foreground provider normally applies the reconciliation before it
        enqueues this job. Replaying it here makes a detached gateway rewind or
        an interrupted foreground write converge to the same durable state.
        """
        job_id = str(job["id"])
        started_at = utc_now()
        job_input = _mapping(job.get("input"))
        session_id = str(job_input.get("session_id") or "").strip()
        row_ids = [
            value for value in job_input.get("source_row_ids", []) if str(value).strip()
        ]
        if not session_id:
            raise CortexDreamError("session reconciliation requires a session id")
        self._checkpoint(
            job_id,
            owner,
            "reconcile",
            {"session_id": session_id, "source_row_count": len(row_ids)},
        )
        reconciled = self.store.reconcile_rewind(session_id, row_ids)
        completed_at = utc_now()
        report = DreamReport(
            job_id=job_id,
            status="succeeded",
            sessions_processed=1,
            prompt_version=RECONCILE_PROMPT_VERSION,
            started_at=started_at,
            completed_at=completed_at,
        )
        report_value = report.to_dict()
        report_value["reconciled_evidence_count"] = reconciled
        self._record_health(job_id, "succeeded", report_value)
        self._checkpoint(job_id, owner, "complete", report_value)
        return DreamOutcome(
            report=report,
            model_label="",
            prompt_version=RECONCILE_PROMPT_VERSION,
            input_tokens=0,
            output_tokens=0,
            cost_micros=0,
        )

    def _process_session_checkpoint(
        self, job: Mapping[str, Any], *, owner: str
    ) -> DreamOutcome:
        """Acknowledge a compatibility checkpoint without semantic promotion.

        ``on_session_end`` is also used by soft runtime/cache boundaries. Its
        evidence is durable, but only explicit logical-session finalization
        may trigger model-backed consolidation.
        """
        job_id = str(job["id"])
        started_at = utc_now()
        job_input = _mapping(job.get("input"))
        session_id = str(job_input.get("session_id") or "").strip()
        if not session_id:
            raise CortexDreamError("session checkpoint requires a session id")
        self._checkpoint(
            job_id,
            owner,
            "checkpoint",
            {"session_id": session_id, "model_processing": False},
        )
        report = DreamReport(
            job_id=job_id,
            status="succeeded",
            sessions_processed=1,
            prompt_version=CHECKPOINT_PROMPT_VERSION,
            started_at=started_at,
            completed_at=utc_now(),
        )
        report_value = report.to_dict()
        self._record_health(job_id, "succeeded", report_value)
        self._checkpoint(job_id, owner, "complete", report_value)
        return DreamOutcome(
            report=report,
            model_label="",
            prompt_version=CHECKPOINT_PROMPT_VERSION,
            input_tokens=0,
            output_tokens=0,
            cost_micros=0,
        )

    def _process_recovery_checkpoint(
        self, job: Mapping[str, Any], *, owner: str
    ) -> DreamOutcome:
        """Run deterministic recovery maintenance without invoking a model.

        Semantic consolidation is exclusively lineage-scoped
        ``session_distill`` work. Legacy pre-compress/dream rows and the daily
        recovery marker remain deterministic so an upgrade cannot process an
        active conversation unexpectedly. Dream markers may rebuild the
        derived entity-community projection because that operation only groups
        already-persisted graph structure; it does not create customer truth.
        """

        job_id = str(job["id"])
        started_at = utc_now()
        job_input = _mapping(job.get("input"))
        session_ids = _job_session_ids(job_input)
        community_count = 0
        if str(job.get("job_type") or "") in {"dream", "dream_cycle"}:
            community_count = self._rebuild_communities()
        self._checkpoint(
            job_id,
            owner,
            "recovery-checkpoint",
            {
                "legacy_job_type": str(job.get("job_type") or ""),
                "session_ids": list(session_ids),
                "model_processing": False,
                "communities_built": community_count,
            },
        )
        report = DreamReport(
            job_id=job_id,
            status="succeeded",
            sessions_processed=len(session_ids),
            prompt_version=RECOVERY_PROMPT_VERSION,
            started_at=started_at,
            completed_at=utc_now(),
        )
        report_value = report.to_dict()
        report_value["communities_built"] = community_count
        self._record_health(job_id, "succeeded", report_value)
        self._checkpoint(job_id, owner, "complete", report_value)
        return DreamOutcome(
            report=report,
            model_label="",
            prompt_version=RECOVERY_PROMPT_VERSION,
            input_tokens=0,
            output_tokens=0,
            cost_micros=0,
        )

    def _rebuild_communities(self) -> int:
        """Replace derived communities with connected entity components.

        Relations are directed facts, but community membership is structural,
        so connectivity is deliberately undirected. Isolated entities form
        singleton components and remain visible as part of the brain. Spaces
        governed by a published GraphRAG artifact keep their supplied
        communities instead of receiving a duplicate local grouping.
        """
        generated_at = utc_now()
        with self.store.transaction() as connection:
            entity_rows = connection.execute(
                "SELECT e.id, e.knowledge_space_id, e.canonical_name "
                "FROM entities e JOIN knowledge_spaces k "
                "ON k.id=e.knowledge_space_id AND k.brain_id=e.brain_id "
                "WHERE e.brain_id=? AND e.deleted_at IS NULL "
                "AND k.deleted_at IS NULL ORDER BY e.knowledge_space_id, e.id",
                (self.store.brain_id,),
            ).fetchall()
            relation_rows = connection.execute(
                "SELECT r.knowledge_space_id, r.subject_entity_id, "
                "r.object_entity_id FROM relations r "
                "JOIN entities s ON s.id=r.subject_entity_id "
                "AND s.brain_id=r.brain_id AND s.deleted_at IS NULL "
                "JOIN entities o ON o.id=r.object_entity_id "
                "AND o.brain_id=r.brain_id AND o.deleted_at IS NULL "
                "WHERE r.brain_id=? AND r.status='active' "
                "AND s.knowledge_space_id=r.knowledge_space_id "
                "AND o.knowledge_space_id=r.knowledge_space_id "
                "ORDER BY r.knowledge_space_id, r.id",
                (self.store.brain_id,),
            ).fetchall()
            graphrag_spaces = {
                str(row["knowledge_space_id"])
                for row in connection.execute(
                    "SELECT DISTINCT knowledge_space_id FROM communities "
                    "WHERE brain_id=? AND stale=0 "
                    "AND algorithm_version LIKE 'graphrag:%'",
                    (self.store.brain_id,),
                ).fetchall()
            }

            names: dict[str, str] = {}
            spaces: dict[str, list[str]] = {}
            for row in entity_rows:
                entity_id = str(row["id"])
                space_id = str(row["knowledge_space_id"])
                names[entity_id] = str(row["canonical_name"])
                spaces.setdefault(space_id, []).append(entity_id)

            adjacency = {entity_id: set() for entity_id in names}
            for row in relation_rows:
                source = str(row["subject_entity_id"])
                target = str(row["object_entity_id"])
                if source in adjacency and target in adjacency:
                    adjacency[source].add(target)
                    adjacency[target].add(source)

            connection.execute(
                "UPDATE communities SET parent_id=NULL WHERE brain_id=? "
                "AND algorithm_version=?",
                (self.store.brain_id, COMMUNITY_ALGORITHM_VERSION),
            )
            connection.execute(
                "DELETE FROM communities WHERE brain_id=? AND algorithm_version=?",
                (self.store.brain_id, COMMUNITY_ALGORITHM_VERSION),
            )

            communities: list[tuple[str, str, tuple[str, ...]]] = []
            for space_id in sorted(spaces):
                if space_id in graphrag_spaces:
                    continue
                unseen = set(spaces[space_id])
                while unseen:
                    pending = [min(unseen)]
                    members: set[str] = set()
                    while pending:
                        entity_id = pending.pop()
                        if entity_id not in unseen:
                            continue
                        unseen.remove(entity_id)
                        members.add(entity_id)
                        pending.extend(
                            sorted(adjacency[entity_id] & unseen, reverse=True)
                        )
                    communities.append((space_id, min(members), tuple(sorted(members))))

            communities.sort(key=lambda item: (item[0], item[1], item[2]))
            for space_id, _first_member, member_ids in communities:
                member_names = sorted(names[member_id] for member_id in member_ids)
                if len(member_names) == 1:
                    label = member_names[0]
                elif len(member_names) == 2:
                    label = f"{member_names[0]} + {member_names[1]}"
                else:
                    label = f"{member_names[0]} + {len(member_names) - 1} more"
                report_names = ", ".join(member_names[:12])
                if len(member_names) > 12:
                    report_names += f", and {len(member_names) - 12} more"
                community_id = (
                    "community_"
                    + stable_hash(
                        self.store.brain_id,
                        COMMUNITY_ALGORITHM_VERSION,
                        space_id,
                        *member_ids,
                    )[:32]
                )
                connection.execute(
                    "INSERT INTO communities(id, brain_id, knowledge_space_id, level, "
                    "label, report, algorithm_version, member_ids_json, stale, generated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        community_id,
                        self.store.brain_id,
                        space_id,
                        0,
                        label,
                        f"Connected group of {len(member_ids)} entities: {report_names}",
                        COMMUNITY_ALGORITHM_VERSION,
                        _json(member_ids),
                        0,
                        generated_at,
                    ),
                )
        return len(communities)

    def record_failure(self, job_id: str, error: BaseException) -> None:
        self._record_health(
            job_id,
            "failed",
            {
                "status": "failed",
                "error_type": type(error).__name__,
                "at": utc_now(),
            },
        )

    def _materialize_missing_observations(self, job: Mapping[str, Any]) -> None:
        job_input = _mapping(job.get("input"))
        session_ids = _job_session_ids(job_input)
        evidence_ids = [
            str(item) for item in job_input.get("evidence_ids", []) if str(item).strip()
        ]
        if not session_ids and not evidence_ids:
            return
        with self.store.connect() as connection:
            clauses = ["brain_id=?", "tombstoned_at IS NULL"]
            params: list[Any] = [self.store.brain_id]
            if session_ids:
                session_placeholders = ",".join("?" for _ in session_ids)
                clauses.append(f"session_id IN ({session_placeholders})")
                params.extend(session_ids)
            if evidence_ids:
                clauses.append(f"id IN ({','.join('?' for _ in evidence_ids)})")
                params.extend(evidence_ids)
            rows = connection.execute(
                "SELECT id, session_id, source_type, content, occurred_at "
                f"FROM evidence_items WHERE {' AND '.join(clauses)} "
                "ORDER BY occurred_at, id",
                params,
            ).fetchall()
            observation_clauses = ["brain_id=?"]
            observation_params: list[Any] = [self.store.brain_id]
            if session_ids:
                session_placeholders = ",".join("?" for _ in session_ids)
                observation_clauses.append(f"session_id IN ({session_placeholders})")
                observation_params.extend(session_ids)
            observation_rows = connection.execute(
                "SELECT evidence_ids_json FROM observations WHERE "
                + " AND ".join(observation_clauses),
                tuple(observation_params),
            ).fetchall()
        already_observed: set[str] = set()
        for observation_row in observation_rows:
            try:
                values = json.loads(observation_row["evidence_ids_json"] or "[]")
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(values, list):
                already_observed.update(str(value) for value in values)
        for row in rows:
            if (
                row["source_type"] not in AUTHORITATIVE_SOURCE_TYPES
                or not row["session_id"]
            ):
                continue
            if str(row["id"]) in already_observed:
                continue
            self.store.add_observation(
                session_id=str(row["session_id"]),
                kind="correction" if row["source_type"] == "correction" else "event",
                text=str(row["content"])[:8_000],
                evidence_ids=[str(row["id"])],
                idempotency_key=stable_hash(
                    self.store.brain_id,
                    "dream-observation:v1",
                    row["id"],
                ),
                valid_from=str(row["occurred_at"]),
            )

    def _load_candidates(
        self, job: Mapping[str, Any]
    ) -> tuple[list[DreamCandidate], tuple[TriageOperation, ...]]:
        job_input = _mapping(job.get("input"))
        session_ids = _job_session_ids(job_input)
        requested_evidence = {
            str(item) for item in job_input.get("evidence_ids", []) if str(item).strip()
        }
        # The managed maintenance claim is quota-sized for one bounded
        # 100-candidate unit. Continuations carry the immutable evidence
        # snapshot forward; never let a local config expand one atomic unit.
        limit = min(100, self.config.dream_max_batch)
        observations = self.store.pending_observations(
            limit=limit,
            session_ids=session_ids,
            evidence_ids=tuple(requested_evidence),
            knowledge_space="personal",
        )
        selected: list[DreamCandidate] = []
        deterministic: list[TriageOperation] = []
        for observation in observations:
            if (
                session_ids
                and str(observation.get("session_id") or "") not in session_ids
            ):
                continue
            observation_evidence = {
                str(item) for item in observation.get("evidence_ids", [])
            }
            if requested_evidence and not observation_evidence.intersection(
                requested_evidence
            ):
                continue
            candidate = self._candidate_from_observation(observation)
            if candidate.knowledge_space != "personal":
                continue
            if not candidate.authoritative_evidence_ids:
                # Assistant/tool-only material can remain evidence but cannot
                # become customer truth. It is discarded without an LLM call.
                if candidate.allowed_evidence_ids:
                    deterministic.append(
                        TriageOperation(
                            observation_id=candidate.observation_id,
                            action="discard_transient",
                            evidence_ids=tuple(sorted(candidate.allowed_evidence_ids)),
                            rationale="No customer-authoritative evidence supports promotion.",
                        )
                    )
                selected.append(candidate)
            else:
                selected.append(candidate)
            if len(selected) >= limit:
                break
        return selected, tuple(deterministic)

    def _candidate_from_observation(
        self, observation: Mapping[str, Any]
    ) -> DreamCandidate:
        evidence_ids = [str(item) for item in observation.get("evidence_ids", [])]
        if not evidence_ids:
            raise CortexDreamError("observation has no evidence")
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT e.id, e.source_type, e.content, e.occurred_at, e.sensitivity, "
                "k.slug AS knowledge_space FROM evidence_items e "
                "JOIN knowledge_spaces k ON k.id=e.knowledge_space_id "
                f"WHERE e.brain_id=? AND e.tombstoned_at IS NULL AND e.id IN "
                f"({','.join('?' for _ in evidence_ids)})",
                (self.store.brain_id, *evidence_ids),
            ).fetchall()
        found = {str(row["id"]): dict(row) for row in rows}
        if set(found) != set(evidence_ids):
            raise CortexDreamError(
                "observation references missing or unauthorized evidence"
            )
        space = str(observation["knowledge_space"])
        if any(str(row["knowledge_space"]) != space for row in found.values()):
            raise CortexDreamError("observation evidence crosses knowledge spaces")
        authoritative_all = frozenset(
            evidence_id
            for evidence_id, row in found.items()
            if row["source_type"] in AUTHORITATIVE_SOURCE_TYPES
        )
        assistant_all = frozenset(
            evidence_id
            for evidence_id, row in found.items()
            if row["source_type"] in ASSISTANT_SOURCE_TYPES
        )
        authoritative_ordered = [
            item for item in evidence_ids if item in authoritative_all
        ][:_MAX_EVIDENCE_PER_CANDIDATE]
        assistant_ordered = [item for item in evidence_ids if item in assistant_all][:1]
        other_ordered = [
            item
            for item in evidence_ids
            if item not in authoritative_all and item not in assistant_all
        ][:1]
        selected_evidence_ids = tuple(
            dict.fromkeys(authoritative_ordered + assistant_ordered + other_ordered)
        )
        authoritative = frozenset(authoritative_ordered)
        assistant = frozenset(assistant_ordered)
        related = self._related_memories(
            str(observation["normalized_text"]),
            str(observation["knowledge_space_id"]),
        )
        return DreamCandidate(
            observation_id=str(observation["id"]),
            session_id=str(observation.get("session_id") or ""),
            knowledge_space=space,
            kind=str(observation["kind"]),
            text=str(observation["normalized_text"]),
            evidence=tuple(found[evidence_id] for evidence_id in selected_evidence_ids),
            authoritative_evidence_ids=authoritative,
            allowed_evidence_ids=frozenset(selected_evidence_ids),
            assistant_evidence_ids=assistant,
            related_memories=related,
            valid_from=observation.get("valid_from"),
            valid_until=observation.get("valid_until"),
        )

    def _related_memories(
        self, text: str, knowledge_space_id: str
    ) -> tuple[Mapping[str, Any], ...]:
        terms = [
            term.lower()
            for term in text.replace("'", " ").replace("-", " ").split()
            if len(term) >= 4
        ][:8]
        with self.store.connect() as connection:
            if terms:
                clauses = " OR ".join(
                    "LOWER(canonical_statement) LIKE ?" for _ in terms
                )
                rows = connection.execute(
                    "SELECT id, kind, canonical_statement, status, epistemic_status, valid_from, "
                    "valid_until FROM memory_records WHERE brain_id=? AND knowledge_space_id=? "
                    "AND status IN ('active','disputed') AND deleted_at IS NULL "
                    f"AND ({clauses}) ORDER BY updated_at DESC LIMIT 12",
                    (
                        self.store.brain_id,
                        knowledge_space_id,
                        *(f"%{term}%" for term in terms),
                    ),
                ).fetchall()
            else:
                rows = []
        return tuple(dict(row) for row in rows)

    def _call_and_validate(
        self,
        route: CortexModelRoute,
        candidates: Sequence[DreamCandidate],
        usage: UsageTotals,
        *,
        allowed_actions: frozenset[str],
        review: bool,
    ) -> tuple[tuple[TriageOperation, ...], str]:
        prompt = self._prompt(
            candidates, allowed_actions=allowed_actions, review=review
        )
        if _utf8_size(prompt) > _MAX_PROMPT_MESSAGE_BYTES:
            raise CortexDreamError("Cortex prompt exceeds the managed message budget")
        response = self._call_route(route, prompt)
        usage.add_response(response)
        text = self._response_text(response)
        try:
            operations = validate_triage_output(
                text, candidates, allowed_actions=allowed_actions
            )
        except CortexOutputError as first_error:
            repair_prompt = self._repair_prompt(
                text, str(first_error), candidates, allowed_actions=allowed_actions
            )
            if _utf8_size(repair_prompt) > _MAX_PROMPT_MESSAGE_BYTES:
                raise CortexDreamError(
                    "Cortex repair prompt exceeds the managed message budget"
                )
            repaired = self._call_route(route, repair_prompt)
            usage.add_response(repaired)
            try:
                operations = validate_triage_output(
                    self._response_text(repaired),
                    candidates,
                    allowed_actions=allowed_actions,
                )
            except CortexOutputError:
                if len(candidates) <= 1:
                    raise
                # A batch whose full response exceeds the route's output
                # token budget truncates mid-document and can never repair
                # (the repair re-emits the same oversized document). Halving
                # the batch halves the required output, so recursion always
                # terminates at single-candidate batches.
                middle = len(candidates) // 2
                left, label = self._call_and_validate(
                    route,
                    candidates[:middle],
                    usage,
                    allowed_actions=allowed_actions,
                    review=review,
                )
                right, _ = self._call_and_validate(
                    route,
                    candidates[middle:],
                    usage,
                    allowed_actions=allowed_actions,
                    review=review,
                )
                return (*left, *right), label
        response_model = str(_object_value(response, "model", "") or route.model)
        return operations, f"{route.provider}:{response_model}"

    def _prompt_batches(
        self,
        candidates: Sequence[DreamCandidate],
        *,
        allowed_actions: frozenset[str],
        review: bool,
    ) -> list[Sequence[DreamCandidate]]:
        """Pack candidates by count and the gateway's real UTF-8 byte limit."""

        batches: list[Sequence[DreamCandidate]] = []
        current: list[DreamCandidate] = []
        for candidate in candidates:
            proposed = [*current, candidate]
            exceeds_count = len(proposed) > _MAX_CANDIDATES_PER_CALL
            exceeds_bytes = (
                _utf8_size(
                    self._prompt(
                        proposed,
                        allowed_actions=allowed_actions,
                        review=review,
                    )
                )
                > _MAX_PROMPT_MESSAGE_BYTES
            )
            if current and (exceeds_count or exceeds_bytes):
                batches.append(tuple(current))
                current = [candidate]
            else:
                current = proposed
            if (
                _utf8_size(
                    self._prompt(
                        current,
                        allowed_actions=allowed_actions,
                        review=review,
                    )
                )
                > _MAX_PROMPT_MESSAGE_BYTES
            ):
                raise CortexDreamError("one Cortex candidate exceeds the prompt budget")
        if current:
            batches.append(tuple(current))
        return batches

    def _call_route(self, route: CortexModelRoute, prompt: str) -> Any:
        call = self._llm_call
        if call is None:
            from agent.auxiliary_client import call_llm

            call = call_llm
        return call(
            task=route.task,
            provider=route.provider,
            model=route.model,
            base_url=route.base_url,
            api_key=route.api_key,
            api_mode=route.api_mode,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the Atlas Cortex memory utility classifier. Return one exact "
                        "JSON object matching the supplied schema. Never invent evidence IDs, "
                        "database IDs, customer claims, or authorization. Assistant output is "
                        "not customer truth. Do not include markdown or analysis."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            max_tokens=route.max_tokens,
            bounded_output=True,
            timeout=route.timeout,
            extra_body={"response_format": {"type": "json_object"}},
            fallback_policy="none",
        )

    @staticmethod
    def _response_text(response: Any) -> str:
        from agent.auxiliary_client import extract_content_or_reasoning

        text = extract_content_or_reasoning(response)
        if not isinstance(text, str) or not text.strip():
            raise CortexOutputError("model returned no JSON content")
        return text.strip()

    @staticmethod
    def _prompt(
        candidates: Sequence[DreamCandidate],
        *,
        allowed_actions: frozenset[str],
        review: bool,
    ) -> str:
        mode = "deeper ambiguity review" if review else "cheap-model utility triage"
        contract = {
            "schema_version": TRIAGE_SCHEMA_VERSION,
            "operations": [
                {
                    "observation_id": "exact candidate ID",
                    "action": sorted(allowed_actions),
                    "memory_kind": sorted(MEMORY_KINDS),
                    "statement": "one atomic evidence-supported statement or empty",
                    "evidence_ids": ["exact IDs from that candidate"],
                    "target_memory_id": "authorized related ID or null",
                    "entities": [
                        {"name": "name", "type": sorted(ENTITY_TYPES), "aliases": []}
                    ],
                    "relations": [
                        {
                            "subject_entity_index": "zero-based index in entities",
                            "predicate": sorted(PERSONAL_RELATION_PREDICATES),
                            "object_entity_index": "zero-based index in entities",
                        }
                    ],
                    "valid_from": "ISO-8601 with timezone or null",
                    "valid_until": "ISO-8601 with timezone or null",
                    "rationale": "short plain-language reason",
                    "sensitivity": "private|sensitive|restricted",
                    "retention": "standard|short|long",
                    "missing_information": "short note or empty",
                }
            ],
        }
        return _json({
            "task": mode,
            "rubric": [
                "Promote only information useful outside the original turn.",
                "Politeness, repetition, assistant speculation, and execution noise are transient.",
                "Use only listed evidence and related-memory IDs.",
                "Relations may connect only entities in the same operation and must be directly supported by its cited evidence.",
                "Prefer temporal supersession over erasing prior history.",
                "Return exactly one operation for every candidate.",
            ],
            "output_contract": contract,
            "candidates": [candidate.prompt_value() for candidate in candidates],
        })

    @staticmethod
    def _repair_prompt(
        invalid_text: str,
        error: str,
        candidates: Sequence[DreamCandidate],
        *,
        allowed_actions: frozenset[str],
    ) -> str:
        return _json({
            "task": "repair_invalid_cortex_json_once",
            "schema_version": TRIAGE_SCHEMA_VERSION,
            "validation_error": error[:1_000],
            "invalid_output": invalid_text[:20_000],
            "allowed_actions": sorted(allowed_actions),
            "candidate_constraints": [
                {
                    "observation_id": candidate.observation_id,
                    "evidence_ids": sorted(candidate.allowed_evidence_ids),
                    "authoritative_evidence_ids": sorted(
                        candidate.authoritative_evidence_ids
                    ),
                    "target_memory_ids": sorted(candidate.allowed_target_memory_ids),
                }
                for candidate in candidates
            ],
            "instruction": (
                "Return only the corrected exact JSON object. Do not add markdown, "
                "explanation, IDs, or claims."
            ),
        })

    def _apply_operation(
        self, job_id: str, operation: TriageOperation, *, owner: str
    ) -> dict[str, int]:
        graph_signature = _json({
            "entities": [
                {
                    "name": entity.name,
                    "type": entity.entity_type,
                    "aliases": list(entity.aliases),
                }
                for entity in operation.entities
            ],
            "relations": [
                {
                    "subject": relation.subject_entity_index,
                    "predicate": relation.predicate,
                    "object": relation.object_entity_index,
                }
                for relation in operation.relations
            ],
        })
        operation_key = stable_hash(
            self.store.brain_id,
            operation.observation_id,
            operation.action,
            operation.statement,
            operation.target_memory_id,
            graph_signature,
            *sorted(operation.evidence_ids),
            TRIAGE_PROMPT_VERSION,
        )
        with self.store.atomic_job_operation(
            job_id, owner=owner, operation_key=operation_key
        ) as should_apply:
            if not should_apply:
                return {}
            resource_id, result = self._apply_operation_mutations(job_id, operation)
            recorded = self.store.record_job_operation(
                job_id,
                operation_key=operation_key,
                operation_type=operation.action,
                resource_id=resource_id,
                result=result,
            )
            if not recorded:
                raise CortexDreamError("dream operation receipt collided before commit")
        self._heartbeat(job_id, owner)
        return result

    def _apply_operation_mutations(
        self, job_id: str, operation: TriageOperation
    ) -> tuple[str, dict[str, int]]:
        self._validate_operation_evidence(operation)
        sensitive = operation.sensitivity in {"sensitive", "restricted", "confidential"}
        if operation.action == "defer_unresolved":
            # A defer operation is deliberately non-mutating. It records that
            # this job made no semantic decision while leaving the observation
            # pending for a future approved model or customer review.
            self.store.defer_observation(
                operation.observation_id,
                reason=operation.rationale,
                prompt_version=TRIAGE_PROMPT_VERSION,
            )
            resource_id = operation.observation_id
            result = {"needs_review": 1}
        elif sensitive and self.config.sensitive_requires_review:
            self.store.mark_observation(
                operation.observation_id,
                state="needs-review",
                action="needs_deeper_review",
                reason="Sensitive candidate requires customer review before promotion.",
                prompt_version=TRIAGE_PROMPT_VERSION,
            )
            resource_id = operation.observation_id
            result = {"needs_review": 1}
        elif operation.action == "discard_transient":
            self.store.mark_observation(
                operation.observation_id,
                state="discarded",
                action=operation.action,
                reason=operation.rationale,
                prompt_version=TRIAGE_PROMPT_VERSION,
            )
            resource_id = operation.observation_id
            result = {"discarded": 1}
        elif operation.action == "retain_hot_until":
            with self.store.transaction() as connection:
                cursor = connection.execute(
                    "UPDATE observations SET valid_until=?, processing_state='retained-hot', "
                    "utility_action=?, triage_reason=?, prompt_version=?, processed_at=? "
                    "WHERE id=? AND brain_id=?",
                    (
                        operation.valid_until,
                        operation.action,
                        operation.rationale,
                        TRIAGE_PROMPT_VERSION,
                        utc_now(),
                        operation.observation_id,
                        self.store.brain_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise CortexDreamError("observation disappeared before retention")
            resource_id = operation.observation_id
            result = {}
        elif operation.action == "promote_new":
            resource_id, created = self.store.promote_memory(
                statement=operation.statement,
                kind=operation.memory_kind,
                evidence_ids=operation.evidence_ids,
                valid_from=operation.valid_from,
                valid_until=operation.valid_until,
                job_id=job_id,
                prompt_version=TRIAGE_PROMPT_VERSION,
                metadata={
                    "triage_rationale": operation.rationale,
                    "retention_suggestion": operation.retention,
                },
            )
            entities_created, relations_created = self._project_graph(operation)
            self._mark_applied(operation, "promoted")
            result = {
                "promoted": int(created),
                "entities_created": entities_created,
                "relations_created": relations_created,
            }
        elif operation.action == "merge_existing":
            target = self._memory_target(operation)
            resource_id, _ = self.store.promote_memory(
                statement=str(target["canonical_statement"]),
                kind=str(target["kind"]),
                evidence_ids=operation.evidence_ids,
                valid_from=target["valid_from"],
                valid_until=operation.valid_until or target["valid_until"],
                job_id=job_id,
                prompt_version=TRIAGE_PROMPT_VERSION,
                metadata={"merged_observation": operation.observation_id},
            )
            entities_created, relations_created = self._project_graph(operation)
            self._mark_applied(operation, "merged")
            result = {
                "merged": 1,
                "entities_created": entities_created,
                "relations_created": relations_created,
            }
        elif operation.action == "supersede_existing":
            resource_id = self._supersede(operation, job_id)
            entities_created, relations_created = self._project_graph(operation)
            self._mark_applied(operation, "superseded")
            result = {
                "superseded": 1,
                "entities_created": entities_created,
                "relations_created": relations_created,
            }
        elif operation.action == "mark_disputed":
            resource_id = self._mark_disputed(operation)
            entities_created, relations_created = self._project_graph(operation)
            self._mark_applied(operation, "disputed")
            result = {
                "disputed": 1,
                "entities_created": entities_created,
                "relations_created": relations_created,
            }
        else:
            self.store.mark_observation(
                operation.observation_id,
                state="needs-review",
                action="needs_deeper_review",
                reason=operation.rationale,
                prompt_version=TRIAGE_PROMPT_VERSION,
            )
            resource_id = operation.observation_id
            result = {"needs_review": 1}
        return resource_id, result

    def _validate_operation_evidence(self, operation: TriageOperation) -> None:
        placeholders = ",".join("?" for _ in operation.evidence_ids)
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT id, source_type FROM evidence_items WHERE brain_id=? "
                "AND tombstoned_at IS NULL "
                f"AND id IN ({placeholders})",
                (self.store.brain_id, *operation.evidence_ids),
            ).fetchall()
        found = {str(row["id"]): str(row["source_type"]) for row in rows}
        if set(found) != set(operation.evidence_ids):
            raise CortexDreamError("operation evidence is missing or unauthorized")
        if operation.action in PROMOTION_ACTIONS:
            if any(source in ASSISTANT_SOURCE_TYPES for source in found.values()):
                raise CortexDreamError(
                    "assistant output cannot be promoted as customer truth"
                )
            if not any(
                source in AUTHORITATIVE_SOURCE_TYPES for source in found.values()
            ):
                raise CortexDreamError(
                    "promotion lacks customer-authoritative evidence"
                )

    def _memory_target(self, operation: TriageOperation) -> Mapping[str, Any]:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM memory_records WHERE id=? AND brain_id=? AND deleted_at IS NULL",
                (operation.target_memory_id, self.store.brain_id),
            ).fetchone()
        if not row or row["status"] not in {"active", "disputed", "superseded"}:
            raise CortexDreamError("target memory is unavailable")
        return dict(row)

    def _supersede(self, operation: TriageOperation, job_id: str) -> str:
        target = self._memory_target(operation)
        if target["status"] == "superseded":
            successor = target["superseded_by_id"]
            if not successor:
                raise CortexDreamError("superseded memory has no successor")
            with self.store.connect() as connection:
                row = connection.execute(
                    "SELECT canonical_statement FROM memory_records WHERE id=? AND brain_id=?",
                    (successor, self.store.brain_id),
                ).fetchone()
            if row and stable_hash(
                str(row["canonical_statement"]).lower()
            ) == stable_hash(operation.statement.lower()):
                return str(successor)
            raise CortexDreamError(
                "target memory was superseded by a different operation"
            )
        return self.store.supersede_memory(
            str(target["id"]),
            statement=operation.statement,
            kind=operation.memory_kind,
            evidence_ids=operation.evidence_ids,
            epistemic_status="reported",
            job_id=job_id,
            prompt_version=TRIAGE_PROMPT_VERSION,
        )

    def _mark_disputed(self, operation: TriageOperation) -> str:
        target = self._memory_target(operation)
        memory_id = str(target["id"])
        with self.store.transaction() as connection:
            connection.execute(
                "UPDATE memory_records SET status='disputed', epistemic_status='disputed', "
                "updated_at=? WHERE id=? AND brain_id=?",
                (utc_now(), memory_id, self.store.brain_id),
            )
            connection.executemany(
                "INSERT OR IGNORE INTO memory_evidence(memory_id, evidence_id) VALUES(?,?)",
                [(memory_id, evidence_id) for evidence_id in operation.evidence_ids],
            )
        return memory_id

    def _project_graph(self, operation: TriageOperation) -> tuple[int, int]:
        entities_created = 0
        entity_ids: list[str] = []
        for entity in operation.entities:
            entity_id, was_created = self.store.upsert_entity(
                entity_type=entity.entity_type,
                canonical_name=entity.name,
                aliases=entity.aliases,
                evidence_ids=operation.evidence_ids,
            )
            entity_ids.append(entity_id)
            entities_created += int(was_created)

        relations = operation.relations
        if not relations and len(entity_ids) > 1:
            # A conservative fallback gives co-mentioned entities a navigable,
            # evidence-backed edge without inferring a business relationship.
            # A chain is enough to connect the bounded operation graph and
            # avoids the quadratic edge explosion of every possible pair.
            relations = tuple(
                TriageRelation(index, "co_mentioned_with", index + 1)
                for index in range(len(entity_ids) - 1)
            )

        relations_created = 0
        for relation in relations:
            _, was_created = self.store.upsert_relation(
                subject_entity_id=entity_ids[relation.subject_entity_index],
                predicate=relation.predicate,
                object_entity_id=entity_ids[relation.object_entity_index],
                evidence_ids=operation.evidence_ids,
                valid_from=operation.valid_from,
                valid_until=operation.valid_until,
                derived_by="cortex:personal-triage:v1",
            )
            relations_created += int(was_created)
        return entities_created, relations_created

    def _mark_applied(self, operation: TriageOperation, state: str) -> None:
        self.store.mark_observation(
            operation.observation_id,
            state=state,
            action=operation.action,
            reason=operation.rationale,
            prompt_version=TRIAGE_PROMPT_VERSION,
        )

    def _checkpoint(
        self, job_id: str, owner: str, phase: str, data: Mapping[str, Any]
    ) -> None:
        self.store.checkpoint_job(job_id, owner=owner, phase=phase, data=data)

    def _heartbeat(self, job_id: str, owner: str) -> None:
        self.store.heartbeat_job(
            job_id, owner=owner, lease_seconds=self.config.dream_lease_seconds
        )
        if not self.store.renew_named_lease(
            "dream-cycle", owner=owner, lease_seconds=self.config.dream_lease_seconds
        ):
            raise CortexDreamError("dream-cycle brain lease was lost")

    def _record_health(
        self, job_id: str, status: str, report: Mapping[str, Any]
    ) -> None:
        with self.store.transaction() as connection:
            connection.execute(
                "INSERT INTO health_reports(id, brain_id, job_id, status, report_json, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    new_id("health"),
                    self.store.brain_id,
                    job_id,
                    status,
                    _json(sanitize_for_storage(dict(report))),
                    utc_now(),
                ),
            )
            self.store.audit_in_transaction(
                connection,
                principal_id=None,
                action="cortex.dream",
                resource_type="cognitive_job",
                resource_id=job_id,
                outcome=status,
                details={"report": dict(report)},
            )


def _utf8_size(value: str) -> int:
    return len(value.encode("utf-8", errors="replace"))


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


__all__ = [
    "ACTIONS",
    "CortexDreamError",
    "CortexModelRoute",
    "CortexOutputError",
    "CortexRouteConflictError",
    "CortexRouteError",
    "DreamOutcome",
    "DreamProcessor",
    "FINAL_ACTIONS",
    "PERSONAL_RELATION_PREDICATES",
    "TRIAGE_PROMPT_VERSION",
    "TRIAGE_SCHEMA_VERSION",
    "TriageOperation",
    "TriageRelation",
    "cortex_model_routes_equal",
    "cortex_provider_is_approved",
    "cortex_provider_is_managed",
    "resolve_model_route",
    "validate_cortex_model_selection",
    "validate_triage_output",
]
