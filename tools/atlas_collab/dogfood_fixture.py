"""Harmless fixture used by the WS-22 collaboration dogfood."""

CANONICAL_ROLES = frozenset({"coordinator", "implementer", "reviewer"})


def role_label(value: str) -> str:
    """Return a supported role label for a structured collaboration event."""
    if value not in CANONICAL_ROLES:
        raise ValueError(f"unsupported collaboration role: {value!r}")
    return value
