"""Benchmark chart renderer.

Reads the JSON metric files produced by :mod:`benchmark.runner` (one for the
naive run, one for the ACG-planned run) and writes a single PNG. The chart is
the headline visual judges see in the README and demo video.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

# Force a non-interactive backend so this works in CI / headless dev shells.
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

CHART_TITLE = "Agent coordination tax — naive vs ACG-planned"
NAIVE_COLOR = "#999999"
PLANNED_COLOR = "#1f77b4"
FIG_WIDTH_IN = 12
FIG_HEIGHT_IN = 6
FIG_DPI = 100


METRICS = [
    ("overlapping_writes", "Overlapping writes"),
    ("blocked_bad_writes", "Blocked bad writes"),
    ("manual_merge_steps", "Manual merge steps"),
    ("tests_passing_first_run", "Tests pass 1st run"),
    ("wall_time_minutes", "Wall time (min)"),
]


def _coerce(value: object) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def build_chart(naive_path: Path, planned_path: Path, out_path: Path) -> None:
    """Render the bar chart from two metric JSON files.

    Args:
        naive_path: Path to the naive-run metrics JSON.
        planned_path: Path to the ACG-planned-run metrics JSON.
        out_path: Output PNG path. Parent directories are created on demand.
    """
    naive = json.loads(Path(naive_path).read_text())
    planned = json.loads(Path(planned_path).read_text())

    labels = [label for _, label in METRICS]
    naive_values = [_coerce(naive.get(key)) for key, _ in METRICS]
    planned_values = [_coerce(planned.get(key)) for key, _ in METRICS]

    fig, ax = plt.subplots(figsize=(FIG_WIDTH_IN, FIG_HEIGHT_IN), dpi=FIG_DPI)
    x = list(range(len(labels)))
    bar_width = 0.4
    bars_naive = ax.bar(
        [xi - bar_width / 2 for xi in x],
        naive_values,
        width=bar_width,
        label="Naive parallel",
        color=NAIVE_COLOR,
    )
    bars_planned = ax.bar(
        [xi + bar_width / 2 for xi in x],
        planned_values,
        width=bar_width,
        label="ACG-planned",
        color=PLANNED_COLOR,
    )

    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=10, ha="right")
    ax.set_title(CHART_TITLE)
    ax.set_ylabel("Count or minutes")
    ax.legend()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    for bar in list(bars_naive) + list(bars_planned):
        height = bar.get_height()
        ax.annotate(
            f"{height:g}",
            xy=(bar.get_x() + bar.get_width() / 2, height),
            xytext=(0, 4),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=9,
        )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
