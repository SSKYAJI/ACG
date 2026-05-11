"""Tests for :mod:`acg.compiler`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from acg.compiler import compile_lockfile
from acg.schema import TaskInput, TaskInputHints, TasksInput


class _StubLLM:
    model = "stub"

    def __init__(self, replies: list[str]) -> None:
        self._replies = replies
        self._idx = 0

    def complete(
        self, messages: list[dict[str, str]], response_format: dict[str, Any] | None = None
    ) -> str:
        del messages, response_format
        r = self._replies[min(self._idx, len(self._replies) - 1)]
        self._idx += 1
        return r


class _NullPlannerLLM:
    model = "null-planner"
    skip_planner_llm = True

    def complete(
        self, messages: list[dict[str, str]], response_format: dict[str, Any] | None = None
    ) -> str:
        del messages, response_format
        return "{}"


def _repo_graph() -> dict[str, Any]:
    return {
        "language": "typescript",
        "files": [
            {
                "path": "lib/auth.ts",
                "exports": ["authOptions", "getCurrentUser"],
                "imports": ["next-auth"],
                "is_hotspot": True,
            },
            {
                "path": "components/sidebar.tsx",
                "exports": ["Sidebar"],
                "imports": ["next/link"],
                "is_hotspot": True,
            },
        ],
        "symbols_index": {
            "authOptions": "lib/auth.ts",
            "getCurrentUser": "lib/auth.ts",
        },
        "hotspots": ["lib/auth.ts", "components/sidebar.tsx"],
    }


def test_compile_lockfile_tokens_planner_total_positive(tmp_path: Path) -> None:
    tasks = TasksInput(
        tasks=[
            TaskInput(
                id="auth",
                prompt="Refactor authOptions to add a Google provider.",
                hints=TaskInputHints(touches=["auth"]),
            )
        ]
    )
    llm = _StubLLM(
        [
            json.dumps({"paths": ["components/sidebar.tsx"]}),
            json.dumps({"writes": []}),
            json.dumps({}),
        ]
    )
    lock = compile_lockfile(tmp_path, tasks, _repo_graph(), llm)
    assert lock.generator.tokens_planner_total is not None
    assert lock.generator.tokens_planner_total > 0


def test_compile_lockfile_tokens_planner_total_zero_when_planner_skipped(tmp_path: Path) -> None:
    tasks = TasksInput(
        tasks=[
            TaskInput(
                id="auth",
                prompt="Refactor authOptions to add a Google provider.",
                hints=TaskInputHints(touches=["auth"]),
            )
        ]
    )
    llm = _NullPlannerLLM()
    lock = compile_lockfile(tmp_path, tasks, _repo_graph(), llm)
    assert lock.generator.tokens_planner_total == 0


def test_compile_lockfile_adds_tokens_planner_to_tasks_input(tmp_path: Path) -> None:
    tasks = TasksInput(
        tasks=[
            TaskInput(
                id="auth",
                prompt="Refactor authOptions to add a Google provider.",
                hints=TaskInputHints(touches=["auth"]),
            )
        ],
        tokens_planner_total=500,
    )
    llm = _StubLLM(
        [
            json.dumps({"paths": ["components/sidebar.tsx"]}),
            json.dumps({"writes": []}),
            json.dumps({}),
        ]
    )
    lock = compile_lockfile(tmp_path, tasks, _repo_graph(), llm)
    assert lock.generator.tokens_planner_total is not None
    assert lock.generator.tokens_planner_total > 500
