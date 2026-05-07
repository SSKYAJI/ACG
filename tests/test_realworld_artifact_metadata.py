from __future__ import annotations

import json
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]

REALWORLD_ARTIFACTS = [
    (
        "experiments/realworld/runs/eval_run_acg.json",
        "realworld-nestjs-explicit-openrouter-v1",
    ),
    (
        "experiments/realworld/runs_blind_openrouter/eval_run_acg.json",
        "realworld-nestjs-blind-openrouter-v2",
    ),
    (
        "experiments/realworld/runs/tight/eval_run_acg.json",
        "realworld-nestjs-tight",
    ),
]


@pytest.mark.parametrize(("artifact_path", "expected_suite_name"), REALWORLD_ARTIFACTS)
def test_realworld_openrouter_artifacts_have_realworld_metadata(
    artifact_path: str,
    expected_suite_name: str,
) -> None:
    artifact = REPO_ROOT / artifact_path
    if not artifact.exists():
        pytest.skip(f"artifact not present: {artifact_path}")

    payload = json.loads(artifact.read_text())
    repo = payload["repo"]

    assert payload["suite_name"] == expected_suite_name
    assert "lujakob/nestjs-realworld-example-app" in repo["url"]
    assert "spring-attic/greenhouse" not in repo["url"]
    assert repo["local_path"].replace("\\", "/").endswith(
        "/experiments/realworld/checkout"
    )
