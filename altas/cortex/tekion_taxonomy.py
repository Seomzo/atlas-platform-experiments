"""Conservative dealership knowledge taxonomy for Cortex GraphRAG artifacts.

This is a seed vocabulary for normalizing an approved dealership/Tekion
documentation corpus.  It is not a live Tekion connector and does not encode
customer data, credentials, or claims about any deployed Tekion environment.
"""

from __future__ import annotations

import re


ENTITY_TYPES = frozenset({
    "module",
    "screen",
    "field",
    "action",
    "workflow",
    "prerequisite",
    "role",
    "error",
    "customer_concept",
    "deal_concept",
    "vehicle_concept",
    "policy",
    "integration",
    "report",
    "document",
    "concept",
})

RELATIONSHIP_TYPES = frozenset({
    "contains",
    "navigates_to",
    "requires_role",
    "precedes",
    "updates",
    "reads_from",
    "writes_to",
    "blocked_by",
    "resolved_by",
    "documented_in",
    "supersedes",
    "applies_to",
    "related_to",
})

_ENTITY_ALIASES = {
    "page": "screen",
    "permission": "role",
    "permission_role": "role",
    "exception": "error",
    "customer": "customer_concept",
    "deal": "deal_concept",
    "vehicle": "vehicle_concept",
    "doc": "document",
    "version": "document",
}


def _slug(value: object) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    return text.strip("_")


def normalize_entity_type(value: object) -> str:
    """Map extraction output to a bounded, customer-readable entity type."""
    candidate = _ENTITY_ALIASES.get(_slug(value), _slug(value))
    return candidate if candidate in ENTITY_TYPES else "concept"


def normalize_predicate(value: object) -> str:
    """Map extraction output to a bounded relationship vocabulary."""
    candidate = _slug(value)
    return candidate if candidate in RELATIONSHIP_TYPES else "related_to"


__all__ = [
    "ENTITY_TYPES",
    "RELATIONSHIP_TYPES",
    "normalize_entity_type",
    "normalize_predicate",
]
