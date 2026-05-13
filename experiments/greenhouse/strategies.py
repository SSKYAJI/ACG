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
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from acg.apply_patch_adapter import apply_envelope
from acg.correctness import CorrectnessOutcome
from acg.enforce import validate_write
from acg.runtime import (
    LLMReply,
    Proposal,
    RuntimeLLM,
    RuntimeLLMProtocol,
    WorkerResult,
    _parse_apply_envelope,
    complete_llm_with_heartbeat,
    env_int_or_none,
    run_worker,
)
from acg.runtime_proposal import (
    PROPOSAL_OK,
    PROPOSAL_TRANSPORT_ERROR,
    PROPOSAL_TRUNCATED,
    PROPOSAL_UNPARSEABLE,
    classify_zero_proposal_reply,
)
from acg.schema import AgentLock, Task
from acg.typecheck import TypecheckOutcome, run_tsc_noemit

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

# single_agent apply_patch mode: per-task failure when the model reply is not
# envelopes or legacy JSON with file writes.
UNPARSEABLE_APPLY_PATCH_ENVELOPE = "UNPARSEABLE_APPLY_PATCH_ENVELOPE"

# How many predicted writes (sorted by confidence) the mock LLM echoes per task.
# Capped so that a noisy predictor doesn't drown the eval in noise.
LOCKFILE_ECHO_TOP_K = 8
SINGLE_AGENT_TOP_K_FILES = 30


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Optional ceilings for suite-level single-agent LLM calls. Unset / ``0`` /
# ``none`` omits ``max_tokens`` so the provider uses its native output budget.
SINGLE_AGENT_MAX_TOKENS = env_int_or_none("ACG_SINGLE_AGENT_MAX_TOKENS")
SINGLE_AGENT_APPLIED_MAX_TOKENS = env_int_or_none("ACG_SINGLE_AGENT_APPLIED_MAX_TOKENS")


def _merged_single_agent_max_tokens() -> int | None:
    """Largest explicit cap when both single-agent env knobs are set."""
    applied = SINGLE_AGENT_APPLIED_MAX_TOKENS
    general = SINGLE_AGENT_MAX_TOKENS
    if applied is not None and general is not None:
        return max(applied, general)
    if applied is not None:
        return applied
    return general


def _worker_output_truncated(worker: WorkerResult) -> bool:
    err = worker.error or ""
    return err.startswith("finish_reason=length")


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


def _attach_worker_proposal_fields(eval_task: EvalTask, worker: WorkerResult) -> None:
    eval_task.proposal_status = getattr(worker, "proposal_status", None) or PROPOSAL_OK
    eval_task.proposal_write_count = len(worker.proposals)


def _suite_worker_proposal_status(
    reply: LLMReply | None,
    *,
    error: str | None,
    proposals: list[Proposal],
) -> str:
    """Classify suite-level :class:`WorkerResult` built outside :func:`run_worker`."""
    if error:
        el = error.lower()
        if "transport" in el or "contacting" in el:
            return PROPOSAL_TRANSPORT_ERROR
        if el.startswith("finish_reason=length"):
            return PROPOSAL_TRUNCATED
        return PROPOSAL_TRANSPORT_ERROR
    if proposals:
        return PROPOSAL_OK
    if reply is not None and (reply.finish_reason or "").lower() == "length":
        return PROPOSAL_TRUNCATED
    if reply is None:
        return PROPOSAL_TRANSPORT_ERROR
    return classify_zero_proposal_reply(
        raw_content=reply.content,
        finish_reason=reply.finish_reason or "",
    )


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
        max_tokens: int | None = None,
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
        max_tokens: int | None = None,
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
                        # JSON / YAML / etc. cannot accept TS-style line comments; the
                        # apply-and-test smoke uses real repos with package.json writes.
                        if not (fp.endswith(".ts") or fp.endswith(".tsx")):
                            continue
                        parts.append(f"*** Update File: {fp}\n@@\n+// acg-mock-applied:{task_id}\n")
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
        max_tokens: int | None = None,
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
    if worker.error and not _worker_output_truncated(worker):
        eval_task.status = "failed"
        eval_task.failure_reason = "AGENT_FAIL"
    elif _worker_output_truncated(worker):
        eval_task.status = "failed"
        eval_task.failure_reason = "TRUNCATED_BY_MAX_TOKENS"
        eval_task.patch_na_reason = worker.error
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
    _attach_worker_proposal_fields(eval_task, worker)
    return eval_task


def _proposals_to_naive_applied_eval_task(
    worker: WorkerResult,
    lock_task: Task,
    lock: AgentLock,
    *,
    started_at: str,
    finished_at: str,
    prompt: str | None,
    task_outcome: TaskApplyOutcome,
) -> EvalTask:
    """Naive parallel + real git writes — ``actual_changed_files`` come from ``git diff``.

    Status is decided in priority order: AGENT_FAIL > PATCH_NA > NO_APPLIED_CONTENT/
    BLOCKED_BY_SCOPE/EMPTY_PATCH > completed_unsafe (OOB) > FAILED_TYPECHECK >
    completed > completed_unverified.
    """
    git_changed_files = task_outcome.changed_files
    eval_task = task_from_lock(lock_task, prompt=prompt)
    eval_task.actual_changed_files = sorted(git_changed_files)
    eval_task.actual_changed_files_kind = "applied_diff"
    eval_task.out_of_bounds_files = sorted(
        {p for p in git_changed_files if not validate_write(lock, lock_task.id, p)[0]}
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
    tc = task_outcome.typecheck
    if worker.error and not _worker_output_truncated(worker):
        eval_task.status = "failed"
        eval_task.failure_reason = "AGENT_FAIL"
    elif _worker_output_truncated(worker):
        eval_task.status = "failed"
        eval_task.failure_reason = "TRUNCATED_BY_MAX_TOKENS"
        eval_task.patch_na_reason = worker.error
    elif task_outcome.patch_na:
        eval_task.status = "failed"
        eval_task.failure_reason = "PATCH_NA"
        eval_task.patch_na_reason = task_outcome.patch_na_reason
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
    elif tc.ran and tc.exit_code == 0:
        eval_task.status = "completed"
    elif tc.ran and tc.exit_code is not None and tc.exit_code != 0:
        eval_task.status = "failed"
        eval_task.failure_reason = "FAILED_TYPECHECK"
    else:
        eval_task.status = "completed_unverified"
    eval_task.timestamps.started_at = started_at
    eval_task.timestamps.finished_at = finished_at
    eval_task.metrics.wall_time_seconds = round(worker.wall_s, 4)
    eval_task.metrics.tokens_completion = worker.completion_tokens or None
    eval_task.metrics.cost_usd = worker.cost_usd
    eval_task.metrics.cost_source = worker.cost_source
    eval_task.metrics.model_calls = 1
    if task_outcome.patch_na:
        eval_task.metrics.patch_applies = False
    elif eval_task.actual_changed_files:
        eval_task.metrics.patch_applies = True
    eval_task.metrics.typecheck_ran = tc.ran
    eval_task.metrics.typecheck_exit_code = tc.exit_code
    eval_task.metrics.typecheck_diagnostic_count = tc.diagnostic_count
    eval_task.metrics.typecheck_wall_seconds = tc.wall_seconds
    tests = task_outcome.tests_outcome
    if tests is not None:
        eval_task.tests_ran = tests.ran
        eval_task.tests_exit_code = tests.exit_code
        eval_task.tests_passed_count = tests.passed_count
        eval_task.tests_failed_count = tests.failed_count
        eval_task.tests_total_count = tests.total_count
        eval_task.tests_skip_reason = tests.skip_reason
        eval_task.tests_collection_error = tests.collection_error
        eval_task.fail_to_pass_passed = tests.fail_to_pass_passed
        eval_task.fail_to_pass_total = tests.fail_to_pass_total
        eval_task.pass_to_pass_passed = tests.pass_to_pass_passed
        eval_task.pass_to_pass_total = tests.pass_to_pass_total
        eval_task.overlay_applied = tests.overlay_applied
        eval_task.overlay_skip_reason = tests.overlay_skip_reason
    _attach_worker_proposal_fields(eval_task, worker)
    return eval_task


def _proposals_to_suite_applied_eval_task(
    worker: WorkerResult,
    lock_task: Task,
    lock: AgentLock,
    *,
    started_at: str,
    finished_at: str,
    prompt: str | None,
    task_outcome: TaskApplyOutcome,
) -> EvalTask:
    """Single-agent applied mode — git-derived files with suite-level envelope parsing."""
    git_changed_files = task_outcome.changed_files
    eval_task = task_from_lock(lock_task, prompt=prompt)
    eval_task.actual_changed_files = sorted(git_changed_files)
    eval_task.actual_changed_files_kind = "suite_applied_diff"
    eval_task.out_of_bounds_files = sorted(
        {p for p in git_changed_files if not validate_write(lock, lock_task.id, p)[0]}
    )
    eval_task.blocked_write_events = []
    tc = task_outcome.typecheck
    if worker.error and not _worker_output_truncated(worker):
        eval_task.status = "failed"
        eval_task.failure_reason = "AGENT_FAIL"
    elif _worker_output_truncated(worker):
        eval_task.status = "failed"
        eval_task.failure_reason = "TRUNCATED_BY_MAX_TOKENS"
        eval_task.patch_na_reason = worker.error
    elif task_outcome.patch_na:
        eval_task.status = "failed"
        eval_task.failure_reason = "PATCH_NA"
        eval_task.patch_na_reason = task_outcome.patch_na_reason
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
    elif tc.ran and tc.exit_code == 0:
        eval_task.status = "completed"
    elif tc.ran and tc.exit_code is not None and tc.exit_code != 0:
        eval_task.status = "failed"
        eval_task.failure_reason = "FAILED_TYPECHECK"
    else:
        eval_task.status = "completed_unverified"
    eval_task.timestamps.started_at = started_at
    eval_task.timestamps.finished_at = finished_at
    eval_task.metrics.wall_time_seconds = round(worker.wall_s, 4)
    eval_task.metrics.tokens_completion = worker.completion_tokens or None
    eval_task.metrics.cost_usd = worker.cost_usd
    eval_task.metrics.cost_source = worker.cost_source
    eval_task.metrics.model_calls = 1
    if task_outcome.patch_na:
        eval_task.metrics.patch_applies = False
    elif eval_task.actual_changed_files:
        eval_task.metrics.patch_applies = True
    eval_task.metrics.typecheck_ran = tc.ran
    eval_task.metrics.typecheck_exit_code = tc.exit_code
    eval_task.metrics.typecheck_diagnostic_count = tc.diagnostic_count
    eval_task.metrics.typecheck_wall_seconds = tc.wall_seconds
    tests = task_outcome.tests_outcome
    if tests is not None:
        eval_task.tests_ran = tests.ran
        eval_task.tests_exit_code = tests.exit_code
        eval_task.tests_passed_count = tests.passed_count
        eval_task.tests_failed_count = tests.failed_count
        eval_task.tests_total_count = tests.total_count
        eval_task.tests_skip_reason = tests.skip_reason
        eval_task.tests_collection_error = tests.collection_error
        eval_task.fail_to_pass_passed = tests.fail_to_pass_passed
        eval_task.fail_to_pass_total = tests.fail_to_pass_total
        eval_task.pass_to_pass_passed = tests.pass_to_pass_passed
        eval_task.pass_to_pass_total = tests.pass_to_pass_total
        eval_task.overlay_applied = tests.overlay_applied
        eval_task.overlay_skip_reason = tests.overlay_skip_reason
    _attach_worker_proposal_fields(eval_task, worker)
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
    if worker.error and not _worker_output_truncated(worker):
        eval_task.status = "failed"
        eval_task.failure_reason = "AGENT_FAIL"
    elif _worker_output_truncated(worker):
        eval_task.status = "failed"
        eval_task.failure_reason = "TRUNCATED_BY_MAX_TOKENS"
        eval_task.patch_na_reason = worker.error
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
    _attach_worker_proposal_fields(eval_task, worker)
    return eval_task


def _proposals_to_planned_applied_eval_task(
    worker: WorkerResult,
    lock_task: Task,
    *,
    started_at: str,
    finished_at: str,
    prompt: str | None,
    task_outcome: TaskApplyOutcome,
) -> EvalTask:
    """Like :func:`_proposals_to_planned_eval_task` but ``actual_changed_files`` is git-derived.

    Status transitions (priority order):
    AGENT_FAIL > PATCH_NA > NO_APPLIED_CONTENT/BLOCKED_BY_SCOPE/EMPTY_PATCH >
    FAILED_TYPECHECK > completed > completed_unverified.
    """
    git_changed_files = task_outcome.changed_files
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
    tc = task_outcome.typecheck
    if worker.error and not _worker_output_truncated(worker):
        eval_task.status = "failed"
        eval_task.failure_reason = "AGENT_FAIL"
    elif _worker_output_truncated(worker):
        eval_task.status = "failed"
        eval_task.failure_reason = "TRUNCATED_BY_MAX_TOKENS"
        eval_task.patch_na_reason = worker.error
    elif task_outcome.patch_na:
        eval_task.status = "failed"
        eval_task.failure_reason = "PATCH_NA"
        eval_task.patch_na_reason = task_outcome.patch_na_reason
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
    elif tc.ran and tc.exit_code == 0:
        eval_task.status = "completed"
    elif tc.ran and tc.exit_code is not None and tc.exit_code != 0:
        eval_task.status = "failed"
        eval_task.failure_reason = "FAILED_TYPECHECK"
    else:
        eval_task.status = "completed_unverified"
    eval_task.timestamps.started_at = started_at
    eval_task.timestamps.finished_at = finished_at
    eval_task.metrics.wall_time_seconds = round(worker.wall_s, 4)
    eval_task.metrics.tokens_completion = worker.completion_tokens or None
    eval_task.metrics.cost_usd = worker.cost_usd
    eval_task.metrics.cost_source = worker.cost_source
    eval_task.metrics.model_calls = 1
    if task_outcome.patch_na:
        eval_task.metrics.patch_applies = False
    elif eval_task.actual_changed_files:
        eval_task.metrics.patch_applies = True
    eval_task.metrics.typecheck_ran = tc.ran
    eval_task.metrics.typecheck_exit_code = tc.exit_code
    eval_task.metrics.typecheck_diagnostic_count = tc.diagnostic_count
    eval_task.metrics.typecheck_wall_seconds = tc.wall_seconds
    tests = task_outcome.tests_outcome
    if tests is not None:
        eval_task.tests_ran = tests.ran
        eval_task.tests_exit_code = tests.exit_code
        eval_task.tests_passed_count = tests.passed_count
        eval_task.tests_failed_count = tests.failed_count
        eval_task.tests_total_count = tests.total_count
        eval_task.tests_skip_reason = tests.skip_reason
        eval_task.tests_collection_error = tests.collection_error
        eval_task.fail_to_pass_passed = tests.fail_to_pass_passed
        eval_task.fail_to_pass_total = tests.fail_to_pass_total
        eval_task.pass_to_pass_passed = tests.pass_to_pass_passed
        eval_task.pass_to_pass_total = tests.pass_to_pass_total
        eval_task.overlay_applied = tests.overlay_applied
        eval_task.overlay_skip_reason = tests.overlay_skip_reason
    _attach_worker_proposal_fields(eval_task, worker)
    return eval_task


def _sanitize_applied_branch_task_id(task_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "-" for c in task_id)


def _resolve_repo_short_name(checkout: Path | str) -> str | None:
    """Infer the manifest.json short_name from the checkout path.

    E.g. .../experiments/real_repos/starlette/checkout -> 'starlette'.
    """
    parts = Path(checkout).resolve().parts
    if "real_repos" in parts:
        i = parts.index("real_repos")
        if i + 1 < len(parts):
            return parts[i + 1]
    return None


def _git_identity_args() -> list[str]:
    return [
        "-c",
        "user.name=ACG Eval",
        "-c",
        "user.email=acg-eval@users.noreply.localhost",
    ]


@dataclass(frozen=True)
class TaskApplyOutcome:
    """Aggregated apply-step result for one task.

    Combines the git-derived ``changed_files``, the patch-N/A signal lifted
    from ``apply_envelope`` calls (any patch failure surfaces as
    ``patch_na=True`` plus the first reason), and the ``TypecheckOutcome``
    captured from ``npx tsc --noEmit`` after the commit step. Converters
    use this struct to assign honest task statuses
    (``failed/PATCH_NA`` / ``failed/FAILED_TYPECHECK`` / ``completed`` /
    ``completed_unverified``) without needing access to subprocess details.
    """

    changed_files: list[str] = field(default_factory=list)
    patch_na: bool = False
    patch_na_reason: str | None = None
    typecheck: TypecheckOutcome = field(
        default_factory=lambda: TypecheckOutcome(
            ran=False,
            exit_code=None,
            diagnostic_count=None,
            wall_seconds=None,
            skip_reason="NOT_RUN",
        )
    )
    tests_outcome: CorrectnessOutcome | None = None


def _apply_writes_git_sync(
    checkout: Path,
    base_sha: str,
    lock: AgentLock,
    task: Task,
    wr: WorkerResult,
    *,
    require_scope: bool = True,
    run_typecheck: bool = True,
) -> TaskApplyOutcome:
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
        patch_na = False
        patch_na_reason: str | None = None
        # Opt-in envelope dump for debugging EMPTY_PATCH situations.
        # Set ACG_DUMP_ENVELOPES=/some/dir to write each proposal's envelope
        # + apply-outcome to ``<dir>/<task_id>__<seq>.{envelope,outcome.json}``.
        # Default off; ON adds 2 fast filesystem writes per proposal.
        _dump_dir_env = os.environ.get("ACG_DUMP_ENVELOPES", "").strip()
        _dump_dir = Path(_dump_dir_env) if _dump_dir_env else None
        if _dump_dir is not None:
            _dump_dir.mkdir(parents=True, exist_ok=True)
        _dump_seq = 0
        for prop in wr.proposals:
            if require_scope and not prop.allowed:
                continue
            if prop.envelope is None:
                # Proposals without an apply_patch envelope are silently
                # ignored at the apply step. The legacy ``content`` fallback
                # was removed because it muddied the "completed" signal:
                # a raw blob write isn't an apply_patch outcome.
                continue
            if require_scope:
                allowed, _reason = validate_write(lock, task.id, prop.file)
                if not allowed:
                    continue
            outcome = apply_envelope(prop.envelope, root)
            if _dump_dir is not None:
                _dump_seq += 1
                stem = _dump_dir / f"{_sanitize_applied_branch_task_id(task.id)}__{_dump_seq:02d}"
                stem.with_suffix(".envelope").write_text(prop.envelope)
                stem.with_suffix(".outcome.json").write_text(
                    json.dumps(
                        {
                            "file": prop.file,
                            "envelope_parsed": outcome.envelope_parsed,
                            "patch_na": outcome.patch_na,
                            "patch_na_reason": outcome.patch_na_reason,
                            "changed_files": list(outcome.changed_files),
                            "errors": list(outcome.errors),
                        },
                        indent=2,
                    )
                )
            if outcome.patch_na and not patch_na:
                patch_na = True
                patch_na_reason = outcome.patch_na_reason
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
        names = sorted({line.strip() for line in diff.stdout.splitlines() if line.strip()})
        if run_typecheck:
            tc = run_tsc_noemit(checkout)
        else:
            tc = TypecheckOutcome(
                ran=False,
                exit_code=None,
                diagnostic_count=None,
                wall_seconds=None,
                skip_reason="DISABLED",
            )
        # SWE-Bench-style test overlay: reset test files to canonical merge_commit_sha
        # state so FTP scoring uses canonical test names regardless of what the agent wrote.
        # This fires AFTER the agent's commit and BEFORE test execution.
        # Source files (non-test) are left as the agent wrote them.
        overlay_info: dict = {}
        try:
            from acg.correctness import overlay_canonical_tests

            repo_short_for_overlay = _resolve_repo_short_name(checkout)
            if repo_short_for_overlay and task is not None:
                overlay_info = overlay_canonical_tests(Path(checkout), repo_short_for_overlay, task.id)
                if overlay_info.get("overlay_applied"):
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            repo,
                            *_git_identity_args(),
                            "commit",
                            "-m",
                            f"acg(test-overlay): {task.id}",
                            "--allow-empty",
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
        except Exception as exc:
            overlay_info = {"overlay_applied": False, "skip_reason": f"exception:{type(exc).__name__}"}

        # PR-scoped test gate (Python-and-similar repos via manifest.test_command)
        tests_outcome = None
        try:
            from acg.correctness import run_pr_tests

            repo_short = _resolve_repo_short_name(checkout)
            if repo_short and task is not None:
                tests_outcome = run_pr_tests(Path(checkout), repo_short, task.id)
        except Exception as exc:
            tests_outcome = CorrectnessOutcome(
                ran=False, skip_reason=f"exception:{type(exc).__name__}"
            )
        if tests_outcome is not None:
            tests_outcome.overlay_applied = bool(overlay_info.get("overlay_applied"))
            tests_outcome.overlay_skip_reason = overlay_info.get("skip_reason") or ""
        return TaskApplyOutcome(
            changed_files=names,
            patch_na=patch_na,
            patch_na_reason=patch_na_reason,
            typecheck=tc,
            tests_outcome=tests_outcome,
        )
    finally:
        subprocess.run(
            ["git", "-C", repo, "checkout", "--quiet", "--detach", base_sha],
            check=True,
            capture_output=True,
            text=True,
        )
        if os.environ.get("ACG_APPLIED_BRANCH_CLEANUP", "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }:
            subprocess.run(
                ["git", "-C", repo, "branch", "-D", branch],
                capture_output=True,
                text=True,
                check=False,
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
    eval_dump_dir: Path | None = None,
    strategy_folder: str = "",
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
    _persist_worker_raw_replies(eval_dump_dir, strategy_folder, worker_results)

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
    eval_dump_dir: Path | None = None,
    strategy_folder: str = "",
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
    _persist_worker_raw_replies(eval_dump_dir, strategy_folder, worker_results)

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
    eval_dump_dir: Path | None = None,
    strategy_folder: str = "",
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
    outcome_by_task: dict[str, TaskApplyOutcome] = {}
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
                outcome = await asyncio.to_thread(
                    _apply_writes_git_sync,
                    checkout,
                    base_sha,
                    lock,
                    task,
                    wr,
                    require_scope=False,
                )
            outcome_by_task[wr.task_id] = outcome
    finally:
        await counting_sub.aclose()
    wall_s = time.perf_counter() - t0
    finished = now_iso()
    _persist_worker_raw_replies(eval_dump_dir, strategy_folder, worker_results)

    tasks = [
        _proposals_to_naive_applied_eval_task(
            wr,
            tasks_by_id[wr.task_id],
            lock,
            started_at=started,
            finished_at=finished,
            prompt=(prompts_by_task or {}).get(wr.task_id),
            task_outcome=outcome_by_task.get(wr.task_id, TaskApplyOutcome()),
        )
        for wr in worker_results
        if wr.task_id in tasks_by_id
    ]
    for et in tasks:
        et.metrics.tokens_prompt = counting_sub.tokens_by_task.get(et.task_id)
    annotate_overlaps(tasks)
    return tasks, wall_s, counting_sub.prompt_token_method


async def _run_naive_parallel_blind_applied(
    lock: AgentLock,
    repo_graph: dict[str, Any],
    sub_factory: Callable[[], RuntimeLLMProtocol],
    checkout_path: Path,
    *,
    prompts_by_task: dict[str, str] | None = None,
    cap_parallelism: int | None = None,
    eval_dump_dir: Path | None = None,
    strategy_folder: str = "",
) -> tuple[list[EvalTask], float, str]:
    """Naive blind workers (no lockfile hints) + real git writes.

    Mirrors :func:`_run_naive_parallel_applied` but every worker is built
    with ``include_lockfile_hints=False`` so the prompt does not enumerate
    ``predicted_writes`` / ``candidate_context``. This is the apply-step
    counterpart of :func:`_run_naive_parallel_blind` and exposes the OOB
    behavior for the blind baseline.
    """
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

    blind_runtime_config = RuntimeConfig.from_env()
    blind_runtime_config.auto_replan = False
    tasks_by_id = {t.id: t for t in lock.tasks}
    outcome_by_task: dict[str, TaskApplyOutcome] = {}
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
                    config=blind_runtime_config,
                    include_lockfile_hints=False,
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
                outcome = await asyncio.to_thread(
                    _apply_writes_git_sync,
                    checkout,
                    base_sha,
                    lock,
                    task,
                    wr,
                    require_scope=False,
                )
            outcome_by_task[wr.task_id] = outcome
    finally:
        await counting_sub.aclose()
    wall_s = time.perf_counter() - t0
    finished = now_iso()
    _persist_worker_raw_replies(eval_dump_dir, strategy_folder, worker_results)

    tasks = [
        _proposals_to_naive_applied_eval_task(
            wr,
            tasks_by_id[wr.task_id],
            lock,
            started_at=started,
            finished_at=finished,
            prompt=(prompts_by_task or {}).get(wr.task_id),
            task_outcome=outcome_by_task.get(wr.task_id, TaskApplyOutcome()),
        )
        for wr in worker_results
        if wr.task_id in tasks_by_id
    ]
    for et in tasks:
        et.metrics.tokens_prompt = counting_sub.tokens_by_task.get(et.task_id)
        et.actual_changed_files_kind = "naive_parallel_blind_applied_diff"
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
            "A) OpenAI apply_patch layout (preferred): for every task, emit a line "
            "that starts with the exact ASCII bytes ``Task id: `` (capital T, lowercase "
            "ask, ASCII colon, ASCII space) immediately followed by the task id from the "
            "suite list, then a single newline, then only a ``*** Begin Patch`` … "
            "``*** End Patch`` envelope for that task. Do not wrap the reply in markdown "
            "code fences (no ```), do not bold the ``Task id:`` line, do not emit JSON "
            "or YAML alongside format A, and do not add headings before ``Task id:``.\n\n"
            'B) Legacy JSON object with key "tasks": an array of objects. Each object '
            'must have "task_id" and "writes"; every write object must include '
            '"file", "description", and a string "content" field whose value is '
            "the complete UTF-8 file body to write.\n\n"
            "Do not mix formats. If you choose JSON, partial file bodies are invalid."
        )
    else:
        system = (
            "You are a single coding agent handling an entire task suite without "
            'a precomputed file contract. Output ONLY a JSON object with key "tasks": an '
            'array of objects. Each object must have "task_id" and "writes"; '
            '"writes" is an array of objects with "file" and "description". '
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


def _normalize_single_agent_apply_patch_text(raw: str) -> str:
    """Strip outer markdown fences so patch markers remain visible to regex."""
    text = (raw or "").strip()
    changed = True
    while changed:
        changed = False
        if text.startswith("```"):
            text = re.sub(r"^```(?:[\w-]+)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```\s*$", "", text)
            text = text.strip()
            changed = True
    return text


# Task id headers copied into the prompt use ``Task id: <id>``; models often
# add markdown headings or bold. Keep the id capture permissive.
_TASK_ID_HEADER_RE = re.compile(r"(?m)^\s*(?:#{1,6}\s*)?\*{0,2}\s*Task\s+id\s*:\s*\*{0,2}\s*(\S+)")


def _parse_single_agent_applied_sections(raw: str) -> dict[str, str]:
    text = _normalize_single_agent_apply_patch_text(raw)
    if not text or "task id" not in text.lower():
        return {}
    sections: dict[str, str] = {}
    matches = list(_TASK_ID_HEADER_RE.finditer(text))
    if not matches:
        return {}
    for i, m in enumerate(matches):
        tid = m.group(1).strip().strip("*_:`\"'").rstrip(":.,;")
        if not tid:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        if chunk:
            sections[tid] = chunk
    return sections


def _parse_single_agent_applied_mega(raw: str, lock: AgentLock) -> dict[str, str]:
    text = _normalize_single_agent_apply_patch_text(raw)
    chunks_by_task: dict[str, list[str]] = {t.id: [] for t in lock.tasks}
    if not lock.tasks:
        return {}
    for block in re.findall(r"\*\*\* Begin Patch[\s\S]*?\*\*\* End Patch", text):
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


_SINGLE_AGENT_PATCH_PATH_HEADER_RE = re.compile(
    r"(?m)^(?:\*\*\* (?:Update|Add|Delete) File: |\+\+\+ b/)(.+?)\s*$"
)


def _writes_from_single_agent_patch_blob(blob: str) -> list[dict[str, str]]:
    """Extract touched paths from ``*** Update File:`` / unified-diff ``+++ b/`` headers.

    Match group paths have trailing whitespace stripped; duplicates per blob are skipped
    in first-seen order (both headers may name the same file).
    """
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for m in _SINGLE_AGENT_PATCH_PATH_HEADER_RE.finditer(blob or ""):
        path = m.group(1).rstrip()
        if not path or path in seen:
            continue
        seen.add(path)
        out.append({"file": path, "description": ""})
    return out


_SINGLE_AGENT_RAW_FILE_CAP = 2_000_000


def _persist_single_agent_raw_reply_files(
    out_dir: Path, lock: AgentLock, raw_text: str
) -> dict[str, str]:
    """Write ``single_agent_raw/<task_id>.txt`` and ``suite_reply.txt`` under ``out_dir``.

    Returns task_id → path relative to ``out_dir`` for :attr:`TaskArtifacts.log_path`.
    """
    rd = out_dir / "single_agent_raw"
    rd.mkdir(parents=True, exist_ok=True)
    body = raw_text or ""
    (rd / "suite_reply.txt").write_text(body[:_SINGLE_AGENT_RAW_FILE_CAP], encoding="utf-8")
    rel: dict[str, str] = {}
    sections = _parse_single_agent_applied_sections(body) if body else {}
    for t in lock.tasks:
        chunk = (sections.get(t.id) or "").strip()
        per_task = chunk if chunk else body
        (rd / f"{t.id}.txt").write_text(per_task[:_SINGLE_AGENT_RAW_FILE_CAP], encoding="utf-8")
        rel[t.id] = f"single_agent_raw/{t.id}.txt"
    return rel


_WORKER_RAW_FILE_CAP = _SINGLE_AGENT_RAW_FILE_CAP


def _strategy_worker_raw_folder(strategy: str) -> str:
    """Subdir under ``eval_dump_dir`` for per-task worker raw replies.

    Names match the multi-strategy spot-check layout (``naive_parallel_raw``,
    ``acg_planned_raw``, …), not ``eval_run_*.json`` short names.
    """
    return {
        NAIVE_STRATEGY: "naive_parallel_raw",
        NAIVE_PARALLEL_BLIND_STRATEGY: "naive_parallel_blind_raw",
        ACG_PLANNED_STRATEGY: "acg_planned_raw",
        ACG_PLANNED_FULL_CONTEXT_STRATEGY: "acg_full_context_raw",
        ACG_PLANNED_REPLAN_STRATEGY: "acg_replan_raw",
        ACG_PLANNED_APPLIED_STRATEGY: "acg_planned_applied_raw",
    }.get(strategy, f"{strategy}_raw")


def _persist_worker_raw_replies(
    eval_dump_dir: Path | None,
    strategy_folder: str,
    worker_results: list[WorkerResult],
) -> dict[str, str]:
    """Write ``<eval_dump_dir>/<strategy_folder>/<task_id>.txt`` per worker result.

    No-op when ``eval_dump_dir`` is None or ``strategy_folder`` is empty.
    Returns task_id → path relative to ``eval_dump_dir``.
    """
    if eval_dump_dir is None or not strategy_folder:
        return {}
    rd = eval_dump_dir / strategy_folder
    rd.mkdir(parents=True, exist_ok=True)
    rel: dict[str, str] = {}
    for wr in worker_results:
        body = (wr.raw_content or "")[:_WORKER_RAW_FILE_CAP]
        (rd / f"{wr.task_id}.txt").write_text(body, encoding="utf-8")
        rel[wr.task_id] = f"{strategy_folder}/{wr.task_id}.txt"
    return rel


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
    eval_dump_dir: Path | None = None,
) -> tuple[list[EvalTask], float, str]:
    """Run one suite-level agent with no lockfile write contract in prompt.

    When ``ACG_SINGLE_AGENT_APPLY_PATCH=1`` is set, the agent is asked to
    emit OpenAI ``apply_patch`` envelopes (one per task) instead of the
    legacy JSON ``{file, description}`` summary. This makes its output
    apples-to-apples with ``naive_parallel`` / ``acg_planned`` for
    token-economy comparisons — those strategies always emit code-bearing
    envelopes, and the legacy JSON path here is "list paths only" which is
    a fundamentally cheaper deliverable.
    """
    llm = sub_factory()
    apply_patch_mode = os.environ.get("ACG_SINGLE_AGENT_APPLY_PATCH", "0") == "1"
    messages = _build_single_agent_prompt(
        lock,
        repo_graph,
        prompts_by_task=prompts_by_task,
        apply_patch_suites=apply_patch_mode,
    )
    started = now_iso()
    t0 = time.perf_counter()
    reply: LLMReply | None = None
    error: str | None = None
    try:
        reply = await complete_llm_with_heartbeat(
            llm,
            task_id="single_agent",
            messages=messages,
            max_tokens=SINGLE_AGENT_MAX_TOKENS,
            temperature=0.2,
        )
    except Exception as exc:  # pragma: no cover - exercised by live backends.
        error = str(exc)
    finally:
        await llm.aclose()
    wall_s = time.perf_counter() - t0
    finished = now_iso()

    if reply is not None and not error and (reply.finish_reason or "").lower() == "length":
        max_desc = (
            str(SINGLE_AGENT_MAX_TOKENS)
            if SINGLE_AGENT_MAX_TOKENS is not None
            else "provider-native"
        )
        error = f"finish_reason=length; output truncated at max_tokens={max_desc}"

    prompt_tokens: int | None = None
    prompt_method = "estimated_chars_div_4"
    if reply is not None and reply.prompt_tokens is not None:
        prompt_tokens = reply.prompt_tokens
        prompt_method = "provider_usage_prompt_tokens"
    elif reply is not None:
        prompt_tokens = estimate_prompt_tokens(messages)
    if reply is not None and not error and apply_patch_mode:
        # apply_patch envelopes: derive ``writes`` lists from the file
        # headers in each task's envelope so downstream eval_run fields
        # (proposal_write_count, actual_changed_files) stay populated the
        # same way as the JSON path. If the model ignored format A, fall back
        # to the legacy JSON parser so mixed-provider replies still score.
        envelopes = _parse_single_agent_applied_envelopes(reply.content, lock)
        parsed = {
            task_id: _writes_from_single_agent_patch_blob(env) for task_id, env in envelopes.items()
        }
        if not any(parsed.get(t.id) for t in lock.tasks):
            parsed = _parse_single_agent_task_writes(
                reply.content, {task.id for task in lock.tasks}
            )
    elif reply is not None and not error:
        parsed = _parse_single_agent_task_writes(reply.content, {task.id for task in lock.tasks})
    else:
        parsed = {}

    raw_log_paths: dict[str, str] = {}
    if eval_dump_dir is not None and reply is not None:
        raw_log_paths = _persist_single_agent_raw_reply_files(
            eval_dump_dir, lock, reply.content or ""
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
        writes = parsed.get(lock_task.id, [])
        apply_patch_miss = bool(apply_patch_mode and not error and reply is not None and not writes)
        if error:
            eval_task.status = "failed"
            eval_task.failure_reason = (
                "TRUNCATED_BY_MAX_TOKENS"
                if error.startswith("finish_reason=length")
                else "AGENT_FAIL"
            )
        elif apply_patch_miss:
            eval_task.status = "failed"
            eval_task.failure_reason = UNPARSEABLE_APPLY_PATCH_ENVELOPE
        else:
            eval_task.status = "completed"
            eval_task.failure_reason = None
        if error and error.startswith("finish_reason=length"):
            eval_task.patch_na_reason = error
        eval_task.timestamps.started_at = started
        eval_task.timestamps.finished_at = finished
        eval_task.metrics.wall_time_seconds = round(wall_s if index == 0 else 0.0, 4)
        eval_task.metrics.model_calls = 1 if index == 0 else 0
        if index == 0 and reply is not None:
            eval_task.metrics.tokens_prompt = prompt_tokens
            eval_task.metrics.tokens_completion = reply.completion_tokens or None
            eval_task.metrics.cost_usd = reply.cost_usd
            eval_task.metrics.cost_source = reply.cost_source
            raw_body = reply.content or ""
            if raw_body:
                eval_task.artifacts.raw_reply = raw_body[:8192]
        if raw_log_paths.get(lock_task.id):
            eval_task.artifacts.log_path = raw_log_paths[lock_task.id]
        eval_task.proposal_write_count = len(writes)
        if error:
            eval_task.proposal_status = (
                PROPOSAL_TRUNCATED
                if error.startswith("finish_reason=length")
                else PROPOSAL_TRANSPORT_ERROR
            )
        elif writes:
            eval_task.proposal_status = PROPOSAL_OK
        elif apply_patch_miss:
            eval_task.proposal_status = PROPOSAL_UNPARSEABLE
        elif reply is not None:
            eval_task.proposal_status = classify_zero_proposal_reply(
                raw_content=reply.content,
                finish_reason=reply.finish_reason or "",
            )
        else:
            eval_task.proposal_status = PROPOSAL_TRANSPORT_ERROR
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
    eval_dump_dir: Path | None = None,
    strategy_folder: str = "",
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
    merged_max = _merged_single_agent_max_tokens()
    try:
        reply = await complete_llm_with_heartbeat(
            llm,
            task_id="single_agent_applied",
            messages=messages,
            max_tokens=merged_max,
            temperature=0.2,
        )
    except Exception as exc:  # pragma: no cover - exercised by live backends.
        error = str(exc)
    finally:
        await llm.aclose()
    wall_s = time.perf_counter() - t0
    finished = now_iso()

    if reply is not None and not error and (reply.finish_reason or "").lower() == "length":
        max_desc = str(merged_max) if merged_max is not None else "provider-native"
        error = f"finish_reason=length; output truncated at max_tokens={max_desc}"

    prompt_tokens: int | None = None
    prompt_method = "estimated_chars_div_4"
    if reply is not None and reply.prompt_tokens is not None:
        prompt_tokens = reply.prompt_tokens
        prompt_method = "provider_usage_prompt_tokens"
    elif reply is not None:
        prompt_tokens = estimate_prompt_tokens(messages)

    if reply is not None and not error:
        envelopes_by_task = _parse_single_agent_applied_envelopes(reply.content, lock)
        parsed_writes = _parse_single_agent_task_writes(reply.content, {t.id for t in lock.tasks})
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
            proposal_status=_suite_worker_proposal_status(reply, error=error, proposals=proposals),
        )

    _persist_worker_raw_replies(
        eval_dump_dir,
        strategy_folder,
        [workers_by_task[t.id] for t in lock.tasks if t.id in workers_by_task],
    )

    outcome_by_task: dict[str, TaskApplyOutcome] = {}
    git_lock = asyncio.Lock()

    async def _apply_one(task_id: str) -> None:
        wr = workers_by_task[task_id]
        task = next(t for t in lock.tasks if t.id == task_id)
        async with git_lock:
            outcome = await asyncio.to_thread(
                _apply_writes_git_sync,
                checkout,
                base_sha,
                lock,
                task,
                wr,
                require_scope=False,
            )
        outcome_by_task[task_id] = outcome

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
            task_outcome=outcome_by_task.get(lock_task.id, TaskApplyOutcome()),
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
    eval_dump_dir: Path | None = None,
    strategy_folder: str = "",
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
    _persist_worker_raw_replies(eval_dump_dir, strategy_folder, worker_results)

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
    eval_dump_dir: Path | None = None,
    strategy_folder: str = "",
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
    outcome_by_task: dict[str, TaskApplyOutcome] = {}
    git_lock = asyncio.Lock()
    started = now_iso()
    t0 = time.perf_counter()

    async def _apply_one(wr: WorkerResult) -> None:
        task = tasks_by_id.get(wr.task_id)
        if task is None:
            return
        async with git_lock:
            outcome = await asyncio.to_thread(
                _apply_writes_git_sync,
                checkout,
                base_sha,
                lock,
                task,
                wr,
                require_scope=True,
            )
        outcome_by_task[wr.task_id] = outcome

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
                        # Inline predicted_writes file contents so the agent
                        # emits Update File hunks whose context lines match
                        # disk. Without this, every applied-diff run reports
                        # EMPTY_PATCH regardless of model.
                        repo_root=checkout,
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
    _persist_worker_raw_replies(eval_dump_dir, strategy_folder, worker_results)

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
            task_outcome=outcome_by_task.get(wr.task_id, TaskApplyOutcome()),
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
            extra_params=cfg.extra_params,
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
    eval_dump_dir: Path | None = None,
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

    resolved_parallel_cap = cap_parallelism
    if resolved_parallel_cap is None:
        raw_w = os.environ.get("ACG_WORKER_CONCURRENCY", "0")
        try:
            wc = int(str(raw_w).strip() or "0")
        except ValueError:
            wc = 0
        resolved_parallel_cap = wc if wc > 0 else None

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
            NAIVE_PARALLEL_BLIND_STRATEGY,
            ACG_PLANNED_STRATEGY,
            ACG_PLANNED_REPLAN_STRATEGY,
            ACG_PLANNED_FULL_CONTEXT_STRATEGY,
        )
    )
    run_planned_git_applied = strategy == ACG_PLANNED_APPLIED_STRATEGY or (
        applied_diff_live
        and strategy
        in (
            ACG_PLANNED_STRATEGY,
            ACG_PLANNED_REPLAN_STRATEGY,
            ACG_PLANNED_FULL_CONTEXT_STRATEGY,
        )
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

    if eval_dump_dir is None:
        worker_raw_folder = ""
    elif strategy == SINGLE_AGENT_STRATEGY and use_applied:
        worker_raw_folder = "single_agent_applied_raw"
    elif strategy == SINGLE_AGENT_STRATEGY:
        worker_raw_folder = ""
    else:
        worker_raw_folder = _strategy_worker_raw_folder(strategy)

    if strategy == SINGLE_AGENT_STRATEGY:
        if applied_diff_live:
            tasks, wall_s, prompt_token_method = asyncio.run(
                _run_single_agent_applied(
                    lock,
                    repo_graph,
                    sub_factory,
                    checkout,
                    prompts_by_task=prompts_by_task,
                    eval_dump_dir=eval_dump_dir,
                    strategy_folder=worker_raw_folder,
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
                    eval_dump_dir=eval_dump_dir,
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
                    cap_parallelism=resolved_parallel_cap,
                    eval_dump_dir=eval_dump_dir,
                    strategy_folder=worker_raw_folder,
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
                    cap_parallelism=resolved_parallel_cap,
                    eval_dump_dir=eval_dump_dir,
                    strategy_folder=worker_raw_folder,
                )
            )
            orch_overhead = None
            execution_mode = "propose_validate"
            evidence_kind = "proposed_write_set"
    elif strategy == NAIVE_PARALLEL_BLIND_STRATEGY:
        if applied_diff_live:
            tasks, wall_s, prompt_token_method = asyncio.run(
                _run_naive_parallel_blind_applied(
                    lock,
                    repo_graph,
                    sub_factory,
                    checkout,
                    prompts_by_task=prompts_by_task,
                    cap_parallelism=resolved_parallel_cap,
                    eval_dump_dir=eval_dump_dir,
                    strategy_folder=worker_raw_folder,
                )
            )
            orch_overhead = None
            execution_mode = "applied_diff_live"
            evidence_kind = "naive_parallel_blind_applied_diff"
        else:
            tasks, wall_s, prompt_token_method = asyncio.run(
                _run_naive_parallel_blind(
                    lock,
                    repo_graph,
                    sub_factory,
                    prompts_by_task=prompts_by_task,
                    cap_parallelism=resolved_parallel_cap,
                    eval_dump_dir=eval_dump_dir,
                    strategy_folder=worker_raw_folder,
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
                cap_parallelism=resolved_parallel_cap,
                scope_repo_graph=strategy != ACG_PLANNED_FULL_CONTEXT_STRATEGY,
                auto_replan=(strategy == ACG_PLANNED_REPLAN_STRATEGY),
                eval_dump_dir=eval_dump_dir,
                strategy_folder=worker_raw_folder,
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
                cap_parallelism=resolved_parallel_cap,
                scope_repo_graph=(strategy in {ACG_PLANNED_STRATEGY, ACG_PLANNED_REPLAN_STRATEGY}),
                auto_replan=(strategy == ACG_PLANNED_REPLAN_STRATEGY),
                eval_dump_dir=eval_dump_dir,
                strategy_folder=worker_raw_folder,
            )
        )
        orch_overhead = None
        execution_mode = "propose_validate"
        evidence_kind = "proposed_write_set"

    # Compile-step accounting (planner tokens, wall time, cost) is charged
    # only to strategies that ACTUALLY consume the lockfile — i.e. the
    # acg_planned* family. ``naive_parallel`` and ``single_agent`` operate
    # blind (no predicted_writes, no allowed_paths), so charging them the
    # compile cost would inflate their headline numbers and *understate*
    # the relative ACG savings. The eval still records the compile cost
    # on the lockfile itself; this filter just controls which strategy
    # rows carry it in their summary_metrics.
    _strategy_uses_lock = strategy.startswith("acg_planned")
    summary = compute_summary_metrics(
        tasks,
        wall_time_seconds=wall_s,
        sequential_wall_time_seconds=sequential_wall_time_seconds,
        tokens_orchestrator_overhead=orch_overhead,
        tokens_planner_total=(
            lock.generator.tokens_planner_total
            if lock.generator is not None and _strategy_uses_lock
            else None
        ),
        tokens_planner_completion_total=(
            lock.generator.tokens_planner_completion_total
            if lock.generator is not None and _strategy_uses_lock
            else None
        ),
        tokens_planner_method=(
            lock.generator.tokens_planner_method
            if lock.generator is not None and _strategy_uses_lock
            else None
        ),
        tokens_scope_review_total=(
            lock.generator.tokens_scope_review_total
            if lock.generator is not None and _strategy_uses_lock
            else None
        ),
        compile_wall_seconds=(
            lock.generator.compile_wall_seconds
            if lock.generator is not None and _strategy_uses_lock
            else None
        ),
        compile_cost_usd=(
            lock.generator.compile_cost_usd
            if lock.generator is not None and _strategy_uses_lock
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
