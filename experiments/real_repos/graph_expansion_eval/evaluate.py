from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acg.compiler import compile_lockfile
from acg.llm import LLMClient, MockLLMClient
from acg.repo_graph import detect_language, load_context_graph, scan_context_graph
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
    "hard_recall",
    "hard_precision",
    "hard_f1",
    "candidate_context_count",
    "candidate_recall",
    "candidate_precision",
    "candidate_f1",
    "hard_fp_per_task",
    "blocked_true_positive_count",
    "approval_needed_count",
    "hard_conflict_pair_count",
    "candidate_conflict_pair_count",
    "tokens_planner_total",
    "tokens_scope_review_total",
    "tokens_localization_total",
    "exact_overlap",
    "predicted_writes",
    "allowed_paths",
    "candidate_context_paths",
    "must_write_paths",
    "ground_truth_files",
    "false_positives",
    "false_negatives",
    "candidate_false_positives",
    "candidate_false_negatives",
    "localization_backend",
    "ablation_name",
    "scip_status",
    "scip_definition_file_count",
    "scip_reference_file_count",
    "scip_signal_scope_count",
    "scip_signal_must_write_count",
    "scip_signal_candidate_context_count",
    "scip_signal_needs_replan_count",
    "scip_signal_true_positive_count",
    "scip_signal_false_positive_count",
    "scip_signal_false_negative_count",
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


def _predicted_for_task(lock: AgentLock, task_id: str) -> tuple[list[str], list[str], list[str]]:
    for task in lock.tasks:
        if task.id == task_id:
            return (
                [write.path for write in task.predicted_writes],
                list(task.allowed_paths),
                list(task.candidate_context_paths),
            )
    if len(lock.tasks) == 1:
        task = lock.tasks[0]
        return (
            [write.path for write in task.predicted_writes],
            list(task.allowed_paths),
            list(task.candidate_context_paths),
        )
    return [], [], []


def _predicted_items_for_task(lock: AgentLock, task_id: str) -> list[dict[str, Any]]:
    for task in lock.tasks:
        if task.id == task_id:
            return [write.model_dump() for write in task.predicted_writes]
    if len(lock.tasks) == 1:
        return [write.model_dump() for write in lock.tasks[0].predicted_writes]
    return []


def _file_scopes_for_task(lock: AgentLock, task_id: str) -> list[Any]:
    for task in lock.tasks:
        if task.id == task_id:
            return list(task.file_scopes)
    if len(lock.tasks) == 1:
        return list(lock.tasks[0].file_scopes)
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


def _prf(
    predicted_set: set[str],
    truth_set: set[str],
) -> tuple[list[str], list[str], list[str], float, float, float]:
    tp = sorted(predicted_set & truth_set)
    fp = sorted(predicted_set - truth_set)
    fn = sorted(truth_set - predicted_set)
    recall = len(tp) / len(truth_set) if truth_set else 0.0
    precision = len(tp) / len(predicted_set) if predicted_set else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return tp, fp, fn, recall, precision, f1


def _conflict_pair_counts(lock: AgentLock) -> tuple[int, int]:
    hard_sets = [
        {write.path for write in task.predicted_writes}
        for task in lock.tasks
    ]
    candidate_sets = [
        hard | set(task.candidate_context_paths)
        for hard, task in zip(hard_sets, lock.tasks, strict=True)
    ]
    hard_pairs = 0
    candidate_pairs = 0
    for idx, hard in enumerate(hard_sets):
        for other_idx in range(idx + 1, len(hard_sets)):
            if hard & hard_sets[other_idx]:
                hard_pairs += 1
            if candidate_sets[idx] & candidate_sets[other_idx]:
                candidate_pairs += 1
    return hard_pairs, candidate_pairs


def _metric_row(
    task: EvalTask,
    predicted: list[str],
    allowed: list[str],
    candidate_context: list[str],
    hard_conflict_pair_count: int,
    candidate_conflict_pair_count: int,
    tokens_planner_total: int | None = None,
    tokens_scope_review_total: int | None = None,
    *,
    localization_backend: str = "native",
    ablation_name: str = "",
    repo_graph: dict[str, Any] | None = None,
    file_scopes: list[Any] | None = None,
) -> dict[str, str]:
    hard_set = set(predicted)
    candidate_set = hard_set | set(candidate_context)
    truth_set = set(task.ground_truth)
    tp, fp, fn, recall, precision, f1 = _prf(hard_set, truth_set)
    ctp, cfp, cfn, crecall, cprecision, cf1 = _prf(candidate_set, truth_set)
    del ctp
    approval_needed = sorted((truth_set & set(candidate_context)) - hard_set)
    scip_metrics = _scip_metrics(repo_graph or {}, file_scopes or [], truth_set)
    tokens_localization_total = (
        None
        if tokens_planner_total is None and tokens_scope_review_total is None
        else (tokens_planner_total or 0) + (tokens_scope_review_total or 0)
    )
    return {
        "repo": task.repo,
        "task_id": task.task_id,
        "pr_number": task.pr_number,
        "ground_truth_count": str(len(truth_set)),
        "predicted_count": str(len(hard_set)),
        "allowed_path_count": str(len(set(allowed))),
        "true_positive_count": str(len(tp)),
        "false_positive_count": str(len(fp)),
        "false_negative_count": str(len(fn)),
        "recall": f"{recall:.6f}",
        "precision": f"{precision:.6f}",
        "f1": f"{f1:.6f}",
        "hard_recall": f"{recall:.6f}",
        "hard_precision": f"{precision:.6f}",
        "hard_f1": f"{f1:.6f}",
        "candidate_context_count": str(len(set(candidate_context))),
        "candidate_recall": f"{crecall:.6f}",
        "candidate_precision": f"{cprecision:.6f}",
        "candidate_f1": f"{cf1:.6f}",
        "hard_fp_per_task": str(len(fp)),
        "blocked_true_positive_count": str(len(truth_set - hard_set)),
        "approval_needed_count": str(len(approval_needed)),
        "hard_conflict_pair_count": str(hard_conflict_pair_count),
        "candidate_conflict_pair_count": str(candidate_conflict_pair_count),
        "tokens_planner_total": "" if tokens_planner_total is None else str(tokens_planner_total),
        "tokens_scope_review_total": (
            "" if tokens_scope_review_total is None else str(tokens_scope_review_total)
        ),
        "tokens_localization_total": (
            "" if tokens_localization_total is None else str(tokens_localization_total)
        ),
        "exact_overlap": str(hard_set == truth_set).lower(),
        "predicted_writes": ";".join(sorted(hard_set)),
        "allowed_paths": ";".join(sorted(set(allowed))),
        "candidate_context_paths": ";".join(sorted(set(candidate_context))),
        "must_write_paths": ";".join(sorted(hard_set)),
        "ground_truth_files": ";".join(sorted(truth_set)),
        "false_positives": ";".join(fp),
        "false_negatives": ";".join(fn),
        "candidate_false_positives": ";".join(cfp),
        "candidate_false_negatives": ";".join(cfn),
        "localization_backend": localization_backend,
        "ablation_name": ablation_name,
        **scip_metrics,
    }


def _scip_metrics(
    repo_graph: dict[str, Any],
    file_scopes: list[Any],
    truth_set: set[str],
) -> dict[str, str]:
    files = repo_graph.get("files") if isinstance(repo_graph, dict) else []
    definition_file_count = 0
    reference_file_count = 0
    if isinstance(files, list):
        for entry in files:
            if not isinstance(entry, dict):
                continue
            if _safe_int(entry.get("scip_definition_count")) > 0:
                definition_file_count += 1
            if _safe_int(entry.get("scip_reference_count")) > 0:
                reference_file_count += 1

    scip_paths: set[str] = set()
    tier_counts = {"must_write": 0, "candidate_context": 0, "needs_replan": 0}
    for scope in file_scopes:
        path = getattr(scope, "path", "")
        tier = getattr(scope, "tier", "")
        signals = getattr(scope, "signals", [])
        if not isinstance(signals, list):
            continue
        if not any(
            isinstance(signal, str) and signal.lower().startswith("scip")
            for signal in signals
        ):
            continue
        if isinstance(path, str) and path:
            scip_paths.add(path)
        if tier in tier_counts:
            tier_counts[tier] += 1

    status = repo_graph.get("scip_status") if isinstance(repo_graph, dict) else None
    if isinstance(status, dict):
        status_text = str(status.get("status") or "")
        index_path = str(status.get("index_path") or "")
    elif status is None:
        status_text = ""
        index_path = ""
    else:
        status_text = str(status)
        index_path = ""
    summary = repo_graph.get("scip_summary") if isinstance(repo_graph, dict) else None
    if isinstance(summary, dict):
        scip_file_count = _safe_int(summary.get("file_count"))
        scip_symbol_count = _safe_int(summary.get("symbol_count"))
        scip_reference_count = _safe_int(summary.get("reference_count"))
    else:
        scip_file_count = 0
        scip_symbol_count = 0
        scip_reference_count = 0
    if not scip_file_count:
        scip_file_count = len(
            {
                str(entity.get("path"))
                for entity in repo_graph.get("scip_entities", [])
                if isinstance(entity, dict) and entity.get("path")
            }
            | {
                str(reference.get("path"))
                for reference in repo_graph.get("scip_references", [])
                if isinstance(reference, dict) and reference.get("path")
            }
        )
    if not scip_symbol_count:
        scip_symbol_count = len(repo_graph.get("scip_entities", []) or [])
    if not scip_reference_count:
        scip_reference_count = len(repo_graph.get("scip_references", []) or [])

    scip_tp, scip_fp, scip_fn, scip_recall, scip_precision, scip_f1 = _prf(
        scip_paths,
        truth_set,
    )
    return {
        "scip_status": status_text,
        "scip_definition_file_count": str(definition_file_count),
        "scip_reference_file_count": str(reference_file_count),
        "scip_signal_scope_count": str(len(scip_paths)),
        "scip_signal_must_write_count": str(tier_counts["must_write"]),
        "scip_signal_candidate_context_count": str(tier_counts["candidate_context"]),
        "scip_signal_needs_replan_count": str(tier_counts["needs_replan"]),
        "scip_signal_true_positive_count": str(len(scip_paths & truth_set)),
        "scip_signal_false_positive_count": str(len(scip_paths - truth_set)),
        "scip_signal_false_negative_count": str(len(truth_set - scip_paths)),
        "scip_index_path": index_path,
        "scip_file_count": str(scip_file_count),
        "scip_symbol_count": str(scip_symbol_count),
        "scip_reference_count": str(scip_reference_count),
        "scip_candidate_count": str(len(scip_paths)),
        "scip_true_positive_count": str(len(scip_tp)),
        "scip_false_positive_count": str(len(scip_fp)),
        "scip_false_negative_count": str(len(scip_fn)),
        "scip_recall": f"{scip_recall:.6f}",
        "scip_precision": f"{scip_precision:.6f}",
        "scip_f1": f"{scip_f1:.6f}",
        "candidate_recall_delta_vs_native": "",
        "hard_recall_delta_vs_native": "",
    }


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _load_dotenv(path: Path = PROJECT_ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")


def _output_stem(
    mode: str,
    llm_mode: str,
    localization_backend: str = "native",
    ablation_name: str = "",
) -> str:
    parts = [mode]
    label = (ablation_name or "").strip().lower().replace("-", "_")
    backend = localization_backend.strip().lower()
    if backend != "native":
        parts.append(backend)
    if label:
        parts.append(label)
    if mode == "after" and llm_mode != "replay":
        parts.append(llm_mode)
    return "_".join(parts)


def _diff_name(
    llm_mode: str,
    localization_backend: str = "native",
    ablation_name: str = "",
) -> str:
    backend = localization_backend.strip().lower()
    label = (ablation_name or "").strip().lower().replace("-", "_")
    parts = ["predictor_diff"]
    if backend != "native":
        parts.append(backend)
    if label:
        parts.append(label)
    if llm_mode != "replay":
        parts.append(llm_mode)
    return "_".join(parts) + ".csv"


def _locks_dir_name(output_stem: str, llm_mode: str) -> str:
    if output_stem == "after":
        return "after_locks"
    if output_stem == f"after_{llm_mode}":
        return f"after_locks_{llm_mode}"
    return f"{output_stem}_locks"


def _llm_for_eval(llm_mode: str, baseline_lock: AgentLock, task_id: str) -> Any:
    if llm_mode == "replay":
        return ReplayLockLLM(_predicted_items_for_task(baseline_lock, task_id))
    if llm_mode == "mock":
        return MockLLMClient()
    if llm_mode == "live":
        _load_dotenv()
        llm = LLMClient.from_env()
        if isinstance(llm, MockLLMClient):
            raise RuntimeError(
                "live eval requested but no ACG_LLM_API_KEY/GROQ_API_KEY is configured"
            )
        return llm
    raise ValueError(f"unknown llm_mode: {llm_mode}")


def write_predictor_csv(
    mode: str,
    llm_mode: str = "replay",
    localization_backend: str = "native",
    ablation_name: str = "",
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    output_stem = _output_stem(mode, llm_mode, localization_backend, ablation_name)
    locks_dir = OUT_DIR / _locks_dir_name(output_stem, llm_mode)
    if mode == "after":
        locks_dir.mkdir(parents=True, exist_ok=True)
    for task in discover_tasks():
        repo_graph = load_context_graph(task.checkout_path)
        if mode == "before":
            lock = AgentLock.model_validate_json(task.lock_path.read_text())
        else:
            baseline_lock = AgentLock.model_validate_json(task.lock_path.read_text())
            tasks_input = TasksInput.model_validate_json(task.task_path.read_text())
            detected_language = detect_language(task.checkout_path)
            graph_paths = [
                entry.get("path", "")
                for entry in repo_graph.get("files", [])
                if isinstance(entry, dict)
            ]
            has_ignored_paths = any(
                path.startswith((".venv/", "node_modules/", "vendor/"))
                for path in graph_paths
            )
            graph_backend = repo_graph.get("localization_backend") or "native"
            backend_mismatch = graph_backend != localization_backend
            status = repo_graph.get("scip_status")
            scip_unavailable = (
                localization_backend in {"scip", "auto"}
                and (
                    not isinstance(status, dict)
                    or status.get("status") != "ok"
                )
            )
            if (
                not repo_graph
                or repo_graph.get("language") != detected_language
                or has_ignored_paths
                or backend_mismatch
                or scip_unavailable
            ):
                repo_graph = scan_context_graph(
                    task.checkout_path,
                    detected_language,
                    localization_backend=localization_backend,
                )
            llm = _llm_for_eval(llm_mode, baseline_lock, task.task_id)
            lock = compile_lockfile(task.checkout_path, tasks_input, repo_graph, llm)
            (locks_dir / f"{task.repo}-{task.task_id}.json").write_text(
                lock.model_dump_json(indent=2) + "\n"
            )
        hard_conflicts, candidate_conflicts = _conflict_pair_counts(lock)
        predicted, allowed, candidate_context = _predicted_for_task(lock, task.task_id)
        file_scopes = _file_scopes_for_task(lock, task.task_id)
        rows.append(
            _metric_row(
                task,
                predicted,
                allowed,
                candidate_context,
                hard_conflicts,
                candidate_conflicts,
                (
                    lock.generator.tokens_planner_total
                    if lock.generator is not None
                    else None
                ),
                (
                    lock.generator.tokens_scope_review_total
                    if lock.generator is not None
                    else None
                ),
                localization_backend=localization_backend,
                ablation_name=ablation_name,
                repo_graph=repo_graph,
                file_scopes=file_scopes,
            )
        )
    out_path = OUT_DIR / f"{output_stem}_predictor.csv"
    _attach_native_deltas(rows, mode, llm_mode, localization_backend, ablation_name)
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PREDICTOR_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def _read_csv(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    with path.open(newline="") as fh:
        return {(row["repo"], row["task_id"]): row for row in csv.DictReader(fh)}


def _attach_native_deltas(
    rows: list[dict[str, str]],
    mode: str,
    llm_mode: str,
    localization_backend: str,
    ablation_name: str,
) -> None:
    if not rows:
        return
    backend = localization_backend.strip().lower()
    label = (ablation_name or "").strip()
    if mode != "after" or (backend == "native" and not label):
        for row in rows:
            row["candidate_recall_delta_vs_native"] = "0.000000"
            row["hard_recall_delta_vs_native"] = "0.000000"
        return

    native_path = OUT_DIR / f"{_output_stem('after', llm_mode, 'native', '')}_predictor.csv"
    if not native_path.exists():
        return
    native_rows = _read_csv(native_path)
    for row in rows:
        native = native_rows.get((row["repo"], row["task_id"]))
        if native is None:
            continue
        row["candidate_recall_delta_vs_native"] = _float_delta(
            row.get("candidate_recall", ""),
            native.get("candidate_recall", ""),
        )
        row["hard_recall_delta_vs_native"] = _float_delta(
            row.get("hard_recall", ""),
            native.get("hard_recall", ""),
        )


def _float_delta(new_value: str, old_value: str) -> str:
    try:
        return f"{float(new_value) - float(old_value):.6f}"
    except (TypeError, ValueError):
        return ""


def write_diff_csv(
    llm_mode: str = "replay",
    localization_backend: str = "native",
    ablation_name: str = "",
) -> None:
    before_path = OUT_DIR / "before_predictor.csv"
    after_path = OUT_DIR / (
        f"{_output_stem('after', llm_mode, localization_backend, ablation_name)}_predictor.csv"
    )
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
        "candidate_recall_before",
        "candidate_recall_after",
        "candidate_precision_before",
        "candidate_precision_after",
        "approval_needed_before",
        "approval_needed_after",
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
                "candidate_recall_before": old.get("candidate_recall", ""),
                "candidate_recall_after": new.get("candidate_recall", ""),
                "candidate_precision_before": old.get("candidate_precision", ""),
                "candidate_precision_after": new.get("candidate_precision", ""),
                "approval_needed_before": old.get("approval_needed_count", ""),
                "approval_needed_after": new.get("approval_needed_count", ""),
                "predicted_count_before": old["predicted_count"],
                "predicted_count_after": new["predicted_count"],
                "allowed_path_count_before": old["allowed_path_count"],
                "allowed_path_count_after": new["allowed_path_count"],
                "new_true_positives": ";".join(sorted((new_pred - old_pred) & truth)),
                "new_false_positives": ";".join(sorted((new_pred - old_pred) - truth)),
                "remaining_false_negatives": new["false_negatives"],
            }
        )
    diff_name = _diff_name(llm_mode, localization_backend, ablation_name)
    with (OUT_DIR / diff_name).open("w", newline="") as fh:
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


def write_openrouter_tokens_csv(
    mode: str,
    llm_mode: str = "replay",
    localization_backend: str = "native",
    ablation_name: str = "",
) -> None:
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
                "source": (
                    "predictor CSV uses live model compile"
                    if llm_mode == "live"
                    else "live OpenRouter rerun not available; predictor CSV uses lockfile replay"
                ),
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
    output_stem = _output_stem(mode, llm_mode, localization_backend, ablation_name)
    with (OUT_DIR / f"{output_stem}_openrouter_tokens.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


STRATEGY_SCORE_FIELDS = [
    "repo",
    "task_id",
    "pr_number",
    "run_set",
    "strategy",
    "backend",
    "source",
    "localization_backend",
    "ablation_name",
    "status",
    "ground_truth_count",
    "actual_changed_count",
    "true_positive_count",
    "false_positive_count",
    "false_negative_count",
    "recall",
    "precision",
    "f1",
    "actual_changed_files",
    "ground_truth_files",
    "false_positives",
    "false_negatives",
    "out_of_bounds_count",
    "blocked_write_count",
    "approved_replan_count",
    "overlapping_write_pairs",
    "tokens_prompt_total",
    "tokens_completion_total",
    "tokens_all_in",
    "cost_usd_total",
    "f1_delta_vs_acg_planned",
    "recall_delta_vs_acg_planned",
    "precision_delta_vs_acg_planned",
    "tokens_all_in_delta_vs_acg_planned",
]

STRATEGY_SUMMARY_FIELDS = [
    "repo",
    "run_set",
    "strategy",
    "task_count",
    "macro_recall",
    "macro_precision",
    "macro_f1",
    "micro_recall",
    "micro_precision",
    "micro_f1",
    "total_out_of_bounds",
    "total_blocked_invalid",
    "total_tokens_all_in",
    "total_cost_usd",
    "macro_f1_delta_vs_acg_planned",
]


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def _number_text(value: Any) -> str:
    return "" if value is None else str(value)


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _task_payload_for(run: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    tasks = run.get("tasks")
    if not isinstance(tasks, list):
        return None
    for item in tasks:
        if isinstance(item, dict) and item.get("task_id") == task_id:
            return item
    return None


def _task_actual_files(task_payload: dict[str, Any] | None) -> list[str]:
    if not task_payload:
        return []
    value = task_payload.get("actual_changed_files")
    if not isinstance(value, list):
        return []
    return sorted({str(path).strip("./") for path in value if str(path).strip()})


def _strategy_score_rows_for_task(
    task: EvalTask,
    *,
    localization_backend: str,
    ablation_name: str,
) -> list[dict[str, str]]:
    repo_dir = REAL_REPOS / task.repo
    rows: list[dict[str, str]] = []
    for path in sorted(repo_dir.glob(f"runs*/{task.task_id}/eval_run_combined.json")):
        payload = _load_json(path)
        strategies = payload.get("strategies")
        if not isinstance(strategies, dict):
            continue
        run_set = path.relative_to(repo_dir).parts[0]
        truth_set = set(task.ground_truth)
        for strategy, run in sorted(strategies.items()):
            if not isinstance(run, dict):
                continue
            task_payload = _task_payload_for(run, task.task_id)
            actual = _task_actual_files(task_payload)
            actual_set = set(actual)
            tp, fp, fn, recall, precision, f1 = _prf(actual_set, truth_set)
            summary = run.get("summary_metrics")
            if not isinstance(summary, dict):
                summary = {}
            blocked_events = []
            approved_replans = []
            out_of_bounds = []
            status = "missing_task"
            if isinstance(task_payload, dict):
                status = str(task_payload.get("status") or "")
                blocked_events = task_payload.get("blocked_write_events") or []
                approved_replans = task_payload.get("approved_replan_files") or []
                out_of_bounds = task_payload.get("out_of_bounds_files") or []
            rows.append(
                {
                    "repo": task.repo,
                    "task_id": task.task_id,
                    "pr_number": task.pr_number,
                    "run_set": run_set,
                    "strategy": str(strategy),
                    "backend": str(run.get("backend") or ""),
                    "source": _display_path(path),
                    "localization_backend": localization_backend,
                    "ablation_name": ablation_name,
                    "status": status,
                    "ground_truth_count": str(len(truth_set)),
                    "actual_changed_count": str(len(actual_set)),
                    "true_positive_count": str(len(tp)),
                    "false_positive_count": str(len(fp)),
                    "false_negative_count": str(len(fn)),
                    "recall": f"{recall:.6f}",
                    "precision": f"{precision:.6f}",
                    "f1": f"{f1:.6f}",
                    "actual_changed_files": ";".join(actual),
                    "ground_truth_files": ";".join(sorted(truth_set)),
                    "false_positives": ";".join(fp),
                    "false_negatives": ";".join(fn),
                    "out_of_bounds_count": str(len(out_of_bounds) if isinstance(out_of_bounds, list) else 0),
                    "blocked_write_count": str(len(blocked_events) if isinstance(blocked_events, list) else 0),
                    "approved_replan_count": str(len(approved_replans) if isinstance(approved_replans, list) else 0),
                    "overlapping_write_pairs": _number_text(summary.get("overlapping_write_pairs")),
                    "tokens_prompt_total": _number_text(summary.get("tokens_prompt_total")),
                    "tokens_completion_total": _number_text(summary.get("tokens_completion_total")),
                    "tokens_all_in": _number_text(summary.get("tokens_all_in")),
                    "cost_usd_total": _number_text(summary.get("cost_usd_total")),
                    "f1_delta_vs_acg_planned": "",
                    "recall_delta_vs_acg_planned": "",
                    "precision_delta_vs_acg_planned": "",
                    "tokens_all_in_delta_vs_acg_planned": "",
                }
            )
    return rows


def _attach_strategy_deltas(rows: list[dict[str, str]]) -> None:
    baselines = {
        (row["repo"], row["task_id"], row["run_set"]): row
        for row in rows
        if row["strategy"] == "acg_planned"
    }
    for row in rows:
        baseline = baselines.get((row["repo"], row["task_id"], row["run_set"]))
        if baseline is None:
            continue
        row["f1_delta_vs_acg_planned"] = _float_delta(row["f1"], baseline["f1"])
        row["recall_delta_vs_acg_planned"] = _float_delta(row["recall"], baseline["recall"])
        row["precision_delta_vs_acg_planned"] = _float_delta(row["precision"], baseline["precision"])
        row["tokens_all_in_delta_vs_acg_planned"] = _float_delta(
            row["tokens_all_in"],
            baseline["tokens_all_in"],
        )


def _strategy_summary_rows(score_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    groups: dict[tuple[str, str, str], list[dict[str, str]]] = {}
    for row in score_rows:
        groups.setdefault((row["repo"], row["run_set"], row["strategy"]), []).append(row)
    summary_rows: list[dict[str, str]] = []
    macro_f1_by_group: dict[tuple[str, str, str], float] = {}
    for key, rows in sorted(groups.items()):
        repo, run_set, strategy = key
        task_count = len(rows)
        recall_values = [float(row["recall"]) for row in rows]
        precision_values = [float(row["precision"]) for row in rows]
        f1_values = [float(row["f1"]) for row in rows]
        tp = sum(_safe_int(row["true_positive_count"]) for row in rows)
        fp = sum(_safe_int(row["false_positive_count"]) for row in rows)
        fn = sum(_safe_int(row["false_negative_count"]) for row in rows)
        micro_recall = tp / (tp + fn) if tp + fn else 0.0
        micro_precision = tp / (tp + fp) if tp + fp else 0.0
        micro_f1 = (
            2 * micro_precision * micro_recall / (micro_precision + micro_recall)
            if micro_precision + micro_recall
            else 0.0
        )
        total_tokens = sum(
            value for row in rows if (value := _float_or_none(row["tokens_all_in"])) is not None
        )
        total_cost = sum(
            value for row in rows if (value := _float_or_none(row["cost_usd_total"])) is not None
        )
        macro_f1 = sum(f1_values) / task_count if task_count else 0.0
        macro_f1_by_group[key] = macro_f1
        summary_rows.append(
            {
                "repo": repo,
                "run_set": run_set,
                "strategy": strategy,
                "task_count": str(task_count),
                "macro_recall": f"{(sum(recall_values) / task_count if task_count else 0.0):.6f}",
                "macro_precision": f"{(sum(precision_values) / task_count if task_count else 0.0):.6f}",
                "macro_f1": f"{macro_f1:.6f}",
                "micro_recall": f"{micro_recall:.6f}",
                "micro_precision": f"{micro_precision:.6f}",
                "micro_f1": f"{micro_f1:.6f}",
                "total_out_of_bounds": str(sum(_safe_int(row["out_of_bounds_count"]) for row in rows)),
                "total_blocked_invalid": str(sum(_safe_int(row["blocked_write_count"]) for row in rows)),
                "total_tokens_all_in": f"{total_tokens:.0f}" if total_tokens else "",
                "total_cost_usd": f"{total_cost:.8f}" if total_cost else "",
                "macro_f1_delta_vs_acg_planned": "",
            }
        )
    for row in summary_rows:
        baseline = macro_f1_by_group.get((row["repo"], row["run_set"], "acg_planned"))
        if baseline is not None:
            row["macro_f1_delta_vs_acg_planned"] = f"{float(row['macro_f1']) - baseline:.6f}"
    return summary_rows


def write_strategy_score_csv(
    mode: str,
    llm_mode: str = "replay",
    localization_backend: str = "native",
    ablation_name: str = "",
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for task in discover_tasks():
        rows.extend(
            _strategy_score_rows_for_task(
                task,
                localization_backend=localization_backend,
                ablation_name=ablation_name,
            )
        )
    _attach_strategy_deltas(rows)
    summary_rows = _strategy_summary_rows(rows)
    output_stem = _output_stem(mode, llm_mode, localization_backend, ablation_name)
    with (OUT_DIR / f"{output_stem}_strategy_scores.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=STRATEGY_SCORE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with (OUT_DIR / f"{output_stem}_strategy_summary.csv").open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=STRATEGY_SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(summary_rows)
    return rows


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
    parser.add_argument(
        "--llm-mode",
        choices=["replay", "live", "mock"],
        default="replay",
        help=(
            "after-mode LLM source. replay isolates deterministic localization; "
            "live calls ACG_LLM_*; mock uses canned offline writes."
        ),
    )
    parser.add_argument("--with-tests", action="append", default=[])
    parser.add_argument(
        "--localization-backend",
        choices=["native", "scip", "auto"],
        default="native",
        help="Graph localization backend used for after-mode scans.",
    )
    parser.add_argument(
        "--ablation-name",
        default="",
        help="Optional output stem label for ablation CSVs and lock dirs.",
    )
    args = parser.parse_args()

    if args.mode == "before" and args.llm_mode != "replay":
        raise SystemExit("--llm-mode only applies to --mode after")

    write_predictor_csv(
        args.mode,
        args.llm_mode,
        args.localization_backend,
        args.ablation_name,
    )
    write_openrouter_tokens_csv(
        args.mode,
        args.llm_mode,
        args.localization_backend,
        args.ablation_name,
    )
    write_strategy_score_csv(
        args.mode,
        args.llm_mode,
        args.localization_backend,
        args.ablation_name,
    )
    if args.mode == "after":
        write_diff_csv(args.llm_mode, args.localization_backend, args.ablation_name)
    if args.with_tests:
        write_test_results_csv(args.mode, args.with_tests)


if __name__ == "__main__":
    main()
