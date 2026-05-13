"""Tests for the paired-bootstrap CI helper in the Starlette aggregator."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

AGG_PATH = (
    Path(__file__).resolve().parents[1]
    / "experiments"
    / "real_repos"
    / "starlette"
    / "aggregate.py"
)


def _load_aggregate_module():
    spec = importlib.util.spec_from_file_location("starlette_aggregate", AGG_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def agg():
    return _load_aggregate_module()


def test_paired_bootstrap_ci_zero_diff_when_inputs_equal(agg) -> None:
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    mean_diff, low, high = agg.paired_bootstrap_ci(
        values, values, n_resamples=2000, rng_seed=42
    )
    assert mean_diff == pytest.approx(0.0)
    assert low == pytest.approx(0.0)
    assert high == pytest.approx(0.0)


def test_paired_bootstrap_ci_deterministic_with_seed(agg) -> None:
    a = [10.0, 12.0, 9.0, 11.0, 13.0]
    b = [8.0, 11.0, 9.0, 10.0, 10.0]
    md1, lo1, hi1 = agg.paired_bootstrap_ci(a, b, n_resamples=5000, rng_seed=123)
    md2, lo2, hi2 = agg.paired_bootstrap_ci(a, b, n_resamples=5000, rng_seed=123)
    assert (md1, lo1, hi1) == (md2, lo2, hi2)
    assert md1 == pytest.approx(sum(x - y for x, y in zip(a, b, strict=True)) / len(a))
    assert lo1 <= md1 <= hi1


def test_paired_bootstrap_ci_rejects_mismatched_lengths(agg) -> None:
    with pytest.raises(ValueError):
        agg.paired_bootstrap_ci([1.0, 2.0], [1.0, 2.0, 3.0])
