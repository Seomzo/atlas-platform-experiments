"""Atlas control-plane command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from altas import __version__


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="atlas-control",
        description="Atlas managed AI worker control plane",
    )
    parser.add_argument("--version", action="version", version=f"Atlas {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="Start the local Control Plane")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8787)
    serve.add_argument("--reload", action="store_true")

    worker = subcommands.add_parser("worker", help="Run the managed local worker")
    mode = worker.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Run one heartbeat/job cycle")
    mode.add_argument("--watch", action="store_true", help="Poll continuously")

    subcommands.add_parser("doctor", help="Validate local prototype configuration")
    return parser


def _serve(args: argparse.Namespace) -> int:
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("the prototype Control Plane may only bind to a loopback host")

    import uvicorn

    uvicorn.run(
        "altas.control_plane.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )
    return 0


def _worker(args: argparse.Namespace) -> int:
    from altas.managed.worker import AltasWorker, WorkerSettings

    worker = AltasWorker(WorkerSettings.from_env())
    if args.watch:
        worker.run_forever()
        return 0
    try:
        print(worker.run_once().to_json())
    finally:
        worker.close()
    return 0


def _doctor() -> int:
    from altas.control_plane.config import ControlPlaneSettings
    from altas.managed.worker import WorkerSettings

    checks: dict[str, str] = {}
    try:
        settings = ControlPlaneSettings.from_env()
        checks["control_plane"] = "configured"
        checks["database_parent"] = (
            "ready" if settings.database_path.parent.exists() else "created_on_start"
        )
    except Exception as exc:
        checks["control_plane"] = f"invalid:{type(exc).__name__}"
    try:
        WorkerSettings.from_env()
        checks["worker"] = "configured"
    except Exception:
        checks["worker"] = "not_configured"
    checks["fixture"] = (
        "ready"
        if (
            Path(__file__).parent
            / "fixed_ops"
            / "fixtures"
            / "tekion_service_snapshot.json"
        ).exists()
        else "missing"
    )
    print(json.dumps(checks, indent=2, sort_keys=True))
    return 0 if checks.get("control_plane") == "configured" else 1


def main(argv: list[str] | None = None) -> None:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(raw_argv)
    handlers = {
        "serve": lambda: _serve(args),
        "worker": lambda: _worker(args),
        "doctor": _doctor,
    }
    try:
        code = handlers[args.command]()
    except KeyboardInterrupt:
        code = 130
    except Exception as exc:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error": type(exc).__name__,
                    "message": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        code = 1
    raise SystemExit(code)
