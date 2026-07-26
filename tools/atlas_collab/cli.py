"""Command-line interface for the Atlas development collaboration plane."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

import yaml

from . import __version__
from .adapters.buzz import BuzzAdapter
from .adapters.github import GitHubAdapter
from .adapters.runtime import RuntimeInventory
from .config import (
    DEFAULTS,
    config_path,
    inventory_path,
    load_config,
    state_path,
    write_default_config,
)
from .keychain import KeyringVault
from .models import ContractError
from .orchestrator import Orchestrator, issue_contract, load_task_contract
from .services import install_services, service_action, service_dependencies
from .state import StateStore


def _json(value: Any) -> None:
    if is_dataclass(value):
        value = asdict(value)
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _repo_root() -> Path:
    result = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
        check=False,
    )
    return Path(result.stdout.strip()) if result.returncode == 0 else Path.cwd()


def _vault_or_none() -> KeyringVault | None:
    try:
        return KeyringVault()
    except RuntimeError:
        return None


def _buzz(
    config: dict[str, Any],
    *,
    role: str = "coordinator",
) -> BuzzAdapter | None:
    if not config["buzz"]["relay_url"]:
        return None
    vault = _vault_or_none()
    if vault is None:
        return None
    return BuzzAdapter(
        relay_url=config["buzz"]["relay_url"],
        private_key_provider=lambda: vault.get(role),
    )


def _orchestrator(
    root: Path,
    store: StateStore,
    config: dict[str, Any],
    *,
    buzz_role: str = "coordinator",
    with_external: bool = True,
) -> Orchestrator:
    buzz = _buzz(config, role=buzz_role) if with_external else None
    github = GitHubAdapter(config["repository"], root) if with_external else None
    return Orchestrator(
        root=root,
        store=store,
        config=config,
        buzz=buzz,
        github=github,
    )


def doctor(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    vault = _vault_or_none()
    vault_detail = "available"
    role_credentials = {}
    for role in config["roles"]:
        try:
            role_credentials[role] = bool(vault and vault.get(role))
        except RuntimeError as exc:
            role_credentials[role] = False
            vault_detail = str(exc)
    buzz = _buzz(config)
    buzz_health = (
        asdict(buzz.probe())
        if buzz and role_credentials.get("coordinator")
        else {
            "available": bool(shutil.which("buzz")),
            "authenticated": False,
            "relay_url": config["buzz"]["relay_url"],
            "detail": "coordinator vault credential or relay config is missing",
        }
    )
    github = GitHubAdapter(config["repository"], root)
    runtime = [asdict(item) for item in RuntimeInventory().detect()]
    dependencies = service_dependencies()
    checks = {
        "repository": {
            "root": str(root),
            "git_checkout": (root / ".git").exists() or (root / ".git").is_file(),
            "clean": not bool(
                subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=root,
                    text=True,
                    capture_output=True,
                    check=False,
                ).stdout
            ),
        },
        "config": {
            "path": str(config_path()),
            "exists": config_path().exists(),
            "non_secret": True,
        },
        "state": {"path": str(state_path()), "exists": state_path().exists()},
        "inventory": {
            "path": str(inventory_path()),
            "exists": inventory_path().exists(),
        },
        "buzz": buzz_health,
        "github": asdict(github.probe()),
        "runtime": runtime,
        "vault": {
            "backend_available": vault is not None,
            "detail": vault_detail,
            "role_credentials": role_credentials,
        },
        "services": dependencies,
        "boundaries": {
            "development_only": True,
            "automatic_merge": False,
            "automatic_deploy": False,
            "product_permission_widening": False,
            "periodic_llm_heartbeat_seconds": config["routing"][
                "recovery_heartbeat_seconds"
            ],
        },
    }
    checks["ready"] = bool(
        checks["repository"]["git_checkout"]
        and checks["github"]["authenticated"]
        and checks["buzz"]["authenticated"]
        and all(role_credentials.values())
    )
    return checks


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atlas-collab")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor")

    bootstrap = sub.add_parser("bootstrap")
    mode = bootstrap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")

    agents = sub.add_parser("agents")
    agents_sub = agents.add_subparsers(dest="agents_command", required=True)
    agents_sub.add_parser("list")
    enroll = agents_sub.add_parser("enroll")
    enroll.add_argument("--role", required=True)
    enroll.add_argument("--apply", action="store_true")

    services = sub.add_parser("services")
    services_sub = services.add_subparsers(dest="services_command", required=True)
    install = services_sub.add_parser("install")
    install.add_argument("--apply", action="store_true")
    for name in ("start", "stop", "status"):
        services_sub.add_parser(name)

    task = sub.add_parser("task")
    task_sub = task.add_subparsers(dest="task_command", required=True)
    create = task_sub.add_parser("create")
    source = create.add_mutually_exclusive_group(required=True)
    source.add_argument("--issue", type=int)
    source.add_argument("--file", type=Path)
    create.add_argument("--workstream", default="WS-22")
    create.add_argument("--apply", action="store_true")
    status = task_sub.add_parser("status")
    status.add_argument("task_id")
    for name in ("pause", "resume", "cancel"):
        command = task_sub.add_parser(name)
        command.add_argument("task_id")
        command.add_argument("--apply", action="store_true")
    replay = task_sub.add_parser("replay")
    replay.add_argument("task_id")

    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--task", required=True)
    mode = cleanup.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = _repo_root()
    if args.command == "bootstrap" and args.apply:
        write_default_config(config_path(), DEFAULTS)
    config = load_config()
    read_only_without_state = (
        args.command == "doctor" or (args.command == "bootstrap" and args.dry_run)
    ) and not state_path().exists()
    selected_state = Path(":memory:") if read_only_without_state else state_path()
    with StateStore(selected_state) as store:
        if args.command == "doctor":
            _json(doctor(root, config))
            return 0
        if args.command == "bootstrap":
            orchestrator = _orchestrator(
                root,
                store,
                config,
                with_external=args.apply,
            )
            if args.dry_run:
                _json({"apply": False, "actions": orchestrator.bootstrap_plan()})
            else:
                _json({"apply": True, "actions": orchestrator.bootstrap()})
            return 0
        if args.command == "agents":
            if args.agents_command == "list":
                _json(store.agents())
                return 0
            if not args.apply:
                _json({
                    "apply": False,
                    "role": args.role,
                    "actions": [
                        "generate distinct secp256k1 keypair",
                        "store private key in OS credential vault",
                        "persist public identity and isolated runtime profile",
                        "attempt relay profile publication",
                    ],
                })
                return 0
            vault = KeyringVault()
            orchestrator = _orchestrator(
                root,
                store,
                config,
                buzz_role=args.role,
            )
            _json(orchestrator.enroll_agent(role=args.role, vault=vault))
            return 0
        if args.command == "services":
            if args.services_command == "install":
                _json(install_services(apply=args.apply))
            else:
                _json(service_action(args.services_command))
            return 0
        orchestrator = _orchestrator(
            root,
            store,
            config,
            with_external=getattr(args, "apply", False),
        )
        if args.command == "task":
            if args.task_command == "status":
                task = store.task(args.task_id)
                if not task:
                    raise KeyError(args.task_id)
                _json(task)
                return 0
            if args.task_command == "replay":
                _json(orchestrator.replay(args.task_id))
                return 0
            if args.task_command in {"pause", "resume", "cancel"}:
                target = {
                    "pause": "paused",
                    "resume": "resume",
                    "cancel": "canceled",
                }[args.task_command]
                if not args.apply:
                    _json({
                        "apply": False,
                        "task_id": args.task_id,
                        "target": target,
                    })
                    return 0
                changed = store.transition(
                    args.task_id, target, actor_role="coordinator"
                )
                _json({
                    "apply": True,
                    "changed": changed,
                    "task": store.task(args.task_id),
                })
                return 0
            if args.issue is not None:
                github = GitHubAdapter(config["repository"], root)
                issue = github.issue(args.issue)
                base_sha = github.base_sha(config["base_ref"])
                issue_number = args.issue
                try:
                    contract = issue_contract(
                        issue,
                        workstream_id=args.workstream,
                        repository=config["repository"],
                        base_sha=base_sha,
                        base_ref=config["base_ref"],
                    )
                except ContractError as exc:
                    if not args.apply:
                        _json({
                            "apply": False,
                            "task_id": f"GH-{args.issue}",
                            "target_state": "needs-clarification",
                            "reason": str(exc),
                        })
                        return 0
                    _json(
                        orchestrator.create_clarification(
                            task_id=f"GH-{args.issue}",
                            workstream_id=args.workstream,
                            base_sha=base_sha,
                            title=issue["title"],
                            intake={
                                "issue": args.issue,
                                "title": issue["title"],
                                "url": issue.get("url", ""),
                            },
                            reason=str(exc),
                            issue=args.issue,
                        )
                    )
                    return 0
            else:
                try:
                    contract = load_task_contract(args.file)
                except ContractError as exc:
                    raw = yaml.safe_load(args.file.read_text(encoding="utf-8")) or {}
                    if not isinstance(raw, dict):
                        raw = {}
                    task_id = str(raw.get("task_id") or f"INTAKE-{args.file.stem}")
                    workstream = str(raw.get("workstream_id") or args.workstream)
                    base_sha = str(raw.get("base_sha") or "unresolved")
                    if not args.apply:
                        _json({
                            "apply": False,
                            "task_id": task_id,
                            "target_state": "needs-clarification",
                            "reason": str(exc),
                        })
                        return 0
                    _json(
                        orchestrator.create_clarification(
                            task_id=task_id,
                            workstream_id=workstream,
                            base_sha=base_sha,
                            title=str(raw.get("title") or args.file.stem),
                            intake={"source_file": str(args.file)},
                            reason=str(exc),
                        )
                    )
                    return 0
                issue_number = None
            if not args.apply:
                _json({
                    "apply": False,
                    "task_id": contract.task_id,
                    "contract_hash": contract.digest,
                    "actions": [
                        "create one durable task record",
                        "hash canonical context",
                        "ensure one task channel and canvas",
                        "post one TASK_CREATED event",
                        "write one GitHub milestone comment when issue-backed",
                    ],
                })
                return 0
            _json(orchestrator.create_task(contract, issue=issue_number))
            return 0
        if args.command == "cleanup":
            plan = orchestrator.cleanup_plan(args.task)
            if args.apply:
                # Cleanup releases orchestration leases/state only. Branches,
                # worktrees, channels, and review artifacts are retained.
                from .claims import release_claims

                released = release_claims(store, args.task)
                _json({"apply": True, "released_claims": released, "plan": plan})
            else:
                _json({"apply": False, "plan": plan})
            return 0
    return 1


def entrypoint() -> int:
    try:
        return main()
    except (ContractError, KeyError, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(entrypoint())
