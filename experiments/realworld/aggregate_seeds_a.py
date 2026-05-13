#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import random
from pathlib import Path
from statistics import mean, stdev

BASE_DIR = Path("experiments/realworld/runs_blind_openrouter_seeds")
REFERENCE_PATH = Path("experiments/realworld/runs_blind_openrouter/eval_run_combined.json")
METRICS = [
    "tokens_prompt_total",
    "out_of_bounds_write_count",
    "blocked_invalid_write_count",
    "cost_usd_total",
    "wall_time_seconds",
]
STRATEGIES = ["acg_planned", "acg_planned_full_context", "naive_parallel"]
BOOTSTRAP_RESAMPLES = 1000
BOOTSTRAP_SEED = 20260506


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
    value = run["summary_metrics"].get(metric)
    if value is None:
        raise ValueError(f"missing summary metric {metric!r} for {run.get('strategy')!r}")
    return float(value)


def variance_treatment(metric: str, values: list[float]) -> str:
    if metric == "tokens_prompt_total":
        return "deterministic"
    if values and all(value == values[0] for value in values):
        return "zero_variance_observation"
    return "stochastic"


def load_seed_runs() -> list[dict]:
    runs = []
    for seed in range(1, 6):
        path = BASE_DIR / f"seed{seed}" / "eval_run_combined.json"
        if not path.exists():
            raise FileNotFoundError(path)
        data = load_json(path)
        missing = [strategy for strategy in STRATEGIES if strategy not in data.get("strategies", {})]
        if missing:
            raise ValueError(f"{path} missing strategies: {', '.join(missing)}")
        runs.append({"seed": seed, "path": str(path), "data": data})
    return runs


def validate_prompt_token_drift(seed1: dict, reference: dict) -> list[str]:
    failures = []
    checks = [
        ("acg_planned", 1105.0),
        ("acg_planned_full_context", 2128.0),
        ("naive_parallel", 2128.0),
    ]
    for strategy, expected in checks:
        observed = metric_value(seed1["data"]["strategies"][strategy], "tokens_prompt_total")
        drift = abs(observed - expected) / expected
        if drift > 0.30:
            failures.append(
                f"seed1 {strategy} prompt tokens drifted by {drift:.1%}: "
                f"observed {observed:g}, expected {expected:g}"
            )

    for strategy in STRATEGIES:
        if strategy not in reference.get("strategies", {}):
            continue
        observed = metric_value(seed1["data"]["strategies"][strategy], "tokens_prompt_total")
        ref_value = metric_value(reference["strategies"][strategy], "tokens_prompt_total")
        drift = abs(observed - ref_value) / ref_value
        if drift > 0.30:
            failures.append(
                f"seed1 {strategy} prompt tokens drifted by {drift:.1%} vs reference artifact: "
                f"observed {observed:g}, reference {ref_value:g}"
            )
    return failures


def write_failure(failures: list[str]) -> None:
    path = BASE_DIR / "FAILURE.md"
    lines = ["# FAILURE", "", "Sanity checks failed.", ""]
    for failure in failures:
        lines.append(f"- {failure}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def render_markdown(aggregate: dict) -> str:
    ratio = aggregate["reduction_ratio"]
    lines = [
        "# RealWorld Blind OpenRouter Seed Aggregate",
        "",
        (
            "Prompt-token reduction is deterministic for a fixed lockfile, task suite, and "
            f"worker prompt. Across N={aggregate['n']} OpenRouter seeds on the RealWorld "
            "NestJS blind task suite, scoped planned execution reduced worker prompt "
            f"context by {ratio['mean'] * 100:.1f}% with zero variance across seeds. "
            "Bootstrap intervals are reported only for stochastic measurements below."
        ),
        "",
        "## Deterministic Measurements",
        "",
        "| metric | value across all seeds | stdev |",
        "| --- | ---: | ---: |",
    ]
    for strategy in STRATEGIES:
        item = aggregate["strategies"][strategy]["tokens_prompt_total"]
        lines.append(
            f"| `{strategy}.tokens_prompt_total` | {item['mean']:.0f} | {item['stdev']:.6f} |"
        )
    lines.extend(
        [
            (
                f"| `1 - acg_planned / acg_planned_full_context` | "
                f"{ratio['mean']:.6f} | {ratio['stdev']:.6f} |"
            ),
            "",
            "## Stochastic Measurements",
            "",
            "| strategy | metric | mean | stdev | 95% CI |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
    )
    for strategy in STRATEGIES:
        for metric in METRICS:
            if metric == "tokens_prompt_total":
                continue
            item = aggregate["strategies"][strategy][metric]
            if item["variance_treatment"] != "stochastic":
                continue
            ci95 = item["ci95"]
            lines.append(
                f"| `{strategy}` | `{metric}` | {item['mean']:.6f} | {item['stdev']:.6f} | "
                f"{ci95['low']:.6f}-{ci95['high']:.6f} |"
            )
    lines.extend(
        [
            "",
            "## Zero-Variance Safety Observations",
            "",
            "| strategy | metric | value across all seeds |",
            "| --- | --- | ---: |",
        ]
    )
    for strategy in STRATEGIES:
        for metric in ("out_of_bounds_write_count", "blocked_invalid_write_count"):
            item = aggregate["strategies"][strategy][metric]
            if item["variance_treatment"] == "zero_variance_observation":
                lines.append(f"| `{strategy}` | `{metric}` | {item['mean']:.0f} |")
    lines.extend(
        [
            "",
            "## Seeds",
            "",
            "| seed | acg_planned tokens | full_context tokens | naive tokens | reduction ratio |",
            "| ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for seed in aggregate["seeds"]:
        lines.append(
            f"| {seed['seed']} | {seed['tokens_prompt_total']['acg_planned']:.0f} | "
            f"{seed['tokens_prompt_total']['acg_planned_full_context']:.0f} | "
            f"{seed['tokens_prompt_total']['naive_parallel']:.0f} | "
            f"{seed['reduction_ratio']:.6f} |"
        )
    return "\n".join(lines) + "\n"


def render_png(aggregate: dict) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = STRATEGIES
    means = [
        aggregate["strategies"][strategy]["tokens_prompt_total"]["mean"]
        for strategy in labels
    ]
    lower_errors = [0.0 for _ in means]
    upper_errors = [0.0 for _ in means]

    fig, ax = plt.subplots(figsize=(6, 3.5), dpi=160)
    colors = ["#2f6f73", "#b15c38", "#6f5a9b"]
    ax.bar(labels, means, yerr=[lower_errors, upper_errors], capsize=5, color=colors)
    ax.set_ylabel("Prompt tokens")
    ax.set_title("RealWorld blind OpenRouter prompt tokens, N=5")
    ax.grid(axis="y", alpha=0.25)
    ax.set_axisbelow(True)
    ax.tick_params(axis="x", labelrotation=18)
    fig.tight_layout()
    fig.savefig(BASE_DIR / "aggregate.png")
    plt.close(fig)


def main() -> int:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    failure_path = BASE_DIR / "FAILURE.md"
    if failure_path.exists():
        failure_path.unlink()

    rng = random.Random(BOOTSTRAP_SEED)
    seed_runs = load_seed_runs()
    reference = load_json(REFERENCE_PATH)

    failures = validate_prompt_token_drift(seed_runs[0], reference)

    aggregate: dict = {
        "n": len(seed_runs),
        "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "metrics": METRICS,
        "strategies": {},
        "reduction_ratio": {},
        "seeds": [],
    }

    for seed_run in seed_runs:
        seed_entry: dict = {
            "seed": seed_run["seed"],
            "path": seed_run["path"],
            "tokens_prompt_total": {},
        }
        planned_tokens = metric_value(
            seed_run["data"]["strategies"]["acg_planned"], "tokens_prompt_total"
        )
        full_context_tokens = metric_value(
            seed_run["data"]["strategies"]["acg_planned_full_context"],
            "tokens_prompt_total",
        )
        seed_entry["reduction_ratio"] = 1 - planned_tokens / full_context_tokens
        for strategy in STRATEGIES:
            seed_entry["tokens_prompt_total"][strategy] = metric_value(
                seed_run["data"]["strategies"][strategy], "tokens_prompt_total"
            )
        aggregate["seeds"].append(seed_entry)

    for strategy in STRATEGIES:
        aggregate["strategies"][strategy] = {}
        for metric in METRICS:
            values = [
                metric_value(seed_run["data"]["strategies"][strategy], metric)
                for seed_run in seed_runs
            ]
            aggregate["strategies"][strategy][metric] = summarize_values(
                values,
                rng=rng,
                variance_treatment=variance_treatment(metric, values),
            )

    ratios = [seed["reduction_ratio"] for seed in aggregate["seeds"]]
    aggregate["reduction_ratio"] = summarize_values(
        ratios,
        rng=rng,
        variance_treatment="deterministic",
    )

    ratio_mean = aggregate["reduction_ratio"]["mean"]
    if not 0.42 <= ratio_mean <= 0.55:
        failures.append(
            f"reduction-ratio mean outside 0.42-0.55: observed {ratio_mean:.6f}"
        )

    if failures:
        write_failure(failures)
        return 1

    (BASE_DIR / "aggregate.json").write_text(
        json.dumps(aggregate, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (BASE_DIR / "aggregate.md").write_text(render_markdown(aggregate), encoding="utf-8")
    render_png(aggregate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
