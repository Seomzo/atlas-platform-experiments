"""Provider-neutral account identity verification.

The control plane consumes an already-authenticated browser identity through
``IdentityVerifier``.  Production can inject an OIDC verifier without changing
account, membership, or enrollment policy.  Local demo mode uses the
deterministic signer in this module so the complete contract remains testable
without selecting or contacting an identity vendor.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


class IdentityVerificationError(ValueError):
    """An external identity assertion could not be trusted."""


@dataclass(frozen=True, slots=True)
class ProviderIdentity:
    """Stable provider coordinates after cryptographic verification."""

    issuer: str
    subject: str


class IdentityVerifier(Protocol):
    """Boundary implemented by a future OIDC adapter or the local test signer."""

    def verify(self, token: str) -> ProviderIdentity: ...


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, TypeError, ValueError) as exc:
        raise IdentityVerificationError("identity_token_invalid") from exc
    if _b64encode(decoded) != value:
        raise IdentityVerificationError("identity_token_invalid")
    return decoded


class DeterministicIdentityProvider:
    """Short-lived, signed identity assertions for isolated development only."""

    issuer = "https://identity.dev.atlas.invalid"
    _PREFIX = "atlas-dev-identity-v1"

    def __init__(
        self,
        master_key: bytes,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(master_key) < 32:
            raise ValueError("identity master key must contain at least 32 bytes")
        self._key = hmac.new(
            master_key,
            b"atlas-control-plane/dev-identity/v1",
            hashlib.sha256,
        ).digest()
        self._clock = clock

    def issue(self, *, subject: str, ttl_seconds: int) -> tuple[str, int]:
        if not subject or len(subject) > 255:
            raise ValueError("identity subject must contain 1 to 255 characters")
        if ttl_seconds < 15:
            raise ValueError("identity token ttl must be at least 15 seconds")
        issued_at = int(self._clock())
        expires_at = issued_at + ttl_seconds
        payload: dict[str, Any] = {
            "aud": "atlas-account",
            "exp": expires_at,
            "iat": issued_at,
            "iss": self.issuer,
            "nonce": secrets.token_hex(16),
            "sub": subject,
            "typ": "identity",
        }
        encoded = _b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )
        signing_input = f"{self._PREFIX}.{encoded}".encode("ascii")
        signature = _b64encode(
            hmac.new(self._key, signing_input, hashlib.sha256).digest()
        )
        return f"{self._PREFIX}.{encoded}.{signature}", expires_at

    def verify(self, token: str) -> ProviderIdentity:
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != self._PREFIX:
            raise IdentityVerificationError("identity_token_invalid")
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        expected = hmac.new(self._key, signing_input, hashlib.sha256).digest()
        supplied = _b64decode(parts[2])
        if not hmac.compare_digest(expected, supplied):
            raise IdentityVerificationError("identity_token_invalid")
        try:
            payload = json.loads(_b64decode(parts[1]))
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise IdentityVerificationError("identity_token_invalid") from exc
        if not isinstance(payload, dict):
            raise IdentityVerificationError("identity_token_invalid")
        if (
            payload.get("typ") != "identity"
            or payload.get("iss") != self.issuer
            or payload.get("aud") != "atlas-account"
        ):
            raise IdentityVerificationError("identity_token_invalid")
        subject = payload.get("sub")
        nonce = payload.get("nonce")
        if (
            not isinstance(subject, str)
            or not subject
            or len(subject) > 255
            or not isinstance(nonce, str)
            or not nonce
        ):
            raise IdentityVerificationError("identity_token_invalid")
        try:
            issued_at = int(payload["iat"])
            expires_at = int(payload["exp"])
        except (KeyError, TypeError, ValueError) as exc:
            raise IdentityVerificationError("identity_token_invalid") from exc
        now = int(self._clock())
        if expires_at <= now:
            raise IdentityVerificationError("identity_token_expired")
        if issued_at > now + 30 or expires_at <= issued_at:
            raise IdentityVerificationError("identity_token_invalid")
        return ProviderIdentity(issuer=self.issuer, subject=subject)


class UnconfiguredIdentityProvider:
    """Fail-closed default outside deterministic demo mode."""

    def verify(self, token: str) -> ProviderIdentity:
        del token
        raise IdentityVerificationError("identity_provider_unconfigured")
