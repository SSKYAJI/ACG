"""Smoke + behaviour tests for :mod:`acg.analyze`.

The analyzer is wired into ``acg analyze-runs`` and feeds the megaplan's
"learn from mistakes" loop. The tests here pin three contracts:

1. F1 / precision / recall are computed correctly across multiple runs
   that share a task id (the cross-strategy aggregation case).
2. Combined ``eval_run`` files (``strategies.{naive_parallel, acg_planned}``)
   are flattened so each strategy contributes independently.
3. Refinement suggestions surface the canonical signals — over-prediction
   (low precision) and OOB writes — and stay quiet when the predictor
   matches actual writes exactly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from acg.analyze import (
    AnalysisReport,
    analyze_paths,
    collect_suggestions,
    format_markdown,
)
from experiments.greenhouse.eval_schema import (
    EvalTask,
    TaskMetrics,
    compute_summary_metrics,
)


def _write_eval_run(
    path: Path,
    *,
    strategy: str,
    backend: str,
    tasks: list[dict],
    execution_mode: str | None = None,
    evidence_kind: str | None = None,
    overlap_pairs: int = 0,
    oob_count: int = 0,
    blocked_count: int = 0,
) -> None:
    payload = {
        "version": "0.1",
        "strategy": strategy,
        "backend": backend,
        "suite_name": "test-suite",
        **({"execution_mode": execution_mode} if execution_mode is not None else {}),
        **({"evidence_kind": evidence_kind} if evidence_kind is not None else {}),
        "summary_metrics": {
            "tasks_total": len(tasks),
            "tasks_completed": sum(1 for t in tasks if t.get("status") == "completed"),
            "overlapping_write_pairs": overlap_pairs,
            "out_of_bounds_write_count": oob_count,
            "blocked_invalid_write_count": blocked_count,
            "tokens_prompt_total": sum(
                (t.get("metrics", {}) or {}).get("tokens_prompt", 0) for t in tasks
            )
            or None,
            "tokens_orchestrator_overhead": None,
        },
        "tasks": tasks,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def test_aggregates_precision_recall_f1_across_runs(tmp_path: Path) -> None:
    """Two runs of the same task yield aggregated TP/FP/FN."""
    a = tmp_path / "run_a.json"
    b = tmp_path / "run_b.json"
    _write_eval_run(
        a,
        strategy="naive_parallel",
        backend="mock",
        tasks=[
            {
                "task_id": "t1",
                "status": "completed",
                "predicted_write_files": ["a.py", "b.py", "c.py"],
                "actual_changed_files": ["a.py", "b.py"],
                "allowed_write_globs": ["**/*.py"],
                "out_of_bounds_files": [],
                "blocked_write_events": [],
            }
        ],
    )
    _write_eval_run(
        b,
        strategy="acg_planned",
        backend="mock",
        tasks=[
            {
                "task_id": "t1",
                "status": "completed",
                "predicted_write_files": ["a.py", "b.py", "c.py"],
                "actual_changed_files": ["a.py", "d.py"],
                "allowed_write_globs": ["**/*.py"],
                "out_of_bounds_files": [],
                "blocked_write_events": [],
            }
        ],
    )

    report = analyze_paths([a, b])
    assert isinstance(report, AnalysisReport)
    task = report.tasks["t1"]
    # Predicted across runs: {a, b, c}; Actual seen: {a, b, d}
    # TP = {a, b} = 2; FP = {c} = 1; FN = {d} = 1
    assert task.true_positives == 2
    assert task.false_positives == 1
    assert task.false_negatives == 1
    assert task.precision == 2 / 3
    assert task.recall == 2 / 3
    assert abs(task.f1 - 2 / 3) < 1e-9


def test_combined_eval_run_is_flattened_into_two_runs(tmp_path: Path) -> None:
    """A combined file with two strategies should contribute two RunSummary entries."""
    combined_path = tmp_path / "eval_run_combined.json"
    combined = {
        "strategies": {
            "naive_parallel": {
                "strategy": "naive_parallel",
                "backend": "mock",
                "suite_name": "demo",
                "summary_metrics": {
                    "tasks_total": 1,
                    "tasks_completed": 1,
                    "overlapping_write_pairs": 0,
                    "out_of_bounds_write_count": 0,
                    "blocked_invalid_write_count": 0,
                    "tokens_prompt_total": 100,
                    "tokens_orchestrator_overhead": None,
                },
                "tasks": [
                    {
                        "task_id": "t1",
                        "status": "completed",
                        "predicted_write_files": ["a.py"],
                        "actual_changed_files": ["a.py"],
                        "allowed_write_globs": ["**/*.py"],
                        "out_of_bounds_files": [],
                        "blocked_write_events": [],
                    }
                ],
            },
            "acg_planned": {
                "strategy": "acg_planned",
                "backend": "mock",
                "suite_name": "demo",
                "summary_metrics": {
                    "tasks_total": 1,
                    "tasks_completed": 1,
                    "overlapping_write_pairs": 0,
                    "out_of_bounds_write_count": 0,
                    "blocked_invalid_write_count": 0,
                    "tokens_prompt_total": 50,
                    "tokens_orchestrator_overhead": 200,
                },
                "tasks": [
                    {
                        "task_id": "t1",
                        "status": "completed",
                        "predicted_write_files": ["a.py"],
                        "actual_changed_files": ["a.py"],
                        "allowed_write_globs": ["**/*.py"],
                        "out_of_bounds_files": [],
                        "blocked_write_events": [],
                    }
                ],
            },
        }
    }
    combined_path.write_text(json.dumps(combined))

    report = analyze_paths([combined_path])
    assert {r.strategy for r in report.runs} == {"naive_parallel", "acg_planned"}
    # The same task contributes once per strategy run.
    assert report.tasks["t1"].runs_seen == 2


def test_suggestions_flag_overprediction_and_oob_but_quiet_when_calibrated(
    tmp_path: Path,
) -> None:
    """Refinements appear when precision is low or OOB writes occur; absent when calibrated."""
    over_path = tmp_path / "over.json"
    _write_eval_run(
        over_path,
        strategy="acg_planned",
        backend="mock",
        tasks=[
            # Over-prediction: 4 predicted, 1 actual -> precision 0.25
            {
                "task_id": "over_pred",
                "status": "completed",
                "predicted_write_files": ["a.py", "b.py", "c.py", "d.py"],
                "actual_changed_files": ["a.py"],
                "allowed_write_globs": ["**/*.py"],
                "out_of_bounds_files": [],
                "blocked_write_events": [],
            },
            # OOB: agent wrote outside scope.
            {
                "task_id": "oob_writer",
                "status": "completed",
                "predicted_write_files": ["a.py"],
                "actual_changed_files": ["a.py", "x.py"],
                "allowed_write_globs": ["**/*.py"],
                "out_of_bounds_files": ["x.py"],
                "blocked_write_events": [
                    {"file": "x.py", "description": "outside scope", "reason": "no match"}
                ],
            },
            # Calibrated: predictor and actual match exactly.
            {
                "task_id": "calibrated",
                "status": "completed",
                "predicted_write_files": ["a.py", "b.py"],
                "actual_changed_files": ["a.py", "b.py"],
                "allowed_write_globs": ["**/*.py"],
                "out_of_bounds_files": [],
                "blocked_write_events": [],
            },
        ],
        oob_count=1,
        blocked_count=1,
    )

    report = analyze_paths([over_path])
    suggestions = collect_suggestions(report)

    assert any("over-predicts" in s for s in suggestions["over_pred"])
    assert any("OOB write" in s for s in suggestions["oob_writer"])
    assert suggestions["calibrated"] == []

    # Markdown rendering should not raise and should mention the OOB event.
    md = format_markdown(report)
    assert "## Predictor accuracy" in md
    assert "OOB" in md or "out-of-bounds" in md.lower()


def test_markdown_distinguishes_oob_proposals_from_validator_blocks(
    tmp_path: Path,
) -> None:
    """Blind naive OOB proposals must not be reported as blocked writes."""
    run_path = tmp_path / "blind_naive.json"
    _write_eval_run(
        run_path,
        strategy="naive_parallel",
        backend="mock",
        tasks=[
            {
                "task_id": "blind",
                "status": "completed_unsafe",
                "predicted_write_files": ["a.py"],
                "actual_changed_files": ["a.py", "x.py", "y.py", "z.py"],
                "allowed_write_globs": ["a.py"],
                "out_of_bounds_files": ["x.py", "y.py", "z.py"],
                "blocked_write_events": [],
            }
        ],
        oob_count=3,
        blocked_count=0,
    )

    report = analyze_paths([run_path])
    md = format_markdown(report)

    assert report.runs[0].out_of_bounds_write_count == 3
    assert report.runs[0].blocked_invalid_write_count == 0
    assert report.total_proposal_oob == 3
    assert report.total_posthoc_oob == 0
    assert "Proposal out-of-bounds files across proposal-only runs: **3**" in md
    assert "Planned validator-blocked write events across all runs: **0**" in md
    assert "validator-blocked write events across all runs: **3**" not in md


def test_markdown_does_not_double_count_posthoc_diff_oob(
    tmp_path: Path,
) -> None:
    """Applied diff OOB belongs in post-hoc counts, not proposal counts."""
    run_path = tmp_path / "devin_oob.json"
    _write_eval_run(
        run_path,
        strategy="acg_planned",
        backend="devin-api",
        execution_mode="devin_diff",
        evidence_kind="applied_diff",
        tasks=[
            {
                "task_id": "hosted",
                "status": "completed_unsafe",
                "predicted_write_files": ["a.py"],
                "actual_changed_files": ["a.py", "oops.py"],
                "allowed_write_globs": ["a.py"],
                "out_of_bounds_files": ["oops.py"],
                "blocked_write_events": [],
            }
        ],
        oob_count=1,
        blocked_count=0,
    )

    report = analyze_paths([run_path])
    md = format_markdown(report)

    assert report.total_oob == 1
    assert report.total_proposal_oob == 0
    assert report.total_posthoc_oob == 1
    assert "Proposal out-of-bounds files across proposal-only runs: **0**" in md
    assert "Post-hoc out-of-bounds files detected in applied/manual diffs: **1**" in md


def test_summary_metrics_includes_tokens_total_and_cost_per_completed() -> None:
    tasks = [
        EvalTask(
            task_id="a",
            status="completed",
            actual_changed_files=["x"],
            metrics=TaskMetrics(tokens_prompt=100, tokens_completion=50, cost_usd=0.5),
        ),
        EvalTask(
            task_id="b",
            status="completed",
            actual_changed_files=["y"],
            metrics=TaskMetrics(tokens_prompt=200, tokens_completion=None, cost_usd=0.5),
        ),
    ]
    summary = compute_summary_metrics(
        tasks,
        wall_time_seconds=10.0,
        cost_usd_total=1.0,
        evidence_kind="proposed_write_set",
    )
    assert summary.tokens_total_per_task_mean == pytest.approx(175.0, rel=1e-6)
    assert summary.cost_per_completed_task == pytest.approx(0.5, rel=1e-6)


def test_summary_metrics_replan_rescued_count() -> None:
    tasks = [
        EvalTask(
            task_id="a",
            status="completed",
            actual_changed_files=["x"],
            approved_replan_files=["z.py"],
        ),
        EvalTask(task_id="b", status="completed", actual_changed_files=["y"]),
    ]
    summary = compute_summary_metrics(tasks, wall_time_seconds=1.0)
    assert summary.replan_rescued_count == 1


def test_summary_metrics_oob_files_per_task_mean() -> None:
    tasks = [
        EvalTask(
            task_id="a",
            status="completed",
            actual_changed_files=["x"],
            out_of_bounds_files=["o1", "o2"],
        ),
        EvalTask(task_id="b", status="completed", actual_changed_files=["y"]),
    ]
    summary = compute_summary_metrics(tasks, wall_time_seconds=1.0)
    assert summary.oob_files_per_task_mean == pytest.approx(1.0, rel=1e-6)


def test_summary_metrics_typecheck_skipped_count_for_proposed_only_is_zero() -> None:
    tasks = [
        EvalTask(
            task_id="a",
            status="completed",
            actual_changed_files=["x"],
            metrics=TaskMetrics(typecheck_ran=False),
        ),
    ]
    proposed = compute_summary_metrics(
        tasks, wall_time_seconds=1.0, evidence_kind="proposed_write_set"
    )
    assert proposed.typecheck_skipped_count == 0
    applied = compute_summary_metrics(
        tasks, wall_time_seconds=1.0, evidence_kind="applied_diff_live"
    )
    assert applied.typecheck_skipped_count == 1


def test_analyze_runs_headline_table_includes_new_columns(tmp_path: Path) -> None:
    run_path = tmp_path / "headline.json"
    run_path.write_text(
        json.dumps(
            {
                "version": "0.1",
                "strategy": "acg_planned",
                "backend": "local",
                "suite_name": "demo",
                "execution_mode": "applied_diff",
                "evidence_kind": "applied_diff",
                "summary_metrics": {
                    "tasks_total": 1,
                    "tasks_completed": 1,
                    "wall_time_seconds": 12.34,
                    "tokens_prompt_total": 100,
                    "tokens_completion_total": 50,
                    "tokens_all_in": None,
                    "tokens_prompt_method": "x",
                    "tokens_orchestrator_overhead": None,
                    "cost_usd_total": 0.25,
                    "patch_na_count": 0,
                    "typecheck_pass_count": 1,
                    "typecheck_fail_count": 0,
                    "typecheck_skipped_count": 0,
                    "tokens_total_per_task_mean": 150.0,
                    "cost_per_completed_task": 0.25,
                    "oob_files_per_task_mean": 0.0,
                    "replan_rescued_count": 0,
                },
                "tasks": [
                    {
                        "task_id": "t1",
                        "status": "completed",
                        "predicted_write_files": ["a.py"],
                        "actual_changed_files": ["a.py"],
                        "allowed_write_globs": ["**/*.py"],
                        "out_of_bounds_files": [],
                        "blocked_write_events": [],
                        "metrics": {
                            "tokens_prompt": 100,
                            "tokens_completion": 50,
                            "typecheck_ran": True,
                            "typecheck_exit_code": 0,
                        },
                    }
                ],
            }
        )
    )
    md = format_markdown(analyze_paths([run_path]))
    for col in (
        "tokens_total/task",
        "cost_usd/task",
        "OOB_files/task",
        "replan_rescued",
        "patch_applies%",
        "typecheck_pass%",
    ):
        assert col in md


def test_analyze_runs_handles_runs_without_new_fields(tmp_path: Path) -> None:
    run_path = tmp_path / "legacy.json"
    _write_eval_run(
        run_path,
        strategy="naive_parallel",
        backend="mock",
        tasks=[
            {
                "task_id": "t1",
                "status": "completed",
                "predicted_write_files": ["a.py"],
                "actual_changed_files": ["a.py"],
                "allowed_write_globs": ["**/*.py"],
                "out_of_bounds_files": [],
                "blocked_write_events": [],
            }
        ],
    )
    md = format_markdown(analyze_paths([run_path]))
    assert re.search(
        r"naive_parallel \| proposed \|[^\n]+\| — \| — \|",
        md,
    ), "expected proposed headline row with dash patch/typecheck cells"


def test_replan_rescue_section_lists_approved_files(tmp_path: Path) -> None:
    run_path = tmp_path / "replan.json"
    run_path.write_text(
        json.dumps(
            {
                "strategy": "acg_planned_replan",
                "backend": "mock",
                "suite_name": "demo",
                "summary_metrics": {
                    "tasks_total": 1,
                    "tasks_completed": 1,
                    "wall_time_seconds": 1.0,
                },
                "tasks": [
                    {
                        "task_id": "t1",
                        "status": "completed",
                        "predicted_write_files": ["a.py"],
                        "actual_changed_files": ["a.py"],
                        "allowed_write_globs": ["**/*.py"],
                        "out_of_bounds_files": [],
                        "blocked_write_events": [],
                        "approved_replan_files": ["extra.py"],
                    }
                ],
            }
        )
    )
    report = analyze_paths([run_path])
    assert len(report.replan_rescues) == 1
    md = format_markdown(report)
    assert "### Replan rescue" in md
    assert "extra.py" in md
