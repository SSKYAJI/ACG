"""Strategy runners that turn a lockfile + a backend into ``EvalRun`` artifacts.

Two strategies (``naive_parallel``, ``acg_planned``) crossed with four
backends (``mock``, ``local``, ``devin-manual``, ``devin-api``) all share
the same per-task data shape: see :mod:`eval_schema`.

The mock backend is the workhorse for CI and offline development. It uses
:class:`LockfileEchoMockLLM` to derive proposals from the lockfile's
``predicted_writes`` so we don't need to teach :mod:`acg.runtime`'s
canonical ``MockRuntimeLLM`` about Greenhouse-specific task ids.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any

from acg.enforce import validate_write
from acg.runtime import (
    LLMReply,
    RuntimeLLM,
    RuntimeLLMProtocol,
    WorkerResult,
    run_worker,
)
from acg.schema import AgentLock, Task

from .eval_schema import (
    BlockedWriteEvent,
    EvalModel,
    EvalRun,
    EvalTask,
    annotate_overlaps,
    compute_summary_metrics,
    make_run_id,
    now_iso,
    task_from_lock,
)

# How many predicted writes (sorted by confidence) the mock LLM echoes per task.
# Capped so that a noisy predictor doesn't drown the eval in noise.
LOCKFILE_ECHO_TOP_K = 8

# Heuristic ratio for converting prompt characters to estimated input tokens.
# Llama 3.x averages ~3.7-4.2 chars/token on English+code; 4 is a defensible
# midpoint that lets us compare strategies on the same backend without
# touching ``acg.runtime``. Devin's v3 API does not expose token counts, so
# this estimator is the only token signal available for cross-strategy
# comparison on the local backend.
_CHARS_PER_TOKEN = 4


def estimate_prompt_tokens(messages: list[dict[str, str]]) -> int:
    """Estimate input tokens for an OpenAI-style messages list.

    Sums the lengths of every message's ``content`` field and divides by
    :data:`_CHARS_PER_TOKEN`. Floors at 1 so empty prompts still count as one
    LLM round-trip.
    """
    total_chars = sum(len(m.get("content", "") or "") for m in messages)
    return max(1, total_chars // _CHARS_PER_TOKEN)


def _extract_task_id(messages: list[dict[str, str]]) -> str | None:
    """Recover the worker's task id from a ``Task id: <id>`` line in the prompt.

    Mirrors the line :func:`acg.runtime._build_worker_prompt` always emits.
    Returns ``None`` for non-worker prompts, so the caller can attribute their
    tokens to shared coordination/review overhead instead of any single task.
    """
    for m in messages:
        content = m.get("content", "") or ""
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("Task id:"):
                return stripped[len("Task id:") :].strip() or None
    return None


def _scoped_repo_graph(
    repo_graph: dict[str, Any], lock: AgentLock, task_id: str
) -> dict[str, Any]:
    """Filter ``repo_graph['files']`` to only paths inside the task's allowed_paths.

    The reduced graph is what gets handed to :func:`acg.runtime.run_worker`
    in the planned strategy, so the worker prompt only enumerates files the
    task is contractually allowed to touch. Files outside scope are removed
    using :func:`acg.enforce.validate_write` so the filter matches the
    enforcement boundary exactly.

    Other graph fields (``symbols_index``, ``imports``, etc.) are left intact
    — :func:`acg.runtime._build_worker_prompt` only consults ``files``.
    """
    files = repo_graph.get("files") or []
    scoped: list[Any] = []
    for entry in files:
        path = entry.get("path") if isinstance(entry, dict) else entry
        if not isinstance(path, str) or not path:
            continue
        allowed, _reason = validate_write(lock, task_id, path)
        if allowed:
            scoped.append(entry)
    new_graph = dict(repo_graph)
    new_graph["files"] = scoped
    return new_graph


class _PromptCountingLLM:
    """Wraps an :class:`RuntimeLLMProtocol` to estimate per-task input tokens.

    For every ``complete()`` call we sum the message-content character
    counts and convert to a token estimate via :func:`estimate_prompt_tokens`.
    The estimate is attributed to a task id when the prompt contains the
    ``Task id:`` marker (always emitted by :func:`acg.runtime._build_worker_prompt`),
    otherwise it lands in :attr:`orchestrator_tokens` for optional plan-review
    or shared coordination calls.

    The wrapper is a pure observer — it never mutates the prompt or the
    inner LLM's reply, so behavior on either backend is unchanged.
    """

    def __init__(self, inner: RuntimeLLMProtocol) -> None:
        self._inner = inner
        self.url = inner.url
        self.model = inner.model
        self.tokens_by_task: dict[str, int] = {}
        self.orchestrator_tokens: int = 0
        self.calls: int = 0

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 700,
        temperature: float = 0.2,
    ) -> LLMReply:
        est = estimate_prompt_tokens(messages)
        task_id = _extract_task_id(messages)
        if task_id is not None:
            self.tokens_by_task[task_id] = self.tokens_by_task.get(task_id, 0) + est
        else:
            self.orchestrator_tokens += est
        self.calls += 1
        return await self._inner.complete(
            messages, max_tokens=max_tokens, temperature=temperature
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


# ---------------------------------------------------------------------------
# Lockfile-aware mock LLM (Greenhouse-friendly).
# ---------------------------------------------------------------------------


class LockfileEchoMockLLM:
    """Async stub that echoes a task's lockfile ``predicted_writes`` as proposals.

    Implements the same shape as :class:`acg.runtime.RuntimeLLMProtocol` so
    it can drop into :func:`run_worker` and :func:`run_lockfile` without any
    runtime changes.

    For orchestrator calls (no ``Task id:`` substring) it returns a tiny
    ``approved=True`` JSON object so :func:`run_lockfile`'s thinking-pass
    contract stays satisfied.
    """

    def __init__(
        self,
        lock: AgentLock,
        *,
        role: str = "worker",
        top_k: int = LOCKFILE_ECHO_TOP_K,
    ) -> None:
        self.role = role
        self.url = f"mock-lockfile://{role}"
        self.model = "mock-lockfile-echo"
        self._top_k = top_k
        self._predictions: dict[str, list[dict[str, str]]] = {}
        for task in lock.tasks:
            sorted_writes = sorted(task.predicted_writes, key=lambda pw: -pw.confidence)[:top_k]
            self._predictions[task.id] = [
                {
                    "file": pw.path,
                    "description": (pw.reason or "predicted write").splitlines()[0][:160],
                }
                for pw in sorted_writes
            ]

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 700,
        temperature: float = 0.2,
    ) -> LLMReply:
        del max_tokens, temperature
        await asyncio.sleep(0)
        user_blob = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")

        # Worker path: match "Task id: <id>" emitted by acg.runtime._build_worker_prompt.
        for task_id, writes in self._predictions.items():
            if f"Task id: {task_id}" in user_blob:
                return LLMReply(
                    content=json.dumps({"writes": writes}),
                    reasoning="",
                    completion_tokens=len(writes) * 4,
                    finish_reason="stop",
                    wall_s=0.0,
                )

        # Orchestrator path (or unknown task) — emit a benign approval.
        approval = {"approved": True, "concerns": [], "dispatch_order": []}
        return LLMReply(
            content=json.dumps(approval),
            reasoning="lockfile-echo mock orchestrator",
            completion_tokens=8,
            finish_reason="stop",
            wall_s=0.0,
        )

    async def aclose(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Worker → EvalTask conversion helpers.
# ---------------------------------------------------------------------------


def _proposals_to_naive_eval_task(
    worker: WorkerResult,
    lock_task: Task,
    *,
    started_at: str,
    finished_at: str,
    prompt: str | None,
) -> EvalTask:
    """Convert a naive worker run into an :class:`EvalTask`.

    Naive mode does not enforce ``allowed_paths`` — every proposed file is
    treated as actually changed. Out-of-bounds files are scored post-hoc so
    the artifact still tells the safety story.
    """
    eval_task = task_from_lock(lock_task, prompt=prompt)
    eval_task.actual_changed_files = sorted({p.file for p in worker.proposals})
    eval_task.out_of_bounds_files = sorted({p.file for p in worker.proposals if not p.allowed})
    if worker.error:
        eval_task.status = "failed"
        eval_task.failure_reason = "AGENT_FAIL"
    elif eval_task.out_of_bounds_files:
        eval_task.status = "completed_unsafe"
    else:
        eval_task.status = "completed"
    eval_task.timestamps.started_at = started_at
    eval_task.timestamps.finished_at = finished_at
    eval_task.metrics.wall_time_seconds = round(worker.wall_s, 4)
    eval_task.metrics.tokens_completion = worker.completion_tokens or None
    eval_task.metrics.model_calls = 1
    return eval_task


def _proposals_to_planned_eval_task(
    worker: WorkerResult,
    lock_task: Task,
    *,
    started_at: str,
    finished_at: str,
    prompt: str | None,
) -> EvalTask:
    """Convert a planned worker run into an :class:`EvalTask`.

    Planned mode honors ``allowed_paths`` via :func:`acg.enforce.validate_write`
    — out-of-bounds proposals become ``BlockedWriteEvent`` entries and are
    NOT promoted to ``actual_changed_files``.
    """
    eval_task = task_from_lock(lock_task, prompt=prompt)
    eval_task.actual_changed_files = sorted({p.file for p in worker.proposals if p.allowed})
    eval_task.blocked_write_events = [
        BlockedWriteEvent(
            file=p.file,
            description=p.description,
            reason=p.reason or "outside allowed_paths",
        )
        for p in worker.proposals
        if not p.allowed
    ]
    if worker.error:
        eval_task.status = "failed"
        eval_task.failure_reason = "AGENT_FAIL"
    else:
        eval_task.status = "completed"
    eval_task.timestamps.started_at = started_at
    eval_task.timestamps.finished_at = finished_at
    eval_task.metrics.wall_time_seconds = round(worker.wall_s, 4)
    eval_task.metrics.tokens_completion = worker.completion_tokens or None
    eval_task.metrics.model_calls = 1
    return eval_task


# ---------------------------------------------------------------------------
# Naive parallel strategy.
# ---------------------------------------------------------------------------


async def _gather_capped(
    coros: list, cap_parallelism: int | None
) -> list:
    """asyncio.gather variant that bounds in-flight concurrency to ``cap``.

    ``cap_parallelism`` of ``None`` or ``<= 0`` is treated as "uncapped" and
    falls through to a plain :func:`asyncio.gather` so existing call-sites
    keep their original semantics.
    """
    if cap_parallelism is None or cap_parallelism <= 0:
        return await asyncio.gather(*coros)
    sem = asyncio.Semaphore(cap_parallelism)

    async def _bounded(coro):
        async with sem:
            return await coro

    return await asyncio.gather(*[_bounded(c) for c in coros])


async def _run_naive_parallel(
    lock: AgentLock,
    repo_graph: dict[str, Any],
    sub_factory: Callable[[], RuntimeLLMProtocol],
    *,
    prompts_by_task: dict[str, str] | None = None,
    cap_parallelism: int | None = None,
) -> tuple[list[EvalTask], float]:
    """Fan all lockfile tasks out concurrently with no coordination.

    Every worker receives the **full** ``repo_graph`` so its prompt enumerates
    the global top-K most-imported files — this is the "no contract"
    baseline. The wrapper :class:`_PromptCountingLLM` records the input-token
    estimate per task so the resulting :class:`EvalTask` artifacts carry a
    naive vs planned-comparable ``tokens_prompt`` value.

    Returns ``(tasks, wall_time_seconds)``. Wall time is the wall-clock the
    gather observed (mocks ⇒ near-zero; live LLMs ⇒ honest).
    """
    sub_inner = sub_factory()
    counting_sub = _PromptCountingLLM(sub_inner)
    started = now_iso()
    t0 = time.perf_counter()
    try:
        worker_results = await _gather_capped(
            [
                run_worker(task, lock, repo_graph, counting_sub, group_id=0)
                for task in lock.tasks
            ],
            cap_parallelism,
        )
    finally:
        await counting_sub.aclose()
    wall_s = time.perf_counter() - t0
    finished = now_iso()

    by_id = {t.id: t for t in lock.tasks}
    tasks = [
        _proposals_to_naive_eval_task(
            wr,
            by_id[wr.task_id],
            started_at=started,
            finished_at=finished,
            prompt=(prompts_by_task or {}).get(wr.task_id),
        )
        for wr in worker_results
    ]
    for et in tasks:
        et.metrics.tokens_prompt = counting_sub.tokens_by_task.get(et.task_id)
    annotate_overlaps(tasks)
    return tasks, wall_s


# ---------------------------------------------------------------------------
# ACG-planned strategy.
# ---------------------------------------------------------------------------


async def _run_acg_planned(
    lock: AgentLock,
    repo_graph: dict[str, Any],
    sub_factory: Callable[[], RuntimeLLMProtocol],
    *,
    lockfile_path: str,
    prompts_by_task: dict[str, str] | None = None,
    cap_parallelism: int | None = None,
) -> tuple[list[EvalTask], float]:
    """Walk ``execution_plan.groups`` with per-task scoped repo graphs.

    Replaces a previous :func:`acg.runtime.run_lockfile` call so we can
    inject a different ``repo_graph`` per worker invocation — the planned
    strategy's value is precisely that each worker only sees files inside
    its task's ``allowed_paths``. A normal lead/coordinator exists for both
    naive and planned strategies; this runner does not charge ACG for a
    second LLM plan-review pass by default.

    ``lockfile_path`` is accepted for signature parity with previous
    callers; the lockfile object is the source of truth so the path is
    only used in narrative output upstream.

    Returns ``(tasks, wall_time_seconds)``.
    """
    del lockfile_path  # narrative-only; the AgentLock object IS the contract

    sub_inner = sub_factory()
    counting_sub = _PromptCountingLLM(sub_inner)

    tasks_by_id = {t.id: t for t in lock.tasks}
    worker_results: list[WorkerResult] = []
    started = now_iso()
    t0 = time.perf_counter()
    try:
        # Walk execution_plan groups in dispatch order, dispatching every
        # task in a group concurrently with its own scoped repo graph.
        for group in sorted(lock.execution_plan.groups, key=lambda g: g.id):
            group_tasks = [tasks_by_id[tid] for tid in group.tasks if tid in tasks_by_id]
            if not group_tasks:
                continue
            results = await _gather_capped(
                [
                    run_worker(
                        t,
                        lock,
                        _scoped_repo_graph(repo_graph, lock, t.id),
                        counting_sub,
                        group.id,
                    )
                    for t in group_tasks
                ],
                cap_parallelism,
            )
            worker_results.extend(results)
    finally:
        await counting_sub.aclose()
    wall_s = time.perf_counter() - t0
    finished = now_iso()

    tasks: list[EvalTask] = []
    for wr in worker_results:
        if wr.task_id not in tasks_by_id:
            continue
        et = _proposals_to_planned_eval_task(
            wr,
            tasks_by_id[wr.task_id],
            started_at=started,
            finished_at=finished,
            prompt=(prompts_by_task or {}).get(wr.task_id),
        )
        et.metrics.tokens_prompt = counting_sub.tokens_by_task.get(wr.task_id)
        tasks.append(et)
    annotate_overlaps(tasks)
    return tasks, wall_s


# ---------------------------------------------------------------------------
# Backend → factory wiring.
# ---------------------------------------------------------------------------


def _mock_factory(lock: AgentLock) -> tuple[Callable[[], RuntimeLLMProtocol], EvalModel]:
    """Build a worker factory for the mock backend."""
    def sub_factory() -> RuntimeLLMProtocol:
        return LockfileEchoMockLLM(lock, role="worker")

    return sub_factory, EvalModel(provider="mock", model="lockfile-echo", url="mock://local")


def _local_factory() -> tuple[Callable[[], RuntimeLLMProtocol], EvalModel]:
    """Build a live :class:`RuntimeLLM` worker factory from ``ACG_*`` env vars."""
    from acg.runtime import RuntimeConfig

    cfg = RuntimeConfig.from_env()

    def sub_factory() -> RuntimeLLMProtocol:
        return RuntimeLLM(
            cfg.sub_url,
            cfg.sub_model,
            cfg.sub_api_key,
            timeout=cfg.request_timeout_s,
        )

    return sub_factory, EvalModel(
        provider="openai-compatible",
        model=cfg.sub_model,
        url=cfg.sub_url,
    )


# ---------------------------------------------------------------------------
# Public entry-points.
# ---------------------------------------------------------------------------


def run_strategy(
    *,
    strategy: str,
    backend: str,
    lock: AgentLock,
    repo_graph: dict[str, Any],
    lockfile_path: str,
    prompts_by_task: dict[str, str] | None = None,
    sequential_wall_time_seconds: float | None = None,
    cap_parallelism: int | None = None,
) -> EvalRun:
    """Execute one (strategy, backend) pair and return the populated :class:`EvalRun`.

    Raises ``ValueError`` for unknown strategy/backend names. The
    ``devin-manual`` and ``devin-api`` backends route through
    :mod:`devin_adapter` rather than this function — they don't go through
    :class:`RuntimeLLM`.
    """
    if strategy not in ("naive_parallel", "acg_planned"):
        raise ValueError(f"unknown strategy {strategy!r}")
    if backend not in ("mock", "local"):
        raise ValueError(
            f"backend {backend!r} not handled by run_strategy; use devin_adapter for "
            "devin-manual / devin-api."
        )

    if backend == "mock":
        sub_factory, model = _mock_factory(lock)
    else:
        sub_factory, model = _local_factory()

    orch_overhead: int | None
    if strategy == "naive_parallel":
        tasks, wall_s = asyncio.run(
            _run_naive_parallel(
                lock,
                repo_graph,
                sub_factory,
                prompts_by_task=prompts_by_task,
                cap_parallelism=cap_parallelism,
            )
        )
        orch_overhead = None
    else:
        tasks, wall_s = asyncio.run(
            _run_acg_planned(
                lock,
                repo_graph,
                sub_factory,
                lockfile_path=lockfile_path,
                prompts_by_task=prompts_by_task,
                cap_parallelism=cap_parallelism,
            )
        )
        orch_overhead = None

    summary = compute_summary_metrics(
        tasks,
        wall_time_seconds=wall_s,
        sequential_wall_time_seconds=sequential_wall_time_seconds,
        tokens_orchestrator_overhead=orch_overhead,
    )
    return EvalRun(
        run_id=make_run_id(strategy, backend),
        created_at=now_iso(),
        strategy=strategy,
        backend=backend,
        model=model,
        lockfile=lockfile_path,
        tasks=tasks,
        summary_metrics=summary,
    )


__all__ = [
    "LOCKFILE_ECHO_TOP_K",
    "LockfileEchoMockLLM",
    "run_strategy",
]
