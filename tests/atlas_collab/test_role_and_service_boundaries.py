import plistlib
import inspect

import pytest

from tools.atlas_collab.config import DEFAULTS
from tools.atlas_collab.keychain import MemoryVault
from tools.atlas_collab.orchestrator import Orchestrator
from tools.atlas_collab import runtime_shim, service_runner, services
from tools.atlas_collab.services import launch_agent_definition


class _ProfileBuzz:
    def __init__(self):
        self.profiles = []

    def set_profile(self, *, name, about):
        self.profiles.append({"name": name, "about": about})


def test_three_roles_get_distinct_public_identities_and_profiles(
    tmp_path, store, config, monkeypatch
):
    monkeypatch.setenv("ATLAS_COLLAB_HOME", str(tmp_path / "home"))
    vault = MemoryVault({})
    orchestrator = Orchestrator(
        root=tmp_path,
        store=store,
        config=config,
    )
    for role in ("coordinator", "implementer", "reviewer"):
        result = orchestrator.enroll_agent(role=role, vault=vault)
        assert result["created"]
    agents = store.agents()
    assert len({agent["public_key"] for agent in agents}) == 3
    assert len({agent["profile"] for agent in agents}) == 3
    assert len(vault.values) == 3
    inventory = (tmp_path / "home" / "inventory.json").read_text(encoding="utf-8")
    assert all(secret not in inventory for secret in vault.values.values())


def test_existing_identity_retries_relay_profile_publication(
    tmp_path, store, config, monkeypatch
):
    monkeypatch.setenv("ATLAS_COLLAB_HOME", str(tmp_path / "home"))
    vault = MemoryVault({})
    first = Orchestrator(root=tmp_path, store=store, config=config)
    enrolled = first.enroll_agent(role="coordinator", vault=vault)
    buzz = _ProfileBuzz()

    retried = Orchestrator(
        root=tmp_path,
        store=store,
        config=config,
        buzz=buzz,
    ).enroll_agent(role="coordinator", vault=vault)

    assert not retried["created"]
    assert retried["agent"]["public_key"] == enrolled["agent"]["public_key"]
    assert retried["relay_status"] == "profile-published"
    assert buzz.profiles == [
        {
            "name": "atlas-coordinator",
            "about": (
                "Atlas development-only coordinator; cannot merge, deploy, "
                "or widen permissions."
            ),
        }
    ]


def test_launch_agent_contains_no_private_key_or_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_COLLAB_HOME", str(tmp_path))
    definition = launch_agent_definition("coordinator")
    payload = plistlib.dumps(definition).decode()
    assert "private-key" not in payload.lower()
    assert "BUZZ_PRIVATE_KEY" not in payload
    assert "service_runner" in payload
    assert "/Users/" not in inspect.getsource(service_runner)


def test_launchctl_gui_domain_uses_guarded_macos_user_id(monkeypatch):
    monkeypatch.setattr(services.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(services.os, "getuid", lambda: 501, raising=False)

    assert services._gui_domain() == "gui/501"


def test_launchctl_gui_domain_fails_cleanly_without_user_id(monkeypatch):
    monkeypatch.setattr(services.platform, "system", lambda: "Darwin")
    monkeypatch.delattr(services.os, "getuid", raising=False)

    with pytest.raises(RuntimeError, match="user ID support"):
        services._gui_domain()


def test_development_config_has_no_product_or_merge_authority():
    assert DEFAULTS["routing"]["recovery_heartbeat_seconds"] == 0
    serialized = str(DEFAULTS).lower()
    assert "auto_merge" not in serialized
    assert "deploy" not in serialized


def test_live_author_gates_force_worker_requests_through_coordinator(config):
    owner = "1" * 64
    coordinator = "2" * 64
    implementer = "3" * 64
    reviewer = "4" * 64
    config["buzz"]["owner_public_keys"] = [owner]
    config["roles"]["coordinator"]["public_key"] = coordinator
    config["roles"]["implementer"]["public_key"] = implementer
    config["roles"]["reviewer"]["public_key"] = reviewer

    assert service_runner.role_author_allowlist(config, "coordinator") == [
        owner,
        implementer,
        reviewer,
    ]
    assert service_runner.role_author_allowlist(config, "implementer") == [
        owner,
        coordinator,
    ]
    assert service_runner.role_author_allowlist(config, "reviewer") == [
        owner,
        coordinator,
    ]
    config["buzz"]["owner_public_keys"] = []
    with pytest.raises(RuntimeError, match="owner_public_keys"):
        service_runner.role_author_allowlist(config, "coordinator")


def test_service_health_is_durable_nonsecret_state(store):
    store.record_service(
        name="io.atlas.collab.coordinator",
        manager="launchd",
        definition_path="/safe/LaunchAgents/coordinator.plist",
        fingerprint="a" * 64,
        status="installed",
    )
    store.update_service_status("io.atlas.collab.coordinator", "running")
    assert store.services()[0]["status"] == "running"


def test_runtime_shim_strips_buzz_identity_before_agent_exec():
    source = {
        "BUZZ_PRIVATE_KEY": "sentinel-private-value",
        "BUZZ_AUTH_TAG": "sentinel-auth-value",
        "ATLAS_COLLAB_VAULT_VALUE": "sentinel-vault-value",
        "ATLAS_COLLAB_RUNTIME_COMMAND": "/example/hermes",
        "ATLAS_COLLAB_RUNTIME_ARGS_JSON": '["acp"]',
        "HERMES_PROFILE": "atlas-collab-implementer",
    }
    sanitized = runtime_shim.sanitized_runtime_env(source)
    assert "BUZZ_PRIVATE_KEY" not in sanitized
    assert "BUZZ_AUTH_TAG" not in sanitized
    assert "ATLAS_COLLAB_VAULT_VALUE" not in sanitized
    assert sanitized["HERMES_PROFILE"] == "atlas-collab-implementer"
    assert runtime_shim.runtime_command(source) == ["/example/hermes", "acp"]
