"""Narrow authenticated phone/worker API for durable text relay."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Annotated

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, ConfigDict, Field

from .config import ControlPlaneSettings
from .relay_repository import (
    RelayAccessDenied,
    RelayBackpressure,
    RelayConflict,
    RelayNotFound,
    RelayRepository,
)
from .relay_security import InvalidRelayCursor, RelayCursorSigner
from .repository import ControlPlaneRepository
from .security import DeviceSessionSigner, InvalidDeviceSession


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PairingRequest(_StrictModel):
    worker_device_id: str = Field(min_length=1, max_length=128)


class SessionRequest(_StrictModel):
    pairing_id: str = Field(min_length=1, max_length=128)


class MessageRequest(_StrictModel):
    text: str = Field(min_length=1, max_length=16_000)


class InterruptRequest(_StrictModel):
    reason: str = Field(default="user_requested", min_length=1, max_length=80)


@dataclass(slots=True)
class _WorkerConnection:
    connection_id: str
    worker_device_id: str
    websocket: WebSocket
    sent_command_ids: set[str] = field(default_factory=set)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class RelayHub:
    """Track live outbound worker sockets; durable state remains in SQLite."""

    def __init__(
        self,
        repository: RelayRepository,
        *,
        max_inflight_commands: int,
    ) -> None:
        self.repository = repository
        self.max_inflight_commands = max_inflight_commands
        self._connections: dict[str, _WorkerConnection] = {}
        self._lock = asyncio.Lock()

    async def register(self, *, worker_device_id: str, websocket: WebSocket) -> str:
        connection_id = f"relay_connection_{uuid.uuid4().hex}"
        connection = _WorkerConnection(
            connection_id=connection_id,
            worker_device_id=worker_device_id,
            websocket=websocket,
        )
        await websocket.accept()
        async with self._lock:
            previous = self._connections.get(worker_device_id)
            self._connections[worker_device_id] = connection
        if previous is not None:
            try:
                await previous.websocket.close(code=4001, reason="superseded")
            except RuntimeError:
                pass
        self.repository.open_worker_connection(
            worker_device_id=worker_device_id,
            connection_id=connection_id,
        )
        await self._send(
            connection,
            {
                "type": "hello",
                "connection_id": connection_id,
                "protocol": "atlas-mobile-relay-v1",
                "durable_ack_required": True,
            },
        )
        await self.deliver_pending(worker_device_id)
        return connection_id

    async def unregister(
        self,
        *,
        worker_device_id: str,
        connection_id: str,
        reason: str,
    ) -> None:
        async with self._lock:
            current = self._connections.get(worker_device_id)
            if current is not None and current.connection_id == connection_id:
                del self._connections[worker_device_id]
        self.repository.close_worker_connection(connection_id, reason=reason)

    def availability(self, worker_device_id: str) -> dict[str, Any]:
        connection = self._connections.get(worker_device_id)
        return {
            "status": "online" if connection is not None else "offline",
        }

    async def disconnect_worker(self, worker_device_id: str, *, reason: str) -> None:
        connection = self._connections.get(worker_device_id)
        if connection is None:
            return
        try:
            await connection.websocket.close(code=4003, reason=reason[:80])
        except RuntimeError:
            pass

    async def send_control(
        self, *, worker_device_id: str, frame: dict[str, Any]
    ) -> None:
        connection = self._connections.get(worker_device_id)
        if connection is not None:
            await self._send(connection, frame)

    async def _send(self, connection: _WorkerConnection, frame: dict[str, Any]) -> None:
        async with connection.send_lock:
            await connection.websocket.send_json(frame)

    async def deliver_pending(self, worker_device_id: str) -> int:
        connection = self._connections.get(worker_device_id)
        if connection is None:
            return 0
        slots = self.max_inflight_commands - len(connection.sent_command_ids)
        if slots <= 0:
            return 0
        commands = self.repository.list_deliverable_commands(
            worker_device_id=worker_device_id,
            limit=slots,
        )
        delivered = 0
        for command in commands:
            command_id = str(command["id"])
            if command_id in connection.sent_command_ids:
                continue
            connection.sent_command_ids.add(command_id)
            await self._send(
                connection,
                {
                    "type": "command",
                    "command": command,
                },
            )
            delivered += 1
        return delivered

    async def command_acknowledged(
        self, *, worker_device_id: str, command_id: str
    ) -> None:
        connection = self._connections.get(worker_device_id)
        if connection is not None:
            connection.sent_command_ids.discard(command_id)
            await self._send(
                connection,
                {"type": "command.acknowledged", "command_id": command_id},
            )
        await self.deliver_pending(worker_device_id)

    async def event_acknowledged(
        self,
        *,
        worker_device_id: str,
        source_event_id: str,
        event_id: str,
        sequence: int,
    ) -> None:
        connection = self._connections.get(worker_device_id)
        if connection is None:
            return
        await self._send(
            connection,
            {
                "type": "event.ack",
                "source_event_id": source_event_id,
                "event_id": event_id,
                "sequence": sequence,
            },
        )


PhoneContext = dict[str, dict[str, Any]]


def build_relay_router(
    *,
    settings: ControlPlaneSettings,
    repository: ControlPlaneRepository,
    relay_repository: RelayRepository,
    device_session_signer: DeviceSessionSigner,
    cursor_signer: RelayCursorSigner,
    require_account: Callable[..., dict[str, Any]],
) -> tuple[APIRouter, RelayHub]:
    """Build the relay routes with the app factory's concrete auth dependencies."""

    router = APIRouter(prefix="/api/v1/mobile/relay", tags=["mobile-relay"])
    hub = RelayHub(
        relay_repository,
        max_inflight_commands=settings.relay_max_inflight_commands,
    )

    def _device_from_session(token: str) -> dict[str, Any] | None:
        try:
            claims = device_session_signer.verify(token)
        except InvalidDeviceSession:
            return None
        device = repository.authenticate_device_session(
            device_id=claims.device_id,
            credential_version=claims.credential_version,
        )
        if device is not None:
            device["_credential_version"] = claims.credential_version
        return device

    def require_phone(
        device_session: Annotated[
            str | None,
            Header(alias="X-Atlas-Device-Session"),
        ] = None,
        user: dict[str, Any] = Depends(require_account),
    ) -> PhoneContext:
        device = _device_from_session(device_session or "")
        if (
            device is None
            or device.get("device_class") != "phone"
            or device.get("enrolled_by_user_id") != user.get("id")
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "phone_device_session_required"},
            )
        return {"user": user, "device": device}

    def _http_error(exc: ValueError) -> HTTPException:
        code = str(exc)
        if isinstance(exc, RelayBackpressure):
            return HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": code},
                headers={"Retry-After": "3"},
            )
        if isinstance(exc, RelayConflict):
            return HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": code},
            )
        if isinstance(exc, RelayNotFound):
            return HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": code},
            )
        return HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": code},
        )

    @router.post("/pairings", status_code=status.HTTP_201_CREATED)
    async def create_pairing(
        request: PairingRequest,
        context: PhoneContext = Depends(require_phone),
    ) -> dict[str, Any]:
        user = context["user"]
        phone = context["device"]
        try:
            pairing = relay_repository.create_pairing(
                user_id=str(user["id"]),
                phone_device_id=str(phone["id"]),
                worker_device_id=request.worker_device_id,
            )
        except (RelayAccessDenied, RelayConflict) as exc:
            raise _http_error(exc) from exc
        repository.record_audit(
            actor_type="account",
            action="relay.pairing.create",
            outcome="succeeded",
            tenant_id=pairing["tenant_id"],
            store_id=pairing["store_id"],
            agent_id=pairing["agent_id"],
            device_id=pairing["phone_device_id"],
            user_id=user["id"],
            resource_type="relay_pairing",
            resource_id=pairing["id"],
            details={"worker_device_id": pairing["worker_device_id"]},
        )
        return {
            "pairing": pairing,
            "worker": hub.availability(pairing["worker_device_id"]),
        }

    @router.post("/sessions", status_code=status.HTTP_201_CREATED)
    async def create_session(
        request: SessionRequest,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=160),
        ],
        context: PhoneContext = Depends(require_phone),
    ) -> dict[str, Any]:
        user = context["user"]
        phone = context["device"]
        try:
            session, command, created = relay_repository.create_session(
                pairing_id=request.pairing_id,
                user_id=str(user["id"]),
                phone_device_id=str(phone["id"]),
                idempotency_key=idempotency_key,
                command_ttl_seconds=settings.relay_command_ttl_seconds,
                max_pending_commands=settings.relay_max_pending_commands,
            )
        except (RelayBackpressure, RelayConflict, RelayNotFound) as exc:
            raise _http_error(exc) from exc
        delivered = await hub.deliver_pending(str(session["worker_device_id"]))
        if created:
            repository.record_audit(
                actor_type="phone",
                action="relay.session.create",
                outcome="accepted",
                tenant_id=phone["tenant_id"],
                store_id=session["store_id"],
                agent_id=session["agent_id"],
                device_id=phone["id"],
                user_id=user["id"],
                resource_type="relay_session",
                resource_id=session["id"],
                correlation_id=command["id"],
                details={"worker_online": delivered > 0},
            )
        return {
            "session": session,
            "command": command,
            "idempotent_replay": not created,
            "worker": hub.availability(str(session["worker_device_id"])),
        }

    @router.get("/sessions")
    def list_sessions(
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        context: PhoneContext = Depends(require_phone),
    ) -> dict[str, Any]:
        items = relay_repository.list_sessions(
            user_id=str(context["user"]["id"]),
            phone_device_id=str(context["device"]["id"]),
            limit=limit,
        )
        return {"items": items, "count": len(items)}

    @router.get("/sessions/{session_id}")
    def get_session(
        session_id: str,
        context: PhoneContext = Depends(require_phone),
    ) -> dict[str, Any]:
        try:
            session = relay_repository.get_session(
                session_id=session_id,
                user_id=str(context["user"]["id"]),
                phone_device_id=str(context["device"]["id"]),
            )
        except RelayNotFound as exc:
            raise _http_error(exc) from exc
        return {
            "session": session,
            "worker": hub.availability(str(session["worker_device_id"])),
        }

    @router.post(
        "/sessions/{session_id}/messages", status_code=status.HTTP_202_ACCEPTED
    )
    async def submit_message(
        session_id: str,
        request: MessageRequest,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=160),
        ],
        context: PhoneContext = Depends(require_phone),
    ) -> dict[str, Any]:
        try:
            command, created = relay_repository.enqueue_command(
                session_id=session_id,
                user_id=str(context["user"]["id"]),
                phone_device_id=str(context["device"]["id"]),
                command_type="prompt.submit",
                payload={"text": request.text},
                idempotency_key=idempotency_key,
                command_ttl_seconds=settings.relay_command_ttl_seconds,
                max_pending_commands=settings.relay_max_pending_commands,
            )
        except (RelayBackpressure, RelayConflict, RelayNotFound) as exc:
            raise _http_error(exc) from exc
        session = relay_repository.get_session(
            session_id=session_id,
            user_id=str(context["user"]["id"]),
            phone_device_id=str(context["device"]["id"]),
        )
        delivered = await hub.deliver_pending(str(session["worker_device_id"]))
        if created:
            repository.record_audit(
                actor_type="phone",
                action="relay.prompt.submit",
                outcome="accepted",
                tenant_id=context["device"]["tenant_id"],
                store_id=session["store_id"],
                agent_id=session["agent_id"],
                device_id=context["device"]["id"],
                user_id=context["user"]["id"],
                resource_type="relay_command",
                resource_id=command["id"],
                correlation_id=command["id"],
                details={"worker_online": delivered > 0, "content_in_audit": False},
            )
        return {
            "command": command,
            "idempotent_replay": not created,
            "delivery": "sent" if delivered else "queued",
        }

    @router.post(
        "/sessions/{session_id}/interrupts",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def interrupt_session(
        session_id: str,
        request: InterruptRequest,
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=160),
        ],
        context: PhoneContext = Depends(require_phone),
    ) -> dict[str, Any]:
        try:
            command, created = relay_repository.enqueue_command(
                session_id=session_id,
                user_id=str(context["user"]["id"]),
                phone_device_id=str(context["device"]["id"]),
                command_type="session.interrupt",
                payload={"reason": request.reason},
                idempotency_key=idempotency_key,
                command_ttl_seconds=settings.relay_command_ttl_seconds,
                max_pending_commands=settings.relay_max_pending_commands,
            )
            session = relay_repository.get_session(
                session_id=session_id,
                user_id=str(context["user"]["id"]),
                phone_device_id=str(context["device"]["id"]),
            )
        except (RelayBackpressure, RelayConflict, RelayNotFound) as exc:
            raise _http_error(exc) from exc
        delivered = await hub.deliver_pending(str(session["worker_device_id"]))
        return {
            "command": command,
            "idempotent_replay": not created,
            "delivery": "sent" if delivered else "queued",
        }

    @router.get("/commands/{command_id}")
    def get_command(
        command_id: str,
        context: PhoneContext = Depends(require_phone),
    ) -> dict[str, Any]:
        try:
            command = relay_repository.get_command_status(
                command_id=command_id,
                user_id=str(context["user"]["id"]),
                phone_device_id=str(context["device"]["id"]),
            )
        except RelayNotFound as exc:
            raise _http_error(exc) from exc
        return {"command": command}

    @router.get("/sessions/{session_id}/events")
    def list_events(
        session_id: str,
        cursor: Annotated[str | None, Query(max_length=1024)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        context: PhoneContext = Depends(require_phone),
    ) -> dict[str, Any]:
        try:
            after_sequence = (
                cursor_signer.verify(cursor, session_id=session_id) if cursor else 0
            )
        except InvalidRelayCursor as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": str(exc)},
            ) from exc
        try:
            events = relay_repository.list_events(
                session_id=session_id,
                user_id=str(context["user"]["id"]),
                phone_device_id=str(context["device"]["id"]),
                after_sequence=after_sequence,
                limit=limit + 1,
            )
        except RelayNotFound as exc:
            raise _http_error(exc) from exc
        has_more = len(events) > limit
        items = events[:limit]
        next_sequence = int(items[-1]["sequence"]) if items else after_sequence
        return {
            "items": items,
            "has_more": has_more,
            "next_cursor": cursor_signer.issue(
                session_id=session_id,
                sequence=next_sequence,
            ),
        }

    @router.websocket("/worker/connect")
    async def worker_connect(websocket: WebSocket) -> None:
        if websocket.query_params:
            await websocket.close(code=4400, reason="query_auth_forbidden")
            return
        if websocket.headers.get("origin"):
            await websocket.close(code=4403, reason="browser_origin_forbidden")
            return
        authorization = websocket.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        try:
            worker_claims = device_session_signer.verify(
                token if scheme.lower() == "bearer" else ""
            )
        except InvalidDeviceSession:
            worker_claims = None
        device = (
            repository.authenticate_device_session(
                device_id=worker_claims.device_id,
                credential_version=worker_claims.credential_version,
            )
            if worker_claims is not None
            else None
        )
        if device is None or device.get("device_class") != "worker":
            await websocket.close(code=4401, reason="worker_device_session_required")
            return
        agent = next(
            (
                item
                for item in (
                    repository.get_agent(agent_id)
                    for agent_id in _agent_ids(repository, device["id"])
                )
                if item is not None
                and item.get("status") == "active"
                and item.get("tenant_id") == device.get("tenant_id")
                and item.get("store_id") == device.get("store_id")
                and item.get("device_id") == device.get("id")
            ),
            None,
        )
        if agent is None:
            await websocket.close(code=4403, reason="worker_agent_binding_required")
            return
        worker_device_id = str(device["id"])
        connection_id = await hub.register(
            worker_device_id=worker_device_id,
            websocket=websocket,
        )
        repository.record_audit(
            actor_type="worker",
            action="relay.worker.connect",
            outcome="succeeded",
            tenant_id=device["tenant_id"],
            store_id=device["store_id"],
            agent_id=agent["id"],
            device_id=worker_device_id,
            resource_type="relay_connection",
            resource_id=connection_id,
        )
        close_reason = "client_disconnected"
        try:
            while True:
                assert worker_claims is not None
                session_lifetime = worker_claims.expires_at - time.time()
                if session_lifetime <= 0:
                    close_reason = "device_session_expired"
                    await websocket.close(
                        code=4003,
                        reason="device_session_expired",
                    )
                    break
                try:
                    frame = await asyncio.wait_for(
                        websocket.receive_json(),
                        timeout=session_lifetime,
                    )
                except TimeoutError:
                    close_reason = "device_session_expired"
                    await websocket.close(
                        code=4003,
                        reason="device_session_expired",
                    )
                    break
                if not isinstance(frame, dict):
                    await hub.send_control(
                        worker_device_id=worker_device_id,
                        frame={
                            "type": "protocol.error",
                            "code": "frame_object_required",
                        },
                    )
                    continue
                frame_type = frame.get("type")
                if frame_type == "ping":
                    relay_repository.touch_worker_connection(connection_id)
                    await hub.send_control(
                        worker_device_id=worker_device_id,
                        frame={"type": "pong"},
                    )
                    continue
                if frame_type == "command.ack":
                    command_id = str(frame.get("command_id") or "")
                    try:
                        relay_repository.acknowledge_command(
                            worker_device_id=worker_device_id,
                            command_id=command_id,
                        )
                    except RelayNotFound:
                        await hub.send_control(
                            worker_device_id=worker_device_id,
                            frame={
                                "type": "protocol.error",
                                "code": "command_not_found",
                            },
                        )
                        continue
                    relay_repository.touch_worker_connection(connection_id)
                    await hub.command_acknowledged(
                        worker_device_id=worker_device_id,
                        command_id=command_id,
                    )
                    continue
                if frame_type != "event":
                    await hub.send_control(
                        worker_device_id=worker_device_id,
                        frame={"type": "protocol.error", "code": "frame_type_invalid"},
                    )
                    continue
                try:
                    event = _validate_worker_event(
                        frame,
                        max_event_bytes=settings.relay_max_event_bytes,
                    )
                    stored, _ = relay_repository.append_worker_event(
                        worker_device_id=worker_device_id,
                        session_id=event["session_id"],
                        source_event_id=event["source_event_id"],
                        event_type=event["event_type"],
                        command_id=event["command_id"],
                        payload=event["payload"],
                    )
                except (RelayConflict, RelayNotFound, ValueError) as exc:
                    await hub.send_control(
                        worker_device_id=worker_device_id,
                        frame={"type": "protocol.error", "code": str(exc)},
                    )
                    continue
                relay_repository.touch_worker_connection(connection_id)
                await hub.event_acknowledged(
                    worker_device_id=worker_device_id,
                    source_event_id=stored["source_event_id"],
                    event_id=stored["id"],
                    sequence=int(stored["sequence"]),
                )
        except WebSocketDisconnect:
            pass
        except (RuntimeError, json.JSONDecodeError):
            close_reason = "protocol_failure"
        finally:
            await hub.unregister(
                worker_device_id=worker_device_id,
                connection_id=connection_id,
                reason=close_reason,
            )

    return router, hub


def _agent_ids(repository: ControlPlaneRepository, device_id: str) -> list[str]:
    """Resolve bound agents without exposing a list-all repository primitive."""

    with repository.database.connect() as connection:
        rows = connection.execute(
            "SELECT id FROM agents WHERE device_id = ? ORDER BY id",
            (device_id,),
        ).fetchall()
    return [str(row["id"]) for row in rows]


_WORKER_EVENT_TYPES = frozenset({
    "session.created",
    "session.failed",
    "session.closed",
    "session.interrupted",
    "message.start",
    "message.delta",
    "message.complete",
    "status.update",
    "tool.start",
    "tool.progress",
    "tool.complete",
    "error",
})


def _validate_worker_event(
    frame: dict[str, Any], *, max_event_bytes: int
) -> dict[str, Any]:
    try:
        encoded_size = len(
            json.dumps(frame, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("event_json_invalid") from exc
    if encoded_size > max_event_bytes:
        raise ValueError("event_too_large")
    session_id = frame.get("session_id")
    source_event_id = frame.get("source_event_id")
    event_type = frame.get("event_type")
    command_id = frame.get("command_id")
    payload = frame.get("payload")
    if not isinstance(session_id, str) or not 1 <= len(session_id) <= 128:
        raise ValueError("event_session_id_invalid")
    if not isinstance(source_event_id, str) or not 1 <= len(source_event_id) <= 160:
        raise ValueError("event_source_id_invalid")
    if event_type not in _WORKER_EVENT_TYPES:
        raise ValueError("event_type_not_allowed")
    if not isinstance(command_id, str) or not 1 <= len(command_id) <= 128:
        raise ValueError("event_command_id_invalid")
    if not isinstance(payload, dict):
        raise ValueError("event_payload_object_required")
    return {
        "session_id": session_id,
        "source_event_id": source_event_id,
        "event_type": event_type,
        "command_id": command_id,
        "payload": payload,
    }
