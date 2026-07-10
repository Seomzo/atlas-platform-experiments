"""Opaque credential-vault contract.

Raw connector and device secrets must not be placed in model-visible tool
arguments, configuration files, logs, audit metadata, or workflow results.
Trusted code refers to a secret through `SecretHandle` and resolves it only at
the final use boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


_HANDLE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class CredentialNotFound(LookupError):
    """Raised when a vault handle does not resolve to a credential."""


@dataclass(frozen=True, slots=True)
class SecretHandle:
    """A non-secret reference to one credential."""

    tenant_id: str
    store_id: str
    kind: str
    name: str = "default"

    def __post_init__(self) -> None:
        for field_name, value in (
            ("tenant_id", self.tenant_id),
            ("store_id", self.store_id),
            ("kind", self.kind),
            ("name", self.name),
        ):
            if not _HANDLE_COMPONENT.fullmatch(value):
                raise ValueError(
                    f"{field_name} must be an unambiguous identifier containing "
                    "only letters, digits, '.', '_', or '-'"
                )

    @property
    def storage_key(self) -> str:
        return f"{self.tenant_id}/{self.store_id}/{self.kind}/{self.name}"

    def __str__(self) -> str:
        return f"altas-secret://{self.storage_key}"


@runtime_checkable
class CredentialVault(Protocol):
    """Storage boundary implemented by OS keychains and test doubles."""

    def put(self, handle: SecretHandle, value: str) -> None:
        """Create or replace a credential."""

    def resolve(self, handle: SecretHandle) -> str:
        """Resolve a credential for immediate trusted use."""

    def delete(self, handle: SecretHandle) -> None:
        """Delete a credential if present."""
