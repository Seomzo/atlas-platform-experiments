"""Canonical context manifests without embedding source contents."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .models import content_hash


def build_context_manifest(root: Path, paths: list[str]) -> dict[str, Any]:
    entries = []
    for relative in sorted(set(paths)):
        path = (root / relative).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError(f"context path escapes repository: {relative}") from exc
        if not path.is_file():
            raise FileNotFoundError(relative)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        entries.append({
            "path": relative,
            "sha256": digest,
            "size": path.stat().st_size,
        })
    manifest = {
        "schema_version": "atlas.collab.context.v1",
        "entries": entries,
    }
    manifest["manifest_hash"] = content_hash(manifest)
    return manifest
