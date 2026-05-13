"""Tests for experiments/real_repos/starlette/aggregate.py."""

from __future__ import annotations

import importlib.util
import json
import random
from pathlib import Path


def _load_aggregate():
    root = Path(__file__).resolve().parents[1]
    path = root / "experiments/real_repos/starlette/aggregate.py"
    spec = importlib.util.spec_from_file_location("starlette_aggregate", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _minimal_combined(seed_offset: float) -> dict:
    """2 strategies × metrics; shift numeric values by seed_offset for two seeds."""
    strategies_block = {}
    for name in ("single_agent", "acg_planned"):
        strategies_block[name] = {
            "strategy": name,
            "summary_metrics": {
                "task_completion_rate": 0.5 + seed_offset,
                "typecheck_pass_count": 1.0 + seed_offset,
                "tests_ran_count": 2.0,
                "first_run_pass_rate": 0.25,
                "tokens_prompt_total": 1000.0 + 10 * seed_offset,
                "cost_usd_total": 0.01 + seed_offset / 100,
                "wall_time_seconds": 30.0 + seed_offset,
            },
        }
    return {"version": "0.1", "strategies": strategies_block}


def test_aggregate_synthetic_two_seeds(tmp_path: Path) -> None:
    mod = _load_aggregate()
    for seed, off in ((1, 0.0), (2, 1.0)):
        d = tmp_path / f"seed{seed}"
        d.mkdir()
        (d / "eval_run_combined.json").write_text(
            json.dumps(_minimal_combined(off)),
            encoding="utf-8",
        )

    rng = random.Random(mod.BOOTSTRAP_SEED)
    strategies = ["single_agent", "acg_planned"]
    agg = mod.aggregate(tmp_path, [1, 2], strategies=strategies, rng=rng)
    assert agg["n"] == 2
    assert set(agg["strategies"]) == set(strategies)
    tpt = agg["strategies"]["single_agent"]["tokens_prompt_total"]
    assert abs(tpt["mean"] - 1005.0) < 1e-6
    assert tpt["variance_treatment"] == "deterministic"


def test_aggregate_dry_run_smoke(tmp_path: Path) -> None:
    mod = _load_aggregate()
    assert mod.dry_run_scan(tmp_path, [1, 2]) == 0
