"""Secret rejection and log redaction."""

from __future__ import annotations

import re
from typing import Any

_SECRET_KEYS = re.compile(
    r"(api[_-]?key|private[_-]?key|password|secret|token|cookie|authorization)",
    re.IGNORECASE,
)
_SECRET_VALUES = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bnsec1[a-z0-9]{20,}\b", re.IGNORECASE),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}=*\b"),
)


class SecretMaterialError(ValueError):
    """Secret-like material crossed a non-secret boundary."""


class UnsafeInstructionError(ValueError):
    """Untrusted task/event content requested a forbidden control action."""


_UNSAFE_DIRECTIVES = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^ignore\b.{0,60}\b(?:instruction|system|policy|guard)",
        r"^(?:reveal|print|send|post|upload|exfiltrate)\b.{0,60}"
        r"\b(?:secret|api[ _-]?key|private[ _-]?key|token|password|cookie)",
        r"^(?:disable|skip|bypass|remove)\b.{0,40}"
        r"\b(?:test|security|approval|permission|guard)",
        r"^(?:run\s+)?git\s+(?:push\s+--force|reset\s+--hard|clean\s+-|branch\s+-D)",
        r"^(?:merge|approve|mark)\b.{0,30}\b(?:pull request|pr|ready)",
        r"^(?:delete|rewrite)\b.{0,30}\b(?:history|branch|commit)",
        r"^(?:grant|widen|change)\b.{0,30}\b(?:permission|scope|access)",
        r"^(?:follow|execute)\b.{0,30}\b(?:hidden|embedded)\b.{0,20}\binstruction",
    )
)
_NEGATED_DIRECTIVE = re.compile(
    r"^(?:do not|don't|never|must not|cannot|can't|no)\b",
    re.IGNORECASE,
)


def redact(text: str) -> str:
    for pattern in _SECRET_VALUES:
        text = pattern.sub("[REDACTED]", text)
    return text


def assert_non_secret(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if _SECRET_KEYS.search(str(key)):
                raise SecretMaterialError(f"secret-like key rejected at {path}.{key}")
            assert_non_secret(nested, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            assert_non_secret(nested, f"{path}[{index}]")
    elif isinstance(value, str) and redact(value) != value:
        raise SecretMaterialError(f"secret-like value rejected at {path}")


def assert_safe_untrusted(value: Any, path: str = "root") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            assert_safe_untrusted(nested, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, nested in enumerate(value):
            assert_safe_untrusted(nested, f"{path}[{index}]")
    elif isinstance(value, str):
        for line_number, raw_line in enumerate(value.splitlines(), start=1):
            line = re.sub(
                r"^\s*(?:[-*]\s*)?(?:\[[ xX]\]\s*)?",
                "",
                raw_line,
            ).strip()
            if not line or _NEGATED_DIRECTIVE.match(line):
                continue
            if any(pattern.search(line) for pattern in _UNSAFE_DIRECTIVES):
                raise UnsafeInstructionError(
                    f"unsafe untrusted instruction rejected at {path}:{line_number}"
                )
