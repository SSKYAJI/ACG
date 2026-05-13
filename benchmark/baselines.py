"""Trivial file-ranking baselines for ``benchmark.predictor_eval``."""

from __future__ import annotations

import random
import subprocess
from pathlib import Path

from acg.index.bm25 import BM25Indexer
from acg.repo_graph import benchmark_source_paths
from acg.schema import TaskInput

_SOURCE_SUFFIXES = frozenset({".py", ".js", ".ts", ".tsx", ".java"})


class RandomAtK:
    name = "random_at_k"

    def __init__(self, seed: int = 0) -> None:
        self._rng = random.Random(seed)

    def predict(self, task: TaskInput, repo: Path, top_k: int = 5) -> list[str]:
        del task
        candidates = [p.relative_to(repo).as_posix() for p in benchmark_source_paths(repo)]
        if not candidates:
            return []
        k = min(top_k, len(candidates))
        return self._rng.sample(candidates, k)


class AllFilesTopK:
    name = "all_files_top_k"

    def predict(self, task: TaskInput, repo: Path, top_k: int = 5) -> list[str]:
        del task
        scored: list[tuple[int, str]] = []
        for path in benchmark_source_paths(repo):
            rel = path.relative_to(repo).as_posix()
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                n = 0
            else:
                n = text.count("\n") + 1 if text else 0
            scored.append((n, rel))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [rel for _, rel in scored[:top_k]]


class Bm25Only:
    name = "bm25_only"

    def predict(self, task: TaskInput, repo: Path, top_k: int = 5) -> list[str]:
        writes = BM25Indexer(top_n=top_k).predict(task, repo, {})
        return [w.path for w in writes]


class LastCommitFiles:
    name = "last_commit_files"

    def predict(self, task: TaskInput, repo: Path, top_k: int = 5) -> list[str]:
        del task
        proc = subprocess.run(
            ["git", "log", "--name-only", "--diff-filter=AMR", "-n", "1", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if proc.returncode != 0:
            return []
        out: list[str] = []
        seen: set[str] = set()
        for raw in proc.stdout.splitlines():
            line = raw.strip()
            if not line:
                continue
            candidate = repo / line
            try:
                candidate.relative_to(repo)
            except ValueError:
                continue
            if not candidate.is_file() or candidate.suffix not in _SOURCE_SUFFIXES:
                continue
            rel = candidate.relative_to(repo).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            out.append(rel)
            if len(out) >= top_k:
                break
        return out[:top_k]
