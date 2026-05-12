"""Strategy runners that turn a lockfile + a backend into ``EvalRun`` artifacts.

The local/mock strategies (``naive_parallel``, ``naive_parallel_blind``,
``acg_planned``, and the ``acg_planned_full_context`` ablation) share the same
per-task data shape: see :mod:`eval_schema`.

The mock backend is the workhorse for CI and offline development. It uses
:class:`LockfileEchoMockLLM` to derive proposals from the lockfile's
``predicted_writes`` so we don't need to teach :mod:`acg.runtime`'s
canonical ``MockRuntimeLLM`` about Greenhouse-specific task ids.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from acg.apply_patch_adapter import apply_envelope
from acg.enforce import validate_write
from acg.runtime import (
    LLMReply,
    Proposal,
    RuntimeLLM,
    RuntimeLLMProtocol,
    WorkerResult,
    _parse_apply_envelope,
    run_worker,
)
from acg.schema import AgentLock, Task

from .eval_schema import (
    BlockedWriteEvent,
    EvalModel,
    EvalRepo,
    EvalRun,
    EvalTask,
    annotate_overlaps,
    compute_summary_metrics,
    make_run_id,
    now_iso,
    repo_from_path,
    suite_name_from_lock,
    task_from_lock,
)

# How many predicted writes (sorted by confidence) the mock LLM echoes per task.
# Capped so that a noisy predictor doesn't drown the eval in noise.
LOCKFILE_ECHO_TOP_K = 8
SINGLE_AGENT_TOP_K_FILES = 30
SINGLE_AGENT_MAX_TOKENS = 1600

SINGLE_AGENT_STRATEGY = "single_agent"
NAIVE_STRATEGY = "naive_parallel"
NAIVE_PARALLEL_BLIND_STRATEGY = "naive_parallel_blind"
ACG_PLANNED_STRATEGY = "acg_planned"
ACG_PLANNED_FULL_CONTEXT_STRATEGY = "acg_planned_full_context"
ACG_PLANNED_REPLAN_STRATEGY = "acg_planned_replan"
ACG_PLANNED_APPLIED_STRATEGY = "acg_planned_applied"
LOCAL_STRATEGIES = (
    SINGLE_AGENT_STRATEGY,
    NAIVE_STRATEGY,
    NAIVE_PARALLEL_BLIND_STRATEGY,
    ACG_PLANNED_STRATEGY,
    ACG_PLANNED_FULL_CONTEXT_STRATEGY,
    ACG_PLANNED_REPLAN_STRATEGY,
    ACG_PLANNED_APPLIED_STRATEGY,
)

# Fallback ratio for converting prompt characters to estimated input tokens.
# Llama 3.x averages ~3.7-4.2 chars/token on English+code; 4 is a defensible
# midpoint when a provider does not return ``usage.prompt_tokens``.
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


def _scoped_repo_graph(repo_graph: dict[str, Any], lock: AgentLock, task_id: str) -> dict[str, Any]:
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
    """Wraps an :class:`RuntimeLLMProtocol` to record per-task input tokens.

    For providers that return ``usage.prompt_tokens`` (OpenRouter/OpenAI-style
    responses), the provider count wins. When that field is absent we fall back
    to the previous chars/4 estimate. The count is attributed to a task id when
    the prompt contains the ``Task id:`` marker (always emitted by
    :func:`acg.runtime._build_worker_prompt`), otherwise it lands in
    :attr:`orchestrator_tokens` for optional plan-review or shared coordination
    calls.

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
        self.provider_prompt_token_calls: int = 0
        self.estimated_prompt_token_calls: int = 0

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 700,
        temperature: float = 0.2,
    ) -> LLMReply:
        est = estimate_prompt_tokens(messages)
        task_id = _extract_task_id(messages)
        reply = await self._inner.complete(messages, max_tokens=max_tokens, temperature=temperature)
        if reply.prompt_tokens is not None:
            prompt_tokens = reply.prompt_tokens
            self.provider_prompt_token_calls += 1
        else:
            prompt_tokens = est
            self.estimated_prompt_token_calls += 1
        if task_id is not None:
            self.tokens_by_task[task_id] = self.tokens_by_task.get(task_id, 0) + prompt_tokens
        else:
            self.orchestrator_tokens += prompt_tokens
        self.calls += 1
        return reply

    async def aclose(self) -> None:
        await self._inner.aclose()

    @property
    def prompt_token_method(self) -> str:
        if self.provider_prompt_token_calls and not self.estimated_prompt_token_calls:
            return "provider_usage_prompt_tokens"
        if self.provider_prompt_token_calls and self.estimated_prompt_token_calls:
            return "mixed_provider_usage_prompt_tokens_and_estimated_chars_div_4"
        return "estimated_chars_div_4"


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
        echo_write_content: bool = False,
    ) -> None:
        self.role = role
        self.url = f"mock-lockfile://{role}"
        self.model = "mock-lockfile-echo"
        self._top_k = top_k
        self._echo_write_content = echo_write_content
        self._predictions: dict[str, list[dict[str, str]]] = {}
        for task in lock.tasks:
            sorted_writes = sorted(task.predicted_writes, key=lambda pw: -pw.confidence)[:top_k]
            rows: list[dict[str, str]] = []
            for pw in sorted_writes:
                row = {
                    "file": pw.path,
                    "description": (pw.reason or "predicted write").splitlines()[0][:160],
                }
                if echo_write_content:
                    row["content"] = f"// TODO acg-applied: {task.id}\n"
                rows.append(row)
            self._predictions[task.id] = rows

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
                if self._echo_write_content:
                    parts: list[str] = []
                    for row in writes:
                        fp = row["file"]
                        parts.append(
                            f"*** Update File: {fp}\n@@\n+ // TODO acg-applied: {task_id}\n"
                        )
                    envelope = "*** Begin Patch\n" + "\n".join(parts) + "\n*** End Patch\n"
                    return LLMReply(
                        content=envelope,
                        reasoning="",
                        completion_tokens=max(8, len(envelope) // 8),
                        finish_reason="stop",
                        wall_s=0.0,
                    )
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


class NoLockSuiteMockLLM:
    """Deterministic mock for the suite-level ``single_agent`` baseline.

    It only reads file paths explicitly named in task prompts. It deliberately
    does not inspect the lockfile's predicted writes, allowed paths, execution
    plan, or candidate context, so tests can keep the baseline honest.
    """

    url = "mock://single-agent"
    model = "mock-no-lock-suite"

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = SINGLE_AGENT_MAX_TOKENS,
        temperature: float = 0.2,
    ) -> LLMReply:
        del max_tokens, temperature
        await asyncio.sleep(0)
        user_blob = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
        task_prompts: dict[str, str] = {}
        current_id: str | None = None
        for line in user_blob.splitlines():
            if line.startswith("Task id:"):
                current_id = line[len("Task id:") :].strip() or None
            elif line.startswith("Task:") and current_id:
                task_prompts[current_id] = line[len("Task:") :].strip()
                current_id = None

        tasks = []
        for task_id, prompt in task_prompts.items():
            files = _paths_named_in_prompt(prompt)
            tasks.append(
                {
                    "task_id": task_id,
                    "writes": [
                        {
                            "file": path,
                            "description": "file named in the suite task prompt",
                        }
                        for path in files
                    ],
                }
            )
        write_count = sum(len(task["writes"]) for task in tasks)
        return LLMReply(
            content=json.dumps({"tasks": tasks}),
            reasoning="",
            completion_tokens=max(8, write_count * 4),
            finish_reason="stop",
            wall_s=0.0,
        )

    async def aclose(self) -> None:
        return None


_PROMPT_PATH_RE = re.compile(r"(?<![\w./-])pom\.xml(?![\w./-])|(?:[\w.-]+/)+[\w.\-\[\]@]+")


def _paths_named_in_prompt(prompt: str) -> list[str]:
    """Return repo-relative-looking file paths explicitly named in ``prompt``."""
    seen: set[str] = set()
    paths: list[str] = []
    for match in _PROMPT_PATH_RE.finditer(prompt):
        path = match.group(0).strip().strip("`'\".,;:)")
        if not path or path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


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
    eval_task.metrics.cost_usd = worker.cost_usd
    eval_task.metrics.cost_source = worker.cost_source
    eval_task.metrics.model_calls = 1
    return eval_task


def _proposals_to_naive_applied_eval_task(
    worker: WorkerResult,
    lock_task: Task,
    lock: AgentLock,
    *,
    started_at: str,
    finished_at: str,
    prompt: str | None,
    git_changed_files: list[str],
) -> EvalTask:
    """Naive parallel + real git writes — ``actual_changed_files`` come from ``git diff``."""
    eval_task = task_from_lock(lock_task, prompt=prompt)
    eval_task.actual_changed_files = sorted(git_changed_files)
    eval_task.actual_changed_files_kind = "applied_diff"
    eval_task.out_of_bounds_files = sorted(
        {
            p
            for p in git_changed_files
            if not validate_write(lock, lock_task.id, p)[0]
        }
    )
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
    elif not eval_task.actual_changed_files:
        if not worker.proposals or all(p.envelope is None for p in worker.proposals):
            eval_task.status = "failed"
            eval_task.failure_reason = "NO_APPLIED_CONTENT"
        elif eval_task.blocked_write_events:
            eval_task.status = "blocked"
            eval_task.failure_reason = "BLOCKED_BY_SCOPE"
        else:
            eval_task.status = "failed"
            eval_task.failure_reason = "EMPTY_PATCH"
    elif eval_task.out_of_bounds_files:
        eval_task.status = "completed_unsafe"
        eval_task.failure_reason = None
    else:
        eval_task.status = "completed"
    eval_task.timestamps.started_at = started_at
    eval_task.timestamps.finished_at = finished_at
    eval_task.metrics.wall_time_seconds = round(worker.wall_s, 4)
    eval_task.metrics.tokens_completion = worker.completion_tokens or None
    eval_task.metrics.cost_usd = worker.cost_usd
    eval_task.metrics.cost_source = worker.cost_source
    eval_task.metrics.model_calls = 1
    return eval_task


def _proposals_to_suite_applied_eval_task(
    worker: WorkerResult,
    lock_task: Task,
    lock: AgentLock,
    *,
    started_at: str,
    finished_at: str,
    prompt: str | None,
    git_changed_files: list[str],
) -> EvalTask:
    """Single-agent applied mode — git-derived files with suite-level envelope parsing."""
    eval_task = task_from_lock(lock_task, prompt=prompt)
    eval_task.actual_changed_files = sorted(git_changed_files)
    eval_task.actual_changed_files_kind = "suite_applied_diff"
    eval_task.out_of_bounds_files = sorted(
        {
            p
            for p in git_changed_files
            if not validate_write(lock, lock_task.id, p)[0]
        }
    )
    eval_task.blocked_write_events = []
    if worker.error:
        eval_task.status = "failed"
        eval_task.failure_reason = "AGENT_FAIL"
    elif not eval_task.actual_changed_files:
        if not worker.proposals or all(p.envelope is None for p in worker.proposals):
            eval_task.status = "failed"
            eval_task.failure_reason = "NO_APPLIED_CONTENT"
        else:
            eval_task.status = "failed"
            eval_task.failure_reason = "EMPTY_PATCH"
    elif eval_task.out_of_bounds_files:
        eval_task.status = "completed_unsafe"
        eval_task.failure_reason = None
    else:
        eval_task.status = "completed"
    eval_task.timestamps.started_at = started_at
    eval_task.timestamps.finished_at = finished_at
    eval_task.metrics.wall_time_seconds = round(worker.wall_s, 4)
    eval_task.metrics.tokens_completion = worker.completion_tokens or None
    eval_task.metrics.cost_usd = worker.cost_usd
    eval_task.metrics.cost_source = worker.cost_source
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

    Status assignment:

    * ``failed`` — the worker raised (``worker.error`` is set).
    * ``blocked`` — the worker proposed only out-of-scope paths
      (zero ``actual_changed_files`` AND at least one ``blocked_write_events``
      entry). ``failure_reason`` is ``BLOCKED_BY_SCOPE``. These tasks must
      not count as ``completed`` in the summary metrics.
    * ``completed`` — the worker produced at least one accepted proposal
      (partial blocks are still ``completed``; the burden metric still
      records the rejected events).
    """
    eval_task = task_from_lock(lock_task, prompt=prompt)
    eval_task.actual_changed_files = sorted({p.file for p in worker.proposals if p.allowed})
    eval_task.approved_replan_files = sorted(
        {p.file for p in worker.proposals if p.scope_status == "approved_replan"}
    )
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
    elif not eval_task.actual_changed_files and eval_task.blocked_write_events:
        eval_task.status = "blocked"
        eval_task.failure_reason = "BLOCKED_BY_SCOPE"
    else:
        eval_task.status = "completed"
    eval_task.timestamps.started_at = started_at
    eval_task.timestamps.finished_at = finished_at
    eval_task.metrics.wall_time_seconds = round(worker.wall_s, 4)
    eval_task.metrics.tokens_completion = worker.completion_tokens or None
    eval_task.metrics.cost_usd = worker.cost_usd
    eval_task.metrics.cost_source = worker.cost_source
    eval_task.metrics.model_calls = 1
    return eval_task


def _proposals_to_planned_applied_eval_task(
    worker: WorkerResult,
    lock_task: Task,
    *,
    started_at: str,
    finished_at: str,
    prompt: str | None,
    git_changed_files: list[str],
) -> EvalTask:
    """Like :func:`_proposals_to_planned_eval_task` but ``actual_changed_files`` is git-derived."""
    eval_task = task_from_lock(lock_task, prompt=prompt)
    eval_task.actual_changed_files = sorted(git_changed_files)
    eval_task.actual_changed_files_kind = "applied_diff"
    eval_task.approved_replan_files = sorted(
        {p.file for p in worker.proposals if p.scope_status == "approved_replan"}
    )
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
    elif not eval_task.actual_changed_files:
        if not worker.proposals or all(p.envelope is None for p in worker.proposals):
            eval_task.status = "failed"
            eval_task.failure_reason = "NO_APPLIED_CONTENT"
        elif eval_task.blocked_write_events:
            eval_task.status = "blocked"
            eval_task.failure_reason = "BLOCKED_BY_SCOPE"
        else:
            eval_task.status = "failed"
            eval_task.failure_reason = "EMPTY_PATCH"
    else:
        eval_task.status = "completed"
    eval_task.timestamps.started_at = started_at
    eval_task.timestamps.finished_at = finished_at
    eval_task.metrics.wall_time_seconds = round(worker.wall_s, 4)
    eval_task.metrics.tokens_completion = worker.completion_tokens or None
    eval_task.metrics.cost_usd = worker.cost_usd
    eval_task.metrics.cost_source = worker.cost_source
    eval_task.metrics.model_calls = 1
    return eval_task


def _sanitize_applied_branch_task_id(task_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in task_id)


def _git_identity_args() -> list[str]:
    return [
        "-c",
        "user.name=ACG Eval",
        "-c",
        "user.email=acg-eval@users.noreply.localhost",
    ]


def _apply_writes_git_sync(
    checkout: Path,
    base_sha: str,
    lock: AgentLock,
    task: Task,
    wr: WorkerResult,
    *,
    require_scope: bool = True,
) -> list[str]:
    """Create ``acg-applied/<task>`` from ``base_sha``, apply envelopes, commit, return diff names."""
    repo = str(checkout.resolve())
    branch = f"acg-applied/{_sanitize_applied_branch_task_id(task.id)}"
    subprocess.run(
        ["git", "-C", repo, "branch", "-f", branch, base_sha],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "-C", repo, "checkout", "--quiet", branch],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        root = checkout.resolve()
        for prop in wr.proposals:
            if require_scope and not prop.allowed:
                continue
            if prop.envelope is not None:
                if require_scope:
                    allowed, _reason = validate_write(lock, task.id, prop.file)
                    if not allowed:
                        continue
                apply_envelope(prop.envelope, root)
            elif prop.content is not None:
                if require_scope:
                    allowed, _reason = validate_write(lock, task.id, prop.file)
                    if not allowed:
                        continue
                rel = prop.file.lstrip("./")
                dest = (checkout / rel).resolve()
                try:
                    dest.relative_to(root)
                except ValueError:
                    continue
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(prop.content, encoding="utf-8")
        subprocess.run(["git", "-C", repo, "add", "-A"], check=True, capture_output=True, text=True)
        st = subprocess.run(
            ["git", "-C", repo, "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        )
        if st.stdout.strip():
            subprocess.run(
                [
                    "git",
                    "-C",
                    repo,
                    *_git_identity_args(),
                    "commit",
                    "-m",
                    f"acg(applied): {task.id}",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        diff = subprocess.run(
            ["git", "-C", repo, "diff", "--name-only", base_sha, branch],
            check=True,
            capture_output=True,
            text=True,
        )
        names = [line.strip() for line in diff.stdout.splitlines() if line.strip()]
        return sorted(set(names))
    finally:
        subprocess.run(
            ["git", "-C", repo, "checkout", "--quiet", "--detach", base_sha],
            check=True,
            capture_output=True,
            text=True,
        )




# ---------------------------------------------------------------------------
# Naive parallel strategy.
# ---------------------------------------------------------------------------


async def _gather_capped(coros: list, cap_parallelism: int | None) -> list:
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
) -> tuple[list[EvalTask], float, str]:
    """Fan all lockfile tasks out concurrently with no coordination.

    Every worker receives the **full** ``repo_graph`` so its prompt enumerates
    the global top-K most-imported files — this is the "no contract"
    baseline. The wrapper :class:`_PromptCountingLLM` records the input-token
    estimate per task so the resulting :class:`EvalTask` artifacts carry a
    naive vs planned-comparable ``tokens_prompt`` value.

    Returns ``(tasks, wall_time_seconds, prompt_token_method)``. Wall time is
    the wall-clock the gather observed (mocks ⇒ near-zero; live LLMs ⇒ honest).
    """
    sub_inner = sub_factory()
    counting_sub = _PromptCountingLLM(sub_inner)
    started = now_iso()
    t0 = time.perf_counter()
    from acg.runtime import RuntimeConfig

    naive_runtime_config = RuntimeConfig.from_env()
    naive_runtime_config.auto_replan = False
    try:
        worker_results = await _gather_capped(
            [
                run_worker(
                    task,
                    lock,
                    repo_graph,
                    counting_sub,
                    group_id=0,
                    config=naive_runtime_config,
                )
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
    return tasks, wall_s, counting_sub.prompt_token_method


async def _run_naive_parallel_blind(
    lock: AgentLock,
    repo_graph: dict[str, Any],
    sub_factory: Callable[[], RuntimeLLMProtocol],
    *,
    prompts_by_task: dict[str, str] | None = None,
    cap_parallelism: int | None = None,
) -> tuple[list[EvalTask], float, str]:
    """Per-task workers with NO predictor output in the prompt and no scope guard.

    This is the true blind baseline; contrast with :func:`_run_naive_parallel`,
    which shares the worker prompt template with :func:`_run_acg_planned` and
    therefore consumes ``predicted_writes``/``candidate_context`` for free.
    """
    sub_inner = sub_factory()
    counting_sub = _PromptCountingLLM(sub_inner)
    started = now_iso()
    t0 = time.perf_counter()
    from acg.runtime import RuntimeConfig

    naive_runtime_config = RuntimeConfig.from_env()
    naive_runtime_config.auto_replan = False
    try:
        worker_results = await _gather_capped(
            [
                run_worker(
                    task,
                    lock,
                    repo_graph,
                    counting_sub,
                    group_id=0,
                    config=naive_runtime_config,
                    include_lockfile_hints=False,
                )
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
        et.actual_changed_files_kind = "naive_parallel_blind_proposed_write_set"
    annotate_overlaps(tasks)
    return tasks, wall_s, counting_sub.prompt_token_method


async def _run_naive_parallel_applied(
    lock: AgentLock,
    repo_graph: dict[str, Any],
    sub_factory: Callable[[], RuntimeLLMProtocol],
    checkout_path: Path,
    *,
    prompts_by_task: dict[str, str] | None = None,
    cap_parallelism: int | None = None,
) -> tuple[list[EvalTask], float, str]:
    """Naive workers + real git writes (no scope gate on apply)."""
    checkout = checkout_path.resolve()
    probe = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        raise ValueError(f"applied branch writes require a git checkout: {checkout}")
    if lock.repo and (lock.repo.commit or "").strip():
        pin = lock.repo.commit.strip()
        base_sha = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "--verify", pin],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    else:
        base_sha = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    sub_inner = sub_factory()
    counting_sub = _PromptCountingLLM(sub_inner)
    started = now_iso()
    t0 = time.perf_counter()
    from acg.runtime import RuntimeConfig

    naive_runtime_config = RuntimeConfig.from_env()
    naive_runtime_config.auto_replan = False
    tasks_by_id = {t.id: t for t in lock.tasks}
    changed_by_task: dict[str, list[str]] = {}
    git_lock = asyncio.Lock()
    try:
        worker_results = await _gather_capped(
            [
                run_worker(
                    task,
                    lock,
                    repo_graph,
                    counting_sub,
                    group_id=0,
                    config=naive_runtime_config,
                )
                for task in lock.tasks
            ],
            cap_parallelism,
        )
        for wr in worker_results:
            task = tasks_by_id.get(wr.task_id)
            if task is None:
                continue
            async with git_lock:
                names = await asyncio.to_thread(
                    _apply_writes_git_sync,
                    checkout,
                    base_sha,
                    lock,
                    task,
                    wr,
                    require_scope=False,
                )
            changed_by_task[wr.task_id] = names
    finally:
        await counting_sub.aclose()
    wall_s = time.perf_counter() - t0
    finished = now_iso()

    tasks = [
        _proposals_to_naive_applied_eval_task(
            wr,
            tasks_by_id[wr.task_id],
            lock,
            started_at=started,
            finished_at=finished,
            prompt=(prompts_by_task or {}).get(wr.task_id),
            git_changed_files=changed_by_task.get(wr.task_id, []),
        )
        for wr in worker_results
        if wr.task_id in tasks_by_id
    ]
    for et in tasks:
        et.metrics.tokens_prompt = counting_sub.tokens_by_task.get(et.task_id)
    annotate_overlaps(tasks)
    return tasks, wall_s, counting_sub.prompt_token_method


# ---------------------------------------------------------------------------
# Suite-level no-lock single-agent strategy.
# ---------------------------------------------------------------------------


def _repo_graph_file_paths(
    repo_graph: dict[str, Any], *, k: int = SINGLE_AGENT_TOP_K_FILES
) -> list[str]:
    files = repo_graph.get("files") or []
    if not isinstance(files, list):
        return []
    scored: list[tuple[int, str]] = []
    for entry in files:
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if not isinstance(path, str) or not path:
            continue
        score = entry.get("imported_by_count", entry.get("import_fan_in", 0)) or 0
        try:
            score_int = int(score)
        except (TypeError, ValueError):
            score_int = 0
        scored.append((-score_int, path))
    return [path for _, path in sorted(scored)[:k]]


def _build_single_agent_prompt(
    lock: AgentLock,
    repo_graph: dict[str, Any],
    *,
    prompts_by_task: dict[str, str] | None = None,
    apply_patch_suites: bool = False,
) -> list[dict[str, str]]:
    files = _repo_graph_file_paths(repo_graph)
    file_block = "\n".join(f"  - {p}" for p in files) or "  (graph empty)"
    task_blocks = []
    for task in lock.tasks:
        task_blocks.append(
            f"Task id: {task.id}\nTask: {(prompts_by_task or {}).get(task.id, task.prompt)}"
        )
    if apply_patch_suites:
        system = (
            "You are a single coding agent handling an entire task suite without "
            "a precomputed file contract. You must implement every task on disk.\n\n"
            "Choose exactly ONE response format:\n\n"
            "A) OpenAI apply_patch layout: for each task, emit a `Task id: <id>` line "
            "copied from the list below, followed only by a `*** Begin Patch` … "
            "`*** End Patch` envelope for that task (no code fences, no JSON).\n\n"
            "B) Legacy JSON object with key \"tasks\": an array of objects. Each object "
            "must have \"task_id\" and \"writes\"; every write object must include "
            "\"file\", \"description\", and a string \"content\" field whose value is "
            "the complete UTF-8 file body to write.\n\n"
            "Do not mix formats. If you choose JSON, partial file bodies are invalid."
        )
    else:
        system = (
            "You are a single coding agent handling an entire task suite without "
            "a precomputed file contract. Output ONLY a JSON object with key \"tasks\": an "
            "array of objects. Each object must have \"task_id\" and \"writes\"; "
            "\"writes\" is an array of objects with \"file\" and \"description\". "
            "Do not include prose, code fences, or contract-derived fields."
        )
    task_join = "\n\n".join(task_blocks)
    user = (
        "Single-agent no-lock suite.\n"
        "Use only these task descriptions and the repo file list below.\n\n"
        "Suite tasks:\n"
        f"{task_join}\n\n"
        f"Available files in this repo (top {len(files)} by importance):\n"
        f"{file_block}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _task_mentions_path(task: Task, path: str) -> bool:
    n = path.lstrip("./")
    if any(pw.path.lstrip("./") == n for pw in task.predicted_writes):
        return True
    return n in {c.lstrip("./") for c in task.candidate_context_paths}


def _pick_task_for_patch_block(lock: AgentLock, block: str) -> str:
    paths = re.findall(r"(?m)^\*\*\* (?:Update|Add|Delete) File: (.+?)\s*$", block)
    paths = [p.strip() for p in paths]
    if not paths or not lock.tasks:
        return lock.tasks[0].id if lock.tasks else ""
    for task in lock.tasks:
        if all(_task_mentions_path(task, p) for p in paths):
            return task.id
    for task in lock.tasks:
        if any(_task_mentions_path(task, p) for p in paths):
            return task.id
    return lock.tasks[0].id


def _parse_single_agent_applied_sections(raw: str) -> dict[str, str]:
    text = (raw or "").strip()
    if not text or "Task id:" not in text:
        return {}
    sections: dict[str, str] = {}
    pattern = re.compile(r"(?m)^Task id:\s*([^\s]+)\s*$")
    matches = list(pattern.finditer(text))
    if not matches:
        return {}
    for i, m in enumerate(matches):
        tid = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        if chunk:
            sections[tid] = chunk
    return sections


def _parse_single_agent_applied_mega(raw: str, lock: AgentLock) -> dict[str, str]:
    chunks_by_task: dict[str, list[str]] = {t.id: [] for t in lock.tasks}
    if not lock.tasks:
        return {}
    for block in re.findall(r"\*\*\* Begin Patch[\s\S]*?\*\*\* End Patch", raw or ""):
        b = block.strip()
        if not b:
            continue
        tid = _pick_task_for_patch_block(lock, b)
        chunks_by_task.setdefault(tid, []).append(b)
    return {tid: "\n\n".join(chs).strip() for tid, chs in chunks_by_task.items() if chs}


def _parse_single_agent_applied_envelopes(raw: str, lock: AgentLock) -> dict[str, str]:
    by_section = _parse_single_agent_applied_sections(raw)
    if by_section:
        return by_section
    return _parse_single_agent_applied_mega(raw, lock)


def _proposals_for_task_envelope_blob(blob: str) -> list[Proposal]:
    return [
        Proposal(
            file=r["file"],
            description=str(r.get("description", "")),
            allowed=True,
            reason=None,
            scope_status="allowed",
            content=None,
            envelope=r["envelope"],
        )
        for r in _parse_apply_envelope(blob or "")
    ]


def _jsonish_payload(raw: str) -> Any:
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for opener, closer in (("{", "}"), ("[", "]")):
            start = text.find(opener)
            end = text.rfind(closer)
            if start == -1 or end == -1 or end <= start:
                continue
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


def _coerce_write_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            file_path = item
            description = ""
        elif isinstance(item, dict):
            file_path = item.get("file") or item.get("path")
            description = item.get("description") or item.get("reason") or ""
        else:
            continue
        if not isinstance(file_path, str) or not file_path.strip():
            continue
        if not isinstance(description, str):
            description = str(description)
        row: dict[str, Any] = {
            "file": file_path.strip().lstrip("./"),
            "description": description.strip(),
        }
        if isinstance(item, dict):
            wc = item.get("content")
            if isinstance(wc, str):
                row["content"] = wc
        out.append(row)
    return out


def _parse_single_agent_task_writes(
    raw: str, task_ids: set[str]
) -> dict[str, list[dict[str, Any]]]:
    payload = _jsonish_payload(raw)
    parsed: dict[str, list[dict[str, Any]]] = {task_id: [] for task_id in task_ids}
    if isinstance(payload, dict):
        tasks_payload = payload.get("tasks")
        if isinstance(tasks_payload, list):
            for item in tasks_payload:
                if not isinstance(item, dict):
                    continue
                task_id = item.get("task_id") or item.get("id")
                if not isinstance(task_id, str) or task_id not in task_ids:
                    continue
                writes = _coerce_write_items(
                    item.get("writes")
                    or item.get("actual_changed_files")
                    or item.get("files")
                    or []
                )
                parsed[task_id].extend(writes)
        writes_by_task = payload.get("writes_by_task") or payload.get("tasks_by_id")
        if isinstance(writes_by_task, dict):
            for task_id, writes_payload in writes_by_task.items():
                if task_id in task_ids:
                    parsed[task_id].extend(_coerce_write_items(writes_payload))
        for task_id in task_ids:
            if task_id in payload:
                parsed[task_id].extend(_coerce_write_items(payload[task_id]))
    elif isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            task_id = item.get("task_id") or item.get("id")
            if isinstance(task_id, str) and task_id in task_ids:
                parsed[task_id].extend(_coerce_write_items(item.get("writes") or []))

    for task_id, writes in parsed.items():
        seen: set[str] = set()
        deduped: list[dict[str, str]] = []
        for write in writes:
            path = write["file"]
            if path in seen:
                continue
            seen.add(path)
            deduped.append(write)
        parsed[task_id] = deduped
    return parsed


async def _run_single_agent(
    lock: AgentLock,
    repo_graph: dict[str, Any],
    sub_factory: Callable[[], RuntimeLLMProtocol],
    *,
    prompts_by_task: dict[str, str] | None = None,
) -> tuple[list[EvalTask], float, str]:
    """Run one suite-level agent with no lockfile write contract in prompt."""
    llm = sub_factory()
    messages = _build_single_agent_prompt(
        lock,
        repo_graph,
        prompts_by_task=prompts_by_task,
    )
    started = now_iso()
    t0 = time.perf_counter()
    reply: LLMReply | None = None
    error: str | None = None
    try:
        reply = await llm.complete(messages, max_tokens=SINGLE_AGENT_MAX_TOKENS)
    except Exception as exc:  # pragma: no cover - exercised by live backends.
        error = str(exc)
    finally:
        await llm.aclose()
    wall_s = time.perf_counter() - t0
    finished = now_iso()

    prompt_tokens: int | None = None
    prompt_method = "estimated_chars_div_4"
    if reply is not None and reply.prompt_tokens is not None:
        prompt_tokens = reply.prompt_tokens
        prompt_method = "provider_usage_prompt_tokens"
    elif reply is not None:
        prompt_tokens = estimate_prompt_tokens(messages)
    parsed = (
        _parse_single_agent_task_writes(reply.content, {task.id for task in lock.tasks})
        if reply is not None
        else {}
    )

    tasks: list[EvalTask] = []
    for index, lock_task in enumerate(lock.tasks):
        eval_task = EvalTask(
            task_id=lock_task.id,
            prompt=(prompts_by_task or {}).get(lock_task.id, lock_task.prompt),
            actual_changed_files_kind="suite_proposed_write_set",
        )
        eval_task.actual_changed_files = sorted(
            {write["file"] for write in parsed.get(lock_task.id, [])}
        )
        eval_task.status = "failed" if error else "completed"
        eval_task.failure_reason = "AGENT_FAIL" if error else None
        eval_task.timestamps.started_at = started
        eval_task.timestamps.finished_at = finished
        eval_task.metrics.wall_time_seconds = round(wall_s if index == 0 else 0.0, 4)
        eval_task.metrics.model_calls = 1 if index == 0 else 0
        if index == 0 and reply is not None:
            eval_task.metrics.tokens_prompt = prompt_tokens
            eval_task.metrics.tokens_completion = reply.completion_tokens or None
            eval_task.metrics.cost_usd = reply.cost_usd
            eval_task.metrics.cost_source = reply.cost_source
        tasks.append(eval_task)
    annotate_overlaps(tasks)
    return tasks, wall_s, prompt_method


async def _run_single_agent_applied(
    lock: AgentLock,
    repo_graph: dict[str, Any],
    sub_factory: Callable[[], RuntimeLLMProtocol],
    checkout_path: Path,
    *,
    prompts_by_task: dict[str, str] | None = None,
) -> tuple[list[EvalTask], float, str]:
    """Suite-level agent whose reply is split per task and applied as git branches."""
    checkout = checkout_path.resolve()
    probe = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        raise ValueError(f"applied branch writes require a git checkout: {checkout}")
    if lock.repo and (lock.repo.commit or "").strip():
        pin = lock.repo.commit.strip()
        base_sha = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "--verify", pin],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    else:
        base_sha = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    llm = sub_factory()
    messages = _build_single_agent_prompt(
        lock,
        repo_graph,
        prompts_by_task=prompts_by_task,
        apply_patch_suites=True,
    )
    started = now_iso()
    t0 = time.perf_counter()
    reply: LLMReply | None = None
    error: str | None = None
    try:
        reply = await llm.complete(messages, max_tokens=max(16000, SINGLE_AGENT_MAX_TOKENS))
    except Exception as exc:  # pragma: no cover - exercised by live backends.
        error = str(exc)
    finally:
        await llm.aclose()
    wall_s = time.perf_counter() - t0
    finished = now_iso()

    prompt_tokens: int | None = None
    prompt_method = "estimated_chars_div_4"
    if reply is not None and reply.prompt_tokens is not None:
        prompt_tokens = reply.prompt_tokens
        prompt_method = "provider_usage_prompt_tokens"
    elif reply is not None:
        prompt_tokens = estimate_prompt_tokens(messages)

    if reply is not None and not error:
        envelopes_by_task = _parse_single_agent_applied_envelopes(reply.content, lock)
        parsed_writes = _parse_single_agent_task_writes(
            reply.content, {t.id for t in lock.tasks}
        )
    else:
        envelopes_by_task = {}
        parsed_writes = {}

    workers_by_task: dict[str, WorkerResult] = {}
    for task in lock.tasks:
        blob = envelopes_by_task.get(task.id, "")
        proposals = _proposals_for_task_envelope_blob(blob)
        if not proposals:
            for row in parsed_writes.get(task.id, []):
                proposals.append(
                    Proposal(
                        file=row["file"],
                        description=row.get("description", ""),
                        allowed=True,
                        reason=None,
                        scope_status="allowed",
                        content=row.get("content") if isinstance(row.get("content"), str) else None,
                        envelope=None,
                    )
                )
        workers_by_task[task.id] = WorkerResult(
            task_id=task.id,
            group_id=0,
            url=llm.url,
            model=llm.model,
            wall_s=reply.wall_s if reply is not None else 0.0,
            completion_tokens=(reply.completion_tokens if reply is not None else 0) or 0,
            finish_reason=(reply.finish_reason if reply is not None else "error"),
            raw_content=blob,
            proposals=proposals,
            allowed_count=sum(1 for p in proposals if p.allowed),
            blocked_count=sum(1 for p in proposals if not p.allowed),
            error=error,
            prompt_tokens=reply.prompt_tokens if reply is not None else None,
            cost_usd=reply.cost_usd if reply is not None else None,
            cost_source=reply.cost_source if reply is not None else None,
        )

    changed_by_task: dict[str, list[str]] = {}
    git_lock = asyncio.Lock()

    async def _apply_one(task_id: str) -> None:
        wr = workers_by_task[task_id]
        task = next(t for t in lock.tasks if t.id == task_id)
        async with git_lock:
            names = await asyncio.to_thread(
                _apply_writes_git_sync,
                checkout,
                base_sha,
                lock,
                task,
                wr,
                require_scope=False,
            )
        changed_by_task[task_id] = names

    if not error:
        for task in lock.tasks:
            await _apply_one(task.id)

    tasks_out: list[EvalTask] = []
    for index, lock_task in enumerate(lock.tasks):
        wr = workers_by_task[lock_task.id]
        eval_task = _proposals_to_suite_applied_eval_task(
            wr,
            lock_task,
            lock,
            started_at=started,
            finished_at=finished,
            prompt=(prompts_by_task or {}).get(lock_task.id, lock_task.prompt),
            git_changed_files=changed_by_task.get(lock_task.id, []),
        )
        eval_task.metrics.wall_time_seconds = round(wall_s if index == 0 else 0.0, 4)
        eval_task.metrics.model_calls = 1 if index == 0 else 0
        if index == 0 and reply is not None:
            eval_task.metrics.tokens_prompt = prompt_tokens
            eval_task.metrics.tokens_completion = reply.completion_tokens or None
            eval_task.metrics.cost_usd = reply.cost_usd
            eval_task.metrics.cost_source = reply.cost_source
        tasks_out.append(eval_task)
    annotate_overlaps(tasks_out)
    return tasks_out, wall_s, prompt_method


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
    scope_repo_graph: bool = True,
    auto_replan: bool = False,
) -> tuple[list[EvalTask], float, str]:
    """Walk ``execution_plan.groups`` with optional per-task scoped repo graphs.

    Replaces a previous :func:`acg.runtime.run_lockfile` call so we can
    inject a different ``repo_graph`` per worker invocation — the planned
    strategy's value is precisely that each worker only sees files inside
    its task's ``allowed_paths``. The ``acg_planned_full_context`` ablation
    sets ``scope_repo_graph=False`` so it keeps the same serialized schedule
    but gives every worker the full repo graph.

    A normal lead/coordinator exists for both naive and planned strategies;
    this runner does not charge ACG for a second LLM plan-review pass by
    default.

    ``lockfile_path`` is accepted for signature parity with previous
    callers; the lockfile object is the source of truth so the path is
    only used in narrative output upstream.

    Returns ``(tasks, wall_time_seconds, prompt_token_method)``.
    """
    del lockfile_path  # narrative-only; the AgentLock object IS the contract

    sub_inner = sub_factory()
    counting_sub = _PromptCountingLLM(sub_inner)
    from acg.runtime import RuntimeConfig

    runtime_config = RuntimeConfig.from_env()
    runtime_config.auto_replan = auto_replan

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
            coros = []
            for task in group_tasks:
                task_graph = (
                    _scoped_repo_graph(repo_graph, lock, task.id)
                    if scope_repo_graph
                    else repo_graph
                )
                coros.append(
                    run_worker(
                        task,
                        lock,
                        task_graph,
                        counting_sub,
                        group.id,
                        config=runtime_config,
                    )
                )
            results = await _gather_capped(coros, cap_parallelism)
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
    return tasks, wall_s, counting_sub.prompt_token_method


async def _run_acg_planned_applied(
    lock: AgentLock,
    repo_graph: dict[str, Any],
    sub_factory: Callable[[], RuntimeLLMProtocol],
    *,
    checkout_path: Path,
    lockfile_path: str,
    prompts_by_task: dict[str, str] | None = None,
    cap_parallelism: int | None = None,
    scope_repo_graph: bool = True,
    auto_replan: bool = False,
) -> tuple[list[EvalTask], float, str]:
    """Planned workers + git-backed writes for proposals that carry apply_patch envelopes."""
    del lockfile_path
    checkout = checkout_path.resolve()
    probe = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0 or probe.stdout.strip() != "true":
        raise ValueError(f"applied branch writes require a git checkout: {checkout}")
    if lock.repo and (lock.repo.commit or "").strip():
        pin = lock.repo.commit.strip()
        base_sha = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "--verify", pin],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    else:
        base_sha = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    sub_inner = sub_factory()
    counting_sub = _PromptCountingLLM(sub_inner)
    from acg.runtime import RuntimeConfig

    runtime_config = RuntimeConfig.from_env()
    runtime_config.auto_replan = auto_replan

    tasks_by_id = {t.id: t for t in lock.tasks}
    worker_results: list[WorkerResult] = []
    changed_by_task: dict[str, list[str]] = {}
    git_lock = asyncio.Lock()
    started = now_iso()
    t0 = time.perf_counter()

    async def _apply_one(wr: WorkerResult) -> None:
        task = tasks_by_id.get(wr.task_id)
        if task is None:
            return
        async with git_lock:
            names = await asyncio.to_thread(
                _apply_writes_git_sync,
                checkout,
                base_sha,
                lock,
                task,
                wr,
                require_scope=True,
            )
        changed_by_task[wr.task_id] = names

    try:
        for group in sorted(lock.execution_plan.groups, key=lambda g: g.id):
            group_tasks = [tasks_by_id[tid] for tid in group.tasks if tid in tasks_by_id]
            if not group_tasks:
                continue
            coros = []
            for task in group_tasks:
                task_graph = (
                    _scoped_repo_graph(repo_graph, lock, task.id)
                    if scope_repo_graph
                    else repo_graph
                )
                coros.append(
                    run_worker(
                        task,
                        lock,
                        task_graph,
                        counting_sub,
                        group.id,
                        config=runtime_config,
                    )
                )
            results = await _gather_capped(coros, cap_parallelism)
            for wr in results:
                await _apply_one(wr)
            worker_results.extend(results)
    finally:
        await counting_sub.aclose()
    wall_s = time.perf_counter() - t0
    finished = now_iso()

    tasks: list[EvalTask] = []
    for wr in worker_results:
        if wr.task_id not in tasks_by_id:
            continue
        et = _proposals_to_planned_applied_eval_task(
            wr,
            tasks_by_id[wr.task_id],
            started_at=started,
            finished_at=finished,
            prompt=(prompts_by_task or {}).get(wr.task_id),
            git_changed_files=changed_by_task.get(wr.task_id, []),
        )
        et.metrics.tokens_prompt = counting_sub.tokens_by_task.get(wr.task_id)
        tasks.append(et)
    annotate_overlaps(tasks)
    return tasks, wall_s, counting_sub.prompt_token_method


# ---------------------------------------------------------------------------
# Backend → factory wiring.
# ---------------------------------------------------------------------------


def _mock_factory(
    lock: AgentLock, *, echo_write_content: bool = False
) -> tuple[Callable[[], RuntimeLLMProtocol], EvalModel]:
    """Build a worker factory for the mock backend."""

    def sub_factory() -> RuntimeLLMProtocol:
        return LockfileEchoMockLLM(lock, role="worker", echo_write_content=echo_write_content)

    return sub_factory, EvalModel(provider="mock", model="lockfile-echo", url="mock://local")


def _single_agent_mock_factory() -> tuple[Callable[[], RuntimeLLMProtocol], EvalModel]:
    """Build the no-lock suite mock backend."""

    def sub_factory() -> RuntimeLLMProtocol:
        return NoLockSuiteMockLLM()

    return sub_factory, EvalModel(
        provider="mock",
        model=NoLockSuiteMockLLM.model,
        url=NoLockSuiteMockLLM.url,
    )


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
    suite_name: str | None = None,
    repo: EvalRepo | None = None,
    applied_diff_live: bool = False,
) -> EvalRun:
    """Execute one (strategy, backend) pair and return the populated :class:`EvalRun`.

    Raises ``ValueError`` for unknown strategy/backend names. The
    ``devin-manual`` and ``devin-api`` backends route through
    :mod:`devin_adapter` rather than this function — they don't go through
    :class:`RuntimeLLM`.
    """
    if strategy not in LOCAL_STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}")
    if backend not in ("mock", "local"):
        raise ValueError(
            f"backend {backend!r} not handled by run_strategy; use devin_adapter for "
            "devin-manual / devin-api."
        )

    eval_repo = repo or repo_from_path(
        Path(lock.repo.root) if lock.repo and lock.repo.root else None,
        repo_url=lock.repo.git_url if lock.repo else None,
        repo_commit=lock.repo.commit if lock.repo else None,
    )
    use_applied = strategy == ACG_PLANNED_APPLIED_STRATEGY or (
        applied_diff_live
        and strategy
        in (
            SINGLE_AGENT_STRATEGY,
            NAIVE_STRATEGY,
            ACG_PLANNED_STRATEGY,
            ACG_PLANNED_REPLAN_STRATEGY,
        )
    )
    run_planned_git_applied = strategy == ACG_PLANNED_APPLIED_STRATEGY or (
        applied_diff_live
        and strategy in (ACG_PLANNED_STRATEGY, ACG_PLANNED_REPLAN_STRATEGY)
    )
    if applied_diff_live and strategy == ACG_PLANNED_FULL_CONTEXT_STRATEGY:
        raise ValueError(
            "--applied-diff-live is not supported for acg_planned_full_context "
            "(use single_agent, naive_parallel, acg_planned, acg_planned_replan, "
            "or acg_planned_applied)"
        )

    if backend == "mock":
        if strategy == SINGLE_AGENT_STRATEGY:
            sub_factory, model = _single_agent_mock_factory()
        else:
            sub_factory, model = _mock_factory(lock, echo_write_content=use_applied)
    else:
        sub_factory, model = _local_factory()

    orch_overhead: int | None
    checkout = Path(eval_repo.local_path).resolve()

    if strategy == SINGLE_AGENT_STRATEGY:
        if applied_diff_live:
            tasks, wall_s, prompt_token_method = asyncio.run(
                _run_single_agent_applied(
                    lock,
                    repo_graph,
                    sub_factory,
                    checkout,
                    prompts_by_task=prompts_by_task,
                )
            )
            orch_overhead = None
            execution_mode = "applied_diff_live"
            evidence_kind = "suite_applied_diff"
        else:
            tasks, wall_s, prompt_token_method = asyncio.run(
                _run_single_agent(
                    lock,
                    repo_graph,
                    sub_factory,
                    prompts_by_task=prompts_by_task,
                )
            )
            orch_overhead = None
            execution_mode = "single_agent_no_lock"
            evidence_kind = "suite_proposed_write_set"
    elif strategy == NAIVE_STRATEGY:
        if applied_diff_live:
            tasks, wall_s, prompt_token_method = asyncio.run(
                _run_naive_parallel_applied(
                    lock,
                    repo_graph,
                    sub_factory,
                    checkout,
                    prompts_by_task=prompts_by_task,
                    cap_parallelism=cap_parallelism,
                )
            )
            orch_overhead = None
            execution_mode = "applied_diff_live"
            evidence_kind = "applied_diff"
        else:
            tasks, wall_s, prompt_token_method = asyncio.run(
                _run_naive_parallel(
                    lock,
                    repo_graph,
                    sub_factory,
                    prompts_by_task=prompts_by_task,
                    cap_parallelism=cap_parallelism,
                )
            )
            orch_overhead = None
            execution_mode = "propose_validate"
            evidence_kind = "proposed_write_set"
    elif strategy == NAIVE_PARALLEL_BLIND_STRATEGY:
        tasks, wall_s, prompt_token_method = asyncio.run(
            _run_naive_parallel_blind(
                lock,
                repo_graph,
                sub_factory,
                prompts_by_task=prompts_by_task,
                cap_parallelism=cap_parallelism,
            )
        )
        orch_overhead = None
        execution_mode = "propose_validate_blind"
        evidence_kind = "naive_parallel_blind_proposed_write_set"
    elif run_planned_git_applied:
        tasks, wall_s, prompt_token_method = asyncio.run(
            _run_acg_planned_applied(
                lock,
                repo_graph,
                sub_factory,
                checkout_path=checkout,
                lockfile_path=lockfile_path,
                prompts_by_task=prompts_by_task,
                cap_parallelism=cap_parallelism,
                scope_repo_graph=True,
                auto_replan=(strategy == ACG_PLANNED_REPLAN_STRATEGY),
            )
        )
        orch_overhead = None
        execution_mode = "applied_diff_live"
        evidence_kind = "applied_diff"
    else:
        tasks, wall_s, prompt_token_method = asyncio.run(
            _run_acg_planned(
                lock,
                repo_graph,
                sub_factory,
                lockfile_path=lockfile_path,
                prompts_by_task=prompts_by_task,
                cap_parallelism=cap_parallelism,
                scope_repo_graph=(strategy in {ACG_PLANNED_STRATEGY, ACG_PLANNED_REPLAN_STRATEGY}),
                auto_replan=(strategy == ACG_PLANNED_REPLAN_STRATEGY),
            )
        )
        orch_overhead = None
        execution_mode = "propose_validate"
        evidence_kind = "proposed_write_set"

    summary = compute_summary_metrics(
        tasks,
        wall_time_seconds=wall_s,
        sequential_wall_time_seconds=sequential_wall_time_seconds,
        tokens_orchestrator_overhead=orch_overhead,
        tokens_planner_total=(
            lock.generator.tokens_planner_total
            if lock.generator is not None and strategy != SINGLE_AGENT_STRATEGY
            else None
        ),
        tokens_scope_review_total=(
            lock.generator.tokens_scope_review_total
            if lock.generator is not None and strategy != SINGLE_AGENT_STRATEGY
            else None
        ),
        tokens_prompt_method=prompt_token_method,
        tokens_completion_method=(
            "provider_usage_completion_tokens"
            if backend == "local"
            else "mock_reply_completion_tokens"
        ),
        evidence_kind=evidence_kind,
    )
    return EvalRun(
        run_id=make_run_id(strategy, backend),
        created_at=now_iso(),
        suite_name=suite_name or suite_name_from_lock(lock),
        strategy=strategy,
        backend=backend,
        execution_mode=execution_mode,
        evidence_kind=evidence_kind,
        model=model,
        repo=eval_repo,
        lockfile=lockfile_path,
        tasks=tasks,
        summary_metrics=summary,
    )


__all__ = [
    "ACG_PLANNED_APPLIED_STRATEGY",
    "ACG_PLANNED_FULL_CONTEXT_STRATEGY",
    "ACG_PLANNED_REPLAN_STRATEGY",
    "ACG_PLANNED_STRATEGY",
    "LOCKFILE_ECHO_TOP_K",
    "LockfileEchoMockLLM",
    "LOCAL_STRATEGIES",
    "NAIVE_PARALLEL_BLIND_STRATEGY",
    "NAIVE_STRATEGY",
    "NoLockSuiteMockLLM",
    "SINGLE_AGENT_STRATEGY",
    "run_strategy",
]
