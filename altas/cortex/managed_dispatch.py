"""Strict provenance contract for managed Atlas Cortex model dispatches.

The envelope intentionally contains no conversation text, customer identifier,
or brain identifier.  It binds a control-plane maintenance job to one exact
locally admitted semantic job while leaving the owner's Cortex database as the
authority for whether that admission is genuine.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Mapping


CORTEX_DISPATCH_SCHEMA = "atlas.cortex.dispatch-admission.v1"
_HEX_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_LOCAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_EXPECTED_FIELDS = frozenset(
    {
        "schema_version",
        "local_job_id",
        "admission_id",
        "root_job_id",
        "canonical_input_hash",
        "attempt",
        "due_at",
        "dispatch_key_commitment",
    }
)


def _digest(*parts: object) -> str:
    encoded = "\x1f".join(str(part) for part in parts).encode(
        "utf-8", errors="strict"
    )
    return hashlib.sha256(encoded).hexdigest()


def dispatch_key_commitment(dispatch_key: str) -> str:
    """Return the domain-separated commitment persisted by the control plane."""

    if not isinstance(dispatch_key, str) or not _HEX_DIGEST.fullmatch(dispatch_key):
        raise ValueError("invalid Cortex dispatch key")
    return _digest("atlas-cortex-managed-dispatch-key-commitment-v1", dispatch_key)


@dataclass(frozen=True, slots=True)
class CortexDispatchAdmission:
    """Privacy-safe identity of one exact leaseable local semantic job."""

    schema_version: str
    local_job_id: str
    admission_id: str
    root_job_id: str
    canonical_input_hash: str
    attempt: int
    due_at: str
    dispatch_key_commitment: str

    def __post_init__(self) -> None:
        if self.schema_version != CORTEX_DISPATCH_SCHEMA:
            raise ValueError("unsupported Cortex dispatch admission schema")
        for name in ("local_job_id", "admission_id", "root_job_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _LOCAL_ID.fullmatch(value):
                raise ValueError(f"invalid Cortex dispatch admission {name}")
        for name in ("canonical_input_hash", "dispatch_key_commitment"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _HEX_DIGEST.fullmatch(value):
                raise ValueError(f"invalid Cortex dispatch admission {name}")
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int):
            raise ValueError("invalid Cortex dispatch admission attempt")
        if self.attempt < 0 or self.attempt > 1_000_000:
            raise ValueError("invalid Cortex dispatch admission attempt")
        if not isinstance(self.due_at, str) or not (1 <= len(self.due_at) <= 64):
            raise ValueError("invalid Cortex dispatch admission due_at")
        try:
            parsed_due_at = datetime.fromisoformat(self.due_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid Cortex dispatch admission due_at") from exc
        if parsed_due_at.tzinfo is None:
            raise ValueError("invalid Cortex dispatch admission due_at")

    @classmethod
    def create(
        cls,
        *,
        local_job_id: str,
        admission_id: str,
        root_job_id: str,
        canonical_input_hash: str,
        attempt: int,
        due_at: str,
    ) -> tuple["CortexDispatchAdmission", str]:
        """Build an admission and its deterministic retry-safe dispatch key."""

        dispatch_key = _digest(
            "atlas-cortex-managed-dispatch-v2",
            CORTEX_DISPATCH_SCHEMA,
            local_job_id,
            admission_id,
            root_job_id,
            canonical_input_hash,
            attempt,
            due_at,
        )
        admission = cls(
            schema_version=CORTEX_DISPATCH_SCHEMA,
            local_job_id=local_job_id,
            admission_id=admission_id,
            root_job_id=root_job_id,
            canonical_input_hash=canonical_input_hash,
            attempt=attempt,
            due_at=due_at,
            dispatch_key_commitment=dispatch_key_commitment(dispatch_key),
        )
        return admission, dispatch_key

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CortexDispatchAdmission":
        """Decode an exact envelope; extra and missing fields are rejected."""

        if not isinstance(value, Mapping) or frozenset(value) != _EXPECTED_FIELDS:
            raise ValueError("invalid Cortex dispatch admission fields")
        return cls(
            schema_version=value["schema_version"],
            local_job_id=value["local_job_id"],
            admission_id=value["admission_id"],
            root_job_id=value["root_job_id"],
            canonical_input_hash=value["canonical_input_hash"],
            attempt=value["attempt"],
            due_at=value["due_at"],
            dispatch_key_commitment=value["dispatch_key_commitment"],
        )

    @classmethod
    def from_header(cls, value: str) -> "CortexDispatchAdmission":
        """Decode the bounded base64url representation sent to the model API."""

        if not isinstance(value, str) or not (1 <= len(value) <= 2_048):
            raise ValueError("invalid Cortex dispatch admission header")
        try:
            padding = "=" * (-len(value) % 4)
            raw = base64.b64decode(
                value + padding,
                altchars=b"-_",
                validate=True,
            )
            decoded = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("invalid Cortex dispatch admission header") from exc
        if not isinstance(decoded, dict):
            raise ValueError("invalid Cortex dispatch admission header")
        return cls.from_mapping(decoded)

    def to_mapping(self) -> dict[str, Any]:
        return asdict(self)

    def canonical_json(self) -> str:
        return json.dumps(
            self.to_mapping(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    def to_header(self) -> str:
        return base64.urlsafe_b64encode(self.canonical_json().encode("ascii")).decode(
            "ascii"
        ).rstrip("=")

    def matches_dispatch_key(self, dispatch_key: str) -> bool:
        try:
            actual = dispatch_key_commitment(dispatch_key)
        except ValueError:
            return False
        return hmac.compare_digest(
            actual.encode("ascii"), self.dispatch_key_commitment.encode("ascii")
        )

    def canonical_dispatch_key(self) -> str:
        """Derive the only valid key for this envelope's provenance fields."""

        _admission, dispatch_key = type(self).create(
            local_job_id=self.local_job_id,
            admission_id=self.admission_id,
            root_job_id=self.root_job_id,
            canonical_input_hash=self.canonical_input_hash,
            attempt=self.attempt,
            due_at=self.due_at,
        )
        return dispatch_key

    def is_canonical(self) -> bool:
        return self.matches_dispatch_key(self.canonical_dispatch_key())

    def matches_job(self, job: Mapping[str, Any]) -> bool:
        """Constant-time where secret-like hashes are involved, exact otherwise."""

        try:
            identity_matches = (
                str(job.get("id") or "") == self.local_job_id
                and str(job.get("admission_id") or "") == self.admission_id
                and str(job.get("root_job_id") or "") == self.root_job_id
                and int(job.get("attempt")) == self.attempt
                and str(job.get("due_at") or "") == self.due_at
            )
            supplied_hash = str(job.get("input_hash") or "")
        except (TypeError, ValueError):
            return False
        if not _HEX_DIGEST.fullmatch(supplied_hash):
            return False
        return identity_matches and hmac.compare_digest(
            supplied_hash,
            self.canonical_input_hash,
        )


__all__ = [
    "CORTEX_DISPATCH_SCHEMA",
    "CortexDispatchAdmission",
    "dispatch_key_commitment",
]
