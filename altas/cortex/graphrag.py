"""Versioned GraphRAG artifact ingestion for Atlas Cortex.

The adapter deliberately keeps Microsoft GraphRAG outside the conversational
runtime.  An approved offline build produces an immutable artifact, this
module verifies and projects it, and Cortex retrieval reads only the database
version selected by the active pointer.

Portable artifacts require no optional dependency. Microsoft GraphRAG JSON
outputs work directly; Parquet outputs require ``pyarrow`` so row counts and
uncompressed sizes can be bounded before materialization.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence

from altas.control_plane.redaction import sanitize_for_storage

from .store import CortexStore, new_id, stable_hash, utc_now
from .tekion_taxonomy import normalize_entity_type, normalize_predicate


PORTABLE_FORMAT = "atlas-cortex-graphrag-jsonl"
MICROSOFT_FORMAT = "microsoft-graphrag"
MANIFEST_SCHEMA_VERSION = 1

_FORMATS = {PORTABLE_FORMAT, MICROSOFT_FORMAT}
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
_MAX_MANIFEST_BYTES = 2 * 1024 * 1024
_MAX_ARTIFACT_FILE_BYTES = 512 * 1024 * 1024
_MAX_BUNDLE_BYTES = 128 * 1024 * 1024
_MAX_BUNDLE_MATERIALIZED_BYTES = 256 * 1024 * 1024
_MAX_BUNDLE_CELLS = 5_000_000
_MAX_BUNDLE_PYTHON_BYTES = 512 * 1024 * 1024
_MAX_RECORDS_PER_FILE = 500_000
_MAX_BUNDLE_RECORDS = 250_000
_SNAPSHOT_PREFIX = ".atlas-graphrag-verify-"
_STALE_SNAPSHOT_AGE_SECONDS = 60 * 60
_PROJECTION_DIGEST_VERSION = 1
_DOCUMENT_DIGEST_DOMAIN = "atlas.cortex.graphrag.documents.v1"
_PROJECTION_DIGEST_DOMAIN = "atlas.cortex.graphrag.projection.v1"
_KNOWN_ROLES = {
    "documents",
    "text_units",
    "entities",
    "relationships",
    "communities",
    "community_reports",
}


class GraphRAGArtifactError(ValueError):
    """The artifact is corrupt, unsafe, or incompatible."""


class GraphRAGSignatureError(GraphRAGArtifactError):
    """The artifact signature is missing, untrusted, or invalid."""


class GraphRAGProjectionError(GraphRAGArtifactError):
    """A persisted projection no longer matches its verified artifact."""


@dataclass(frozen=True)
class ManifestFile:
    path: str
    sha256: str
    role: str
    size: int


@dataclass(frozen=True)
class ArtifactManifest:
    version: str
    artifact_format: str
    knowledge_space: str
    files: tuple[ManifestFile, ...]
    manifest_hash: str
    signed_by: str | None
    raw: Mapping[str, Any] = field(repr=False)


@dataclass(frozen=True)
class GraphRAGIndex:
    id: str
    version: str
    state: str
    path: str
    manifest_hash: str
    signed_by: str | None
    document_count: int
    published_at: str | None
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _Document:
    external_id: str
    title: str
    text: str
    source_uri: str | None
    entity_ids: tuple[str, ...]
    community_ids: tuple[str, ...]
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class _Entity:
    external_id: str
    name: str
    entity_type: str
    description: str
    aliases: tuple[str, ...]
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class _Relationship:
    external_id: str
    source: str
    target: str
    predicate: str
    weight: float


@dataclass(frozen=True)
class _Community:
    external_id: str
    label: str
    level: int
    parent: str | None
    report: str
    entity_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Bundle:
    documents: tuple[_Document, ...]
    entities: tuple[_Entity, ...]
    relationships: tuple[_Relationship, ...]
    communities: tuple[_Community, ...]


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def canonical_manifest_payload(manifest: Mapping[str, Any]) -> bytes:
    """Return the bytes covered by an artifact's detached signature."""
    unsigned = dict(manifest)
    unsigned.pop("signature", None)
    return _canonical_json(unsigned).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value: Any, *, limit: int = 2_000_000) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list | tuple | set):
        return list(value)
    if hasattr(value, "tolist"):
        converted = value.tolist()
        return converted if isinstance(converted, list) else [converted]
    return [value]


def _string_list(value: Any) -> tuple[str, ...]:
    result: list[str] = []
    for item in _list(value):
        text = _text(item, limit=500)
        if text and text not in result:
            result.append(text)
    return tuple(result[:10_000])


def _sanitized_text(value: Any, *, limit: int) -> str:
    return _text(sanitize_for_storage(_text(value, limit=limit)), limit=limit)


def _sanitized_string_list(value: Any) -> tuple[str, ...]:
    result: list[str] = []
    for raw in _list(value):
        item = _sanitized_text(raw, limit=500)
        if item and item not in result:
            result.append(item)
    return tuple(result[:10_000])


def _decoded_json(value: Any) -> Any:
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"invalid_json": str(value or "")}


def _canonical_digest(domain: str, payload: Any) -> str:
    framed = {"domain": domain, "payload": payload}
    return hashlib.sha256(_canonical_json(framed).encode("utf-8")).hexdigest()


def _metadata(row: Mapping[str, Any], *extra_keys: str) -> Mapping[str, Any]:
    value = row.get("metadata")
    result = dict(value) if isinstance(value, Mapping) else {}
    for key in extra_keys:
        if key in row and row[key] not in (None, "", [], {}):
            result[key] = _json_safe(row[key])
    return sanitize_for_storage(result)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(child) for child in value]
    if hasattr(value, "tolist"):
        return _json_safe(value.tolist())
    return str(value)


def _reject_nested_arrow_type(value_type: Any, arrow: Any) -> None:
    """Reject nested/repeated Parquet values before Python materialization."""
    arrow_types = arrow.types
    nested_checks = (
        arrow_types.is_list,
        arrow_types.is_large_list,
        arrow_types.is_fixed_size_list,
        arrow_types.is_map,
        arrow_types.is_struct,
        arrow_types.is_union,
    )
    optional_nested_checks = tuple(
        check
        for name in ("is_list_view", "is_large_list_view")
        if (check := getattr(arrow_types, name, None)) is not None
    )
    if any(check(value_type) for check in (*nested_checks, *optional_nested_checks)):
        raise GraphRAGArtifactError(
            "GraphRAG Parquet nested or repeated values are not supported"
        )


def _arrow_cell_count(array: Any, arrow: Any) -> int:
    _reject_nested_arrow_type(array.type, arrow)
    return len(array)


def _record_id(row: Mapping[str, Any], *, fallback: str = "") -> str:
    return _text(
        row.get("id")
        or row.get("document_id")
        or row.get("human_readable_id")
        or row.get("community")
        or fallback,
        limit=500,
    )


def _role_for_path(path: str) -> str:
    stem = Path(path).stem.lower().replace("create_final_", "")
    aliases = {
        "document": "documents",
        "text_unit": "text_units",
        "entity": "entities",
        "relationship": "relationships",
        "community": "communities",
        "community_report": "community_reports",
    }
    return aliases.get(stem, stem if stem in _KNOWN_ROLES else "support")


def _safe_relative_path(raw: Any) -> PurePosixPath:
    value = str(raw or "")
    if not value or "\\" in value or "\x00" in value:
        raise GraphRAGArtifactError(
            "artifact file paths must be portable relative paths"
        )
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise GraphRAGArtifactError("artifact file path escapes the artifact directory")
    return path


def _decode_base64(value: str) -> bytes:
    try:
        padded = value + "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError, binascii.Error) as exc:
        raise GraphRAGSignatureError("artifact signature is not valid base64") from exc


def _verify_ed25519_signature(
    manifest: Mapping[str, Any],
    trusted_public_keys: Mapping[str, bytes | str],
    *,
    required: bool,
) -> str | None:
    signature = manifest.get("signature")
    if signature is None:
        if required:
            raise GraphRAGSignatureError("artifact signature is required")
        return None
    if not isinstance(signature, Mapping):
        raise GraphRAGSignatureError("artifact signature must be an object")
    if signature.get("algorithm") != "ed25519":
        raise GraphRAGSignatureError("only Ed25519 artifact signatures are accepted")
    key_id = _text(signature.get("key_id"), limit=200)
    encoded = _text(signature.get("value"), limit=1_000)
    if not key_id or not encoded or key_id not in trusted_public_keys:
        raise GraphRAGSignatureError("artifact signature key is not trusted")

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:  # pragma: no cover - core currently pins cryptography
        raise GraphRAGSignatureError(
            "cryptography is required to validate a signed GraphRAG artifact"
        ) from exc

    supplied = trusted_public_keys[key_id]
    key_bytes = supplied if isinstance(supplied, bytes) else supplied.encode("utf-8")
    try:
        if key_bytes.lstrip().startswith(b"-----BEGIN"):
            public_key = serialization.load_pem_public_key(key_bytes)
            if not isinstance(public_key, Ed25519PublicKey):
                raise GraphRAGSignatureError("trusted artifact key is not Ed25519")
        else:
            if isinstance(supplied, str):
                key_bytes = _decode_base64(supplied)
            public_key = Ed25519PublicKey.from_public_bytes(key_bytes)
        public_key.verify(_decode_base64(encoded), canonical_manifest_payload(manifest))
    except GraphRAGSignatureError:
        raise
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise GraphRAGSignatureError("artifact signature validation failed") from exc
    return key_id


class GraphRAGIndexManager:
    """Validate, stage, publish, and roll back one brain's GraphRAG indexes."""

    def __init__(
        self,
        store: CortexStore,
        index_root: str | Path,
        *,
        trusted_public_keys: Mapping[str, bytes | str] | None = None,
        require_signature: bool = False,
    ) -> None:
        if not store.brain_id:
            raise RuntimeError(
                "CortexStore.initialize() must run before GraphRAG setup"
            )
        self.store = store
        self.index_root = Path(index_root).expanduser()
        self._lexical_index_root = Path(os.path.abspath(self.index_root))
        self.trusted_public_keys = dict(trusted_public_keys or {})
        # A managed brain is the authorization boundary.  Do not trust an
        # ephemeral process flag to decide whether shared customer knowledge
        # must be signed: profile workers intentionally omit such flags.
        self.require_signature = bool(require_signature) or str(
            store.owner_customer_id
        ).startswith("managed:")
        self._initialize_root()
        self._ensure_attestation_schema()
        self._revalidate_active_index()

    def _revalidate_active_index(self) -> None:
        """Re-attest the active artifact before its projection can be recalled.

        Older databases may contain a projection activated before signature
        enforcement was enabled.  A failed startup attestation atomically
        removes that projection from every retrieval channel and records the
        index as quarantined before surfacing the validation error.
        """
        active = self.active_index()
        if active is None:
            return
        verified_bundle: _Bundle | None = None
        try:
            with self._verified_snapshot(active.path) as (snapshot, manifest):
                if (
                    manifest.version != active.version
                    or manifest.manifest_hash != active.manifest_hash
                    or (active.signed_by and manifest.signed_by != active.signed_by)
                ):
                    raise GraphRAGArtifactError(
                        "active GraphRAG manifest no longer matches its index"
                    )
                bundle = self._load_bundle(snapshot, manifest)
                verified_bundle = bundle
                if len(bundle.documents) != active.document_count:
                    raise GraphRAGProjectionError(
                        "active GraphRAG document projection no longer matches its artifact"
                    )
                with self.store.transaction() as connection:
                    current = connection.execute(
                        "SELECT * FROM graphrag_indexes WHERE id=? AND brain_id=? "
                        "AND state='active'",
                        (active.id, self.store.brain_id),
                    ).fetchone()
                    if not current:
                        return
                    space_id = self.store.space_id("tekion", connection=connection)
                    expected_document_digest = self._expected_document_digest(
                        bundle, index_id=active.id, space_id=space_id
                    )
                    attestation = self._attestation(connection, active.id)
                    if attestation is None or not attestation["projection_digest"]:
                        # Mutable provenance cannot establish which legacy graph
                        # rows are safe to preserve.  Fail closed instead of
                        # blessing an unattested active projection.
                        raise GraphRAGProjectionError(
                            "active GraphRAG projection attestation is missing"
                        )
                    persisted_document_digest = self._persisted_document_digest(
                        connection, index_id=active.id
                    )
                    persisted_projection_digest = self._persisted_projection_digest(
                        connection, current
                    )
                    (
                        expected_projection_digest,
                        expected_live_projection_digest,
                    ) = self._expected_projection_digests(connection, current, bundle)
                    valid = (
                        int(attestation["digest_version"]) == _PROJECTION_DIGEST_VERSION
                        and str(attestation["brain_id"]) == self.store.brain_id
                        and hmac.compare_digest(
                            str(attestation["document_digest"]),
                            expected_document_digest,
                        )
                        and hmac.compare_digest(
                            persisted_document_digest,
                            expected_document_digest,
                        )
                        and hmac.compare_digest(
                            str(attestation["projection_digest"]),
                            persisted_projection_digest,
                        )
                        and hmac.compare_digest(
                            str(attestation["projection_digest"]),
                            expected_projection_digest,
                        )
                    )
                    if not valid:
                        raise GraphRAGProjectionError(
                            "active GraphRAG persisted projection digest mismatch"
                        )
                    if self.store._fts_available:
                        expected_live_document_digest = self._expected_document_digest(
                            bundle,
                            index_id=active.id,
                            space_id=space_id,
                            include_fts=True,
                        )
                        persisted_live_document_digest = (
                            self._persisted_document_digest(
                                connection,
                                index_id=active.id,
                                include_fts=True,
                            )
                        )
                        persisted_live_projection_digest = (
                            self._persisted_projection_digest(
                                connection, current, include_fts=True
                            )
                        )
                        stored_live_document_digest = attestation["document_fts_digest"]
                        stored_live_projection_digest = attestation[
                            "projection_fts_digest"
                        ]
                        if bool(stored_live_document_digest) != bool(
                            stored_live_projection_digest
                        ):
                            raise GraphRAGProjectionError(
                                "active GraphRAG FTS attestation is incomplete"
                            )
                        if stored_live_document_digest:
                            live_valid = bool(expected_live_projection_digest)
                            live_valid = live_valid and hmac.compare_digest(
                                str(stored_live_document_digest),
                                persisted_live_document_digest,
                            )
                            live_valid = live_valid and hmac.compare_digest(
                                str(stored_live_document_digest),
                                expected_live_document_digest,
                            )
                            live_valid = live_valid and hmac.compare_digest(
                                str(stored_live_projection_digest),
                                persisted_live_projection_digest,
                            )
                            live_valid = live_valid and hmac.compare_digest(
                                str(stored_live_projection_digest),
                                str(expected_live_projection_digest),
                            )
                            if not live_valid:
                                raise GraphRAGProjectionError(
                                    "active GraphRAG persisted projection digest mismatch"
                                )
                        else:
                            # The core attestation proves the database projection
                            # is canonical.  A null live digest means FTS was not
                            # available when it was published, so establish the
                            # first live baseline from the verified bundle.
                            self._project_bundle(connection, current, bundle)
                            rebuilt_document_digest = self._persisted_document_digest(
                                connection, index_id=active.id
                            )
                            rebuilt_projection_digest = (
                                self._persisted_projection_digest(connection, current)
                            )
                            persisted_live_document_digest = (
                                self._persisted_document_digest(
                                    connection,
                                    index_id=active.id,
                                    include_fts=True,
                                )
                            )
                            persisted_live_projection_digest = (
                                self._persisted_projection_digest(
                                    connection, current, include_fts=True
                                )
                            )
                            if not (
                                hmac.compare_digest(
                                    rebuilt_document_digest,
                                    expected_document_digest,
                                )
                                and hmac.compare_digest(
                                    rebuilt_projection_digest,
                                    expected_projection_digest,
                                )
                                and hmac.compare_digest(
                                    persisted_live_document_digest,
                                    expected_live_document_digest,
                                )
                                and expected_live_projection_digest
                                and hmac.compare_digest(
                                    persisted_live_projection_digest,
                                    expected_live_projection_digest,
                                )
                            ):
                                raise GraphRAGProjectionError(
                                    "active GraphRAG FTS baseline rebuild failed"
                                )
                            self._write_attestation(
                                connection,
                                index_id=active.id,
                                document_digest=expected_document_digest,
                                projection_digest=expected_projection_digest,
                                document_fts_digest=expected_live_document_digest,
                                projection_fts_digest=(expected_live_projection_digest),
                            )
                    # Additive migration can leave provenance empty on a previously
                    # signed index. Backfill only after complete attestation.
                    if manifest.signed_by and not active.signed_by:
                        connection.execute(
                            "UPDATE graphrag_indexes SET signed_by=? "
                            "WHERE id=? AND brain_id=? AND state='active'",
                            (manifest.signed_by, active.id, self.store.brain_id),
                        )
        except GraphRAGArtifactError as exc:
            self._quarantine_active_index(active, exc, verified_bundle=verified_bundle)
            raise

    def _quarantine_active_index(
        self,
        active: GraphRAGIndex,
        error: GraphRAGArtifactError,
        *,
        verified_bundle: _Bundle | None = None,
    ) -> None:
        """Fail closed without quarantining a concurrently replaced pointer."""
        with self.store.transaction() as connection:
            current = connection.execute(
                "SELECT id FROM graphrag_indexes "
                "WHERE brain_id=? AND state='active' LIMIT 1",
                (self.store.brain_id,),
            ).fetchone()
            if not current or str(current["id"]) != active.id:
                return
            space_id = self.store.space_id("tekion", connection=connection)
            self._clear_projection(connection, space_id)
            if verified_bundle is not None:
                self._purge_bundle_projection(
                    connection,
                    index_id=active.id,
                    space_id=space_id,
                    bundle=verified_bundle,
                )
            connection.execute(
                "UPDATE graphrag_indexes SET state='quarantined' "
                "WHERE id=? AND brain_id=? AND state='active'",
                (active.id, self.store.brain_id),
            )
            self.store.audit_in_transaction(
                connection,
                principal_id=None,
                action="graphrag.quarantine",
                resource_type="graphrag_index",
                resource_id=active.id,
                outcome="failed",
                details={
                    "version": active.version,
                    "manifest_hash": active.manifest_hash,
                    "reason": error.__class__.__name__,
                },
            )

    def _quarantine_index(
        self, index: GraphRAGIndex, error: GraphRAGProjectionError
    ) -> None:
        """Quarantine a tampered staged/retained index without replacing an active peer."""
        with self.store.transaction() as connection:
            current = connection.execute(
                "SELECT state FROM graphrag_indexes WHERE id=? AND brain_id=?",
                (index.id, self.store.brain_id),
            ).fetchone()
            if not current or str(current["state"]) == "quarantined":
                return
            if str(current["state"]) == "active":
                space_id = self.store.space_id("tekion", connection=connection)
                self._clear_projection(connection, space_id)
            connection.execute(
                "UPDATE graphrag_indexes SET state='quarantined' "
                "WHERE id=? AND brain_id=?",
                (index.id, self.store.brain_id),
            )
            self.store.audit_in_transaction(
                connection,
                principal_id=None,
                action="graphrag.quarantine",
                resource_type="graphrag_index",
                resource_id=index.id,
                outcome="failed",
                details={
                    "version": index.version,
                    "manifest_hash": index.manifest_hash,
                    "reason": error.__class__.__name__,
                },
            )

    def _initialize_root(self) -> None:
        if self.index_root.exists() and self.index_root.is_symlink():
            raise GraphRAGArtifactError(
                "GraphRAG index root must not be a symbolic link"
            )
        self.index_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._lexical_index_root = Path(os.path.abspath(self.index_root))
        self.index_root = self.index_root.resolve()
        try:
            self.index_root.chmod(0o700)
        except OSError:
            pass
        self._reap_stale_snapshots()

    @staticmethod
    def _remove_private_snapshot(snapshot: Path) -> None:
        if snapshot.is_symlink() or not snapshot.is_dir():
            return
        try:
            snapshot.chmod(0o700)
            for member in snapshot.rglob("*"):
                if member.is_symlink():
                    continue
                if member.is_dir():
                    member.chmod(0o700)
                elif member.is_file():
                    member.chmod(0o600)
        except OSError:
            pass
        shutil.rmtree(snapshot, ignore_errors=True)

    def _reap_stale_snapshots(self) -> None:
        now = time.time()
        for candidate in self.index_root.glob(f"{_SNAPSHOT_PREFIX}*"):
            if candidate.is_symlink():
                continue
            try:
                modified_at = candidate.lstat().st_mtime
            except OSError:
                continue
            if now - modified_at < _STALE_SNAPSHOT_AGE_SECONDS:
                continue
            owner = candidate.name.removeprefix(_SNAPSHOT_PREFIX).split("-", 1)[0]
            if owner.isdigit():
                try:
                    os.kill(int(owner), 0)
                except ProcessLookupError:
                    pass
                except OSError:
                    continue
                else:
                    continue
            self._remove_private_snapshot(candidate)

    def _ensure_attestation_schema(self) -> None:
        with self.store.transaction() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS graphrag_projection_attestations ("
                "index_id TEXT PRIMARY KEY REFERENCES graphrag_indexes(id) ON DELETE CASCADE, "
                "brain_id TEXT NOT NULL REFERENCES brains(id), "
                "digest_version INTEGER NOT NULL CHECK(digest_version=1), "
                "document_digest TEXT NOT NULL CHECK(length(document_digest)=64 "
                "AND document_digest NOT GLOB '*[^0-9a-f]*'), "
                "projection_digest TEXT CHECK(projection_digest IS NULL OR "
                "(length(projection_digest)=64 "
                "AND projection_digest NOT GLOB '*[^0-9a-f]*')), "
                "document_fts_digest TEXT CHECK(document_fts_digest IS NULL OR "
                "(length(document_fts_digest)=64 "
                "AND document_fts_digest NOT GLOB '*[^0-9a-f]*')), "
                "projection_fts_digest TEXT CHECK(projection_fts_digest IS NULL OR "
                "(length(projection_fts_digest)=64 "
                "AND projection_fts_digest NOT GLOB '*[^0-9a-f]*')), "
                "attested_at TEXT NOT NULL)"
            )
            columns = {
                str(row["name"])
                for row in connection.execute(
                    "PRAGMA table_info(graphrag_projection_attestations)"
                )
            }
            for column in ("document_fts_digest", "projection_fts_digest"):
                if column not in columns:
                    connection.execute(
                        f"ALTER TABLE graphrag_projection_attestations "
                        f"ADD COLUMN {column} TEXT"
                    )

    def _expected_document_digest(
        self,
        bundle: _Bundle,
        *,
        index_id: str,
        space_id: str,
        include_fts: bool = False,
    ) -> str:
        documents: list[dict[str, Any]] = []
        fts: list[dict[str, str]] = []
        for document in bundle.documents:
            row_id = (
                "grdoc_"
                + stable_hash(self.store.brain_id, index_id, document.external_id)[:32]
            )
            documents.append({
                "id": row_id,
                "brain_id": self.store.brain_id,
                "index_id": index_id,
                "knowledge_space_id": space_id,
                "document_id": document.external_id,
                "title": document.title,
                "text": document.text,
                "source_uri": document.source_uri,
                "metadata": sanitize_for_storage(dict(document.metadata)),
            })
            if include_fts:
                fts.append({
                    "id": row_id,
                    "content": f"{document.title} {document.text}",
                })
        documents.sort(key=lambda row: str(row["id"]))
        fts.sort(key=lambda row: (row["id"], row["content"]))
        return _canonical_digest(
            _DOCUMENT_DIGEST_DOMAIN,
            {"documents": documents, "graphrag_fts": fts},
        )

    def _persisted_document_digest(
        self,
        connection: sqlite3.Connection,
        *,
        index_id: str,
        include_fts: bool = False,
    ) -> str:
        rows = connection.execute(
            "SELECT id, brain_id, index_id, knowledge_space_id, document_id, "
            "title, text, source_uri, metadata_json FROM graphrag_documents "
            "WHERE brain_id=? AND index_id=? ORDER BY id",
            (self.store.brain_id, index_id),
        ).fetchall()
        documents = [
            {
                "id": str(row["id"]),
                "brain_id": str(row["brain_id"]),
                "index_id": str(row["index_id"]),
                "knowledge_space_id": str(row["knowledge_space_id"]),
                "document_id": str(row["document_id"]),
                "title": str(row["title"]),
                "text": str(row["text"]),
                "source_uri": (
                    str(row["source_uri"]) if row["source_uri"] is not None else None
                ),
                "metadata": _decoded_json(row["metadata_json"]),
            }
            for row in rows
        ]
        fts: list[dict[str, str]] = []
        if include_fts:
            fts = [
                {"id": str(row["id"]), "content": str(row["content"])}
                for row in connection.execute(
                    "SELECT f.id, f.content FROM graphrag_fts f "
                    "JOIN graphrag_documents d ON d.id=f.id "
                    "WHERE d.brain_id=? AND d.index_id=? ORDER BY f.id, f.content",
                    (self.store.brain_id, index_id),
                ).fetchall()
            ]
        return _canonical_digest(
            _DOCUMENT_DIGEST_DOMAIN,
            {"documents": documents, "graphrag_fts": fts},
        )

    def _persisted_projection_digest(
        self,
        connection: sqlite3.Connection,
        index: Mapping[str, Any],
        *,
        include_fts: bool = False,
    ) -> str:
        index_id = str(index["id"])
        version = str(index["version"])
        space_id = self.store.space_id("tekion", connection=connection)
        document_rows = connection.execute(
            "SELECT id, brain_id, index_id, knowledge_space_id, document_id, title, "
            "text, source_uri, entity_ids_json, community_ids_json, metadata_json "
            "FROM graphrag_documents WHERE brain_id=? AND index_id=? ORDER BY id",
            (self.store.brain_id, index_id),
        ).fetchall()
        documents = [
            {
                "id": str(row["id"]),
                "brain_id": str(row["brain_id"]),
                "index_id": str(row["index_id"]),
                "knowledge_space_id": str(row["knowledge_space_id"]),
                "document_id": str(row["document_id"]),
                "title": str(row["title"]),
                "text": str(row["text"]),
                "source_uri": (
                    str(row["source_uri"]) if row["source_uri"] is not None else None
                ),
                "entity_ids": _decoded_json(row["entity_ids_json"]),
                "community_ids": _decoded_json(row["community_ids_json"]),
                "metadata": _decoded_json(row["metadata_json"]),
            }
            for row in document_rows
        ]
        document_fts: list[dict[str, str]] = []
        if include_fts:
            document_fts = [
                {"id": str(row["id"]), "content": str(row["content"])}
                for row in connection.execute(
                    "SELECT f.id, f.content FROM graphrag_fts f "
                    "JOIN graphrag_documents d ON d.id=f.id "
                    "WHERE d.brain_id=? AND d.index_id=? ORDER BY f.id, f.content",
                    (self.store.brain_id, index_id),
                ).fetchall()
            ]
        relation_rows = connection.execute(
            "SELECT id, brain_id, knowledge_space_id, subject_entity_id, predicate, "
            "object_entity_id, status, epistemic_status, valid_from, valid_until, "
            "weight, derived_by FROM relations WHERE brain_id=? AND derived_by=? "
            "ORDER BY id",
            (self.store.brain_id, f"graphrag:{index_id}"),
        ).fetchall()
        relations = [dict(row) for row in relation_rows]
        algorithm_version = f"graphrag:{index_id}:{version}"
        community_rows = connection.execute(
            "SELECT id, brain_id, knowledge_space_id, level, parent_id, label, report, "
            "algorithm_version, member_ids_json, stale FROM communities "
            "WHERE brain_id=? AND algorithm_version=? ORDER BY id",
            (self.store.brain_id, algorithm_version),
        ).fetchall()
        communities = [
            {
                **{
                    key: row[key]
                    for key in (
                        "id",
                        "brain_id",
                        "knowledge_space_id",
                        "level",
                        "parent_id",
                        "label",
                        "report",
                        "algorithm_version",
                        "stale",
                    )
                },
                "member_ids": _decoded_json(row["member_ids_json"]),
            }
            for row in community_rows
        ]
        referenced_entity_ids: set[str] = set()
        for document in documents:
            if isinstance(document["entity_ids"], list):
                referenced_entity_ids.update(map(str, document["entity_ids"]))
        for relation in relations:
            referenced_entity_ids.add(str(relation["subject_entity_id"]))
            referenced_entity_ids.add(str(relation["object_entity_id"]))
        for community in communities:
            if isinstance(community["member_ids"], list):
                referenced_entity_ids.update(map(str, community["member_ids"]))
        all_entity_rows = connection.execute(
            "SELECT id, brain_id, knowledge_space_id, entity_type, canonical_name, "
            "normalized_name, description, visibility, metadata_json, deleted_at "
            "FROM entities WHERE brain_id=? AND knowledge_space_id=? ORDER BY id",
            (self.store.brain_id, space_id),
        ).fetchall()
        entities: list[dict[str, Any]] = []
        for row in all_entity_rows:
            metadata = _decoded_json(row["metadata_json"])
            is_projection = (
                isinstance(metadata, Mapping)
                and metadata.get("source") == "graphrag"
                and metadata.get("index_id") == index_id
            )
            if str(row["id"]) not in referenced_entity_ids and not is_projection:
                continue
            referenced_entity_ids.add(str(row["id"]))
            entities.append({
                **{
                    key: row[key]
                    for key in (
                        "id",
                        "brain_id",
                        "knowledge_space_id",
                        "entity_type",
                        "canonical_name",
                        "normalized_name",
                        "description",
                        "visibility",
                        "deleted_at",
                    )
                },
                "metadata": metadata,
            })
        aliases: list[dict[str, Any]] = []
        for row in connection.execute(
            "SELECT a.entity_id, a.knowledge_space_id, a.alias, a.normalized_alias, "
            "a.evidence_id, a.deleted_at FROM entity_aliases a JOIN entities e "
            "ON e.id=a.entity_id WHERE e.brain_id=? AND e.knowledge_space_id=? "
            "ORDER BY a.entity_id, a.normalized_alias, a.alias",
            (self.store.brain_id, space_id),
        ).fetchall():
            if str(row["entity_id"]) in referenced_entity_ids:
                aliases.append(dict(row))
        entity_fts: list[dict[str, str]] = []
        if include_fts:
            for row in connection.execute(
                "SELECT f.id, f.content FROM entity_fts f JOIN entities e ON e.id=f.id "
                "WHERE e.brain_id=? AND e.knowledge_space_id=? ORDER BY f.id, f.content",
                (self.store.brain_id, space_id),
            ).fetchall():
                if str(row["id"]) in referenced_entity_ids:
                    entity_fts.append({
                        "id": str(row["id"]),
                        "content": str(row["content"]),
                    })
        return _canonical_digest(
            _PROJECTION_DIGEST_DOMAIN,
            {
                "index": {
                    "id": index_id,
                    "version": version,
                    "manifest_hash": str(index["manifest_hash"]),
                },
                "documents": documents,
                "graphrag_fts": document_fts,
                "entities": entities,
                "entity_aliases": aliases,
                "entity_fts": entity_fts,
                "relations": relations,
                "communities": communities,
            },
        )

    @staticmethod
    def _attestation(
        connection: sqlite3.Connection, index_id: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM graphrag_projection_attestations WHERE index_id=?",
            (index_id,),
        ).fetchone()

    def _write_attestation(
        self,
        connection: sqlite3.Connection,
        *,
        index_id: str,
        document_digest: str,
        projection_digest: str | None,
        document_fts_digest: str | None = None,
        projection_fts_digest: str | None = None,
    ) -> None:
        connection.execute(
            "INSERT INTO graphrag_projection_attestations("
            "index_id, brain_id, digest_version, document_digest, "
            "projection_digest, document_fts_digest, projection_fts_digest, "
            "attested_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(index_id) DO UPDATE SET brain_id=excluded.brain_id, "
            "digest_version=excluded.digest_version, "
            "document_digest=excluded.document_digest, "
            "projection_digest=excluded.projection_digest, "
            "document_fts_digest=excluded.document_fts_digest, "
            "projection_fts_digest=excluded.projection_fts_digest, "
            "attested_at=excluded.attested_at",
            (
                index_id,
                self.store.brain_id,
                _PROJECTION_DIGEST_VERSION,
                document_digest,
                projection_digest,
                document_fts_digest,
                projection_fts_digest,
                utc_now(),
            ),
        )

    def _expected_projection_digests(
        self,
        connection: sqlite3.Connection,
        index: sqlite3.Row,
        bundle: _Bundle,
    ) -> tuple[str, str | None]:
        savepoint = "graphrag_expected_projection"
        connection.execute(f"SAVEPOINT {savepoint}")
        try:
            self._project_bundle(connection, index, bundle)
            core_digest = self._persisted_projection_digest(connection, index)
            live_digest = (
                self._persisted_projection_digest(connection, index, include_fts=True)
                if self.store._fts_available
                else None
            )
        except BaseException:
            connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            connection.execute(f"RELEASE SAVEPOINT {savepoint}")
            raise
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        return core_digest, live_digest

    @staticmethod
    def _open_artifact_member(artifact: Path, relative: PurePosixPath) -> int:
        """Open one regular artifact member without following any path symlink."""
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        directory_fd = -1
        try:
            directory_fd = os.open(artifact, directory_flags | nofollow)
            for part in relative.parts[:-1]:
                next_fd = os.open(
                    part,
                    directory_flags | nofollow,
                    dir_fd=directory_fd,
                )
                os.close(directory_fd)
                directory_fd = next_fd
            descriptor = os.open(
                relative.name,
                os.O_RDONLY | nofollow,
                dir_fd=directory_fd,
            )
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                raise GraphRAGArtifactError(
                    f"artifact file is not a regular file: {relative}"
                )
            return descriptor
        except GraphRAGArtifactError:
            raise
        except OSError as exc:
            raise GraphRAGArtifactError(
                f"unable to securely open GraphRAG artifact file: {relative}"
            ) from exc
        finally:
            if directory_fd >= 0:
                os.close(directory_fd)

    def _copy_artifact_member(
        self,
        artifact: Path,
        snapshot: Path,
        relative: PurePosixPath,
        *,
        max_bytes: int,
    ) -> int:
        destination = snapshot.joinpath(*relative.parts)
        try:
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise GraphRAGArtifactError(
                f"unable to create private GraphRAG snapshot path: {relative}"
            ) from exc
        descriptor = self._open_artifact_member(artifact, relative)
        copied = 0
        try:
            source = os.fdopen(descriptor, "rb")
            descriptor = -1
            with source, destination.open("xb") as target:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > max_bytes:
                        raise GraphRAGArtifactError(
                            "GraphRAG artifact exceeds the aggregate byte limit"
                        )
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            destination.chmod(0o600)
        except GraphRAGArtifactError:
            raise
        except OSError as exc:
            raise GraphRAGArtifactError(
                f"unable to copy GraphRAG artifact file: {relative}"
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        return copied

    def _snapshot_member_paths(self, raw: Any) -> tuple[PurePosixPath, ...]:
        if not isinstance(raw, Mapping):
            raise GraphRAGArtifactError("GraphRAG manifest must be a JSON object")
        if raw.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise GraphRAGArtifactError("unsupported GraphRAG manifest schema version")
        if _text(raw.get("format"), limit=100) not in _FORMATS:
            raise GraphRAGArtifactError("unsupported GraphRAG artifact format")
        version = _text(raw.get("version"), limit=200)
        if not _VERSION.fullmatch(version):
            raise GraphRAGArtifactError(
                "GraphRAG version must be a safe immutable identifier"
            )
        if raw.get("knowledge_space") != "tekion":
            raise GraphRAGArtifactError(
                "GraphRAG artifacts may only project into the shared tekion knowledge space"
            )
        _verify_ed25519_signature(
            raw, self.trusted_public_keys, required=self.require_signature
        )
        raw_files = raw.get("files")
        if not isinstance(raw_files, list) or not raw_files or len(raw_files) > 64:
            raise GraphRAGArtifactError("GraphRAG manifest must list 1 to 64 files")
        paths: list[PurePosixPath] = []
        seen: set[str] = set()
        declared_total = 0
        for entry in raw_files:
            if not isinstance(entry, Mapping):
                raise GraphRAGArtifactError(
                    "GraphRAG manifest file entry must be an object"
                )
            relative = _safe_relative_path(entry.get("path"))
            normalized = relative.as_posix()
            if normalized == "manifest.json" or normalized in seen:
                raise GraphRAGArtifactError(
                    "GraphRAG manifest contains duplicate or reserved file paths"
                )
            seen.add(normalized)
            declared_size = entry.get("size")
            if declared_size is not None:
                try:
                    size = int(declared_size)
                except (TypeError, ValueError) as exc:
                    raise GraphRAGArtifactError(
                        "artifact file size must be an integer"
                    ) from exc
                if size < 0:
                    raise GraphRAGArtifactError(
                        "artifact file size must not be negative"
                    )
                declared_total += size
                if declared_total > _MAX_BUNDLE_BYTES:
                    raise GraphRAGArtifactError(
                        "GraphRAG artifact exceeds the aggregate byte limit"
                    )
            paths.append(relative)
        return tuple(paths)

    @contextmanager
    def _verified_snapshot(
        self, path: str | Path
    ) -> Iterator[tuple[Path, ArtifactManifest]]:
        """Yield a private snapshot whose parsed bytes are the verified bytes."""
        artifact = self._resolve_artifact(path)
        snapshot = Path(
            tempfile.mkdtemp(
                prefix=f"{_SNAPSHOT_PREFIX}{os.getpid()}-", dir=self.index_root
            )
        )
        try:
            manifest_relative = PurePosixPath("manifest.json")
            self._copy_artifact_member(
                artifact,
                snapshot,
                manifest_relative,
                max_bytes=_MAX_MANIFEST_BYTES,
            )
            try:
                raw = json.loads(
                    (snapshot / "manifest.json").read_text(encoding="utf-8")
                )
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise GraphRAGArtifactError(
                    "GraphRAG manifest is not valid UTF-8 JSON"
                ) from exc
            members = self._snapshot_member_paths(raw)
            copied = 0
            for relative in members:
                remaining = _MAX_BUNDLE_BYTES - copied
                if remaining <= 0:
                    raise GraphRAGArtifactError(
                        "GraphRAG artifact exceeds the aggregate byte limit"
                    )
                copied += self._copy_artifact_member(
                    artifact,
                    snapshot,
                    relative,
                    max_bytes=min(_MAX_ARTIFACT_FILE_BYTES, remaining),
                )
            manifest = self.validate_artifact(snapshot)
            snapshot_members = list(snapshot.rglob("*"))
            for member in snapshot_members:
                if member.is_file():
                    member.chmod(0o400)
            for member in sorted(
                (item for item in snapshot_members if item.is_dir()),
                key=lambda item: len(item.parts),
                reverse=True,
            ):
                member.chmod(0o500)
            snapshot.chmod(0o500)
            yield snapshot, manifest
        finally:
            if snapshot.exists():
                self._remove_private_snapshot(snapshot)

    def _resolve_artifact(self, path: str | Path, *, must_exist: bool = True) -> Path:
        raw = Path(path)
        if ".." in raw.parts:
            raise GraphRAGArtifactError("artifact path traversal is not allowed")
        candidate = raw if raw.is_absolute() else self._lexical_index_root / raw
        lexical = Path(os.path.abspath(candidate))
        try:
            relative = lexical.relative_to(self._lexical_index_root)
            cursor = self._lexical_index_root
        except ValueError:
            try:
                resolved_candidate = lexical.resolve(strict=must_exist)
                relative = resolved_candidate.relative_to(self.index_root)
                cursor = self.index_root
            except (OSError, ValueError) as exc:
                raise GraphRAGArtifactError(
                    "artifact must stay under the configured index root"
                ) from exc
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise GraphRAGArtifactError(
                    "symbolic links are not allowed in GraphRAG artifacts"
                )
        if must_exist and (not lexical.exists() or not lexical.is_dir()):
            raise GraphRAGArtifactError("GraphRAG artifact directory does not exist")
        try:
            resolved = lexical.resolve(strict=must_exist)
            resolved.relative_to(self.index_root)
        except (OSError, ValueError) as exc:
            raise GraphRAGArtifactError(
                "artifact must stay under the configured index root"
            ) from exc
        return resolved

    def _artifact_file(self, artifact: Path, relative: PurePosixPath) -> Path:
        candidate = artifact.joinpath(*relative.parts)
        cursor = artifact
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise GraphRAGArtifactError(
                    "symbolic links are not allowed in GraphRAG artifacts"
                )
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(artifact)
        except (OSError, ValueError) as exc:
            raise GraphRAGArtifactError(
                "artifact file path escapes its version directory"
            ) from exc
        if not resolved.is_file():
            raise GraphRAGArtifactError(
                f"artifact file is not a regular file: {relative}"
            )
        return resolved

    def validate_artifact(self, path: str | Path) -> ArtifactManifest:
        artifact = self._resolve_artifact(path)
        manifest_path = artifact / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise GraphRAGArtifactError("artifact must contain a regular manifest.json")
        if manifest_path.stat().st_size > _MAX_MANIFEST_BYTES:
            raise GraphRAGArtifactError("GraphRAG manifest is too large")
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise GraphRAGArtifactError(
                "GraphRAG manifest is not valid UTF-8 JSON"
            ) from exc
        if not isinstance(raw, Mapping):
            raise GraphRAGArtifactError("GraphRAG manifest must be a JSON object")
        if raw.get("schema_version") != MANIFEST_SCHEMA_VERSION:
            raise GraphRAGArtifactError("unsupported GraphRAG manifest schema version")
        artifact_format = _text(raw.get("format"), limit=100)
        if artifact_format not in _FORMATS:
            raise GraphRAGArtifactError("unsupported GraphRAG artifact format")
        version = _text(raw.get("version"), limit=200)
        if not _VERSION.fullmatch(version):
            raise GraphRAGArtifactError(
                "GraphRAG version must be a safe immutable identifier"
            )
        if raw.get("knowledge_space") != "tekion":
            raise GraphRAGArtifactError(
                "GraphRAG artifacts may only project into the shared tekion knowledge space"
            )
        signed_by = _verify_ed25519_signature(
            raw, self.trusted_public_keys, required=self.require_signature
        )

        raw_files = raw.get("files")
        if not isinstance(raw_files, list) or not raw_files or len(raw_files) > 64:
            raise GraphRAGArtifactError("GraphRAG manifest must list 1 to 64 files")
        files: list[ManifestFile] = []
        seen: set[str] = set()
        parsed_roles: set[str] = set()
        total_size = 0
        for entry in raw_files:
            if not isinstance(entry, Mapping):
                raise GraphRAGArtifactError(
                    "GraphRAG manifest file entry must be an object"
                )
            relative = _safe_relative_path(entry.get("path"))
            normalized_path = relative.as_posix()
            if normalized_path in seen:
                raise GraphRAGArtifactError(
                    "GraphRAG manifest contains duplicate file paths"
                )
            seen.add(normalized_path)
            expected_hash = _text(entry.get("sha256"), limit=80).lower()
            if not _SHA256.fullmatch(expected_hash):
                raise GraphRAGArtifactError(
                    "every artifact file requires a SHA-256 digest"
                )
            file_path = self._artifact_file(artifact, relative)
            size = file_path.stat().st_size
            if size > _MAX_ARTIFACT_FILE_BYTES:
                raise GraphRAGArtifactError(
                    "GraphRAG artifact file exceeds the size limit"
                )
            total_size += size
            if total_size > _MAX_BUNDLE_BYTES:
                raise GraphRAGArtifactError(
                    "GraphRAG artifact exceeds the aggregate byte limit"
                )
            declared_size = entry.get("size")
            if declared_size is not None:
                try:
                    expected_size = int(declared_size)
                except (TypeError, ValueError) as exc:
                    raise GraphRAGArtifactError(
                        "artifact file size must be an integer"
                    ) from exc
                if expected_size != size:
                    raise GraphRAGArtifactError(
                        "GraphRAG artifact file size does not match"
                    )
            if sha256_file(file_path) != expected_hash:
                raise GraphRAGArtifactError(
                    f"GraphRAG artifact hash mismatch: {normalized_path}"
                )
            role = _text(entry.get("role"), limit=100) or _role_for_path(
                normalized_path
            )
            if role in _KNOWN_ROLES:
                parsed_roles.add(role)
            files.append(ManifestFile(normalized_path, expected_hash, role, size))

        if not parsed_roles.intersection({"documents", "text_units"}):
            raise GraphRAGArtifactError(
                "GraphRAG artifact has no document or text-unit records"
            )
        manifest_hash = hashlib.sha256(_canonical_json(raw).encode("utf-8")).hexdigest()
        return ArtifactManifest(
            version=version,
            artifact_format=artifact_format,
            knowledge_space="tekion",
            files=tuple(files),
            manifest_hash=manifest_hash,
            signed_by=signed_by,
            raw=raw,
        )

    def import_artifact(self, path: str | Path) -> GraphRAGIndex:
        """Validate and stage an artifact without changing the active pointer."""
        artifact = self._resolve_artifact(path)
        relative_path = artifact.relative_to(self.index_root).as_posix()
        with self._verified_snapshot(artifact) as (snapshot, manifest):
            bundle = self._load_bundle(snapshot, manifest)
            with self.store.transaction() as connection:
                existing = connection.execute(
                    "SELECT * FROM graphrag_indexes WHERE brain_id=? AND version=?",
                    (self.store.brain_id, manifest.version),
                ).fetchone()
                if existing:
                    if existing["manifest_hash"] != manifest.manifest_hash:
                        raise GraphRAGArtifactError(
                            "an immutable GraphRAG version already exists with different contents"
                        )
                    return self._index_from_row(existing)

                index_id = new_id("graphrag")
                created_at = utc_now()
                connection.execute(
                    "INSERT INTO graphrag_indexes(id, brain_id, version, path, manifest_hash, "
                    "signed_by, state, document_count, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        index_id,
                        self.store.brain_id,
                        manifest.version,
                        relative_path,
                        manifest.manifest_hash,
                        manifest.signed_by,
                        "staged",
                        len(bundle.documents),
                        created_at,
                    ),
                )
                space_id = self.store.space_id("tekion", connection=connection)
                self._insert_bundle_documents(
                    connection,
                    index_id=index_id,
                    space_id=space_id,
                    bundle=bundle,
                )
                expected_digest = self._expected_document_digest(
                    bundle, index_id=index_id, space_id=space_id
                )
                persisted_digest = self._persisted_document_digest(
                    connection, index_id=index_id
                )
                if not hmac.compare_digest(expected_digest, persisted_digest):
                    raise GraphRAGProjectionError(
                        "imported GraphRAG document projection failed attestation"
                    )
                expected_live_digest: str | None = None
                if self.store._fts_available:
                    expected_live_digest = self._expected_document_digest(
                        bundle,
                        index_id=index_id,
                        space_id=space_id,
                        include_fts=True,
                    )
                    persisted_live_digest = self._persisted_document_digest(
                        connection, index_id=index_id, include_fts=True
                    )
                    if not hmac.compare_digest(
                        expected_live_digest, persisted_live_digest
                    ):
                        raise GraphRAGProjectionError(
                            "imported GraphRAG document FTS projection failed attestation"
                        )
                self._write_attestation(
                    connection,
                    index_id=index_id,
                    document_digest=expected_digest,
                    projection_digest=None,
                    document_fts_digest=expected_live_digest,
                    projection_fts_digest=None,
                )
                self.store.audit_in_transaction(
                    connection,
                    principal_id=None,
                    action="graphrag.import",
                    resource_type="graphrag_index",
                    resource_id=index_id,
                    outcome="succeeded",
                    details={
                        "version": manifest.version,
                        "manifest_hash": manifest.manifest_hash,
                        "signed_by": manifest.signed_by,
                        "document_count": len(bundle.documents),
                    },
                )
                row = connection.execute(
                    "SELECT * FROM graphrag_indexes WHERE id=? AND brain_id=?",
                    (index_id, self.store.brain_id),
                ).fetchone()
                return self._index_from_row(row)

    def publish(self, version: str) -> GraphRAGIndex:
        """Atomically project and select a previously imported index version."""
        return self._activate(
            version, audit_action="graphrag.publish", required_state="staged"
        )

    def rollback(self, version: str | None = None) -> GraphRAGIndex:
        """Atomically return to a retained version, revalidating its artifact."""
        if version is None:
            with self.store.connect() as connection:
                row = connection.execute(
                    "SELECT version FROM graphrag_indexes WHERE brain_id=? AND state='retained' "
                    "ORDER BY published_at DESC, created_at DESC LIMIT 1",
                    (self.store.brain_id,),
                ).fetchone()
            if not row:
                raise LookupError(
                    "no retained GraphRAG index is available for rollback"
                )
            version = str(row["version"])
        target = self.get_index(version)
        if target is None or target.state != "retained":
            raise LookupError(
                "GraphRAG rollback target must be a retained index version"
            )
        return self._activate(
            version, audit_action="graphrag.rollback", required_state="retained"
        )

    def _activate(
        self,
        version: str,
        *,
        audit_action: str,
        required_state: str,
    ) -> GraphRAGIndex:
        staged = self.get_index(version)
        if staged is None:
            raise LookupError(f"GraphRAG index version was not imported: {version}")
        if staged.state != required_state:
            raise LookupError(
                f"GraphRAG activation target must be a {required_state} index version"
            )
        try:
            with self._verified_snapshot(staged.path) as (snapshot, manifest):
                if (
                    manifest.version != staged.version
                    or manifest.manifest_hash != staged.manifest_hash
                ):
                    raise GraphRAGArtifactError(
                        "stored GraphRAG manifest no longer matches its index"
                    )
                bundle = self._load_bundle(snapshot, manifest)
                if len(bundle.documents) != staged.document_count:
                    raise GraphRAGProjectionError(
                        "GraphRAG document projection no longer matches its import"
                    )

                with self.store.transaction() as connection:
                    target = connection.execute(
                        "SELECT * FROM graphrag_indexes WHERE id=? AND brain_id=? "
                        "AND manifest_hash=? AND state=?",
                        (
                            staged.id,
                            self.store.brain_id,
                            manifest.manifest_hash,
                            required_state,
                        ),
                    ).fetchone()
                    if not target:
                        raise GraphRAGArtifactError(
                            "GraphRAG index changed before publication"
                        )
                    space_id = self.store.space_id("tekion", connection=connection)
                    expected_document_digest = self._expected_document_digest(
                        bundle, index_id=staged.id, space_id=space_id
                    )
                    attestation = self._attestation(connection, staged.id)
                    if required_state == "retained" and (
                        attestation is None or not attestation["projection_digest"]
                    ):
                        raise GraphRAGProjectionError(
                            "retained GraphRAG projection attestation is missing"
                        )
                    if attestation is None:
                        # One-time upgrade for imports created before digest
                        # attestation: rebuild from the verified snapshot rather
                        # than trusting or blessing the old database rows.
                        self._replace_bundle_documents(
                            connection,
                            index_id=staged.id,
                            space_id=space_id,
                            bundle=bundle,
                        )
                    else:
                        persisted_document_digest = self._persisted_document_digest(
                            connection, index_id=staged.id
                        )
                        if not (
                            int(attestation["digest_version"])
                            == _PROJECTION_DIGEST_VERSION
                            and str(attestation["brain_id"]) == self.store.brain_id
                            and hmac.compare_digest(
                                str(attestation["document_digest"]),
                                expected_document_digest,
                            )
                            and hmac.compare_digest(
                                persisted_document_digest,
                                expected_document_digest,
                            )
                        ):
                            raise GraphRAGProjectionError(
                                "GraphRAG document projection digest mismatch"
                            )
                    expected_live_document_digest: str | None = None
                    if self.store._fts_available:
                        expected_live_document_digest = self._expected_document_digest(
                            bundle,
                            index_id=staged.id,
                            space_id=space_id,
                            include_fts=True,
                        )
                        stored_live_document_digest = (
                            attestation["document_fts_digest"]
                            if attestation is not None
                            else None
                        )
                        if stored_live_document_digest:
                            persisted_live_document_digest = (
                                self._persisted_document_digest(
                                    connection,
                                    index_id=staged.id,
                                    include_fts=True,
                                )
                            )
                            if not (
                                hmac.compare_digest(
                                    str(stored_live_document_digest),
                                    persisted_live_document_digest,
                                )
                                and hmac.compare_digest(
                                    str(stored_live_document_digest),
                                    expected_live_document_digest,
                                )
                            ):
                                raise GraphRAGProjectionError(
                                    "GraphRAG document FTS projection digest mismatch"
                                )
                        else:
                            self._replace_bundle_documents(
                                connection,
                                index_id=staged.id,
                                space_id=space_id,
                                bundle=bundle,
                            )
                            rebuilt_document_digest = self._persisted_document_digest(
                                connection, index_id=staged.id
                            )
                            rebuilt_live_document_digest = (
                                self._persisted_document_digest(
                                    connection,
                                    index_id=staged.id,
                                    include_fts=True,
                                )
                            )
                            if not (
                                hmac.compare_digest(
                                    rebuilt_document_digest,
                                    expected_document_digest,
                                )
                                and hmac.compare_digest(
                                    rebuilt_live_document_digest,
                                    expected_live_document_digest,
                                )
                            ):
                                raise GraphRAGProjectionError(
                                    "GraphRAG document FTS baseline rebuild failed"
                                )
                    previous = connection.execute(
                        "SELECT version FROM graphrag_indexes "
                        "WHERE brain_id=? AND state='active' LIMIT 1",
                        (self.store.brain_id,),
                    ).fetchone()
                    self._project_bundle(connection, target, bundle)
                    persisted_document_digest = self._persisted_document_digest(
                        connection, index_id=staged.id
                    )
                    if not hmac.compare_digest(
                        persisted_document_digest, expected_document_digest
                    ):
                        raise GraphRAGProjectionError(
                            "published GraphRAG document projection failed attestation"
                        )
                    projection_digest = self._persisted_projection_digest(
                        connection, target
                    )
                    (
                        expected_projection_digest,
                        expected_live_projection_digest,
                    ) = self._expected_projection_digests(connection, target, bundle)
                    if not hmac.compare_digest(
                        projection_digest, expected_projection_digest
                    ):
                        raise GraphRAGProjectionError(
                            "published GraphRAG projection failed attestation"
                        )
                    if self.store._fts_available:
                        persisted_live_projection_digest = (
                            self._persisted_projection_digest(
                                connection, target, include_fts=True
                            )
                        )
                        if not (
                            expected_live_projection_digest
                            and hmac.compare_digest(
                                persisted_live_projection_digest,
                                expected_live_projection_digest,
                            )
                        ):
                            raise GraphRAGProjectionError(
                                "published GraphRAG FTS projection failed attestation"
                            )
                    self._write_attestation(
                        connection,
                        index_id=staged.id,
                        document_digest=expected_document_digest,
                        projection_digest=projection_digest,
                        document_fts_digest=expected_live_document_digest,
                        projection_fts_digest=expected_live_projection_digest,
                    )
                    now = utc_now()
                    connection.execute(
                        "UPDATE graphrag_indexes SET state='retained' "
                        "WHERE brain_id=? AND state='active' AND id<>?",
                        (self.store.brain_id, staged.id),
                    )
                    connection.execute(
                        "UPDATE graphrag_indexes SET state='active', published_at=? "
                        "WHERE brain_id=? AND id=?",
                        (now, self.store.brain_id, staged.id),
                    )
                    self.store.audit_in_transaction(
                        connection,
                        principal_id=None,
                        action=audit_action,
                        resource_type="graphrag_index",
                        resource_id=staged.id,
                        outcome="succeeded",
                        details={
                            "version": staged.version,
                            "previous_version": (
                                previous["version"] if previous else None
                            ),
                            "manifest_hash": staged.manifest_hash,
                            "projection_digest": projection_digest,
                        },
                    )
                    row = connection.execute(
                        "SELECT * FROM graphrag_indexes WHERE id=? AND brain_id=?",
                        (staged.id, self.store.brain_id),
                    ).fetchone()
                    return self._index_from_row(row)
        except GraphRAGProjectionError as exc:
            self._quarantine_index(staged, exc)
            raise

    def list_indexes(self) -> tuple[GraphRAGIndex, ...]:
        with self.store.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM graphrag_indexes WHERE brain_id=? "
                "ORDER BY CASE state WHEN 'active' THEN 0 WHEN 'staged' THEN 1 ELSE 2 END, "
                "created_at DESC",
                (self.store.brain_id,),
            ).fetchall()
        return tuple(self._index_from_row(row) for row in rows)

    def active_index(self) -> GraphRAGIndex | None:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM graphrag_indexes WHERE brain_id=? AND state='active' LIMIT 1",
                (self.store.brain_id,),
            ).fetchone()
        return self._index_from_row(row) if row else None

    def active_provenance(self) -> dict[str, str] | None:
        """Return the server-selected version namespace for caches and citations.

        ``tekion`` remains the authorization space.  The versioned namespace
        is derived from the active, brain-scoped pointer and must never be
        accepted as a caller-selected authorization value.
        """
        active = self.active_index()
        if active is None:
            return None
        provenance = {
            "knowledge_space": "tekion",
            "index_version": active.version,
            "manifest_hash": active.manifest_hash,
            "cache_namespace": f"tekion:{active.version}:{active.manifest_hash[:16]}",
        }
        if active.signed_by:
            provenance["signed_by"] = active.signed_by
        return provenance

    def get_index(self, version: str) -> GraphRAGIndex | None:
        if not _VERSION.fullmatch(str(version)):
            raise GraphRAGArtifactError("invalid GraphRAG version")
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM graphrag_indexes WHERE brain_id=? AND version=?",
                (self.store.brain_id, version),
            ).fetchone()
        return self._index_from_row(row) if row else None

    @staticmethod
    def _index_from_row(row: sqlite3.Row) -> GraphRAGIndex:
        return GraphRAGIndex(
            id=str(row["id"]),
            version=str(row["version"]),
            state=str(row["state"]),
            path=str(row["path"]),
            manifest_hash=str(row["manifest_hash"]),
            signed_by=(str(row["signed_by"]) if row["signed_by"] else None),
            document_count=int(row["document_count"]),
            published_at=row["published_at"],
            created_at=str(row["created_at"]),
        )

    def _load_bundle(self, artifact: Path, manifest: ArtifactManifest) -> _Bundle:
        by_role: dict[str, list[Mapping[str, Any]]] = {
            role: [] for role in _KNOWN_ROLES
        }
        total_records = 0
        estimated_materialized_bytes = 0
        estimated_materialized_cells = 0
        actual_materialized_bytes = 0
        actual_materialized_cells = 0
        actual_python_bytes = 0
        for entry in manifest.files:
            if entry.role not in _KNOWN_ROLES:
                continue
            remaining = _MAX_BUNDLE_RECORDS - total_records
            if remaining <= 0:
                raise GraphRAGArtifactError("GraphRAG aggregate record limit exceeded")
            path = self._artifact_file(artifact, _safe_relative_path(entry.path))
            parquet_rows: int | None = None
            estimated_cells = 0
            if path.suffix.lower() == ".parquet":
                parquet_rows, estimated_cells, estimated_bytes = (
                    self._parquet_materialization(path)
                )
                if parquet_rows > remaining:
                    raise GraphRAGArtifactError(
                        "GraphRAG aggregate record limit exceeded"
                    )
                estimated_materialized_cells += estimated_cells
                if estimated_materialized_cells > _MAX_BUNDLE_CELLS:
                    raise GraphRAGArtifactError(
                        "GraphRAG aggregate materialized cell limit exceeded"
                    )
            else:
                estimated_bytes = entry.size
            estimated_materialized_bytes += estimated_bytes
            if estimated_materialized_bytes > _MAX_BUNDLE_MATERIALIZED_BYTES:
                raise GraphRAGArtifactError(
                    "GraphRAG aggregate materialized byte limit exceeded"
                )
            records, actual_bytes, actual_cells, actual_python = self._read_records(
                path,
                entry.role,
                max_records=min(_MAX_RECORDS_PER_FILE, remaining),
                max_materialized_bytes=(
                    _MAX_BUNDLE_MATERIALIZED_BYTES - actual_materialized_bytes
                ),
                max_cells=_MAX_BUNDLE_CELLS - actual_materialized_cells,
                max_python_bytes=_MAX_BUNDLE_PYTHON_BYTES - actual_python_bytes,
            )
            actual_materialized_bytes += actual_bytes
            actual_materialized_cells += actual_cells
            actual_python_bytes += actual_python
            if actual_materialized_bytes > _MAX_BUNDLE_MATERIALIZED_BYTES:
                raise GraphRAGArtifactError(
                    "GraphRAG aggregate materialized byte limit exceeded"
                )
            if actual_materialized_cells > _MAX_BUNDLE_CELLS:
                raise GraphRAGArtifactError(
                    "GraphRAG aggregate materialized cell limit exceeded"
                )
            if actual_python_bytes > _MAX_BUNDLE_PYTHON_BYTES:
                raise GraphRAGArtifactError(
                    "GraphRAG aggregate Python materialization limit exceeded"
                )
            if parquet_rows is not None and len(records) != parquet_rows:
                raise GraphRAGArtifactError(
                    "GraphRAG Parquet row count changed during materialization"
                )
            total_records += len(records)
            by_role[entry.role].extend(records)

        document_rows = by_role["text_units"] or by_role["documents"]
        documents = self._normalize_documents(document_rows)
        entities = self._normalize_entities(by_role["entities"])
        relationships = self._normalize_relationships(by_role["relationships"])
        communities = self._normalize_communities(
            by_role["communities"], by_role["community_reports"]
        )
        if not documents:
            raise GraphRAGArtifactError(
                "GraphRAG artifact contains no usable source text"
            )
        return _Bundle(documents, entities, relationships, communities)

    @staticmethod
    def _parquet_materialization(path: Path) -> tuple[int, int, int]:
        try:
            import pyarrow as arrow
            import pyarrow.parquet as parquet
        except ImportError as exc:
            raise GraphRAGArtifactError(
                "safely bounded GraphRAG Parquet output requires pyarrow"
            ) from exc
        try:
            parquet_file = parquet.ParquetFile(path)
            for field in parquet_file.schema_arrow:
                _reject_nested_arrow_type(field.type, arrow)
            metadata = parquet_file.metadata
            if metadata is None:
                raise GraphRAGArtifactError(
                    "GraphRAG Parquet output has no readable metadata"
                )
            rows = int(metadata.num_rows)
            cells = rows * int(metadata.num_columns)
            materialized_bytes = sum(
                int(metadata.row_group(index).total_byte_size)
                for index in range(metadata.num_row_groups)
            )
            if rows < 0 or cells < 0 or materialized_bytes < 0:
                raise GraphRAGArtifactError(
                    "GraphRAG Parquet output has invalid materialization metadata"
                )
            return rows, cells, materialized_bytes
        except GraphRAGArtifactError:
            raise
        except Exception as exc:
            raise GraphRAGArtifactError(
                "unable to inspect GraphRAG Parquet output"
            ) from exc

    @staticmethod
    def _read_records(
        path: Path,
        role: str,
        *,
        max_records: int = _MAX_RECORDS_PER_FILE,
        max_materialized_bytes: int = _MAX_BUNDLE_MATERIALIZED_BYTES,
        max_cells: int = _MAX_BUNDLE_CELLS,
        max_python_bytes: int = _MAX_BUNDLE_PYTHON_BYTES,
    ) -> tuple[list[Mapping[str, Any]], int, int, int]:
        suffix = path.suffix.lower()
        if suffix == ".jsonl":
            records: list[Mapping[str, Any]] = []
            try:
                with path.open("r", encoding="utf-8") as source:
                    for line_number, line in enumerate(source, start=1):
                        if not line.strip():
                            continue
                        if len(records) >= max_records:
                            raise GraphRAGArtifactError(
                                "GraphRAG aggregate record limit exceeded"
                            )
                        record = json.loads(line)
                        if not isinstance(record, Mapping):
                            raise GraphRAGArtifactError(
                                f"{role} JSONL row {line_number} is not an object"
                            )
                        records.append(_json_safe(record))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise GraphRAGArtifactError(f"invalid {role} JSONL output") from exc
            size = path.stat().st_size
            return records, size, 0, size
        if suffix == ".json":
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise GraphRAGArtifactError(f"invalid {role} JSON output") from exc
            if isinstance(payload, Mapping):
                payload = payload.get(role, payload.get("records", payload.get("data")))
            if not isinstance(payload, list) or len(payload) > max_records:
                raise GraphRAGArtifactError(
                    f"{role} JSON output must be a bounded array"
                )
            if not all(isinstance(record, Mapping) for record in payload):
                raise GraphRAGArtifactError(f"{role} JSON records must be objects")
            size = path.stat().st_size
            return [_json_safe(record) for record in payload], size, 0, size
        if suffix == ".parquet":
            try:
                import pyarrow as arrow
                import pyarrow.parquet as parquet

                parquet_file = parquet.ParquetFile(path)
                metadata = parquet_file.metadata
                if metadata is not None and metadata.num_rows > max_records:
                    raise GraphRAGArtifactError(
                        "GraphRAG aggregate record limit exceeded"
                    )
                records = []
                materialized_bytes = 0
                materialized_cells = 0
                python_bytes = 0
                for batch in parquet_file.iter_batches(
                    batch_size=max(1, min(4_096, max_records))
                ):
                    if len(records) + batch.num_rows > max_records:
                        raise GraphRAGArtifactError(
                            "GraphRAG aggregate record limit exceeded"
                        )
                    materialized_bytes += int(batch.nbytes)
                    batch_cells = sum(
                        _arrow_cell_count(batch.column(index), arrow)
                        for index in range(batch.num_columns)
                    )
                    materialized_cells += batch_cells
                    python_bytes += int(
                        batch.nbytes + batch_cells * 64 + batch.num_rows * 64
                    )
                    if materialized_bytes > max_materialized_bytes:
                        raise GraphRAGArtifactError(
                            "GraphRAG aggregate materialized byte limit exceeded"
                        )
                    if materialized_cells > max_cells:
                        raise GraphRAGArtifactError(
                            "GraphRAG aggregate materialized cell limit exceeded"
                        )
                    if python_bytes > max_python_bytes:
                        raise GraphRAGArtifactError(
                            "GraphRAG aggregate Python materialization limit exceeded"
                        )
                    records.extend(batch.to_pylist())
            except GraphRAGArtifactError:
                raise
            except ImportError as exc:
                raise GraphRAGArtifactError(
                    "safely bounded GraphRAG Parquet output requires pyarrow"
                ) from exc
            except Exception as exc:
                raise GraphRAGArtifactError(
                    "unable to read GraphRAG Parquet output"
                ) from exc
            if len(records) > max_records:
                raise GraphRAGArtifactError("GraphRAG aggregate record limit exceeded")
            return (
                [_json_safe(record) for record in records],
                materialized_bytes,
                materialized_cells,
                python_bytes,
            )
        raise GraphRAGArtifactError(
            f"unsupported GraphRAG data file extension for role {role}: {suffix}"
        )

    @staticmethod
    def _normalize_documents(
        rows: Sequence[Mapping[str, Any]],
    ) -> tuple[_Document, ...]:
        result: list[_Document] = []
        seen: set[str] = set()
        for position, row in enumerate(rows):
            external_id = _record_id(row, fallback=f"document-{position + 1}")
            text = _sanitized_text(
                row.get("text") or row.get("content"), limit=2_000_000
            )
            if not text:
                continue
            if external_id in seen:
                raise GraphRAGArtifactError(
                    f"duplicate GraphRAG document id: {external_id}"
                )
            seen.add(external_id)
            document_ids = _string_list(row.get("document_ids"))
            title = _text(row.get("title"), limit=2_000)
            if not title:
                title = document_ids[0] if document_ids else f"Source {external_id}"
            title = _sanitized_text(title, limit=2_000)
            metadata = _metadata(
                row,
                "document_ids",
                "text_unit_ids",
                "relationship_ids",
                "covariate_ids",
                "creation_date",
            )
            metadata_source = (
                metadata.get("source_uri") if isinstance(metadata, Mapping) else None
            )
            source_uri = (
                _sanitized_text(
                    row.get("source_uri") or row.get("url") or metadata_source,
                    limit=8_000,
                )
                or None
            )
            result.append(
                _Document(
                    external_id,
                    title,
                    text,
                    source_uri,
                    _string_list(row.get("entity_ids")),
                    _string_list(row.get("community_ids")),
                    metadata,
                )
            )
        return tuple(result)

    @staticmethod
    def _normalize_entities(rows: Sequence[Mapping[str, Any]]) -> tuple[_Entity, ...]:
        result: list[_Entity] = []
        seen: set[str] = set()
        for position, row in enumerate(rows):
            name = _sanitized_text(row.get("name") or row.get("title"), limit=2_000)
            if not name:
                continue
            external_id = _record_id(row, fallback=f"entity-{position + 1}")
            if external_id in seen:
                raise GraphRAGArtifactError(
                    f"duplicate GraphRAG entity id: {external_id}"
                )
            seen.add(external_id)
            result.append(
                _Entity(
                    external_id,
                    name,
                    normalize_entity_type(row.get("entity_type") or row.get("type")),
                    _sanitized_text(row.get("description"), limit=50_000),
                    _sanitized_string_list(row.get("aliases")),
                    _metadata(row, "frequency", "degree", "text_unit_ids"),
                )
            )
        return tuple(result)

    @staticmethod
    def _normalize_relationships(
        rows: Sequence[Mapping[str, Any]],
    ) -> tuple[_Relationship, ...]:
        result: list[_Relationship] = []
        for position, row in enumerate(rows):
            source = _text(row.get("source") or row.get("source_id"), limit=2_000)
            target = _text(row.get("target") or row.get("target_id"), limit=2_000)
            if not source or not target:
                continue
            try:
                weight = float(row.get("weight", 1.0))
            except (TypeError, ValueError):
                weight = 1.0
            if not math.isfinite(weight):
                weight = 1.0
            result.append(
                _Relationship(
                    _record_id(row, fallback=f"relationship-{position + 1}"),
                    source,
                    target,
                    normalize_predicate(
                        row.get("predicate")
                        or row.get("relationship_type")
                        or row.get("type")
                    ),
                    max(0.0, min(1_000_000.0, weight)),
                )
            )
        return tuple(result)

    @staticmethod
    def _normalize_communities(
        rows: Sequence[Mapping[str, Any]], reports: Sequence[Mapping[str, Any]]
    ) -> tuple[_Community, ...]:
        report_map: dict[str, Mapping[str, Any]] = {}
        for row in reports:
            key = _record_id(row)
            community_key = _text(row.get("community"), limit=500)
            if key:
                report_map[key] = row
            if community_key:
                report_map[community_key] = row
        result: list[_Community] = []
        seen: set[str] = set()
        for position, row in enumerate(rows):
            external_id = _record_id(row, fallback=f"community-{position + 1}")
            if external_id in seen:
                raise GraphRAGArtifactError(
                    f"duplicate GraphRAG community id: {external_id}"
                )
            seen.add(external_id)
            report_row = report_map.get(external_id, {})
            try:
                level = max(
                    0, min(100, int(row.get("level", report_row.get("level", 0))))
                )
            except (TypeError, ValueError):
                level = 0
            report = _sanitized_text(
                report_row.get("full_content")
                or report_row.get("summary")
                or row.get("report")
                or row.get("summary"),
                limit=200_000,
            )
            label = _sanitized_text(
                row.get("label")
                or row.get("title")
                or report_row.get("title")
                or f"Community {external_id}",
                limit=2_000,
            )
            parent = (
                _text(row.get("parent") or report_row.get("parent"), limit=500) or None
            )
            result.append(
                _Community(
                    external_id,
                    label,
                    level,
                    parent,
                    report,
                    _string_list(row.get("entity_ids")),
                )
            )
        return tuple(result)

    def _insert_bundle_documents(
        self,
        connection: sqlite3.Connection,
        *,
        index_id: str,
        space_id: str,
        bundle: _Bundle,
    ) -> None:
        for document in bundle.documents:
            document_id = (
                "grdoc_"
                + stable_hash(self.store.brain_id, index_id, document.external_id)[:32]
            )
            connection.execute(
                "INSERT INTO graphrag_documents(id, brain_id, index_id, knowledge_space_id, "
                "document_id, title, text, source_uri, entity_ids_json, community_ids_json, "
                "metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    document_id,
                    self.store.brain_id,
                    index_id,
                    space_id,
                    document.external_id,
                    document.title,
                    document.text,
                    document.source_uri,
                    _canonical_json(list(document.entity_ids)),
                    _canonical_json(list(document.community_ids)),
                    _canonical_json(sanitize_for_storage(dict(document.metadata))),
                ),
            )
            if self.store._fts_available:
                connection.execute(
                    "INSERT INTO graphrag_fts(id, content) VALUES(?,?)",
                    (document_id, f"{document.title} {document.text}"),
                )

    def _replace_bundle_documents(
        self,
        connection: sqlite3.Connection,
        *,
        index_id: str,
        space_id: str,
        bundle: _Bundle,
    ) -> None:
        if self.store._fts_available:
            rows = connection.execute(
                "SELECT id FROM graphrag_documents WHERE brain_id=? AND index_id=?",
                (self.store.brain_id, index_id),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "DELETE FROM graphrag_fts WHERE id=?", (str(row["id"]),)
                )
        connection.execute(
            "DELETE FROM graphrag_documents WHERE brain_id=? AND index_id=?",
            (self.store.brain_id, index_id),
        )
        self._insert_bundle_documents(
            connection,
            index_id=index_id,
            space_id=space_id,
            bundle=bundle,
        )

    def _project_bundle(
        self, connection: sqlite3.Connection, index: sqlite3.Row, bundle: _Bundle
    ) -> None:
        space_id = self.store.space_id("tekion", connection=connection)
        self._clear_projection(connection, space_id)
        entity_map = self._project_entities(
            connection,
            space_id,
            str(index["id"]),
            str(index["version"]),
            bundle.entities,
        )
        community_map = self._project_communities(
            connection,
            space_id,
            str(index["id"]),
            str(index["version"]),
            bundle.communities,
            entity_map,
        )
        self._project_relationships(
            connection,
            space_id,
            str(index["id"]),
            bundle.relationships,
            entity_map,
        )
        documents = connection.execute(
            "SELECT id, document_id FROM graphrag_documents WHERE brain_id=? AND index_id=?",
            (self.store.brain_id, index["id"]),
        ).fetchall()
        normalized = {document.external_id: document for document in bundle.documents}
        for row in documents:
            document = normalized.get(str(row["document_id"]))
            if not document:
                raise GraphRAGArtifactError(
                    "imported GraphRAG document is missing from artifact"
                )
            connection.execute(
                "UPDATE graphrag_documents SET document_id=?, title=?, text=?, source_uri=?, "
                "entity_ids_json=?, community_ids_json=?, metadata_json=? "
                "WHERE id=? AND brain_id=? AND index_id=?",
                (
                    document.external_id,
                    document.title,
                    document.text,
                    document.source_uri,
                    _canonical_json([
                        entity_map[item]
                        for item in document.entity_ids
                        if item in entity_map
                    ]),
                    _canonical_json([
                        community_map[item]
                        for item in document.community_ids
                        if item in community_map
                    ]),
                    _canonical_json(sanitize_for_storage(dict(document.metadata))),
                    row["id"],
                    self.store.brain_id,
                    index["id"],
                ),
            )
            if self.store._fts_available:
                connection.execute(
                    "DELETE FROM graphrag_fts WHERE id=?", (str(row["id"]),)
                )
                connection.execute(
                    "INSERT INTO graphrag_fts(id, content) VALUES(?,?)",
                    (str(row["id"]), f"{document.title} {document.text}"),
                )

    def _clear_projection(self, connection: sqlite3.Connection, space_id: str) -> None:
        now = utc_now()
        connection.execute(
            "DELETE FROM relations WHERE brain_id=? AND knowledge_space_id=? "
            "AND derived_by LIKE 'graphrag:%'",
            (self.store.brain_id, space_id),
        )
        connection.execute(
            "UPDATE communities SET parent_id=NULL WHERE brain_id=? AND knowledge_space_id=? "
            "AND algorithm_version LIKE 'graphrag:%'",
            (self.store.brain_id, space_id),
        )
        connection.execute(
            "DELETE FROM communities WHERE brain_id=? AND knowledge_space_id=? "
            "AND algorithm_version LIKE 'graphrag:%'",
            (self.store.brain_id, space_id),
        )
        rows = connection.execute(
            "SELECT id, metadata_json FROM entities WHERE brain_id=? AND knowledge_space_id=?",
            (self.store.brain_id, space_id),
        ).fetchall()
        graph_entity_ids: list[str] = []
        for row in rows:
            try:
                metadata = json.loads(row["metadata_json"] or "{}")
            except json.JSONDecodeError:
                metadata = {}
            if metadata.get("source") == "graphrag":
                graph_entity_ids.append(str(row["id"]))
        for entity_id in graph_entity_ids:
            connection.execute(
                "DELETE FROM entity_aliases WHERE entity_id=?", (entity_id,)
            )
            connection.execute(
                "UPDATE entities SET deleted_at=?, last_seen_at=? WHERE id=? AND brain_id=?",
                (now, now, entity_id, self.store.brain_id),
            )
            if self.store._fts_available:
                connection.execute("DELETE FROM entity_fts WHERE id=?", (entity_id,))

    def _purge_bundle_projection(
        self,
        connection: sqlite3.Connection,
        *,
        index_id: str,
        space_id: str,
        bundle: _Bundle,
    ) -> None:
        """Remove verified-bundle graph rows even if provenance was tampered."""
        now = utc_now()
        entity_map: dict[str, str] = {}
        entity_ids: set[str] = set()
        for entity in bundle.entities:
            normalized_name = " ".join(entity.name.lower().split())
            entity_id = (
                "entity_"
                + stable_hash(
                    self.store.brain_id,
                    space_id,
                    entity.entity_type,
                    normalized_name,
                )[:32]
            )
            entity_ids.add(entity_id)
            entity_map[entity.external_id] = entity_id
            entity_map.setdefault(entity.name, entity_id)
            entity_map.setdefault(normalized_name, entity_id)
        for relationship in bundle.relationships:
            subject = entity_map.get(relationship.source) or entity_map.get(
                " ".join(relationship.source.lower().split())
            )
            object_id = entity_map.get(relationship.target) or entity_map.get(
                " ".join(relationship.target.lower().split())
            )
            if not subject or not object_id or subject == object_id:
                continue
            relation_id = (
                "relation_"
                + stable_hash(
                    self.store.brain_id,
                    index_id,
                    relationship.external_id,
                    subject,
                    relationship.predicate,
                    object_id,
                )[:32]
            )
            connection.execute(
                "DELETE FROM relations WHERE id=? AND brain_id=?",
                (relation_id, self.store.brain_id),
            )
        community_ids = {
            "community_"
            + stable_hash(self.store.brain_id, index_id, community.external_id)[:32]
            for community in bundle.communities
        }
        for community_id in community_ids:
            connection.execute(
                "UPDATE communities SET parent_id=NULL WHERE id=? AND brain_id=?",
                (community_id, self.store.brain_id),
            )
        for community_id in community_ids:
            connection.execute(
                "DELETE FROM communities WHERE id=? AND brain_id=?",
                (community_id, self.store.brain_id),
            )
        for entity_id in entity_ids:
            connection.execute(
                "DELETE FROM entity_aliases WHERE entity_id=?", (entity_id,)
            )
            connection.execute(
                "UPDATE entities SET deleted_at=?, last_seen_at=? "
                "WHERE id=? AND brain_id=? AND knowledge_space_id=?",
                (now, now, entity_id, self.store.brain_id, space_id),
            )
            if self.store._fts_available:
                connection.execute("DELETE FROM entity_fts WHERE id=?", (entity_id,))

    def _project_entities(
        self,
        connection: sqlite3.Connection,
        space_id: str,
        index_id: str,
        version: str,
        entities: Sequence[_Entity],
    ) -> dict[str, str]:
        now = utc_now()
        result: dict[str, str] = {}
        for entity in entities:
            normalized_name = " ".join(entity.name.lower().split())
            row = connection.execute(
                "SELECT id, metadata_json FROM entities WHERE brain_id=? AND knowledge_space_id=? "
                "AND entity_type=? AND normalized_name=?",
                (self.store.brain_id, space_id, entity.entity_type, normalized_name),
            ).fetchone()
            metadata = sanitize_for_storage({
                **dict(entity.metadata),
                "source": "graphrag",
                "index_id": index_id,
                "index_version": version,
                "external_id": entity.external_id,
            })
            if row:
                entity_id = str(row["id"])
                try:
                    existing_metadata = json.loads(row["metadata_json"] or "{}")
                except json.JSONDecodeError:
                    existing_metadata = {}
                # Do not relabel a separately governed record that happens to
                # share this normalized name.
                if existing_metadata and existing_metadata.get("source") != "graphrag":
                    metadata = existing_metadata
                connection.execute(
                    "UPDATE entities SET canonical_name=?, description=?, last_seen_at=?, "
                    "metadata_json=?, deleted_at=NULL WHERE id=? AND brain_id=?",
                    (
                        entity.name,
                        entity.description or None,
                        now,
                        _canonical_json(metadata),
                        entity_id,
                        self.store.brain_id,
                    ),
                )
            else:
                entity_id = (
                    "entity_"
                    + stable_hash(
                        self.store.brain_id,
                        space_id,
                        entity.entity_type,
                        normalized_name,
                    )[:32]
                )
                connection.execute(
                    "INSERT INTO entities(id, brain_id, knowledge_space_id, entity_type, "
                    "canonical_name, normalized_name, description, first_seen_at, last_seen_at, "
                    "metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        entity_id,
                        self.store.brain_id,
                        space_id,
                        entity.entity_type,
                        entity.name,
                        normalized_name,
                        entity.description or None,
                        now,
                        now,
                        _canonical_json(metadata),
                    ),
                )
            for alias in {entity.name, *entity.aliases}:
                normalized_alias = " ".join(alias.lower().split())
                if normalized_alias:
                    connection.execute(
                        "INSERT OR IGNORE INTO entity_aliases(entity_id, knowledge_space_id, alias, "
                        "normalized_alias) VALUES(?,?,?,?)",
                        (entity_id, space_id, alias, normalized_alias),
                    )
            if self.store._fts_available:
                indexed_aliases = " ".join(sorted({entity.name, *entity.aliases}))
                connection.execute("DELETE FROM entity_fts WHERE id=?", (entity_id,))
                connection.execute(
                    "INSERT INTO entity_fts(id, content) VALUES(?,?)",
                    (
                        entity_id,
                        f"{entity.name} {entity.description} {indexed_aliases}".strip(),
                    ),
                )
            result[entity.external_id] = entity_id
            result.setdefault(entity.name, entity_id)
            result.setdefault(normalized_name, entity_id)
        return result

    def _project_relationships(
        self,
        connection: sqlite3.Connection,
        space_id: str,
        index_id: str,
        relationships: Sequence[_Relationship],
        entity_map: Mapping[str, str],
    ) -> None:
        now = utc_now()
        for relationship in relationships:
            subject = entity_map.get(relationship.source) or entity_map.get(
                " ".join(relationship.source.lower().split())
            )
            object_id = entity_map.get(relationship.target) or entity_map.get(
                " ".join(relationship.target.lower().split())
            )
            if not subject or not object_id or subject == object_id:
                continue
            relation_id = (
                "relation_"
                + stable_hash(
                    self.store.brain_id,
                    index_id,
                    relationship.external_id,
                    subject,
                    relationship.predicate,
                    object_id,
                )[:32]
            )
            connection.execute(
                "INSERT INTO relations(id, brain_id, knowledge_space_id, subject_entity_id, "
                "predicate, object_entity_id, status, epistemic_status, weight, derived_by, "
                "created_at, updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    relation_id,
                    self.store.brain_id,
                    space_id,
                    subject,
                    relationship.predicate,
                    object_id,
                    "active",
                    "inferred",
                    relationship.weight,
                    f"graphrag:{index_id}",
                    now,
                    now,
                ),
            )

    def _project_communities(
        self,
        connection: sqlite3.Connection,
        space_id: str,
        index_id: str,
        version: str,
        communities: Sequence[_Community],
        entity_map: Mapping[str, str],
    ) -> dict[str, str]:
        now = utc_now()
        result = {
            community.external_id: "community_"
            + stable_hash(self.store.brain_id, index_id, community.external_id)[:32]
            for community in communities
        }
        for community in communities:
            connection.execute(
                "INSERT INTO communities(id, brain_id, knowledge_space_id, level, parent_id, "
                "label, report, algorithm_version, member_ids_json, generated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    result[community.external_id],
                    self.store.brain_id,
                    space_id,
                    community.level,
                    None,
                    community.label,
                    community.report or None,
                    f"graphrag:{index_id}:{version}",
                    _canonical_json([
                        entity_map[item]
                        for item in community.entity_ids
                        if item in entity_map
                    ]),
                    now,
                ),
            )
        for community in communities:
            if community.parent and community.parent in result:
                connection.execute(
                    "UPDATE communities SET parent_id=? WHERE id=? AND brain_id=?",
                    (
                        result[community.parent],
                        result[community.external_id],
                        self.store.brain_id,
                    ),
                )
        return result


def open_graphrag_manager(store: CortexStore, config: Any) -> GraphRAGIndexManager:
    """Build the manager using the active profile's isolated trust scope."""
    env_name = str(
        getattr(config, "graphrag_trusted_public_keys_env", "")
        or "ATLAS_CORTEX_GRAPHRAG_PUBLIC_KEYS"
    )
    # ``get_secret`` reads the current profile context while multiplexing and
    # deliberately refuses to fall through to another profile's process env.
    from agent.secret_scope import get_secret

    raw = str(get_secret(env_name, "") or "").strip()
    trusted: dict[str, str] = {}
    if raw:
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GraphRAGSignatureError(
                f"{env_name} must contain a JSON object of key_id to Ed25519 public key"
            ) from exc
        if not isinstance(decoded, Mapping):
            raise GraphRAGSignatureError(
                f"{env_name} must contain a JSON object of trusted public keys"
            )
        trusted = {
            str(key): str(value)
            for key, value in decoded.items()
            if str(key).strip() and str(value).strip()
        }
    return GraphRAGIndexManager(
        store,
        getattr(config, "graphrag_index_root"),
        trusted_public_keys=trusted,
        require_signature=bool(getattr(config, "graphrag_require_signature", False))
        or str(store.owner_customer_id).startswith("managed:"),
    )


__all__ = [
    "ArtifactManifest",
    "GraphRAGArtifactError",
    "GraphRAGIndex",
    "GraphRAGIndexManager",
    "GraphRAGProjectionError",
    "GraphRAGSignatureError",
    "MANIFEST_SCHEMA_VERSION",
    "MICROSOFT_FORMAT",
    "PORTABLE_FORMAT",
    "canonical_manifest_payload",
    "sha256_file",
    "open_graphrag_manager",
]
