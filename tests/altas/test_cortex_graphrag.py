from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

import pytest

import altas.cortex.graphrag as graphrag_module
from altas.control_plane.redaction import REDACTED
from altas.cortex.graphrag import (
    MICROSOFT_FORMAT,
    PORTABLE_FORMAT,
    GraphRAGArtifactError,
    GraphRAGIndexManager,
    GraphRAGProjectionError,
    GraphRAGSignatureError,
    canonical_manifest_payload,
    open_graphrag_manager,
)
from altas.cortex.store import CortexStore
from agent.secret_scope import reset_secret_scope, set_secret_scope


def _store(path: Path, owner: str) -> CortexStore:
    store = CortexStore(path, owner_customer_id=owner)
    store.initialize()
    return store


def _write_records(path: Path, records: Sequence[Mapping[str, Any]]) -> bytes:
    if path.suffix == ".jsonl":
        data = "".join(json.dumps(record) + "\n" for record in records).encode("utf-8")
    else:
        data = json.dumps(list(records)).encode("utf-8")
    path.write_bytes(data)
    return data


def _artifact(
    root: Path,
    version: str,
    marker: str,
    *,
    artifact_format: str = PORTABLE_FORMAT,
    signing_key: Any = None,
    key_id: str = "test-index-key",
) -> Path:
    artifact = root / version
    artifact.mkdir(parents=True)
    suffix = ".json" if artifact_format == MICROSOFT_FORMAT else ".jsonl"
    records = {
        "documents": [
            {
                "id": f"doc-{version}",
                "title": f"{marker} workflow",
                "text": f"Use the {marker} screen to complete the approved dealership workflow.",
                "source_uri": f"https://docs.example.test/{version}",
                "entity_ids": [f"workflow-{version}", f"screen-{version}"],
                "community_ids": [f"community-{version}"],
                "metadata": {"product_area": "dealership operations"},
            }
        ],
        "entities": [
            {
                "id": f"workflow-{version}",
                "name": f"{marker} workflow",
                "type": "workflow",
            },
            {
                "id": f"screen-{version}",
                "name": f"{marker} screen",
                "type": "screen",
            },
        ],
        "relationships": [
            {
                "id": f"relationship-{version}",
                "source": f"workflow-{version}",
                "target": f"screen-{version}",
                "predicate": "navigates_to",
            }
        ],
        "communities": [
            {
                "id": f"community-{version}",
                "title": f"{marker} operations",
                "entity_ids": [f"workflow-{version}", f"screen-{version}"],
            }
        ],
        "community_reports": [
            {
                "id": f"community-{version}",
                "title": f"{marker} operations",
                "summary": f"Approved guidance for {marker} operations.",
            }
        ],
    }
    files = []
    for role, rows in records.items():
        # Portable artifacts need only the four projection streams. Microsoft
        # output also exercises the separate community report projection.
        if artifact_format == PORTABLE_FORMAT and role == "community_reports":
            continue
        path = artifact / f"{role}{suffix}"
        data = _write_records(path, rows)
        files.append({
            "path": path.name,
            "role": role,
            "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data),
        })
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "format": artifact_format,
        "version": version,
        "knowledge_space": "tekion",
        "files": files,
    }
    if signing_key is not None:
        signature = signing_key.sign(canonical_manifest_payload(manifest))
        manifest["signature"] = {
            "algorithm": "ed25519",
            "key_id": key_id,
            "value": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        }
    (artifact / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True), encoding="utf-8"
    )
    return artifact


def _rewrite_role(
    artifact: Path,
    role: str,
    transform: Any,
) -> None:
    manifest_path = artifact / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["files"] if item.get("role") == role)
    path = artifact / entry["path"]
    if path.suffix == ".jsonl":
        records = [json.loads(line) for line in path.read_text().splitlines() if line]
    else:
        records = json.loads(path.read_text(encoding="utf-8"))
    data = _write_records(path, transform(records))
    entry["sha256"] = hashlib.sha256(data).hexdigest()
    entry["size"] = len(data)
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")


def _rename_role_as_parquet(artifact: Path, role: str) -> None:
    manifest_path = artifact / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    entry = next(item for item in manifest["files"] if item.get("role") == role)
    source = artifact / entry["path"]
    destination = source.with_suffix(".parquet")
    source.rename(destination)
    entry["path"] = destination.name
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")


def _graphrag_items(store: CortexStore, query: str) -> list[dict[str, Any]]:
    result = store.recall(query, allowed_spaces=("tekion",), max_items=20)
    return [item.to_dict() for item in result.items if item.kind == "graphrag"]


def test_portable_publish_projects_graph_and_is_query_compatible(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    artifact = _artifact(root, "2026.07.1", "desking")
    manager = GraphRAGIndexManager(store, root)

    staged = manager.import_artifact(artifact)
    assert staged.state == "staged"
    assert _graphrag_items(store, "desking screen") == []

    active = manager.publish("2026.07.1")
    matches = _graphrag_items(store, "desking screen")
    assert active.state == "active"
    assert len(matches) == 1
    assert matches[0]["metadata"]["index_version"] == "2026.07.1"
    assert matches[0]["evidence_locator"] == "https://docs.example.test/2026.07.1"
    assert manager.active_provenance() == {
        "knowledge_space": "tekion",
        "index_version": "2026.07.1",
        "manifest_hash": active.manifest_hash,
        "cache_namespace": f"tekion:2026.07.1:{active.manifest_hash[:16]}",
    }

    with store.connect() as connection:
        entity_count = connection.execute(
            "SELECT COUNT(*) AS n FROM entities WHERE brain_id=? AND deleted_at IS NULL",
            (store.brain_id,),
        ).fetchone()["n"]
        relation = connection.execute(
            "SELECT predicate, derived_by FROM relations WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()
        community_count = connection.execute(
            "SELECT COUNT(*) AS n FROM communities WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()["n"]
    assert entity_count == 2
    assert relation["predicate"] == "navigates_to"
    assert relation["derived_by"] == f"graphrag:{active.id}"
    assert community_count == 1


def test_microsoft_json_outputs_use_text_units_and_community_reports(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    artifact = _artifact(
        root, "ms-2026-07", "service lane", artifact_format=MICROSOFT_FORMAT
    )
    # Model current Microsoft output naming and ensure role inference works.
    manifest_path = artifact / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest["files"]:
        entry.pop("role")
        if entry["path"] == "documents.json":
            old_path = artifact / entry["path"]
            new_path = artifact / "create_final_text_units.json"
            old_path.rename(new_path)
            entry["path"] = new_path.name
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    manager = GraphRAGIndexManager(store, root)
    manager.import_artifact(artifact)
    manager.publish("ms-2026-07")

    matches = _graphrag_items(store, "service lane screen")
    assert [item["metadata"]["index_version"] for item in matches] == ["ms-2026-07"]
    with store.connect() as connection:
        report = connection.execute(
            "SELECT report FROM communities WHERE brain_id=?",
            (store.brain_id,),
        ).fetchone()["report"]
    assert "Approved guidance for service lane operations" in report
    global_result = store.recall(
        "major themes across approved service lane operations",
        allowed_spaces=("tekion",),
        max_items=20,
    )
    community = next(item for item in global_result.items if item.kind == "community")
    assert community.epistemic_status == "inferred"
    assert community.metadata["index_version"] == "ms-2026-07"
    assert "derived Tekion GraphRAG community report" in community.source_label


def test_publish_and_rollback_swap_one_active_pointer_and_projection(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    first = _artifact(root, "v1", "legacy alpha")
    second = _artifact(root, "v2", "current beta")
    manager = GraphRAGIndexManager(store, root)

    manager.import_artifact(first)
    with pytest.raises(LookupError, match="retained"):
        manager.rollback("v1")
    manager.publish("v1")
    manager.import_artifact(second)
    manager.publish("v2")
    assert _graphrag_items(store, "legacy alpha") == []
    assert (
        _graphrag_items(store, "current beta")[0]["metadata"]["index_version"] == "v2"
    )

    rolled_back = manager.rollback("v1")
    assert rolled_back.version == "v1"
    assert _graphrag_items(store, "current beta") == []
    assert (
        _graphrag_items(store, "legacy alpha")[0]["metadata"]["index_version"] == "v1"
    )
    assert sum(index.state == "active" for index in manager.list_indexes()) == 1


def test_corrupt_hash_and_manifest_path_traversal_are_rejected(tmp_path: Path) -> None:
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    corrupt = _artifact(root, "corrupt", "corrupt hash")
    manifest_path = corrupt / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"][0]["sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manager = GraphRAGIndexManager(store, root)

    with pytest.raises(GraphRAGArtifactError, match="hash mismatch"):
        manager.import_artifact(corrupt)

    traversal = _artifact(root, "traversal", "unsafe path")
    traversal_manifest = traversal / "manifest.json"
    manifest = json.loads(traversal_manifest.read_text(encoding="utf-8"))
    manifest["files"][0]["path"] = "../documents.jsonl"
    traversal_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(GraphRAGArtifactError, match="escapes"):
        manager.import_artifact(traversal)
    with pytest.raises(GraphRAGArtifactError, match="index root"):
        manager.import_artifact(tmp_path)
    assert manager.list_indexes() == ()


def test_artifact_is_revalidated_before_atomic_publish(tmp_path: Path) -> None:
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    artifact = _artifact(root, "v1", "stable")
    manager = GraphRAGIndexManager(store, root)
    manager.import_artifact(artifact)
    with (artifact / "documents.jsonl").open("ab") as target:
        target.write(b'{"id":"injected","text":"unverified"}\n')

    with pytest.raises(GraphRAGArtifactError, match="size does not match"):
        manager.publish("v1")
    assert manager.active_index() is None
    assert manager.get_index("v1").state == "staged"


def test_ed25519_signature_can_be_required_and_untrusted_keys_fail(
    tmp_path: Path,
) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    signed = _artifact(root, "signed-v1", "signed", signing_key=private_key)

    required = GraphRAGIndexManager(
        store,
        root,
        trusted_public_keys={"test-index-key": public_key},
        require_signature=True,
    )
    assert required.import_artifact(signed).version == "signed-v1"

    other_store = _store(tmp_path / "other.db", "customer-b")
    untrusted = GraphRAGIndexManager(
        other_store,
        root,
        trusted_public_keys={"different-key": public_key},
        require_signature=True,
    )
    with pytest.raises(GraphRAGSignatureError, match="not trusted"):
        untrusted.import_artifact(signed)


def test_managed_brain_requires_signature_without_ephemeral_mode_flag(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "managed.db", "managed:tenant-a:store-a:atlas-agent")
    root = tmp_path / "indexes"
    unsigned = _artifact(root, "unsigned-v1", "unsigned")

    manager = GraphRAGIndexManager(store, root, require_signature=False)

    assert manager.require_signature is True
    with pytest.raises(GraphRAGSignatureError, match="signature is required"):
        manager.import_artifact(unsigned)


def test_open_manager_reads_trust_keys_from_active_profile_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    encoded_key = base64.urlsafe_b64encode(public_key).decode("ascii").rstrip("=")
    store = _store(tmp_path / "managed.db", "managed:tenant-a:store-a:atlas-agent")
    root = tmp_path / "indexes"
    signed = _artifact(root, "signed-v1", "signed", signing_key=private_key)
    env_name = "ATLAS_TEST_GRAPHRAG_KEYS"
    # A multiplexed process may contain a different profile's global value.
    # The installed scope must remain authoritative.
    monkeypatch.setenv(env_name, json.dumps({"wrong-profile": encoded_key}))
    token = set_secret_scope({env_name: json.dumps({"test-index-key": encoded_key})})
    try:
        manager = open_graphrag_manager(
            store,
            SimpleNamespace(
                graphrag_index_root=root,
                graphrag_require_signature=False,
                graphrag_trusted_public_keys_env=env_name,
            ),
        )
    finally:
        reset_secret_scope(token)

    staged = manager.import_artifact(signed)
    active = manager.publish(staged.version)

    assert active.signed_by == "test-index-key"
    assert manager.active_provenance()["signed_by"] == "test-index-key"


def test_managed_startup_quarantines_legacy_unsigned_active_projection(
    tmp_path: Path,
) -> None:
    owner = "managed:tenant-a:store-a:atlas-agent"
    store = _store(tmp_path / "managed.db", owner)
    root = tmp_path / "indexes"
    unsigned = _artifact(root, "legacy-v1", "legacy unsigned")

    # Simulate an index published by an older build before managed signature
    # enforcement was derived from the persisted owner boundary.
    store.owner_customer_id = "local:legacy-publisher"
    legacy = GraphRAGIndexManager(store, root)
    legacy.import_artifact(unsigned)
    legacy.publish("legacy-v1")
    store.owner_customer_id = owner
    assert _graphrag_items(store, "legacy unsigned")

    with pytest.raises(GraphRAGSignatureError, match="signature is required"):
        GraphRAGIndexManager(store, root)

    assert _graphrag_items(store, "legacy unsigned") == []
    with store.connect() as connection:
        state = connection.execute(
            "SELECT state FROM graphrag_indexes WHERE brain_id=? AND version=?",
            (store.brain_id, "legacy-v1"),
        ).fetchone()["state"]
        relations = connection.execute(
            "SELECT COUNT(*) AS n FROM relations WHERE brain_id=? "
            "AND derived_by LIKE 'graphrag:%'",
            (store.brain_id,),
        ).fetchone()["n"]
        communities = connection.execute(
            "SELECT COUNT(*) AS n FROM communities WHERE brain_id=? "
            "AND algorithm_version LIKE 'graphrag:%'",
            (store.brain_id,),
        ).fetchone()["n"]
        live_entities = connection.execute(
            "SELECT COUNT(*) AS n FROM entities WHERE brain_id=? "
            'AND deleted_at IS NULL AND metadata_json LIKE \'%"source":"graphrag"%\'',
            (store.brain_id,),
        ).fetchone()["n"]
    assert state == "quarantined"
    assert relations == 0
    assert communities == 0
    assert live_entities == 0


def test_startup_quarantines_active_projection_when_artifact_changes(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    artifact = _artifact(root, "v1", "attested")
    manager = GraphRAGIndexManager(store, root)
    manager.import_artifact(artifact)
    manager.publish("v1")
    assert _graphrag_items(store, "attested")

    with (artifact / "documents.jsonl").open("ab") as target:
        target.write(b'{"id":"untrusted","text":"changed after publication"}\n')

    with pytest.raises(GraphRAGArtifactError, match="size does not match"):
        GraphRAGIndexManager(store, root)

    assert _graphrag_items(store, "attested") == []
    assert manager.get_index("v1").state == "quarantined"
    with store.connect() as connection:
        audit = connection.execute(
            "SELECT action, outcome, details_json FROM cortex_audit "
            "WHERE brain_id=? AND action='graphrag.quarantine' "
            "ORDER BY created_at DESC LIMIT 1",
            (store.brain_id,),
        ).fetchone()
    assert audit["outcome"] == "failed"
    assert json.loads(audit["details_json"])["reason"] == "GraphRAGArtifactError"


def test_index_state_and_retrieval_are_brain_scoped(tmp_path: Path) -> None:
    root = tmp_path / "shared-artifact-root"
    artifact = _artifact(root, "shared-v1", "shared manual")
    first_store = _store(tmp_path / "profile-a" / "cortex.db", "customer-a")
    second_store = _store(tmp_path / "profile-b" / "cortex.db", "customer-b")
    first = GraphRAGIndexManager(first_store, root)
    second = GraphRAGIndexManager(second_store, root)

    first.import_artifact(artifact)
    first.publish("shared-v1")
    assert first.active_index() is not None
    assert second.active_index() is None
    assert second.list_indexes() == ()
    assert _graphrag_items(second_store, "shared manual") == []

    second.import_artifact(artifact)
    second.publish("shared-v1")
    assert first.active_index().id != second.active_index().id
    assert (
        _graphrag_items(first_store, "shared manual")[0]["metadata"]["index_version"]
        == "shared-v1"
    )
    assert (
        _graphrag_items(second_store, "shared manual")[0]["metadata"]["index_version"]
        == "shared-v1"
    )


def test_import_parses_only_the_private_verified_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    artifact = _artifact(root, "snapshot-v1", "verified original")
    manager = GraphRAGIndexManager(store, root)
    original_loader = manager._load_bundle

    def mutate_source_after_snapshot(snapshot: Path, manifest: Any) -> Any:
        assert snapshot != artifact
        (artifact / "documents.jsonl").write_text(
            '{"id":"malicious","title":"swapped","text":"unverified bytes"}\n',
            encoding="utf-8",
        )
        return original_loader(snapshot, manifest)

    monkeypatch.setattr(manager, "_load_bundle", mutate_source_after_snapshot)
    staged = manager.import_artifact(artifact)

    with store.connect() as connection:
        row = connection.execute(
            "SELECT title, text FROM graphrag_documents WHERE index_id=?",
            (staged.id,),
        ).fetchone()
    assert row["title"] == "verified original workflow"
    assert "verified original screen" in row["text"]
    assert "unverified bytes" not in row["text"]


def test_publish_quarantines_same_count_staged_document_tampering(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    artifact = _artifact(root, "staged-v1", "staged safe")
    manager = GraphRAGIndexManager(store, root)
    staged = manager.import_artifact(artifact)
    with store.transaction() as connection:
        connection.execute(
            "UPDATE graphrag_documents SET text='same count poisoned text' "
            "WHERE index_id=?",
            (staged.id,),
        )

    with pytest.raises(GraphRAGProjectionError, match="document projection digest"):
        manager.publish(staged.version)

    assert manager.get_index(staged.version).state == "quarantined"
    assert manager.active_index() is None
    with pytest.raises(LookupError, match="staged index version"):
        manager.publish(staged.version)


@pytest.mark.parametrize(
    "tamper_sql",
    [
        "UPDATE graphrag_documents SET title=title || ' poisoned'",
        "UPDATE graphrag_documents SET text=text || ' poisoned'",
        "UPDATE entities SET canonical_name=canonical_name || ' poisoned' "
        "WHERE metadata_json LIKE '%graphrag%'",
        "UPDATE graphrag_fts SET content=content || ' poisoned'",
        "UPDATE entity_fts SET content=content || ' poisoned'",
        "UPDATE communities SET label=label || ' poisoned' "
        "WHERE algorithm_version LIKE 'graphrag:%'",
    ],
)
def test_startup_quarantines_same_count_projection_tampering(
    tmp_path: Path, tamper_sql: str
) -> None:
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    artifact = _artifact(root, "active-v1", "attested safe")
    manager = GraphRAGIndexManager(store, root)
    manager.import_artifact(artifact)
    manager.publish("active-v1")
    with store.transaction() as connection:
        connection.execute(tamper_sql)

    with pytest.raises(GraphRAGProjectionError, match="projection digest mismatch"):
        GraphRAGIndexManager(store, root)

    assert manager.get_index("active-v1").state == "quarantined"
    assert _graphrag_items(store, "attested safe") == []


def test_active_projection_without_attestation_fails_closed_and_purges_entities(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    artifact = _artifact(root, "legacy-digest-v1", "legacy verified")
    manager = GraphRAGIndexManager(store, root)
    active = manager.import_artifact(artifact)
    manager.publish(active.version)
    with store.transaction() as connection:
        connection.execute(
            "DELETE FROM graphrag_projection_attestations WHERE index_id=?",
            (active.id,),
        )
        connection.execute(
            "UPDATE entities SET metadata_json=?, visibility='poisoned' "
            "WHERE metadata_json LIKE '%graphrag%'",
            (json.dumps({"source": "manual"}),),
        )
        connection.execute(
            "UPDATE entity_aliases SET alias='legacy poisoned alias', "
            "normalized_alias='legacy poisoned alias'"
        )
        connection.execute("UPDATE relations SET derived_by='manual'")
        connection.execute("UPDATE communities SET algorithm_version='manual'")

    with pytest.raises(GraphRAGProjectionError, match="attestation is missing"):
        GraphRAGIndexManager(store, root)

    assert manager.active_index() is None
    assert manager.get_index(active.version).state == "quarantined"
    assert _graphrag_items(store, "legacy verified") == []
    with store.connect() as connection:
        entity_rows = connection.execute(
            "SELECT deleted_at, visibility, metadata_json FROM entities "
            "WHERE brain_id=?",
            (store.brain_id,),
        ).fetchall()
        alias_count = connection.execute(
            "SELECT COUNT(*) AS n FROM entity_aliases"
        ).fetchone()["n"]
        relation_count = connection.execute(
            "SELECT COUNT(*) AS n FROM relations"
        ).fetchone()["n"]
        community_count = connection.execute(
            "SELECT COUNT(*) AS n FROM communities"
        ).fetchone()["n"]
    assert entity_rows
    assert all(row["deleted_at"] for row in entity_rows)
    assert all(row["visibility"] == "poisoned" for row in entity_rows)
    assert all(
        json.loads(row["metadata_json"])["source"] == "manual" for row in entity_rows
    )
    assert alias_count == 0
    assert relation_count == 0
    assert community_count == 0


def test_retained_projection_without_attestation_cannot_be_rolled_back(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    first = _artifact(root, "retained-v1", "retained safe")
    second = _artifact(root, "current-v2", "current safe")
    manager = GraphRAGIndexManager(store, root)
    retained = manager.import_artifact(first)
    manager.publish(retained.version)
    manager.import_artifact(second)
    manager.publish("current-v2")
    with store.transaction() as connection:
        connection.execute(
            "DELETE FROM graphrag_projection_attestations WHERE index_id=?",
            (retained.id,),
        )

    with pytest.raises(GraphRAGProjectionError, match="attestation is missing"):
        manager.rollback(retained.version)

    assert manager.get_index(retained.version).state == "quarantined"
    assert manager.active_index().version == "current-v2"
    assert {
        item["metadata"]["index_version"]
        for item in _graphrag_items(store, "current safe")
    } == {"current-v2"}


def test_primary_graphrag_fields_and_fts_are_sanitized_before_persistence(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    artifact = _artifact(root, "redacted-v1", "redacted")
    secret = "sk_live_" + "s" * 24

    def poison_documents(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        records[0].update({
            "title": f"Document {secret}",
            "text": f"Body contains {secret}",
            "source_uri": f"https://docs.example.test/?token={secret}",
        })
        return records

    def poison_entities(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        records[0].update({
            "name": f"Entity {secret}",
            "description": f"Description {secret}",
            "aliases": [f"Alias {secret}"],
        })
        return records

    def poison_communities(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        records[0].update({
            "title": f"Community {secret}",
            "report": f"Report {secret}",
        })
        return records

    _rewrite_role(artifact, "documents", poison_documents)
    _rewrite_role(artifact, "entities", poison_entities)
    _rewrite_role(artifact, "communities", poison_communities)
    manager = GraphRAGIndexManager(store, root)
    manager.import_artifact(artifact)
    manager.publish("redacted-v1")

    with store.connect() as connection:
        persisted = {
            "documents": [
                dict(row)
                for row in connection.execute(
                    "SELECT title, text, source_uri FROM graphrag_documents"
                )
            ],
            "entities": [
                dict(row)
                for row in connection.execute(
                    "SELECT canonical_name, description FROM entities"
                )
            ],
            "aliases": [
                dict(row)
                for row in connection.execute("SELECT alias FROM entity_aliases")
            ],
            "communities": [
                dict(row)
                for row in connection.execute("SELECT label, report FROM communities")
            ],
            "document_fts": [
                dict(row)
                for row in connection.execute("SELECT content FROM graphrag_fts")
            ],
            "entity_fts": [
                dict(row)
                for row in connection.execute("SELECT content FROM entity_fts")
            ],
        }
    serialized = json.dumps(persisted)
    assert secret not in serialized
    assert REDACTED in serialized


def test_aggregate_artifact_byte_budget_is_enforced_before_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    artifact = _artifact(root, "oversized-v1", "oversized")
    monkeypatch.setattr(graphrag_module, "_MAX_BUNDLE_BYTES", 64)
    manager = GraphRAGIndexManager(store, root)

    with pytest.raises(GraphRAGArtifactError, match="aggregate byte limit"):
        manager.import_artifact(artifact)
    assert manager.list_indexes() == ()


def test_aggregate_record_budget_is_enforced_across_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    artifact = _artifact(root, "too-many-rows-v1", "too many rows")
    monkeypatch.setattr(graphrag_module, "_MAX_BUNDLE_RECORDS", 3)
    manager = GraphRAGIndexManager(store, root)

    with pytest.raises(GraphRAGArtifactError, match="aggregate record limit"):
        manager.import_artifact(artifact)
    assert manager.list_indexes() == ()


@pytest.mark.parametrize(
    ("stats", "limit_name", "limit", "message"),
    [
        ((4, 4, 4), "_MAX_BUNDLE_RECORDS", 3, "aggregate record limit"),
        ((1, 4, 4), "_MAX_BUNDLE_CELLS", 3, "materialized cell limit"),
        (
            (1, 1, 65),
            "_MAX_BUNDLE_MATERIALIZED_BYTES",
            64,
            "materialized byte limit",
        ),
    ],
)
def test_parquet_rows_cells_and_content_are_bounded_before_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stats: tuple[int, int, int],
    limit_name: str,
    limit: int,
    message: str,
) -> None:
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    artifact = _artifact(root, "bounded-parquet-v1", "bounded parquet")
    _rename_role_as_parquet(artifact, "documents")
    monkeypatch.setattr(graphrag_module, limit_name, limit)
    monkeypatch.setattr(
        GraphRAGIndexManager,
        "_parquet_materialization",
        staticmethod(lambda _path: stats),
    )
    manager = GraphRAGIndexManager(store, root)

    with pytest.raises(GraphRAGArtifactError, match=message):
        manager.import_artifact(artifact)
    assert manager.list_indexes() == ()


def test_nested_parquet_values_are_rejected_before_python_materialization() -> None:
    arrow_types = SimpleNamespace(
        is_list=lambda value: value.kind == "list",
        is_large_list=lambda value: False,
        is_fixed_size_list=lambda value: False,
        is_map=lambda value: False,
        is_struct=lambda value: False,
        is_union=lambda value: False,
    )

    with pytest.raises(GraphRAGArtifactError, match="nested or repeated"):
        graphrag_module._reject_nested_arrow_type(
            SimpleNamespace(kind="list"), SimpleNamespace(types=arrow_types)
        )


def test_persisted_attestation_is_stable_across_fts_capability_changes(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    artifact = _artifact(root, "fts-stable-v1", "fts stable")
    manager = GraphRAGIndexManager(store, root)
    active = manager.import_artifact(artifact)
    manager.publish(active.version)
    with store.connect() as connection:
        before = tuple(
            connection.execute(
                "SELECT document_digest, projection_digest "
                "FROM graphrag_projection_attestations WHERE index_id=?",
                (active.id,),
            ).fetchone()
        )

    store._fts_available = False
    without_fts = GraphRAGIndexManager(store, root)
    assert without_fts.active_index().version == active.version
    with store.connect() as connection:
        after = tuple(
            connection.execute(
                "SELECT document_digest, projection_digest "
                "FROM graphrag_projection_attestations WHERE index_id=?",
                (active.id,),
            ).fetchone()
        )
    assert after == before

    store._fts_available = True
    with_fts_again = GraphRAGIndexManager(store, root)
    assert with_fts_again.active_index().version == active.version


def test_first_fts_enablement_builds_a_live_baseline_from_core_attestation(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    artifact = _artifact(root, "fts-first-enable-v1", "fts first enable")
    store._fts_available = False
    manager = GraphRAGIndexManager(store, root)
    active = manager.import_artifact(artifact)
    manager.publish(active.version)
    with store.connect() as connection:
        before = connection.execute(
            "SELECT document_fts_digest, projection_fts_digest "
            "FROM graphrag_projection_attestations WHERE index_id=?",
            (active.id,),
        ).fetchone()
    assert before["document_fts_digest"] is None
    assert before["projection_fts_digest"] is None

    store._fts_available = True
    accelerated = GraphRAGIndexManager(store, root)

    assert accelerated.active_index().version == active.version
    with store.connect() as connection:
        after = connection.execute(
            "SELECT document_fts_digest, projection_fts_digest "
            "FROM graphrag_projection_attestations WHERE index_id=?",
            (active.id,),
        ).fetchone()
        document_fts_count = connection.execute(
            "SELECT COUNT(*) AS n FROM graphrag_fts"
        ).fetchone()["n"]
        entity_fts_count = connection.execute(
            "SELECT COUNT(*) AS n FROM entity_fts"
        ).fetchone()["n"]
    assert len(after["document_fts_digest"]) == 64
    assert len(after["projection_fts_digest"]) == 64
    assert document_fts_count > 0
    assert entity_fts_count > 0

    with store.transaction() as connection:
        connection.execute(
            "UPDATE graphrag_fts SET content=content || ' poisoned after baseline'"
        )
    with pytest.raises(GraphRAGProjectionError, match="projection digest mismatch"):
        GraphRAGIndexManager(store, root)
    assert manager.get_index(active.version).state == "quarantined"


def test_stale_private_snapshots_are_reaped_without_following_symlinks(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "cortex.db", "customer-a")
    root = tmp_path / "indexes"
    stale = root / ".atlas-graphrag-verify-stale"
    stale.mkdir(parents=True)
    stale_file = stale / "verified.json"
    stale_file.write_text("{}", encoding="utf-8")
    stale_file.chmod(0o400)
    stale.chmod(0o500)
    os.utime(stale, (1, 1))
    live = root / f".atlas-graphrag-verify-{os.getpid()}-live"
    live.mkdir()
    os.utime(live, (1, 1))
    outside = tmp_path / "must-survive"
    outside.mkdir()
    marker = outside / "marker.txt"
    marker.write_text("safe", encoding="utf-8")
    symlink = root / ".atlas-graphrag-verify-symlink"
    symlink.symlink_to(outside, target_is_directory=True)

    GraphRAGIndexManager(store, root)

    assert not stale.exists()
    assert live.is_dir()
    assert symlink.is_symlink()
    assert marker.read_text(encoding="utf-8") == "safe"
