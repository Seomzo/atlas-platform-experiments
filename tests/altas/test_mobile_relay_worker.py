from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from altas.mobile_relay.gateway_adapter import TuiGatewayTextAdapter
from altas.mobile_relay.store import LocalRelayStore, RelayInboxConflict
from altas.mobile_relay.worker import MobileRelayWorker, MobileRelayWorkerSettings


def _command(*, text: str = "worker-local secret") -> dict[str, Any]:
    return {
        "id": "relay_command_one",
        "session_id": "relay_session_one",
        "worker_sequence": 1,
        "command_type": "prompt.submit",
        "gateway_session_id": "gateway_one",
        "payload": {"text": text},
    }


def test_local_store_commits_encrypted_inbox_before_ack_and_recovers_uncertain(
    tmp_path: Path,
) -> None:
    path = tmp_path / "relay-worker.sqlite3"
    store = LocalRelayStore(path, storage_key=b"local-relay-test-key-material-v1")
    command = _command()
    assert store.persist_command(command) is True
    assert store.persist_command(command) is False
    with pytest.raises(RelayInboxConflict, match="relay_command_identity_conflict"):
        store.persist_command(_command(text="mutated reuse"))

    claimed = store.claim_next_command()
    assert claimed is not None
    assert claimed["payload"] == {"text": "worker-local secret"}
    assert claimed["state"] == "executing"

    database_bytes = b"".join(
        file.read_bytes() for file in tmp_path.glob(f"{path.name}*") if file.is_file()
    )
    assert b"worker-local secret" not in database_bytes

    restarted = LocalRelayStore(path, storage_key=b"local-relay-test-key-material-v1")
    uncertain = restarted.recover_uncertain()
    assert [item["id"] for item in uncertain] == ["relay_command_one"]
    assert restarted.claim_next_command() is None


def test_local_outbox_is_replayed_until_control_plane_ack(tmp_path: Path) -> None:
    store = LocalRelayStore(
        tmp_path / "relay-outbox.sqlite3",
        storage_key=b"local-relay-test-key-material-v1",
    )
    store.persist_command(_command())
    frame = {
        "type": "event",
        "source_event_id": "worker:event:one",
        "session_id": "relay_session_one",
        "command_id": "relay_command_one",
        "event_type": "message.complete",
        "payload": {"text": "durable response"},
    }
    assert store.persist_event(frame) is True
    assert store.persist_event(frame) is False
    assert store.pending_events() == [frame]
    store.acknowledge_event("worker:event:one")
    assert store.pending_events() == []
    store.acknowledge_event("worker:event:one")


class _FakeSocket:
    def __init__(self, frames: list[dict[str, Any]]) -> None:
        self._frames = [json.dumps(frame) for frame in frames]
        self.sent: list[dict[str, Any]] = []

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


class _UnusedAdapter:
    async def close(self) -> None:
        return None


class _UnusedSessionProvider:
    async def issue(self) -> str:
        return "unused"


def test_worker_sends_command_ack_only_after_durable_store_write(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = LocalRelayStore(
            tmp_path / "relay-ack.sqlite3",
            storage_key=b"local-relay-test-key-material-v1",
        )
        socket = _FakeSocket([{"type": "command", "command": _command()}])
        worker = MobileRelayWorker(
            settings=MobileRelayWorkerSettings(
                control_plane_url="http://127.0.0.1:8787",
                device_id="worker_one",
                device_private_key_path=tmp_path / "unused.key",
                store_path=tmp_path / "relay-ack.sqlite3",
            ),
            store=store,
            adapter=_UnusedAdapter(),  # type: ignore[arg-type]
            session_provider=_UnusedSessionProvider(),  # type: ignore[arg-type]
        )
        worker._socket = socket
        await worker._receive(socket)
        assert socket.sent == [
            {"type": "command.ack", "command_id": "relay_command_one"}
        ]
        claimed = store.claim_next_command()
        assert claimed is not None
        assert claimed["id"] == "relay_command_one"

    asyncio.run(scenario())


class _FakeGatewaySocket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.requests: list[dict[str, Any]] = []

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        value = await self.incoming.get()
        if value is None:
            raise StopAsyncIteration
        return value

    async def send(self, raw: str) -> None:
        request = json.loads(raw)
        self.requests.append(request)
        if request["method"] == "session.create":
            result = {
                "session_id": "local_gateway_session",
                "stored_session_id": "stored_gateway_session",
            }
        elif request["method"] == "prompt.submit":
            result = {"status": "streaming"}
        else:
            result = {"status": "interrupted"}
        await self.incoming.put(json.dumps({"id": request["id"], "result": result}))
        if request["method"] == "prompt.submit":
            for event_type, payload in (
                ("message.start", {}),
                ("message.delta", {"text": "hello"}),
                ("message.complete", {"text": "hello", "status": "complete"}),
            ):
                await self.incoming.put(
                    json.dumps({
                        "method": "event",
                        "params": {
                            "type": event_type,
                            "session_id": "local_gateway_session",
                            "payload": payload,
                        },
                    })
                )

    async def close(self) -> None:
        await self.incoming.put(None)


def test_gateway_adapter_is_loopback_and_method_allowlisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        socket = _FakeGatewaySocket()
        connection: dict[str, Any] = {}

        async def fake_connect(url: str, **kwargs: Any):
            connection["url"] = url
            connection["kwargs"] = kwargs
            return socket

        monkeypatch.setattr(
            "altas.mobile_relay.gateway_adapter.websockets.connect",
            fake_connect,
        )
        adapter = TuiGatewayTextAdapter(
            gateway_url="ws://127.0.0.1:8642/api/ws",
            gateway_authorization="token local-only-secret",
            workspace="/worker-owned/project",
        )
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def emit(event_type: str, payload: dict[str, Any]) -> None:
            emitted.append((event_type, payload))

        gateway_session_id = await adapter.execute(
            {
                "command_type": "session.create",
                "session_id": "relay_session_one",
                "gateway_session_id": None,
                "payload": {"cwd": "/phone-cannot-select-this"},
            },
            emit=emit,
        )
        assert gateway_session_id == "local_gateway_session"
        assert connection["url"].endswith("/api/ws?token=local-only-secret")
        assert "additional_headers" not in connection["kwargs"]
        assert socket.requests[0]["method"] == "session.create"
        assert socket.requests[0]["params"]["cwd"] == "/worker-owned/project"

        await adapter.execute(
            {
                "command_type": "prompt.submit",
                "session_id": "relay_session_one",
                "gateway_session_id": "local_gateway_session",
                "payload": {"text": "hello"},
            },
            emit=emit,
        )
        assert [request["method"] for request in socket.requests] == [
            "session.create",
            "prompt.submit",
        ]
        assert [event_type for event_type, _ in emitted] == [
            "session.created",
            "message.start",
            "message.delta",
            "message.complete",
        ]
        with pytest.raises(ValueError, match="loopback-only"):
            TuiGatewayTextAdapter(gateway_url="wss://gateway.example.com/api/ws")
        with pytest.raises(ValueError, match="must not contain a query"):
            TuiGatewayTextAdapter(
                gateway_url="ws://127.0.0.1:8642/api/ws?token=phone-value"
            )
        await adapter.close()

    asyncio.run(scenario())
