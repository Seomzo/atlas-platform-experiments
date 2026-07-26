import pytest

from tools.atlas_collab.dogfood_fixture import role_label


@pytest.mark.parametrize("role", ["coordinator", "implementer", "reviewer"])
def test_role_label_accepts_canonical_roles(role):
    assert role_label(role) == role


def test_role_label_rejects_unknown_role():
    with pytest.raises(ValueError, match="unsupported collaboration role"):
        role_label("administrator")


@pytest.mark.parametrize("role", ["REVIEWER", " implementer", "coordinator "])
def test_role_label_rejects_noncanonical_casing_or_whitespace(role):
    with pytest.raises(ValueError, match="unsupported collaboration role"):
        role_label(role)
