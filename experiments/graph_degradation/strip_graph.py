#!/usr/bin/env python3
"""Compile degraded context-graph variants and score predictor F1.

This experiment intentionally operates on copied repositories under
``experiments/graph_degradation/runs``. The original demo app is only read.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import traceback
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT_DIR = ROOT / "experiments" / "graph_degradation"
RUNS_DIR = EXPERIMENT_DIR / "runs"
DEFAULT_REPO = ROOT / "demo-app"
DEFAULT_GRAPH = DEFAULT_REPO / ".acg" / "context_graph.json"
DEFAULT_TASKS = DEFAULT_REPO / "tasks.json"
DEFAULT_GROUND_TRUTH = DEFAULT_REPO / "agent_lock.json"
ACG = ROOT / ".venv" / "bin" / "acg"

VARIANTS = (
    "degraded_no_symbols",
    "degraded_no_imports",
    "degraded_no_structure",
)


@dataclass(frozen=True)
class Metrics:
    precision: float
    recall: float
    f1: float
    true_positive: int
    predicted: int
    ground_truth: int


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def strip_symbols(graph: dict[str, Any]) -> None:
    graph["symbols_index"] = {}
    for entry in graph.get("files") or []:
        if isinstance(entry, dict):
            entry["symbols"] = []


def strip_imports(graph: dict[str, Any]) -> None:
    for entry in graph.get("files") or []:
        if isinstance(entry, dict):
            entry["imports"] = []
            entry["exports"] = []


def degraded_graph(source: dict[str, Any], variant: str) -> dict[str, Any]:
    graph = deepcopy(source)
    if variant in {"degraded_no_symbols", "degraded_no_structure"}:
        strip_symbols(graph)
    if variant in {"degraded_no_imports", "degraded_no_structure"}:
        strip_imports(graph)
    return graph


def write_failure(message: str, *, variant: str | None = None, details: str | None = None) -> None:
    lines = ["# FAILURE", ""]
    if variant:
        lines.append(f"Variant: `{variant}`")
        lines.append("")
    lines.append(message)
    if details:
        lines.extend(["", "```text", details.rstrip(), "```"])
    (EXPERIMENT_DIR / "FAILURE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def reset_marker_files() -> None:
    for name in ("DONE.md", "FAILURE.md"):
        path = EXPERIMENT_DIR / name
        if path.exists():
            path.unlink()


def copy_repo(source_repo: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)

    def ignore(_: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in {"node_modules", ".next", "dist", "build", "coverage"}
        }

    shutil.copytree(source_repo, destination, ignore=ignore)

    cache_dir = destination / ".acg" / "cache"
    if cache_dir.exists():
        shutil.rmtree(cache_dir)


def compile_variant(
    *,
    source_repo: Path,
    tasks: Path,
    variant: str,
    graph: dict[str, Any],
) -> Path:
    run_dir = RUNS_DIR / variant
    run_dir.mkdir(parents=True, exist_ok=True)
    write_json(run_dir / "context_graph.json", graph)

    repo_copy = run_dir / "repo"
    copy_repo(source_repo, repo_copy)
    repo_graph_path = repo_copy / ".acg" / "context_graph.json"
    write_json(repo_graph_path, graph)

    out_path = run_dir / "agent_lock.json"
    command = [
        str(ACG),
        "compile",
        "--repo",
        str(repo_copy),
        "--tasks",
        str(tasks),
        "--out",
        str(out_path),
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    (run_dir / "compile.stdout.txt").write_text(result.stdout, encoding="utf-8")
    (run_dir / "compile.stderr.txt").write_text(result.stderr, encoding="utf-8")
    if result.returncode != 0:
        details = "\n".join(
            [
                f"$ {' '.join(command)}",
                f"exit code: {result.returncode}",
                "",
                "STDOUT:",
                result.stdout,
                "STDERR:",
                result.stderr,
            ]
        )
        write_failure("`acg compile` failed.", variant=variant, details=details)
        raise SystemExit(result.returncode)
    return out_path


def writes_by_task(lockfile: dict[str, Any]) -> dict[str, set[str]]:
    by_task: dict[str, set[str]] = {}
    for task in lockfile.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        task_id = task.get("id")
        if not isinstance(task_id, str):
            continue
        paths: set[str] = set()
        for write in task.get("predicted_writes") or []:
            if isinstance(write, dict) and isinstance(write.get("path"), str):
                paths.add(write["path"])
            elif isinstance(write, str):
                paths.add(write)
        by_task[task_id] = paths
    return by_task


def micro_metrics(predicted: dict[str, set[str]], truth: dict[str, set[str]]) -> Metrics:
    pred_pairs = {
        (task_id, path)
        for task_id, paths in predicted.items()
        for path in paths
    }
    truth_pairs = {
        (task_id, path)
        for task_id, paths in truth.items()
        for path in paths
    }
    tp = len(pred_pairs & truth_pairs)
    precision = tp / len(pred_pairs) if pred_pairs else 0.0
    recall = tp / len(truth_pairs) if truth_pairs else 0.0
    f1 = (
        (2 * precision * recall / (precision + recall))
        if precision + recall
        else 0.0
    )
    return Metrics(
        precision=precision,
        recall=recall,
        f1=f1,
        true_positive=tp,
        predicted=len(pred_pairs),
        ground_truth=len(truth_pairs),
    )


def per_task_metrics(predicted: dict[str, set[str]], truth: dict[str, set[str]]) -> dict[str, dict[str, float | int]]:
    out: dict[str, dict[str, float | int]] = {}
    for task_id in sorted(set(predicted) | set(truth)):
        metrics = micro_metrics({task_id: predicted.get(task_id, set())}, {task_id: truth.get(task_id, set())})
        out[task_id] = {
            "precision": metrics.precision,
            "recall": metrics.recall,
            "f1": metrics.f1,
            "true_positive": metrics.true_positive,
            "predicted": metrics.predicted,
            "ground_truth": metrics.ground_truth,
        }
    return out


def graph_read_investigation(source_graph: dict[str, Any]) -> dict[str, Any]:
    source_symbols = len(source_graph.get("symbols_index") or {})
    source_file_symbols = sum(
        len(entry.get("symbols") or [])
        for entry in source_graph.get("files") or []
        if isinstance(entry, dict)
    )
    source_imports = sum(
        len(entry.get("imports") or [])
        for entry in source_graph.get("files") or []
        if isinstance(entry, dict)
    )
    source_exports = sum(
        len(entry.get("exports") or [])
        for entry in source_graph.get("files") or []
        if isinstance(entry, dict)
    )
    variants: dict[str, Any] = {}
    for variant in VARIANTS:
        graph = load_json(RUNS_DIR / variant / "repo" / ".acg" / "context_graph.json")
        variants[variant] = {
            "symbols_index": len(graph.get("symbols_index") or {}),
            "file_symbols": sum(
                len(entry.get("symbols") or [])
                for entry in graph.get("files") or []
                if isinstance(entry, dict)
            ),
            "imports": sum(
                len(entry.get("imports") or [])
                for entry in graph.get("files") or []
                if isinstance(entry, dict)
            ),
            "exports": sum(
                len(entry.get("exports") or [])
                for entry in graph.get("files") or []
                if isinstance(entry, dict)
            ),
            "compile_stdout": (RUNS_DIR / variant / "compile.stdout.txt").read_text(encoding="utf-8"),
        }
    return {
        "source": {
            "symbols_index": source_symbols,
            "file_symbols": source_file_symbols,
            "imports": source_imports,
            "exports": source_exports,
        },
        "variants": variants,
    }


def fmt(value: float) -> str:
    return f"{value:.3f}"


def write_reports(results: list[dict[str, Any]]) -> None:
    write_json(EXPERIMENT_DIR / "report.json", {"variants": results})

    lines = [
        "# Graph Degradation Report",
        "",
        "| Variant | precision | recall | F1 | delta F1 vs control |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in results:
        lines.append(
            "| {variant} | {precision} | {recall} | {f1} | {delta} |".format(
                variant=row["variant"],
                precision=fmt(row["precision"]),
                recall=fmt(row["recall"]),
                f1=fmt(row["f1"]),
                delta=fmt(row["delta_f1_vs_control"]),
            )
        )
    (EXPERIMENT_DIR / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_done(results: list[dict[str, Any]]) -> None:
    by_variant = {row["variant"]: row for row in results}
    drop_points = (
        by_variant["control"]["f1"] - by_variant["degraded_no_symbols"]["f1"]
    ) * 100
    headline = (
        "Stripping the symbols index from the TypeScript graph dropped predictor "
        f"F1 by {drop_points:.1f} points; the contract is only as good as the "
        "graph it compiles from."
    )
    lines = [
        f"# {headline}",
        "",
        "See `report.md` and `report.json` for the full precision/recall table.",
    ]
    (EXPERIMENT_DIR / "DONE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> int:
    reset_marker_files()
    source_repo = args.repo.resolve()
    source_graph_path = args.graph.resolve()
    tasks = args.tasks.resolve()
    ground_truth_path = args.ground_truth.resolve()

    source_graph = load_json(source_graph_path)
    ground_truth = load_json(ground_truth_path)
    truth_writes = writes_by_task(ground_truth)

    RUNS_DIR.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    control_metrics = micro_metrics(writes_by_task(ground_truth), truth_writes)
    results.append(
        {
            "variant": "control",
            "precision": control_metrics.precision,
            "recall": control_metrics.recall,
            "f1": control_metrics.f1,
            "delta_f1_vs_control": 0.0,
            "true_positive": control_metrics.true_positive,
            "predicted": control_metrics.predicted,
            "ground_truth": control_metrics.ground_truth,
            "per_task": per_task_metrics(writes_by_task(ground_truth), truth_writes),
            "lockfile": str(ground_truth_path.relative_to(ROOT)),
        }
    )

    for variant in VARIANTS:
        graph = degraded_graph(source_graph, variant)
        try:
            lock_path = compile_variant(
                source_repo=source_repo,
                tasks=tasks,
                variant=variant,
                graph=graph,
            )
        except Exception:
            if not (EXPERIMENT_DIR / "FAILURE.md").exists():
                write_failure(
                    "Experiment failed with an unhandled exception.",
                    variant=variant,
                    details=traceback.format_exc(),
                )
            raise
        lockfile = load_json(lock_path)
        predicted = writes_by_task(lockfile)
        metrics = micro_metrics(predicted, truth_writes)
        results.append(
            {
                "variant": variant,
                "precision": metrics.precision,
                "recall": metrics.recall,
                "f1": metrics.f1,
                "delta_f1_vs_control": metrics.f1 - control_metrics.f1,
                "true_positive": metrics.true_positive,
                "predicted": metrics.predicted,
                "ground_truth": metrics.ground_truth,
                "per_task": per_task_metrics(predicted, truth_writes),
                "lockfile": str(lock_path.relative_to(ROOT)),
                "graph": str((RUNS_DIR / variant / "context_graph.json").relative_to(ROOT)),
                "repo_graph": str((RUNS_DIR / variant / "repo" / ".acg" / "context_graph.json").relative_to(ROOT)),
            }
        )

    if len({round(row["f1"], 12) for row in results}) == 1:
        investigation = graph_read_investigation(source_graph)
        write_json(EXPERIMENT_DIR / "report.json", {"variants": results, "investigation": investigation})
        write_failure(
            "All variants produced identical F1 after confirming the copied repo graphs were degraded. "
            "This suggests the current predictor path is insensitive to these graph fields for the demo tasks.",
            details=json.dumps(investigation, indent=2, sort_keys=True),
        )
        return 1

    write_reports(results)
    write_done(results)
    return 0


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=DEFAULT_REPO)
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    parser.add_argument("--ground-truth", type=Path, default=DEFAULT_GROUND_TRUTH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    return run(parse_args(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
