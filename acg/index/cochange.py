"""ROSE-style co-change association indexer over git history."""

from __future__ import annotations

import pickle
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acg.schema import PredictedWrite, TaskInput

from .util import cache_dir

MIN_COCHANGE_COUNT = 3


@dataclass
class CochangeModel:
    head: str
    cochange: dict[str, Counter[str]]
    commit_counts: Counter[str]


def _git(repo_root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout


def _head(repo_root: Path) -> str | None:
    try:
        return _git(repo_root, ["rev-parse", "HEAD"]).strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _parse_commits(raw: str) -> list[set[str]]:
    commits: list[set[str]] = []
    current: set[str] = set()
    for line in raw.splitlines():
        line = line.strip()
        if line == "COMMIT":
            if current:
                commits.append(current)
            current = set()
        elif line:
            current.add(line)
    if current:
        commits.append(current)
    return commits


def _build(repo_root: Path, head: str) -> CochangeModel:
    try:
        raw = _git(repo_root, ["log", "--name-only", "--pretty=format:COMMIT", "--no-merges"])
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        raw = ""
    cochange: dict[str, Counter[str]] = defaultdict(Counter)
    commit_counts: Counter[str] = Counter()
    for files in _parse_commits(raw):
        for file in files:
            commit_counts[file] += 1
            for other in files - {file}:
                cochange[file][other] += 1
    return CochangeModel(head=head, cochange=dict(cochange), commit_counts=commit_counts)


def load_model(repo_root: Path | None) -> CochangeModel | None:
    if repo_root is None or not (repo_root / ".git").exists():
        return None
    head = _head(repo_root)
    if head is None:
        return None
    directory = cache_dir(repo_root)
    path = directory / "cochange.pickle" if directory is not None else None
    if path is not None and path.exists():
        try:
            with path.open("rb") as fh:
                cached = pickle.load(fh)
            if isinstance(cached, CochangeModel) and cached.head == head:
                return cached
        except (OSError, pickle.PickleError, EOFError):
            pass
    model = _build(repo_root, head)
    if path is not None:
        try:
            with path.open("wb") as fh:
                pickle.dump(model, fh)
        except OSError:
            pass
    return model


class CochangeIndexer:
    """Expand known seed files using support/confidence from commit history."""

    name = "cochange"

    def __init__(self, seed_paths: list[str] | None = None, top_n: int = 8) -> None:
        self.seed_paths = seed_paths or []
        self.top_n = top_n

    def predict(
        self,
        task: TaskInput,
        repo_root: Path | None,
        repo_graph: dict[str, Any],
    ) -> list[PredictedWrite]:
        del task, repo_graph
        model = load_model(repo_root)
        if model is None or not self.seed_paths:
            return []
        scores: dict[str, tuple[int, float, str]] = {}
        seeds = set(self.seed_paths)
        for seed in seeds:
            seed_commits = model.commit_counts.get(seed, 0)
            if seed_commits == 0:
                continue
            for path, count in model.cochange.get(seed, Counter()).items():
                if path in seeds or count < MIN_COCHANGE_COUNT:
                    continue
                confidence = count / seed_commits
                existing = scores.get(path)
                if existing is None or confidence > existing[1]:
                    scores[path] = (count, confidence, seed)
        ranked = sorted(scores.items(), key=lambda item: (-item[1][1], -item[1][0], item[0]))[
            : self.top_n
        ]
        return [
            PredictedWrite(
                path=path,
                confidence=min(0.8, confidence),
                reason=f"ROSE co-change: {path} changed with seed {seed} in {count} commits.",
            )
            for path, (count, confidence, seed) in ranked
        ]


def predict(
    task: TaskInput,
    repo_root: Path | None,
    repo_graph: dict[str, Any],
) -> list[PredictedWrite]:
    return CochangeIndexer().predict(task, repo_root, repo_graph)
