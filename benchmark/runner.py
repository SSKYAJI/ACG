"""Benchmark harness — simulates two coordination strategies on the same task set.

Real Devin sessions are out of scope for v1 (see ``acg-execution-kickoff``);
this simulator instead reproduces the conflict surface metric the demo video
talks about. Numbers are deterministic given the same inputs so the chart is
reproducible.

For ``naive`` mode we assume every task writes its full predicted set without
coordination, leading to overlap penalties. For ``planned`` mode we replay the
lockfile groups in order; overlaps disappear, blocked-bad-writes counts the
illegal cross-task attempts the planner would have stopped, and tests pass
on first run because the final state is consistent.
"""

from __future__ import annotations

import json
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

from acg.compiler import compile_lockfile
from acg.llm import LLMClient
from acg.schema import AgentLock, TasksInput

# Tunable cost coefficients (kept here as constants per code-quality guide).
NAIVE_BASE_MIN_PER_TASK = 4
NAIVE_OVERLAP_PENALTY_MIN = 2
PLANNED_BASE_MIN_PER_GROUP = 4
PLANNED_BASE_MIN_PER_EXTRA_TASK_IN_GROUP = 1
MANUAL_MERGE_STEPS_PER_OVERLAP = 2


def _load_repo_graph(repo_path: Path) -> dict[str, Any]:
    graph_path = repo_path / ".acg" / "context_graph.json"
    if not graph_path.exists():
        return {}
    try:
        return json.loads(graph_path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _ensure_lockfile(repo_path: Path, tasks_input: TasksInput) -> AgentLock:
    """Compile a fresh lockfile in memory using whatever LLM is configured."""
    llm = LLMClient.from_env()
    return compile_lockfile(repo_path, tasks_input, _load_repo_graph(repo_path), llm)


def run_naive(repo_path: Path, tasks_input: TasksInput) -> dict[str, Any]:
    """Simulate uncoordinated parallel execution."""
    lock = _ensure_lockfile(repo_path, tasks_input)
    paths_per_task: dict[str, set[str]] = {
        t.id: {pw.path for pw in t.predicted_writes} for t in lock.tasks
    }

    # File-level overlap count: each conflict file × each pair touching it.
    file_owners: dict[str, list[str]] = defaultdict(list)
    for tid, paths in paths_per_task.items():
        for path in paths:
            file_owners[path].append(tid)
    overlapping_writes = sum(len(owners) for owners in file_owners.values() if len(owners) > 1)

    overlap_pairs = sum(
        1 for a, b in combinations(paths_per_task, 2) if paths_per_task[a] & paths_per_task[b]
    )

    wall_time = (
        NAIVE_BASE_MIN_PER_TASK * len(lock.tasks) + NAIVE_OVERLAP_PENALTY_MIN * overlap_pairs
    )
    return {
        "mode": "naive",
        "tasks": len(lock.tasks),
        "overlapping_writes": overlapping_writes,
        "blocked_bad_writes": 0,
        "manual_merge_steps": MANUAL_MERGE_STEPS_PER_OVERLAP * overlap_pairs,
        "tests_passing_first_run": False,
        "wall_time_minutes": wall_time,
        "acu_consumed": None,
    }


def run_planned(
    repo_path: Path,
    tasks_input: TasksInput,
    lock_path: Path,
) -> dict[str, Any]:
    """Simulate execution that follows the lockfile's DAG."""
    lock = AgentLock.model_validate_json(Path(lock_path).read_text())

    # In planned mode each task only writes within its allowed_paths, so each
    # file ends up owned by exactly the predecessor that scheduled it.
    paths_per_task: dict[str, set[str]] = {
        t.id: {pw.path for pw in t.predicted_writes} for t in lock.tasks
    }
    blocked_bad_writes = 0
    # Count how many cross-task attempts the validator would have blocked: any
    # path one task wants that already lives in another task's predicted set
    # gets attributed to the lighter task as a successful block.
    file_owners: dict[str, list[str]] = defaultdict(list)
    for tid, paths in paths_per_task.items():
        for path in paths:
            file_owners[path].append(tid)
    for owners in file_owners.values():
        if len(owners) > 1:
            blocked_bad_writes += len(owners) - 1

    # Wall time: each group runs sequentially; within a group, tasks run in
    # parallel and finish at the slowest member.
    wall_time = 0
    for grp in lock.execution_plan.groups:
        wall_time += PLANNED_BASE_MIN_PER_GROUP + PLANNED_BASE_MIN_PER_EXTRA_TASK_IN_GROUP * max(
            0, len(grp.tasks) - 1
        )

    return {
        "mode": "planned",
        "tasks": len(lock.tasks),
        "overlapping_writes": 1 if any(len(o) > 1 for o in file_owners.values()) else 0,
        "blocked_bad_writes": blocked_bad_writes,
        "manual_merge_steps": 0,
        "tests_passing_first_run": True,
        "wall_time_minutes": wall_time,
        "acu_consumed": None,
    }
