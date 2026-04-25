"""Top-level orchestration: tasks + repo graph → ``agent_lock.json``.

The compiler is the only module that owns the *what-runs-after-what* heuristics
that are not derivable from write-set overlap alone. Specifically, it injects a
"tests run last" dependency whenever a task hint declares ``tests``. Everything
else is delegated to :mod:`acg.predictor` and :mod:`acg.solver`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import __version__
from .llm import LLMProtocol
from .schema import (
    AgentLock,
    ExecutionPlan,
    Generator,
    PredictedWrite,
    Repo,
    Task,
    TaskInput,
    TasksInput,
)
from .solver import build_dag, detect_conflicts, topological_groups

# Min segments before we broaden ``a/b/c/page.tsx`` into ``a/b/c/**``.
# Set to 4 so shallow paths like ``src/server/x.ts`` (3 segments) stay exact
# and don't accidentally cover sibling feature areas (``src/server/auth/**``).
GLOB_BROADENING_MIN_SEGMENTS = 4
GLOB_BROADENING_MIN_CONFIDENCE = 0.7
TEST_HINT_KEYWORDS = {"tests", "test", "e2e", "playwright"}


def _to_allowed_path(write: PredictedWrite) -> str:
    """Convert a single predicted write into an ``allowed_paths`` entry.

    Deep, high-confidence paths broaden to ``parent/**`` so the agent can
    create sibling files in the same feature directory. Shallow or
    low-confidence paths stay exact.
    """
    parts = write.path.split("/")
    if (
        write.confidence >= GLOB_BROADENING_MIN_CONFIDENCE
        and len(parts) >= GLOB_BROADENING_MIN_SEGMENTS
    ):
        return "/".join(parts[:-1]) + "/**"
    return write.path


def _build_allowed_paths(writes: list[PredictedWrite]) -> list[str]:
    """Return a sorted, deduplicated list of glob patterns covering ``writes``."""
    seen: set[str] = set()
    for write in writes:
        seen.add(_to_allowed_path(write))
    return sorted(seen)


def _is_test_task(task: TaskInput) -> bool:
    if not task.hints or not task.hints.touches:
        return False
    needles = {h.lower() for h in task.hints.touches}
    return bool(needles & TEST_HINT_KEYWORDS)


def _resolve_dependencies(tasks_input: list[TaskInput]) -> dict[str, list[str]]:
    """Return ``{task_id: depends_on}`` after applying heuristics.

    The user's explicit ``depends_on`` declarations are preserved verbatim;
    test-flagged tasks additionally depend on every non-test task. The result
    is acyclic by construction: tests can only depend on non-tests.
    """
    deps: dict[str, list[str]] = {t.id: list(t.depends_on) for t in tasks_input}
    test_ids = {t.id for t in tasks_input if _is_test_task(t)}
    non_test_ids = [t.id for t in tasks_input if t.id not in test_ids]
    for tid in test_ids:
        for other in non_test_ids:
            if other not in deps[tid]:
                deps[tid].append(other)
    return deps


def _detect_languages(repo_graph: dict[str, Any]) -> list[str]:
    """Best-effort language list pulled from the graph builder output."""
    languages: list[str] = []
    if isinstance(repo_graph, dict):
        lang = repo_graph.get("language")
        if isinstance(lang, str) and lang:
            languages.append(lang)
        extra = repo_graph.get("languages")
        if isinstance(extra, list):
            for lang in extra:
                if isinstance(lang, str) and lang and lang not in languages:
                    languages.append(lang)
    return languages or ["unknown"]


def compile_lockfile(
    repo_path: Path,
    tasks_input: TasksInput,
    repo_graph: dict[str, Any],
    llm: LLMProtocol,
) -> AgentLock:
    """Compile a fully populated :class:`AgentLock`.

    Args:
        repo_path: Path to the target repository (used for metadata only).
        tasks_input: Parsed ``tasks.json`` document.
        repo_graph: Output of the TS graph builder; ``{}`` is acceptable.
        llm: LLM client used by :mod:`acg.predictor`.

    Returns:
        A validated :class:`AgentLock` that round-trips through the JSON
        Schema in ``schema/agent_lock.schema.json``.
    """
    # Late import to keep the schema module dependency-free at import time.
    from .predictor import predict_writes

    deps_by_id = _resolve_dependencies(tasks_input.tasks)
    tasks: list[Task] = []
    for ti in tasks_input.tasks:
        writes = predict_writes(ti, repo_graph, llm)
        allowed_paths = _build_allowed_paths(writes)
        tasks.append(
            Task(
                id=ti.id,
                prompt=ti.prompt,
                predicted_writes=writes,
                allowed_paths=allowed_paths,
                depends_on=deps_by_id.get(ti.id, []),
            )
        )

    conflicts = detect_conflicts(tasks)
    dag = build_dag(tasks)
    groups = topological_groups(dag)

    # Stamp parallel_group onto each task for human readability.
    group_by_task: dict[str, int] = {}
    for grp in groups:
        for task_id in grp.tasks:
            group_by_task[task_id] = grp.id
    for task in tasks:
        task.parallel_group = group_by_task.get(task.id)

    return AgentLock(
        version="1.0",
        generated_at=AgentLock.utcnow(),
        generator=Generator(tool="acg", version=__version__, model=llm.model),
        repo=Repo(root=str(repo_path), languages=_detect_languages(repo_graph)),
        tasks=tasks,
        execution_plan=ExecutionPlan(groups=groups),
        conflicts_detected=conflicts,
    )
