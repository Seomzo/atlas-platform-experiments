from pathlib import Path

import yaml


SKILL = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "software-development"
    / "atlas-collaboration"
    / "SKILL.md"
)


def test_skill_frontmatter_and_safety_contract():
    text = SKILL.read_text(encoding="utf-8")
    _, frontmatter, _ = text.split("---", 2)
    metadata = yaml.safe_load(frontmatter)
    assert metadata["name"] == "atlas-collaboration"
    assert len(metadata["description"]) <= 60
    for heading in (
        "## When to Use",
        "## Prerequisites",
        "## Procedure",
        "## Pitfalls",
        "## Verification",
    ):
        assert heading in text
    assert "Never merge, deploy" in text
    assert "Channel membership is identity, not authorization" in text
