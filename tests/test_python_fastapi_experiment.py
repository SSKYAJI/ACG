from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from acg.schema import AgentLock, TasksInput

ROOT = Path(__file__).resolve().parent.parent
EXPERIMENT_DIR = ROOT / "experiments" / "python_fastapi"
TASKS_PATH = EXPERIMENT_DIR / "tasks.json"
LOCKFILE_PATH = EXPERIMENT_DIR / "agent_lock.json"
MAKEFILE_PATH = ROOT / "Makefile"

EXPECTED_TASK_IDS = {
    "health-route",
    "admin-audit-route",
    "oauth-callback-route",
    "smtp-timeout-config",
    "request-logging-config",
}

CANONICAL_CONTENTION_PATHS = {
    "backend/app/api/main.py",
    "backend/app/core/config.py",
    "backend/app/main.py",
    "backend/tests/api/routes/",
}

REQUIRED_MAKE_TARGETS = {
    "setup-python-fastapi",
    "compile-python-fastapi",
    "eval-python-fastapi-mock",
    "analyze-python-fastapi-mock",
}


def _load_tasks_input() -> TasksInput:
    return TasksInput.model_validate_json(TASKS_PATH.read_text())


def test_python_fastapi_tasks_json_is_well_formed() -> None:
    assert TASKS_PATH.exists(), f"missing required tasks file: {TASKS_PATH}"

    tasks_input = _load_tasks_input()
    task_ids = [task.id for task in tasks_input.tasks]

    assert tasks_input.version == "1.0"
    assert len(tasks_input.tasks) == 5
    assert len(task_ids) == len(set(task_ids)), "task ids must be unique"
    assert set(task_ids) == EXPECTED_TASK_IDS

    for task in tasks_input.tasks:
        assert task.prompt, f"task {task.id} must have a non-empty prompt"
        assert any(path in task.prompt for path in CANONICAL_CONTENTION_PATHS), (
            f"task {task.id} prompt must mention at least one canonical contention path"
        )


def test_makefile_contains_python_fastapi_targets() -> None:
    assert MAKEFILE_PATH.exists(), f"missing Makefile: {MAKEFILE_PATH}"
    makefile_text = MAKEFILE_PATH.read_text()

    for target_name in REQUIRED_MAKE_TARGETS:
        assert target_name in makefile_text, f"Makefile missing target text {target_name!r}"


def test_python_fastapi_lockfile_shape_if_present() -> None:
    if not LOCKFILE_PATH.exists():
        pytest.skip("python_fastapi lockfile not committed yet")

    tasks_input = _load_tasks_input()
    expected_task_ids = {task.id for task in tasks_input.tasks}
    lock = AgentLock.model_validate_json(LOCKFILE_PATH.read_text())

    assert lock.repo.languages == ["python"]
    assert len(lock.tasks) == 5
    assert len(lock.conflicts_detected) >= 2
    assert len(lock.execution_plan.groups) >= 2

    grouped_task_ids = [
        task_id for group in lock.execution_plan.groups for task_id in group.tasks
    ]
    grouped_counts = Counter(grouped_task_ids)

    assert set(grouped_counts) == expected_task_ids
    assert all(count == 1 for count in grouped_counts.values())
