"""Command-line interface for the Atlas development collaboration plane."""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

import yaml

from . import PROTOCOL_VERSION, __version__
from .adapters.buzz import BuzzAdapter
from .adapters.github import GitHubAdapter
from .adapters.runtime import RuntimeInventory
from .config import (
    DEFAULTS,
    config_path,
    inventory_path,
    load_config,
    state_path,
    write_config,
    write_default_config,
)
from .keychain import KeyringVault, vault_status
from .models import ContractError
from .orchestrator import Orchestrator, issue_contract, load_task_contract
from .redaction import assert_non_secret
from .services import (
    install_services,
    service_action,
    service_dependencies,
    service_logs,
    uninstall_services,
)
from .state import StateStore
from .state_machine import STATES


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


def doctor(
    root: Path,
    config: dict[str, Any],
    store: StateStore | None = None,
) -> dict[str, Any]:
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
    runtime_inventory = RuntimeInventory()
    runtime_capabilities = runtime_inventory.detect()
    runtime = [asdict(item) for item in runtime_capabilities]
    runtime_by_name = {item.name: item for item in runtime_capabilities}
    agents = store.agents() if store else []
    agents_by_role = {item["role"]: item for item in agents}
    owner_keys = config["buzz"].get("owner_public_keys", [])
    owner_allowlist_valid = bool(owner_keys) and all(
        re.fullmatch(r"[0-9a-f]{64}", str(value)) for value in owner_keys
    )
    role_readiness = {}
    for role_name, settings in config["roles"].items():
        agent = agents_by_role.get(role_name)
        capability = runtime_by_name.get(settings["runtime"])
        profile = runtime_inventory.ensure_profile(
            runtime=settings["runtime"],
            profile=settings["profile"],
            description=f"Atlas development-only {role_name} profile.",
            apply=False,
        )
        binding = {
            "task_id": settings.get("task_id", ""),
            "branch": settings.get("branch", ""),
            "worktree": settings.get("worktree", ""),
            "valid": False,
            "detail": "role is not bound to a task branch/worktree",
        }
        if store and binding["task_id"] and binding["branch"] and binding["worktree"]:
            task = store.task(str(binding["task_id"]))
            if task is None:
                binding["detail"] = "bound task is missing from durable state"
            elif not task.get("buzz_channel_id"):
                binding["detail"] = "bound task has no live Buzz channel"
            else:
                try:
                    worktree_health = github.worktree_health(
                        branch=str(binding["branch"]),
                        worktree=Path(str(binding["worktree"])),
                        base_ref=task["base_sha"],
                    )
                    identity = github.worktree_identity(Path(str(binding["worktree"])))
                    expected_identity = {
                        "name": settings["git_name"],
                        "email": settings["git_email"],
                    }
                    identity_matches = identity == expected_identity
                    binding["worktree_health"] = asdict(worktree_health)
                    binding["git_identity"] = identity
                    binding["git_identity_matches"] = identity_matches
                    binding["valid"] = worktree_health.valid and identity_matches
                    binding["detail"] = (
                        "ready"
                        if binding["valid"]
                        else "worktree-specific Git identity does not match role"
                        if worktree_health.valid
                        else "; ".join(worktree_health.problems)
                    )
                except (RuntimeError, ValueError) as exc:
                    binding["detail"] = str(exc)
        public_identity_matches = bool(
            agent
            and settings.get("public_key")
            and agent["public_key"] == settings["public_key"]
        )
        ready = bool(
            role_credentials.get(role_name)
            and public_identity_matches
            and capability
            and capability.installed
            and capability.authenticated
            and profile["status"] == "ready"
            and binding["valid"]
        )
        role_readiness[role_name] = {
            "ready": ready,
            "enrolled": agent is not None,
            "public_identity_matches": public_identity_matches,
            "runtime": asdict(capability) if capability else None,
            "profile": profile,
            "binding": binding,
        }
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
            "status": vault_status(),
            "role_credentials": role_credentials,
        },
        "roles": role_readiness,
        "author_gate": {
            "owner_allowlist_configured": bool(owner_keys),
            "owner_allowlist_valid": owner_allowlist_valid,
            "worker_to_worker_direct_wake": False,
            "worker_requests_route_via_coordinator": True,
        },
        "services": dependencies,
        "boundaries": {
            "protocol_version": PROTOCOL_VERSION,
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
        and owner_allowlist_valid
        and all(item["ready"] for item in role_readiness.values())
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
    bind = agents_sub.add_parser("bind")
    bind.add_argument("--role", required=True)
    bind.add_argument("--task", required=True)
    bind.add_argument("--branch", required=True)
    bind.add_argument("--worktree", type=Path, required=True)
    bind.add_argument("--session-id")
    bind.add_argument("--apply", action="store_true")

    services = sub.add_parser("services")
    services_sub = services.add_subparsers(dest="services_command", required=True)
    install = services_sub.add_parser("install")
    install.add_argument("--apply", action="store_true")
    for name in ("start", "stop", "restart"):
        action = services_sub.add_parser(name)
        action.add_argument("--apply", action="store_true")
    services_sub.add_parser("status")
    services_sub.add_parser("logs")
    uninstall = services_sub.add_parser("uninstall")
    uninstall.add_argument("--apply", action="store_true")

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
    transition = task_sub.add_parser("transition")
    transition.add_argument("task_id")
    transition.add_argument("--to", choices=sorted(STATES), required=True)
    transition.add_argument("--apply", action="store_true")
    replay = task_sub.add_parser("replay")
    replay.add_argument("task_id")
    handoff = task_sub.add_parser("handoff")
    handoff.add_argument("task_id")
    handoff.add_argument("--branch", required=True)
    handoff.add_argument("--base")
    handoff.add_argument("--title", required=True)
    handoff.add_argument("--body-file", type=Path, required=True)
    handoff.add_argument("--apply", action="store_true")

    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--task", required=True)
    mode = cleanup.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser


def _is_non_applying(args: argparse.Namespace) -> bool:
    if args.command == "doctor":
        return True
    if args.command == "bootstrap":
        return args.dry_run
    if args.command == "agents":
        return args.agents_command == "enroll" and not args.apply
    if args.command == "services":
        return args.services_command == "install" and not args.apply
    if args.command == "task":
        return (
            args.task_command == "create"
            and not args.apply
            or (args.task_command in {"pause", "resume", "cancel"} and not args.apply)
        )
    if args.command == "cleanup":
        return args.dry_run
    return False


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = _repo_root()
    if args.command == "bootstrap" and args.apply:
        write_default_config(config_path(), DEFAULTS)
    config = load_config()
    read_only_without_state = _is_non_applying(args) and not state_path().exists()
    selected_state = Path(":memory:") if read_only_without_state else state_path()
    with StateStore(selected_state) as store:
        if args.command == "doctor":
            _json(doctor(root, config, store))
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
            if args.agents_command == "bind":
                task = store.task(args.task)
                if task is None:
                    raise KeyError(args.task)
                if args.role not in config["roles"]:
                    raise ValueError(f"unknown configured role: {args.role}")
                role_settings = config["roles"][args.role]
                agent = next(
                    (item for item in store.agents() if item["role"] == args.role),
                    None,
                )
                if agent is None:
                    raise RuntimeError(
                        f"role {args.role} must be enrolled before binding"
                    )
                worktree = args.worktree.expanduser().resolve()
                github = GitHubAdapter(config["repository"], root)
                health = github.validate_worktree(
                    branch=args.branch,
                    worktree=worktree,
                    base_ref=task["base_sha"],
                )
                session_id = args.session_id or f"{args.task}:{args.role}"
                identity = {
                    "name": role_settings["git_name"],
                    "email": role_settings["git_email"],
                }
                if not args.apply:
                    _json({
                        "apply": False,
                        "role": args.role,
                        "task_id": args.task,
                        "branch": args.branch,
                        "worktree": str(worktree),
                        "session_id": session_id,
                        "git_identity": identity,
                        "worktree_health": asdict(health),
                    })
                    return 0
                identity = github.configure_worktree_identity(
                    worktree=worktree,
                    name=identity["name"],
                    email=identity["email"],
                )
                store.bind_agent(
                    task_id=args.task,
                    agent_id=agent["agent_id"],
                    branch=args.branch,
                    worktree=str(worktree),
                    session_id=session_id,
                    git_name=identity["name"],
                    git_email=identity["email"],
                )
                settings = config["roles"][args.role]
                settings["task_id"] = args.task
                settings["branch"] = args.branch
                settings["worktree"] = str(worktree)
                write_config(config_path(), config)
                _orchestrator(
                    root,
                    store,
                    config,
                    with_external=False,
                ).write_inventory()
                _json({
                    "apply": True,
                    "role": args.role,
                    "task_id": args.task,
                    "branch": args.branch,
                    "worktree": str(worktree),
                    "session_id": session_id,
                    "git_identity": identity,
                    "worktree_health": asdict(health),
                })
                return 0
            if args.role not in config["roles"]:
                raise ValueError(f"unknown configured role: {args.role}")
            role_settings = config["roles"][args.role]
            if not args.apply:
                existing = next(
                    (agent for agent in store.agents() if agent["role"] == args.role),
                    None,
                )
                profile_plan = RuntimeInventory().ensure_profile(
                    runtime=role_settings["runtime"],
                    profile=role_settings["profile"],
                    description=(
                        f"Atlas development-only {args.role} profile; "
                        "no merge, deploy, or product authority."
                    ),
                    apply=False,
                )
                _json({
                    "apply": False,
                    "role": args.role,
                    "profile": profile_plan,
                    "actions": (
                        [
                            "reuse enrolled public identity and vault credential",
                            "reuse isolated runtime profile",
                            "retry relay profile publication",
                        ]
                        if existing
                        else [
                            "generate distinct secp256k1 keypair",
                            "store private key in OS credential vault",
                            "persist public identity and isolated runtime profile",
                            "attempt relay profile publication",
                        ]
                    ),
                })
                return 0
            profile_result = RuntimeInventory().ensure_profile(
                runtime=role_settings["runtime"],
                profile=role_settings["profile"],
                description=(
                    f"Atlas development-only {args.role} profile; "
                    "no merge, deploy, or product authority."
                ),
                apply=True,
            )
            vault = KeyringVault()
            orchestrator = _orchestrator(
                root,
                store,
                config,
                buzz_role=args.role,
            )
            result = orchestrator.enroll_agent(role=args.role, vault=vault)
            result["profile"] = profile_result
            _json(result)
            return 0
        if args.command == "services":
            if args.services_command == "install":
                results = install_services(apply=args.apply)
                if args.apply:
                    for item in results:
                        if "service" not in item:
                            continue
                        store.record_service(
                            name=item["service"],
                            manager="launchd",
                            definition_path=item["path"],
                            fingerprint=item["fingerprint"],
                            status="installed",
                        )
                _json(results)
            elif args.services_command == "logs":
                _json(service_logs())
            elif args.services_command == "status":
                _json(service_action("status"))
            elif args.services_command == "uninstall":
                results = uninstall_services(apply=args.apply)
                if args.apply:
                    for item in results:
                        try:
                            store.update_service_status(
                                item["service"],
                                "archived",
                            )
                        except KeyError:
                            pass
                _json(results)
            elif not args.apply:
                _json({
                    "apply": False,
                    "action": args.services_command,
                    "services": [f"io.atlas.collab.{role}" for role in config["roles"]],
                })
            else:
                if args.services_command in {"start", "restart"}:
                    readiness = doctor(root, config, store)
                    if not readiness["ready"]:
                        raise RuntimeError(
                            "doctor is not ready; services will not start until "
                            "identities, author gates, profiles, task channels, "
                            "and worktree bindings are verified"
                        )
                if args.services_command == "restart":
                    stopped = {item["service"]: item for item in service_action("stop")}
                    started = service_action("start")
                    results = [
                        {
                            **item,
                            "stopped": stopped[item["service"]].get("stopped", False),
                        }
                        for item in started
                    ]
                else:
                    results = service_action(args.services_command)
                if args.services_command in {"start", "stop", "restart"}:
                    for item in results:
                        status = (
                            "running"
                            if item.get("started")
                            else "stopped"
                            if item.get("stopped")
                            else "error"
                        )
                        try:
                            store.update_service_status(
                                item["service"],
                                status,
                            )
                        except KeyError:
                            pass
                _json(results)
            if getattr(args, "apply", False):
                _orchestrator(
                    root,
                    store,
                    config,
                    with_external=False,
                ).write_inventory()
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
            if args.task_command == "handoff":
                task = store.task(args.task_id)
                if task is None:
                    raise KeyError(args.task_id)
                body = args.body_file.read_text(encoding="utf-8")
                assert_non_secret({"title": args.title, "body": body})
                base = args.base or config["base_ref"]
                if not args.apply:
                    _json({
                        "apply": False,
                        "task_id": args.task_id,
                        "branch": args.branch,
                        "base": base,
                        "actions": [
                            "create or update exactly one draft pull request",
                            "retain the human-controlled review and merge gate",
                            "persist the pull request reference for recovery",
                        ],
                    })
                    return 0
                github = GitHubAdapter(config["repository"], root)
                pull, created = github.ensure_draft_pr(
                    store,
                    branch=args.branch,
                    base=base,
                    title=args.title,
                    body=body,
                )
                store.update_task_refs(args.task_id, github_pr=int(pull["number"]))
                _json({
                    "apply": True,
                    "created": created,
                    "task_id": args.task_id,
                    "pull_request": pull,
                })
                return 0
            if args.task_command == "transition":
                if not args.apply:
                    _json({
                        "apply": False,
                        "task_id": args.task_id,
                        "target": args.to,
                    })
                    return 0
                transition = orchestrator.transition_task(
                    args.task_id,
                    args.to,
                )
                _json({
                    "apply": True,
                    "transition": transition,
                })
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
                transition = orchestrator.transition_task(args.task_id, target)
                stopped_processes = []
                released_claims = 0
                if args.task_command == "cancel":
                    from .claims import release_claims
                    from .process_control import TaskProcessController

                    stopped_processes = TaskProcessController(store).cancel_task(
                        args.task_id
                    )
                    released_claims = release_claims(store, args.task_id)
                _json({
                    "apply": True,
                    "changed": transition["changed"],
                    "stopped_processes": stopped_processes,
                    "released_claims": released_claims,
                    "transition": transition,
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
                        "ensure requested role channel memberships",
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
                from .process_control import TaskProcessController

                stopped = TaskProcessController(store).cancel_task(args.task)
                released = release_claims(store, args.task)
                _json({
                    "apply": True,
                    "stopped_processes": stopped,
                    "released_claims": released,
                    "plan": plan,
                })
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
