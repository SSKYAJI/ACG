"""Offline tests for ``benchmark.predictor_eval`` Python additions.

These tests do **not** clone the upstream Python repos; they exercise the
new fixture wiring, dataset configuration, dispatch logic, and secondary
metrics by monkeypatching the network-bound entry points. The full eval
loop with live clones is exercised manually by reviewers running
``python -m benchmark.predictor_eval``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark import predictor_eval

FIXTURE_ROOT = Path(__file__).parent / "fixtures"


def test_python_datasets_have_distinct_kinds() -> None:
    kinds = {ds.kind for ds in predictor_eval.PYTHON_DATASETS}
    assert kinds == {"runtime", "non_runtime"}
    assert len(predictor_eval.PYTHON_DATASETS) == 2


def test_python_datasets_have_unique_names_and_fixtures() -> None:
    names = [ds.name for ds in predictor_eval.PYTHON_DATASETS]
    fixtures = [ds.fixture for ds in predictor_eval.PYTHON_DATASETS]
    assert len(set(names)) == len(names)
    assert len(set(fixtures)) == len(fixtures)


def test_python_dataset_lookup() -> None:
    assert predictor_eval._python_dataset("click") is not None
    assert predictor_eval._python_dataset("fastapi-template") is not None
    assert predictor_eval._python_dataset("demo-app") is None


def test_language_for_dataset() -> None:
    assert predictor_eval._language_for_dataset("click") == "python"
    assert predictor_eval._language_for_dataset("fastapi-template") == "python"
    assert predictor_eval._language_for_dataset("demo-app") == "typescript"
    assert predictor_eval._language_for_dataset("express") == "javascript"


def test_python_fixtures_exist_and_well_formed() -> None:
    for ds in predictor_eval.PYTHON_DATASETS:
        path = predictor_eval.FIXTURE_DIR / ds.fixture
        rows = json.loads(path.read_text())
        assert isinstance(rows, list)
        assert len(rows) >= 4, f"{ds.fixture} should have at least 4 tasks"
        for row in rows:
            assert isinstance(row.get("id"), str) and row["id"]
            assert isinstance(row.get("prompt"), str) and row["prompt"]
            truth = row.get("ground_truth_paths")
            assert isinstance(truth, list) and truth
            for path_value in truth:
                assert isinstance(path_value, str) and path_value


def test_load_fixture_routes_python_dataset_to_python_file() -> None:
    rows = predictor_eval._load_fixture("click")
    assert any(row["id"] == "option-type-validator" for row in rows)


def test_f1_helper() -> None:
    assert predictor_eval._f1(0.0, 0.0) == 0.0
    assert predictor_eval._f1(1.0, 1.0) == 1.0
    assert predictor_eval._f1(0.5, 0.5) == 0.5
    # Standard harmonic mean check.
    f1 = predictor_eval._f1(0.6, 0.4)
    assert 0.47 < f1 < 0.49


def test_evaluate_dataset_uses_python_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch repo + scanner to use the tiny library fixture instead of cloning click."""

    fixture_repo = FIXTURE_ROOT / "tiny_py_lib"
    monkeypatch.setattr(
        predictor_eval, "_repo_for_dataset", lambda name: fixture_repo
    )

    metrics = predictor_eval.evaluate_dataset("click", indexers=None)

    assert set(metrics) >= {"recall@5", "precision@5", "f1@5", "wall_s", "language"}
    assert metrics["language"] == "python"
    assert 0.0 <= metrics["recall@5"] <= 1.0
    assert 0.0 <= metrics["precision@5"] <= 1.0
    assert 0.0 <= metrics["f1@5"] <= 1.0


def test_evaluate_dataset_secondary_runs_against_runtime_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the tiny FastAPI fixture in place of the upstream FastAPI template."""

    fixture_repo = FIXTURE_ROOT / "tiny_py_runtime"
    monkeypatch.setattr(
        predictor_eval, "_repo_for_dataset", lambda name: fixture_repo
    )

    # Point the lockfile output to a tmp area the fixture won't keep around.
    # The eval helper writes to ``<repo>/.acg/<name>_eval_lock.json`` so we
    # patch the LLM client to a deterministic offline stub instead.
    class _StubLLM:
        model = "test"

        def complete(
            self,
            messages: list[dict[str, str]],
            response_format: dict[str, object] | None = None,
        ) -> str:
            return json.dumps({"writes": []})

    monkeypatch.setattr(
        predictor_eval.LLMClient, "from_env", classmethod(lambda cls: _StubLLM())
    )

    metrics = predictor_eval.evaluate_dataset_secondary("fastapi-template")
    assert set(metrics) >= {
        "conflicts_detected",
        "blocked_bad_write_rate",
        "bad_write_attempts",
    }
    assert metrics["bad_write_attempts"] >= 0
    assert 0.0 <= metrics["blocked_bad_write_rate"] <= 1.0


def test_evaluate_dataset_secondary_rejects_non_python() -> None:
    with pytest.raises(ValueError):
        predictor_eval.evaluate_dataset_secondary("demo-app")
