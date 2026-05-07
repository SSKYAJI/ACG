"""``eval_run.json`` v0.1 — dataclasses, builders, and scoring helpers.

The single artifact this whole experiment produces is ``eval_run.json``.
Everything else (Devin sessions, local LLM proposals, the headtohead CLI,
reports) feeds this shape so we can compare strategies and backends with
one stable score.

Keep v0.1 forgiving: optional fields are ``None`` or ``[]``. Numeric
metrics never raise on a divide-by-zero; missing data is reported as
``0.0`` so the chart still renders.
"""

from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Any

from acg.enforce import validate_write
from acg.schema import AgentLock, Task

EVAL_VERSION = "0.1"
SUITE_NAME = "greenhouse-java6-modernization"
GREENHOUSE_REPO_URL = "https://github.com/spring-attic/greenhouse.git"
GREENHOUSE_PINNED_COMMIT = "174c1c320875a66447deb2a15d04fc86afd07f60"
SECONDS_PER_HOUR = 3600.0


# ---------------------------------------------------------------------------
# Per-task substructures.
# ---------------------------------------------------------------------------


@dataclass
class TaskTest:
    """Optional test result for a single task. ``ran=False`` means we skipped
    or could not run a test (Devin didn't run mvn, mock backend, etc.)."""

    command: str | None = None
    ran: bool = False
    exit_code: int | None = None
    passed: bool | None = None
    duration_seconds: float | None = None
    failed_tests: list[str] = field(default_factory=list)


@dataclass
class TaskTimestamps:
    started_at: str | None = None
    finished_at: str | None = None


@dataclass
class TaskMetrics:
    wall_time_seconds: float = 0.0
    model_calls: int | None = None
    tokens_prompt: int | None = None
    tokens_completion: int | None = None
    human_interventions: int = 0
    # Devin reports ``acus_consumed`` per session; populated by the
    # ``devin-api`` backend, ``None`` for mock/local where ACUs do not apply.
    acus_consumed: float | None = None
    cost_usd: float | None = None
    cost_source: str | None = None


@dataclass
class TaskArtifacts:
    diff_path: str | None = None
    pr_url: str | None = None
    log_path: str | None = None
    session_id: str | None = None
    branch: str | None = None
    commit: str | None = None


@dataclass
class BlockedWriteEvent:
    """One ``validate_write`` rejection captured during a planned run."""

    file: str
    description: str
    reason: str


@dataclass
class EvalTask:
    """Per-task block in ``eval_run.json``.

    ``predicted_write_files`` and ``allowed_write_globs`` are taken straight
    from the lockfile so reviewers can audit the mapping. For mock/local
    propose-and-validate runs, ``actual_changed_files`` is the accepted/proposed
    write set, not a git diff from mutated files. For applied-diff / Devin
    runs it is the backend-reported or git-derived applied diff file list.
    ``actual_changed_files_kind`` records which interpretation is valid for
    each task.
    """

    task_id: str
    status: str = "pending"
    failure_reason: str | None = None
    session_id: str | None = None
    prompt: str = ""
    predicted_write_files: list[str] = field(default_factory=list)
    allowed_write_globs: list[str] = field(default_factory=list)
    actual_changed_files: list[str] = field(default_factory=list)
    actual_changed_files_kind: str = "proposed_write_set"
    out_of_bounds_files: list[str] = field(default_factory=list)
    blocked_write_events: list[BlockedWriteEvent] = field(default_factory=list)
    overlaps_with: list[str] = field(default_factory=list)
    test: TaskTest = field(default_factory=TaskTest)
    timestamps: TaskTimestamps = field(default_factory=TaskTimestamps)
    metrics: TaskMetrics = field(default_factory=TaskMetrics)
    artifacts: TaskArtifacts = field(default_factory=TaskArtifacts)


# ---------------------------------------------------------------------------
# Top-level run.
# ---------------------------------------------------------------------------


@dataclass
class EvalRepo:
    url: str = GREENHOUSE_REPO_URL
    commit: str = GREENHOUSE_PINNED_COMMIT
    local_path: str = "experiments/greenhouse/checkout"


@dataclass
class EvalModel:
    provider: str | None = None
    model: str | None = None
    url: str | None = None


@dataclass
class SummaryMetrics:
    tasks_total: int = 0
    tasks_completed: int = 0
    task_completion_rate: float = 0.0
    # Proposal completion counts "worker produced an accepted proposal" for
    # propose-validate backends. It is not implementation correctness.
    proposal_completion_rate: float = 0.0
    tests_ran_count: int = 0
    tested_tasks_completed: int = 0
    tested_completion_rate: float = 0.0
    tasks_completed_per_hour: float = 0.0
    first_run_pass_rate: float = 0.0
    successful_parallel_speedup: float | None = None
    overlapping_write_pairs: int = 0
    out_of_bounds_write_count: int = 0
    blocked_invalid_write_count: int = 0
    merge_conflicts: int = 0
    human_interventions: int = 0
    wall_time_seconds: float = 0.0
    # Sum of per-task ``acus_consumed`` for the ``devin-api`` backend; ``None``
    # when no task reports ACUs (mock/local/devin-manual).
    acus_consumed_total: float | None = None
    # Sum of per-task ``tokens_prompt``. OpenAI-compatible providers may
    # populate this from ``usage.prompt_tokens``; otherwise the harness falls
    # back to chars//4 on the exact worker prompt strings. ``tokens_prompt_method``
    # records which path was used.
    tokens_prompt_total: int | None = None
    tokens_prompt_method: str | None = None
    # Sum of per-task ``tokens_completion`` (real output tokens reported by
    # the OpenAI-compatible ``usage`` block on the local backend).
    tokens_completion_total: int | None = None
    tokens_completion_method: str | None = None
    # Tokens spent on optional LLM coordination / plan-review calls outside
    # per-task worker prompts. Default local/mock ACG planned execution walks
    # the compiled lockfile directly, so this is normally ``None``.
    tokens_orchestrator_overhead: int | None = None
    cost_usd_total: float | None = None
    cost_method: str | None = None
    cost_source: str | None = None


@dataclass
class EvalRun:
    """Top-level ``eval_run.json`` document."""

    version: str = EVAL_VERSION
    run_id: str = ""
    created_at: str = ""
    suite_name: str = SUITE_NAME
    strategy: str = "naive_parallel"
    backend: str = "mock"
    execution_mode: str = "propose_validate"
    evidence_kind: str = "proposed_write_set"
    model: EvalModel = field(default_factory=EvalModel)
    repo: EvalRepo = field(default_factory=EvalRepo)
    lockfile: str = ""
    tasks: list[EvalTask] = field(default_factory=list)
    summary_metrics: SummaryMetrics = field(default_factory=SummaryMetrics)


# ---------------------------------------------------------------------------
# Builders.
# ---------------------------------------------------------------------------


def now_iso() -> str:
    """Return a timezone-aware UTC ISO-8601 timestamp (Z-suffixed)."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_run_id(strategy: str, backend: str) -> str:
    """Construct a deterministic-ish run id like ``greenhouse-mock-naive_parallel-2026...``."""
    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"greenhouse-{backend}-{strategy}-{ts}"


def _git_value(repo_path: Path, args: list[str]) -> str | None:
    """Return a git value for ``repo_path`` when it is available."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    value = proc.stdout.strip()
    return value or None


def repo_from_path(
    repo_path: Path | None,
    *,
    repo_url: str | None = None,
    repo_commit: str | None = None,
) -> EvalRepo:
    """Build honest repo metadata from a checkout, preserving Greenhouse defaults.

    If no ``repo_path`` is supplied, the historical Greenhouse defaults are
    retained for compatibility with existing tests and artifacts.
    """
    if repo_path is None:
        return EvalRepo(
            url=repo_url or GREENHOUSE_REPO_URL,
            commit=repo_commit or GREENHOUSE_PINNED_COMMIT,
            local_path="experiments/greenhouse/checkout",
        )
    resolved = Path(repo_path).resolve()
    return EvalRepo(
        url=repo_url or _git_value(resolved, ["remote", "get-url", "origin"]) or "",
        commit=repo_commit or _git_value(resolved, ["rev-parse", "HEAD"]) or "",
        local_path=str(resolved),
    )


def suite_name_from_lock(lock: AgentLock, explicit: str | None = None) -> str:
    """Choose a suite name without hardcoding Greenhouse for every codebase."""
    if explicit:
        return explicit
    root = (lock.repo.root or "").replace("\\", "/").rstrip("/")
    if "greenhouse" in root:
        return SUITE_NAME
    if root:
        path = Path(root)
        name = path.parent.name if path.name == "checkout" and path.parent.name else path.name
        return f"{name}-eval"
    return SUITE_NAME


def task_from_lock(task: Task, *, prompt: str | None = None) -> EvalTask:
    """Initialize an :class:`EvalTask` from a lockfile :class:`Task`.

    ``predicted_write_files`` and ``allowed_write_globs`` are the audit
    surface; the lockfile is the source of truth. ``prompt`` is taken from
    the original ``tasks.json`` when supplied so the Devin session prompt is
    reproducible from the artifact alone.
    """
    return EvalTask(
        task_id=task.id,
        prompt=prompt or task.prompt,
        predicted_write_files=[pw.path for pw in task.predicted_writes],
        allowed_write_globs=list(task.allowed_paths),
    )


def validate_actual_files(
    lock: AgentLock, task_id: str, actual_files: list[str]
) -> tuple[list[str], list[BlockedWriteEvent]]:
    """Split ``actual_files`` into in-bounds vs out-of-bounds for ``task_id``.

    Returns ``(out_of_bounds, blocked_events)``. ``blocked_events`` mirrors
    one entry per offending file with the validator reason — useful when a
    naive backend reports edits and we want to score them against ACG's
    boundary post-hoc.
    """
    out_of_bounds: list[str] = []
    events: list[BlockedWriteEvent] = []
    for file in actual_files:
        allowed, reason = validate_write(lock, task_id, file)
        if not allowed:
            out_of_bounds.append(file)
            events.append(
                BlockedWriteEvent(
                    file=file,
                    description="actual change outside allowed_paths",
                    reason=reason or "outside allowed_paths",
                )
            )
    return out_of_bounds, events


def compute_overlap_pairs(tasks: list[EvalTask]) -> int:
    """Count distinct task-pairs whose ``actual_changed_files`` intersect.

    Mirrors :mod:`benchmark.runner`'s ``overlap_pairs`` definition so the
    Greenhouse and demo-app charts use the same metric.
    """
    pair_count = 0
    for a, b in combinations(tasks, 2):
        if set(a.actual_changed_files) & set(b.actual_changed_files):
            pair_count += 1
    return pair_count


def annotate_overlaps(tasks: list[EvalTask]) -> None:
    """Populate each task's ``overlaps_with`` list in place."""
    by_id = {t.task_id: t for t in tasks}
    for task in tasks:
        my_files = set(task.actual_changed_files)
        peers = [
            other_id
            for other_id, other in by_id.items()
            if other_id != task.task_id and my_files & set(other.actual_changed_files)
        ]
        task.overlaps_with = sorted(peers)


def _is_completed(task: EvalTask) -> bool:
    """A conservative ``completed`` predicate matching the megaplan.

    ``completed_unsafe`` tasks (real backends that wrote outside their
    boundary) do **not** count as fully completed — the megaplan calls this
    out explicitly: "For sponsor claims, prefer conservative scoring:
    unsafe completion does not count as fully completed."
    """
    if task.status != "completed":
        return False
    if task.test.ran and task.test.passed is False:
        return False
    return True


def _is_proposal_completed(task: EvalTask) -> bool:
    """Completion for proposal-only evidence, separated from correctness."""
    if task.status not in {"completed", "completed_unsafe"}:
        return False
    return bool(task.actual_changed_files or task.blocked_write_events)


def _is_tested_completed(task: EvalTask) -> bool:
    """Implementation success requires an executed passing test."""
    return task.status == "completed" and task.test.ran and task.test.passed is True


def _is_first_run_pass(task: EvalTask) -> bool:
    """First-run pass ⇒ tests ran AND passed AND zero retries/interventions."""
    if not task.test.ran:
        return False
    if task.test.passed is not True:
        return False
    if task.metrics.human_interventions:
        return False
    return True


def compute_summary_metrics(
    tasks: list[EvalTask],
    *,
    wall_time_seconds: float,
    sequential_wall_time_seconds: float | None = None,
    merge_conflicts: int = 0,
    tokens_orchestrator_overhead: int | None = None,
    tokens_prompt_method: str | None = None,
    tokens_completion_method: str | None = None,
    cost_usd_total: float | None = None,
    cost_method: str | None = None,
    cost_source: str | None = None,
) -> SummaryMetrics:
    """Aggregate per-task data into the run-level summary.

    Args:
        tasks: All :class:`EvalTask` entries (must already have
            ``actual_changed_files`` and ``out_of_bounds_files`` populated).
        wall_time_seconds: Strategy wall-clock time. Used for
            ``tasks_completed_per_hour``.
        sequential_wall_time_seconds: Optional sequential baseline; populates
            ``successful_parallel_speedup`` when supplied and positive.
        merge_conflicts: Count of API/git-reported merge conflicts (caller
            tallies these from backend output).
        tokens_orchestrator_overhead: Estimated input tokens consumed by optional
            coordination / plan-review calls outside per-task worker prompts.
    """
    total = len(tasks)
    completed = sum(1 for t in tasks if _is_completed(t))
    proposal_completed = sum(1 for t in tasks if _is_proposal_completed(t))
    first_run = sum(1 for t in tasks if _is_first_run_pass(t))
    tests_ran = sum(1 for t in tasks if t.test.ran)
    tested_completed = sum(1 for t in tasks if _is_tested_completed(t))
    rate = completed / total if total else 0.0
    proposal_rate = proposal_completed / total if total else 0.0
    tested_rate = tested_completed / total if total else 0.0
    per_hour = completed / (wall_time_seconds / SECONDS_PER_HOUR) if wall_time_seconds > 0 else 0.0
    pass_rate = first_run / total if total else 0.0
    speedup: float | None
    if (
        sequential_wall_time_seconds is not None
        and sequential_wall_time_seconds > 0
        and wall_time_seconds > 0
    ):
        speedup = round(sequential_wall_time_seconds / wall_time_seconds, 4)
    else:
        speedup = None

    acu_values = [t.metrics.acus_consumed for t in tasks if t.metrics.acus_consumed is not None]
    acus_total: float | None = round(sum(acu_values), 4) if acu_values else None

    prompt_values = [t.metrics.tokens_prompt for t in tasks if t.metrics.tokens_prompt is not None]
    completion_values = [
        t.metrics.tokens_completion for t in tasks if t.metrics.tokens_completion is not None
    ]
    prompt_total: int | None = sum(prompt_values) if prompt_values else None
    completion_total: int | None = sum(completion_values) if completion_values else None
    task_cost_values = [t.metrics.cost_usd for t in tasks if t.metrics.cost_usd is not None]
    if cost_usd_total is None and task_cost_values:
        cost_usd_total = round(sum(task_cost_values), 8)
        cost_method = cost_method or "sum_provider_reported_task_costs"
        sources = sorted({t.metrics.cost_source or "provider_response" for t in tasks if t.metrics.cost_usd is not None})
        cost_source = cost_source or ",".join(sources)

    return SummaryMetrics(
        tasks_total=total,
        tasks_completed=completed,
        task_completion_rate=round(rate, 4),
        proposal_completion_rate=round(proposal_rate, 4),
        tests_ran_count=tests_ran,
        tested_tasks_completed=tested_completed,
        tested_completion_rate=round(tested_rate, 4),
        tasks_completed_per_hour=round(per_hour, 4),
        first_run_pass_rate=round(pass_rate, 4),
        successful_parallel_speedup=speedup,
        overlapping_write_pairs=compute_overlap_pairs(tasks),
        out_of_bounds_write_count=sum(len(t.out_of_bounds_files) for t in tasks),
        blocked_invalid_write_count=sum(len(t.blocked_write_events) for t in tasks),
        merge_conflicts=merge_conflicts,
        human_interventions=sum(t.metrics.human_interventions for t in tasks),
        wall_time_seconds=round(wall_time_seconds, 4),
        acus_consumed_total=acus_total,
        tokens_prompt_total=prompt_total,
        tokens_prompt_method=tokens_prompt_method if prompt_total is not None else None,
        tokens_completion_total=completion_total,
        tokens_completion_method=(
            tokens_completion_method if completion_total is not None else None
        ),
        tokens_orchestrator_overhead=tokens_orchestrator_overhead,
        cost_usd_total=cost_usd_total,
        cost_method=cost_method,
        cost_source=cost_source,
    )


# ---------------------------------------------------------------------------
# Serialization.
# ---------------------------------------------------------------------------


def to_dict(run: EvalRun) -> dict[str, Any]:
    """Convert an :class:`EvalRun` into a plain dict suitable for JSON dump."""
    return asdict(run)


def write_eval_run(run: EvalRun, out_path: Path) -> Path:
    """Write ``run`` to ``out_path`` with stable formatting.

    Uses ``sort_keys=True`` and ``indent=2`` so artifacts diff cleanly
    across runs.
    """
    import json

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = to_dict(run)
    out_path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")
    return out_path


__all__ = [
    "BlockedWriteEvent",
    "EVAL_VERSION",
    "EvalRepo",
    "EvalRun",
    "EvalTask",
    "EvalModel",
    "GREENHOUSE_PINNED_COMMIT",
    "GREENHOUSE_REPO_URL",
    "SECONDS_PER_HOUR",
    "SUITE_NAME",
    "SummaryMetrics",
    "TaskArtifacts",
    "TaskMetrics",
    "TaskTest",
    "TaskTimestamps",
    "annotate_overlaps",
    "compute_overlap_pairs",
    "compute_summary_metrics",
    "make_run_id",
    "now_iso",
    "repo_from_path",
    "suite_name_from_lock",
    "task_from_lock",
    "to_dict",
    "validate_actual_files",
    "write_eval_run",
]
