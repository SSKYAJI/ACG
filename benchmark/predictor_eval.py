"""Evaluate deterministic indexers against small fixture datasets.

Python coverage notes
---------------------

This script also exercises ACG's new Python codemap on two pinned, real-world
repositories: ``pallets/click`` (non-runtime CLI library, ``src/`` layout)
and ``tiangolo/full-stack-fastapi-template`` (runtime FastAPI service). The
pins are intentionally separated from the eval logic so reviewers can update
them before re-running the paper benchmark; see :data:`PYTHON_DATASETS`.

For the Python datasets we additionally compute *secondary* metrics --
lockfile conflict count and a blocked-bad-write rate -- to demonstrate that
the downstream lockfile-enforcement pipeline holds on Python repos with no
Python-specific predictor logic.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from acg.compiler import compile_lockfile  # noqa: E402
from acg.enforce import EXIT_BLOCKED, cli_validate  # noqa: E402
from acg.index import aggregate  # noqa: E402
from acg.index.bm25 import BM25Indexer  # noqa: E402
from acg.index.cochange import CochangeIndexer  # noqa: E402
from acg.index.framework import FrameworkIndexer  # noqa: E402
from acg.index.pagerank import PageRankIndexer  # noqa: E402
from acg.index.types import Indexer  # noqa: E402
from acg.llm import LLMClient  # noqa: E402
from acg.repo_graph import scan_context_graph  # noqa: E402
from acg.schema import TaskInput, TaskInputHints, TasksInput  # noqa: E402

FIXTURE_DIR = ROOT / "benchmark" / "fixtures"
RESULTS_PATH = ROOT / "benchmark" / "results.json"


@dataclass(frozen=True)
class PythonDataset:
    """Configuration for a pinned Python benchmark repository.

    ``ref`` is anything ``git checkout`` accepts (commit SHA, tag, branch).
    Reviewers running for the paper should pin ``ref`` to an exact SHA for
    reproducibility -- branch names are fine for development iteration.
    """

    name: str
    fixture: str
    repo_url: str
    ref: str
    kind: str  # "runtime" | "non_runtime"


# NOTE: ``ref`` values below are intentionally human-meaningful (release
# tags / main) so this file is readable; lock to an exact SHA before
# generating the paper's headline numbers. Override at runtime with
# ``ACG_BENCHMARK_<NAME>_REF=<sha>`` if you want to bypass the default.
PYTHON_DATASETS: tuple[PythonDataset, ...] = (
    PythonDataset(
        name="fastapi-template",
        fixture="fastapi-template-tasks.json",
        repo_url="https://github.com/tiangolo/full-stack-fastapi-template.git",
        ref="master",
        kind="runtime",
    ),
    PythonDataset(
        name="click",
        fixture="click-tasks.json",
        repo_url="https://github.com/pallets/click.git",
        ref="8.1.7",
        kind="non_runtime",
    ),
)


def _python_dataset(name: str) -> PythonDataset | None:
    for ds in PYTHON_DATASETS:
        if ds.name == name:
            return ds
    return None


def _run(args: list[str], cwd: Path, timeout: int = 90) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True, timeout=timeout)


def _clone(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "clone", "--depth", "1", url, str(dest)], ROOT)
    return dest


def _clone_at_ref(url: str, ref: str, dest: Path) -> Path:
    """Clone ``url`` and check out ``ref`` (SHA, tag, or branch).

    A shallow clone is attempted first; if ``git checkout <ref>`` fails on
    the shallow copy (commonly the case for SHAs that aren't on the default
    branch tip), the directory is removed and a full clone is retried.
    """

    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        _run(["git", "clone", "--depth", "1", "--branch", ref, url, str(dest)], ROOT, timeout=180)
        return dest
    except subprocess.CalledProcessError:
        pass  # ref isn't a branch/tag tip; fall through to full clone.
    if dest.exists():
        _run(["rm", "-rf", str(dest)], ROOT)
    _run(["git", "clone", url, str(dest)], ROOT, timeout=300)
    _run(["git", "checkout", ref], dest, timeout=60)
    return dest


def _load_fixture(name: str) -> list[dict[str, Any]]:
    py_ds = _python_dataset(name)
    if py_ds is not None:
        return json.loads((FIXTURE_DIR / py_ds.fixture).read_text())
    fixture_name = "express-api" if name == "express" else name
    return json.loads((FIXTURE_DIR / f"{fixture_name}-tasks.json").read_text())


def _task(row: dict[str, Any]) -> TaskInput:
    hints = row.get("hints") or {}
    return TaskInput(
        id=row["id"],
        prompt=row["prompt"],
        hints=TaskInputHints(**hints) if hints else None,
    )


def _repo_for_dataset(name: str) -> Path:
    cache = ROOT / ".acg" / "benchmark_repos"
    if name == "demo-app":
        return ROOT / "demo-app"
    if name == "t3-app":
        return _clone("https://github.com/t3-oss/create-t3-app.git", cache / "create-t3-app")
    if name == "express":
        return _clone("https://github.com/expressjs/express.git", cache / "express")
    py_ds = _python_dataset(name)
    if py_ds is not None:
        ref = os.environ.get(f"ACG_BENCHMARK_{name.upper().replace('-', '_')}_REF", py_ds.ref)
        return _clone_at_ref(py_ds.repo_url, ref, cache / py_ds.name)
    raise ValueError(f"unknown dataset {name}")


def _language_for_dataset(name: str) -> str:
    if _python_dataset(name) is not None:
        return "python"
    if name in {"demo-app", "t3-app"}:
        return "typescript"
    if name == "express":
        return "javascript"
    return "auto"


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _ensure_repo_graph(name: str, repo: Path) -> dict[str, Any]:
    """Build (or reuse) ``<repo>/.acg/context_graph.json`` for ``name``.

    Returns the loaded graph as a dict. For Python datasets this exercises
    the new LibCST scanner end-to-end; non-Python datasets fall back to the
    pre-existing TS/Java path.
    """

    language = _language_for_dataset(name)
    return scan_context_graph(repo, language=language)


def evaluate_dataset(
    name: str,
    indexers: Sequence[Indexer] | None = None,
) -> dict[str, float]:
    rows = _load_fixture(name)
    repo = _repo_for_dataset(name)
    repo_graph = _ensure_repo_graph(name, repo) if _python_dataset(name) else {}
    start = time.perf_counter()
    recall_total = 0.0
    precision_total = 0.0
    for row in rows:
        truth = set(row["ground_truth_paths"])
        predictions = [
            write.path
            for write in aggregate(
                _task(row), repo, repo_graph, indexers=indexers, top_n=5
            )
        ]
        hits = len(set(predictions) & truth)
        recall_total += hits / len(truth) if truth else 1.0
        precision_total += hits / 5
    wall_s = time.perf_counter() - start
    recall = recall_total / len(rows)
    precision = precision_total / len(rows)
    return {
        "recall@5": recall,
        "precision@5": precision,
        "f1@5": _f1(precision, recall),
        "wall_s": wall_s,
        "language": _language_for_dataset(name),
    }


def evaluate_dataset_secondary(name: str) -> dict[str, float]:
    """Compile a lockfile and probe enforcement on a Python dataset.

    Reports two reviewer-facing signals:

    * ``conflicts_detected`` -- how many predicted-write conflicts the
      compiler surfaced for this task batch.
    * ``blocked_bad_write_rate`` -- given each task's lockfile entry, the
      fraction of *deliberately wrong* paths (sampled from sibling tasks'
      ground truth) the runtime enforcer would block. A value near 1.0 means
      the lockfile is correctly scoped; lower values indicate the planner
      authorized too many globs.
    """

    py_ds = _python_dataset(name)
    if py_ds is None:
        raise ValueError(f"secondary metrics only support Python datasets; got {name}")

    rows = _load_fixture(name)
    repo = _repo_for_dataset(name)
    repo_graph = _ensure_repo_graph(name, repo)

    tasks_input = TasksInput(
        version="1.0",
        tasks=[_task(row) for row in rows],
    )
    lock = compile_lockfile(repo, tasks_input, repo_graph, LLMClient.from_env())

    lock_path = repo / ".acg" / f"{name}_eval_lock.json"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(lock.model_dump_json(indent=2) + "\n")

    truth_by_id = {row["id"]: set(row["ground_truth_paths"]) for row in rows}
    bad_writes_total = 0
    bad_writes_blocked = 0
    for row in rows:
        truth = truth_by_id[row["id"]]
        wrong: set[str] = set()
        for other_id, other_truth in truth_by_id.items():
            if other_id == row["id"]:
                continue
            wrong.update(other_truth - truth)
        for path in sorted(wrong):
            bad_writes_total += 1
            code, _msg = cli_validate(lock_path, row["id"], path)
            if code == EXIT_BLOCKED:
                bad_writes_blocked += 1

    blocked_rate = bad_writes_blocked / bad_writes_total if bad_writes_total else 0.0
    return {
        "conflicts_detected": float(len(lock.conflicts_detected)),
        "blocked_bad_write_rate": blocked_rate,
        "bad_write_attempts": float(bad_writes_total),
    }


def _indexers_with_embeddings() -> Sequence[Indexer] | None:
    """Return the default first-pass indexer list with EmbeddingsIndexer appended.

    Returns ``None`` when ``sentence-transformers`` is not importable so callers
    can fall back to the base indexer set without crashing.
    """

    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return None

    from acg.index.embeddings import EmbeddingsIndexer

    return [
        FrameworkIndexer(),
        PageRankIndexer(),
        BM25Indexer(),
        EmbeddingsIndexer(),
        CochangeIndexer(),
    ]


def _table(results: dict[str, dict[str, float]]) -> str:
    lines = [
        "| dataset | language | recall@5 | precision@5 | F1@5 | wall_s |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for name, metrics in results.items():
        language = str(metrics.get("language") or "-")
        f1 = metrics.get("f1@5", _f1(metrics["precision@5"], metrics["recall@5"]))
        lines.append(
            f"| {name} | {language} | {metrics['recall@5']:.2f} "
            f"| {metrics['precision@5']:.2f} | {f1:.2f} | {metrics['wall_s']:.2f} |"
        )
    if results:
        n = len(results)
        mean_recall = sum(item["recall@5"] for item in results.values()) / n
        mean_precision = sum(item["precision@5"] for item in results.values()) / n
        mean_f1 = sum(
            item.get("f1@5", _f1(item["precision@5"], item["recall@5"]))
            for item in results.values()
        ) / n
        mean_wall = sum(item["wall_s"] for item in results.values()) / n
        lines.append(
            f"| mean | - | {mean_recall:.2f} | {mean_precision:.2f} "
            f"| {mean_f1:.2f} | {mean_wall:.2f} |"
        )
    return "\n".join(lines)


def _delta_table(
    base: dict[str, dict[str, float]],
    embed: dict[str, dict[str, float]],
) -> str:
    lines = [
        "| dataset | recall@5 | precision@5 | wall_s | Δrecall@5 |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    deltas: list[float] = []
    for name in sorted(embed.keys()):
        metrics = embed[name]
        delta = metrics["recall@5"] - base.get(name, {}).get("recall@5", 0.0)
        deltas.append(delta)
        lines.append(
            f"| {name} | {metrics['recall@5']:.2f} | {metrics['precision@5']:.2f} "
            f"| {metrics['wall_s']:.2f} | {delta:+.2f} |"
        )
    if embed:
        mean_recall = sum(item["recall@5"] for item in embed.values()) / len(embed)
        mean_precision = sum(item["precision@5"] for item in embed.values()) / len(embed)
        mean_wall = sum(item["wall_s"] for item in embed.values()) / len(embed)
        mean_delta = sum(deltas) / len(deltas)
        lines.append(
            f"| mean | {mean_recall:.2f} | {mean_precision:.2f} | {mean_wall:.2f} | {mean_delta:+.2f} |"
        )
    return "\n".join(lines)


def _markdown(payload: dict[str, dict[str, dict[str, float]]]) -> str:
    base = payload.get("base") or {}
    embed = payload.get("with_embeddings") or {}
    sections: list[str] = []
    sections.append("## Base (framework + pagerank + bm25 + cochange)")
    sections.append("")
    sections.append(_table(base))
    sections.append("")
    sections.append("## With embeddings (+ EmbeddingsIndexer, ACG_INDEX_EMBEDDINGS=1)")
    sections.append("")
    if embed:
        sections.append(_delta_table(base, embed))
    else:
        sections.append("# embeddings extra not installed — skipped")
    return "\n".join(sections)


DATASETS: tuple[str, ...] = (
    "demo-app",
    "t3-app",
    "express",
    "fastapi-template",
    "click",
)


def main() -> None:
    base_results: dict[str, dict[str, float]] = {}
    embed_results: dict[str, dict[str, float]] = {}
    secondary_results: dict[str, dict[str, float]] = {}
    embed_indexers = _indexers_with_embeddings()
    skip_secondary = os.environ.get("ACG_BENCHMARK_SKIP_SECONDARY") == "1"
    for name in DATASETS:
        base_results[name] = evaluate_dataset(name, indexers=None)
        if embed_indexers is not None:
            embed_results[name] = evaluate_dataset(name, indexers=embed_indexers)
        if not skip_secondary and _python_dataset(name) is not None:
            try:
                secondary_results[name] = evaluate_dataset_secondary(name)
                ds = _python_dataset(name)
                if ds is not None:
                    secondary_results[name]["pinned_ref"] = ds.ref  # type: ignore[assignment]
            except Exception as exc:  # noqa: BLE001
                secondary_results[name] = {"error": str(exc)}  # type: ignore[dict-item]
    payload: dict[str, Any] = {
        "base": base_results,
        "with_embeddings": embed_results,
        "secondary": secondary_results,
        "pinned_refs": {ds.name: ds.ref for ds in PYTHON_DATASETS},
    }
    RESULTS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(_markdown(payload))
    if secondary_results:
        print("\n## Python secondary metrics (lockfile + enforcement)")
        print("")
        print("| dataset | conflicts_detected | blocked_bad_write_rate | attempts | pinned_ref |")
        print("| --- | ---: | ---: | ---: | --- |")
        for name, metrics in secondary_results.items():
            if "error" in metrics:
                print(f"| {name} | error: {metrics['error']} | - | - | - |")
                continue
            print(
                f"| {name} | {metrics['conflicts_detected']:.0f} "
                f"| {metrics['blocked_bad_write_rate']:.2f} "
                f"| {metrics['bad_write_attempts']:.0f} "
                f"| {metrics.get('pinned_ref', '-')} |"
            )
    if embed_indexers is None:
        print("\n# embeddings extra not installed — install with `pip install -e '.[index-vector]'`")


if __name__ == "__main__":
    main()
