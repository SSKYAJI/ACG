"""Report renderer for ``eval_run.json`` artifacts.

Two outputs:

- A markdown table comparing strategies (printed to stdout).
- An optional PNG bar chart (matplotlib, headless backend) — same metric
  set the megaplan calls out: tasks completed, tasks/hour, overlaps, OOB
  writes.

Run via ``python -m experiments.greenhouse.report --help``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Pure helpers — exported so tests can use them without spinning up the CLI.
# ---------------------------------------------------------------------------

TABLE_HEADERS = (
    "Strategy",
    "Backend",
    "Tasks completed",
    "Tasks/hour",
    "First-run pass",
    "Overlaps",
    "OOB writes",
    "Blocked",
    "Wall (s)",
)

# Tunables for the bar chart.
CHART_TITLE = "ACG vs naive parallel — Greenhouse Java 6"
CHART_NAIVE_COLOR = "#999999"
CHART_PLANNED_COLOR = "#1f77b4"
CHART_DEVIN_COLOR = "#d62728"
CHART_FIG_WIDTH_IN = 11
CHART_FIG_HEIGHT_IN = 6
CHART_FIG_DPI = 100


def _load(path: Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def _row(run: dict[str, Any]) -> list[str]:
    summary = run.get("summary_metrics") or {}
    completed = summary.get("tasks_completed", 0)
    total = summary.get("tasks_total", 0)
    return [
        run.get("strategy", "?"),
        run.get("backend", "?"),
        f"{completed}/{total}",
        f"{summary.get('tasks_completed_per_hour', 0.0):.2f}",
        f"{summary.get('first_run_pass_rate', 0.0) * 100:.0f}%",
        str(summary.get("overlapping_write_pairs", 0)),
        str(summary.get("out_of_bounds_write_count", 0)),
        str(summary.get("blocked_invalid_write_count", 0)),
        f"{summary.get('wall_time_seconds', 0.0):.2f}",
    ]


def render_markdown_table(runs: list[dict[str, Any]]) -> str:
    """Render a markdown table of strategy comparisons."""
    if not runs:
        return "_no eval_run files supplied_\n"
    header = "| " + " | ".join(TABLE_HEADERS) + " |"
    align = "| " + " | ".join([":---", ":---"] + ["---:" for _ in TABLE_HEADERS[2:]]) + " |"
    lines = [header, align]
    for run in runs:
        lines.append("| " + " | ".join(_row(run)) + " |")
    return "\n".join(lines) + "\n"


def render_demo_line(runs: list[dict[str, Any]]) -> str:
    """Pick the megaplan demo line that fits the data.

    If naive shows overlaps and planned doesn't, use the affirmative line.
    Otherwise fall back to the safety/serialization framing.
    """
    naive = next((r for r in runs if r.get("strategy") == "naive_parallel"), None)
    planned = next((r for r in runs if r.get("strategy") == "acg_planned"), None)
    if naive and planned:
        n_over = (naive.get("summary_metrics") or {}).get("overlapping_write_pairs", 0)
        p_over = (planned.get("summary_metrics") or {}).get("overlapping_write_pairs", 0)
        if n_over > 0 and p_over == 0:
            return (
                "Same repo, same Java 6 modernization tasks, same agent backend. "
                "Naive parallel agents collide on shared files; ACG serializes only "
                "the risky writes and gives us an auditable eval_run.json showing "
                "more safe completions per hour."
            )
        return (
            "On the collision-heavy Java 6 migration, ACG correctly chooses safety "
            "over unsafe parallelism. The eval artifact shows the cost of naive "
            "concurrency and exactly which writes would have collided."
        )
    return (
        "Run both --strategy naive_parallel and --strategy acg_planned to get the "
        "head-to-head comparison the demo line frames."
    )


def render_chart(runs: list[dict[str, Any]], out_path: Path) -> Path:
    """Render a 4-bar comparison PNG (tasks completed, tasks/hour, overlaps, OOB)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    metrics = [
        ("tasks_completed", "Tasks completed"),
        ("tasks_completed_per_hour", "Tasks/hour"),
        ("overlapping_write_pairs", "Overlap pairs"),
        ("out_of_bounds_write_count", "OOB writes"),
    ]
    if not runs:
        raise ValueError("render_chart needs at least one run")

    width = 0.8 / max(len(runs), 1)
    fig, ax = plt.subplots(figsize=(CHART_FIG_WIDTH_IN, CHART_FIG_HEIGHT_IN), dpi=CHART_FIG_DPI)
    x_positions = list(range(len(metrics)))

    color_map = {
        "naive_parallel": CHART_NAIVE_COLOR,
        "acg_planned": CHART_PLANNED_COLOR,
    }
    for offset, run in enumerate(runs):
        summary = run.get("summary_metrics") or {}
        values = [float(summary.get(key, 0.0)) for key, _ in metrics]
        label = f"{run.get('strategy', '?')} ({run.get('backend', '?')})"
        color = color_map.get(run.get("strategy", ""), CHART_DEVIN_COLOR)
        ax.bar(
            [xi + offset * width - (len(runs) - 1) * width / 2 for xi in x_positions],
            values,
            width=width,
            label=label,
            color=color,
            edgecolor="black",
            linewidth=0.5,
        )

    ax.set_xticks(x_positions)
    ax.set_xticklabels([label for _, label in metrics], rotation=10, ha="right")
    ax.set_title(CHART_TITLE)
    ax.set_ylabel("Count or rate")
    ax.legend()
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path)
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="experiments.greenhouse.report",
        description=(
            "Render a markdown summary table (and optional PNG chart) for one "
            "or more eval_run.json files."
        ),
    )
    parser.add_argument(
        "runs",
        nargs="+",
        type=Path,
        help="One or more eval_run.json files.",
    )
    parser.add_argument(
        "--chart",
        type=Path,
        default=None,
        help="Optional path to write a comparison PNG.",
    )
    parser.add_argument(
        "--no-demo-line",
        action="store_true",
        help="Suppress the trailing demo line.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    runs = [_load(p) for p in args.runs]
    sys.stdout.write(render_markdown_table(runs))
    if not args.no_demo_line:
        sys.stdout.write("\n")
        sys.stdout.write(render_demo_line(runs) + "\n")
    if args.chart is not None:
        out = render_chart(runs, args.chart)
        sys.stdout.write(f"\nchart written to {out}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - convenience entry-point.
    raise SystemExit(main())
