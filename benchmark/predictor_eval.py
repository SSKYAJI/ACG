"""Evaluate deterministic indexers against small fixture datasets."""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from acg.index import aggregate
from acg.schema import TaskInput, TaskInputHints

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
    if name == "demo-app":
        return ROOT / "demo-app"
    cache = ROOT / ".acg" / "benchmark_repos"
    if name == "t3-app":
        return _clone("https://github.com/t3-oss/create-t3-app.git", cache / "create-t3-app")
    if name == "express":
        return _clone("https://github.com/expressjs/express.git", cache / "express")
    raise ValueError(f"unknown dataset {name}")


def evaluate_dataset(name: str) -> dict[str, float]:
    rows = _load_fixture(name)
    repo = _repo_for_dataset(name)
    start = time.perf_counter()
    recall_total = 0.0
    precision_total = 0.0
    for row in rows:
        truth = set(row["ground_truth_paths"])
        predictions = [write.path for write in aggregate(_task(row), repo, {}, top_n=5)]
        hits = len(set(predictions) & truth)
        recall_total += hits / len(truth) if truth else 1.0
        precision_total += hits / 5
    wall_s = time.perf_counter() - start
    return {
        "recall@5": recall_total / len(rows),
        "precision@5": precision_total / len(rows),
        "wall_s": wall_s,
    }


def _markdown(results: dict[str, dict[str, float]]) -> str:
    lines = [
        "| dataset | recall@5 | precision@5 | wall_s |",
        "| --- | ---: | ---: | ---: |",
    ]
    for name, metrics in results.items():
        lines.append(
            f"| {name} | {metrics['recall@5']:.2f} | {metrics['precision@5']:.2f} | {metrics['wall_s']:.2f} |"
        )
    mean_recall = sum(item["recall@5"] for item in results.values()) / len(results)
    mean_precision = sum(item["precision@5"] for item in results.values()) / len(results)
    mean_wall = sum(item["wall_s"] for item in results.values()) / len(results)
    lines.append(f"| mean | {mean_recall:.2f} | {mean_precision:.2f} | {mean_wall:.2f} |")
    return "\n".join(lines)


def main() -> None:
    results = {
        name: evaluate_dataset(name)
        for name in ("demo-app", "t3-app", "express")
    }
    RESULTS_PATH.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    print(_markdown(results))


if __name__ == "__main__":
    main()
