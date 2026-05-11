from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from acg.schema import FileScope


def _load_evaluate_module():
    path = (
        Path(__file__).resolve().parents[1]
        / "experiments"
        / "real_repos"
        / "graph_expansion_eval"
        / "evaluate.py"
    )
    spec = importlib.util.spec_from_file_location("graph_expansion_eval_evaluate", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_eval_output_stem_preserves_native_names_and_labels_scip() -> None:
    evaluate = _load_evaluate_module()

    assert evaluate._output_stem("before", "replay") == "before"
    assert evaluate._output_stem("after", "replay") == "after"
    assert evaluate._output_stem("after", "live") == "after_live"
    assert evaluate._output_stem("after", "replay", "scip") == "after_scip"
    assert evaluate._output_stem("after", "live", "scip") == "after_scip_live"
    assert (
        evaluate._output_stem("after", "replay", "scip", "scip-replay")
        == "after_scip_scip_replay"
    )
    assert (
        evaluate._diff_name("replay", "scip", "scip-replay")
        == "predictor_diff_scip_scip_replay.csv"
    )
    assert (
        evaluate._diff_name("live", "scip", "scip-replay")
        == "predictor_diff_scip_scip_replay_live.csv"
    )


def test_eval_metric_row_appends_scip_fields_from_graph_and_file_scopes() -> None:
    evaluate = _load_evaluate_module()
    task = evaluate.EvalTask(
        repo="repo",
        pr_number="123",
        task_id="task",
        prompt="",
        checkout_path=Path("/tmp/repo"),
        task_path=Path("/tmp/tasks.json"),
        lock_path=Path("/tmp/lock.json"),
        ground_truth=["src/a.py", "src/c.py"],
    )

    row = evaluate._metric_row(
        task,
        predicted=["src/a.py"],
        allowed=["src/a.py", "src/b.py"],
        candidate_context=["src/b.py"],
        hard_conflict_pair_count=0,
        candidate_conflict_pair_count=0,
        localization_backend="scip",
        ablation_name="scip-replay",
        repo_graph={
            "scip_status": {"status": "ok"},
            "files": [
                {"path": "src/a.py", "scip_definition_count": 1, "scip_reference_count": 0},
                {"path": "src/b.py", "scip_definition_count": 0, "scip_reference_count": 3},
            ],
            "scip_summary": {"file_count": 2, "symbol_count": 5, "reference_count": 3},
        },
        file_scopes=[
            FileScope(
                path="src/a.py",
                tier="must_write",
                score=0.9,
                signals=["scip"],
                reason="SCIP definition",
            ),
            FileScope(
                path="src/b.py",
                tier="candidate_context",
                score=0.7,
                signals=["scip_reference"],
                reason="SCIP reference",
            ),
        ],
    )

    assert row["localization_backend"] == "scip"
    assert row["ablation_name"] == "scip-replay"
    assert row["scip_status"] == "ok"
    assert row["scip_definition_file_count"] == "1"
    assert row["scip_reference_file_count"] == "1"
    assert row["scip_signal_scope_count"] == "2"
    assert row["scip_signal_must_write_count"] == "1"
    assert row["scip_signal_candidate_context_count"] == "1"
    assert row["scip_signal_true_positive_count"] == "1"
    assert row["scip_signal_false_positive_count"] == "1"
    assert row["scip_signal_false_negative_count"] == "1"
    assert row["scip_file_count"] == "2"
    assert row["scip_symbol_count"] == "5"
    assert row["scip_reference_count"] == "3"
    assert row["scip_candidate_count"] == "2"
    assert row["scip_true_positive_count"] == "1"
    assert row["scip_false_positive_count"] == "1"
    assert row["scip_false_negative_count"] == "1"
    assert row["scip_recall"] == "0.500000"
    assert row["scip_precision"] == "0.500000"
    for field in [
        "scip_index_path",
        "scip_file_count",
        "scip_symbol_count",
        "scip_reference_count",
        "scip_candidate_count",
        "scip_true_positive_count",
        "scip_false_positive_count",
        "scip_false_negative_count",
        "scip_recall",
        "scip_precision",
        "scip_f1",
        "candidate_recall_delta_vs_native",
        "hard_recall_delta_vs_native",
        "tokens_localization_total",
    ]:
        assert field in evaluate.PREDICTOR_FIELDS


def test_strategy_score_csv_compares_runs_to_ground_truth(tmp_path: Path) -> None:
    evaluate = _load_evaluate_module()
    real_repos = tmp_path / "real_repos"
    repo_dir = real_repos / "repo"
    run_dir = repo_dir / "runs_mock" / "task"
    out_dir = tmp_path / "out"
    run_dir.mkdir(parents=True)
    out_dir.mkdir()
    (run_dir / "eval_run_combined.json").write_text(
        json.dumps(
            {
                "version": "0.1",
                "strategies": {
                    "single_agent": {
                        "strategy": "single_agent",
                        "backend": "mock",
                        "tasks": [
                            {
                                "task_id": "task",
                                "status": "completed",
                                "actual_changed_files": ["src/a.py", "src/x.py"],
                            }
                        ],
                        "summary_metrics": {
                            "overlapping_write_pairs": 0,
                            "tokens_all_in": 100,
                        },
                    },
                    "acg_planned": {
                        "strategy": "acg_planned",
                        "backend": "mock",
                        "tasks": [
                            {
                                "task_id": "task",
                                "status": "completed",
                                "actual_changed_files": ["src/a.py", "src/b.py"],
                            }
                        ],
                        "summary_metrics": {
                            "overlapping_write_pairs": 0,
                            "tokens_all_in": 80,
                        },
                    },
                },
            }
        )
        + "\n"
    )
    evaluate.REAL_REPOS = real_repos
    evaluate.OUT_DIR = out_dir
    evaluate.PROJECT_ROOT = tmp_path
    evaluate.discover_tasks = lambda: [
        evaluate.EvalTask(
            repo="repo",
            pr_number="123",
            task_id="task",
            prompt="",
            checkout_path=repo_dir / "checkout",
            task_path=repo_dir / "tasks" / "pr-123.json",
            lock_path=repo_dir / "agent_lock_pr-123.json",
            ground_truth=["src/a.py", "src/b.py"],
        )
    ]

    rows = evaluate.write_strategy_score_csv("after")

    by_strategy = {row["strategy"]: row for row in rows}
    assert by_strategy["single_agent"]["true_positive_count"] == "1"
    assert by_strategy["single_agent"]["false_positive_count"] == "1"
    assert by_strategy["single_agent"]["false_negative_count"] == "1"
    assert by_strategy["single_agent"]["f1"] == "0.500000"
    assert by_strategy["single_agent"]["f1_delta_vs_acg_planned"] == "-0.500000"
    assert (out_dir / "after_strategy_scores.csv").exists()
    summary = (out_dir / "after_strategy_summary.csv").read_text()
    assert "single_agent" in summary
    assert "acg_planned" in summary
