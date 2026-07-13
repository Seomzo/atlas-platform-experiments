"""Public product branding for the Atlas distribution.

The engine keeps its upstream Python module and environment-variable names so
upstream updates remain practical.  The public ``atlas`` entry point and the
desktop app opt into Atlas naming without adding a wrapper process.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path


_ATLAS_ENV = "HERMES_PUBLIC_BRAND"


def is_atlas_branded() -> bool:
    """Return whether this process should present the Atlas product brand."""

    selected = os.environ.get(_ATLAS_ENV, "").strip().lower()
    if selected:
        return selected == "atlas"
    return Path(sys.argv[0]).stem.lower() == "atlas"


def configure_runtime_brand() -> None:
    """Apply Atlas runtime defaults before the engine loads configuration."""

    if not is_atlas_branded():
        return
    os.environ[_ATLAS_ENV] = "atlas"
    os.environ.setdefault("HERMES_HOME", str(Path.home() / ".atlas"))
    os.environ.setdefault("HERMES_BIN", "atlas")


def product_name() -> str:
    return "Atlas" if is_atlas_branded() else "Hermes Agent"


def command_name() -> str:
    return "atlas" if is_atlas_branded() else "hermes"


def brand_text(text: str) -> str:
    """Translate customer-facing upstream copy while leaving internals intact."""

    if not is_atlas_branded() or not isinstance(text, str):
        return text

    branded = text.replace("a Hermes", "an Atlas")
    branded = branded.replace("Hermes Desktop", "Atlas Desktop")
    branded = branded.replace("Hermes Agent", "Atlas")
    branded = branded.replace("Hermes", "Atlas")
    branded = branded.replace("~/.hermes", "~/.atlas")
    branded = branded.replace("$HOME/.hermes", "$HOME/.atlas")
    # Rewrite command examples, but not package names, URLs, filenames, or
    # internal identifiers such as hermes-agent and hermes_cli.
    branded = re.sub(r"(?<![\w./-])hermes(?=(?:\s|$|[`',:;)]))", "atlas", branded)
    return branded


def brand_argparse(parser: object) -> None:
    """Apply public branding to a completed argparse parser tree in place."""

    if not is_atlas_branded():
        return

    seen: set[int] = set()

    def visit(current: object) -> None:
        identity = id(current)
        if identity in seen:
            return
        seen.add(identity)

        for attribute in ("prog", "description", "epilog", "usage"):
            value = getattr(current, attribute, None)
            if isinstance(value, str):
                setattr(current, attribute, brand_text(value))

        for action in getattr(current, "_actions", ()):
            help_text = getattr(action, "help", None)
            if isinstance(help_text, str):
                action.help = brand_text(help_text)
            for choice_action in getattr(action, "_choices_actions", ()):
                choice_help = getattr(choice_action, "help", None)
                if isinstance(choice_help, str):
                    choice_action.help = brand_text(choice_help)
            choices = getattr(action, "choices", None)
            children = choices.values() if isinstance(choices, dict) else ()
            for child in children:
                visit(child)

    visit(parser)
