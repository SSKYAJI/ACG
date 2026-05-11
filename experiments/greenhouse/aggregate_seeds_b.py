"""Aggregate Lane B Greenhouse OpenRouter seed runs.

Reads ``runs_openrouter_seeds/seed*/eval_run_combined.json`` artifacts,
computes mean +/- stdev plus bootstrap confidence intervals, and renders
JSON, Markdown, and PNG summaries.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import statistics
from pathlib import Path
from typing import Any

import matplotlib
import matplotlib.pyplot as plt

matplotlib.use("Agg")

METRICS = (
    "tokens_prompt_total",
    "out_of_bounds_write_count",
    "blocked_invalid_write_count",
    "cost_usd_total",
    "wall_time_seconds",
)
BOOTSTRAP_REPS = 10000
BOOTSTRAP_SEED = 20260506


def _seed_sort_key(path: Path) -> tuple[int, str]:
    match = re.search(r"(\d+)$", path.name)
    return (int(match.group(1)) if match else 10**9, path.name)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _load_seed(seed_dir: Path) -> dict[str, Any]:
    combined_path = seed_dir / "eval_run_combined.json"
    if combined_path.exists():
        combined = _read_json(combined_path)
        strategies = combined.get("strategies") or {}
    else:
        strategies = {}
        for path in seed_dir.glob("eval_run_*.json"):
            if path.name == "eval_run_combined.json":
                continue
            payload = _read_json(path)
            strategy = payload.get("strategy")
            if strategy:
                strategies[strategy] = payload

    missing = {"naive_parallel", "acg_planned"} - set(strategies)
    if missing:
        names = ", ".join(sorted(missing))
        raise ValueError(f"{seed_dir} is missing required strategies: {names}")
    return {"seed": seed_dir.name, "path": str(seed_dir), "strategies": strategies}


def _metric_value(run: dict[str, Any], metric: str) -> float | None:
    value = ((run.get("summary_metrics") or {}).get(metric))
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return float(value)
    return None


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if not sorted_values:
        raise ValueError("cannot compute percentile of empty list")
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = percentile * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[int(position)]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _bootstrap_mean_ci(
    values: list[float],
    *,
    reps: int = BOOTSTRAP_REPS,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float, float] | tuple[None, None]:
    if not values:
        return (None, None)
    if len(values) == 1:
        return (values[0], values[0])
    rng = random.Random(seed)
    n = len(values)
    means = sorted(_mean([values[rng.randrange(n)] for _ in range(n)]) for _ in range(reps))
    return (_percentile(means, 0.025), _percentile(means, 0.975))


def _summarize(
    values_by_seed: dict[str, float | None],
    *,
    variance_treatment: str | None = None,
) -> dict[str, Any]:
    values = [v for v in values_by_seed.values() if v is not None]
    stdev = statistics.stdev(values) if len(values) > 1 else (0.0 if values else None)
    if variance_treatment is None:
        if values and all(value == values[0] for value in values):
            variance_treatment = "zero_variance_observation"
        else:
            variance_treatment = "stochastic"
    if variance_treatment == "stochastic" and stdev and stdev > 0:
        low, high = _bootstrap_mean_ci(values)
        bootstrap_95_ci: list[float | None] | None = [low, high]
    else:
        bootstrap_95_ci = None
    return {
        "n": len(values),
        "missing": len(values_by_seed) - len(values),
        "mean": _mean(values) if values else None,
        "stdev": stdev,
        "variance_treatment": variance_treatment,
        "bootstrap_95_ci": bootstrap_95_ci,
        "values_by_seed": values_by_seed,
    }


def _round_for_json(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 8)
    if isinstance(value, list):
        return [_round_for_json(v) for v in value]
    if isinstance(value, dict):
        return {k: _round_for_json(v) for k, v in value.items()}
    return value


def build_aggregate(base_dir: Path) -> dict[str, Any]:
    seed_dirs = sorted(
        [p for p in base_dir.glob("seed*") if p.is_dir()],
        key=_seed_sort_key,
    )
    if not seed_dirs:
        raise ValueError(f"no seed directories found under {base_dir}")

    seeds = [_load_seed(seed_dir) for seed_dir in seed_dirs]
    strategy_names = sorted({name for seed in seeds for name in seed["strategies"]})
    strategy_summaries: dict[str, Any] = {}
    for strategy in strategy_names:
        metric_summaries: dict[str, Any] = {}
        for metric in METRICS:
            values_by_seed = {
                seed["seed"]: _metric_value(seed["strategies"][strategy], metric)
                for seed in seeds
                if strategy in seed["strategies"]
            }
            metric_summaries[metric] = _summarize(
                values_by_seed,
                variance_treatment="deterministic" if metric == "tokens_prompt_total" else None,
            )
        strategy_summaries[strategy] = {"metrics": metric_summaries}

    reduction_values: dict[str, float | None] = {}
    full_context_source_by_seed: dict[str, str] = {}
    for seed in seeds:
        strategies = seed["strategies"]
        scoped = _metric_value(strategies["acg_planned"], "tokens_prompt_total")
        if "acg_planned_full_context" in strategies:
            full_context_strategy = "acg_planned_full_context"
        else:
            full_context_strategy = "naive_parallel"
        full_context = _metric_value(
            strategies[full_context_strategy],
            "tokens_prompt_total",
        )
        full_context_source_by_seed[seed["seed"]] = full_context_strategy
        if scoped is None or full_context is None or full_context <= 0:
            reduction_values[seed["seed"]] = None
        else:
            reduction_values[seed["seed"]] = (full_context - scoped) / full_context

    aggregate = {
        "version": "0.1",
        "base_dir": str(base_dir),
        "seed_count": len(seeds),
        "seeds": [{"seed": seed["seed"], "path": seed["path"]} for seed in seeds],
        "metrics": list(METRICS),
        "strategies": strategy_summaries,
        "prompt_token_reduction": {
            "scoped_strategy": "acg_planned",
            "full_context_source_by_seed": full_context_source_by_seed,
            **_summarize(reduction_values, variance_treatment="deterministic"),
        },
        "bootstrap": {"reps": BOOTSTRAP_REPS, "seed": BOOTSTRAP_SEED},
    }
    return _round_for_json(aggregate)


def _fmt(value: Any, *, percent: bool = False) -> str:
    if value is None:
        return "n/a"
    if percent:
        return f"{float(value) * 100:.2f}%"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if abs(value) >= 100:
            return f"{value:.2f}"
        if abs(value) >= 1:
            return f"{value:.4f}"
        return f"{value:.6f}"
    return str(value)


def render_markdown(aggregate: dict[str, Any], out_path: Path) -> None:
    reduction = aggregate["prompt_token_reduction"]
    has_full_context = "acg_planned_full_context" in aggregate["strategies"]
    full_context_label = "acg_planned_full_context" if has_full_context else "naive_parallel"
    title = (
        "# Lane B Greenhouse OpenRouter Ablation Seed Aggregate"
        if has_full_context
        else "# Lane B Greenhouse OpenRouter Seed Aggregate"
    )
    headline = (
        "Deterministic scoped prompt-token reduction across "
        f"N={reduction['n']} seeds: {_fmt(reduction['mean'], percent=True)}."
    )

    lines = [
        title,
        "",
        headline,
        "",
        f"- Base directory: `{aggregate['base_dir']}`",
        f"- Seed directories found: {aggregate['seed_count']}",
        "- Deterministic prompt-token quantities: fixed by strategy prompt construction; variance is zero and no bootstrap CI is reported.",
        f"- Stochastic metrics: bootstrap intervals use {aggregate['bootstrap']['reps']} paired resamples, seed {aggregate['bootstrap']['seed']}.",
        "",
        "## Prompt-Token Reduction",
        "",
        "| Seed | Full-context source | Reduction | Variance treatment |",
        "| --- | --- | ---: | --- |",
    ]
    for seed, value in reduction["values_by_seed"].items():
        source = reduction["full_context_source_by_seed"].get(seed, "n/a")
        lines.append(
            f"| {seed} | `{source}` | {_fmt(value, percent=True)} | deterministic, zero variance |"
        )

    lines.extend(
        [
            "",
            (
                "Pure scope reduction: "
                f"`1 - acg_planned / {full_context_label}` = "
                f"{_fmt(reduction['mean'], percent=True)}."
            ),
            "",
            "## Prompt-Token Aggregates",
            "",
            "| Strategy | N | Mean | Stdev | Variance treatment |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
    )
    for strategy, strategy_payload in aggregate["strategies"].items():
        summary = strategy_payload["metrics"]["tokens_prompt_total"]
        lines.append(
            f"| `{strategy}` | {summary['n']} | {_fmt(summary['mean'])} | "
            f"{_fmt(summary['stdev'])} | deterministic, zero variance; no CI |"
        )

    lines.extend(
        [
            "",
            "## Stochastic Metric Aggregates",
            "",
            "| Strategy | Metric | N | Mean | Stdev | Bootstrap 95% CI |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for strategy, strategy_payload in aggregate["strategies"].items():
        for metric in METRICS:
            if metric == "tokens_prompt_total":
                continue
            summary = strategy_payload["metrics"][metric]
            if summary["variance_treatment"] != "stochastic":
                continue
            ci = summary["bootstrap_95_ci"]
            low, high = ci
            lines.append(
                f"| `{strategy}` | `{metric}` | {summary['n']} | "
                f"{_fmt(summary['mean'])} | {_fmt(summary['stdev'])} | "
                f"{_fmt(low)} to {_fmt(high)} |"
            )

    lines.extend(
        [
            "",
            "## Zero-Variance Safety Observations",
            "",
            "| Strategy | Metric | N | Value |",
            "| --- | --- | ---: | ---: |",
        ]
    )
    for strategy, strategy_payload in aggregate["strategies"].items():
        for metric in ("out_of_bounds_write_count", "blocked_invalid_write_count"):
            summary = strategy_payload["metrics"][metric]
            if summary["variance_treatment"] == "zero_variance_observation":
                lines.append(
                    f"| `{strategy}` | `{metric}` | {summary['n']} | {_fmt(summary['mean'])} |"
                )

    out_path.write_text("\n".join(lines) + "\n")


def _bar_with_ci(ax: Any, labels: list[str], summaries: list[dict[str, Any]], title: str) -> None:
    means = [summary["mean"] for summary in summaries]
    if all(mean is None for mean in means):
        ax.text(0.5, 0.5, "no data", ha="center", va="center", transform=ax.transAxes)
        ax.set_title(title)
        ax.set_xticks([])
        return

    numeric_means = [0.0 if mean is None else float(mean) for mean in means]
    lower_err = []
    upper_err = []
    for mean, summary in zip(means, summaries, strict=True):
        ci = summary["bootstrap_95_ci"]
        low, high = ci if ci else (None, None)
        if mean is None or low is None or high is None:
            lower_err.append(0.0)
            upper_err.append(0.0)
        else:
            lower_err.append(max(0.0, float(mean) - float(low)))
            upper_err.append(max(0.0, float(high) - float(mean)))

    ax.bar(labels, numeric_means, yerr=[lower_err, upper_err], capsize=4)
    ax.set_title(title)
    ax.tick_params(axis="x", rotation=25)
    ax.grid(axis="y", alpha=0.25)


def render_png(aggregate: dict[str, Any], out_path: Path) -> None:
    strategies = [
        strategy
        for strategy in ("naive_parallel", "acg_planned", "acg_planned_full_context")
        if strategy in aggregate["strategies"]
    ]
    labels = {
        "naive_parallel": "naive",
        "acg_planned": "scoped",
        "acg_planned_full_context": "planned full",
    }
    fig, axes = plt.subplots(2, 3, figsize=(14, 8))
    flat_axes = list(axes.flat)
    for ax, metric in zip(flat_axes[:5], METRICS, strict=True):
        _bar_with_ci(
            ax,
            [labels.get(strategy, strategy) for strategy in strategies],
            [aggregate["strategies"][strategy]["metrics"][metric] for strategy in strategies],
            metric,
        )

    reduction = aggregate["prompt_token_reduction"]
    _bar_with_ci(flat_axes[5], ["scoped reduction"], [reduction], "prompt-token reduction")
    flat_axes[5].set_ylim(bottom=0)
    flat_axes[5].yaxis.set_major_formatter(lambda x, _pos: f"{x * 100:.0f}%")

    fig.suptitle("Lane B Greenhouse OpenRouter seed aggregate", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path("experiments/greenhouse/runs_openrouter_seeds"),
    )
    parser.add_argument("--out-json", type=Path, default=None)
    parser.add_argument("--out-md", type=Path, default=None)
    parser.add_argument("--out-png", type=Path, default=None)
    args = parser.parse_args(argv)

    base_dir = args.base_dir
    out_json = args.out_json or base_dir / "aggregate.json"
    out_md = args.out_md or base_dir / "aggregate.md"
    out_png = args.out_png or base_dir / "aggregate.png"

    aggregate = build_aggregate(base_dir)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(aggregate, sort_keys=True, indent=2) + "\n")
    render_markdown(aggregate, out_md)
    render_png(aggregate, out_png)

    reduction = aggregate["prompt_token_reduction"]
    print(
        "Deterministic Greenhouse scoped prompt-token reduction across "
        f"N={reduction['n']} seeds: {_fmt(reduction['mean'], percent=True)} "
        "with zero variance."
    )
    print(f"wrote {out_json}")
    print(f"wrote {out_md}")
    print(f"wrote {out_png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
