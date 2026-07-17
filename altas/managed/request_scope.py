"""Request-local managed authorization bound to one trusted Atlas profile."""

from __future__ import annotations

import hmac
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType
from typing import Iterator, Mapping

from agent.secret_scope import (
    build_profile_secret_scope,
    reset_secret_scope,
    set_secret_scope,
)
from altas.managed.context import ManagedContext, ManagedRequestAuthorization


class ManagedRequestScopeError(PermissionError):
    """The authorized job does not bind exactly to the requested profile."""


_PROFILE_IDENTITY_FIELDS = (
    ("ATLAS_TENANT_ID", "tenant_id"),
    ("ATLAS_STORE_ID", "store_id"),
    ("ATLAS_AGENT_ID", "agent_id"),
    ("ATLAS_DEVICE_ID", "device_id"),
)


def validate_managed_profile_identity(
    profile_home: str | Path,
    context: ManagedContext,
) -> Mapping[str, str]:
    """Return profile secrets only when identity matches claimed job context."""

    if not isinstance(context, ManagedContext):
        raise TypeError("context must be a ManagedContext")
    profile_secrets = build_profile_secret_scope(Path(profile_home))
    for env_name, context_field in _PROFILE_IDENTITY_FIELDS:
        actual = profile_secrets.get(env_name, "")
        expected = getattr(context, context_field)
        if not actual or not hmac.compare_digest(
            actual.encode("utf-8"), expected.encode("utf-8")
        ):
            raise ManagedRequestScopeError(
                f"managed request profile identity mismatch: {env_name}"
            )
    return MappingProxyType(profile_secrets)


def build_managed_request_secret_scope(
    profile_home: str | Path,
    authorization: ManagedRequestAuthorization,
) -> Mapping[str, str]:
    """Build an immutable profile scope with one ephemeral job authorization.

    Static identity and credentials remain profile-owned in ``<home>/.env``.
    The caller supplies only a typed, already-authorized job request.  Every
    identity component must be present and match exactly before any ephemeral
    value is overlaid.  The source mapping, process environment, and file are
    never mutated.
    """

    if not isinstance(authorization, ManagedRequestAuthorization):
        raise TypeError("authorization must be a ManagedRequestAuthorization")

    profile_secrets = validate_managed_profile_identity(
        profile_home,
        authorization.context,
    )
    profile_control_plane = str(
        profile_secrets.get("ATLAS_CONTROL_PLANE_URL") or ""
    ).rstrip("/")
    if not profile_control_plane or not hmac.compare_digest(
        profile_control_plane.encode("utf-8"),
        authorization.control_plane_url.encode("utf-8"),
    ):
        raise ManagedRequestScopeError(
            "managed request profile endpoint mismatch: ATLAS_CONTROL_PLANE_URL"
        )

    scoped = dict(profile_secrets)
    scoped.update(authorization.ephemeral_scope_overlay())
    return MappingProxyType(scoped)


@contextmanager
def managed_request_scope(
    profile_home: str | Path,
    authorization: ManagedRequestAuthorization,
) -> Iterator[Mapping[str, str]]:
    """Install one bound job authorization and restore the prior scope."""

    scoped = build_managed_request_secret_scope(profile_home, authorization)
    token = set_secret_scope(scoped)
    try:
        yield scoped
    finally:
        reset_secret_scope(token)


__all__ = [
    "ManagedRequestScopeError",
    "build_managed_request_secret_scope",
    "managed_request_scope",
    "validate_managed_profile_identity",
]
