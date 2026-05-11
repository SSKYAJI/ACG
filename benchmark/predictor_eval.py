"""Evaluate deterministic indexers against small fixture datasets.

Note: This script calls ``acg.index.aggregate`` (framework + PageRank + BM25 +
co-change) directly. It does **not** exercise the full ``predict_writes`` /
``predict_file_scopes`` seed pipeline (static, symbol, test-scaffold, env,
sibling-pattern, auth-role, package, cluster, etc.). If the goal is to measure
recall of the newer seed changes, a separate full-predictor benchmark row is
needed.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from acg.index import aggregate  # noqa: E402
from acg.index.bm25 import BM25Indexer  # noqa: E402
from acg.index.cochange import CochangeIndexer  # noqa: E402
from acg.index.framework import FrameworkIndexer  # noqa: E402
from acg.index.pagerank import PageRankIndexer  # noqa: E402
from acg.index.types import Indexer  # noqa: E402
from acg.schema import TaskInput, TaskInputHints  # noqa: E402

FIXTURE_DIR = ROOT / "benchmark" / "fixtures"
RESULTS_PATH = ROOT / "benchmark" / "results.json"


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True, timeout=90)


def _clone(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "clone", "--depth", "1", url, str(dest)], ROOT)
    return dest


def _load_fixture(name: str) -> list[dict[str, Any]]:
    if name == "express":
        fixture_name = "express-api"
    elif name == "realworld":
        fixture_name = "realworld"
    else:
        fixture_name = name
    return json.loads((FIXTURE_DIR / f"{fixture_name}-tasks.json").read_text())


def _task(row: dict[str, Any]) -> TaskInput:
    hints = row.get("hints") or {}
    return TaskInput(
        id=row["id"],
        prompt=row["prompt"],
        hints=TaskInputHints(**hints) if hints else None,
    )


def _repo_for_dataset(name: str) -> Path:
    if name == "demo-app":
        return ROOT / "demo-app"
    if name == "realworld":
        return ROOT / "experiments" / "realworld" / "checkout"
    cache = ROOT / ".acg" / "benchmark_repos"
    if name == "t3-app":
        return _clone("https://github.com/t3-oss/create-t3-app.git", cache / "create-t3-app")
    if name == "express":
        return _clone("https://github.com/expressjs/express.git", cache / "express")
    raise ValueError(f"unknown dataset {name}")


def evaluate_dataset(
    name: str,
    indexers: Sequence[Indexer] | None = None,
) -> dict[str, float]:
    rows = _load_fixture(name)
    repo = _repo_for_dataset(name)
    start = time.perf_counter()
    recall_total = 0.0
    precision_total = 0.0
    for row in rows:
        truth = set(row["ground_truth_paths"])
        predictions = [
            write.path for write in aggregate(_task(row), repo, {}, indexers=indexers, top_n=5)
        ]
        hits = len(set(predictions) & truth)
        recall_total += hits / len(truth) if truth else 1.0
        precision_total += hits / 5
    wall_s = time.perf_counter() - start
    return {
        "recall@5": recall_total / len(rows),
        "precision@5": precision_total / len(rows),
        "wall_s": wall_s,
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
        "| dataset | recall@5 | precision@5 | wall_s |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, metrics in results.items():
        lines.append(
            f"| {name} | {metrics['recall@5']:.2f} | {metrics['precision@5']:.2f} | {metrics['wall_s']:.2f} |"
        )
    if results:
        mean_recall = sum(item["recall@5"] for item in results.values()) / len(results)
        mean_precision = sum(item["precision@5"] for item in results.values()) / len(results)
        mean_wall = sum(item["wall_s"] for item in results.values()) / len(results)
        lines.append(f"| mean | {mean_recall:.2f} | {mean_precision:.2f} | {mean_wall:.2f} |")
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


def main() -> None:
    base_results: dict[str, dict[str, float]] = {}
    embed_results: dict[str, dict[str, float]] = {}
    embed_indexers = _indexers_with_embeddings()
    for name in ("demo-app", "t3-app", "express", "realworld"):
        base_results[name] = evaluate_dataset(name, indexers=None)
        if embed_indexers is not None:
            embed_results[name] = evaluate_dataset(name, indexers=embed_indexers)
    payload: dict[str, dict[str, dict[str, float]]] = {
        "base": base_results,
        "with_embeddings": embed_results,
    }
    RESULTS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(_markdown(payload))
    if embed_indexers is None:
        print(
            "\n# embeddings extra not installed — install with `pip install -e '.[index-vector]'`"
        )


if __name__ == "__main__":
    main()
