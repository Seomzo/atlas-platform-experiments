"""Index the installed Atlas tools and skills into the capability graph."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from .models import EvidenceInput
from .store import CortexStore, stable_hash


_FRONTMATTER_DESCRIPTION = re.compile(r"(?m)^description:\s*[\"']?(.*?)[\"']?\s*$")
_HEADING = re.compile(r"(?m)^#\s+(.+?)\s*$")


def _skill_summary(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")[:64_000]
    heading = _HEADING.search(text)
    description = _FRONTMATTER_DESCRIPTION.search(text)
    name = (heading.group(1).strip() if heading else path.parent.name)[:160]
    summary = (
        description.group(1).strip() if description else "Installed Atlas skill"
    )[:600]
    return name, summary


def sync_capability_graph(
    store: CortexStore,
    hermes_home: str | Path,
    *,
    tool_names: Iterable[str] = (),
) -> dict[str, int]:
    """Idempotently project safe tool/skill metadata into Cortex.

    Skill bodies can contain instructions, credentials, or customer content,
    so only a bounded title/declared description is indexed. Dynamic recall
    still treats these records as reference evidence, never authorization.
    """
    home = Path(hermes_home).expanduser().resolve()
    session_id = "cortex-capability-catalog"
    store.ensure_session(
        session_id,
        logical_conversation_id=session_id,
        title="Atlas capability catalog",
        workspace="atlas",
    )
    tools = 0
    skills = 0
    for raw_name in sorted({
        str(value).strip() for value in tool_names if str(value).strip()
    }):
        evidence_id = store.append_evidence(
            session_id,
            EvidenceInput(
                source_type="system_event",
                content=f"Atlas tool: {raw_name}",
                source_locator=f"atlas:tool:{stable_hash(raw_name)[:24]}",
                knowledge_space="atlas-capabilities",
                sensitivity="internal",
                retention_class="catalog",
                metadata={"catalog_type": "tool"},
            ),
        )
        store.upsert_entity(
            canonical_name=raw_name,
            entity_type="tool",
            description="Installed Atlas tool",
            aliases=(raw_name.replace("_", " "),),
            evidence_id=evidence_id,
            knowledge_space="atlas-capabilities",
        )
        tools += 1

    skills_root = home / "skills"
    if skills_root.is_dir():
        for path in sorted(skills_root.glob("**/SKILL.md")):
            try:
                resolved = path.resolve()
                resolved.relative_to(skills_root.resolve())
                if path.is_symlink() or not path.is_file():
                    continue
                name, summary = _skill_summary(path)
                locator = resolved.relative_to(home).as_posix()
                evidence_id = store.append_evidence(
                    session_id,
                    EvidenceInput(
                        source_type="manual",
                        content=f"Atlas skill: {name}. {summary}",
                        source_locator=f"atlas-profile:{locator}",
                        knowledge_space="atlas-capabilities",
                        sensitivity="internal",
                        retention_class="catalog",
                        metadata={"catalog_type": "skill", "path": locator},
                    ),
                )
                store.upsert_entity(
                    canonical_name=name,
                    entity_type="skill",
                    description=summary,
                    aliases=(path.parent.name,),
                    evidence_id=evidence_id,
                    knowledge_space="atlas-capabilities",
                )
                skills += 1
            except (OSError, UnicodeError, ValueError):
                continue
    return {"tools": tools, "skills": skills}


__all__ = ["sync_capability_graph"]
