"""Versioned, privacy-safe DTOs for the Atlas Cortex customer graph.

The graph overview is intentionally a projection rather than a database dump.
It contains stable identifiers, typed nodes and directed edges, but never raw
evidence or GraphRAG document bodies.  Those bodies are loaded only by the
authenticated detail endpoint for a node the customer selected.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections import Counter
from typing import Any, Iterable, Mapping, Sequence

from .store import CortexStore, stable_hash, utc_now


GRAPH_DTO_VERSION = "atlas.cortex.graph.v1"
DETAIL_DTO_VERSION = "atlas.cortex.detail.v1"
HEALTH_DTO_VERSION = "atlas.cortex.health.v1"
JOB_DTO_VERSION = "atlas.cortex.job.v1"

_NODE_TYPES = frozenset({
    "entity",
    "memory",
    "session",
    "evidence",
    "document",
    "community",
})
_PROJECTIONS = frozenset({
    "growth",
    "local",
    "communities",
    "answer-path",
    "timeline",
    "workflow",
    "health",
})
_STRATIFIED_TYPE_ORDER = (
    "memory",
    "session",
    "community",
    "document",
    "entity",
    "evidence",
)
_STRATIFIED_FIRST_WINDOW_TARGETS = {
    "memory": 50,
    "session": 100,
    "community": 200,
    "document": 50,
    "entity": 200,
}
_SAFE_JOB_OUTPUT_FIELDS = frozenset({
    "status",
    "sessions_processed",
    "observations_processed",
    "promoted",
    "merged",
    "superseded",
    "disputed",
    "discarded",
    "needs_review",
    "entities_created",
    "relations_created",
})


def _bounded(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _truncate(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)].rstrip()}…"


def _loads_list(value: Any) -> list[Any]:
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return decoded if isinstance(decoded, list) else []


def _loads_mapping(value: Any) -> dict[str, Any]:
    try:
        decoded = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _edge_id(edge_type: str, source: str, target: str, qualifier: str = "") -> str:
    return f"edge_{stable_hash(edge_type, source, target, qualifier)[:32]}"


def _encode_cursor(offset: int) -> str:
    payload = json.dumps({"offset": offset}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        offset = int(payload["offset"])
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        binascii.Error,
    ) as exc:
        raise ValueError("invalid cognitive graph cursor") from exc
    if offset < 0 or offset > 10_000:
        raise ValueError("cognitive graph cursor is outside the supported window")
    return offset


def _facets(nodes: Sequence[Mapping[str, Any]], key: str) -> list[dict[str, Any]]:
    counts = Counter(str(node.get(key) or "unknown") for node in nodes)
    return [{"value": value, "count": counts[value]} for value in sorted(counts)]


def _clean_node(node: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in node.items() if not key.startswith("_")}


def _base_node(
    *,
    node_id: str,
    label: str,
    node_type: str,
    domain: str,
    summary: str,
    status: str,
    created_at: str | None,
    updated_at: str | None,
    privacy: str,
    badges: Iterable[str] = (),
    community: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "label": _truncate(label, 120) or node_type.title(),
        "type": node_type,
        "domain": domain,
        "community": community,
        "created_at": created_at,
        "updated_at": updated_at or created_at,
        "status": status,
        "badges": sorted(set(badges)),
        "summary": _truncate(summary, 420),
        "degree": 0,
        "usage": 0,
        "privacy": privacy,
        "metadata": dict(metadata or {}),
        "_sort_at": updated_at or created_at or "",
    }


def _space_maps(
    connection: Any, store: CortexStore
) -> tuple[dict[str, str], dict[str, str]]:
    rows = connection.execute(
        "SELECT id, slug, visibility FROM knowledge_spaces "
        "WHERE brain_id=? AND deleted_at IS NULL",
        (store.brain_id,),
    ).fetchall()
    return (
        {str(row["id"]): str(row["slug"]) for row in rows},
        {str(row["id"]): str(row["visibility"]) for row in rows},
    )


def _collect_nodes(
    connection: Any,
    store: CortexStore,
    *,
    fetch_limit: int,
    node_types: set[str],
    domain: str | None,
    status: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    spaces, visibility = _space_maps(connection, store)
    nodes: list[dict[str, Any]] = []
    community_rows: list[dict[str, Any]] = []

    def allowed(space_id: str, item_status: str) -> bool:
        return (not domain or spaces.get(space_id) == domain) and (
            not status or item_status == status
        )

    if "entity" in node_types:
        rows = connection.execute(
            "SELECT * FROM entities WHERE brain_id=? AND deleted_at IS NULL "
            "ORDER BY last_seen_at DESC, id LIMIT ?",
            (store.brain_id, fetch_limit),
        ).fetchall()
        for row in rows:
            space_id = str(row["knowledge_space_id"])
            if not allowed(space_id, "active"):
                continue
            badges = ["private"] if visibility.get(space_id) == "private" else []
            nodes.append(
                _base_node(
                    node_id=str(row["id"]),
                    label=str(row["canonical_name"]),
                    node_type="entity",
                    domain=spaces.get(space_id, "unknown"),
                    summary=str(row["description"] or ""),
                    status="active",
                    created_at=row["first_seen_at"],
                    updated_at=row["last_seen_at"],
                    privacy=visibility.get(space_id, "private"),
                    badges=badges,
                    metadata={"entity_type": str(row["entity_type"])},
                )
            )

    if "memory" in node_types:
        rows = connection.execute(
            "SELECT m.*, COUNT(e.id) AS evidence_count FROM memory_records m "
            "LEFT JOIN memory_evidence me ON me.memory_id=m.id "
            "LEFT JOIN evidence_items e ON e.id=me.evidence_id "
            "AND e.brain_id=m.brain_id AND e.tombstoned_at IS NULL "
            "WHERE m.brain_id=? AND m.deleted_at IS NULL "
            "AND m.status IN ('active','disputed') GROUP BY m.id "
            "ORDER BY m.updated_at DESC, m.id LIMIT ?",
            (store.brain_id, fetch_limit),
        ).fetchall()
        for row in rows:
            space_id = str(row["knowledge_space_id"])
            item_status = str(row["status"])
            if not allowed(space_id, item_status):
                continue
            badges = []
            if row["protected"]:
                badges.append("protected")
            if int(row["evidence_count"] or 0) > 0:
                badges.append("cited")
            if item_status == "disputed":
                badges.append(item_status)
            if str(row["epistemic_status"]) == "inferred":
                badges.append("inferred")
            if visibility.get(space_id) == "private":
                badges.append("private")
            statement = str(row["canonical_statement"])
            nodes.append(
                _base_node(
                    node_id=str(row["id"]),
                    label=statement,
                    node_type="memory",
                    domain=spaces.get(space_id, "unknown"),
                    summary=statement,
                    status=item_status,
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    privacy=visibility.get(space_id, "private"),
                    badges=badges,
                    metadata={
                        "memory_kind": str(row["kind"]),
                        "epistemic_status": str(row["epistemic_status"]),
                        "evidence_count": int(row["evidence_count"] or 0),
                        "valid_from": row["valid_from"],
                        "valid_until": row["valid_until"],
                    },
                )
            )

    if "session" in node_types and (not domain or domain == "personal"):
        rows = connection.execute(
            "SELECT * FROM sessions WHERE brain_id=? AND state!='deleted' "
            "ORDER BY updated_at DESC, id LIMIT ?",
            (store.brain_id, fetch_limit),
        ).fetchall()
        for row in rows:
            item_status = str(row["state"])
            if status and status != item_status:
                continue
            label = str(row["title"] or f"Session {str(row['started_at'])[:10]}")
            nodes.append(
                _base_node(
                    node_id=str(row["id"]),
                    label=label,
                    node_type="session",
                    domain="personal",
                    summary=str(row["summary"] or "Conversation session"),
                    status=item_status,
                    created_at=row["started_at"],
                    updated_at=row["updated_at"],
                    privacy="private",
                    badges=("private",),
                    metadata={"finalized_at": row["finalized_at"]},
                )
            )

    if "evidence" in node_types:
        rows = connection.execute(
            "SELECT * FROM evidence_items WHERE brain_id=? AND tombstoned_at IS NULL "
            "ORDER BY ingested_at DESC, id LIMIT ?",
            (store.brain_id, fetch_limit),
        ).fetchall()
        for row in rows:
            space_id = str(row["knowledge_space_id"])
            if not allowed(space_id, "active"):
                continue
            source_type = str(row["source_type"])
            label = f"{source_type.replace('_', ' ').title()} evidence"
            badges = ["source", str(row["sensitivity"])]
            nodes.append({
                **_base_node(
                    node_id=str(row["id"]),
                    label=label,
                    node_type="evidence",
                    domain=spaces.get(space_id, "unknown"),
                    summary="Source evidence; content is hidden in the graph overview.",
                    status="active",
                    created_at=row["occurred_at"],
                    updated_at=row["ingested_at"],
                    privacy=str(row["sensitivity"]),
                    badges=badges,
                    metadata={
                        "source_type": source_type,
                        "retention_class": str(row["retention_class"]),
                    },
                ),
                "_session_id": row["session_id"],
            })

    if "document" in node_types:
        rows = connection.execute(
            "SELECT d.*, i.version, i.published_at FROM graphrag_documents d "
            "JOIN graphrag_indexes i ON i.id=d.index_id "
            "WHERE d.brain_id=? AND i.state='active' "
            "ORDER BY COALESCE(i.published_at, i.created_at) DESC, d.id LIMIT ?",
            (store.brain_id, fetch_limit),
        ).fetchall()
        for row in rows:
            space_id = str(row["knowledge_space_id"])
            if not allowed(space_id, "active"):
                continue
            nodes.append({
                **_base_node(
                    node_id=str(row["id"]),
                    label=str(row["title"]),
                    node_type="document",
                    domain=spaces.get(space_id, "unknown"),
                    summary="Versioned knowledge-base document; content is hidden in the graph overview.",
                    status="active",
                    created_at=row["published_at"],
                    updated_at=row["published_at"],
                    privacy=visibility.get(space_id, "shared"),
                    badges=("verified", "cited"),
                    metadata={
                        "index_version": str(row["version"]),
                        "document_id": str(row["document_id"]),
                    },
                ),
                "_entity_ids": [
                    str(item) for item in _loads_list(row["entity_ids_json"])
                ],
                "_community_ids": [
                    str(item) for item in _loads_list(row["community_ids_json"])
                ],
            })

    rows = connection.execute(
        "SELECT * FROM communities WHERE brain_id=? AND stale=0 "
        "ORDER BY generated_at DESC, id LIMIT ?",
        (store.brain_id, min(fetch_limit, 500)),
    ).fetchall()
    for row in rows:
        space_id = str(row["knowledge_space_id"])
        if domain and spaces.get(space_id) != domain:
            continue
        item = {
            "id": str(row["id"]),
            "label": _truncate(row["label"], 120),
            "level": int(row["level"]),
            "parent_id": row["parent_id"],
            "domain": spaces.get(space_id, "unknown"),
            "member_ids": [str(item) for item in _loads_list(row["member_ids_json"])],
            "generated_at": row["generated_at"],
            "status": "active",
        }
        community_rows.append(item)
        if "community" in node_types:
            nodes.append({
                **_base_node(
                    node_id=item["id"],
                    label=item["label"],
                    node_type="community",
                    domain=item["domain"],
                    summary=str(row["report"] or "Knowledge community"),
                    status="active",
                    created_at=row["generated_at"],
                    updated_at=row["generated_at"],
                    privacy=visibility.get(space_id, "private"),
                    badges=("community",),
                    metadata={
                        "level": item["level"],
                        "member_count": len(item["member_ids"]),
                    },
                ),
                "_member_ids": item["member_ids"],
            })

    return nodes, community_rows


def _stratified_node_order(
    nodes: Sequence[dict[str, Any]], *, projection: str
) -> list[dict[str, Any]]:
    """Return one stable order whose leading window represents every type.

    Evidence is normally the highest-volume table and the newest row for every
    turn. A global recency sort therefore made a bounded overview look like an
    evidence log. Keep recency inside each type, reserve the leading window for
    the complete small durable layers, then let recency order the remainder.
    The ordering is independent of the requested page size, so offset cursors
    retain their existing no-duplicate/no-gap contract.
    """
    buckets: dict[str, list[dict[str, Any]]] = {
        node_type: [] for node_type in _STRATIFIED_TYPE_ORDER
    }
    for node in nodes:
        buckets.setdefault(str(node.get("type") or ""), []).append(node)
    for bucket in buckets.values():
        bucket.sort(
            key=lambda node: (
                str(node.get("_sort_at") or ""),
                str(node.get("id") or ""),
            ),
            reverse=True,
        )

    positions = {node_type: 0 for node_type in buckets}
    ordered: list[dict[str, Any]] = []

    def take(node_type: str, count: int) -> None:
        start = positions[node_type]
        end = min(len(buckets[node_type]), start + max(0, count))
        ordered.extend(buckets[node_type][start:end])
        positions[node_type] = end

    # One node from every populated type makes the vocabulary truthful even
    # when the caller asks for a small overview.
    for node_type in _STRATIFIED_TYPE_ORDER:
        take(node_type, 1)

    # Durable/derived layers are small in normal stores. Keep their bounded
    # working sets in front of the high-volume evidence residual; entities
    # receive a larger allowance because they are the connective substrate.
    for node_type, target in _STRATIFIED_FIRST_WINDOW_TARGETS.items():
        take(node_type, target - positions[node_type])

    priority = {
        "community": 6 if projection == "communities" else 1,
        "entity": 5,
        "memory": 4,
        "document": 3,
        "session": 2,
        "evidence": 1,
    }
    remainder = [
        node
        for node_type, bucket in buckets.items()
        for node in bucket[positions[node_type] :]
    ]
    remainder.sort(
        key=lambda node: (
            str(node.get("_sort_at") or ""),
            priority.get(str(node.get("type")), 0),
            str(node.get("id") or ""),
        ),
        reverse=True,
    )
    ordered.extend(remainder)
    return ordered


def _collect_edges(
    connection: Any,
    store: CortexStore,
    nodes: Sequence[dict[str, Any]],
    *,
    edge_limit: int,
) -> list[dict[str, Any]]:
    included = {str(node["id"]) for node in nodes}
    if not included:
        return []
    edges: list[dict[str, Any]] = []
    placeholders = ",".join("?" for _ in included)
    ids = tuple(sorted(included))

    relation_rows = connection.execute(
        f"SELECT r.* FROM relations r "
        f"JOIN entities s ON s.id=r.subject_entity_id AND s.brain_id=r.brain_id "
        f"JOIN entities o ON o.id=r.object_entity_id AND o.brain_id=r.brain_id "
        f"WHERE r.brain_id=? AND r.status='active' "
        f"AND s.deleted_at IS NULL AND o.deleted_at IS NULL "
        f"AND r.subject_entity_id IN ({placeholders}) "
        f"AND r.object_entity_id IN ({placeholders}) "
        f"ORDER BY r.updated_at DESC, r.id LIMIT ?",
        (store.brain_id, *ids, *ids, edge_limit * 2),
    ).fetchall()
    for row in relation_rows:
        source = str(row["subject_entity_id"])
        target = str(row["object_entity_id"])
        edges.append({
            "id": str(row["id"]),
            "source": source,
            "target": target,
            "type": str(row["predicate"]),
            "direction": "directed",
            "status": str(row["status"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "metadata": {
                "epistemic_status": str(row["epistemic_status"]),
                "valid_from": row["valid_from"],
                "valid_until": row["valid_until"],
                "derived_by": row["derived_by"],
            },
        })

    memory_ids = sorted(node["id"] for node in nodes if node["type"] == "memory")
    if memory_ids:
        marks = ",".join("?" for _ in memory_ids)
        rows = connection.execute(
            f"SELECT id, subject_entity_id, object_entity_id, created_at, updated_at "
            f"FROM memory_records WHERE brain_id=? AND id IN ({marks})",
            (store.brain_id, *memory_ids),
        ).fetchall()
        for row in rows:
            memory_id = str(row["id"])
            for edge_type, target in (
                ("about_subject", row["subject_entity_id"]),
                ("about_object", row["object_entity_id"]),
            ):
                if target and str(target) in included:
                    edges.append({
                        "id": _edge_id(edge_type, memory_id, str(target)),
                        "source": memory_id,
                        "target": str(target),
                        "type": edge_type,
                        "direction": "directed",
                        "status": "active",
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                        "metadata": {},
                    })
        evidence_rows = connection.execute(
            f"SELECT me.memory_id, me.evidence_id, e.occurred_at, e.ingested_at "
            f"FROM memory_evidence me JOIN memory_records m ON m.id=me.memory_id "
            f"JOIN evidence_items e ON e.id=me.evidence_id "
            f"WHERE m.brain_id=? AND me.memory_id IN ({marks}) AND e.tombstoned_at IS NULL",
            (store.brain_id, *memory_ids),
        ).fetchall()
        for row in evidence_rows:
            source, target = str(row["memory_id"]), str(row["evidence_id"])
            if target in included:
                edges.append({
                    "id": _edge_id("supported_by", source, target),
                    "source": source,
                    "target": target,
                    "type": "supported_by",
                    "direction": "directed",
                    "status": "active",
                    "created_at": row["occurred_at"],
                    "updated_at": row["ingested_at"],
                    "metadata": {},
                })

    for node in nodes:
        node_id = str(node["id"])
        if node["type"] == "evidence":
            session_id = node.get("_session_id")
            if session_id and str(session_id) in included:
                edges.append({
                    "id": _edge_id("contains_evidence", str(session_id), node_id),
                    "source": str(session_id),
                    "target": node_id,
                    "type": "contains_evidence",
                    "direction": "directed",
                    "status": "active",
                    "created_at": node.get("created_at"),
                    "updated_at": node.get("updated_at"),
                    "metadata": {},
                })
        elif node["type"] == "document":
            for entity_id in node.get("_entity_ids", []):
                if entity_id in included:
                    edges.append({
                        "id": _edge_id("mentions", node_id, entity_id),
                        "source": node_id,
                        "target": entity_id,
                        "type": "mentions",
                        "direction": "directed",
                        "status": "active",
                        "created_at": node.get("created_at"),
                        "updated_at": node.get("updated_at"),
                        "metadata": {},
                    })
        elif node["type"] == "community":
            for member_id in node.get("_member_ids", []):
                if member_id in included:
                    edges.append({
                        "id": _edge_id("has_member", node_id, member_id),
                        "source": node_id,
                        "target": member_id,
                        "type": "has_member",
                        "direction": "directed",
                        "status": "active",
                        "created_at": node.get("created_at"),
                        "updated_at": node.get("updated_at"),
                        "metadata": {},
                    })

    session_ids = sorted(str(node["id"]) for node in nodes if node["type"] == "session")
    capability_entities: dict[str, str] = {}
    for node in nodes:
        if node["type"] != "entity" or node.get("domain") != "atlas-capabilities":
            continue
        label = str(node.get("label") or "").strip().lower()
        if label:
            capability_entities[label] = str(node["id"])
            capability_entities[label.replace("_", " ")] = str(node["id"])
    if session_ids and capability_entities:
        marks = ",".join("?" for _ in session_ids)
        work_rows = connection.execute(
            f"SELECT w.* FROM work_events w JOIN evidence_items ev ON ev.id=w.evidence_id "
            f"WHERE w.brain_id=? AND ev.tombstoned_at IS NULL "
            f"AND w.session_id IN ({marks}) ORDER BY w.occurred_at DESC LIMIT ?",
            (store.brain_id, *session_ids, edge_limit * 2),
        ).fetchall()
        for row in work_rows:
            metadata = _loads_mapping(row["metadata_json"])
            tool_name = str(metadata.get("tool_name") or "").strip().lower()
            target = capability_entities.get(tool_name) or capability_entities.get(
                tool_name.replace("_", " ")
            )
            source = str(row["session_id"] or "")
            if not target or source not in included or target not in included:
                continue
            edges.append({
                "id": _edge_id("used_tool", source, target, str(row["id"])),
                "source": source,
                "target": target,
                "type": "used_tool",
                "direction": "directed",
                "status": "active",
                "created_at": row["occurred_at"],
                "updated_at": row["occurred_at"],
                "metadata": {
                    "event_type": str(row["event_type"]),
                    "summary": _truncate(row["summary"], 240),
                },
            })

    deduped = {str(edge["id"]): edge for edge in edges}
    return sorted(deduped.values(), key=lambda edge: str(edge["id"]))[:edge_limit]


def build_graph_overview(
    store: CortexStore,
    *,
    projection: str = "growth",
    limit: int = 250,
    cursor: str | None = None,
    domain: str | None = None,
    node_types: Sequence[str] | None = None,
    status: str | None = None,
    retrieval_run_id: str | None = None,
) -> dict[str, Any]:
    """Build a bounded graph projection without returning source bodies."""
    projection = str(projection or "growth").strip().lower()
    if projection not in _PROJECTIONS:
        raise ValueError(f"unsupported cognitive graph projection: {projection}")
    limit = _bounded(limit, 250, 1, 500)
    offset = _decode_cursor(cursor)
    requested_types = {
        str(value).strip().lower()
        for value in (node_types or _NODE_TYPES)
        if str(value).strip()
    }
    unknown_types = requested_types - _NODE_TYPES
    if unknown_types:
        raise ValueError(f"unsupported cognitive node type: {sorted(unknown_types)[0]}")
    if projection == "communities":
        requested_types.add("community")

    fetch_limit = min(10_501, offset + limit + 1)
    with store.connect() as connection:
        nodes, community_rows = _collect_nodes(
            connection,
            store,
            fetch_limit=fetch_limit,
            node_types=requested_types,
            domain=(domain or "").strip() or None,
            status=(status or "").strip() or None,
        )

        answer_path_ids: set[str] | None = None
        if retrieval_run_id:
            run = connection.execute(
                "SELECT selected_ids_json FROM retrieval_runs WHERE id=? AND brain_id=?",
                (retrieval_run_id, store.brain_id),
            ).fetchone()
            if not run:
                raise LookupError("retrieval run was not found")
            answer_path_ids = {
                str(item) for item in _loads_list(run["selected_ids_json"])
            }
            if projection == "answer-path":
                nodes = [node for node in nodes if str(node["id"]) in answer_path_ids]

        nodes = _stratified_node_order(nodes, projection=projection)
        total_in_window = len(nodes)
        visible_nodes = nodes[offset : offset + limit]
        edges = _collect_edges(
            connection,
            store,
            visible_nodes,
            edge_limit=min(2_000, max(50, limit * 4)),
        )

    degree = Counter()
    for edge in edges:
        degree[str(edge["source"])] += 1
        degree[str(edge["target"])] += 1
    for node in visible_nodes:
        node["degree"] = degree[str(node["id"])]

    included = {str(node["id"]) for node in visible_nodes}
    communities = []
    for item in community_rows:
        visible_members = sorted(set(item["member_ids"]) & included)
        if visible_members or projection == "communities":
            communities.append({
                "id": item["id"],
                "label": item["label"],
                "level": item["level"],
                "parent_id": item["parent_id"],
                "domain": item["domain"],
                "status": item["status"],
                "member_count": len(item["member_ids"]),
                "visible_member_count": len(visible_members),
                "generated_at": item["generated_at"],
            })
    communities.sort(key=lambda item: (item["level"], item["label"], item["id"]))

    clean_nodes = [_clean_node(node) for node in visible_nodes]
    timestamps = sorted(
        str(node.get("updated_at") or node.get("created_at"))
        for node in clean_nodes
        if node.get("updated_at") or node.get("created_at")
    )
    has_more = offset + limit < total_in_window or total_in_window >= fetch_limit
    evidence_nodes = [node for node in clean_nodes if node["type"] == "evidence"]
    document_nodes = [node for node in clean_nodes if node["type"] == "document"]
    return {
        "version": GRAPH_DTO_VERSION,
        "generated_at": utc_now(),
        "projection": projection,
        "retrieval_run_id": retrieval_run_id,
        "layout_seed": stable_hash("cortex-layout", store.brain_id, projection)[:16],
        "nodes": clean_nodes,
        "edges": edges,
        "communities": communities,
        "timeline_window": {
            "start": timestamps[0] if timestamps else None,
            "end": timestamps[-1] if timestamps else None,
        },
        "facets": {
            "types": _facets(clean_nodes, "type"),
            "domains": _facets(clean_nodes, "domain"),
            "statuses": _facets(clean_nodes, "status"),
        },
        "next_cursor": _encode_cursor(offset + limit) if has_more else None,
        "redaction_summary": {
            "raw_evidence_bodies_hidden": len(evidence_nodes),
            "document_bodies_hidden": len(document_nodes),
            "nodes_omitted_by_limit": max(
                0, total_in_window - offset - len(clean_nodes)
            ),
        },
    }


def _evidence_detail(
    row: Mapping[str, Any], *, max_chars: int = 50_000
) -> dict[str, Any]:
    content = str(row["content"] or "")
    return {
        "id": str(row["id"]),
        "source_type": str(row["source_type"]),
        "source_locator": str(row["source_locator"]),
        "occurred_at": row["occurred_at"],
        "ingested_at": row["ingested_at"],
        "sensitivity": str(row["sensitivity"]),
        "retention_class": str(row["retention_class"]),
        "content": content[:max_chars],
        "content_truncated": len(content) > max_chars,
        "metadata": _loads_mapping(row["metadata_json"]),
    }


def _supporting_evidence(
    connection: Any,
    store: CortexStore,
    evidence_ids: Sequence[str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    ids = list(dict.fromkeys(str(item) for item in evidence_ids if item))[:limit]
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    rows = connection.execute(
        f"SELECT * FROM evidence_items WHERE brain_id=? AND tombstoned_at IS NULL "
        f"AND id IN ({marks}) ORDER BY occurred_at DESC, id",
        (store.brain_id, *ids),
    ).fetchall()
    return [_evidence_detail(row) for row in rows]


def build_node_detail(
    store: CortexStore,
    node_id: str,
    *,
    evidence_limit: int = 20,
) -> dict[str, Any]:
    """Return one selected node and bounded provenance from the same brain."""
    node_id = str(node_id or "").strip()
    if not node_id or len(node_id) > 256:
        raise ValueError("a valid cognitive node id is required")
    evidence_limit = _bounded(evidence_limit, 20, 1, 100)

    with store.connect() as connection:
        spaces, visibility = _space_maps(connection, store)
        node: dict[str, Any] | None = None
        content = ""
        metadata: dict[str, Any] = {}
        evidence_ids: list[str] = []

        row = connection.execute(
            "SELECT * FROM entities WHERE id=? AND brain_id=? AND deleted_at IS NULL",
            (node_id, store.brain_id),
        ).fetchone()
        if row:
            space_id = str(row["knowledge_space_id"])
            aliases = connection.execute(
                "SELECT alias, evidence_id FROM entity_aliases WHERE entity_id=? "
                "AND knowledge_space_id=? AND deleted_at IS NULL "
                "ORDER BY normalized_alias LIMIT 100",
                (node_id, space_id),
            ).fetchall()
            evidence_ids.extend(
                str(item["evidence_id"]) for item in aliases if item["evidence_id"]
            )
            node = _base_node(
                node_id=node_id,
                label=str(row["canonical_name"]),
                node_type="entity",
                domain=spaces.get(space_id, "unknown"),
                summary=str(row["description"] or ""),
                status="active",
                created_at=row["first_seen_at"],
                updated_at=row["last_seen_at"],
                privacy=visibility.get(space_id, "private"),
                badges=("private",) if visibility.get(space_id) == "private" else (),
                metadata={"entity_type": str(row["entity_type"])},
            )
            content = str(row["description"] or "")
            metadata["aliases"] = [str(item["alias"]) for item in aliases]

        if node is None:
            row = connection.execute(
                "SELECT * FROM memory_records WHERE id=? AND brain_id=? "
                "AND deleted_at IS NULL AND status IN ('active','disputed')",
                (node_id, store.brain_id),
            ).fetchone()
            if row:
                space_id = str(row["knowledge_space_id"])
                links = connection.execute(
                    "SELECT me.evidence_id FROM memory_evidence me "
                    "JOIN evidence_items e ON e.id=me.evidence_id "
                    "WHERE me.memory_id=? AND e.brain_id=? "
                    "AND e.tombstoned_at IS NULL ORDER BY me.evidence_id",
                    (node_id, store.brain_id),
                ).fetchall()
                evidence_ids.extend(str(item["evidence_id"]) for item in links)
                statement = str(row["canonical_statement"])
                badges = [str(row["status"])] if row["status"] != "active" else []
                if row["protected"]:
                    badges.append("protected")
                if evidence_ids:
                    badges.append("cited")
                node = _base_node(
                    node_id=node_id,
                    label=statement,
                    node_type="memory",
                    domain=spaces.get(space_id, "unknown"),
                    summary=statement,
                    status=str(row["status"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    privacy=visibility.get(space_id, "private"),
                    badges=badges,
                    metadata={
                        "memory_kind": str(row["kind"]),
                        "epistemic_status": str(row["epistemic_status"]),
                        "valid_from": row["valid_from"],
                        "valid_until": row["valid_until"],
                        "first_seen_at": row["first_seen_at"],
                        "last_confirmed_at": row["last_confirmed_at"],
                        "protected": bool(row["protected"]),
                    },
                )
                content = statement

        if node is None:
            row = connection.execute(
                "SELECT e.*, k.slug AS domain, k.visibility FROM evidence_items e "
                "JOIN knowledge_spaces k ON k.id=e.knowledge_space_id "
                "WHERE e.id=? AND e.brain_id=? AND e.tombstoned_at IS NULL",
                (node_id, store.brain_id),
            ).fetchone()
            if row:
                detail = _evidence_detail(row)
                node = _base_node(
                    node_id=node_id,
                    label=f"{str(row['source_type']).replace('_', ' ').title()} evidence",
                    node_type="evidence",
                    domain=str(row["domain"]),
                    summary="Selected source evidence",
                    status="active",
                    created_at=row["occurred_at"],
                    updated_at=row["ingested_at"],
                    privacy=str(row["sensitivity"]),
                    badges=("source", str(row["sensitivity"])),
                    metadata={
                        "source_type": str(row["source_type"]),
                        "retention_class": str(row["retention_class"]),
                    },
                )
                content = detail["content"]
                metadata.update({
                    "source_locator": detail["source_locator"],
                    "content_truncated": detail["content_truncated"],
                    "source_metadata": detail["metadata"],
                })

        if node is None:
            row = connection.execute(
                "SELECT * FROM sessions WHERE id=? AND brain_id=? AND state!='deleted'",
                (node_id, store.brain_id),
            ).fetchone()
            if row:
                evidence_rows = connection.execute(
                    "SELECT id FROM evidence_items WHERE brain_id=? AND session_id=? "
                    "AND tombstoned_at IS NULL ORDER BY occurred_at DESC LIMIT ?",
                    (store.brain_id, node_id, evidence_limit),
                ).fetchall()
                evidence_ids.extend(str(item["id"]) for item in evidence_rows)
                node = _base_node(
                    node_id=node_id,
                    label=str(row["title"] or f"Session {str(row['started_at'])[:10]}"),
                    node_type="session",
                    domain="personal",
                    summary=str(row["summary"] or "Conversation session"),
                    status=str(row["state"]),
                    created_at=row["started_at"],
                    updated_at=row["updated_at"],
                    privacy="private",
                    badges=("private",),
                    metadata={
                        "finalized_at": row["finalized_at"],
                        "workspace": row["workspace"],
                    },
                )
                content = str(row["summary"] or "")

        if node is None:
            row = connection.execute(
                "SELECT d.*, i.version, i.published_at, k.slug AS domain, k.visibility "
                "FROM graphrag_documents d JOIN graphrag_indexes i ON i.id=d.index_id "
                "JOIN knowledge_spaces k ON k.id=d.knowledge_space_id "
                "WHERE d.id=? AND d.brain_id=? AND i.state='active'",
                (node_id, store.brain_id),
            ).fetchone()
            if row:
                text = str(row["text"] or "")
                node = _base_node(
                    node_id=node_id,
                    label=str(row["title"]),
                    node_type="document",
                    domain=str(row["domain"]),
                    summary="Versioned knowledge-base document",
                    status="active",
                    created_at=row["published_at"],
                    updated_at=row["published_at"],
                    privacy=str(row["visibility"]),
                    badges=("verified", "cited"),
                    metadata={
                        "index_version": str(row["version"]),
                        "document_id": str(row["document_id"]),
                    },
                )
                content = text[:100_000]
                metadata.update({
                    "source_uri": row["source_uri"],
                    "content_truncated": len(text) > 100_000,
                    "source_metadata": _loads_mapping(row["metadata_json"]),
                })

        if node is None:
            row = connection.execute(
                "SELECT c.*, k.slug AS domain, k.visibility FROM communities c "
                "JOIN knowledge_spaces k ON k.id=c.knowledge_space_id "
                "WHERE c.id=? AND c.brain_id=? AND c.stale=0",
                (node_id, store.brain_id),
            ).fetchone()
            if row:
                member_ids = [str(item) for item in _loads_list(row["member_ids_json"])]
                node = _base_node(
                    node_id=node_id,
                    label=str(row["label"]),
                    node_type="community",
                    domain=str(row["domain"]),
                    summary=str(row["report"] or "Knowledge community"),
                    status="active",
                    created_at=row["generated_at"],
                    updated_at=row["generated_at"],
                    privacy=str(row["visibility"]),
                    badges=("community",),
                    metadata={
                        "level": int(row["level"]),
                        "member_count": len(member_ids),
                    },
                )
                content = str(row["report"] or "")
                metadata["member_ids"] = member_ids[:500]

        if node is None:
            raise LookupError("cognitive node was not found")

        supporting = _supporting_evidence(
            connection, store, evidence_ids, limit=evidence_limit
        )
        neighbor_nodes = _detail_neighbors(connection, store, node_id, limit=100)

    clean = _clean_node(node)
    clean["degree"] = len(neighbor_nodes["edges"])
    return {
        "version": DETAIL_DTO_VERSION,
        "generated_at": utc_now(),
        "node": clean,
        "content": content,
        "detail": metadata,
        "evidence": supporting,
        "neighbors": neighbor_nodes["nodes"],
        "edges": neighbor_nodes["edges"],
        "redaction_summary": {
            "evidence_limit": evidence_limit,
            "evidence_omitted": max(0, len(evidence_ids) - len(supporting)),
        },
    }


def _detail_neighbors(
    connection: Any,
    store: CortexStore,
    node_id: str,
    *,
    limit: int,
) -> dict[str, list[dict[str, Any]]]:
    """Return relation neighbors without loading any source bodies."""
    rows = connection.execute(
        "SELECT r.*, s.canonical_name AS subject_name, s.entity_type AS subject_type, "
        "o.canonical_name AS object_name, o.entity_type AS object_type, "
        "k.slug AS domain, k.visibility FROM relations r "
        "JOIN entities s ON s.id=r.subject_entity_id AND s.brain_id=r.brain_id "
        "JOIN entities o ON o.id=r.object_entity_id AND o.brain_id=r.brain_id "
        "JOIN knowledge_spaces k ON k.id=r.knowledge_space_id "
        "WHERE r.brain_id=? AND r.status='active' "
        "AND s.deleted_at IS NULL AND o.deleted_at IS NULL "
        "AND (r.subject_entity_id=? OR r.object_entity_id=?) "
        "ORDER BY r.updated_at DESC, r.id LIMIT ?",
        (store.brain_id, node_id, node_id, limit),
    ).fetchall()
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    for row in rows:
        source, target = str(row["subject_entity_id"]), str(row["object_entity_id"])
        for entity_id, name, entity_type in (
            (source, row["subject_name"], row["subject_type"]),
            (target, row["object_name"], row["object_type"]),
        ):
            if entity_id != node_id:
                nodes[entity_id] = {
                    "id": entity_id,
                    "label": _truncate(name, 120),
                    "type": "entity",
                    "domain": str(row["domain"]),
                    "status": "active",
                    "privacy": str(row["visibility"]),
                    "metadata": {"entity_type": str(entity_type)},
                }
        edges.append({
            "id": str(row["id"]),
            "source": source,
            "target": target,
            "type": str(row["predicate"]),
            "direction": "directed",
            "status": str(row["status"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "metadata": {"epistemic_status": str(row["epistemic_status"])},
        })
    return {
        "nodes": sorted(nodes.values(), key=lambda item: item["id"]),
        "edges": edges,
    }


def build_job_status(store: CortexStore, job_id: str) -> dict[str, Any]:
    """Return a customer-safe job status without leases or job input bodies."""
    job_id = str(job_id or "").strip()
    if not job_id or len(job_id) > 256:
        raise ValueError("a valid cognitive job id is required")
    with store.connect() as connection:
        row = connection.execute(
            "SELECT id, job_type, state, attempt, scheduled_at, started_at, completed_at, "
            "model, prompt_version, output_json, error, created_at, updated_at "
            "FROM cognitive_jobs WHERE id=? AND brain_id=?",
            (job_id, store.brain_id),
        ).fetchone()
    if not row:
        raise LookupError("cognitive job was not found")
    raw_output = _loads_mapping(row["output_json"])
    safe_output = {
        key: value
        for key, value in raw_output.items()
        if key in _SAFE_JOB_OUTPUT_FIELDS
        and isinstance(value, (str, int, float, bool, type(None)))
    }
    return {
        "version": JOB_DTO_VERSION,
        "job": {
            "id": str(row["id"]),
            "type": str(row["job_type"]),
            "status": str(row["state"]),
            "attempt": int(row["attempt"]),
            "scheduled_at": row["scheduled_at"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "model": row["model"],
            "prompt_version": row["prompt_version"],
            "output": safe_output,
            "error": "Memory maintenance failed" if row["error"] else None,
        },
    }


def enqueue_dream(store: CortexStore, *, source: str = "dashboard") -> dict[str, Any]:
    """Queue deterministic structural recovery maintenance.

    The legacy public endpoint keeps its ``dream`` name for compatibility,
    but this job never performs global semantic consolidation. It may rebuild
    derived graph communities from existing entities and relations. Model-
    backed work remains scoped to logical sessions finalized by the lifecycle
    hook.
    """
    requested_at = utc_now()
    job_id = store.enqueue_job(
        "dream",
        input_hash=stable_hash("manual-dream", requested_at, source),
        input_data={"source": source, "requested_at": requested_at},
        scheduled_at=requested_at,
    )
    return build_job_status(store, job_id)


def build_health(store: CortexStore) -> dict[str, Any]:
    """Build health telemetry without exposing brain identifiers or file paths."""
    base = store.health()
    with store.connect() as connection:
        job_rows = connection.execute(
            "SELECT state, COUNT(*) AS n FROM cognitive_jobs WHERE brain_id=? GROUP BY state",
            (store.brain_id,),
        ).fetchall()
        latest_dream = connection.execute(
            "SELECT id FROM cognitive_jobs WHERE brain_id=? AND job_type='dream' "
            "ORDER BY created_at DESC LIMIT 1",
            (store.brain_id,),
        ).fetchone()
        latest_index = connection.execute(
            "SELECT version, state, document_count, published_at, created_at "
            "FROM graphrag_indexes WHERE brain_id=? "
            "ORDER BY CASE state WHEN 'active' THEN 0 WHEN 'staged' THEN 1 ELSE 2 END, "
            "created_at DESC LIMIT 1",
            (store.brain_id,),
        ).fetchone()
        disputed = int(
            connection.execute(
                "SELECT COUNT(*) AS n FROM memory_records WHERE brain_id=? "
                "AND status='disputed' AND deleted_at IS NULL",
                (store.brain_id,),
            ).fetchone()["n"]
        )
        unprocessed = int(
            connection.execute(
                "SELECT COUNT(*) AS n FROM observations WHERE brain_id=? "
                "AND processing_state IN ('pending','retained-hot','needs-review')",
                (store.brain_id,),
            ).fetchone()["n"]
        )
    jobs = {str(row["state"]): int(row["n"]) for row in job_rows}
    failed = jobs.get("failed", 0) + jobs.get("dead_letter", 0)
    status = "degraded" if failed else str(base.get("status") or "healthy")
    return {
        "version": HEALTH_DTO_VERSION,
        "generated_at": utc_now(),
        "name": str(base.get("name") or "Atlas Cortex"),
        "status": status,
        "schema_version": base.get("schema_version"),
        "capabilities": {
            "full_text_search": bool(base.get("fts_available")),
            "typed_graph": True,
            "temporal_memory": True,
            "graphrag": latest_index is not None,
        },
        "counts": dict(base.get("counts") or {}),
        "jobs": {
            "by_status": jobs,
            "pending": int(base.get("pending_jobs") or 0),
            "failed": failed,
            "latest_dream_job_id": str(latest_dream["id"]) if latest_dream else None,
            "oldest_pending_age_seconds": int(
                base.get("oldest_pending_age_seconds") or 0
            ),
            "oldest_overdue_age_seconds": int(
                base.get("oldest_overdue_age_seconds") or 0
            ),
            "pending_boundaries": int(base.get("pending_session_boundaries") or 0),
            "expired_running": int(base.get("expired_running_jobs") or 0),
            "stale": bool(base.get("stale_pending_jobs")),
        },
        "quality": {
            "disputed_memories": disputed,
            "observations_awaiting_maintenance": unprocessed,
        },
        "graphrag": (
            {
                "version": str(latest_index["version"]),
                "status": str(latest_index["state"]),
                "document_count": int(latest_index["document_count"]),
                "published_at": latest_index["published_at"],
                "created_at": latest_index["created_at"],
            }
            if latest_index
            else None
        ),
    }
