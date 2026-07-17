"""``hermes model`` subcommand parser.

Extracted verbatim from ``hermes_cli/main.py:main()`` (god-file Phase 2).
Handler injected to avoid importing ``main``.
"""

from __future__ import annotations

from typing import Callable


def build_model_parser(subparsers, *, cmd_model: Callable) -> None:
    """Attach the ``model`` subcommand to ``subparsers``."""
    # =========================================================================
    # model command
    # =========================================================================
    model_parser = subparsers.add_parser(
        "model",
        help="Select default model and provider",
        description="Interactively select your inference provider and default model",
    )
    model_parser.add_argument(
        "--refresh",
        action="store_true",
        help="Wipe the model picker disk cache and re-fetch every provider's live /v1/models list.",
    )
    # Nous-login flags: parseable everywhere for compatibility, but hidden
    # from help on Atlas builds (no Nous Portal path).
    from argparse import SUPPRESS

    from hermes_cli.brand import is_atlas_branded

    _atlas = is_atlas_branded()
    model_parser.add_argument(
        "--portal-url",
        help=SUPPRESS if _atlas else "Portal base URL for Nous login (default: production portal)",
    )
    model_parser.add_argument(
        "--inference-url",
        help=SUPPRESS if _atlas else "Inference API base URL for Nous login (default: production inference API)",
    )
    model_parser.add_argument(
        "--client-id",
        default=None,
        help=SUPPRESS if _atlas else "OAuth client id to use for Nous login (default: hermes-cli)",
    )
    model_parser.add_argument(
        "--scope",
        default=None,
        help=SUPPRESS if _atlas else "OAuth scope to request for Nous login",
    )
    model_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not attempt to open the browser automatically during OAuth login"
        if _atlas
        else "Do not attempt to open the browser automatically during Nous login",
    )
    model_parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP request timeout in seconds for OAuth login (default: 15)"
        if _atlas
        else "HTTP request timeout in seconds for Nous login (default: 15)",
    )
    model_parser.add_argument(
        "--ca-bundle",
        help="Path to CA bundle PEM file for TLS verification"
        if _atlas
        else "Path to CA bundle PEM file for Nous TLS verification",
    )
    model_parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification for OAuth login (testing only)"
        if _atlas
        else "Disable TLS verification for Nous login (testing only)",
    )
    model_parser.set_defaults(func=cmd_model)
