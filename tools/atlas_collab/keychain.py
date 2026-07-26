"""OS credential-vault boundary for role-specific Buzz private keys."""

from __future__ import annotations

from dataclasses import dataclass
import os
import platform
import secrets
import subprocess
from typing import Protocol

SERVICE = "io.atlas.collab.buzz"


class CredentialVault(Protocol):
    def get(self, account: str) -> str | None: ...

    def set(self, account: str, value: str) -> None: ...

    def delete(self, account: str) -> None: ...


class KeyringVault:
    """OS vault without placing credential values in process arguments."""

    def __init__(self, service: str = SERVICE):
        self.service = service
        self._macos = platform.system() == "Darwin"
        if not self._macos:
            try:
                import keyring
            except ImportError as exc:
                raise RuntimeError(
                    "keyring is required for this platform credential vault"
                ) from exc
            self._keyring = keyring

    def get(self, account: str) -> str | None:
        if not self._macos:
            return self._keyring.get_password(self.service, account)
        result = subprocess.run(
            [
                "/usr/bin/security",
                "find-generic-password",
                "-a",
                account,
                "-s",
                self.service,
                "-w",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 44:
            return None
        if result.returncode:
            raise RuntimeError(
                "macOS login Keychain is unavailable; unlock it personally "
                "before reading Atlas role credentials"
            )
        return result.stdout.rstrip("\n")

    def set(self, account: str, value: str) -> None:
        if not self._macos:
            self._keyring.set_password(self.service, account, value)
            return
        # `/usr/bin/security -w` with no argument prompts twice. Expect supplies
        # the value from a child-only environment variable; it is never an argv
        # value, transcript, or persisted temporary file.
        script = """
log_user 0
set timeout 15
spawn /usr/bin/security add-generic-password -U -a $env(ATLAS_COLLAB_VAULT_ACCOUNT) -s $env(ATLAS_COLLAB_VAULT_SERVICE) -w
expect -exact "password data for new item:"
send -- "$env(ATLAS_COLLAB_VAULT_VALUE)\\r"
expect -exact "retype password for new item:"
send -- "$env(ATLAS_COLLAB_VAULT_VALUE)\\r"
expect eof
catch wait result
exit [lindex $result 3]
"""
        env = os.environ.copy()
        env["ATLAS_COLLAB_VAULT_ACCOUNT"] = account
        env["ATLAS_COLLAB_VAULT_SERVICE"] = self.service
        env["ATLAS_COLLAB_VAULT_VALUE"] = value
        result = subprocess.run(
            ["/usr/bin/expect", "-c", script],
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(
                "macOS login Keychain rejected the credential write; unlock "
                "the login Keychain personally and retry"
            )

    def delete(self, account: str) -> None:
        if not self._macos:
            try:
                self._keyring.delete_password(self.service, account)
            except self._keyring.errors.PasswordDeleteError:
                pass
            return
        result = subprocess.run(
            [
                "/usr/bin/security",
                "delete-generic-password",
                "-a",
                account,
                "-s",
                self.service,
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode not in {0, 44}:
            raise RuntimeError("macOS login Keychain rejected the credential deletion")


@dataclass
class MemoryVault:
    """Test-only vault; never selected by production CLI configuration."""

    values: dict[str, str]

    def get(self, account: str) -> str | None:
        return self.values.get(account)

    def set(self, account: str, value: str) -> None:
        self.values[account] = value

    def delete(self, account: str) -> None:
        self.values.pop(account, None)


def generate_nostr_keypair() -> tuple[str, str]:
    """Return `(private_hex, x_only_public_hex)` for secp256k1."""
    from cryptography.hazmat.primitives.asymmetric import ec

    while True:
        private_hex = secrets.token_hex(32)
        try:
            private = ec.derive_private_key(int(private_hex, 16), ec.SECP256K1())
        except ValueError:
            continue
        public_hex = f"{private.public_key().public_numbers().x:064x}"
        return private_hex, public_hex
