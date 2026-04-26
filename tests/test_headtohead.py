"""Tests for the Greenhouse head-to-head harness.

All tests run in deterministic mock mode; the live GX10 path is exercised
only by the human author. The harness lives at
``experiments/greenhouse/headtohead.py``; we load it once via importlib
so the tests don't require the ``experiments`` directory to be a package.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from acg import runtime as acg_runtime
from acg.schema import (
    AgentLock,
    ExecutionPlan,
    Generator,
    Group,
    PredictedWrite,
    Repo,
    Task,
)

ROOT = Path(__file__).resolve().parent.parent
HARNESS_PATH = ROOT / "experiments" / "greenhouse" / "headtohead.py"


def _load_harness():
    spec = importlib.util.spec_from_file_location("greenhouse_headtohead", str(HARNESS_PATH))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness():
    return _load_harness()


def _make_task(
    task_id: str,
    *,
    paths: list[str],
    allowed: list[str] | None = None,
) -> Task:
    return Task(
        id=task_id,
        prompt=f"Refactor {task_id}.",
        predicted_writes=[
            PredictedWrite(path=p, confidence=0.9, reason=f"{task_id} touches {p}") for p in paths
        ],
        allowed_paths=allowed if allowed is not None else paths,
    )


def _make_lock(
    tasks: list[Task],
    groups: list[Group] | None = None,
    *,
    repo_root: str = "experiments/greenhouse/checkout",
) -> AgentLock:
    if groups is None:
        groups = [Group(id=1, tasks=[t.id for t in tasks], type="parallel", waits_for=[])]
    return AgentLock(
        version="1.0",
        generated_at=AgentLock.utcnow(),
        generator=Generator(tool="acg", version="0.1.0"),
        repo=Repo(root=repo_root, languages=["java"]),
        tasks=tasks,
        execution_plan=ExecutionPlan(groups=groups),
    )


@pytest.fixture
def pom_collision_lock() -> AgentLock:
    """3-task lockfile where every task predicts ``pom.xml`` (the collision
    lever the demo leans on)."""
    tasks = [
        _make_task(
            "lambda-rowmapper-account",
            paths=["pom.xml", "src/main/java/A.java"],
            allowed=["pom.xml", "src/main/java/A.java"],
        ),
        _make_task(
            "lambda-rowmapper-invite",
            paths=["pom.xml", "src/main/java/B.java"],
            # Allowed path strictly excludes pom.xml so planned mode blocks it.
            allowed=["src/main/java/B.java"],
        ),
        _make_task(
            "lambda-rowmapper-app",
            paths=["pom.xml", "src/main/java/C.java"],
            allowed=["src/main/java/C.java"],
        ),
    ]
    groups = [
        Group(id=1, tasks=["lambda-rowmapper-account"], type="serial", waits_for=[]),
        Group(id=2, tasks=["lambda-rowmapper-invite"], type="serial", waits_for=[1]),
        Group(id=3, tasks=["lambda-rowmapper-app"], type="serial", waits_for=[2]),
    ]
    return _make_lock(tasks, groups)


def test_naive_records_overlap_on_pom_xml(harness, pom_collision_lock) -> None:
    """All 3 tasks predict pom.xml → overlapping_writes >= 3 and overlap_pairs == 3."""
    sub_llm = harness._GreenhouseMockLLM(pom_collision_lock, role="worker")
    metrics = asyncio.run(harness.simulate_naive(pom_collision_lock, {}, sub_llm))

    assert metrics["tasks"] == 3
    assert metrics["overlapping_writes"] >= 3
    assert metrics["overlap_pairs"] == 3
    assert metrics["blocked_bad_writes"] == 0
    # Manual merge steps mirror the benchmark coefficient (2 * pairs).
    assert metrics["manual_merge_steps"] == 2 * 3
    assert metrics["tests_passing_first_run"] is False
    # Every proposal touching pom.xml should be flagged as a naive overlap.
    pom_entries = [p for p in metrics["proposals"] if p["file"] == "pom.xml"]
    assert len(pom_entries) == 3
    for entry in pom_entries:
        assert entry["allowed"] is False
        assert "naive overlap" in entry["reason"]


def test_planned_zero_manual_merges(harness, pom_collision_lock) -> None:
    """Planned mode: no manual merges, tests pass on first run."""
    orch = harness._GreenhouseMockLLM(pom_collision_lock, role="orchestrator")
    sub = harness._GreenhouseMockLLM(pom_collision_lock, role="worker")
    metrics = asyncio.run(
        harness.simulate_planned(
            pom_collision_lock,
            {},
            orch,
            sub,
            lockfile_path="test://greenhouse/agent_lock.json",
        )
    )

    assert metrics["manual_merge_steps"] == 0
    assert metrics["tests_passing_first_run"] is True
    # Planned must catch >= as many bad writes as naive (acceptance gate).
    assert metrics["blocked_bad_writes"] >= 0
    # Two of three pom.xml proposals are outside their allowed_paths.
    assert metrics["blocked_bad_writes"] == 2


def test_naive_does_not_call_validate_write(harness, pom_collision_lock, monkeypatch) -> None:
    """Naive simulator must NOT enforce — patching validate_write to raise
    AssertionError must leave the run intact."""
    monkeypatch.setattr(
        "acg.enforce.validate_write",
        MagicMock(side_effect=AssertionError("validator must not run in naive")),
    )
    sub_llm = harness._GreenhouseMockLLM(pom_collision_lock, role="worker")
    # Should complete without raising AssertionError.
    metrics = asyncio.run(harness.simulate_naive(pom_collision_lock, {}, sub_llm))
    assert metrics["tasks"] == 3


def test_planned_calls_run_lockfile_once(harness, pom_collision_lock, monkeypatch) -> None:
    """Planned path must delegate to ``acg.runtime.run_lockfile`` exactly once."""
    fake_run_result = acg_runtime.RunResult(
        version="1.0",
        generated_at="2026-04-25T00:00:00Z",
        lockfile="test://greenhouse/agent_lock.json",
        config={
            "orch_url": "mock://orch",
            "orch_model": "mock",
            "sub_url": "mock://sub",
            "sub_model": "mock",
        },
        orchestrator=acg_runtime.OrchestratorResult(
            url="mock://orch",
            model="mock",
            wall_s=0.0,
            completion_tokens=0,
            finish_reason="stop",
            content="{}",
            reasoning_content="",
            parsed=None,
        ),
        workers=[],
        groups_executed=[],
        started_at="2026-04-25T00:00:00Z",
        finished_at="2026-04-25T00:00:00Z",
        total_wall_s=0.0,
    )
    mock_run = AsyncMock(return_value=fake_run_result)
    monkeypatch.setattr(acg_runtime, "run_lockfile", mock_run)

    repo_graph: dict[str, Any] = {"language": "java", "files": []}
    orch = harness._GreenhouseMockLLM(pom_collision_lock, role="orchestrator")
    sub = harness._GreenhouseMockLLM(pom_collision_lock, role="worker")
    asyncio.run(
        harness.simulate_planned(
            pom_collision_lock,
            repo_graph,
            orch,
            sub,
            lockfile_path="test://greenhouse/agent_lock.json",
        )
    )

    assert mock_run.await_count == 1
    kwargs = mock_run.call_args.kwargs
    assert kwargs["lock"] is pom_collision_lock
    assert kwargs["repo_graph"] == repo_graph
    assert kwargs["lockfile_path"] == "test://greenhouse/agent_lock.json"


def test_cli_writes_combined_json(harness, pom_collision_lock, tmp_path: Path) -> None:
    """End-to-end CLI invocation produces a JSON file with both blocks."""
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(pom_collision_lock.model_dump_json(indent=2))
    repo_dir = tmp_path / "checkout"
    repo_dir.mkdir()
    out_path = tmp_path / "headtohead.json"

    result = subprocess.run(
        [
            sys.executable,
            str(HARNESS_PATH),
            "--lock",
            str(lock_path),
            "--repo",
            str(repo_dir),
            "--out",
            str(out_path),
            "--mock",
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert result.returncode == 0, result.stderr
    assert out_path.exists()

    payload = json.loads(out_path.read_text())
    assert "naive" in payload
    assert "planned" in payload
    assert payload["mode"] == "both"
    assert payload["version"] == "1.0"
    # Acceptance-gate invariant: planned never catches fewer bad writes than naive.
    assert payload["planned"]["blocked_bad_writes"] >= payload["naive"]["blocked_bad_writes"]
