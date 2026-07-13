"""Device-secret authentication and signed worker leases."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any


class InvalidLease(ValueError):
    """Raised when a lease is malformed, forged, expired, or out of context."""


def hash_secret(secret: str) -> str:
    """Return the stable digest stored for bearer-secret authentication."""

    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str, *, error_code: str = "lease_encoding_invalid") -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError, TypeError) as exc:
        raise InvalidLease(error_code) from exc
    if _b64encode(decoded) != value:
        raise InvalidLease(error_code)
    return decoded


@dataclass(frozen=True, slots=True)
class LeaseClaims:
    device_id: str
    tenant_id: str
    store_id: str
    agent_id: str
    capabilities: tuple[str, ...]
    issued_at: int
    expires_at: int
    nonce: str


class LeaseSigner:
    """Small, dependency-free HMAC lease format.

    The format is ``atlas-v1.<base64url-json>.<base64url-hmac>``. The signed
    payload carries only identity and authorization claims; it never contains
    credentials or dealership data.
    """

    _PREFIX = "atlas-v1"

    def __init__(
        self,
        signing_key: bytes,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(signing_key) < 32:
            raise ValueError("signing key must contain at least 32 bytes")
        self._key = signing_key
        self._clock = clock

    def issue(
        self,
        *,
        device_id: str,
        tenant_id: str,
        store_id: str,
        agent_id: str,
        capabilities: Iterable[str],
        ttl_seconds: int,
        nonce: str,
    ) -> tuple[str, LeaseClaims]:
        issued_at = int(self._clock())
        unique_capabilities = tuple(sorted(set(capabilities)))
        payload: dict[str, Any] = {
            "agent_id": agent_id,
            "aud": "atlas-worker",
            "capabilities": unique_capabilities,
            "device_id": device_id,
            "exp": issued_at + ttl_seconds,
            "iat": issued_at,
            "iss": "atlas-control-plane",
            "nonce": nonce,
            "store_id": store_id,
            "tenant_id": tenant_id,
            "typ": "lease",
        }
        payload_encoded = _b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signing_input = f"{self._PREFIX}.{payload_encoded}".encode("ascii")
        signature = _b64encode(
            hmac.new(self._key, signing_input, hashlib.sha256).digest()
        )
        claims = self._claims_from_payload(payload)
        return f"{self._PREFIX}.{payload_encoded}.{signature}", claims

    def verify(self, token: str) -> LeaseClaims:
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != self._PREFIX:
            raise InvalidLease("lease_format_invalid")
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        expected = hmac.new(self._key, signing_input, hashlib.sha256).digest()
        supplied = _b64decode(parts[2], error_code="lease_signature_invalid")
        if not hmac.compare_digest(expected, supplied):
            raise InvalidLease("lease_signature_invalid")
        try:
            payload = json.loads(
                _b64decode(parts[1], error_code="lease_payload_invalid")
            )
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
            raise InvalidLease("lease_payload_invalid") from exc
        if not isinstance(payload, dict):
            raise InvalidLease("lease_payload_invalid")
        claims = self._claims_from_payload(payload)
        if claims.expires_at <= int(self._clock()):
            raise InvalidLease("lease_expired")
        if claims.issued_at > int(self._clock()) + 30:
            raise InvalidLease("lease_issued_in_future")
        return claims

    @staticmethod
    def _claims_from_payload(payload: dict[str, Any]) -> LeaseClaims:
        if (
            payload.get("typ") != "lease"
            or payload.get("iss") != "atlas-control-plane"
            or payload.get("aud") != "atlas-worker"
        ):
            raise InvalidLease("lease_claims_invalid")
        required_strings = (
            "device_id",
            "tenant_id",
            "store_id",
            "agent_id",
            "nonce",
        )
        if any(
            not isinstance(payload.get(key), str) or not payload[key]
            for key in required_strings
        ):
            raise InvalidLease("lease_claims_invalid")
        capabilities = payload.get("capabilities")
        if not isinstance(capabilities, list | tuple) or any(
            not isinstance(item, str) or not item for item in capabilities
        ):
            raise InvalidLease("lease_claims_invalid")
        try:
            issued_at = int(payload["iat"])
            expires_at = int(payload["exp"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidLease("lease_claims_invalid") from exc
        return LeaseClaims(
            device_id=payload["device_id"],
            tenant_id=payload["tenant_id"],
            store_id=payload["store_id"],
            agent_id=payload["agent_id"],
            capabilities=tuple(capabilities),
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=payload["nonce"],
        )
