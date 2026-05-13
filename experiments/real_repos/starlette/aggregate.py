#!/usr/bin/env python3
"""Aggregate N-seed Starlette eval_run_combined.json runs (comparison / comparison_full)."""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from statistics import mean, stdev

DEFAULT_BASE = Path("experiments/real_repos/starlette/runs_sonnet_v2_n5")
STRATEGIES_DEFAULT = [
    "single_agent",
    "naive_parallel",
    "naive_parallel_blind",
    "acg_planned_full_context",
    "acg_planned",
]
METRICS = [
    "task_completion_rate",
    "typecheck_pass_count",
    "tests_ran_count",
    "first_run_pass_rate",
    "tokens_prompt_total",
    "tokens_completion_total",
    "cost_usd_total",
    "wall_time_seconds",
]
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = 20260512


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        raise ValueError("cannot compute percentile of empty values")
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * pct
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return sorted_values[int(pos)]
    weight = pos - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def bootstrap_ci(values: list[float], *, rng: random.Random) -> dict[str, float]:
    if not values:
        raise ValueError("cannot bootstrap empty values")
    sample_size = len(values)
    boot_means = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        sample = [values[rng.randrange(sample_size)] for _ in range(sample_size)]
        boot_means.append(mean(sample))
    boot_means.sort()
    return {
        "low": percentile(boot_means, 0.025),
        "high": percentile(boot_means, 0.975),
    }


def summarize_values(
    values: list[float],
    *,
    rng: random.Random,
    variance_treatment: str = "stochastic",
) -> dict[str, float | None | str | dict[str, float]]:
    stdev_value = stdev(values) if len(values) > 1 else 0.0
    summary: dict[str, float | None] = {
        "mean": mean(values),
        "stdev": stdev_value,
    }
    summary["variance_treatment"] = variance_treatment  # type: ignore[assignment]
    if variance_treatment == "stochastic" and stdev_value > 0:
        summary["ci95"] = bootstrap_ci(values, rng=rng)  # type: ignore[assignment]
    else:
        summary["ci95"] = None  # type: ignore[assignment]
    return summary


def metric_value(run: dict, metric: str) -> float:
    raw = run.get("summary_metrics", {}).get(metric)
    if raw is None:
        return 0.0
    return float(raw)


def variance_treatment(metric: str, values: list[float]) -> str:
    if metric == "tokens_prompt_total":
        return "deterministic"
    if values and all(value == values[0] for value in values):
        return "zero_variance_observation"
    return "stochastic"


def discover_strategies(sample: dict) -> list[str]:
    strategies = sample.get("strategies") or {}
    return [s for s in STRATEGIES_DEFAULT if s in strategies]


def load_seed_runs(base_dir: Path, seeds: list[int], strategies: list[str]) -> list[dict]:
    runs = []
    for seed in seeds:
        path = base_dir / f"seed{seed}" / "eval_run_combined.json"
        if not path.exists():
            raise FileNotFoundError(path)
        data = load_json(path)
        missing = [s for s in strategies if s not in data.get("strategies", {})]
        if missing:
            raise ValueError(f"{path} missing strategies: {', '.join(missing)}")
        runs.append({"seed": seed, "path": str(path), "data": data})
    return runs


def dry_run_scan(base_dir: Path, seeds: list[int]) -> int:
    print(f"# aggregate dry-run base_dir={base_dir}")
    for seed in seeds:
        p = base_dir / f"seed{seed}" / "eval_run_combined.json"
        status = "present" if p.exists() else "MISSING"
        print(f"- seed{seed}: {p} ({status})")
        if p.exists():
            data = load_json(p)
            strat_keys = sorted((data.get("strategies") or {}).keys())
            print(f"    strategies: {', '.join(strat_keys)}")
            print(f"    would aggregate metrics: {', '.join(METRICS)}")
    print("Dry-run complete (no aggregate.json written).")
    return 0


def aggregate(
    base_dir: Path,
    seeds: list[int],
    *,
    strategies: list[str] | None = None,
    rng: random.Random,
) -> dict:
    strategies = list(strategies or STRATEGIES_DEFAULT)
    seed_runs = load_seed_runs(base_dir, seeds, strategies)
    out: dict = {
        "n": len(seed_runs),
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "metrics": METRICS,
        "strategies": {},
        "seeds": [],
    }
    for seed_run in seed_runs:
        seed_entry: dict = {"seed": seed_run["seed"], "path": seed_run["path"], "metrics": {}}
        for strategy in strategies:
            block = seed_run["data"]["strategies"][strategy]
            seed_entry["metrics"][strategy] = {m: metric_value(block, m) for m in METRICS}
        out["seeds"].append(seed_entry)

    for strategy in strategies:
        out["strategies"][strategy] = {}
        for metric in METRICS:
            values = [metric_value(sr["data"]["strategies"][strategy], metric) for sr in seed_runs]
            out["strategies"][strategy][metric] = summarize_values(
                values,
                rng=rng,
                variance_treatment=variance_treatment(metric, values),
            )
    return out


def render_markdown(aggregate: dict) -> str:
    lines = [
        "# Starlette Kimi seed aggregate",
        "",
        f"Seeds: **{aggregate['n']}** (bootstrap N={aggregate['bootstrap_resamples']}).",
        "",
        "| strategy | metric | mean | stdev | 95% CI |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for strategy in aggregate["strategies"]:
        for metric in METRICS:
            item = aggregate["strategies"][strategy][metric]
            ci = item.get("ci95")
            ci_s = f"{ci['low']:.4f}-{ci['high']:.4f}" if isinstance(ci, dict) else "—"
            lines.append(
                f"| `{strategy}` | `{metric}` | {item['mean']:.6f} | {item['stdev']:.6f} | {ci_s} |"
            )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=DEFAULT_BASE,
        help="Directory containing seed*/eval_run_combined.json",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List seed files and strategies only; do not require all seeds.",
    )
    parser.add_argument(
        "--seeds",
        type=str,
        default="1,2,3,4,5",
        help="Comma-separated seed indices (default 1-5).",
    )
    args = parser.parse_args(argv)

    seeds = [int(s.strip()) for s in args.seeds.split(",") if s.strip()]
    base_dir = args.base_dir

    if args.dry_run:
        return dry_run_scan(base_dir, seeds)

    rng = random.Random(BOOTSTRAP_SEED)
    sample_path = base_dir / f"seed{seeds[0]}" / "eval_run_combined.json"
    if not sample_path.exists():
        print(f"error: missing {sample_path}", file=sys.stderr)
        return 1
    sample = load_json(sample_path)
    strategies = discover_strategies(sample)
    if not strategies:
        print("error: no known strategies in sample combined file", file=sys.stderr)
        return 1

    agg = aggregate(base_dir, seeds, strategies=strategies, rng=rng)
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "aggregate.json").write_text(
        json.dumps(agg, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (base_dir / "aggregate.md").write_text(render_markdown(agg), encoding="utf-8")
    print(f"wrote {base_dir / 'aggregate.json'}")
    print(f"wrote {base_dir / 'aggregate.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
