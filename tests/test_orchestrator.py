"""Tests for goal-level task decomposition."""

from __future__ import annotations

import json
from typing import Any

from acg.orchestrator import plan_tasks_from_goal


class PlannerLLM:
    model = "planner-test"

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.messages: list[dict[str, str]] | None = None

    def complete(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None = None,
    ) -> str:
        del response_format
        self.messages = messages
        return json.dumps(self.payload)


def test_plan_tasks_from_goal_returns_valid_tasks_input() -> None:
    llm = PlannerLLM(
        {
            "tasks": [
                {
                    "id": "add-oauth",
                    "prompt": "Add OAuth provider support in the auth config.",
                    "hints": {
                        "touches": ["auth", "oauth"],
                        "suspected_files": ["src/server/auth/config.ts"],
                    },
                },
                {
                    "id": "add-tests",
                    "prompt": "Add focused tests for the OAuth flow.",
                    "hints": {"touches": ["tests"]},
                    "depends_on": ["add-oauth"],
                },
            ]
        }
    )
    graph = {
        "language": "typescript",
        "files": [{"path": "src/server/auth/config.ts", "exports": ["authOptions"]}],
        "hotspots": ["src/server/auth/config.ts"],
    }

    tasks = plan_tasks_from_goal("Add Google OAuth and tests.", graph, llm)

    assert [task.id for task in tasks.tasks] == ["add-oauth", "add-tests"]
    assert tasks.tasks[0].hints is not None
    assert tasks.tasks[0].hints.touches == ["auth", "oauth"]
    assert tasks.tasks[0].hints.suspected_files == ["src/server/auth/config.ts"]
    assert tasks.tasks[1].depends_on == ["add-oauth"]
    assert tasks.tokens_planner_total is not None
    assert llm.messages is not None
    assert "High-level goal" in llm.messages[1]["content"]


def test_plan_tasks_from_goal_sanitizes_ids_and_drops_unknown_dependencies() -> None:
    llm = PlannerLLM(
        {
            "tasks": [
                {
                    "id": "Implement Core!",
                    "prompt": "Implement the core behavior.",
                    "depends_on": ["missing"],
                },
                {
                    "id": "Implement Core!",
                    "prompt": "Add a second scoped change.",
                    "depends_on": ["Implement Core!"],
                },
            ]
        }
    )

    tasks = plan_tasks_from_goal("Do the work.", {"files": []}, llm)

    assert [task.id for task in tasks.tasks] == ["implement-core", "implement-core-2"]
    assert tasks.tasks[0].depends_on == []
    assert tasks.tasks[1].depends_on == ["implement-core"]
