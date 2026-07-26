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
from .config import config_path, inventory_path, load_config, state_path
from .context import build_context_manifest
from .keychain import CredentialVault, generate_nostr_keypair
from .models import (
    CollaborationEvent,
    ContractError,
    TaskContract,
    content_hash,
)
from .redaction import assert_non_secret
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
            return {"agent": existing, "created": False}
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
        config_path().parent.mkdir(parents=True, exist_ok=True)
        config_path().write_text(
            yaml.safe_dump(self.config, sort_keys=False),
            encoding="utf-8",
        )
        config_path().chmod(0o600)
        relay_status = "not-attempted"
        if self.buzz:
            try:
                self.buzz.set_profile(
                    name=settings["display_name"],
                    about=(
                        f"Atlas development-only {role}; cannot merge, deploy, "
                        "or widen permissions."
                    ),
                )
                relay_status = "profile-published"
            except Exception as exc:
                relay_status = f"owner-approval-or-relay-pending: {exc}"
        self.write_inventory()
        agent = next(agent for agent in self.store.agents() if agent["role"] == role)
        return {"agent": agent, "created": True, "relay_status": relay_status}

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
        inventory = {
            "schema_version": "atlas.collab.inventory.v1",
            "relay_url": self.config["buzz"]["relay_url"],
            "community": self.config["buzz"]["community"],
            "agents": self.store.agents(),
            "tasks": self.store.tasks(),
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
