#!/usr/bin/env python3
"""Aggregate N-seed Click eval_run_combined.json runs (comparison / comparison_full)."""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from itertools import combinations
from pathlib import Path
from statistics import mean, stdev

DEFAULT_BASE = Path("experiments/real_repos/click/runs_sonnet_v2_n5")
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
    summary: dict[str, float | None | str | dict[str, float]] = {
        "mean": mean(values),
        "stdev": stdev_value,
        "variance_treatment": variance_treatment,
    }
    if variance_treatment == "stochastic" and stdev_value > 0:
        summary["ci95"] = bootstrap_ci(values, rng=rng)
    else:
        summary["ci95"] = None
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


def paired_bootstrap_ci(
    values_a: list[float],
    values_b: list[float],
    n_resamples: int = 10000,
    ci: float = 0.95,
    rng_seed: int = 0,
) -> tuple[float, float, float]:
    """Paired bootstrap CI for mean(values_a - values_b).

    Returns (mean_diff, ci_low, ci_high).
    values_a and values_b must be the same length and aligned (same seed x task ordering).
    """
    if len(values_a) != len(values_b):
        raise ValueError("values_a and values_b must have the same length")
    n = len(values_a)
    diffs = [a - b for a, b in zip(values_a, values_b, strict=True)]
    mean_diff = mean(diffs)
    rng = random.Random(rng_seed)
    boot_means: list[float] = []
    for _ in range(n_resamples):
        indices = [rng.randrange(n) for _ in range(n)]
        boot_means.append(mean([diffs[i] for i in indices]))
    boot_means.sort()
    alpha = 1.0 - ci
    ci_low = percentile(boot_means, alpha / 2)
    ci_high = percentile(boot_means, 1.0 - alpha / 2)
    return mean_diff, ci_low, ci_high


def _paired_cupp_vectors(
    seed_runs: list[dict],
    strategy_a: str,
    strategy_b: str,
) -> tuple[list[float], list[float]]:
    """Extract per-(seed, task) cupp outcome vectors for two strategies.

    Each seed contributes one cupp_rate value (summary metric). Returns
    aligned lists of length n_seeds.
    """
    vec_a: list[float] = []
    vec_b: list[float] = []
    for sr in seed_runs:
        strats = sr["data"].get("strategies", {})
        val_a = strats.get(strategy_a, {}).get("summary_metrics", {}).get("cupp_rate")
        val_b = strats.get(strategy_b, {}).get("summary_metrics", {}).get("cupp_rate")
        if val_a is not None and val_b is not None:
            vec_a.append(float(val_a))
            vec_b.append(float(val_b))
    return vec_a, vec_b


def _paired_tokens_per_cupp_vectors(
    seed_runs: list[dict],
    strategy_a: str,
    strategy_b: str,
) -> tuple[list[float], list[float]]:
    """Extract per-seed tokens_per_cupp vectors; skips seeds where either is None."""
    vec_a: list[float] = []
    vec_b: list[float] = []
    for sr in seed_runs:
        strats = sr["data"].get("strategies", {})
        val_a = strats.get(strategy_a, {}).get("summary_metrics", {}).get("tokens_per_cupp")
        val_b = strats.get(strategy_b, {}).get("summary_metrics", {}).get("tokens_per_cupp")
        if val_a is not None and val_b is not None:
            vec_a.append(float(val_a))
            vec_b.append(float(val_b))
    return vec_a, vec_b


def render_paired_bootstrap_section(seed_runs: list[dict], strategies: list[str]) -> str:
    """Render paired bootstrap CI table for cupp_rate and tokens_per_cupp."""
    pairs = list(combinations(strategies, 2))
    rng_seed = BOOTSTRAP_SEED

    cupp_lines = [
        "## Paired bootstrap CI: cupp_rate (A - B)",
        "",
        "| strategy A | strategy B | mean diff | 95% CI low | 95% CI high |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    tokens_lines = [
        "## Paired bootstrap CI: tokens_per_cupp (A - B)",
        "",
        "| strategy A | strategy B | mean diff | 95% CI low | 95% CI high |",
        "| --- | --- | ---: | ---: | ---: |",
    ]

    has_cupp = False
    has_tokens = False

    for strat_a, strat_b in pairs:
        vec_a, vec_b = _paired_cupp_vectors(seed_runs, strat_a, strat_b)
        if len(vec_a) >= 2 and any(v != 0.0 for v in vec_a + vec_b):
            has_cupp = True
            md, lo, hi = paired_bootstrap_ci(vec_a, vec_b, rng_seed=rng_seed)
            cupp_lines.append(
                f"| `{strat_a}` | `{strat_b}` | {md:.6f} | {lo:.6f} | {hi:.6f} |"
            )

        tp_a, tp_b = _paired_tokens_per_cupp_vectors(seed_runs, strat_a, strat_b)
        if len(tp_a) >= 2:
            has_tokens = True
            md, lo, hi = paired_bootstrap_ci(tp_a, tp_b, rng_seed=rng_seed)
            tokens_lines.append(
                f"| `{strat_a}` | `{strat_b}` | {md:.2f} | {lo:.2f} | {hi:.2f} |"
            )

    sections: list[str] = []
    if has_cupp:
        sections.append("\n".join(cupp_lines))
    if has_tokens:
        sections.append("\n".join(tokens_lines))
    return ("\n\n".join(sections) + "\n") if sections else ""


def render_markdown(aggregate: dict) -> str:
    lines = [
        "# Click Kimi seed aggregate",
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
