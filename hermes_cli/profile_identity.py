"""Per-profile worker identity storage.

Identity metadata lives at ``<HERMES_HOME>/identity.json``.  Callers pass the
resolved profile home explicitly so dashboard requests can manage any profile
without changing the process-wide active profile.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any


IDENTITY_FILENAME = "identity.json"
AVATARS_DIRNAME = "avatars"

_TEXT_LIMITS = {
    "display_name": 80,
    "role": 80,
    "tagline": 200,
}
_IDENTITY_FIELDS = frozenset((*_TEXT_LIMITS, "avatar"))
_AVATAR_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


def default_profile_identity(profile_name: str) -> dict[str, Any]:
    """Return the complete identity defaults for ``profile_name``."""
    return {
        "display_name": profile_name,
        "role": "",
        "tagline": "",
        "avatar": None,
    }


def validate_identity_update(update: Mapping[str, Any]) -> dict[str, str]:
    """Validate and normalize a partial user-editable identity update."""
    if not isinstance(update, Mapping):
        raise ValueError("Identity update must be a JSON object")

    unknown = set(update) - set(_TEXT_LIMITS)
    if unknown:
        fields = ", ".join(sorted(str(field) for field in unknown))
        raise ValueError(f"Unsupported identity field(s): {fields}")

    normalized: dict[str, str] = {}
    for field, value in update.items():
        if type(value) is not str:
            raise ValueError(f"{field} must be a string")
        text = value.strip()
        limit = _TEXT_LIMITS[field]
        if len(text) > limit:
            raise ValueError(f"{field} must be at most {limit} characters")
        normalized[field] = text
    return normalized


def _normalize_avatar(value: Any) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        raise ValueError("avatar must be a relative filename or null")

    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or len(path.parts) != 2
        or path.parts[0] != AVATARS_DIRNAME
        or path.name in {"", ".", ".."}
        or path.suffix.lower() not in _AVATAR_SUFFIXES
    ):
        raise ValueError("avatar must be an image filename under avatars/")
    return path.as_posix()


def load_profile_identity(profile_home: Path, profile_name: str) -> dict[str, Any]:
    """Load a complete identity, falling back safely for missing/bad files."""
    defaults = default_profile_identity(profile_name)
    path = profile_home / IDENTITY_FILENAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return defaults

        text_values = {
            field: raw[field]
            for field in _TEXT_LIMITS
            if field in raw
        }
        identity = dict(defaults)
        identity.update(validate_identity_update(text_values))
        identity["avatar"] = _normalize_avatar(raw.get("avatar"))
        return identity
    except Exception:
        # Profile listing must remain available even when a user edits this
        # small metadata file by hand and leaves it unreadable or malformed.
        return defaults


def save_profile_identity(
    profile_home: Path,
    profile_name: str,
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and atomically persist a complete identity record."""
    if not isinstance(identity, Mapping):
        raise ValueError("Identity must be a mapping")
    unknown = set(identity) - _IDENTITY_FIELDS
    if unknown:
        fields = ", ".join(sorted(str(field) for field in unknown))
        raise ValueError(f"Unsupported identity field(s): {fields}")

    normalized = default_profile_identity(profile_name)
    normalized.update(
        validate_identity_update({
            field: identity[field]
            for field in _TEXT_LIMITS
            if field in identity
        })
    )
    normalized["avatar"] = _normalize_avatar(identity.get("avatar"))

    path = profile_home / IDENTITY_FILENAME
    fd, tmp_name = tempfile.mkstemp(
        prefix=".identity.",
        suffix=".tmp",
        dir=str(profile_home),
    )
    tmp_path = Path(tmp_name)
    replaced = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(normalized, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp_path, path)
        replaced = True
    finally:
        if not replaced:
            tmp_path.unlink(missing_ok=True)

    return normalized


def resolve_profile_avatar_path(
    profile_home: Path,
    identity: Mapping[str, Any],
) -> Path | None:
    """Resolve an identity avatar without allowing it outside ``avatars/``."""
    try:
        relative = _normalize_avatar(identity.get("avatar"))
        if relative is None:
            return None
        avatars_root = (profile_home / AVATARS_DIRNAME).resolve()
        candidate = (profile_home / relative).resolve()
        if candidate.parent != avatars_root:
            return None
        return candidate
    except (OSError, RuntimeError, ValueError):
        return None


def has_profile_avatar(
    profile_home: Path,
    identity: Mapping[str, Any],
) -> bool:
    """Return whether the identity points to a readable avatar file."""
    avatar_path = resolve_profile_avatar_path(profile_home, identity)
    try:
        return avatar_path is not None and avatar_path.is_file()
    except OSError:
        return False
