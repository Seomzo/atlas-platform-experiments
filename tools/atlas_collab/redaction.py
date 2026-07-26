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
