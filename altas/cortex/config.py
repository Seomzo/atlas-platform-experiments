"""Configuration and path policy for the Atlas Cortex subsystem."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


CORTEX_NAME = "Atlas Cortex"
CORTEX_PROVIDER = "cortex"
CORTEX_SCHEMA_VERSION = 2


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _bounded_int(value: Any, default: int, *, minimum: int, maximum: int) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    return min(maximum, max(minimum, result))


def _bool(value: Any, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _safe_profile_path(home: Path, raw: Any, default: str) -> Path:
    """Resolve a config path without allowing it to escape the active profile."""
    home = home.expanduser().resolve()
    candidate = Path(str(raw or default)).expanduser()
    if not candidate.is_absolute():
        candidate = home / candidate
    resolved_parent = candidate.parent.resolve()
    try:
        resolved_parent.relative_to(home)
    except ValueError as exc:
        raise ValueError(
            "Cortex storage paths must stay inside the active Atlas profile"
        ) from exc
    # Reject dangling links too; O_CREAT would otherwise follow one and create
    # its target after the containment check.
    if candidate.is_symlink():
        raise ValueError("Cortex storage path must not be a symbolic link")
    return resolved_parent / candidate.name


@dataclass(frozen=True)
class CortexConfig:
    enabled: bool
    profile_home: Path
    database_path: Path
    capture_enabled: bool
    capture_assistant: bool
    capture_tools: bool
    capture_max_chars: int
    raw_evidence_retention_days: int
    recall_enabled: bool
    recall_max_items: int
    recall_max_chars: int
    recall_graph_hops: int
    dream_enabled: bool
    dream_local_time: str
    dream_startup_catchup: bool
    dream_poll_seconds: int
    dream_lease_seconds: int
    dream_max_batch: int
    graphrag_enabled: bool
    graphrag_index_root: Path
    graphrag_max_items: int
    graphrag_require_signature: bool
    graphrag_trusted_public_keys_env: str
    redact_secrets: bool
    sensitive_requires_review: bool
    approved_model_providers: tuple[str, ...]
    timezone: str

    @classmethod
    def from_mapping(
        cls, config: Mapping[str, Any], hermes_home: str | Path
    ) -> "CortexConfig":
        root = _mapping(config.get("cortex"))
        storage = _mapping(root.get("storage"))
        capture = _mapping(root.get("capture"))
        recall = _mapping(root.get("recall"))
        dream = _mapping(root.get("dream"))
        graphrag = _mapping(root.get("graphrag"))
        security = _mapping(root.get("security"))
        home = Path(hermes_home)

        backend = str(storage.get("backend", "sqlite")).strip().lower()
        if backend != "sqlite":
            raise ValueError(
                "This Atlas runtime supports cortex.storage.backend=sqlite; "
                "managed PostgreSQL must be supplied by the Cortex service adapter"
            )

        local_time = str(dream.get("local_time", "04:00")).strip()
        pieces = local_time.split(":")
        try:
            valid_time = (
                len(pieces) == 2
                and 0 <= int(pieces[0]) <= 23
                and 0 <= int(pieces[1]) <= 59
            )
        except ValueError:
            valid_time = False
        if not valid_time:
            local_time = "04:00"

        timezone = str(
            root.get("timezone") or config.get("timezone") or "local"
        ).strip()
        return cls(
            enabled=_bool(root.get("enabled"), True),
            profile_home=home.expanduser().resolve(),
            database_path=_safe_profile_path(
                home, storage.get("path"), "cortex/cortex.db"
            ),
            capture_enabled=_bool(capture.get("enabled"), True),
            capture_assistant=_bool(capture.get("assistant_evidence"), True),
            capture_tools=_bool(capture.get("tool_evidence"), True),
            capture_max_chars=_bounded_int(
                capture.get("max_content_chars"),
                250_000,
                minimum=1_000,
                maximum=2_000_000,
            ),
            raw_evidence_retention_days=_bounded_int(
                capture.get("raw_evidence_retention_days"),
                0,
                minimum=0,
                maximum=3_650,
            ),
            recall_enabled=_bool(recall.get("enabled"), True),
            recall_max_items=_bounded_int(
                recall.get("max_items"), 8, minimum=1, maximum=30
            ),
            recall_max_chars=_bounded_int(
                recall.get("max_chars"), 6_000, minimum=500, maximum=30_000
            ),
            recall_graph_hops=_bounded_int(
                recall.get("graph_hops"), 1, minimum=0, maximum=2
            ),
            dream_enabled=_bool(dream.get("enabled"), True),
            dream_local_time=local_time,
            dream_startup_catchup=_bool(dream.get("startup_catchup"), True),
            dream_poll_seconds=_bounded_int(
                dream.get("poll_seconds"), 300, minimum=30, maximum=3_600
            ),
            dream_lease_seconds=_bounded_int(
                dream.get("lease_seconds"), 1_800, minimum=60, maximum=21_600
            ),
            dream_max_batch=_bounded_int(
                dream.get("max_batch"), 100, minimum=1, maximum=100
            ),
            graphrag_enabled=_bool(graphrag.get("enabled"), True),
            graphrag_index_root=_safe_profile_path(
                home, graphrag.get("index_root"), "cortex/graphrag"
            ),
            graphrag_max_items=_bounded_int(
                graphrag.get("max_items"), 6, minimum=1, maximum=20
            ),
            graphrag_require_signature=_bool(graphrag.get("require_signature"), False),
            graphrag_trusted_public_keys_env=str(
                graphrag.get("trusted_public_keys_env")
                or "ATLAS_CORTEX_GRAPHRAG_PUBLIC_KEYS"
            ).strip(),
            redact_secrets=_bool(security.get("redact_secrets"), True),
            sensitive_requires_review=_bool(
                security.get("sensitive_requires_review"), True
            ),
            approved_model_providers=tuple(
                str(value).strip().lower()
                for value in security.get("approved_model_providers", [])
                if str(value).strip()
            )
            if isinstance(security.get("approved_model_providers", []), (list, tuple))
            else (),
            timezone=timezone or "local",
        )

    @classmethod
    def load(cls, hermes_home: str | Path) -> "CortexConfig":
        from hermes_cli.config import load_config
        from hermes_constants import (
            reset_hermes_home_override,
            set_hermes_home_override,
        )

        # ``load_config`` intentionally resolves the active profile through
        # ``get_hermes_home()``.  Cortex callers, however, frequently open a
        # *specific* profile from a multiplexed gateway/API process.  Scope
        # that lookup explicitly so ``CortexConfig.load(profile_b)`` cannot
        # silently read profile A's config while merely placing A's values
        # under B's filesystem root.  ContextVar scoping keeps this safe for
        # concurrent profile requests without mutating process-wide env vars.
        home = Path(hermes_home).expanduser().resolve()
        token = set_hermes_home_override(home)
        try:
            raw = load_config()
        finally:
            reset_hermes_home_override(token)
        return cls.from_mapping(raw, home)
