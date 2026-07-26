"""Thin adapter over the official Buzz CLI JSON interface."""

from __future__ import annotations

from dataclasses import dataclass
import json
import shutil
from typing import Any, Callable

from ..models import CollaborationEvent, content_hash
from ..state import StateStore
from .process import CommandError, CommandRunner


@dataclass(frozen=True)
class BuzzHealth:
    available: bool
    authenticated: bool
    relay_url: str
    detail: str


class BuzzAdapter:
    def __init__(
        self,
        *,
        relay_url: str,
        private_key_provider: Callable[[], str | None],
        auth_tag_provider: Callable[[], str | None] | None = None,
        runner: CommandRunner | None = None,
        binary: str = "buzz",
    ):
        self.relay_url = relay_url
        self._private_key_provider = private_key_provider
        self._auth_tag_provider = auth_tag_provider or (lambda: None)
        self.runner = runner or CommandRunner()
        self.binary = binary

    def _env(self) -> dict[str, str]:
        key = self._private_key_provider()
        if not key:
            raise RuntimeError(
                "Buzz credential is unavailable from the configured vault"
            )
        env = {"BUZZ_RELAY_URL": self.relay_url, "BUZZ_PRIVATE_KEY": key}
        auth_tag = self._auth_tag_provider()
        if auth_tag:
            env["BUZZ_AUTH_TAG"] = auth_tag
        return env

    def _run(
        self,
        args: list[str],
        *,
        stdin: str | None = None,
        check: bool = True,
    ) -> Any:
        result = self.runner.run(
            [self.binary, "--format", "json", *args],
            env=self._env(),
            stdin=stdin,
            check=check,
        )
        return result.json()

    def feature(self, *command: str) -> bool:
        if not shutil.which(self.binary):
            return False
        result = self.runner.run(
            [self.binary, *command, "--help"], check=False, timeout=10
        )
        return result.code == 0

    def probe(self) -> BuzzHealth:
        if not shutil.which(self.binary):
            return BuzzHealth(False, False, self.relay_url, "buzz CLI not found")
        try:
            self._run(["users", "get"])
        except RuntimeError as exc:
            return BuzzHealth(True, False, self.relay_url, str(exc))
        except CommandError as exc:
            return BuzzHealth(True, exc.code != 3, self.relay_url, exc.detail)
        return BuzzHealth(True, True, self.relay_url, "connected")

    def channels(self) -> list[dict[str, Any]]:
        payload = self._run(["channels", "list", "--member"])
        if isinstance(payload, list):
            return payload
        return payload.get("channels", [])

    def find_channel(self, name: str) -> dict[str, Any] | None:
        payload = self._run(["channels", "search", "--query", name, "--exact"])
        channels = payload if isinstance(payload, list) else payload.get("channels", [])
        active = [item for item in channels if not item.get("archived")]
        if len(active) > 1:
            raise RuntimeError(f"multiple active Buzz channels named {name!r}")
        return active[0] if active else None

    def ensure_channel(
        self,
        *,
        name: str,
        description: str,
        visibility: str = "private",
    ) -> tuple[dict[str, Any], bool]:
        existing = self.find_channel(name)
        if existing:
            return existing, False
        created = self._run([
            "channels",
            "create",
            "--name",
            name,
            "--type",
            "stream",
            "--visibility",
            visibility,
            "--description",
            description,
        ])
        return created, True

    def add_member(self, channel_id: str, public_key: str, role: str = "bot") -> Any:
        return self._run([
            "channels",
            "add-member",
            "--channel",
            channel_id,
            "--pubkey",
            public_key,
            "--role",
            role,
        ])

    def set_profile(self, *, name: str, about: str) -> Any:
        return self._run(["users", "set-profile", "--name", name, "--about", about])

    def set_canvas(self, channel_id: str, content: str) -> Any:
        return self._run(
            ["canvas", "set", "--channel", channel_id, "--content", "-"],
            stdin=content,
        )

    def send(
        self,
        *,
        channel_id: str,
        content: str,
        reply_to: str = "",
    ) -> dict[str, Any]:
        args = [
            "messages",
            "send",
            "--channel",
            channel_id,
            "--content",
            "-",
        ]
        if reply_to:
            args.extend(["--reply-to", reply_to])
        payload = self._run(args, stdin=content)
        if not isinstance(payload, dict):
            raise RuntimeError("Buzz message response was not a JSON object")
        return payload

    def send_event(
        self,
        store: StateStore,
        event: CollaborationEvent,
        *,
        channel_id: str,
    ) -> tuple[str, bool]:
        payload = event.render_buzz()
        key = f"buzz:{channel_id}:{event.idempotency_key}"
        existing = store.external_write(key)
        if existing and existing["status"] == "complete":
            return str(existing["external_id"]), False
        recovered = self.find_event_by_marker(
            event.idempotency_key,
            channel_id=channel_id,
        )
        if recovered:
            if not existing:
                store.reserve_external_write(
                    key,
                    system="buzz",
                    target=channel_id,
                    payload_hash=content_hash(payload),
                )
            store.finish_external_write(key, recovered)
            return recovered, False
        if not existing:
            store.reserve_external_write(
                key,
                system="buzz",
                target=channel_id,
                payload_hash=content_hash(payload),
            )
        result = self.send(
            channel_id=channel_id,
            content=payload,
            reply_to=event.reply_to,
        )
        external_id = str(
            result.get("event_id") or result.get("id") or result.get("eventId") or ""
        )
        if not external_id:
            raise RuntimeError("Buzz did not return an event ID")
        store.finish_external_write(key, external_id)
        return external_id, True

    def find_event_by_marker(
        self,
        marker: str,
        *,
        channel_id: str,
    ) -> str:
        payload = self._run([
            "messages",
            "search",
            "--query",
            f"atlas-collab:{marker}",
            "--limit",
            "10",
        ])
        items = payload if isinstance(payload, list) else payload.get("messages", [])
        matches = []
        for item in items:
            item_channel = str(item.get("channel_id") or item.get("channelId") or "")
            content = str(item.get("content") or item.get("text") or "")
            event_id = str(
                item.get("event_id") or item.get("eventId") or item.get("id") or ""
            )
            if (
                item_channel == channel_id
                and f"atlas-collab:{marker}" in content
                and event_id
            ):
                matches.append(event_id)
        if len(matches) > 1:
            raise RuntimeError(f"multiple Buzz events use idempotency marker {marker}")
        return matches[0] if matches else ""

    @staticmethod
    def deep_link(community: str, channel_id: str, event_id: str = "") -> str:
        base = f"buzz://community/{community}/channel/{channel_id}"
        return f"{base}?event={event_id}" if event_id else base


class FakeBuzzAdapter:
    """Deterministic adapter used for integration and failure tests."""

    def __init__(self):
        self.online = True
        self.channels_by_name: dict[str, dict[str, Any]] = {}
        self.messages: list[dict[str, Any]] = []
        self.canvases: dict[str, str] = {}

    def probe(self) -> BuzzHealth:
        return BuzzHealth(self.online, self.online, "fake://relay", "connected")

    def ensure_channel(
        self, *, name: str, description: str, visibility: str = "private"
    ) -> tuple[dict[str, Any], bool]:
        if not self.online:
            raise ConnectionError("fake Buzz offline")
        if name in self.channels_by_name:
            return self.channels_by_name[name], False
        channel = {
            "id": f"channel-{len(self.channels_by_name) + 1}",
            "name": name,
            "description": description,
            "visibility": visibility,
        }
        self.channels_by_name[name] = channel
        return channel, True

    def set_canvas(self, channel_id: str, content: str) -> dict[str, str]:
        if not self.online:
            raise ConnectionError("fake Buzz offline")
        self.canvases[channel_id] = content
        return {"channel_id": channel_id}

    def send_event(
        self, store: StateStore, event: CollaborationEvent, *, channel_id: str
    ) -> tuple[str, bool]:
        if not self.online:
            raise ConnectionError("fake Buzz offline")
        key = f"buzz:{channel_id}:{event.idempotency_key}"
        existing = store.external_write(key)
        if existing and existing["status"] == "complete":
            return str(existing["external_id"]), False
        recovered = next(
            (
                str(item["id"])
                for item in self.messages
                if item["channel_id"] == channel_id
                and item.get("idempotency_key") == event.idempotency_key
            ),
            "",
        )
        if recovered:
            if not existing:
                store.reserve_external_write(
                    key,
                    system="buzz",
                    target=channel_id,
                    payload_hash=content_hash(event.render_buzz()),
                )
            store.finish_external_write(key, recovered)
            return recovered, False
        if not existing:
            store.reserve_external_write(
                key,
                system="buzz",
                target=channel_id,
                payload_hash=content_hash(event.render_buzz()),
            )
        event_id = f"fake-event-{len(self.messages) + 1}"
        self.messages.append({
            "id": event_id,
            "channel_id": channel_id,
            "idempotency_key": event.idempotency_key,
            "payload": json.loads(json.dumps(event.to_dict())),
        })
        store.finish_external_write(key, event_id)
        return event_id, True

    @staticmethod
    def deep_link(community: str, channel_id: str, event_id: str = "") -> str:
        base = f"buzz://community/{community}/channel/{channel_id}"
        return f"{base}?event={event_id}" if event_id else base
