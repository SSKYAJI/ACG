from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acg.compiler import compile_lockfile
from acg.repo_graph import load_context_graph
from acg.schema import AgentLock, TasksInput

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REAL_REPOS = PROJECT_ROOT / "experiments" / "real_repos"
OUT_DIR = Path(__file__).resolve().parent
MANIFEST = REAL_REPOS / "manifest.json"

PREDICTOR_FIELDS = [
    "repo",
    "task_id",
    "pr_number",
    "ground_truth_count",
    "predicted_count",
    "allowed_path_count",
    "true_positive_count",
    "false_positive_count",
    "false_negative_count",
    "recall",
    "precision",
    "f1",
    "exact_overlap",
    "predicted_writes",
    "allowed_paths",
    "ground_truth_files",
    "false_positives",
    "false_negatives",
]


@dataclass(frozen=True)
class EvalTask:
    repo: str
    pr_number: str
    task_id: str
    prompt: str
    checkout_path: Path
    task_path: Path
    lock_path: Path
    ground_truth: list[str]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _task_id_for(pr_number: Any, task_path: Path) -> str:
    payload = _load_json(task_path)
    tasks = payload.get("tasks") if isinstance(payload, dict) else None
    if isinstance(tasks, list) and tasks and isinstance(tasks[0], dict):
        task_id = tasks[0].get("id")
        if isinstance(task_id, str) and task_id:
            return task_id
    return f"pr-{pr_number}"


def discover_tasks() -> list[EvalTask]:
    manifest = _load_json(MANIFEST)
    tasks: list[EvalTask] = []
    for repo in manifest.get("repos", []):
        if not isinstance(repo, dict):
            continue
        short = repo.get("short_name")
        checkout = repo.get("checkout_path")
        if not isinstance(short, str) or not isinstance(checkout, str):
            continue
        repo_dir = REAL_REPOS / short
        checkout_path = PROJECT_ROOT / checkout
        for item in repo.get("tasks", []):
            if not isinstance(item, dict):
                continue
            pr_number = str(item.get("pr_number", ""))
            ground_truth = [
                path for path in item.get("ground_truth_files", []) if isinstance(path, str)
            ]
            task_candidates = sorted((repo_dir / "tasks").glob(f"*{pr_number}*.json"))
            lock_candidates = sorted(repo_dir.glob(f"agent_lock*{pr_number}*.json"))
            if not task_candidates or not lock_candidates or not ground_truth:
                continue
            tasks.append(
                EvalTask(
                    repo=short,
                    pr_number=pr_number,
                    task_id=_task_id_for(pr_number, task_candidates[0]),
                    prompt=str(item.get("task_prompt") or ""),
                    checkout_path=checkout_path,
                    task_path=task_candidates[0],
                    lock_path=lock_candidates[0],
                    ground_truth=ground_truth,
                )
            )
    return sorted(tasks, key=lambda task: (task.repo, task.pr_number, task.task_id))


def _predicted_for_task(lock: AgentLock, task_id: str) -> tuple[list[str], list[str]]:
    for task in lock.tasks:
        if task.id == task_id:
            return [write.path for write in task.predicted_writes], list(task.allowed_paths)
    if len(lock.tasks) == 1:
        task = lock.tasks[0]
        return [write.path for write in task.predicted_writes], list(task.allowed_paths)
    return [], []


def _predicted_items_for_task(lock: AgentLock, task_id: str) -> list[dict[str, Any]]:
    for task in lock.tasks:
        if task.id == task_id:
            return [write.model_dump() for write in task.predicted_writes]
    if len(lock.tasks) == 1:
        return [write.model_dump() for write in lock.tasks[0].predicted_writes]
    return []


class ReplayLockLLM:
    """Replay the baseline lockfile's LLM writes to isolate graph expansion.

    The before CSV is based on already-generated lockfiles, some produced with
    real provider calls. Replaying those predictions during the after compile
    keeps the LLM contribution fixed, so the CSV measures only deterministic
    graph/index changes instead of comparing a real old LLM to a mock new one.
    """

    model = "lockfile-replay"

    def __init__(self, writes: list[dict[str, Any]]) -> None:
        self._writes = writes

    def complete(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None = None,
    ) -> str:
        del messages, response_format
        return json.dumps({"writes": self._writes})


def _metric_row(task: EvalTask, predicted: list[str], allowed: list[str]) -> dict[str, str]:
    predicted_set = set(predicted)
    truth_set = set(task.ground_truth)
    tp = sorted(predicted_set & truth_set)
    fp = sorted(predicted_set - truth_set)
    fn = sorted(truth_set - predicted_set)
    recall = len(tp) / len(truth_set) if truth_set else 0.0
    precision = len(tp) / len(predicted_set) if predicted_set else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "repo": task.repo,
        "task_id": task.task_id,
        "pr_number": task.pr_number,
        "ground_truth_count": str(len(truth_set)),
        "predicted_count": str(len(predicted_set)),
        "allowed_path_count": str(len(set(allowed))),
        "true_positive_count": str(len(tp)),
        "false_positive_count": str(len(fp)),
        "false_negative_count": str(len(fn)),
        "recall": f"{recall:.6f}",
        "precision": f"{precision:.6f}",
        "f1": f"{f1:.6f}",
        "exact_overlap": str(predicted_set == truth_set).lower(),
        "predicted_writes": ";".join(sorted(predicted_set)),
        "allowed_paths": ";".join(sorted(set(allowed))),
        "ground_truth_files": ";".join(sorted(truth_set)),
        "false_positives": ";".join(fp),
        "false_negatives": ";".join(fn),
    }


def write_predictor_csv(mode: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    locks_dir = OUT_DIR / "after_locks"
    if mode == "after":
        locks_dir.mkdir(parents=True, exist_ok=True)
    for task in discover_tasks():
        if mode == "before":
            lock = AgentLock.model_validate_json(task.lock_path.read_text())
        else:
            baseline_lock = AgentLock.model_validate_json(task.lock_path.read_text())
            tasks_input = TasksInput.model_validate_json(task.task_path.read_text())
            repo_graph = load_context_graph(task.checkout_path)
            llm = ReplayLockLLM(_predicted_items_for_task(baseline_lock, task.task_id))
            lock = compile_lockfile(task.checkout_path, tasks_input, repo_graph, llm)
            (locks_dir / f"{task.repo}-{task.task_id}.json").write_text(
                lock.model_dump_json(indent=2) + "\n"
            )
        predicted, allowed = _predicted_for_task(lock, task.task_id)
        rows.append(_metric_row(task, predicted, allowed))
    out_path = OUT_DIR / f"{mode}_predictor.csv"
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PREDICTOR_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def _read_csv(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    with path.open(newline="") as fh:
        return {(row["repo"], row["task_id"]): row for row in csv.DictReader(fh)}


def write_diff_csv() -> None:
    before_path = OUT_DIR / "before_predictor.csv"
    after_path = OUT_DIR / "after_predictor.csv"
    if not before_path.exists() or not after_path.exists():
        return
    before = _read_csv(before_path)
    after = _read_csv(after_path)
    fields = [
        "repo",
        "task_id",
        "pr_number",
        "recall_before",
        "recall_after",
        "recall_delta",
        "precision_before",
        "precision_after",
        "precision_delta",
        "f1_before",
        "f1_after",
        "f1_delta",
        "predicted_count_before",
        "predicted_count_after",
        "allowed_path_count_before",
        "allowed_path_count_after",
        "new_true_positives",
        "new_false_positives",
        "remaining_false_negatives",
    ]
    rows: list[dict[str, str]] = []
    for key in sorted(before.keys() | after.keys()):
        old = before.get(key)
        new = after.get(key)
        if old is None or new is None:
            continue
        old_pred = set(filter(None, old["predicted_writes"].split(";")))
        new_pred = set(filter(None, new["predicted_writes"].split(";")))
        truth = set(filter(None, new["ground_truth_files"].split(";")))
        rows.append(
            {
                "repo": key[0],
                "task_id": key[1],
                "pr_number": new["pr_number"],
                "recall_before": old["recall"],
                "recall_after": new["recall"],
                "recall_delta": f"{float(new['recall']) - float(old['recall']):.6f}",
                "precision_before": old["precision"],
                "precision_after": new["precision"],
                "precision_delta": f"{float(new['precision']) - float(old['precision']):.6f}",
                "f1_before": old["f1"],
                "f1_after": new["f1"],
                "f1_delta": f"{float(new['f1']) - float(old['f1']):.6f}",
                "predicted_count_before": old["predicted_count"],
                "predicted_count_after": new["predicted_count"],
                "allowed_path_count_before": old["allowed_path_count"],
                "allowed_path_count_after": new["allowed_path_count"],
                "new_true_positives": ";".join(sorted((new_pred - old_pred) & truth)),
                "new_false_positives": ";".join(sorted((new_pred - old_pred) - truth)),
                "remaining_false_negatives": new["false_negatives"],
            }
        )
    with (OUT_DIR / "predictor_diff.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _summary_metric(summary: dict[str, Any], key: str) -> str:
    value = summary.get(key)
    return "" if value is None else str(value)


def _token_rows_for_task(task: EvalTask) -> list[dict[str, str]]:
    repo_dir = REAL_REPOS / task.repo
    rows: list[dict[str, str]] = []
    for path in sorted(repo_dir.glob(f"runs*/{task.task_id}/eval_run_combined.json")):
        payload = _load_json(path)
        strategies = payload.get("strategies")
        if not isinstance(strategies, dict):
            continue
        run_set = path.relative_to(repo_dir).parts[0]
        for strategy, run in sorted(strategies.items()):
            if not isinstance(run, dict):
                continue
            summary = run.get("summary_metrics")
            if not isinstance(summary, dict):
                continue
            rows.append(
                {
                    "repo": task.repo,
                    "task_id": task.task_id,
                    "pr_number": task.pr_number,
                    "run_set": run_set,
                    "strategy": str(strategy),
                    "status": "available",
                    "prompt_tokens": _summary_metric(summary, "tokens_prompt_total"),
                    "completion_tokens": _summary_metric(summary, "tokens_completion_total"),
                    "cost_usd": _summary_metric(summary, "cost_usd_total"),
                    "source": str(path.relative_to(PROJECT_ROOT)),
                }
            )
    return rows


def write_openrouter_tokens_csv(mode: str) -> None:
    fields = [
        "repo",
        "task_id",
        "pr_number",
        "run_set",
        "strategy",
        "status",
        "prompt_tokens",
        "completion_tokens",
        "cost_usd",
        "source",
    ]
    rows: list[dict[str, str]] = []
    for task in discover_tasks():
        if mode == "before":
            rows.extend(_token_rows_for_task(task))
            continue
        rows.append(
            {
                "repo": task.repo,
                "task_id": task.task_id,
                "pr_number": task.pr_number,
                "run_set": "",
                "strategy": "",
                "status": "unavailable",
                "prompt_tokens": "",
                "completion_tokens": "",
                "cost_usd": "",
                "source": "live OpenRouter rerun not available; predictor CSV uses lockfile replay",
            }
        )
    if not rows:
        rows = [
            {
                "repo": "",
                "task_id": "",
                "pr_number": "",
                "run_set": "",
                "strategy": "",
                "status": "unavailable",
                "prompt_tokens": "",
                "completion_tokens": "",
                "cost_usd": "",
                "source": "no existing OpenRouter eval_run_combined artifacts found",
            }
        ]
    with (OUT_DIR / f"{mode}_openrouter_tokens.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _count_from_pytest_output(pattern: str, output: str) -> str:
    matches = re.findall(pattern, output)
    return matches[-1] if matches else ""


def write_test_results_csv(phase: str, commands: list[str]) -> None:
    fields = ["phase", "command", "exit_code", "status", "passed_count", "warnings_count"]
    rows: list[dict[str, str]] = []
    for command in commands:
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            shell=True,
            capture_output=True,
            text=True,
        )
        output = f"{result.stdout}\n{result.stderr}"
        rows.append(
            {
                "phase": phase,
                "command": command,
                "exit_code": str(result.returncode),
                "status": "passed" if result.returncode == 0 else "failed",
                "passed_count": _count_from_pytest_output(r"(\d+) passed", output),
                "warnings_count": _count_from_pytest_output(r"(\d+) warnings?", output),
            }
        )
    path = OUT_DIR / "test_results.csv"
    existing: list[dict[str, str]] = []
    if path.exists():
        with path.open(newline="") as fh:
            reader = csv.DictReader(fh)
            if reader.fieldnames == fields:
                existing = list(reader)
    replacement_keys = {(row["phase"], row["command"]) for row in rows}
    existing = [
        row
        for row in existing
        if (row.get("phase", ""), row.get("command", "")) not in replacement_keys
    ]
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(existing + rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["before", "after"], required=True)
    parser.add_argument("--with-tests", action="append", default=[])
    args = parser.parse_args()

    write_predictor_csv(args.mode)
    write_openrouter_tokens_csv(args.mode)
    if args.mode == "after":
        write_diff_csv()
    if args.with_tests:
        write_test_results_csv(args.mode, args.with_tests)


if __name__ == "__main__":
    main()
