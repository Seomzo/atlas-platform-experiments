"""Exec the configured ACP runtime after removing Buzz identity credentials."""

from __future__ import annotations

import json
import os
import sys


_STRIPPED_ENV = frozenset({
    "BUZZ_PRIVATE_KEY",
    "BUZZ_AUTH_TAG",
    "ATLAS_COLLAB_VAULT_VALUE",
})


def sanitized_runtime_env(source: dict[str, str]) -> dict[str, str]:
    return {key: value for key, value in source.items() if key not in _STRIPPED_ENV}


def runtime_command(source: dict[str, str]) -> list[str]:
    command = str(source.get("ATLAS_COLLAB_RUNTIME_COMMAND") or "")
    raw_args = str(source.get("ATLAS_COLLAB_RUNTIME_ARGS_JSON") or "[]")
    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError as exc:
        raise RuntimeError("runtime args are not valid JSON") from exc
    if (
        not command
        or not isinstance(args, list)
        or not all(isinstance(item, str) for item in args)
    ):
        raise RuntimeError("runtime command/args are invalid")
    return [command, *args]


def main() -> int:
    command = runtime_command(os.environ)
    env = sanitized_runtime_env(dict(os.environ))
    os.execvpe(command[0], command, env)
    return 1


if __name__ == "__main__":
    sys.exit(main())
