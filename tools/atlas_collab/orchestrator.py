"""High-level idempotent collaboration operations."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
from typing import Any

import yaml

from .adapters.buzz import BuzzAdapter
from .adapters.github import GitHubAdapter
from .config import config_path, inventory_path, load_config, state_path, write_config
from .context import build_context_manifest
from .keychain import CredentialVault, generate_nostr_keypair
from .models import (
    CollaborationEvent,
    ContractError,
    TaskContract,
    content_hash,
)
from .redaction import assert_non_secret, assert_safe_untrusted
from .state import StateStore


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:42] or "task"


def _channel_id(payload: dict[str, Any]) -> str:
    identifier = (
        payload.get("id") or payload.get("channel_id") or payload.get("channelId")
    )
    if not identifier:
        raise RuntimeError("Buzz channel response did not include an ID")
    return str(identifier)


def issue_contract(
    issue: dict[str, Any],
    *,
    workstream_id: str,
    repository: str,
    base_sha: str,
    base_ref: str = "main",
) -> TaskContract:
    body = str(issue.get("body") or "")
    assert_safe_untrusted({
        "title": str(issue.get("title") or ""),
        "body": body,
    })
    criteria = []
    for line in body.splitlines():
        match = re.match(r"\s*[-*]\s+\[[ xX]\]\s+(.+)", line)
        if not match:
            match = re.match(r"\s*[-*]\s+(AC-\d{2,})\s*[:—-]\s*(.+)", line)
            if match:
                identifier, text = match.groups()
                criteria.append({
                    "id": identifier,
                    "text": text.strip(),
                    "required_evidence": "test, diff, log, or review evidence",
                })
                continue
        if match:
            criteria.append({
                "id": f"AC-{len(criteria) + 1:02d}",
                "text": match.group(1).strip(),
                "required_evidence": "test, diff, log, or review evidence",
            })
    raw = {
        "schema_version": "atlas.collab.task.v1",
        "task_id": f"GH-{issue['number']}",
        "workstream_id": workstream_id,
        "repository": repository,
        "base_ref": base_ref,
        "base_sha": base_sha,
        "title": issue["title"],
        "goal": issue["title"],
        "in_scope": [],
        "out_of_scope": [],
        "acceptance_criteria": criteria,
        "constraints": [],
        "canonical_context": [
            "AGENTS.md",
            "docs/altas/PROJECT_CONTEXT.md",
            "docs/altas/DECISIONS.md",
            "docs/altas/SECURITY.md",
            "docs/altas/TESTING.md",
        ],
        "dependencies": [],
        "human_gates": ["Human approval is required before merge or deployment."],
        "requested_roles": ["coordinator", "implementer", "reviewer"],
    }
    return TaskContract.from_mapping(raw)


class Orchestrator:
    def __init__(
        self,
        *,
        root: Path,
        store: StateStore,
        config: dict[str, Any],
        buzz: Any | None = None,
        github: Any | None = None,
    ):
        self.root = root.resolve()
        self.store = store
        self.config = config
        self.buzz = buzz
        self.github = github

    def bootstrap_plan(self) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = [
            {
                "kind": "config",
                "target": str(config_path()),
                "action": "reuse" if config_path().exists() else "create",
            },
            {
                "kind": "state",
                "target": str(state_path()),
                "action": "reuse" if state_path().exists() else "create",
            },
        ]
        for role, settings in self.config["roles"].items():
            actions.append({
                "kind": "identity",
                "target": role,
                "display_name": settings["display_name"],
                "action": (
                    "reuse"
                    if any(agent["role"] == role for agent in self.store.agents())
                    else "enroll"
                ),
            })
        for key in ("control_channel", "decisions_channel", "reviews_channel"):
            actions.append({
                "kind": "buzz-channel",
                "target": self.config["buzz"][key],
                "action": "ensure",
            })
        return actions

    def bootstrap(self) -> list[dict[str, Any]]:
        results = self.bootstrap_plan()
        if self.buzz:
            channel_id_keys = {
                "control_channel": "control_channel_id",
                "decisions_channel": "decisions_channel_id",
                "reviews_channel": "reviews_channel_id",
            }
            for item in results:
                if item["kind"] != "buzz-channel":
                    continue
                channel, created = self.buzz.ensure_channel(
                    name=item["target"],
                    description="Atlas development collaboration; GitHub remains authoritative.",
                    visibility=self.config["buzz"]["visibility"],
                )
                item["created"] = created
                item["channel_id"] = _channel_id(channel)
                for name_key, id_key in channel_id_keys.items():
                    if item["target"] == self.config["buzz"][name_key]:
                        self.config["buzz"][id_key] = item["channel_id"]
                        break
            write_config(config_path(), self.config)
        self.write_inventory()
        return results

    def enroll_agent(
        self,
        *,
        role: str,
        vault: CredentialVault,
    ) -> dict[str, Any]:
        if role not in self.config["roles"]:
            raise ValueError(f"unknown configured role: {role}")
        existing = next(
            (agent for agent in self.store.agents() if agent["role"] == role),
            None,
        )
        if existing:
            relay_status = self._publish_role_profile(role)
            return {
                "agent": existing,
                "created": False,
                "relay_status": relay_status,
            }
        private_key, public_key = generate_nostr_keypair()
        vault.set(role, private_key)
        settings = self.config["roles"][role]
        agent_id = f"agent-{role}"
        self.store.register_agent(
            agent_id=agent_id,
            role=role,
            display_name=settings["display_name"],
            public_key=public_key,
            runtime=settings["runtime"],
            profile=settings["profile"],
        )
        self.config["roles"][role]["public_key"] = public_key
        write_config(config_path(), self.config)
        relay_status = self._publish_role_profile(role)
        self.write_inventory()
        agent = next(agent for agent in self.store.agents() if agent["role"] == role)
        return {"agent": agent, "created": True, "relay_status": relay_status}

    def _publish_role_profile(self, role: str) -> str:
        if not self.buzz:
            return "not-attempted"
        settings = self.config["roles"][role]
        try:
            self.buzz.set_profile(
                name=settings["display_name"],
                about=(
                    f"Atlas development-only {role}; cannot merge, deploy, "
                    "or widen permissions."
                ),
            )
        except Exception as exc:
            return f"owner-approval-or-relay-pending: {exc}"
        return "profile-published"

    def create_task(
        self,
        contract: TaskContract,
        *,
        issue: int | None = None,
    ) -> dict[str, Any]:
        created = self.store.create_task(contract, issue=issue)
        if not created:
            existing = self.store.task(contract.task_id)
            if existing and existing["contract_hash"] != contract.digest:
                raise ContractError(
                    f"task {contract.task_id} exists with a different contract hash"
                )
        manifest = build_context_manifest(self.root, list(contract.canonical_context))
        task_slug = f"{contract.workstream_id.lower()}-{_slug(contract.title)}"
        channel_id = ""
        canvas_id = ""
        buzz_link = ""
        if self.buzz:
            channel, _ = self.buzz.ensure_channel(
                name=task_slug,
                description=f"{contract.workstream_id}: {contract.title}",
                visibility=self.config["buzz"]["visibility"],
            )
            channel_id = _channel_id(channel)
            canvas = (
                f"# {contract.workstream_id} — {contract.title}\n\n"
                f"State: `intake`\n\n"
                f"Base: `{contract.base_sha}`\n\n"
                f"Task contract: `{contract.digest}`\n\n"
                f"Context manifest: `{manifest['manifest_hash']}`\n\n"
                "GitHub is authoritative for scope, commits, CI, review, and merge."
            )
            canvas_result = self.buzz.set_canvas(channel_id, canvas)
            canvas_id = str(
                canvas_result.get("event_id")
                or canvas_result.get("id")
                or canvas_result.get("version")
                or ""
            )
            event = CollaborationEvent(
                task_id=contract.task_id,
                workstream_id=contract.workstream_id,
                actor_id="atlas-coordinator",
                actor_role="coordinator",
                event_type="TASK_CREATED",
                status="intake",
                summary=(
                    f"Task accepted at base {contract.base_sha[:12]}; "
                    "context acknowledgement and plan review are required before edits."
                ),
                base_sha=contract.base_sha,
                acceptance_criteria_ids=tuple(
                    criterion.id for criterion in contract.acceptance_criteria
                ),
                next_action="All requested agents acknowledge contract/context hashes.",
                event_id=f"evt-{content_hash({'task': contract.task_id, 'type': 'TASK_CREATED'})[:32]}",
            )
            self.store.record_event(event)
            buzz_event_id, _ = self.buzz.send_event(
                self.store, event, channel_id=channel_id
            )
            buzz_link = self.buzz.deep_link(
                self.config["buzz"]["community"],
                channel_id,
                buzz_event_id,
            )
        self.store.update_task_refs(
            contract.task_id,
            context_hash=manifest["manifest_hash"],
            buzz_channel_id=channel_id or None,
            buzz_canvas_id=canvas_id or None,
        )
        github_link = ""
        if issue is not None and self.github:
            body = (
                f"Atlas collaboration task `{contract.task_id}` entered `intake`.\n\n"
                f"- Base SHA: `{contract.base_sha}`\n"
                f"- Contract hash: `{contract.digest}`\n"
                f"- Context hash: `{manifest['manifest_hash']}`\n"
                f"- Buzz task event: {buzz_link or 'degraded/unavailable'}\n\n"
                "No product edits or merge/deploy authority were granted."
            )
            github_link, _ = self.github.ensure_comment(
                self.store,
                issue=issue,
                marker=f"{contract.task_id}:TASK_CREATED",
                body=body,
            )
        self.write_inventory()
        return {
            "created": created,
            "resumed": not created,
            "task_id": contract.task_id,
            "contract_hash": contract.digest,
            "context_hash": manifest["manifest_hash"],
            "channel_id": channel_id,
            "buzz_link": buzz_link,
            "github_link": github_link,
        }

    def create_clarification(
        self,
        *,
        task_id: str,
        workstream_id: str,
        base_sha: str,
        title: str,
        intake: dict[str, Any],
        reason: str,
        issue: int | None = None,
    ) -> dict[str, Any]:
        created = self.store.create_needs_clarification(
            task_id=task_id,
            workstream_id=workstream_id,
            base_sha=base_sha,
            intake=intake,
            reason=reason,
            issue=issue,
        )
        if not created:
            return {"created": False, "task": self.store.task(task_id)}
        channel_id = ""
        buzz_link = ""
        if self.buzz:
            channel, _ = self.buzz.ensure_channel(
                name=f"{workstream_id.lower()}-{_slug(title)}",
                description=f"{workstream_id}: clarification required",
                visibility=self.config["buzz"]["visibility"],
            )
            channel_id = _channel_id(channel)
            canvas = (
                f"# {workstream_id} — {title}\n\n"
                "State: `needs-clarification`\n\n"
                f"Missing decision: {reason}\n\n"
                "No product work is authorized until the task contract is corrected."
            )
            self.buzz.set_canvas(channel_id, canvas)
            event = CollaborationEvent(
                task_id=task_id,
                workstream_id=workstream_id,
                actor_id="atlas-coordinator",
                actor_role="coordinator",
                event_type="TASK_CREATED",
                status="needs-clarification",
                summary=f"Task intake is blocked: {reason}",
                base_sha=base_sha,
                blockers=(reason,),
                next_action="A human supplies observable acceptance criteria.",
            )
            self.store.record_event(event)
            event_id, _ = self.buzz.send_event(self.store, event, channel_id=channel_id)
            buzz_link = self.buzz.deep_link(
                self.config["buzz"]["community"], channel_id, event_id
            )
            self.store.update_task_refs(task_id, buzz_channel_id=channel_id)
        github_link = ""
        if issue is not None and self.github:
            github_link, _ = self.github.ensure_comment(
                self.store,
                issue=issue,
                marker=f"{task_id}:NEEDS_CLARIFICATION",
                body=(
                    f"Atlas collaboration task `{task_id}` is in "
                    f"`needs-clarification`: {reason}\n\n"
                    f"Buzz event: {buzz_link or 'degraded/unavailable'}\n\n"
                    "No product edits are authorized."
                ),
            )
        self.write_inventory()
        return {
            "created": True,
            "task_id": task_id,
            "state": "needs-clarification",
            "reason": reason,
            "channel_id": channel_id,
            "buzz_link": buzz_link,
            "github_link": github_link,
        }

    def replay(self, task_id: str) -> dict[str, Any]:
        task = self.store.task(task_id)
        if task is None:
            raise KeyError(task_id)
        events = self.store.events(task_id)
        return {
            "task_id": task_id,
            "state": task["state"],
            "event_count": len(events),
            "events": events,
        }

    def transition_task(self, task_id: str, target: str) -> dict[str, Any]:
        before = self.store.task(task_id)
        if before is None:
            raise KeyError(task_id)
        changed = self.store.transition(
            task_id,
            target,
            actor_role="coordinator",
        )
        after = self.store.task(task_id)
        if after is None:
            raise KeyError(task_id)
        event_type = {
            "blocked": "BLOCKED",
            "review_requested": "REVIEW_REQUESTED",
            "changes_requested": "CHANGES_REQUESTED",
            "integrating": "INTEGRATION_STARTED",
            "human_approval_required": "HUMAN_GATE_REQUIRED",
            "completed": "TASK_COMPLETED",
            "canceled": "TASK_CANCELED",
        }.get(after["state"], "STATUS_CHANGED")
        event = None
        if not changed:
            event = next(
                (
                    CollaborationEvent(**item)
                    for item in reversed(self.store.events(task_id))
                    if item["event_type"] == event_type
                    and item["status"] == after["state"]
                ),
                None,
            )
        if event is None:
            sequence = len(self.store.events(task_id)) + 1
            event_identity = content_hash({
                "task": task_id,
                "from": before["state"],
                "to": after["state"],
                "sequence": sequence,
            })
            event = CollaborationEvent(
                task_id=task_id,
                workstream_id=after["workstream_id"],
                actor_id="atlas-coordinator",
                actor_role="coordinator",
                event_type=event_type,
                status=after["state"],
                summary=(
                    f"Coordinator changed task state from "
                    f"{before['state']} to {after['state']}."
                ),
                base_sha=after["base_sha"],
                branch=after.get("branch") or "",
                worktree_id=after.get("worktree") or "",
                acceptance_criteria_ids=tuple(
                    item["id"]
                    for item in after["contract"].get("acceptance_criteria", [])
                ),
                next_action=(
                    "No further agent turns are authorized."
                    if after["state"] == "canceled"
                    else "Resume only through an explicit coordinator action."
                    if after["state"] == "paused"
                    else "Continue within the accepted task contract."
                ),
                event_id=f"evt-{event_identity[:32]}",
            )
            self.store.record_event(event)
        result = {
            "changed": changed,
            "task": after,
            "event": event.to_dict(),
            "buzz_link": "",
            "github_link": "",
            "degraded": [],
        }
        channel_id = str(after.get("buzz_channel_id") or "")
        if channel_id and self.buzz:
            try:
                buzz_event_id, _ = self.buzz.send_event(
                    self.store,
                    event,
                    channel_id=channel_id,
                )
                result["buzz_link"] = self.buzz.deep_link(
                    self.config["buzz"]["community"],
                    channel_id,
                    buzz_event_id,
                )
            except Exception as exc:
                result["degraded"].append(f"buzz: {exc}")
        issue = after.get("github_issue")
        if issue is not None and self.github:
            try:
                result["github_link"], _ = self.github.ensure_comment(
                    self.store,
                    issue=int(issue),
                    marker=f"{task_id}:{event.event_id}",
                    body=(
                        f"Atlas collaboration task `{task_id}` changed from "
                        f"`{before['state']}` to `{after['state']}`.\n\n"
                        f"Buzz event: {result['buzz_link'] or 'degraded/unavailable'}\n\n"
                        "This milestone grants no merge or deployment authority."
                    ),
                )
            except Exception as exc:
                result["degraded"].append(f"github: {exc}")
        return result

    def cleanup_plan(self, task_id: str) -> list[dict[str, Any]]:
        task = self.store.task(task_id)
        if task is None:
            raise KeyError(task_id)
        return [
            {"action": "stop-task-processes", "task_id": task_id},
            {"action": "release-claims", "task_id": task_id},
            {
                "action": "retain-review-artifacts",
                "branch": task["branch"],
                "worktree": task["worktree"],
            },
            {
                "action": "never-delete-or-merge-automatically",
                "task_id": task_id,
            },
        ]

    def write_inventory(self) -> Path:
        tasks = [self.store.task(item["task_id"]) for item in self.store.tasks()]
        task_records = [item for item in tasks if item is not None]
        inventory = {
            "schema_version": "atlas.collab.inventory.v1",
            "relay_url": self.config["buzz"]["relay_url"],
            "community": self.config["buzz"]["community"],
            "channels": {
                "control": {
                    "name": self.config["buzz"]["control_channel"],
                    "id": self.config["buzz"].get("control_channel_id", ""),
                },
                "decisions": {
                    "name": self.config["buzz"]["decisions_channel"],
                    "id": self.config["buzz"].get("decisions_channel_id", ""),
                },
                "reviews": {
                    "name": self.config["buzz"]["reviews_channel"],
                    "id": self.config["buzz"].get("reviews_channel_id", ""),
                },
            },
            "agents": self.store.agents(),
            "profiles": {
                role: settings["profile"]
                for role, settings in self.config["roles"].items()
            },
            "services": self.store.services(),
            "worktree_roots": {
                role: settings.get("worktree", "")
                for role, settings in self.config["roles"].items()
            },
            "tasks": [
                {
                    key: task.get(key)
                    for key in (
                        "task_id",
                        "workstream_id",
                        "state",
                        "base_sha",
                        "branch",
                        "worktree",
                        "buzz_channel_id",
                        "buzz_canvas_id",
                        "github_issue",
                        "github_pr",
                        "updated_at",
                    )
                }
                for task in task_records
            ],
            "task_agent_bindings": {
                task["task_id"]: self.store.task_agents(task["task_id"])
                for task in task_records
            },
            "last_verified_health": {
                "buzz_channels": (
                    "verified"
                    if all(
                        self.config["buzz"].get(key)
                        for key in (
                            "control_channel_id",
                            "decisions_channel_id",
                            "reviews_channel_id",
                        )
                    )
                    else "not-verified"
                ),
                "role_profiles": (
                    "configured"
                    if all(
                        settings.get("profile")
                        for settings in self.config["roles"].values()
                    )
                    else "incomplete"
                ),
                "role_bindings": (
                    "configured"
                    if all(
                        settings.get("task_id")
                        and settings.get("branch")
                        and settings.get("worktree")
                        for settings in self.config["roles"].values()
                    )
                    else "incomplete"
                ),
            },
            "state_path": str(self.store.path),
            "config_path": str(config_path()),
        }
        assert_non_secret(inventory)
        path = inventory_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(inventory, indent=2, sort_keys=True), encoding="utf-8"
        )
        path.chmod(0o600)
        return path


def load_task_contract(path: Path) -> TaskContract:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ContractError("task contract must be a YAML mapping")
    return TaskContract.from_mapping(raw)
