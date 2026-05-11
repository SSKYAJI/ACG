from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_eval_seed_suffix_naming_stays_backward_compatible() -> None:
    evaluate = _load_evaluate_module()

    assert evaluate._seeded_output_stem("after_live", None) == "after_live"
    assert evaluate._seeded_output_stem("after_live", 1) == "after_live_seed1"
    assert evaluate._locks_dir_name("after_live_seed1", "live") == "after_live_seed1_locks"


def test_write_strategy_artifacts_for_seed_scopes_acg_llm_seed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    evaluate = _load_evaluate_module()
    real_repos = tmp_path / "real_repos"
    repo_dir = real_repos / "repo"
    checkout_path = repo_dir / "checkout"
    out_dir = tmp_path / "out"
    lock_dir = out_dir / "after_live_seed7_locks"
    lock_path = lock_dir / "repo-task.json"
    checkout_path.mkdir(parents=True)
    out_dir.mkdir()
    lock_dir.mkdir(parents=True)
    lock_path.write_text("{}\n")

    class _FakeLock(SimpleNamespace):
        def model_copy(self, *, deep: bool = False) -> "_FakeLock":
            # Each call yields a distinct instance so the harness can verify
            # per-strategy lock isolation (deep flag is honored by callers).
            return _FakeLock(tasks=list(self.tasks))

    fake_lock = _FakeLock(tasks=[SimpleNamespace(id="task")])
    calls: list[tuple[str, str | None, int]] = []
    seen_lock_ids: list[int] = []

    monkeypatch.setattr(evaluate, "REAL_REPOS", real_repos)
    monkeypatch.setattr(evaluate, "OUT_DIR", out_dir)
    monkeypatch.setattr(
        evaluate,
        "discover_tasks",
        lambda: [
            evaluate.EvalTask(
                repo="repo",
                pr_number="123",
                task_id="task",
                prompt="",
                checkout_path=checkout_path,
                task_path=repo_dir / "tasks" / "pr-123.json",
                lock_path=lock_path,
                ground_truth=["src/a.py"],
            )
        ],
    )
    monkeypatch.setattr(evaluate.AgentLock, "model_validate_json", lambda payload: fake_lock)
    monkeypatch.setattr(evaluate, "_repo_graph_for_task", lambda task, backend: {"backend": backend})
    monkeypatch.setattr(evaluate, "repo_from_path", lambda path: {"repo": str(path)})
    monkeypatch.setattr(evaluate, "to_dict", lambda run: run)

    def fake_run_strategy(
        *,
        strategy: str,
        backend: str,
        lock: object,
        repo_graph: object,
        lockfile_path: str,
        repo: object,
    ) -> dict[str, str]:
        del strategy, repo_graph, lockfile_path, repo
        calls.append((backend, os.environ.get("ACG_LLM_SEED"), id(lock)))
        seen_lock_ids.append(id(lock))
        return {"backend": backend}

    monkeypatch.setattr(evaluate, "run_strategy", fake_run_strategy)
    monkeypatch.setenv("ACG_LLM_SEED", "prior")

    run_sets = evaluate._write_strategy_artifacts_for_seed(
        output_stem="after_live",
        llm_mode="live",
        localization_backend="native",
        seed=7,
    )

    assert run_sets == {"runs_after_live_seed7", "runs_after_mock_seed7"}
    assert [(b, s) for b, s, _ in calls[:3]] == [
        ("local", "7"),
        ("local", "7"),
        ("local", "7"),
    ]
    assert [(b, s) for b, s, _ in calls[3:]] == [
        ("mock", "prior"),
        ("mock", "prior"),
        ("mock", "prior"),
    ]
    assert os.environ.get("ACG_LLM_SEED") == "prior"

    # Every strategy invocation must receive a freshly copied lock so that one
    # strategy's mutations (e.g. promote_candidate_paths during auto-replan)
    # cannot bleed into the next strategy in the loop.
    assert len(seen_lock_ids) == 6
    assert len(set(seen_lock_ids)) == 6
    # The baseline lock itself must not be reused as any strategy_lock.
    assert id(fake_lock) not in seen_lock_ids


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
    assert row["achievable_precision_at_recall_0.9"] == "0.100000"
    assert row["blocked_truth_recoverable_fraction"] == "0.000000"
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
        "achievable_precision_at_recall_0.9",
        "blocked_truth_recoverable_fraction",
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


def test_strategy_score_rows_filter_stale_run_sets(tmp_path: Path) -> None:
    evaluate = _load_evaluate_module()
    real_repos = tmp_path / "real_repos"
    repo_dir = real_repos / "repo"
    stale_dir = repo_dir / "runs_comparison_live" / "task"
    fresh_dir = repo_dir / "runs_after_live_seed1" / "task"
    out_dir = tmp_path / "out"
    stale_dir.mkdir(parents=True)
    fresh_dir.mkdir(parents=True)
    out_dir.mkdir()
    (stale_dir / "eval_run_combined.json").write_text(
        json.dumps(
            {
                "version": "0.1",
                "strategies": {
                    "acg_planned": {
                        "strategy": "acg_planned",
                        "backend": "local",
                        "tasks": [
                            {
                                "task_id": "task",
                                "status": "completed",
                                "actual_changed_files": ["src/stale.py"],
                            }
                        ],
                        "summary_metrics": {"overlapping_write_pairs": 0, "tokens_all_in": 10},
                    },
                    "single_agent": {
                        "strategy": "single_agent",
                        "backend": "local",
                        "tasks": [
                            {
                                "task_id": "task",
                                "status": "completed",
                                "actual_changed_files": ["src/stale_agent.py"],
                            }
                        ],
                        "summary_metrics": {"overlapping_write_pairs": 0, "tokens_all_in": 10},
                    },
                },
            }
        )
        + "\n"
    )
    (fresh_dir / "eval_run_combined.json").write_text(
        json.dumps(
            {
                "version": "0.1",
                "strategies": {
                    "acg_planned": {
                        "strategy": "acg_planned",
                        "backend": "mock",
                        "tasks": [
                            {
                                "task_id": "task",
                                "status": "completed",
                                "actual_changed_files": ["src/a.py"],
                            }
                        ],
                        "summary_metrics": {"overlapping_write_pairs": 0, "tokens_all_in": 20},
                    },
                    "naive_parallel": {
                        "strategy": "naive_parallel",
                        "backend": "mock",
                        "tasks": [
                            {
                                "task_id": "task",
                                "status": "completed",
                                "actual_changed_files": ["src/b.py"],
                            }
                        ],
                        "summary_metrics": {"overlapping_write_pairs": 0, "tokens_all_in": 30},
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
            ground_truth=["src/a.py"],
        )
    ]

    rows = evaluate._strategy_score_rows_for_task(
        evaluate.discover_tasks()[0],
        localization_backend="native",
        ablation_name="",
        run_sets={"runs_after_live_seed1"},
    )

    assert {row["run_set"] for row in rows} == {"runs_after_live_seed1"}
    assert {row["strategy"] for row in rows} == {"acg_planned", "naive_parallel"}
    assert all("runs_after_live_seed1" in row["source"] for row in rows)


def test_strategy_summary_variance_uses_population_std() -> None:
    evaluate = _load_evaluate_module()

    seed_summary_rows = [
        [
            {
                "backend": "local",
                "strategy": "acg_planned",
                "task_count": "2",
                "macro_recall": "0.500000",
                "macro_precision": "0.600000",
                "macro_f1": "0.400000",
                "approved_replan_count": "2",
                "total_out_of_bounds": "1",
                "total_blocked_invalid": "3",
                "total_tokens_all_in": "100",
                "total_cost_usd": "0.01000000",
            }
        ],
        [
            {
                "backend": "local",
                "strategy": "acg_planned",
                "task_count": "4",
                "macro_recall": "0.900000",
                "macro_precision": "0.800000",
                "macro_f1": "0.800000",
                "approved_replan_count": "6",
                "total_out_of_bounds": "2",
                "total_blocked_invalid": "4",
                "total_tokens_all_in": "140",
                "total_cost_usd": "0.03000000",
            }
        ],
    ]

    rows = evaluate._strategy_summary_variance_rows(seed_summary_rows)
    assert len(rows) == 1
    row = rows[0]
    assert row["backend"] == "local"
    assert row["strategy"] == "acg_planned"
    assert row["macro_f1_mean"] == "0.600000"
    assert row["macro_f1_std"] == "0.200000"
    assert row["macro_recall_mean"] == "0.700000"
    assert row["macro_recall_std"] == "0.200000"
    assert row["macro_precision_mean"] == "0.700000"
    assert row["macro_precision_std"] == "0.100000"
    assert row["approved_replan_count_mean"] == "4.000000"
    assert row["approved_replan_count_std"] == "2.000000"


def test_eval_predictor_variance_helpers_use_population_std() -> None:
    evaluate = _load_evaluate_module()

    seed_rows = [
        [
            {
                "repo": "repo",
                "task_id": "task-a",
                "pr_number": "1",
                "localization_backend": "native",
                "ablation_name": "",
                "predicted_count": "1",
                "recall": "0.5",
                "f1": "0.4",
                "candidate_context_count": "2",
                "achievable_precision_at_recall_0.9": "0.250000",
                "blocked_truth_recoverable_fraction": "0.500000",
            },
            {
                "repo": "repo",
                "task_id": "task-b",
                "pr_number": "2",
                "localization_backend": "native",
                "ablation_name": "",
                "predicted_count": "3",
                "recall": "0.25",
                "f1": "0.2",
                "candidate_context_count": "6",
                "achievable_precision_at_recall_0.9": "0.750000",
                "blocked_truth_recoverable_fraction": "1.500000",
            },
        ],
        [
            {
                "repo": "repo",
                "task_id": "task-a",
                "pr_number": "1",
                "localization_backend": "native",
                "ablation_name": "",
                "predicted_count": "5",
                "recall": "1.0",
                "f1": "0.8",
                "candidate_context_count": "4",
                "achievable_precision_at_recall_0.9": "0.500000",
                "blocked_truth_recoverable_fraction": "1.000000",
            },
            {
                "repo": "repo",
                "task_id": "task-b",
                "pr_number": "2",
                "localization_backend": "native",
                "ablation_name": "",
                "predicted_count": "7",
                "recall": "0.75",
                "f1": "0.6",
                "candidate_context_count": "8",
                "achievable_precision_at_recall_0.9": "1.000000",
                "blocked_truth_recoverable_fraction": "0.000000",
            },
        ],
    ]

    variance_rows = evaluate._predictor_variance_rows(seed_rows)
    by_task = {row["task_id"]: row for row in variance_rows}
    assert by_task["task-a"]["predicted_count_mean"] == "3.000000"
    assert by_task["task-a"]["predicted_count_std"] == "2.000000"
    assert by_task["task-b"]["predicted_count_mean"] == "5.000000"
    assert by_task["task-b"]["predicted_count_std"] == "2.000000"
    assert by_task["task-a"]["recall_mean"] == "0.750000"
    assert by_task["task-a"]["recall_std"] == "0.250000"
    assert by_task["task-b"]["f1_mean"] == "0.400000"
    assert by_task["task-b"]["f1_std"] == "0.200000"

    summary_rows = evaluate._predictor_summary_rows(seed_rows)
    assert len(summary_rows) == 1
    summary = summary_rows[0]
    assert summary["scope"] == "macro"
    assert summary["predicted_count_mean"] == "4.000000"
    assert summary["predicted_count_std"] == "2.000000"
    assert summary["candidate_count_median_mean"] == "5.000000"
    assert summary["candidate_count_median_std"] == "1.000000"
    assert summary["candidate_count_p95_mean"] == "7.000000"
    assert summary["candidate_count_p95_std"] == "1.000000"
    assert summary["candidate_count_min_mean"] == "3.000000"
    assert summary["candidate_count_min_std"] == "1.000000"
    assert summary["recall_mean"] == "0.625000"
    assert summary["recall_std"] == "0.250000"
    assert summary["f1_mean"] == "0.500000"
    assert summary["f1_std"] == "0.200000"
    assert summary["achievable_precision_at_recall_0.9_mean"] == "0.625000"
    assert summary["achievable_precision_at_recall_0.9_std"] == "0.125000"
    assert summary["blocked_truth_recoverable_fraction_mean"] == "0.750000"
    assert summary["blocked_truth_recoverable_fraction_std"] == "0.250000"
