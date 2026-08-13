"""Hard-allowlisted adapter from relay commands to a local Desktop gateway."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

import websockets


class LocalGatewayError(RuntimeError):
    """A narrow local gateway request failed or violated its contract."""


EventEmitter = Callable[[str, dict[str, Any]], Awaitable[None]]


class _JsonRpcGateway:
    def __init__(
        self,
        *,
        url: str,
        authorization: str | None,
        request_timeout_seconds: float,
    ) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"ws", "wss"} or parsed.hostname not in {
            "127.0.0.1",
            "::1",
            "localhost",
        }:
            raise ValueError("mobile relay gateway must be loopback-only")
        if parsed.fragment or parsed.query:
            raise ValueError("mobile relay gateway URL must not contain a query")
        self.url = url
        self.credential = authorization
        self.request_timeout_seconds = request_timeout_seconds
        self._socket: Any = None
        self._reader: asyncio.Task[None] | None = None
        self._next_id = 0
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._event_queues: dict[str, asyncio.Queue[dict[str, Any]]] = {}
        self._send_lock = asyncio.Lock()

    async def connect(self) -> None:
        if self._socket is not None:
            return
        url = self.url
        if self.credential:
            scheme, separator, value = self.credential.partition(" ")
            if not separator:
                scheme, value = "token", scheme
            if scheme.lower() == "bearer":
                scheme = "token"
            if scheme.lower() not in {"token", "internal", "ticket"} or not value:
                raise ValueError("local gateway credential format invalid")
            parsed = urlparse(url)
            url = urlunparse(parsed._replace(query=urlencode({scheme.lower(): value})))
        self._socket = await websockets.connect(
            url,
            max_size=1_048_576,
            open_timeout=self.request_timeout_seconds,
        )
        self._reader = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        socket = self._socket
        self._socket = None
        if socket is not None:
            await socket.close()
        if self._reader is not None:
            self._reader.cancel()
            await asyncio.gather(self._reader, return_exceptions=True)
            self._reader = None

    def event_queue(self, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        return self._event_queues.setdefault(session_id, asyncio.Queue(maxsize=1024))

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method not in {"session.create", "prompt.submit", "session.interrupt"}:
            raise LocalGatewayError("gateway method is not relay-allowlisted")
        await self.connect()
        self._next_id += 1
        request_id = f"mobile-relay-{self._next_id}"
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        frame = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        try:
            async with self._send_lock:
                await self._socket.send(json.dumps(frame, separators=(",", ":")))
            result = await asyncio.wait_for(
                future,
                timeout=self.request_timeout_seconds,
            )
        except TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise LocalGatewayError(f"gateway request timed out: {method}") from exc
        if not isinstance(result, dict):
            raise LocalGatewayError("gateway response must be an object")
        return result

    async def _read_loop(self) -> None:
        try:
            async for raw in self._socket:
                try:
                    frame = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue
                if not isinstance(frame, dict):
                    continue
                request_id = frame.get("id")
                if request_id is not None:
                    future = self._pending.pop(str(request_id), None)
                    if future is None or future.done():
                        continue
                    error = frame.get("error")
                    if isinstance(error, dict):
                        future.set_exception(
                            LocalGatewayError(
                                str(error.get("message") or "gateway request failed")
                            )
                        )
                    else:
                        future.set_result(frame.get("result"))
                    continue
                if frame.get("method") != "event" or not isinstance(
                    frame.get("params"), dict
                ):
                    continue
                event = frame["params"]
                session_id = str(event.get("session_id") or "")
                if not session_id:
                    continue
                queue = self.event_queue(session_id)
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    # Losing an event is never silently recoverable. Replace the
                    # oldest entry with an explicit terminal error.
                    queue.get_nowait()
                    queue.put_nowait({
                        "type": "error",
                        "session_id": session_id,
                        "payload": {"code": "local_gateway_event_overflow"},
                    })
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            error = LocalGatewayError(
                f"gateway connection closed: {type(exc).__name__}"
            )
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(error)
            self._pending.clear()


class TuiGatewayTextAdapter:
    """Translate only the text relay contract into three local RPC methods."""

    _EVENT_TYPES = frozenset({
        "message.start",
        "message.delta",
        "message.complete",
        "status.update",
        "tool.start",
        "tool.progress",
        "tool.complete",
        "error",
    })

    def __init__(
        self,
        *,
        gateway_url: str = "ws://127.0.0.1:8642/api/ws",
        gateway_authorization: str | None = None,
        workspace: str | None = None,
        request_timeout_seconds: float = 120.0,
    ) -> None:
        self.workspace = workspace
        self._gateway = _JsonRpcGateway(
            url=gateway_url,
            authorization=gateway_authorization,
            request_timeout_seconds=request_timeout_seconds,
        )

    async def close(self) -> None:
        await self._gateway.close()

    async def execute(
        self,
        command: dict[str, Any],
        *,
        emit: EventEmitter,
    ) -> str | None:
        command_type = str(command["command_type"])
        relay_session_id = str(command["session_id"])
        gateway_session_id = str(command.get("gateway_session_id") or "")
        payload = command["payload"]
        if command_type == "session.create":
            params: dict[str, Any] = {
                "close_on_disconnect": False,
                "cols": 96,
                "source": "mobile_relay",
                "title": "Atlas Mobile",
            }
            if self.workspace:
                # This path is worker-owned configuration. It can never be
                # supplied by the phone or Control Plane command payload.
                params["cwd"] = self.workspace
            result = await self._gateway.request("session.create", params)
            gateway_session_id = str(result.get("session_id") or "")
            if not gateway_session_id:
                raise LocalGatewayError("gateway session id missing")
            await emit(
                "session.created",
                {
                    "gateway_session_id": gateway_session_id,
                    "stored_session_id": result.get("stored_session_id"),
                },
            )
            return gateway_session_id
        if not gateway_session_id:
            raise LocalGatewayError("relay session has no local gateway binding")
        if command_type == "session.interrupt":
            await self._gateway.request(
                "session.interrupt",
                {"session_id": gateway_session_id},
            )
            await emit("session.interrupted", {"reason": payload.get("reason")})
            return None
        if command_type != "prompt.submit":
            raise LocalGatewayError("relay command type is not supported")
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise LocalGatewayError("relay prompt text is required")
        queue = self._gateway.event_queue(gateway_session_id)
        await self._gateway.request(
            "prompt.submit",
            {"session_id": gateway_session_id, "text": text},
        )
        while True:
            event = await queue.get()
            event_type = str(event.get("type") or "")
            if event_type not in self._EVENT_TYPES:
                continue
            raw_payload = event.get("payload")
            event_payload = raw_payload if isinstance(raw_payload, dict) else {}
            await emit(event_type, event_payload)
            if event_type in {"message.complete", "error"}:
                return None
