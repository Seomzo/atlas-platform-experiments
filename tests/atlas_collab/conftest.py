from pathlib import Path

import pytest

from tools.atlas_collab.config import DEFAULTS
from tools.atlas_collab.models import TaskContract
from tools.atlas_collab.state import StateStore


@pytest.fixture(autouse=True)
def collab_home(tmp_path: Path, monkeypatch) -> Path:
    path = tmp_path / "collab-home"
    monkeypatch.setenv("ATLAS_COLLAB_HOME", str(path))
    return path


@pytest.fixture
def store(tmp_path: Path):
    with StateStore(tmp_path / "state.db") as value:
        yield value


@pytest.fixture
def contract() -> TaskContract:
    return TaskContract.from_mapping({
        "schema_version": "atlas.collab.task.v1",
        "task_id": "DOGFOOD-1",
        "workstream_id": "WS-22",
        "repository": "Seomzo/atlas-platform-experiments",
        "base_ref": "main",
        "base_sha": "0123456789abcdef",
        "title": "Validate collaboration fixture",
        "goal": "Produce an observable and reviewed fixture.",
        "in_scope": ["tools/atlas_collab"],
        "out_of_scope": ["Atlas product runtime"],
        "acceptance_criteria": [
            {
                "id": "AC-01",
                "text": "The deterministic fixture passes its focused test.",
                "required_evidence": "focused test output",
            }
        ],
        "constraints": ["No automatic merge or deployment."],
        "canonical_context": ["AGENTS.md"],
        "dependencies": [],
        "human_gates": ["Human merge approval."],
        "requested_roles": ["coordinator", "implementer", "reviewer"],
    })


@pytest.fixture
def config():
    import copy

    value = copy.deepcopy(DEFAULTS)
    value["buzz"]["relay_url"] = "fake://relay"
    value["buzz"]["community"] = "atlas-platform"
    return value
