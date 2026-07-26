from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hermes_cli import brand


def _select_atlas(monkeypatch) -> None:
    # Record an explicit pre-state before deleting these variables so direct
    # writes made by configure_runtime_brand() are reliably undone afterward.
    for key in ("HERMES_PUBLIC_BRAND", "HERMES_HOME", "HERMES_BIN"):
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/atlas"])


def test_atlas_entrypoint_configures_direct_engine_defaults(monkeypatch) -> None:
    _select_atlas(monkeypatch)

    brand.configure_runtime_brand()

    assert brand.is_atlas_branded()
    assert brand.product_name() == "Atlas"
    assert brand.command_name() == "atlas"
    assert brand.os.environ["HERMES_HOME"] == str(Path.home() / ".atlas")
    assert brand.os.environ["HERMES_BIN"] == "atlas"


def test_atlas_public_copy_does_not_rewrite_upstream_identifiers(monkeypatch) -> None:
    _select_atlas(monkeypatch)

    rendered = brand.brand_text(
        "Hermes Agent: run `hermes setup`; config: ~/.hermes/config.yaml; "
        "source: https://github.com/NousResearch/hermes-agent and hermes_cli"
    )

    assert rendered.startswith("Atlas: run `atlas setup`; config: ~/.atlas/config.yaml")
    assert "NousResearch/hermes-agent" in rendered
    assert "hermes_cli" in rendered


def test_hermes_compatibility_entrypoint_keeps_upstream_defaults(monkeypatch) -> None:
    monkeypatch.delenv("HERMES_PUBLIC_BRAND", raising=False)
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/hermes"])

    assert not brand.is_atlas_branded()
    assert brand.product_name() == "Hermes Agent"
    assert brand.command_name() == "hermes"


def test_atlas_setup_banner_uses_blue_wordmark(monkeypatch, capsys) -> None:
    _select_atlas(monkeypatch)
    brand.configure_runtime_brand()

    from hermes_cli import colors, setup

    monkeypatch.setattr(colors, "should_use_color", lambda: True)
    setup._print_setup_wizard_banner()

    rendered = capsys.readouterr().out
    assert "███████╗" in rendered
    assert "Atlas Setup Wizard" in rendered
    assert "DEALERSHIP AI • RUNNING LOCALLY" in rendered
    assert colors.Colors.ATLAS_CYAN in rendered
    assert "Hermes" not in rendered


def test_quick_setup_cancel_stops_before_terminal_and_summary(
    tmp_path, monkeypatch, capsys
) -> None:
    _select_atlas(monkeypatch)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    brand.configure_runtime_brand()

    from hermes_cli import main, setup
    from hermes_cli.config import load_config

    def cancelled_login(*_args, **_kwargs):
        raise SystemExit(130)

    monkeypatch.setattr(main, "_model_flow_nous", cancelled_login)
    monkeypatch.setattr(
        setup,
        "setup_terminal_backend",
        lambda *_args, **_kwargs: pytest.fail("terminal setup ran after cancellation"),
    )
    monkeypatch.setattr(
        setup,
        "_print_setup_summary",
        lambda *_args, **_kwargs: pytest.fail(
            "completion summary ran after cancellation"
        ),
    )

    completed = setup._run_first_time_quick_setup(
        load_config(), tmp_path, is_existing=False
    )

    assert completed is False
    rendered = capsys.readouterr().out
    assert "Nous Portal setup cancelled" in rendered
    assert "stopped before terminal or messaging" in rendered
    assert "Setup complete" not in rendered
