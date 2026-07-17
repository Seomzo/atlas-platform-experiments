"""Conservative secret redaction before caller-controlled data is persisted."""

from __future__ import annotations

import re
from typing import Any


REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {
    "access_key_id",
    "api_key",
    "apikey",
    "authorization",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_secret_key",
    "aws_security_token",
    "aws_session_token",
    "bearer",
    "browser_session",
    "client_secret",
    "cookie",
    "credential",
    "credentials",
    "dealer_key",
    "password",
    "private_key",
    "provider_key",
    "refresh_token",
    "secret",
    "secret_access_key",
    "session_cookie",
    "session_token",
    "set_cookie",
    "ssh_private_key",
    "stripe_secret_key",
    "stripe_webhook_secret",
    "tekion_key",
    "token",
    "access_token",
}
_PEM_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN (?P<label>(?:[A-Z0-9][A-Z0-9_-]* )*PRIVATE KEY)-----"
    r".*?(?:-----END (?P=label)-----|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_DATABASE_URI_USERINFO_PATTERN = re.compile(
    r"(?i)(?P<scheme>\b(?:postgres(?:ql)?|mysql|mongodb|redis|rediss)"
    r"(?:\+[a-z0-9_.-]+)?://)[^/?#\s]+@"
)
_AUTHORIZATION_SCHEME_PATTERN = re.compile(
    r"(?i)(?P<prefix>\b(?:proxy[-_ ]?)?authorization(?:[\"'])?\s*[:=]\s*"
    r"(?:[\"'])?)(?P<scheme>basic|bearer)(?P<spacing>\s+)"
    r"(?P<credential>[^\s,;}\[\]\"']+)"
)
_AUTHORIZATION_VALUE_PATTERN = re.compile(
    r"(?i)(?P<prefix>\b(?:proxy[-_ ]?)?authorization(?:[\"'])?\s*[:=]\s*"
    r"(?:[\"'])?)(?!basic\b|bearer\b)(?P<credential>[^\s,;}\[\]\"']+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|dealer[_-]?key|"
    r"tekion[_-]?(?:key|credential)|password|secret|session[_-]?cookie|"
    r"(?:aws[_-]?)?access[_-]?key[_-]?id|"
    r"(?:aws[_-]?)?secret[_-]?access[_-]?key|"
    r"aws[_-]?(?:security|session)[_-]?token|private[_-]?key)"
    r"(?:[\"'])?\s*[:=]\s*[^\s,;]+"
)
_URL_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"dealer[_-]?key|tekion[_-]?key|token|secret)=)[^&#\s]+"
)
_PROVIDER_KEY_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:sk-(?:proj-)?|xox[baprs]-|gh[pousr]_)[A-Za-z0-9_-]{16,}"
)
_AWS_ACCESS_KEY_PATTERN = re.compile(
    r"(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"
)
_GOOGLE_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:AIza[A-Za-z0-9_-]{35}|"
    r"GOCSPX-[A-Za-z0-9_-]{20,}|ya29\.[A-Za-z0-9_-]{20,})(?![A-Za-z0-9_-])"
)
_GITLAB_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:glpat|gldt|glrt|glcbt|glptt|glft|glffct|"
    r"glimt|glsoat|gloas|glagent)-[A-Za-z0-9_-]{10,}(?![A-Za-z0-9_-])"
)
_NPM_TOKEN_PATTERN = re.compile(r"(?<![A-Za-z0-9_])npm_[A-Za-z0-9]{20,}(?![A-Za-z0-9])")
_STRIPE_TOKEN_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])(?:(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}|"
    r"whsec_[A-Za-z0-9]{16,})(?![A-Za-z0-9])"
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
        "_private_key",
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
        redacted = _PEM_PRIVATE_KEY_PATTERN.sub(REDACTED, value)
        redacted = _DATABASE_URI_USERINFO_PATTERN.sub(
            lambda match: f"{match.group('scheme')}{REDACTED}@", redacted
        )
        redacted = _AUTHORIZATION_SCHEME_PATTERN.sub(
            lambda match: (
                f"{match.group('prefix')}{match.group('scheme')}"
                f"{match.group('spacing')}{REDACTED}"
            ),
            redacted,
        )
        redacted = _AUTHORIZATION_VALUE_PATTERN.sub(
            lambda match: f"{match.group('prefix')}{REDACTED}", redacted
        )
        redacted = _BEARER_PATTERN.sub("Bearer [REDACTED]", redacted)
        redacted = _ASSIGNMENT_PATTERN.sub(
            lambda match: f"{match.group(1)}=[REDACTED]", redacted
        )
        redacted = _URL_SECRET_PATTERN.sub(
            lambda match: f"{match.group(1)}[REDACTED]", redacted
        )
        redacted = _PROVIDER_KEY_PATTERN.sub(REDACTED, redacted)
        redacted = _AWS_ACCESS_KEY_PATTERN.sub(REDACTED, redacted)
        redacted = _GOOGLE_TOKEN_PATTERN.sub(REDACTED, redacted)
        redacted = _GITLAB_TOKEN_PATTERN.sub(REDACTED, redacted)
        redacted = _NPM_TOKEN_PATTERN.sub(REDACTED, redacted)
        redacted = _STRIPE_TOKEN_PATTERN.sub(REDACTED, redacted)
        return _JWT_PATTERN.sub(REDACTED, redacted)
    if value is None or isinstance(value, bool | int | float):
        return value
    return sanitize_for_storage(str(value), _depth=_depth + 1)
