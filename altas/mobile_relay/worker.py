"""Persistent outbound worker connection for the Atlas mobile text relay."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
import websockets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from altas.control_plane.security import device_session_challenge

from .gateway_adapter import TuiGatewayTextAdapter
from .store import LocalRelayStore


def _decode_private_key(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, TypeError, ValueError) as exc:
        raise ValueError("relay device private key is not valid base64url") from exc
    if len(decoded) != 32:
        raise ValueError("relay device private key must be a raw Ed25519 key")
    return decoded


def _read_private_file(path: Path, *, label: str) -> str:
    if path.is_symlink():
        raise ValueError(f"{label} path must not be a symbolic link")
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{label} path must be a regular file")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ValueError(f"{label} file must not be group/world accessible")
    return path.read_text(encoding="utf-8").strip()


@dataclass(slots=True)
class MobileRelayWorkerSettings:
    control_plane_url: str
    device_id: str
    device_private_key_path: Path = field(repr=False)
    store_path: Path
    gateway_url: str = "ws://127.0.0.1:8642/api/ws"
    gateway_authorization_file: Path | None = field(default=None, repr=False)
    gateway_workspace: str | None = None
    reconnect_max_seconds: float = 30.0
    request_timeout_seconds: float = 120.0

    @classmethod
    def from_env(cls) -> "MobileRelayWorkerSettings":
        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise ValueError(f"{name} is required")
            return value

        profile_home = Path(
            os.getenv("ATLAS_PROFILE_HOME") or os.getenv("HERMES_HOME") or "~/.atlas"
        ).expanduser()
        auth_file = os.getenv("ATLAS_LOCAL_GATEWAY_AUTHORIZATION_FILE", "").strip()
        return cls(
            control_plane_url=os.getenv(
                "ATLAS_CONTROL_PLANE_URL", "http://127.0.0.1:8787"
            ).rstrip("/"),
            device_id=required("ATLAS_DEVICE_ID"),
            device_private_key_path=Path(
                required("ATLAS_DEVICE_PRIVATE_KEY_FILE")
            ).expanduser(),
            store_path=Path(
                os.getenv(
                    "ATLAS_MOBILE_RELAY_STORE",
                    str(profile_home / "mobile-relay.sqlite3"),
                )
            ).expanduser(),
            gateway_url=os.getenv(
                "ATLAS_LOCAL_GATEWAY_URL", "ws://127.0.0.1:8642/api/ws"
            ),
            gateway_authorization_file=(
                Path(auth_file).expanduser() if auth_file else None
            ),
            gateway_workspace=os.getenv("ATLAS_MOBILE_RELAY_WORKSPACE") or None,
            reconnect_max_seconds=float(
                os.getenv("ATLAS_MOBILE_RELAY_RECONNECT_MAX_SECONDS", "30")
            ),
            request_timeout_seconds=float(
                os.getenv("ATLAS_MOBILE_RELAY_REQUEST_TIMEOUT_SECONDS", "120")
            ),
        )

    def websocket_url(self) -> str:
        parsed = urlparse(self.control_plane_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        return urlunparse((
            scheme,
            parsed.netloc,
            "/api/v1/mobile/relay/worker/connect",
            "",
            "",
            "",
        ))


class DeviceSessionProvider:
    """Mint short-lived sessions by proving the enrolled worker private key."""

    def __init__(
        self,
        *,
        control_plane_url: str,
        device_id: str,
        private_key: Ed25519PrivateKey,
        timeout_seconds: float,
    ) -> None:
        self.control_plane_url = control_plane_url
        self.device_id = device_id
        self.private_key = private_key
        self.timeout_seconds = timeout_seconds

    async def issue(self) -> str:
        timestamp = int(time.time())
        nonce = secrets.token_urlsafe(24)
        signature = (
            base64
            .urlsafe_b64encode(
                self.private_key.sign(
                    device_session_challenge(
                        device_id=self.device_id,
                        timestamp=timestamp,
                        nonce=nonce,
                    )
                )
            )
            .rstrip(b"=")
            .decode("ascii")
        )
        async with httpx.AsyncClient(
            base_url=self.control_plane_url,
            timeout=self.timeout_seconds,
        ) as client:
            response = await client.post(
                "/api/v1/device/sessions",
                json={
                    "device_id": self.device_id,
                    "timestamp": timestamp,
                    "nonce": nonce,
                    "signature": signature,
                },
            )
        response.raise_for_status()
        payload = response.json()
        token = str(payload.get("access_token") or "")
        if not token.startswith("atlas-device-session-v1."):
            raise ValueError("control plane returned an invalid device session")
        return token


class MobileRelayWorker:
    """Run one sequential, crash-aware command executor behind an outbound WS."""

    def __init__(
        self,
        *,
        settings: MobileRelayWorkerSettings,
        store: LocalRelayStore,
        adapter: TuiGatewayTextAdapter,
        session_provider: DeviceSessionProvider,
    ) -> None:
        self.settings = settings
        self.store = store
        self.adapter = adapter
        self.session_provider = session_provider
        self._socket: Any = None
        self._send_lock = asyncio.Lock()
        self._command_wake = asyncio.Event()
        self._stopping = False

    async def run_forever(self) -> None:
        for command in self.store.recover_uncertain():
            self._persist_recovery_error(command)
        processor = asyncio.create_task(self._process_commands())
        backoff = 1.0
        try:
            while not self._stopping:
                try:
                    token = await self.session_provider.issue()
                    async with websockets.connect(
                        self.settings.websocket_url(),
                        additional_headers={"Authorization": f"Bearer {token}"},
                        max_size=1_048_576,
                        open_timeout=self.settings.request_timeout_seconds,
                        ping_interval=20,
                        ping_timeout=20,
                    ) as socket:
                        self._socket = socket
                        await self._resend_outbox()
                        self._command_wake.set()
                        backoff = 1.0
                        await self._receive(socket)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    print(
                        json.dumps(
                            {
                                "component": "mobile_relay_worker",
                                "event": "connection_retry",
                                "reason": type(exc).__name__,
                                "retry_seconds": backoff,
                            },
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                finally:
                    self._socket = None
                if not self._stopping:
                    await asyncio.sleep(backoff)
                    backoff = min(self.settings.reconnect_max_seconds, backoff * 2)
        finally:
            self._stopping = True
            self._command_wake.set()
            processor.cancel()
            await asyncio.gather(processor, return_exceptions=True)
            await self.adapter.close()

    async def stop(self) -> None:
        self._stopping = True
        socket = self._socket
        if socket is not None:
            await socket.close()

    async def _receive(self, socket: Any) -> None:
        async for raw in socket:
            try:
                frame = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(frame, dict):
                continue
            frame_type = frame.get("type")
            if frame_type == "hello":
                continue
            if frame_type == "command":
                command = frame.get("command")
                if not isinstance(command, dict):
                    continue
                self.store.persist_command(command)
                # The store transaction above is the durable-receipt boundary.
                await self._send({
                    "type": "command.ack",
                    "command_id": command.get("id"),
                })
                self._command_wake.set()
                continue
            if frame_type == "event.ack":
                source_event_id = str(frame.get("source_event_id") or "")
                if source_event_id:
                    self.store.acknowledge_event(source_event_id)
                continue
            if frame_type == "pong" or frame_type == "command.acknowledged":
                continue
            if frame_type == "protocol.error":
                raise RuntimeError("control plane rejected a relay frame")

    async def _send(self, frame: dict[str, Any]) -> None:
        socket = self._socket
        if socket is None:
            return
        async with self._send_lock:
            await socket.send(json.dumps(frame, separators=(",", ":")))

    async def _resend_outbox(self) -> None:
        for frame in self.store.pending_events():
            await self._send(frame)

    async def _process_commands(self) -> None:
        while not self._stopping:
            command = self.store.claim_next_command()
            if command is None:
                self._command_wake.clear()
                await self._command_wake.wait()
                continue
            command_id = str(command["id"])
            session_id = str(command["session_id"])
            if not command.get("gateway_session_id"):
                command["gateway_session_id"] = self.store.gateway_session_id(
                    session_id
                )
            event_counter = 0

            async def emit(event_type: str, payload: dict[str, Any]) -> None:
                nonlocal event_counter
                event_counter += 1
                frame = {
                    "type": "event",
                    "source_event_id": (
                        f"worker:{command_id}:{event_counter}:{event_type}"
                    ),
                    "session_id": session_id,
                    "command_id": command_id,
                    "event_type": event_type,
                    "payload": payload,
                }
                self.store.persist_event(frame)
                if event_type == "session.created":
                    gateway_session_id = str(payload.get("gateway_session_id") or "")
                    self.store.bind_session(
                        session_id=session_id,
                        gateway_session_id=gateway_session_id,
                    )
                await self._send(frame)

            try:
                await self.adapter.execute(command, emit=emit)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                await emit("error", {"code": type(exc).__name__})
                self.store.mark_command(command_id, state="failed")
            else:
                self.store.mark_command(command_id, state="completed")

    def _persist_recovery_error(self, command: dict[str, Any]) -> None:
        command_id = str(command["id"])
        frame = {
            "type": "event",
            "source_event_id": f"worker:{command_id}:recovery:uncertain",
            "session_id": str(command["session_id"]),
            "command_id": command_id,
            "event_type": "error",
            "payload": {
                "code": "local_gateway_outcome_unknown",
                "retry_safe": False,
            },
        }
        self.store.persist_event(frame)


def build_worker(settings: MobileRelayWorkerSettings) -> MobileRelayWorker:
    private_text = _read_private_file(
        settings.device_private_key_path,
        label="relay device private key",
    )
    private_bytes = _decode_private_key(private_text)
    private_key = Ed25519PrivateKey.from_private_bytes(private_bytes)
    storage_key = hmac.new(
        private_bytes,
        b"atlas-mobile-relay/local-store/v1",
        hashlib.sha256,
    ).digest()
    gateway_authorization = None
    if settings.gateway_authorization_file is not None:
        gateway_authorization = _read_private_file(
            settings.gateway_authorization_file,
            label="local gateway authorization",
        )
        if " " not in gateway_authorization:
            gateway_authorization = f"token {gateway_authorization}"
    store = LocalRelayStore(settings.store_path, storage_key=storage_key)
    adapter = TuiGatewayTextAdapter(
        gateway_url=settings.gateway_url,
        gateway_authorization=gateway_authorization,
        workspace=settings.gateway_workspace,
        request_timeout_seconds=settings.request_timeout_seconds,
    )
    session_provider = DeviceSessionProvider(
        control_plane_url=settings.control_plane_url,
        device_id=settings.device_id,
        private_key=private_key,
        timeout_seconds=settings.request_timeout_seconds,
    )
    return MobileRelayWorker(
        settings=settings,
        store=store,
        adapter=adapter,
        session_provider=session_provider,
    )


def main() -> None:
    settings = MobileRelayWorkerSettings.from_env()
    asyncio.run(build_worker(settings).run_forever())


if __name__ == "__main__":
    main()
