"""Unit tests for ``benchmark.baselines`` predictors."""

from __future__ import annotations

import subprocess
from pathlib import Path

from acg.repo_graph import benchmark_source_paths
from acg.schema import TaskInput, TaskInputHints
from benchmark.baselines import AllFilesTopK, Bm25Only, LastCommitFiles, RandomAtK


def _git_commit_all(repo: Path, message: str = "c") -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "baseline-test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "baseline-test"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, capture_output=True)


def test_benchmark_source_paths_skips_vendor_dirs(tmp_path: Path) -> None:
    (tmp_path / "keep.py").write_text("x = 1\n")
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "skip.py").write_text("y = 2\n")
    paths = benchmark_source_paths(tmp_path)
    rels = {p.relative_to(tmp_path).as_posix() for p in paths}
    assert "keep.py" in rels
    assert "node_modules/skip.py" not in rels


def test_random_at_k_is_deterministic_and_bounded(tmp_path: Path) -> None:
    for i in range(10):
        (tmp_path / f"f{i}.py").write_text("pass\n")
    task = TaskInput(id="t", prompt="hi", hints=None)
    a = RandomAtK(seed=42).predict(task, tmp_path, 5)
    b = RandomAtK(seed=42).predict(task, tmp_path, 5)
    assert a == b
    assert len(a) == 5
    assert len(set(a)) == 5


def test_all_files_top_k_orders_by_line_count(tmp_path: Path) -> None:
    (tmp_path / "small.py").write_text("a\n")
    (tmp_path / "big.py").write_text("x\n" * 30)
    task = TaskInput(id="t", prompt="x", hints=None)
    out = AllFilesTopK().predict(task, tmp_path, 5)
    assert out[0] == "big.py"
    assert "small.py" in out


def test_bm25_only_prefers_lexical_overlap(tmp_path: Path) -> None:
    # BM25Indexer corpus uses path + import/export/docstring hooks, not arbitrary comments.
    (tmp_path / "acguniquefileshard.py").write_text("x = 1\n")
    (tmp_path / "other.py").write_text("y = 2\n" * 30)
    task = TaskInput(id="1", prompt="Refactor the acguniquefileshard feature", hints=None)
    out = Bm25Only().predict(task, tmp_path, 5)
    assert out and out[0] == "acguniquefileshard.py"


def test_bm25_only_uses_hints_touches_in_query(tmp_path: Path) -> None:
    (tmp_path / "via_hints.py").write_text("# acgtouchhint654 body\n")
    (tmp_path / "plain.py").write_text("# other\n")
    task = TaskInput(
        id="2",
        prompt="generic work",
        hints=TaskInputHints(touches=["via_hints.py"]),
    )
    out = Bm25Only().predict(task, tmp_path, 5)
    assert "via_hints.py" in out[:2]


def test_last_commit_files_returns_committed_sources(tmp_path: Path) -> None:
    (tmp_path / "committed.py").write_text("print(1)\n")
    _git_commit_all(tmp_path)
    task = TaskInput(id="t", prompt="x", hints=None)
    out = LastCommitFiles().predict(task, tmp_path, 5)
    assert out == ["committed.py"]
