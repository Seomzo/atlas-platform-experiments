from __future__ import annotations

import pytest

from altas.credentials import CredentialNotFound, MemoryCredentialVault, SecretHandle


def test_secret_handle_is_opaque_and_validated() -> None:
    handle = SecretHandle(
        "tenant-a",
        "store-a",
        "tekion-dealer-key",
        "primary",
    )

    assert str(handle) == ("altas-secret://tenant-a/store-a/tekion-dealer-key/primary")
    with pytest.raises(ValueError):
        SecretHandle("tenant a", "store-a", "tekion", "primary")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant_id", "tenant/a"),
        ("store_id", "store/a"),
        ("kind", "tekion/key"),
        ("name", "primary/key"),
        ("name", "primary?key"),
        ("name", "primary#key"),
        ("name", "primary%2Fkey"),
    ],
)
def test_secret_handle_rejects_ambiguous_components(field: str, value: str) -> None:
    values = {
        "tenant_id": "tenant-a",
        "store_id": "store-a",
        "kind": "tekion-key",
        "name": "primary",
    }
    values[field] = value

    with pytest.raises(ValueError, match="unambiguous identifier"):
        SecretHandle(**values)


def test_secret_handle_component_boundaries_cannot_collide() -> None:
    canonical = SecretHandle("tenant-a", "store-a", "tekion-key", "primary")

    with pytest.raises(ValueError):
        SecretHandle("tenant-a/store-a", "tekion-key", "primary", "fallback")

    assert canonical.storage_key == "tenant-a/store-a/tekion-key/primary"


def test_memory_vault_lifecycle() -> None:
    vault = MemoryCredentialVault()
    handle = SecretHandle("tenant-a", "store-a", "device", "worker")

    vault.put(handle, "sentinel-secret")
    assert vault.resolve(handle) == "sentinel-secret"
    vault.delete(handle)

    with pytest.raises(CredentialNotFound):
        vault.resolve(handle)
