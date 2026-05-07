"""Applied-diff and Devin-flavored backends for the Greenhouse harness.

Three backends live here:

- ``applied-diff``: reads a generic sidecar JSON where each task points at
  a branch/head/worktree and scores the actual git diff against the lockfile.
  This is the preferred paper evidence path for file-level collision claims.
- ``devin-manual``: reads a sidecar JSON (paths defined below) where the
  human author has pasted what came out of one or more Devin sessions —
  changed files, status, optional test result, optional PR/branch URLs.
  Use this when API extraction is partial. Honest ``human_interventions``
  counts go up by 1 per task whose data was hand-collected.
- ``devin-api``: live HTTPS API integration. Stub-only until the Devin
  endpoint contract is confirmed (see :func:`devin_api_run`'s docstring
  for the exact questions the human author needs to feed Perplexity /
  Devin contact).

Both backends return :class:`EvalRun` instances with the same shape as the
mock/local backends so :mod:`report` can chart them uniformly.

Manual/applied-diff sidecar JSON (``--diff-results`` or
``--devin-results``) format:

```jsonc
{
  "strategy": "naive_parallel",  // or "acg_planned"
  "wall_time_seconds": 1830.0,   // sequential or parallel wall clock
  "repo_path": "experiments/greenhouse/checkout", // optional git source for diffs
  "base_ref": "main",            // optional; task entry may override
  "tasks": [
    {
      "task_id": "lambda-rowmapper-account",
      "session_id": "devin-abc123",
      "status": "completed",     // completed | completed_unsafe | failed
      "branch": "task/lambda-account", // optional; used as git diff head_ref
      "actual_changed_files": [
        "src/main/java/.../JdbcAccountRepository.java",
        "pom.xml"
      ],
      "test": {
        "command": "mvn -pl . test -Dtest=AccountSomethingTest",
        "ran": true,
        "exit_code": 0,
        "passed": true,
        "duration_seconds": 27.4
      },
      "wall_time_seconds": 612.0,
      "started_at": "2026-04-25T18:00:00Z",
      "finished_at": "2026-04-25T18:10:12Z",
      "pr_url": "https://github.com/.../pull/42",
      "human_interventions": 1
    }
  ]
}
```

Missing keys are tolerated. If ``actual_changed_files`` is omitted and
``repo_path`` is present, the loader computes it with ``git diff --name-only``
from ``base_ref`` to the task's ``head_ref``/``branch`` (or to the current
worktree when no head is supplied).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from acg.schema import AgentLock

from .eval_schema import (
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
    validate_actual_files,
)

# ---------------------------------------------------------------------------
# devin-manual.
# ---------------------------------------------------------------------------


class DevinManualError(ValueError):
    """Raised when the Devin sidecar JSON cannot be loaded or parsed."""


def _load_manual(devin_results_path: Path) -> dict[str, Any]:
    try:
        return json.loads(Path(devin_results_path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise DevinManualError(
            f"could not load Devin results from {devin_results_path}: {exc}"
        ) from exc


def _git_text(repo_path: Path, args: list[str]) -> str:
    """Run a read-only git command and return stdout."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise DevinManualError(
            f"git {' '.join(args)} failed in {repo_path}: {detail}"
        ) from exc
    return proc.stdout


def _changed_files_from_git_diff(
    repo_path: Path, *, base_ref: str, head_ref: str | None = None
) -> list[str]:
    """Return repo-relative paths changed by ``head_ref`` or the worktree.

    ``head_ref`` is appropriate for committed task branches. When omitted, we
    compare the current worktree to ``base_ref`` and include untracked files,
    which makes the helper useful for local agents that edited a checkout but
    did not commit.
    """
    if not repo_path.exists():
        raise DevinManualError(f"git diff repo_path does not exist: {repo_path}")
    diff_target = f"{base_ref}...{head_ref}" if head_ref else base_ref
    changed = {
        line.strip()
        for line in _git_text(
            repo_path,
            ["diff", "--name-only", "--diff-filter=ACMRTUXB", diff_target, "--"],
        ).splitlines()
        if line.strip()
    }
    if head_ref is None:
        changed.update(
            line.strip()
            for line in _git_text(
                repo_path, ["ls-files", "--others", "--exclude-standard"]
            ).splitlines()
            if line.strip()
        )
    return sorted(changed)


def _task_from_manual_entry(
    entry: dict[str, Any],
    lock: AgentLock,
    *,
    strategy: str,
    prompts_by_task: dict[str, str] | None = None,
    default_repo_path: str | None = None,
    default_base_ref: str | None = None,
) -> EvalTask:
    """Translate one manual-sidecar task dict into an :class:`EvalTask`.

    Out-of-bounds files are scored against the lockfile; even if the human
    marked the task ``completed``, the validator can flip it to
    ``completed_unsafe`` for the conservative scoring path.
    """
    task_id = entry.get("task_id") or ""
    lock_task = next((t for t in lock.tasks if t.id == task_id), None)
    if lock_task is None:
        raise DevinManualError(f"task {task_id!r} not found in lockfile")

    eval_task = task_from_lock(
        lock_task, prompt=(prompts_by_task or {}).get(task_id, lock_task.prompt)
    )
    eval_task.actual_changed_files_kind = "applied_diff"
    eval_task.session_id = entry.get("session_id")
    actual_changed_files = sorted(set(entry.get("actual_changed_files") or []))
    repo_path = entry.get("repo_path") or default_repo_path
    base_ref = entry.get("base_ref") or default_base_ref or "HEAD"
    head_ref = entry.get("head_ref") or entry.get("branch")
    if not actual_changed_files and repo_path:
        actual_changed_files = _changed_files_from_git_diff(
            Path(repo_path), base_ref=str(base_ref), head_ref=head_ref
        )
    eval_task.actual_changed_files = actual_changed_files
    eval_task.timestamps.started_at = entry.get("started_at")
    eval_task.timestamps.finished_at = entry.get("finished_at")
    eval_task.metrics.wall_time_seconds = float(entry.get("wall_time_seconds") or 0.0)
    eval_task.metrics.human_interventions = int(entry.get("human_interventions") or 0)
    eval_task.artifacts.session_id = entry.get("session_id")
    eval_task.artifacts.pr_url = entry.get("pr_url")
    eval_task.artifacts.branch = entry.get("branch")
    eval_task.artifacts.commit = entry.get("commit")
    eval_task.artifacts.diff_path = entry.get("diff_path")
    eval_task.artifacts.log_path = entry.get("log_path")

    test_payload = entry.get("test") or {}
    eval_task.test.command = test_payload.get("command")
    eval_task.test.ran = bool(test_payload.get("ran", False))
    if "exit_code" in test_payload:
        eval_task.test.exit_code = test_payload["exit_code"]
    if "passed" in test_payload:
        eval_task.test.passed = bool(test_payload["passed"])
    if "duration_seconds" in test_payload:
        eval_task.test.duration_seconds = float(test_payload["duration_seconds"])
    eval_task.test.failed_tests = list(test_payload.get("failed_tests") or [])

    raw_status = (entry.get("status") or "completed").lower().strip()

    # Score out-of-bounds vs allowed_paths regardless of strategy so the
    # naive run honestly shows safety violations.
    out_of_bounds, blocked = validate_actual_files(lock, task_id, eval_task.actual_changed_files)
    eval_task.out_of_bounds_files = out_of_bounds
    if strategy == "acg_planned":
        # In planned mode, a real backend going out-of-bounds counts as
        # blocked-after-the-fact (we'd have caught it pre-flight if it had
        # gone through validate_write at edit time).
        eval_task.blocked_write_events = blocked
    eval_task.failure_reason = entry.get("failure_reason")

    if raw_status == "failed":
        eval_task.status = "failed"
        if not eval_task.failure_reason:
            eval_task.failure_reason = "AGENT_FAIL"
    elif out_of_bounds:
        eval_task.status = "completed_unsafe"
    else:
        eval_task.status = raw_status or "completed"
    return eval_task


def _run_manual_sidecar(
    *,
    strategy: str,
    lock: AgentLock,
    lockfile_path: str,
    results_path: Path,
    prompts_by_task: dict[str, str] | None = None,
    sequential_wall_time_seconds: float | None = None,
    suite_name: str | None = None,
    repo: EvalRepo | None = None,
    backend: str,
    execution_mode: str,
    model: EvalModel,
) -> EvalRun:
    """Build an :class:`EvalRun` from a manual/applied-diff sidecar JSON."""
    payload = _load_manual(results_path)
    raw_strategy = payload.get("strategy") or strategy
    if raw_strategy != strategy:
        raise DevinManualError(
            f"sidecar declares strategy={raw_strategy!r} but harness asked for {strategy!r}"
        )
    raw_tasks = payload.get("tasks") or []
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise DevinManualError("sidecar 'tasks' must be a non-empty list")

    eval_tasks = [
        _task_from_manual_entry(
            entry,
            lock,
            strategy=strategy,
            prompts_by_task=prompts_by_task,
            default_repo_path=payload.get("repo_path"),
            default_base_ref=payload.get("base_ref") or lock.repo.commit,
        )
        for entry in raw_tasks
    ]
    annotate_overlaps(eval_tasks)

    wall_time_seconds = float(payload.get("wall_time_seconds") or 0.0)
    summary = compute_summary_metrics(
        eval_tasks,
        wall_time_seconds=wall_time_seconds,
        sequential_wall_time_seconds=sequential_wall_time_seconds,
        merge_conflicts=int(payload.get("merge_conflicts") or 0),
    )
    return EvalRun(
        run_id=make_run_id(strategy, backend),
        created_at=now_iso(),
        suite_name=suite_name or suite_name_from_lock(lock),
        strategy=strategy,
        backend=backend,
        execution_mode=execution_mode,
        evidence_kind="applied_diff",
        model=model,
        repo=repo
        or repo_from_path(
            Path(payload.get("repo_path") or lock.repo.root)
            if (payload.get("repo_path") or lock.repo.root)
            else None,
            repo_url=lock.repo.git_url,
            repo_commit=lock.repo.commit,
        ),
        lockfile=lockfile_path,
        tasks=eval_tasks,
        summary_metrics=summary,
    )


def run_applied_diff_manual(
    *,
    strategy: str,
    lock: AgentLock,
    lockfile_path: str,
    diff_results_path: Path,
    prompts_by_task: dict[str, str] | None = None,
    sequential_wall_time_seconds: float | None = None,
    suite_name: str | None = None,
    repo: EvalRepo | None = None,
) -> EvalRun:
    """Build an applied-diff :class:`EvalRun` from task branch/worktree diffs.

    This is intentionally provider-neutral: the sidecar may describe patches
    produced by any agent or by a local worktree. If ``actual_changed_files``
    is omitted, the loader computes it with ``git diff --name-only`` from the
    sidecar's ``repo_path``/``base_ref`` and each task's ``branch`` or
    ``head_ref``.
    """
    return _run_manual_sidecar(
        strategy=strategy,
        lock=lock,
        lockfile_path=lockfile_path,
        results_path=diff_results_path,
        prompts_by_task=prompts_by_task,
        sequential_wall_time_seconds=sequential_wall_time_seconds,
        suite_name=suite_name,
        repo=repo,
        backend="applied-diff",
        execution_mode="applied_diff",
        model=EvalModel(provider="manual", model="git-diff-sidecar"),
    )


def run_devin_manual(
    *,
    strategy: str,
    lock: AgentLock,
    lockfile_path: str,
    devin_results_path: Path,
    prompts_by_task: dict[str, str] | None = None,
    sequential_wall_time_seconds: float | None = None,
    suite_name: str | None = None,
    repo: EvalRepo | None = None,
) -> EvalRun:
    """Build an :class:`EvalRun` from a human-collected Devin sidecar JSON."""
    return _run_manual_sidecar(
        strategy=strategy,
        lock=lock,
        lockfile_path=lockfile_path,
        results_path=devin_results_path,
        prompts_by_task=prompts_by_task,
        sequential_wall_time_seconds=sequential_wall_time_seconds,
        suite_name=suite_name,
        repo=repo,
        backend="devin-manual",
        execution_mode="manual_diff",
        model=EvalModel(provider="devin", model="manual-sidecar"),
    )


# ---------------------------------------------------------------------------
# devin-api — live HTTPS integration against the v3 organization-scoped API.
# ---------------------------------------------------------------------------


class DevinAPINotConfigured(RuntimeError):
    """Raised when ``DEVIN_API_KEY`` / ``DEVIN_ORG_ID`` are missing.

    Subclasses :class:`RuntimeError` (was a ``NotImplementedError`` while
    the backend was a stub). Tests asserting ``pytest.raises(NotImplementedError)``
    must update to :class:`DevinAPINotConfigured` directly.
    """


def devin_api_run(
    *,
    strategy: str,
    lock: AgentLock,
    lockfile_path: str,
    repo_url: str,
    base_branch: str = "master",
    run_id_hint: str | None = None,
    max_parallelism: int = 5,
    poll_interval_s: float = 30.0,
    max_wait_s: float = 2700.0,
    request_timeout_s: float = 60.0,
    max_acu_limit: int | None = None,
    devin_extra_body: dict[str, Any] | None = None,
    sequential_wall_time_seconds: float | None = None,
    client: Any = None,
    suite_name: str | None = None,
    repo: EvalRepo | None = None,
) -> EvalRun:
    """Run all tasks against the live Devin v3 API and build an :class:`EvalRun`.

    Strategy semantics:

    - ``naive_parallel`` — submit every task simultaneously, no contract,
      no dependency context. Sessions race; collisions surface in the
      eval artifact.
    - ``acg_planned`` — walk ``lock.execution_plan.groups`` in order;
      within each group, submit tasks in parallel; wait for the entire
      group to terminate before starting the next one. Each task's
      prompt embeds its ``allowed_paths`` plus any cross-task conflict
      context surfaced by the planner.

    Args:
        repo_url: HTTPS URL of the GitHub fork the user has connected to
            their Devin org. Devin clones from here and opens PRs back
            to it.
        base_branch: Default branch (e.g. ``master``).
        max_parallelism: Upper bound on concurrent in-flight sessions. The
            v3 docs do not publish an explicit limit; default is conservative.
        poll_interval_s / max_wait_s: Polling cadence and per-session
            timeout. Devin codegen sessions typically run 5–30 min.
        max_acu_limit: Optional ACU guardrail per session.
        devin_extra_body: Optional extra fields merged into every
            ``POST /sessions`` body. Used to opt into preview agent
            variants — e.g. ``{"agent": "fast-mode"}`` for the Devin
            "Fast Mode" picker, or ``{"agent": "opus-4.7"}`` for the
            Opus 4.7 model. Devin gracefully ignores unknown fields, so
            this is safe to use as an experimental probe.
        client: Override the :class:`DevinClient`; tests pass a client
            built with ``httpx.MockTransport`` to avoid live calls.

    Raises:
        DevinAPINotConfigured: ``DEVIN_API_KEY`` / ``DEVIN_ORG_ID`` are
            missing and no ``client`` was injected.
        ValueError: ``strategy`` is unknown.
    """
    import asyncio

    from .devin_api import DevinAPIError, DevinClient

    if strategy not in {"naive_parallel", "acg_planned"}:
        raise ValueError(f"unknown strategy {strategy!r}; expected naive_parallel or acg_planned")
    if not repo_url:
        raise ValueError("repo_url is required for the devin-api backend")

    owns_client = False
    if client is None:
        try:
            client = DevinClient.from_env(timeout_s=request_timeout_s)
        except DevinAPIError as exc:
            raise DevinAPINotConfigured(str(exc)) from exc
        owns_client = True

    async def _amain() -> EvalRun:
        try:
            return await _devin_api_run_async(
                client=client,
                strategy=strategy,
                lock=lock,
                lockfile_path=lockfile_path,
                repo_url=repo_url,
                base_branch=base_branch,
                run_id_hint=run_id_hint,
                max_parallelism=max_parallelism,
                poll_interval_s=poll_interval_s,
                max_wait_s=max_wait_s,
                max_acu_limit=max_acu_limit,
                devin_extra_body=devin_extra_body,
                sequential_wall_time_seconds=sequential_wall_time_seconds,
                suite_name=suite_name,
                repo=repo,
            )
        finally:
            if owns_client:
                await client.aclose()

    return asyncio.run(_amain())


async def _devin_api_run_async(
    *,
    client: Any,
    strategy: str,
    lock: AgentLock,
    lockfile_path: str,
    repo_url: str,
    base_branch: str,
    run_id_hint: str | None,
    max_parallelism: int,
    poll_interval_s: float,
    max_wait_s: float,
    max_acu_limit: int | None,
    devin_extra_body: dict[str, Any] | None,
    sequential_wall_time_seconds: float | None,
    suite_name: str | None,
    repo: EvalRepo | None,
) -> EvalRun:
    """Async core for :func:`devin_api_run`. Pure orchestration."""
    import asyncio
    import time as _time

    from acg.schema import Task

    from .devin_api import (
        CHANGED_FILES_SCHEMA,
        DevinAPIError,
        extract_changed_files,
    )
    from .devin_prompts import build_naive_prompt, build_planned_prompt

    run_id = run_id_hint or make_run_id(strategy, "devin-api")
    eval_tasks_by_id: dict[str, EvalTask] = {task.id: task_from_lock(task) for task in lock.tasks}
    for eval_task in eval_tasks_by_id.values():
        eval_task.actual_changed_files_kind = "applied_diff"

    sem = asyncio.Semaphore(max(1, max_parallelism))

    async def _run_one_task(task: Task) -> None:
        eval_task = eval_tasks_by_id[task.id]
        prompt = (
            build_planned_prompt(
                task,
                repo_url=repo_url,
                base_branch=base_branch,
                lock=lock,
            )
            if strategy == "acg_planned"
            else build_naive_prompt(
                task,
                repo_url=repo_url,
                base_branch=base_branch,
            )
        )
        eval_task.prompt = prompt
        tags = [
            f"strategy={strategy}",
            f"task_id={task.id}",
            f"run_id={run_id}",
            "harness=acg-greenhouse",
        ]
        title = f"[ACG/{strategy}] {task.id}"
        eval_task.timestamps.started_at = now_iso()
        started_perf = _time.perf_counter()
        async with sem:
            try:
                created = await client.create_session(
                    prompt=prompt,
                    tags=tags,
                    structured_output_schema=CHANGED_FILES_SCHEMA,
                    max_acu_limit=max_acu_limit,
                    title=title,
                    extra_body=devin_extra_body,
                )
                eval_task.session_id = created.session_id
                eval_task.artifacts.session_id = created.session_id
                final = await client.wait_for_terminal(
                    created.session_id,
                    poll_interval_s=poll_interval_s,
                    max_wait_s=max_wait_s,
                )
            except DevinAPIError as exc:
                eval_task.status = "failed"
                eval_task.failure_reason = f"DEVIN_API_ERROR_{exc.status_code}"
                eval_task.timestamps.finished_at = now_iso()
                eval_task.metrics.wall_time_seconds = round(_time.perf_counter() - started_perf, 4)
                return

        # Collect messages so we have the message-fallback extraction path
        # plus a model_calls heuristic (Devin replies count as model calls).
        try:
            messages = await client.get_messages(created.session_id)
        except DevinAPIError:
            messages = []

        eval_task.metrics.wall_time_seconds = round(_time.perf_counter() - started_perf, 4)
        eval_task.timestamps.finished_at = now_iso()
        eval_task.metrics.acus_consumed = final.acus_consumed
        eval_task.metrics.model_calls = (
            sum(1 for m in messages if m.source.lower() == "devin") or None
        )

        extraction = extract_changed_files(final, messages)
        eval_task.actual_changed_files = sorted(set(extraction.files))
        if final.pull_requests:
            pr = final.pull_requests[0]
            eval_task.artifacts.pr_url = pr.url or eval_task.artifacts.pr_url
            eval_task.artifacts.branch = pr.branch or eval_task.artifacts.branch
        if extraction.pr_url and not eval_task.artifacts.pr_url:
            eval_task.artifacts.pr_url = extraction.pr_url
        if extraction.branch and not eval_task.artifacts.branch:
            eval_task.artifacts.branch = extraction.branch

        # Score allowed_paths violations regardless of strategy. Even
        # naive runs flag out-of-bounds writes so we honestly count safety
        # incidents that ACG would have caught.
        out_of_bounds, blocked = validate_actual_files(
            lock, task.id, eval_task.actual_changed_files
        )
        eval_task.out_of_bounds_files = out_of_bounds
        if strategy == "acg_planned":
            eval_task.blocked_write_events = blocked

        if not _devin_status_is_success(final):
            eval_task.status = "failed"
            eval_task.failure_reason = (
                f"DEVIN_NON_TERMINAL_SUCCESS:{final.status}/{final.status_detail}"
            )
        elif out_of_bounds:
            eval_task.status = "completed_unsafe"
        else:
            eval_task.status = "completed"

    # Strategy-specific submission order.
    overall_start = _time.perf_counter()
    if strategy == "naive_parallel":
        await asyncio.gather(*(_run_one_task(t) for t in lock.tasks))
    else:
        # acg_planned — walk execution_plan groups in order; within each
        # group, fan out in parallel; wait for the group to finish before
        # starting the next one.
        tasks_by_id = {t.id: t for t in lock.tasks}
        for group in lock.execution_plan.groups:
            await asyncio.gather(
                *(_run_one_task(tasks_by_id[tid]) for tid in group.tasks if tid in tasks_by_id)
            )
    overall_wall = _time.perf_counter() - overall_start

    eval_tasks = [eval_tasks_by_id[t.id] for t in lock.tasks]
    annotate_overlaps(eval_tasks)
    summary = compute_summary_metrics(
        eval_tasks,
        wall_time_seconds=overall_wall,
        sequential_wall_time_seconds=sequential_wall_time_seconds,
    )
    agent = (devin_extra_body or {}).get("agent") or "default"
    return EvalRun(
        run_id=run_id,
        created_at=now_iso(),
        suite_name=suite_name or suite_name_from_lock(lock),
        strategy=strategy,
        backend="devin-api",
        execution_mode="devin_diff",
        evidence_kind="applied_diff",
        model=EvalModel(
            provider="devin",
            model=str(agent),
            url=getattr(client, "base_url", None),
        ),
        repo=repo
        or repo_from_path(
            Path(lock.repo.root) if lock.repo.root else None,
            repo_url=lock.repo.git_url,
            repo_commit=lock.repo.commit,
        ),
        lockfile=lockfile_path,
        tasks=eval_tasks,
        summary_metrics=summary,
    )


def _devin_status_is_success(detail: Any) -> bool:
    """Lazy import shim around :meth:`DevinSessionDetail.is_success`.

    Kept as a module-level helper so the dataclass module isn't a hard
    import dependency at module-load time (lets ``--backend mock`` users
    avoid pulling httpx into the import graph if they don't need it).
    """
    return bool(getattr(detail, "is_success", lambda: False)())


__all__ = [
    "DevinAPINotConfigured",
    "DevinManualError",
    "devin_api_run",
    "run_applied_diff_manual",
    "run_devin_manual",
]
