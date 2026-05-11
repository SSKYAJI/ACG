from __future__ import annotations

import subprocess
from pathlib import Path

from acg.index.cochange import CochangeIndexer, load_model
from acg.schema import TaskInput


def run(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def commit_files(root: Path, message: str, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    run(root, "add", *files)
    run(root, "commit", "-m", message)


def fixture_repo(root: Path) -> Path:
    run(root, "init")
    for i in range(3):
        commit_files(
            root, f"pair {i}", {"src/auth.py": f"auth {i}", "tests/test_auth.py": f"test {i}"}
        )
    commit_files(root, "billing", {"src/billing.py": "billing", "tests/test_billing.py": "test"})
    return root


def test_load_model_counts_commit_cooccurrences(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    model = load_model(repo)

    assert model is not None
    assert model.cochange["src/auth.py"]["tests/test_auth.py"] == 3


def test_cochange_expands_seed_paths(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    indexer = CochangeIndexer(seed_paths=["src/auth.py"])

    writes = indexer.predict(TaskInput(id="auth", prompt="Refactor auth"), repo, {})
    assert writes[0].path == "tests/test_auth.py"
    assert writes[0].confidence == 0.8


def test_cochange_threshold_filters_weak_pairs(tmp_path: Path) -> None:
    repo = fixture_repo(tmp_path)
    indexer = CochangeIndexer(seed_paths=["src/billing.py"])

    assert indexer.predict(TaskInput(id="billing", prompt="Refactor billing"), repo, {}) == []
