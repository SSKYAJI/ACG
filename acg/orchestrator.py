"""Goal-level task decomposition for ACG.

The compiler consumes ``tasks.json``; this module gives ACG a first-class
orchestrator step that can create that task list from a higher-level coding
goal. It is deliberately narrow: the output is still a human-reviewable
``TasksInput`` artifact, not an opaque runtime plan.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from .llm import LLMProtocol
from .schema import TaskInput, TaskInputHints, TasksInput

MAX_REPO_FILES_FOR_ORCHESTRATOR = 80
MAX_TASKS_DEFAULT = 8


class TaskPlanningError(ValueError):
    """Raised when a goal cannot be decomposed into a valid task list."""


def _slugify_task_id(value: str) -> str:
    """Return a lockfile-compatible task id from model text."""
    slug = re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-_")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug or "task"


def _dedupe_task_id(task_id: str, seen: set[str]) -> str:
    base = _slugify_task_id(task_id)
    candidate = base
    idx = 2
    while candidate in seen:
        candidate = f"{base}-{idx}"
        idx += 1
    seen.add(candidate)
    return candidate


def _compact_repo_graph(repo_graph: dict[str, Any]) -> dict[str, Any]:
    """Trim scan output to what a planner needs for decomposition."""
    files = repo_graph.get("files") or []
    hotspots = set(repo_graph.get("hotspots") or [])
    scored = sorted(
        files,
        key=lambda f: (
            0 if f.get("path") in hotspots else 1,
            -(f.get("imported_by_count") or 0),
            f.get("path", ""),
        ),
    )
    trimmed: list[dict[str, Any]] = []
    for entry in scored[:MAX_REPO_FILES_FOR_ORCHESTRATOR]:
        if not isinstance(entry, dict):
            continue
        trimmed.append(
            {
                "path": entry.get("path"),
                "exports": (entry.get("exports") or [])[:6],
                "imports": (entry.get("imports") or [])[:6],
                "is_hotspot": bool(entry.get("is_hotspot") or entry.get("path") in hotspots),
            }
        )
    return {
        "language": repo_graph.get("language"),
        "languages": repo_graph.get("languages") or [],
        "hotspots": sorted(hotspots),
        "files": trimmed,
    }


def _build_planner_prompt(
    goal: str,
    repo_graph: dict[str, Any],
    *,
    max_tasks: int,
) -> list[dict[str, str]]:
    system = (
        "You are the ACG orchestrator for a multi-agent coding run. "
        "Decompose one high-level repository goal into reviewable sub-agent tasks. "
        "Each task must be independently executable, file-aware, and small enough "
        "for one coding agent. Prefer explicit dependencies over vague coordination. "
        'Output ONLY a JSON object with key "tasks". Each task must include: '
        '"id" (lowercase letters, digits, dash, underscore), "prompt" '
        '(specific coding instruction), optional "hints" with "touches" '
        '(short feature/path words) and "suspected_files" (repo-relative '
        'files worth inspecting), and optional "depends_on" (task ids).'
    )
    user = (
        f"High-level goal:\n{goal.strip()}\n\n"
        f"Limit the decomposition to at most {max_tasks} tasks.\n\n"
        "Repository graph summary:\n"
        f"{json.dumps(_compact_repo_graph(repo_graph), sort_keys=True)}\n\n"
        "Return JSON only."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if not text:
        raise TaskPlanningError("planner returned an empty response")
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise TaskPlanningError("planner response did not contain a JSON object") from None
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise TaskPlanningError(f"planner response was not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise TaskPlanningError("planner response must be a JSON object")
    return payload


def _coerce_tasks(payload: dict[str, Any], *, max_tasks: int) -> TasksInput:
    raw_tasks = payload.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise TaskPlanningError("planner JSON must contain a non-empty tasks[] array")

    seen: set[str] = set()
    id_aliases: dict[str, str] = {}
    tasks: list[TaskInput] = []
    for idx, item in enumerate(raw_tasks[:max_tasks], start=1):
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id") or f"task-{idx}"
        raw_id_str = str(raw_id)
        task_id = _dedupe_task_id(raw_id_str, seen)
        id_aliases.setdefault(raw_id_str, task_id)
        id_aliases.setdefault(_slugify_task_id(raw_id_str), task_id)
        prompt = item.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            prompt = item.get("description")
        if not isinstance(prompt, str) or not prompt.strip():
            continue

        hints_payload = item.get("hints")
        touches: list[str] = []
        suspected_files: list[str] = []
        if isinstance(hints_payload, dict):
            raw_touches = hints_payload.get("touches") or []
            if isinstance(raw_touches, list):
                touches = [str(t) for t in raw_touches if str(t).strip()]
            raw_suspected = hints_payload.get("suspected_files") or []
            if isinstance(raw_suspected, list):
                suspected_files = [
                    str(path).strip("./") for path in raw_suspected if str(path).strip()
                ]
        depends_on_raw = item.get("depends_on") or []
        depends_on = [str(dep) for dep in depends_on_raw if isinstance(dep, str)]
        tasks.append(
            TaskInput(
                id=task_id,
                prompt=prompt.strip(),
                hints=(
                    TaskInputHints(touches=touches, suspected_files=suspected_files)
                    if touches or suspected_files
                    else None
                ),
                depends_on=depends_on,
            )
        )

    if not tasks:
        raise TaskPlanningError("planner did not produce any usable tasks")

    task_ids = {task.id for task in tasks}
    for task in tasks:
        normalized_deps: list[str] = []
        for dep in task.depends_on:
            dep_id = id_aliases.get(dep) or id_aliases.get(_slugify_task_id(dep)) or dep
            if dep_id in task_ids and dep_id != task.id and dep_id not in normalized_deps:
                normalized_deps.append(dep_id)
        task.depends_on = normalized_deps

    try:
        return TasksInput(version="1.0", tasks=tasks)
    except ValidationError as exc:
        raise TaskPlanningError(f"planner output failed task schema validation: {exc}") from exc


def plan_tasks_from_goal(
    goal: str,
    repo_graph: dict[str, Any],
    llm: LLMProtocol,
    *,
    max_tasks: int = MAX_TASKS_DEFAULT,
) -> TasksInput:
    """Ask an orchestrator LLM to produce a reviewable ``TasksInput`` artifact."""
    max_tasks = max(1, max_tasks)
    messages = _build_planner_prompt(goal, repo_graph, max_tasks=max_tasks)
    reply = llm.complete(messages)
    payload = _extract_json_object(reply)
    tasks = _coerce_tasks(payload, max_tasks=max_tasks)
    tasks.tokens_planner_total = max(
        1,
        sum(len(message.get("content", "") or "") for message in messages) // 4,
    )
    return tasks


__all__ = [
    "MAX_TASKS_DEFAULT",
    "TaskPlanningError",
    "plan_tasks_from_goal",
]
