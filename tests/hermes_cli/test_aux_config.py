"""Tests for the auxiliary-model configuration UI in ``hermes model``.

Covers the helper functions:
  - ``_save_aux_choice`` writes to config.yaml without touching main model config
  - ``_reset_aux_to_auto`` clears routing fields but preserves timeouts
  - ``_format_aux_current`` renders current task config for the menu
  - ``_AUX_TASKS`` stays in sync with ``DEFAULT_CONFIG["auxiliary"]``

These are pure-function tests — the interactive menu loops are not covered
here (they're stdin-driven curses prompts).
"""

from __future__ import annotations

import pytest

from hermes_cli.config import DEFAULT_CONFIG, load_config
from hermes_cli.main import (
    _AUX_TASKS,
    _CORTEX_AUX_TASKS,
    _aux_select_for_task,
    _cortex_memory_catalog_choices,
    _format_aux_current,
    _reset_aux_to_auto,
    _save_aux_choice,
    _save_cortex_memory_choice,
)


# ── Default config ──────────────────────────────────────────────────────────


def test_title_generation_present_in_default_config():
    """`title_generation` task must be defined in DEFAULT_CONFIG.

    Regression for an existing gap: title_generator.py calls
    ``call_llm(task="title_generation", ...)`` but the task was missing
    from DEFAULT_CONFIG["auxiliary"], so the config-backed timeout/provider
    overrides never worked for that task.
    """
    assert "title_generation" in DEFAULT_CONFIG["auxiliary"]
    tg = DEFAULT_CONFIG["auxiliary"]["title_generation"]
    assert tg["provider"] == "auto"
    assert tg["model"] == ""
    assert tg["timeout"] > 0
    assert tg["extra_body"] == {}


def test_session_search_no_longer_appears_in_auxiliary_model_config():
    """session_search is a direct DB-backed tool, not an auxiliary LLM task."""
    assert "session_search" not in DEFAULT_CONFIG["auxiliary"]
    assert "session_search" not in {key for key, _name, _desc in _AUX_TASKS}


def test_aux_tasks_keys_all_exist_in_default_config():
    """Every task the menu offers must be defined in DEFAULT_CONFIG."""
    aux_keys = {k for k, _name, _desc in _AUX_TASKS}
    default_keys = set(DEFAULT_CONFIG["auxiliary"].keys())
    missing = aux_keys - default_keys
    assert not missing, (
        f"_AUX_TASKS references tasks not in DEFAULT_CONFIG.auxiliary: {missing}"
    )


# ── _format_aux_current ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "task_cfg,expected",
    [
        ({}, "auto"),
        ({"provider": "", "model": ""}, "auto"),
        ({"provider": "auto", "model": ""}, "auto"),
        ({"provider": "auto", "model": "gpt-4o"}, "auto · gpt-4o"),
        ({"provider": "openrouter", "model": ""}, "openrouter"),
        (
            {"provider": "openrouter", "model": "google/gemini-2.5-flash"},
            "openrouter · google/gemini-2.5-flash",
        ),
        ({"provider": "nous", "model": "gemini-3-flash"}, "nous · gemini-3-flash"),
        (
            {"provider": "custom", "base_url": "http://localhost:11434/v1", "model": ""},
            "custom (localhost:11434/v1)",
        ),
        (
            {
                "provider": "custom",
                "base_url": "http://localhost:11434/v1/",
                "model": "qwen2.5:32b",
            },
            "custom (localhost:11434/v1) · qwen2.5:32b",
        ),
    ],
)
def test_format_aux_current(task_cfg, expected):
    assert _format_aux_current(task_cfg) == expected


def test_format_aux_current_handles_non_dict():
    assert _format_aux_current(None) == "auto"
    assert _format_aux_current("string") == "auto"


# ── _save_aux_choice ────────────────────────────────────────────────────────


def test_save_aux_choice_persists_to_config_yaml(tmp_path, monkeypatch):
    """Saving a task writes provider/model/base_url/api_key to auxiliary.<task>."""
    from pathlib import Path
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".hermes").mkdir(exist_ok=True)

    _save_aux_choice(
        "vision", provider="openrouter", model="google/gemini-2.5-flash",
    )
    cfg = load_config()
    v = cfg["auxiliary"]["vision"]
    assert v["provider"] == "openrouter"
    assert v["model"] == "google/gemini-2.5-flash"
    assert v["base_url"] == ""
    assert v["api_key"] == ""


def test_save_aux_choice_preserves_timeout(tmp_path, monkeypatch):
    """Saving must NOT clobber user-tuned timeout values."""
    from pathlib import Path
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".hermes").mkdir(exist_ok=True)

    # Default vision timeout is 120
    cfg_before = load_config()
    default_timeout = cfg_before["auxiliary"]["vision"]["timeout"]
    assert default_timeout == 120

    _save_aux_choice("vision", provider="nous", model="gemini-3-flash")
    cfg_after = load_config()
    assert cfg_after["auxiliary"]["vision"]["timeout"] == default_timeout
    # download_timeout also preserved for vision
    assert cfg_after["auxiliary"]["vision"].get("download_timeout") == 30


def test_save_aux_choice_does_not_touch_main_model(tmp_path, monkeypatch):
    """Aux config must never mutate model.default / model.provider / model.base_url."""
    from pathlib import Path
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".hermes").mkdir(exist_ok=True)

    # Simulate a configured main model
    from hermes_cli.config import save_config

    cfg = load_config()
    cfg["model"] = {
        "default": "claude-sonnet-4.6",
        "provider": "anthropic",
        "base_url": "",
    }
    save_config(cfg)

    _save_aux_choice(
        "compression", provider="custom",
        base_url="http://localhost:11434/v1", model="qwen2.5:32b",
    )

    cfg = load_config()
    # Main model untouched
    assert cfg["model"]["default"] == "claude-sonnet-4.6"
    assert cfg["model"]["provider"] == "anthropic"
    # Aux saved correctly
    c = cfg["auxiliary"]["compression"]
    assert c["provider"] == "custom"
    assert c["model"] == "qwen2.5:32b"
    assert c["base_url"] == "http://localhost:11434/v1"


def test_save_aux_choice_creates_missing_task_entry(tmp_path, monkeypatch):
    """Saving a task that was wiped from config.yaml should recreate it."""
    from pathlib import Path
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".hermes").mkdir(exist_ok=True)

    # Remove vision from config entirely
    from hermes_cli.config import save_config

    cfg = load_config()
    cfg.setdefault("auxiliary", {}).pop("vision", None)
    save_config(cfg)

    _save_aux_choice("vision", provider="nous", model="gemini-3-flash")
    cfg = load_config()
    assert cfg["auxiliary"]["vision"]["provider"] == "nous"
    assert cfg["auxiliary"]["vision"]["model"] == "gemini-3-flash"


# ── _reset_aux_to_auto ──────────────────────────────────────────────────────


def test_reset_aux_to_auto_clears_routing_preserves_timeouts(tmp_path, monkeypatch):
    from pathlib import Path
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".hermes").mkdir(exist_ok=True)

    # Configure two tasks non-auto, and bump a timeout
    _save_aux_choice("vision", provider="openrouter", model="gpt-4o")
    _save_aux_choice("compression", provider="nous", model="gemini-3-flash")
    from hermes_cli.config import save_config

    cfg = load_config()
    cfg["auxiliary"]["vision"]["timeout"] = 300  # user-tuned
    save_config(cfg)

    n = _reset_aux_to_auto()
    assert n == 2  # both changed

    cfg = load_config()
    for task in ("vision", "compression"):
        v = cfg["auxiliary"][task]
        assert v["provider"] == "auto"
        assert v["model"] == ""
        assert v["base_url"] == ""
        assert v["api_key"] == ""
    # User-tuned timeout survives reset
    assert cfg["auxiliary"]["vision"]["timeout"] == 300
    # Default compression timeout preserved
    assert cfg["auxiliary"]["compression"]["timeout"] == 120


def test_reset_aux_to_auto_idempotent(tmp_path, monkeypatch):
    """Second reset on already-auto config returns 0 without errors."""
    from pathlib import Path
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".hermes").mkdir(exist_ok=True)

    assert _reset_aux_to_auto() == 0
    _save_aux_choice("vision", provider="nous", model="gemini-3-flash")
    assert _reset_aux_to_auto() == 1
    assert _reset_aux_to_auto() == 0


def test_reset_aux_to_auto_preserves_dedicated_cortex_routes(tmp_path, monkeypatch):
    """Generic reset must not disable session-end Cortex consolidation."""
    from pathlib import Path

    from hermes_cli.config import save_config

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".hermes").mkdir(exist_ok=True)

    cfg = load_config()
    for index, task in enumerate(_CORTEX_AUX_TASKS):
        cfg["auxiliary"][task].update(
            {
                "provider": "openrouter",
                "model": f"google/cortex-memory-{index}",
                "base_url": "https://memory.example.test/v1",
                "api_key": "private-memory-key",
                "timeout": 321 + index,
            }
        )
    cfg["auxiliary"]["vision"].update(
        {"provider": "openrouter", "model": "google/gemini-2.5-flash"}
    )
    save_config(cfg)

    assert _reset_aux_to_auto() == 1

    saved = load_config()
    assert saved["auxiliary"]["vision"]["provider"] == "auto"
    assert saved["auxiliary"]["vision"]["model"] == ""
    for index, task in enumerate(_CORTEX_AUX_TASKS):
        slot = saved["auxiliary"][task]
        assert slot["provider"] == "openrouter"
        assert slot["model"] == f"google/cortex-memory-{index}"
        assert slot["base_url"] == "https://memory.example.test/v1"
        assert slot["api_key"] == "private-memory-key"
        assert slot["timeout"] == 321 + index


def _cortex_catalog(
    *,
    authenticated: bool = True,
    selectable: bool = True,
    structured_json: bool = True,
) -> dict:
    model = "google/gemini-3.1-flash-lite"
    return {
        "providers": [
            {
                "slug": "openrouter",
                "name": "OpenRouter",
                "authenticated": authenticated,
                "models": [model],
                "memory_capabilities": {
                    model: {
                        "selectable": selectable,
                        "structured_json": structured_json,
                    }
                },
            }
        ]
    }


def test_save_cortex_memory_choice_atomically_updates_both_strict_slots(
    tmp_path, monkeypatch
):
    """One validated choice updates both passes once and clears hidden routes."""
    import copy
    from pathlib import Path

    from hermes_cli import config as config_mod
    from hermes_cli import main as main_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".hermes").mkdir(exist_ok=True)

    cfg = load_config()
    cfg["model"] = {
        "provider": "anthropic",
        "default": "claude-sonnet-4.6",
        "base_url": "",
    }
    cfg.setdefault("cortex", {}).setdefault("security", {})[
        "approved_model_providers"
    ] = ["openrouter"]
    for index, task in enumerate(_CORTEX_AUX_TASKS):
        cfg["auxiliary"][task].update(
            {
                "provider": "custom",
                "model": "old-model",
                "base_url": "https://hidden.example.test/v1",
                "api_key": "hidden-key",
                "api": "legacy-key",
                "api_mode": "legacy",
                "fallback_chain": ["main"],
                "timeout": 456 + index,
                "extra_body": {"preserve": True},
            }
        )
    config_mod.save_config(cfg)

    monkeypatch.setattr(
        main_mod,
        "_build_cortex_memory_catalog",
        lambda _cfg: _cortex_catalog(),
    )
    real_save_config = config_mod.save_config
    writes: list[dict] = []

    def tracking_save_config(value, *args, **kwargs):
        writes.append(copy.deepcopy(value))
        return real_save_config(value, *args, **kwargs)

    monkeypatch.setattr(config_mod, "save_config", tracking_save_config)

    result = _save_cortex_memory_choice(
        provider="openrouter",
        model="google/gemini-3.1-flash-lite",
    )

    assert result == ("openrouter", "google/gemini-3.1-flash-lite")
    assert len(writes) == 1
    saved = writes[0]
    assert saved["model"]["provider"] == "anthropic"
    assert saved["model"]["default"] == "claude-sonnet-4.6"
    for index, task in enumerate(_CORTEX_AUX_TASKS):
        slot = saved["auxiliary"][task]
        assert slot["provider"] == "openrouter"
        assert slot["model"] == "google/gemini-3.1-flash-lite"
        assert slot["fallback_chain"] == []
        assert slot["timeout"] == 456 + index
        assert slot["extra_body"] == {"preserve": True}
        for hidden_field in ("base_url", "api_key", "api", "api_mode"):
            assert hidden_field not in slot


@pytest.mark.parametrize(
    ("catalog", "model", "expected_error"),
    [
        (
            _cortex_catalog(authenticated=False),
            "google/gemini-3.1-flash-lite",
            "not authenticated",
        ),
        (
            _cortex_catalog(),
            "unreviewed/arbitrary-model",
            "not selectable",
        ),
        (
            _cortex_catalog(structured_json=False),
            "google/gemini-3.1-flash-lite",
            "not verified for structured",
        ),
    ],
)
def test_save_cortex_memory_choice_rejects_unsafe_catalog_routes_without_write(
    tmp_path,
    monkeypatch,
    catalog,
    model,
    expected_error,
):
    from pathlib import Path

    from hermes_cli import config as config_mod
    from hermes_cli import main as main_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".hermes").mkdir(exist_ok=True)

    cfg = load_config()
    cfg["model"] = {"provider": "anthropic", "default": "claude-sonnet-4.6"}
    config_mod.save_config(cfg)

    monkeypatch.setattr(
        main_mod,
        "_build_cortex_memory_catalog",
        lambda _cfg: catalog,
    )
    writes: list[dict] = []
    monkeypatch.setattr(config_mod, "save_config", lambda value: writes.append(value))

    with pytest.raises(ValueError, match=expected_error):
        _save_cortex_memory_choice(provider="openrouter", model=model)

    assert writes == []


def test_cortex_catalog_choices_hide_unverified_and_unauthenticated_routes():
    """The native picker exposes no auto/custom escape hatch."""
    model = "google/gemini-3.1-flash-lite"
    catalog = _cortex_catalog()
    catalog["providers"].extend(
        [
            {
                "slug": "unauthenticated",
                "authenticated": False,
                "models": [model],
                "memory_capabilities": {
                    model: {"selectable": True, "structured_json": True}
                },
            },
            {
                "slug": "unstructured",
                "authenticated": True,
                "models": [model],
                "memory_capabilities": {
                    model: {"selectable": True, "structured_json": False}
                },
            },
        ]
    )

    assert _cortex_memory_catalog_choices(catalog) == [
        ("openrouter", model, f"OpenRouter · {model}")
    ]


def test_cortex_task_picker_uses_dedicated_both_slot_flow(monkeypatch):
    """Selecting either Cortex row never enters the generic aux picker."""
    from hermes_cli import main as main_mod

    calls: list[str] = []
    monkeypatch.setattr(
        main_mod,
        "_cortex_memory_model_flow",
        lambda: calls.append("cortex"),
    )

    for task in _CORTEX_AUX_TASKS:
        _aux_select_for_task(task)

    assert calls == ["cortex", "cortex"]


def test_generic_save_helper_routes_cortex_through_atomic_both_slot_save(
    monkeypatch,
):
    """Direct helper callers cannot bypass the dedicated Cortex write path."""
    from hermes_cli import main as main_mod

    calls: list[dict] = []
    monkeypatch.setattr(
        main_mod,
        "_save_cortex_memory_choice",
        lambda **kwargs: calls.append(kwargs),
    )

    _save_aux_choice(
        "cortex_triage",
        provider="openrouter",
        model="google/gemini-3.1-flash-lite",
    )

    assert calls == [
        {
            "provider": "openrouter",
            "model": "google/gemini-3.1-flash-lite",
        }
    ]
    with pytest.raises(ValueError, match="arbitrary task-local endpoint"):
        _save_aux_choice(
            "cortex_reasoning",
            provider="custom",
            model="local-model",
            base_url="http://localhost:11434/v1",
        )


def test_openrouter_chat_picker_rejects_existing_cortex_pair_without_write(
    tmp_path,
    monkeypatch,
    capsys,
):
    """Native ``atlas model`` enforces chat/memory separation bidirectionally."""
    from pathlib import Path

    from hermes_cli import auth as auth_mod
    from hermes_cli import main as main_mod
    from hermes_cli import models as models_mod
    from hermes_cli.config import save_config
    from hermes_cli.model_setup_flows import _model_flow_openrouter

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".hermes").mkdir(exist_ok=True)

    memory_model = "google/gemini-3.1-flash-lite"
    cfg = load_config()
    cfg["model"] = {
        "provider": "anthropic",
        "default": "claude-sonnet-4.6",
    }
    for task in _CORTEX_AUX_TASKS:
        cfg["auxiliary"][task]["provider"] = "openrouter"
        cfg["auxiliary"][task]["model"] = memory_model
    save_config(cfg)

    monkeypatch.setattr(
        main_mod,
        "_prompt_api_key",
        lambda *_args, **_kwargs: ("openrouter-key", False),
    )
    monkeypatch.setattr(
        models_mod,
        "model_ids",
        lambda **_kwargs: [memory_model],
    )
    monkeypatch.setattr(
        models_mod,
        "get_pricing_for_provider",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        auth_mod,
        "_prompt_model_selection",
        lambda *_args, **_kwargs: memory_model,
    )
    model_writes: list[str] = []
    monkeypatch.setattr(
        auth_mod,
        "_save_model_choice",
        lambda model: model_writes.append(model),
    )
    provider_changes: list[str] = []
    monkeypatch.setattr(
        auth_mod,
        "deactivate_provider",
        lambda: provider_changes.append("deactivated"),
    )

    _model_flow_openrouter(cfg, current_model="claude-sonnet-4.6")

    assert model_writes == []
    assert provider_changes == []
    saved = load_config()
    assert saved["model"]["provider"] == "anthropic"
    assert saved["model"]["default"] == "claude-sonnet-4.6"
    output = capsys.readouterr().out
    assert "Cannot set the conversational model" in output
    assert "Chat and Cortex memory must use different" in output
    assert "The conversational model was not changed" in output


# ── Menu dispatch ───────────────────────────────────────────────────────────


def test_select_provider_and_model_dispatches_to_aux_menu(tmp_path, monkeypatch):
    """Picking 'Configure auxiliary models...' in the provider list calls _aux_config_menu."""
    from pathlib import Path
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".hermes").mkdir(exist_ok=True)

    from hermes_cli import main as main_mod

    called = {"aux": 0, "flow": 0}

    def fake_prompt(choices, *, default=0):
        # Find the aux-config entry by its label text and return its index
        for i, label in enumerate(choices):
            if "Configure auxiliary models" in label:
                return i
        raise AssertionError("aux entry not in provider list")

    monkeypatch.setattr(main_mod, "_prompt_provider_choice", fake_prompt)
    monkeypatch.setattr(main_mod, "_aux_config_menu", lambda: called.__setitem__("aux", called["aux"] + 1))
    # Guard against any main flow accidentally running
    monkeypatch.setattr(main_mod, "_model_flow_openrouter",
                        lambda *a, **kw: called.__setitem__("flow", called["flow"] + 1))

    main_mod.select_provider_and_model()

    assert called["aux"] == 1, "aux menu not invoked"
    assert called["flow"] == 0, "main provider flow should not run"


def test_leave_unchanged_replaces_cancel_label(tmp_path, monkeypatch):
    """The bottom cancel entry now reads 'Leave unchanged' (UX polish)."""
    from pathlib import Path
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".hermes").mkdir(exist_ok=True)

    from hermes_cli import main as main_mod

    captured: list[list[str]] = []

    def fake_prompt(choices, *, default=0):
        captured.append(list(choices))
        # Pick 'Leave unchanged' (last item) to exit cleanly
        for i, label in enumerate(choices):
            if label == "Leave unchanged":
                return i
        raise AssertionError("Leave unchanged not in provider list")

    monkeypatch.setattr(main_mod, "_prompt_provider_choice", fake_prompt)

    main_mod.select_provider_and_model()

    assert captured, "provider menu never rendered"
    labels = captured[0]
    assert "Leave unchanged" in labels
    assert "Cancel" not in labels, "Cancel label should be replaced"
    assert any("Configure auxiliary models" in label for label in labels)
