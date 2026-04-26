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
class RunSummary:
    """Per-eval-run-file metadata."""

    source_path: str
    strategy: str
    backend: str
    suite_name: str
    tasks_total: int
    tasks_completed: int
    overlapping_write_pairs: int
    out_of_bounds_write_count: int
    blocked_invalid_write_count: int
    tokens_prompt_total: int | None
    tokens_orchestrator_overhead: int | None


@dataclass
class AnalysisReport:
    """Aggregated cross-run report."""

    runs: list[RunSummary] = field(default_factory=list)
    tasks: dict[str, TaskAnalysis] = field(default_factory=dict)

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
        return sum(len(t.out_of_bounds_files) for t in self.tasks.values())


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
            summary_metrics = run.get("summary_metrics", {})
            report.runs.append(
                RunSummary(
                    source_path=run.get("_source_path", str(run_path)),
                    strategy=run.get("strategy", "?"),
                    backend=run.get("backend", "?"),
                    suite_name=run.get("suite_name", "?"),
                    tasks_total=summary_metrics.get("tasks_total", 0),
                    tasks_completed=summary_metrics.get("tasks_completed", 0),
                    overlapping_write_pairs=summary_metrics.get("overlapping_write_pairs", 0),
                    out_of_bounds_write_count=summary_metrics.get("out_of_bounds_write_count", 0),
                    blocked_invalid_write_count=summary_metrics.get(
                        "blocked_invalid_write_count", 0
                    ),
                    tokens_prompt_total=summary_metrics.get("tokens_prompt_total"),
                    tokens_orchestrator_overhead=summary_metrics.get(
                        "tokens_orchestrator_overhead"
                    ),
                )
            )

            for task in run.get("tasks", []):
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


def format_markdown(report: AnalysisReport) -> str:
    """Render the full analysis report as Markdown."""
    lines: list[str] = []
    lines.append("# ACG run-trace analysis")
    lines.append("")
    lines.append(f"_Aggregated across {report.total_runs} run artifact(s)._")
    lines.append("")

    # Per-run summary
    lines.append("## Runs")
    lines.append("")
    if report.runs:
        rows = [
            [
                Path(r.source_path).name,
                r.suite_name,
                r.strategy,
                r.backend,
                f"{r.tasks_completed}/{r.tasks_total}",
                str(r.overlapping_write_pairs),
                str(r.out_of_bounds_write_count),
                str(r.blocked_invalid_write_count),
                "—" if r.tokens_prompt_total is None else str(r.tokens_prompt_total),
            ]
            for r in report.runs
        ]
        lines.append(
            _md_table(
                rows,
                [
                    "file",
                    "suite",
                    "strategy",
                    "backend",
                    "completed",
                    "overlap_pairs",
                    "oob",
                    "blocked",
                    "prompt_tokens",
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
        f"- Total out-of-bounds proposals across all runs: **{report.total_oob}**"
    )
    lines.append(
        f"- Total validator-blocked write events across all runs: "
        f"**{report.total_blocks}**"
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
        lines.append("_No refinements suggested — predictor and contract are well-calibrated for the observed runs._")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "AnalysisReport",
    "RunSummary",
    "TaskAnalysis",
    "analyze_paths",
    "collect_suggestions",
    "format_markdown",
]
