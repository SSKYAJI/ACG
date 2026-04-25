"""Cross-validate that the JSON Schema and the Pydantic models agree."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from acg.schema import AgentLock


@pytest.mark.parametrize(
    "fixture_name",
    ["example_simple_lockfile_path", "example_dag_lockfile_path"],
)
def test_examples_validate_against_schema(
    request: pytest.FixtureRequest, schema_dict: dict, fixture_name: str
) -> None:
    path: Path = request.getfixturevalue(fixture_name)
    payload = json.loads(path.read_text())
    jsonschema.validate(payload, schema_dict)


@pytest.mark.parametrize(
    "fixture_name",
    ["example_simple_lockfile_path", "example_dag_lockfile_path"],
)
def test_examples_load_via_pydantic(
    request: pytest.FixtureRequest, fixture_name: str
) -> None:
    path: Path = request.getfixturevalue(fixture_name)
    lock = AgentLock.model_validate_json(path.read_text())
    assert lock.version == "1.0"
    assert lock.tasks
    assert lock.execution_plan.groups


def test_dag_example_groups_match_demo(
    example_dag_lockfile_path: Path,
) -> None:
    lock = AgentLock.model_validate_json(example_dag_lockfile_path.read_text())
    groups = sorted(lock.execution_plan.groups, key=lambda g: g.id)
    assert [g.tasks for g in groups] == [
        ["oauth", "settings"],
        ["billing"],
        ["tests"],
    ]
    assert [g.type for g in groups] == ["parallel", "serial", "serial"]
    assert [g.waits_for for g in groups] == [[], [1], [2]]
