"""Credential-vault interfaces for Atlas workers."""

from altas.credentials.base import CredentialNotFound, CredentialVault, SecretHandle
from altas.credentials.keyring import SystemKeyringVault
from altas.credentials.memory import MemoryCredentialVault

__all__ = [
    "CredentialNotFound",
    "CredentialVault",
    "MemoryCredentialVault",
    "SecretHandle",
    "SystemKeyringVault",
]
