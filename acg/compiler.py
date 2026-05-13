"""Top-level orchestration: tasks + repo graph → ``agent_lock.json``.

The compiler is the only module that owns the *what-runs-after-what* heuristics
that are not derivable from write-set overlap alone. Specifically, it injects a
"tests run last" dependency whenever a task hint declares ``tests``. Everything
else is delegated to :mod:`acg.predictor` and :mod:`acg.solver`.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

from . import __version__
from .llm import LLMProtocol, LLMUsage
from .schema import (
    AgentLock,
    ExecutionPlan,
    FileScope,
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
TEST_DIR_PREFIXES = ("tests/", "__tests__/", "cypress/", "e2e/", "spec/")
TEST_HINT_KEYWORDS = {"tests", "test", "e2e", "playwright"}


def _to_allowed_path(write: PredictedWrite) -> str:
    """Convert a single predicted write into an ``allowed_paths`` entry.

    Deep, high-confidence paths broaden to ``parent/**`` so the agent can
    create sibling files in the same feature directory. Shallow or
    low-confidence paths stay exact.
    """
    parts = write.path.split("/")
    is_test_path = any(write.path.startswith(prefix) for prefix in TEST_DIR_PREFIXES)
    min_segments = 3 if is_test_path else GLOB_BROADENING_MIN_SEGMENTS
    if write.confidence >= GLOB_BROADENING_MIN_CONFIDENCE and len(parts) >= min_segments:
        return "/".join(parts[:-1]) + "/**"
    return write.path


def _build_allowed_paths(writes: list[PredictedWrite]) -> list[str]:
    """Return a sorted, deduplicated list of glob patterns covering ``writes``."""
    seen: set[str] = set()
    for write in writes:
        seen.add(_to_allowed_path(write))
    return sorted(seen)


def _must_writes(scopes: list[FileScope]) -> list[PredictedWrite]:
    """Convert must-write scopes into legacy ``predicted_writes`` entries."""
    return [
        PredictedWrite(path=scope.path, confidence=scope.score, reason=scope.reason)
        for scope in scopes
        if scope.tier == "must_write"
    ]


def _candidate_context_paths(scopes: list[FileScope]) -> list[str]:
    """Return exact candidate-context paths, excluding hard write authority."""
    seen: set[str] = set()
    out: list[str] = []
    for scope in scopes:
        if scope.tier != "candidate_context" or scope.path in seen:
            continue
        seen.add(scope.path)
        out.append(scope.path)
    return out


def _is_test_task(task: TaskInput) -> bool:
    if not task.hints or not task.hints.touches:
        return False
    needles = {h.lower() for h in task.hints.touches}
    return bool(needles & TEST_HINT_KEYWORDS)


def _explicit_dependencies(tasks_input: list[TaskInput]) -> dict[str, list[str]]:
    """User-declared ``depends_on`` only. Cycles among these must be raised."""
    return {t.id: list(t.depends_on) for t in tasks_input}


def _heuristic_dependencies(tasks_input: list[TaskInput]) -> dict[str, list[str]]:
    """Test-after-everything heuristic: tests depend on every non-test task.

    These edges are *defeasible* — when they would create a cycle alongside
    conflict-derived edges (because a test task's predicted writes overlap a
    feature task's), the solver may collapse the cycle into a serial chain
    instead of raising. Cycles formed purely by user-declared
    :func:`_explicit_dependencies` always raise.
    """
    test_ids = {t.id for t in tasks_input if _is_test_task(t)}
    non_test_ids = [t.id for t in tasks_input if t.id not in test_ids]
    return {tid: list(non_test_ids) for tid in test_ids}


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


_DEFAULT_COMPILE_TASK_CONCURRENCY = 3


def _compile_task_concurrency() -> int:
    """Resolve the per-compile thread-pool size.

    Defaults to 3: the predictor's LLM calls are I/O-bound, so 3 threads
    typically max OpenRouter / Groq paid-tier rate limits without
    saturating CPU. Override with ``ACG_COMPILE_TASK_CONCURRENCY`` (set to
    ``1`` to force serial when debugging or hitting rate limits).
    """
    raw = os.environ.get("ACG_COMPILE_TASK_CONCURRENCY", str(_DEFAULT_COMPILE_TASK_CONCURRENCY))
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_COMPILE_TASK_CONCURRENCY
    return max(1, n)


def _predict_one_task_compile(
    ti: TaskInput,
    *,
    repo_graph: dict[str, Any],
    repo_path: Path,
    llm: LLMProtocol,
    isolated_clients: bool,
) -> tuple[TaskInput, Any, LLMUsage]:
    """Run predictor for one task (used serially or from a thread pool).

    Returns the per-task usage delta as the third tuple element so the caller
    can aggregate across threads. For the shared-client path
    (``isolated_clients=False``), the delta is taken before/after the call so
    multiple sequential invocations on the same shared client do not
    double-count.
    """
    from .llm import LLMClient
    from .predictor import predict_file_scopes_with_usage

    client: LLMProtocol = LLMClient.from_env_for_compile() if isolated_clients else llm
    # Test stubs may not implement ``usage_total`` (LLMProtocol attribute is
    # checked by Pyright but runtime stubs predate it). Fall back to a fresh
    # zeroed delta — the prediction itself is unaffected.
    usage_attr = getattr(client, "usage_total", None)
    if usage_attr is None:
        prediction = predict_file_scopes_with_usage(ti, repo_graph, client, repo_root=repo_path)
        return ti, prediction, LLMUsage()
    before = usage_attr.snapshot()
    prediction = predict_file_scopes_with_usage(ti, repo_graph, client, repo_root=repo_path)
    after = usage_attr.snapshot()
    delta = LLMUsage(
        prompt_tokens=after.prompt_tokens - before.prompt_tokens,
        completion_tokens=after.completion_tokens - before.completion_tokens,
        cost_usd=after.cost_usd - before.cost_usd,
        wall_seconds=after.wall_seconds - before.wall_seconds,
        calls=after.calls - before.calls,
        source=after.source,
    )
    return ti, prediction, delta


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
        llm: LLM client used by :mod:`acg.predictor` when
            ``ACG_COMPILE_TASK_CONCURRENCY`` is ``1`` (default). For values ``>1``,
            each thread calls :meth:`LLMClient.from_env` instead (thread-safe HTTP).

    Returns:
        A validated :class:`AgentLock` that round-trips through the JSON
        Schema in ``schema/agent_lock.schema.json``.
    """
    explicit_deps = _explicit_dependencies(tasks_input.tasks)
    heuristic_deps = _heuristic_dependencies(tasks_input.tasks)
    tasks: list[Task] = []
    scope_review_tokens_total = 0
    compile_planner_tokens_estimate = 0
    compile_usage_total = LLMUsage()
    compile_started = time.perf_counter()

    workers = _compile_task_concurrency()
    task_inputs = list(tasks_input.tasks)
    use_pool = workers > 1 and len(task_inputs) > 1
    if use_pool:
        max_workers = min(workers, len(task_inputs))
        worker = partial(
            _predict_one_task_compile,
            repo_graph=repo_graph,
            repo_path=repo_path,
            llm=llm,
            isolated_clients=True,
        )
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            predictions = list(pool.map(worker, task_inputs))
    else:
        predictions = [
            _predict_one_task_compile(
                ti,
                repo_graph=repo_graph,
                repo_path=repo_path,
                llm=llm,
                isolated_clients=False,
            )
            for ti in task_inputs
        ]

    for ti, prediction, usage_delta in predictions:
        file_scopes = prediction.scopes
        scope_review_tokens_total += prediction.scope_review_tokens
        compile_planner_tokens_estimate += prediction.planner_tokens
        compile_usage_total.add(
            prompt_tokens=usage_delta.prompt_tokens,
            completion_tokens=usage_delta.completion_tokens,
            cost_usd=usage_delta.cost_usd,
            wall_seconds=usage_delta.wall_seconds,
            source=usage_delta.source,
        )
        writes = _must_writes(file_scopes)
        allowed_paths = _build_allowed_paths(writes)
        tasks.append(
            Task(
                id=ti.id,
                prompt=ti.prompt,
                predicted_writes=writes,
                allowed_paths=allowed_paths,
                candidate_context_paths=_candidate_context_paths(file_scopes),
                file_scopes=file_scopes,
                depends_on=explicit_deps.get(ti.id, []),
            )
        )

    conflicts = detect_conflicts(tasks)
    dag = build_dag(tasks, heuristic_deps=heuristic_deps)
    groups = topological_groups(dag)

    # Stamp parallel_group onto each task for human readability.
    group_by_task: dict[str, int] = {}
    for grp in groups:
        for task_id in grp.tasks:
            group_by_task[task_id] = grp.id
    for task in tasks:
        task.parallel_group = group_by_task.get(task.id)

    use_provider = compile_usage_total.source == "provider"
    if use_provider:
        tokens_planner_total = compile_usage_total.prompt_tokens
        tokens_planner_method = "provider_usage"
    else:
        tokens_planner_total = (
            tasks_input.tokens_planner_total or 0
        ) + compile_planner_tokens_estimate
        tokens_planner_method = "estimate_chars_div_4" if tokens_planner_total else "none"
    tokens_planner_completion_total = (
        compile_usage_total.completion_tokens if use_provider else None
    )
    compile_wall_seconds = round(time.perf_counter() - compile_started, 4)
    compile_cost_usd = (
        round(compile_usage_total.cost_usd, 8)
        if use_provider and compile_usage_total.cost_usd > 0
        else None
    )

    return AgentLock(
        version="1.0",
        generated_at=AgentLock.utcnow(),
        generator=Generator(
            tool="acg",
            version=__version__,
            model=llm.model,
            tokens_planner_total=tokens_planner_total,
            tokens_scope_review_total=scope_review_tokens_total or None,
            tokens_planner_completion_total=tokens_planner_completion_total,
            tokens_planner_method=tokens_planner_method,
            compile_wall_seconds=compile_wall_seconds,
            compile_cost_usd=compile_cost_usd,
        ),
        repo=Repo(root=str(repo_path), languages=_detect_languages(repo_graph)),
        tasks=tasks,
        execution_plan=ExecutionPlan(groups=groups),
        conflicts_detected=conflicts,
    )


def rebuild_lockfile_plan(lock: AgentLock) -> None:
    """Recompute conflicts, groups, and parallel_group fields in place."""
    conflicts = detect_conflicts(lock.tasks)
    dag = build_dag(lock.tasks)
    groups = topological_groups(dag)
    group_by_task = {task_id: group.id for group in groups for task_id in group.tasks}
    for task in lock.tasks:
        task.parallel_group = group_by_task.get(task.id)
    lock.conflicts_detected = conflicts
    lock.execution_plan = ExecutionPlan(groups=groups)


def promote_candidate_paths(
    lock: AgentLock,
    task_id: str,
    paths: list[str],
    *,
    reason: str = "runtime replan approved candidate_context path",
) -> list[str]:
    """Promote candidate-context paths to hard write authority, then rebuild DAG."""
    promoted: list[str] = []
    task = next((item for item in lock.tasks if item.id == task_id), None)
    if task is None:
        return promoted
    candidate_set = set(task.candidate_context_paths)
    existing_writes = {write.path for write in task.predicted_writes}
    for raw_path in paths:
        path = raw_path.strip("./")
        if path not in candidate_set or path in existing_writes:
            continue
        scope = next((item for item in task.file_scopes if item.path == path), None)
        confidence = scope.score if scope is not None else 0.72
        task.predicted_writes.append(
            PredictedWrite(path=path, confidence=confidence, reason=reason)
        )
        if scope is not None:
            scope.tier = "must_write"
            scope.score = max(scope.score, confidence)
            signals = set(scope.signals)
            signals.add("approved_replan")
            scope.signals = sorted(signals)
            scope.reason = f"{scope.reason} Replan approved write authority."
        promoted.append(path)
        existing_writes.add(path)
    if not promoted:
        return promoted
    task.predicted_writes = sorted(
        task.predicted_writes, key=lambda write: (-write.confidence, write.path)
    )
    task.candidate_context_paths = [
        path for path in task.candidate_context_paths if path not in set(promoted)
    ]
    task.allowed_paths = _build_allowed_paths(task.predicted_writes)
    rebuild_lockfile_plan(lock)
    return promoted
