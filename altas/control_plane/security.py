"""Device-bound authentication, compatibility secrets, and signed leases."""

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

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class InvalidLease(ValueError):
    """Raised when a lease is malformed, forged, expired, or out of context."""


class InvalidDeviceSession(ValueError):
    """Raised when a short-lived device session cannot be trusted."""


class InvalidDeviceProof(ValueError):
    """Raised when device-bound Ed25519 material or a proof is malformed."""


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


@dataclass(frozen=True, slots=True)
class DeviceSessionClaims:
    device_id: str
    credential_version: int
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


class DeviceSessionSigner:
    """Issue signed sessions only after a device proves its private key.

    The underlying device credential is never transmitted.  The signed session
    is deliberately short-lived and carries a credential version so key
    rotation or revocation invalidates it during the next live-state check.
    """

    _PREFIX = "atlas-device-session-v1"

    def __init__(
        self,
        master_key: bytes,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(master_key) < 32:
            raise ValueError("device session master key must contain at least 32 bytes")
        self._key = hmac.new(
            master_key,
            b"atlas-control-plane/device-session/v1",
            hashlib.sha256,
        ).digest()
        self._clock = clock

    def issue(
        self,
        *,
        device_id: str,
        credential_version: int,
        ttl_seconds: int,
        nonce: str,
    ) -> tuple[str, DeviceSessionClaims]:
        issued_at = int(self._clock())
        payload: dict[str, Any] = {
            "aud": "atlas-device",
            "credential_version": credential_version,
            "device_id": device_id,
            "exp": issued_at + ttl_seconds,
            "iat": issued_at,
            "iss": "atlas-control-plane",
            "nonce": nonce,
            "typ": "device_session",
        }
        payload_encoded = _b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        )
        signing_input = f"{self._PREFIX}.{payload_encoded}".encode("ascii")
        signature = _b64encode(
            hmac.new(self._key, signing_input, hashlib.sha256).digest()
        )
        claims = self._claims_from_payload(payload)
        return f"{self._PREFIX}.{payload_encoded}.{signature}", claims

    def verify(self, token: str) -> DeviceSessionClaims:
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != self._PREFIX:
            raise InvalidDeviceSession("device_session_invalid")
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        expected = hmac.new(self._key, signing_input, hashlib.sha256).digest()
        try:
            supplied = _b64decode(parts[2], error_code="device_session_invalid")
            payload = json.loads(
                _b64decode(parts[1], error_code="device_session_invalid")
            )
        except (
            InvalidLease,
            json.JSONDecodeError,
            UnicodeDecodeError,
            TypeError,
        ) as exc:
            raise InvalidDeviceSession("device_session_invalid") from exc
        if not hmac.compare_digest(expected, supplied) or not isinstance(payload, dict):
            raise InvalidDeviceSession("device_session_invalid")
        claims = self._claims_from_payload(payload)
        now = int(self._clock())
        if claims.expires_at <= now:
            raise InvalidDeviceSession("device_session_expired")
        if claims.issued_at > now + 30:
            raise InvalidDeviceSession("device_session_invalid")
        return claims

    @staticmethod
    def _claims_from_payload(payload: dict[str, Any]) -> DeviceSessionClaims:
        if (
            payload.get("typ") != "device_session"
            or payload.get("iss") != "atlas-control-plane"
            or payload.get("aud") != "atlas-device"
        ):
            raise InvalidDeviceSession("device_session_invalid")
        device_id = payload.get("device_id")
        nonce = payload.get("nonce")
        if (
            not isinstance(device_id, str)
            or not device_id
            or not isinstance(nonce, str)
            or not nonce
        ):
            raise InvalidDeviceSession("device_session_invalid")
        try:
            credential_version = int(payload["credential_version"])
            issued_at = int(payload["iat"])
            expires_at = int(payload["exp"])
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidDeviceSession("device_session_invalid") from exc
        if credential_version < 1 or expires_at <= issued_at:
            raise InvalidDeviceSession("device_session_invalid")
        return DeviceSessionClaims(
            device_id=device_id,
            credential_version=credential_version,
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=nonce,
        )


def _device_b64decode(value: str, *, expected_length: int) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, TypeError, ValueError) as exc:
        raise InvalidDeviceProof("device_proof_invalid") from exc
    if len(decoded) != expected_length or _b64encode(decoded) != value:
        raise InvalidDeviceProof("device_proof_invalid")
    return decoded


def decode_device_public_key(public_key_b64: str) -> bytes:
    """Decode one canonical raw Ed25519 public key."""

    key_bytes = _device_b64decode(public_key_b64, expected_length=32)
    try:
        Ed25519PublicKey.from_public_bytes(key_bytes)
    except ValueError as exc:
        raise InvalidDeviceProof("device_proof_invalid") from exc
    return key_bytes


def device_public_key_thumbprint(public_key_b64: str) -> str:
    """Stable, non-secret identifier for an Ed25519 public key."""

    return hashlib.sha256(decode_device_public_key(public_key_b64)).hexdigest()


def device_session_challenge(*, device_id: str, timestamp: int, nonce: str) -> bytes:
    """Canonical proof signed before minting a device session."""

    return (f"atlas-device-session-proof-v1\n{device_id}\n{timestamp}\n{nonce}").encode(
        "utf-8"
    )


def device_key_rotation_challenge(
    *,
    device_id: str,
    new_public_key_b64: str,
    timestamp: int,
    nonce: str,
) -> bytes:
    """Canonical proof of possession for replacement device key material."""

    return (
        "atlas-device-key-rotation-v1\n"
        f"{device_id}\n{new_public_key_b64}\n{timestamp}\n{nonce}"
    ).encode("utf-8")


def verify_device_signature(
    *,
    public_key_b64: str,
    signature_b64: str,
    message: bytes,
) -> None:
    """Verify a canonical Ed25519 proof without leaking parsing distinctions."""

    key_bytes = decode_device_public_key(public_key_b64)
    signature = _device_b64decode(signature_b64, expected_length=64)
    try:
        Ed25519PublicKey.from_public_bytes(key_bytes).verify(signature, message)
    except (InvalidSignature, ValueError) as exc:
        raise InvalidDeviceProof("device_proof_invalid") from exc
