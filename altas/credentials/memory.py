"""In-memory credential vault for tests and the fixture-only demo."""

from __future__ import annotations

from altas.credentials.base import CredentialNotFound, SecretHandle


class MemoryCredentialVault:
    """A non-persistent vault. Never use this for production credentials."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def put(self, handle: SecretHandle, value: str) -> None:
        if not value:
            raise ValueError("credential value must not be empty")
        self._values[handle.storage_key] = value

    def resolve(self, handle: SecretHandle) -> str:
        try:
            return self._values[handle.storage_key]
        except KeyError as exc:
            raise CredentialNotFound(str(handle)) from exc

    def delete(self, handle: SecretHandle) -> None:
        self._values.pop(handle.storage_key, None)
