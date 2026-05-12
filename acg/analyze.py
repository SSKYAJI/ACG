"""Run-trace analyzer — predictor-accuracy + lockfile-tightness report.

Reads one or more ``eval_run_*.json`` artifacts (or a directory containing
them) and emits a Markdown report covering:

* **Predictor accuracy.** Per-task precision, recall, and F1 between the
  lockfile's ``predicted_write_files`` and the agent-reported
  ``actual_changed_files``. Aggregated across runs when the same task
  appears more than once.
* **Contract enforcement.** Per-task counts of ``out_of_bounds_files`` and
  ``blocked_write_events`` — i.e. proposals the validator rejected because
  they fell outside ``allowed_paths``.
* **Scope coverage.** Ratio of ``actual_changed_files`` to the lockfile's
  ``allowed_write_globs`` glob count, surfacing tasks whose declared scope
  is much larger than what the agent actually touched.
* **Refinement suggestions.** Heuristic recommendations to tighten or widen
  ``predicted_writes`` and ``allowed_paths`` based on the patterns observed
  in the artifacts.

The output is intentionally textual so it composes with shell pipelines and
review tooling (e.g. ``gh pr comment --body-file report.md``). It is also
the substrate for the megaplan's "learn from mistakes" loop: the same data
can later feed the predictor's seed re-weighting.

Usage::

    from acg.analyze import analyze_paths, format_markdown
    report = analyze_paths([Path("eval_run.json")])
    print(format_markdown(report))
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TaskAnalysis:
    """Per-task aggregate across one or more eval_run artifacts."""

    task_id: str
    runs_seen: int = 0
    predicted_files: set[str] = field(default_factory=set)
    actual_files_seen: set[str] = field(default_factory=set)
    actual_files_total: int = 0  # sum across runs (not deduped)
    out_of_bounds_files: list[str] = field(default_factory=list)
    blocked_events_total: int = 0
    allowed_glob_count: int = 0
    backends_seen: set[str] = field(default_factory=set)
    strategies_seen: set[str] = field(default_factory=set)
    statuses: list[str] = field(default_factory=list)

    @property
    def true_positives(self) -> int:
        """Files predicted by ACG that the agent actually wrote."""
        return len(self.predicted_files & self.actual_files_seen)

    @property
    def false_positives(self) -> int:
        """Files predicted by ACG that the agent never wrote."""
        return len(self.predicted_files - self.actual_files_seen)

    @property
    def false_negatives(self) -> int:
        """Files the agent wrote that ACG did not predict."""
        return len(self.actual_files_seen - self.predicted_files)

    @property
    def precision(self) -> float:
        denom = self.true_positives + self.false_positives
        return (self.true_positives / denom) if denom else 0.0

    @property
    def recall(self) -> float:
        denom = self.true_positives + self.false_negatives
        return (self.true_positives / denom) if denom else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return (2 * p * r / (p + r)) if (p + r) > 0 else 0.0


@dataclass
class ReplanRescueEntry:
    """One task that benefited from an approved replan (acg_planned_replan*)."""

    source_path: str
    strategy: str
    task_id: str
    files: list[str]


@dataclass
class RunSummary:
    """Per-eval-run-file metadata."""

    source_path: str
    strategy: str
    backend: str
    suite_name: str
    execution_mode: str
    evidence_kind: str
    tasks_total: int
    tasks_completed: int
    tests_ran_count: int
    tested_tasks_completed: int
    overlapping_write_pairs: int
    out_of_bounds_write_count: int
    blocked_invalid_write_count: int
    tokens_prompt_total: int | None
    tokens_all_in: int | None
    tokens_prompt_method: str | None
    tokens_orchestrator_overhead: int | None
    cost_usd_total: float | None
    cost_method: str | None
    cost_source: str | None
    wall_time_seconds: float
    tokens_completion_total: int | None
    patch_na_count: int
    typecheck_pass_count: int
    typecheck_fail_count: int
    typecheck_skipped_count: int
    tokens_total_per_task_mean: float | None
    cost_per_completed_task: float | None
    oob_files_per_task_mean: float | None
    replan_rescued_count: int


@dataclass
class AnalysisReport:
    """Aggregated cross-run report."""

    runs: list[RunSummary] = field(default_factory=list)
    tasks: dict[str, TaskAnalysis] = field(default_factory=dict)
    replan_rescues: list[ReplanRescueEntry] = field(default_factory=list)

    @property
    def total_runs(self) -> int:
        return len(self.runs)

    @property
    def overall_precision(self) -> float:
        tp = sum(t.true_positives for t in self.tasks.values())
        fp = sum(t.false_positives for t in self.tasks.values())
        return tp / (tp + fp) if (tp + fp) else 0.0

    @property
    def overall_recall(self) -> float:
        tp = sum(t.true_positives for t in self.tasks.values())
        fn = sum(t.false_negatives for t in self.tasks.values())
        return tp / (tp + fn) if (tp + fn) else 0.0

    @property
    def overall_f1(self) -> float:
        p, r = self.overall_precision, self.overall_recall
        return (2 * p * r / (p + r)) if (p + r) > 0 else 0.0

    @property
    def total_blocks(self) -> int:
        return sum(t.blocked_events_total for t in self.tasks.values())

    @property
    def total_oob(self) -> int:
        return self.total_proposal_oob + self.total_posthoc_oob

    @property
    def total_proposal_oob(self) -> int:
        return sum(r.out_of_bounds_write_count for r in self.runs if not _is_applied_diff_run(r))

    @property
    def total_posthoc_oob(self) -> int:
        return sum(r.out_of_bounds_write_count for r in self.runs if _is_applied_diff_run(r))


def _is_applied_diff_run(run: RunSummary) -> bool:
    """Return True when OOB files came from applied/manual/hosted diffs."""
    if run.evidence_kind in {"applied_diff", "manual_diff", "devin_diff"}:
        return True
    if run.execution_mode in {"applied_diff", "manual_diff", "devin_diff"}:
        return True
    if run.evidence_kind in {"proposed_write_set"} or run.execution_mode in {"propose_validate"}:
        return False
    # Backward compatibility for older artifacts written before evidence_kind
    # existed: Devin backends report applied diffs, local/mock report proposals.
    return run.backend in {"devin-api", "devin-manual"}


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def _load_run(path: Path) -> list[dict[str, Any]]:
    """Return the list of strategy-runs found in ``path``.

    Supports both single-strategy ``eval_run.json`` files and combined files
    (``eval_run_combined.json``) that nest ``strategies.<name>``.
    """
    payload = json.loads(path.read_text())
    if isinstance(payload, dict) and "strategies" in payload:
        return [
            {**strategy_payload, "_source_path": str(path)}
            for strategy_payload in payload["strategies"].values()
        ]
    payload["_source_path"] = str(path)
    return [payload]


def _expand_paths(inputs: Iterable[Path]) -> list[Path]:
    """Resolve directories to ``eval_run*.json`` files within them."""
    out: list[Path] = []
    for item in inputs:
        if item.is_dir():
            out.extend(sorted(item.glob("eval_run*.json")))
        elif item.is_file():
            out.append(item)
    # De-duplicate while preserving order.
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in out:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _task_metrics_dict(task: dict[str, Any]) -> dict[str, Any]:
    raw = task.get("metrics")
    return raw if isinstance(raw, dict) else {}


def _honest_metrics_from_task_dicts(
    tasks: list[dict[str, Any]], evidence_kind: str
) -> dict[str, Any]:
    """Mirror :func:`compute_summary_metrics` honest fields for JSON task dicts."""
    applied_like = "applied_diff" in (evidence_kind or "").lower()
    patch_na_count = 0
    typecheck_pass_count = 0
    typecheck_fail_count = 0
    replan_rescued_count = 0
    per_task_token_totals: list[float] = []

    for task in tasks:
        m = _task_metrics_dict(task)
        if m.get("patch_applies") is False:
            patch_na_count += 1
        if m.get("typecheck_ran"):
            code = m.get("typecheck_exit_code")
            if code == 0:
                typecheck_pass_count += 1
            elif code not in (None, 0):
                typecheck_fail_count += 1
        replan = task.get("approved_replan_files") or []
        if replan:
            replan_rescued_count += 1
        p, c = m.get("tokens_prompt"), m.get("tokens_completion")
        if p is not None or c is not None:
            per_task_token_totals.append(float((p or 0) + (c or 0)))

    n = len(tasks)
    typecheck_skipped_count = (
        sum(
            1
            for task in tasks
            if not bool(_task_metrics_dict(task).get("typecheck_ran", False))
        )
        if applied_like
        else 0
    )
    tokens_total_per_task_mean: float | None = None
    if per_task_token_totals:
        tokens_total_per_task_mean = round(
            sum(per_task_token_totals) / len(per_task_token_totals), 4
        )
    oob_files_per_task_mean: float | None = (
        round(
            sum(len(task.get("out_of_bounds_files") or []) for task in tasks) / n,
            4,
        )
        if n
        else None
    )
    completed = sum(
        1
        for task in tasks
        if task.get("status") == "completed"
        and not (
            isinstance(task.get("test"), dict)
            and task["test"].get("ran")
            and task["test"].get("passed") is False
        )
    )
    cost_total: float | None = None
    for task in tasks:
        m = _task_metrics_dict(task)
        if m.get("cost_usd") is not None:
            cost_total = (cost_total or 0.0) + float(m["cost_usd"])
    if cost_total is not None:
        cost_total = round(cost_total, 8)
    cost_per_completed_task: float | None = None
    if completed > 0 and cost_total is not None:
        cost_per_completed_task = round(cost_total / completed, 8)

    return {
        "patch_na_count": patch_na_count,
        "typecheck_pass_count": typecheck_pass_count,
        "typecheck_fail_count": typecheck_fail_count,
        "typecheck_skipped_count": typecheck_skipped_count,
        "tokens_total_per_task_mean": tokens_total_per_task_mean,
        "oob_files_per_task_mean": oob_files_per_task_mean,
        "replan_rescued_count": replan_rescued_count,
        "cost_per_completed_task": cost_per_completed_task,
    }


def _merge_summary_int(
    sm: dict[str, Any], derived: dict[str, Any], key: str, default: int = 0
) -> int:
    if key in sm and sm[key] is not None:
        return int(sm[key])
    val = derived.get(key, default)
    return int(val) if val is not None else default


def _merge_summary_float(
    sm: dict[str, Any], derived: dict[str, Any], key: str
) -> float | None:
    if key in sm and sm[key] is not None:
        return float(sm[key])
    val = derived.get(key)
    return float(val) if val is not None else None


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------


def analyze_paths(paths: Iterable[Path]) -> AnalysisReport:
    """Aggregate one or more eval_run artifacts into a single report.

    A directory may be passed; it is scanned for ``eval_run*.json`` files.
    Combined files (with ``strategies.{naive_parallel, acg_planned}``) are
    flattened so each strategy contributes independently — a task that ran
    under both strategies is counted twice (this is the right behaviour for
    predictor calibration since both strategies use the same lockfile).
    """
    expanded = _expand_paths(paths)
    report = AnalysisReport()

    for run_path in expanded:
        for run in _load_run(run_path):
            summary_metrics = run.get("summary_metrics") or {}
            run_tasks: list[dict[str, Any]] = run.get("tasks", []) or []
            evidence_kind = str(run.get("evidence_kind") or "")
            derived = _honest_metrics_from_task_dicts(run_tasks, evidence_kind)

            wall_time_seconds = float(summary_metrics.get("wall_time_seconds") or 0.0)
            tokens_completion_total = summary_metrics.get("tokens_completion_total")
            if tokens_completion_total is None:
                comp_vals = [
                    _task_metrics_dict(t).get("tokens_completion")
                    for t in run_tasks
                    if _task_metrics_dict(t).get("tokens_completion") is not None
                ]
                tokens_completion_total = sum(comp_vals) if comp_vals else None

            patch_na_count = _merge_summary_int(summary_metrics, derived, "patch_na_count", 0)
            typecheck_pass_count = _merge_summary_int(
                summary_metrics, derived, "typecheck_pass_count", 0
            )
            typecheck_fail_count = _merge_summary_int(
                summary_metrics, derived, "typecheck_fail_count", 0
            )
            typecheck_skipped_count = _merge_summary_int(
                summary_metrics, derived, "typecheck_skipped_count", 0
            )
            tokens_total_per_task_mean = _merge_summary_float(
                summary_metrics, derived, "tokens_total_per_task_mean"
            )
            cost_per_completed_task = _merge_summary_float(
                summary_metrics, derived, "cost_per_completed_task"
            )
            if cost_per_completed_task is None:
                tc_done = summary_metrics.get("tasks_completed")
                ctot = summary_metrics.get("cost_usd_total")
                if tc_done and ctot is not None:
                    cost_per_completed_task = round(float(ctot) / int(tc_done), 8)
            oob_files_per_task_mean = _merge_summary_float(
                summary_metrics, derived, "oob_files_per_task_mean"
            )
            replan_rescued_count = _merge_summary_int(
                summary_metrics, derived, "replan_rescued_count", 0
            )

            report.runs.append(
                RunSummary(
                    source_path=run.get("_source_path", str(run_path)),
                    strategy=run.get("strategy", "?"),
                    backend=run.get("backend", "?"),
                    suite_name=run.get("suite_name", "?"),
                    execution_mode=run.get("execution_mode", "?"),
                    evidence_kind=run.get("evidence_kind", "?"),
                    tasks_total=summary_metrics.get("tasks_total", 0),
                    tasks_completed=summary_metrics.get("tasks_completed", 0),
                    tests_ran_count=summary_metrics.get("tests_ran_count", 0),
                    tested_tasks_completed=summary_metrics.get("tested_tasks_completed", 0),
                    overlapping_write_pairs=summary_metrics.get("overlapping_write_pairs", 0),
                    out_of_bounds_write_count=summary_metrics.get("out_of_bounds_write_count", 0),
                    blocked_invalid_write_count=summary_metrics.get(
                        "blocked_invalid_write_count", 0
                    ),
                    tokens_prompt_total=summary_metrics.get("tokens_prompt_total"),
                    tokens_all_in=summary_metrics.get("tokens_all_in"),
                    tokens_prompt_method=summary_metrics.get("tokens_prompt_method"),
                    tokens_orchestrator_overhead=summary_metrics.get(
                        "tokens_orchestrator_overhead"
                    ),
                    cost_usd_total=summary_metrics.get("cost_usd_total"),
                    cost_method=summary_metrics.get("cost_method"),
                    cost_source=summary_metrics.get("cost_source"),
                    wall_time_seconds=wall_time_seconds,
                    tokens_completion_total=tokens_completion_total,
                    patch_na_count=patch_na_count,
                    typecheck_pass_count=typecheck_pass_count,
                    typecheck_fail_count=typecheck_fail_count,
                    typecheck_skipped_count=typecheck_skipped_count,
                    tokens_total_per_task_mean=tokens_total_per_task_mean,
                    cost_per_completed_task=cost_per_completed_task,
                    oob_files_per_task_mean=oob_files_per_task_mean,
                    replan_rescued_count=replan_rescued_count,
                )
            )

            strategy = run.get("strategy", "?")
            if isinstance(strategy, str) and strategy.startswith("acg_planned_replan"):
                for task in run_tasks:
                    rescued = task.get("approved_replan_files") or []
                    if rescued:
                        report.replan_rescues.append(
                            ReplanRescueEntry(
                                source_path=run.get("_source_path", str(run_path)),
                                strategy=strategy,
                                task_id=str(task.get("task_id", "?")),
                                files=list(rescued),
                            )
                        )

            for task in run_tasks:
                tid = task.get("task_id", "?")
                analysis = report.tasks.setdefault(tid, TaskAnalysis(task_id=tid))
                analysis.runs_seen += 1
                analysis.predicted_files.update(task.get("predicted_write_files", []))
                actual = task.get("actual_changed_files", []) or []
                analysis.actual_files_seen.update(actual)
                analysis.actual_files_total += len(actual)
                analysis.out_of_bounds_files.extend(task.get("out_of_bounds_files", []) or [])
                analysis.blocked_events_total += len(task.get("blocked_write_events", []) or [])
                analysis.allowed_glob_count = max(
                    analysis.allowed_glob_count,
                    len(task.get("allowed_write_globs", []) or []),
                )
                analysis.backends_seen.add(run.get("backend", "?"))
                analysis.strategies_seen.add(run.get("strategy", "?"))
                if task.get("status"):
                    analysis.statuses.append(task["status"])

    return report


# ---------------------------------------------------------------------------
# Refinement suggestions
# ---------------------------------------------------------------------------


def _suggest_for_task(task: TaskAnalysis) -> list[str]:
    """Heuristic refinements per task. Returns a list of short strings."""
    suggestions: list[str] = []

    # Over-prediction (predictor names files agents never touch).
    if task.precision < 0.6 and task.predicted_files:
        unused = sorted(task.predicted_files - task.actual_files_seen)[:3]
        if unused:
            suggestions.append(
                f"predictor over-predicts (precision={task.precision:.2f}); "
                f"consider removing {unused!r} from predicted_writes seeds"
            )

    # Under-prediction (agent writes files the predictor missed).
    if task.recall < 0.6 and task.actual_files_seen:
        missed = sorted(task.actual_files_seen - task.predicted_files)[:3]
        if missed:
            suggestions.append(
                f"predictor missed files (recall={task.recall:.2f}); "
                f"consider seeding {missed!r} into the predictor"
            )

    # Out-of-bounds writes — agent broke the contract; tighten or widen?
    if task.out_of_bounds_files:
        unique_oob = sorted(set(task.out_of_bounds_files))[:3]
        suggestions.append(
            f"agent proposed {len(task.out_of_bounds_files)} OOB write(s) "
            f"({unique_oob!r}); decide: widen allowed_paths to include them, "
            "or audit the agent prompt"
        )

    # Allowed_paths drastically wider than actual usage.
    actual_count = len(task.actual_files_seen)
    if task.allowed_glob_count and actual_count and task.allowed_glob_count > 3 * actual_count:
        suggestions.append(
            f"allowed_paths declares {task.allowed_glob_count} globs but agent "
            f"touched only {actual_count} files; consider tightening scope"
        )

    return suggestions


def collect_suggestions(report: AnalysisReport) -> dict[str, list[str]]:
    """Return per-task suggestion lists keyed by task_id."""
    return {tid: _suggest_for_task(t) for tid, t in report.tasks.items()}


# ---------------------------------------------------------------------------
# Markdown formatting
# ---------------------------------------------------------------------------


def _md_table(rows: list[list[str]], headers: list[str]) -> str:
    """Render a tiny Markdown table. ``rows`` may be empty."""
    out: list[str] = []
    out.append("| " + " | ".join(headers) + " |")
    out.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for row in rows:
        out.append("| " + " | ".join(row) + " |")
    return "\n".join(out)


def _reporting_mode(evidence_kind: str, execution_mode: str) -> str:
    ek = (evidence_kind or "").lower()
    em = (execution_mode or "").lower()
    if "applied_diff" in ek or "applied_diff" in em:
        return "applied"
    if "suite_proposed" in ek:
        return "suite"
    return "proposed"


def _headline_tokens_total_cell(r: RunSummary) -> str:
    if r.tokens_total_per_task_mean is None:
        return "—"
    return str(int(round(r.tokens_total_per_task_mean)))


def _headline_cost_usd_per_task_cell(r: RunSummary) -> str:
    if r.cost_usd_total is None or r.cost_usd_total == 0 or not r.tasks_total:
        return "—"
    return f"{r.cost_usd_total / r.tasks_total:.4f}"


def _headline_oob_per_task_cell(r: RunSummary) -> str:
    if r.oob_files_per_task_mean is None:
        return "—"
    return f"{r.oob_files_per_task_mean:.2f}"


def _fmt_patch_applies_pct(r: RunSummary) -> str:
    if _reporting_mode(r.evidence_kind, r.execution_mode) != "applied":
        return "—"
    total = r.tasks_total or 0
    if total <= 0:
        return "—"
    if r.patch_na_count == 0:
        return "100%"
    return f"{(1 - r.patch_na_count / total) * 100:.0f}%"


def _fmt_typecheck_pass_pct(r: RunSummary) -> str:
    denom = r.typecheck_pass_count + r.typecheck_fail_count
    if denom <= 0:
        return "—"
    return f"{100 * r.typecheck_pass_count / denom:.0f}%"


def _any_acg_planned_replan_runs(report: AnalysisReport) -> bool:
    return any(
        isinstance(r.strategy, str) and r.strategy.startswith("acg_planned_replan")
        for r in report.runs
    )


def format_markdown(report: AnalysisReport) -> str:
    """Render the full analysis report as Markdown."""
    lines: list[str] = []
    lines.append("# ACG run-trace analysis")
    lines.append("")
    lines.append(
        "This harness reports per-task total tokens and cost, consistent with "
        "SWE-ContextBench, AutoCodeRover, and RepoAgent practice (aggregate prompt "
        "plus completion). Patch-applies and typecheck columns are populated only "
        "for applied-diff runs. Out-of-bounds file rates are a write-side analog of "
        "context precision. Published agent-eval work often shows roughly 20–50% "
        "cost deltas between scoped and blind setups; tenfold swings on comparable "
        "slices would be pathological."
    )
    lines.append("")
    lines.append(f"_Aggregated across {report.total_runs} run artifact(s)._")
    lines.append("")
    lines.append("## Runs")
    lines.append("")
    if report.runs:
        headline_rows = [
            [
                r.strategy,
                _reporting_mode(r.evidence_kind, r.execution_mode),
                _headline_tokens_total_cell(r),
                _headline_cost_usd_per_task_cell(r),
                str(r.tasks_completed),
                _headline_oob_per_task_cell(r),
                str(r.replan_rescued_count),
                _fmt_patch_applies_pct(r),
                _fmt_typecheck_pass_pct(r),
                f"{r.wall_time_seconds:.1f}",
            ]
            for r in report.runs
        ]
        lines.append(
            _md_table(
                headline_rows,
                [
                    "strategy",
                    "mode",
                    "tokens_total/task",
                    "cost_usd/task",
                    "tasks_completed",
                    "OOB_files/task",
                    "replan_rescued",
                    "patch_applies%",
                    "typecheck_pass%",
                    "wall_s",
                ],
            )
        )
        lines.append("")
        lines.append("### Safety")
        lines.append("")
        safety_rows = [
            [
                r.strategy,
                _reporting_mode(r.evidence_kind, r.execution_mode),
                str(r.out_of_bounds_write_count),
                str(r.patch_na_count),
                str(r.typecheck_fail_count),
            ]
            for r in report.runs
        ]
        lines.append(
            _md_table(
                safety_rows,
                ["strategy", "mode", "OOB_files", "PATCH_NA_count", "typecheck_fail_count"],
            )
        )
        lines.append("")
        lines.append("### Cost")
        lines.append("")
        cost_rows = []
        for r in report.runs:
            cpc = r.cost_per_completed_task
            cpc_s = "—" if cpc is None else f"{cpc:.4f}"
            tp = r.tokens_prompt_total
            tc = r.tokens_completion_total
            tp_s = "—" if tp is None else str(int(tp))
            tc_s = "—" if tc is None else str(int(tc))
            if tp is None and tc is None:
                tot_s = "—"
            else:
                tot_s = str(int((tp or 0) + (tc or 0)))
            cost_tot = r.cost_usd_total
            cost_tot_s = "—" if cost_tot is None else f"{cost_tot:.6f}"
            cost_rows.append(
                [
                    r.strategy,
                    _reporting_mode(r.evidence_kind, r.execution_mode),
                    cost_tot_s,
                    cpc_s,
                    tp_s,
                    tc_s,
                    tot_s,
                ]
            )
        lines.append(
            _md_table(
                cost_rows,
                [
                    "strategy",
                    "mode",
                    "cost_usd_total",
                    "cost_per_completed_task",
                    "tokens_prompt_total",
                    "tokens_completion_total",
                    "tokens_total",
                ],
            )
        )
        lines.append("")
        if _any_acg_planned_replan_runs(report):
            lines.append("### Replan rescue")
            lines.append("")
            if report.replan_rescues:
                rr_rows = [
                    [
                        Path(e.source_path).name,
                        e.strategy,
                        e.task_id,
                        ", ".join(e.files),
                    ]
                    for e in report.replan_rescues
                ]
                lines.append(
                    _md_table(rr_rows, ["file", "strategy", "task_id", "rescued_files"])
                )
            else:
                lines.append("_No replan-rescue events (empty `approved_replan_files`) on these runs._")
            lines.append("")
        lines.append("### Run file details")
        lines.append("")
        detail_rows = [
            [
                Path(r.source_path).name,
                r.suite_name,
                r.strategy,
                r.backend,
                f"{r.tasks_completed}/{r.tasks_total}",
                f"{r.tested_tasks_completed}/{r.tests_ran_count}",
                str(r.overlapping_write_pairs),
                "0" if _is_applied_diff_run(r) else str(r.out_of_bounds_write_count),
                str(r.out_of_bounds_write_count) if _is_applied_diff_run(r) else "0",
                str(r.blocked_invalid_write_count),
                "—" if r.tokens_prompt_total is None else str(r.tokens_prompt_total),
                "—" if r.tokens_all_in is None else str(r.tokens_all_in),
                r.tokens_prompt_method or "—",
                "not recorded" if r.cost_usd_total is None else f"{r.cost_usd_total:.6f}",
            ]
            for r in report.runs
        ]
        lines.append(
            _md_table(
                detail_rows,
                [
                    "file",
                    "suite",
                    "strategy",
                    "backend",
                    "status_completed",
                    "tested_passed/tests_ran",
                    "overlap_pairs",
                    "proposal_oob",
                    "posthoc_diff_oob",
                    "validator_blocked",
                    "prompt_tokens",
                    "all_in_tokens",
                    "prompt_token_method",
                    "cost_usd",
                ],
            )
        )
    else:
        lines.append("_no runs found_")
    lines.append("")

    # Predictor accuracy
    lines.append("## Predictor accuracy (per task, across runs)")
    lines.append("")
    if report.tasks:
        rows = []
        for tid, t in sorted(report.tasks.items()):
            rows.append(
                [
                    tid,
                    str(t.runs_seen),
                    str(t.true_positives),
                    str(t.false_positives),
                    str(t.false_negatives),
                    f"{t.precision:.2f}",
                    f"{t.recall:.2f}",
                    f"{t.f1:.2f}",
                ]
            )
        lines.append(
            _md_table(
                rows,
                [
                    "task",
                    "runs",
                    "TP",
                    "FP",
                    "FN",
                    "precision",
                    "recall",
                    "F1",
                ],
            )
        )
    else:
        lines.append("_no per-task data_")
    lines.append("")
    lines.append(
        f"**Overall: precision={report.overall_precision:.2f} "
        f"recall={report.overall_recall:.2f} F1={report.overall_f1:.2f}**"
    )
    lines.append("")

    # Contract-enforcement events
    lines.append("## Contract enforcement")
    lines.append("")
    lines.append(
        f"- Proposal out-of-bounds files across proposal-only runs: **{report.total_proposal_oob}**"
    )
    lines.append(
        f"- Planned validator-blocked write events across all runs: **{report.total_blocks}**"
    )
    lines.append(
        f"- Post-hoc out-of-bounds files detected in applied/manual diffs: "
        f"**{report.total_posthoc_oob}**"
    )
    lines.append("")
    if report.total_oob == 0 and report.total_blocks == 0:
        lines.append(
            "> All agents stayed within their `allowed_paths` on every observed run. "
            "The contract acted as a safety net but did not need to fire — agents "
            "behaved within bounds. To stress-test the validator, consider tightening "
            "`allowed_paths` so the predicted set is closer to the minimal write set."
        )
        lines.append("")

    # Refinement suggestions
    lines.append("## Refinement suggestions")
    lines.append("")
    suggestions = collect_suggestions(report)
    any_suggested = False
    for tid, items in sorted(suggestions.items()):
        if not items:
            continue
        any_suggested = True
        lines.append(f"### `{tid}`")
        lines.append("")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")
    if not any_suggested:
        lines.append(
            "_No refinements suggested — predictor and contract are well-calibrated for the observed runs._"
        )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "AnalysisReport",
    "ReplanRescueEntry",
    "RunSummary",
    "TaskAnalysis",
    "analyze_paths",
    "collect_suggestions",
    "format_markdown",
]
