"""Encryption and opaque cursor primitives for the mobile text relay."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag


class InvalidRelayPayload(ValueError):
    """Raised when an encrypted relay payload fails authentication."""


class InvalidRelayCursor(ValueError):
    """Raised when a relay cursor is malformed or bound to another session."""


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, TypeError, ValueError) as exc:
        raise ValueError("base64_invalid") from exc
    if _encode(decoded) != value:
        raise ValueError("base64_invalid")
    return decoded


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


@dataclass(frozen=True, slots=True)
class EncryptedRelayPayload:
    nonce: str
    ciphertext: str
    digest: str


class RelayPayloadCipher:
    """Encrypt relay content before it crosses the SQLite boundary."""

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) < 32:
            raise ValueError("relay master key must contain at least 32 bytes")
        self._encryption_key = hmac.new(
            master_key,
            b"atlas-control-plane/relay-payload-encryption/v1",
            hashlib.sha256,
        ).digest()
        self._digest_key = hmac.new(
            master_key,
            b"atlas-control-plane/relay-payload-digest/v1",
            hashlib.sha256,
        ).digest()

    def encrypt(
        self, payload: dict[str, Any], *, context: str
    ) -> EncryptedRelayPayload:
        plaintext = _canonical_json(payload)
        nonce = os.urandom(12)
        associated_data = context.encode("utf-8")
        ciphertext = AESGCM(self._encryption_key).encrypt(
            nonce,
            plaintext,
            associated_data,
        )
        digest = hmac.new(self._digest_key, plaintext, hashlib.sha256).digest()
        return EncryptedRelayPayload(
            nonce=_encode(nonce),
            ciphertext=_encode(ciphertext),
            digest=_encode(digest),
        )

    def decrypt(
        self,
        *,
        nonce: str,
        ciphertext: str,
        digest: str,
        context: str,
    ) -> dict[str, Any]:
        try:
            plaintext = AESGCM(self._encryption_key).decrypt(
                _decode(nonce),
                _decode(ciphertext),
                context.encode("utf-8"),
            )
            expected = hmac.new(self._digest_key, plaintext, hashlib.sha256).digest()
            if not hmac.compare_digest(expected, _decode(digest)):
                raise InvalidRelayPayload("relay_payload_digest_invalid")
            payload = json.loads(plaintext)
        except (
            InvalidRelayPayload,
            InvalidTag,
            UnicodeDecodeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise InvalidRelayPayload("relay_payload_invalid") from exc
        if not isinstance(payload, dict):
            raise InvalidRelayPayload("relay_payload_invalid")
        return payload


class RelayCursorSigner:
    """Sign a session-bound monotonic event position for replay."""

    _PREFIX = "atlas-relay-cursor-v1"

    def __init__(self, master_key: bytes) -> None:
        if len(master_key) < 32:
            raise ValueError("relay cursor master key must contain at least 32 bytes")
        self._key = hmac.new(
            master_key,
            b"atlas-control-plane/relay-cursor/v1",
            hashlib.sha256,
        ).digest()

    def issue(self, *, session_id: str, sequence: int) -> str:
        if not session_id or sequence < 0:
            raise ValueError("relay cursor claims invalid")
        encoded = _encode(
            _canonical_json({"sequence": sequence, "session_id": session_id})
        )
        signing_input = f"{self._PREFIX}.{encoded}".encode("ascii")
        signature = _encode(hmac.new(self._key, signing_input, hashlib.sha256).digest())
        return f"{self._PREFIX}.{encoded}.{signature}"

    def verify(self, token: str, *, session_id: str) -> int:
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != self._PREFIX:
            raise InvalidRelayCursor("relay_cursor_invalid")
        signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
        expected = hmac.new(self._key, signing_input, hashlib.sha256).digest()
        try:
            supplied = _decode(parts[2])
            payload = json.loads(_decode(parts[1]))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise InvalidRelayCursor("relay_cursor_invalid") from exc
        if not hmac.compare_digest(expected, supplied) or not isinstance(payload, dict):
            raise InvalidRelayCursor("relay_cursor_invalid")
        if payload.get("session_id") != session_id:
            raise InvalidRelayCursor("relay_cursor_scope_mismatch")
        sequence = payload.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise InvalidRelayCursor("relay_cursor_invalid")
        return sequence
