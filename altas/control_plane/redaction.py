"""Conservative secret redaction before caller-controlled data is persisted."""

from __future__ import annotations

import re
from typing import Any


REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "browser_session",
    "cookie",
    "credential",
    "credentials",
    "dealer_key",
    "password",
    "provider_key",
    "refresh_token",
    "secret",
    "session_cookie",
    "set_cookie",
    "tekion_key",
    "token",
    "access_token",
}
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|dealer[_-]?key|"
    r"tekion[_-]?(?:key|credential)|authorization|password|secret|session[_-]?cookie)"
    r"\s*[:=]\s*[^\s,;]+"
)
_URL_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"dealer[_-]?key|tekion[_-]?key|token|secret)=)[^&#\s]+"
)
_PROVIDER_KEY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:sk-(?:proj-)?|xox[baprs]-|gh[pousr]_)[A-Za-z0-9_-]{16,}"
)
_JWT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{10,}\."
    r"[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"
)
_CAMEL_BOUNDARY_PATTERN = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NON_WORD_PATTERN = re.compile(r"[^a-z0-9]+")


def _normalized_key(key: object) -> str:
    split_camel = _CAMEL_BOUNDARY_PATTERN.sub("_", str(key).strip())
    return _NON_WORD_PATTERN.sub("_", split_camel.lower()).strip("_")


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    return normalized in _SENSITIVE_KEYS or normalized.endswith((
        "_api_key",
        "_cookie",
        "_credential",
        "_credentials",
        "_password",
        "_secret",
        "_token",
    ))


def sanitize_for_storage(value: Any, *, _depth: int = 0) -> Any:
    """Return a JSON-compatible value with likely credentials removed.

    This is a defense-in-depth filter, not a substitute for callers keeping
    secrets out of telemetry. Depth and collection bounds also prevent a
    diagnostic payload from growing the local control database without limit.
    """

    if _depth >= 12:
        return "[TRUNCATED:MAX_DEPTH]"
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for index, (key, child) in enumerate(value.items()):
            if index >= 1_000:
                sanitized["_truncated"] = "[TRUNCATED:MAX_ITEMS]"
                break
            output_key = str(key)
            if _is_sensitive_key(key):
                sanitized[output_key] = REDACTED
            else:
                sanitized[output_key] = sanitize_for_storage(child, _depth=_depth + 1)
        return sanitized
    if isinstance(value, list | tuple):
        return [
            sanitize_for_storage(child, _depth=_depth + 1) for child in value[:1_000]
        ]
    if isinstance(value, str):
        redacted = _BEARER_PATTERN.sub("Bearer [REDACTED]", value)
        redacted = _ASSIGNMENT_PATTERN.sub(
            lambda match: f"{match.group(1)}=[REDACTED]", redacted
        )
        redacted = _URL_SECRET_PATTERN.sub(
            lambda match: f"{match.group(1)}[REDACTED]", redacted
        )
        redacted = _PROVIDER_KEY_PATTERN.sub(REDACTED, redacted)
        return _JWT_PATTERN.sub(REDACTED, redacted)
    if value is None or isinstance(value, bool | int | float):
        return value
    return sanitize_for_storage(str(value), _depth=_depth + 1)
