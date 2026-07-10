"""Cross-platform OS keychain adapter using the Python keyring backend."""

from __future__ import annotations

from altas.credentials.base import CredentialNotFound, SecretHandle


class SystemKeyringVault:
    """Store credentials in the platform credential service.

    On macOS this resolves to Keychain. Windows and Linux behavior depends on
    the installed keyring backend and must be validated before production use.
    """

    def __init__(self, service_name: str = "ai.altas.worker") -> None:
        self.service_name = service_name

    @staticmethod
    def _keyring():
        try:
            import keyring
        except ImportError as exc:  # pragma: no cover - depends on installation
            raise RuntimeError(
                "SystemKeyringVault requires the optional 'keyring' package"
            ) from exc
        return keyring

    def put(self, handle: SecretHandle, value: str) -> None:
        if not value:
            raise ValueError("credential value must not be empty")
        self._keyring().set_password(self.service_name, handle.storage_key, value)

    def resolve(self, handle: SecretHandle) -> str:
        value = self._keyring().get_password(self.service_name, handle.storage_key)
        if value is None:
            raise CredentialNotFound(str(handle))
        return value

    def delete(self, handle: SecretHandle) -> None:
        keyring = self._keyring()
        try:
            keyring.delete_password(self.service_name, handle.storage_key)
        except keyring.errors.PasswordDeleteError:
            return
