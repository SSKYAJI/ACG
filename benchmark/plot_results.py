"""Render a benchmark figure from ``benchmark/results.json``.

Usage::

    ./.venv/bin/python benchmark/plot_results.py \
        --results benchmark/results.json \
        --out docs/benchmark_predictor.png

The figure has two panels:

1. Recall / precision / F1 for every dataset in ``results.json``.
2. Python-only secondary metrics (conflicts + blocked-bad-write rate).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _dataset_order(base: dict[str, dict]) -> list[str]:
    language_rank = {
        "typescript": 0,
        "javascript": 1,
        "python": 2,
    }
    return sorted(
        base.keys(),
        key=lambda name: (
            language_rank.get(str(base[name].get("language") or ""), 99),
            name,
        ),
    )


def render(results_path: Path, out_path: Path) -> None:
    payload = _load(results_path)
    base = payload.get("base") or {}
    secondary = payload.get("secondary") or {}

    if not base:
        raise ValueError(f"{results_path} does not contain a non-empty 'base' section")

    datasets = _dataset_order(base)
    labels = [
        f"{name}\n({base[name].get('language', '-')})"
        for name in datasets
    ]
    recall = [float(base[name].get("recall@5", 0.0)) for name in datasets]
    precision = [float(base[name].get("precision@5", 0.0)) for name in datasets]
    f1 = [float(base[name].get("f1@5", 0.0)) for name in datasets]

    py_names = sorted(
        name for name, metrics in base.items() if metrics.get("language") == "python"
    )
    py_conflicts = [float(secondary.get(name, {}).get("conflicts_detected", 0.0)) for name in py_names]
    py_blocked = [
        float(secondary.get(name, {}).get("blocked_bad_write_rate", 0.0))
        for name in py_names
    ]

    fig, axes = plt.subplots(
        1,
        2,
        figsize=(13, 4.8),
        gridspec_kw={"width_ratios": [2.5, 1.2]},
    )

    ax = axes[0]
    xs = list(range(len(datasets)))
    width = 0.24
    ax.bar([x - width for x in xs], recall, width=width, label="recall@5", color="#2f6fed")
    ax.bar(xs, precision, width=width, label="precision@5", color="#f28e2b")
    ax.bar([x + width for x in xs], f1, width=width, label="F1@5", color="#59a14f")
    ax.set_ylim(0, 1.0)
    ax.set_ylabel("score")
    ax.set_title("Write-set retrieval accuracy by dataset")
    ax.set_xticks(xs)
    ax.set_xticklabels(labels, fontsize=9)
    ax.grid(axis="y", alpha=0.25)
    ax.legend(loc="upper right")

    ax2 = axes[1]
    if py_names:
        py_xs = list(range(len(py_names)))
        width2 = 0.32
        ax2.bar(
            [x - width2 / 2 for x in py_xs],
            py_conflicts,
            width=width2,
            label="conflicts_detected",
            color="#9c755f",
        )
        ax2.set_ylabel("conflict count")
        ax2.set_title("Python secondary metrics")
        ax2.set_xticks(py_xs)
        ax2.set_xticklabels(py_names, fontsize=9)
        ax2.grid(axis="y", alpha=0.25)

        ax2b = ax2.twinx()
        ax2b.bar(
            [x + width2 / 2 for x in py_xs],
            py_blocked,
            width=width2,
            label="blocked_bad_write_rate",
            color="#e15759",
        )
        ax2b.set_ylim(0, 1.0)
        ax2b.set_ylabel("blocked rate")

        handles_a, labels_a = ax2.get_legend_handles_labels()
        handles_b, labels_b = ax2b.get_legend_handles_labels()
        ax2.legend(handles_a + handles_b, labels_a + labels_b, loc="upper left")
    else:
        ax2.text(0.5, 0.5, "No Python secondary metrics found", ha="center", va="center")
        ax2.set_axis_off()

    fig.suptitle("ACG benchmark summary", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results",
        type=Path,
        default=Path("benchmark/results.json"),
        help="Path to predictor_eval results.json.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("docs/benchmark_predictor.png"),
        help="PNG output path.",
    )
    args = parser.parse_args(argv)
    render(args.results, args.out)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
