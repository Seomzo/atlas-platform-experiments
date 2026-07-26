import plistlib

from tools.atlas_collab.config import DEFAULTS
from tools.atlas_collab.keychain import MemoryVault
from tools.atlas_collab.orchestrator import Orchestrator
from tools.atlas_collab.services import launch_agent_definition


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


def test_launch_agent_contains_no_private_key_or_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setenv("ATLAS_COLLAB_HOME", str(tmp_path))
    definition = launch_agent_definition("coordinator")
    payload = plistlib.dumps(definition).decode()
    assert "private-key" not in payload.lower()
    assert "BUZZ_PRIVATE_KEY" not in payload
    assert "service_runner" in payload


def test_development_config_has_no_product_or_merge_authority():
    assert DEFAULTS["routing"]["recovery_heartbeat_seconds"] == 0
    serialized = str(DEFAULTS).lower()
    assert "auto_merge" not in serialized
    assert "deploy" not in serialized
