"""``atlas cortex`` administration parser."""

from __future__ import annotations

from typing import Callable


def build_cortex_parser(subparsers, *, cmd_cortex: Callable) -> None:
    parser = subparsers.add_parser(
        "cortex",
        help="Inspect and administer Atlas Cortex",
        description=(
            "Atlas Cortex is the native personal memory, lifecycle, and "
            "knowledge-graph subsystem. Normal capture and recall are automatic."
        ),
    )
    commands = parser.add_subparsers(dest="cortex_command")
    commands.add_parser("status", help="Show privacy-safe health and index status")

    dream = commands.add_parser(
        "dream",
        help="Queue or process deterministic recovery maintenance",
    )
    dream.add_argument(
        "--run-now",
        action="store_true",
        help="Process one queued maintenance job in this process",
    )

    index = commands.add_parser("index", help="Manage versioned GraphRAG artifacts")
    index_commands = index.add_subparsers(dest="cortex_index_command")
    index_commands.add_parser("list", help="List staged, active, and retained indexes")
    import_parser = index_commands.add_parser(
        "import", help="Validate and stage an artifact under the configured index root"
    )
    import_parser.add_argument("path", help="Artifact directory or relative path")
    import_parser.add_argument(
        "--publish", action="store_true", help="Publish atomically after staging"
    )
    publish = index_commands.add_parser("publish", help="Publish a staged version")
    publish.add_argument("version")
    rollback = index_commands.add_parser("rollback", help="Restore a retained version")
    rollback.add_argument("version")
    parser.set_defaults(func=cmd_cortex)


__all__ = ["build_cortex_parser"]
