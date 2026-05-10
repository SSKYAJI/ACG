"""Benchmark chart renderer.

Reads the JSON metric files produced by :mod:`benchmark.runner` and writes a
single PNG: a 2x2 grid of small bar panels (one metric per panel) telling the
ACG hackathon narrative — fewer collisions, less wall time, lighter per-agent
context, and dollar savings at scale.

Style notes:
    Palette draws on Windsurf brand cues — warm cream paper, a sail-coral
    hero accent against a desaturated sand foil. Light theme, no gradients,
    no blue/purple. Typography prefers Tomato Grotesk / Inter / DM Sans if
    installed, falling back to system sans-serif so CI stays portable.

Layout:
    Each panel has its own y-axis scale (units differ — counts, minutes,
    tokens, dollars), color-coded x-tick labels (Naive=sand, ACG=coral),
    and bars with subtly rounded corners via :class:`FancyBboxPatch`.

Token + cost panels:
    The token figures are derived from
    ``experiments/demo-app/runs/eval_run_combined.json`` (the greenhouse-style
    eval pipeline) since the lightweight ``benchmark/runner.py`` simulator
    does not currently track LLM tokens. Numbers cited in panel 3:

      naive  : ``tokens_prompt_total`` 897 across 4 tasks → 224 tok/task
      planned: ``tokens_prompt_total`` 513 + ``tokens_orchestrator_overhead``
               881 across 4 tasks → 128 tok/task + 881 fixed orch overhead

    Panel 4 projects total cost at ``PROJECTION_N`` tasks using Groq's
    published rate for ``llama-3.3-70b-versatile``: $0.59 per 1M input tokens
    (https://groq.com/pricing). Both numbers are linear extrapolations of
    the per-task figures plus the one-time orchestrator pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import matplotlib

# Force a non-interactive backend so this works in CI / headless dev shells.
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

# ---------- Brand palette (Windsurf-sail, light theme) ----------
BG_COLOR = "#F7F4ED"        # warm cream paper
NAIVE_COLOR = "#B8B0A1"     # muted warm sand (foil)
PLANNED_COLOR = "#FF5C39"   # signal coral (hero)
TEXT_COLOR = "#1B1A18"      # near-black, warm
TEXT_MUTED = "#6E6960"      # axis labels / subtitle / footer
GRID_COLOR = "#E8E2D5"      # faint paper-tone grid

PREFERRED_FONTS = [
    "Tomato Grotesk",
    "Inter",
    "DM Sans",
    "SF Pro Display",
    "Helvetica Neue",
    "Helvetica",
    "Arial",
    "sans-serif",
]

# ---------- Layout ----------
FIG_WIDTH_IN = 12
FIG_HEIGHT_IN = 8.0
FIG_DPI = 160
BAR_WIDTH = 0.55
BAR_RADIUS_FRAC = 0.06  # rounding_size as fraction of BAR_WIDTH

# ---------- Token economics (see module docstring for sources) ----------
NAIVE_TOKENS_PER_TASK = 224
PLANNED_TOKENS_PER_TASK = 128
PLANNED_ORCH_OVERHEAD = 881
PRICE_USD_PER_M_TOKENS = 0.59
PROJECTION_N = 10_000

# ---------- Copy ----------
CHART_TITLE = "Agent coordination tax"
CHART_SUBTITLE = (
    "Naive parallel agents collide on shared files; ACG plans first, "
    "then dispatches scoped slices. Each panel tells one part of the story."
)
CHART_FOOTER = (
    "Sources: .acg/run_*.json (overlap, wall time)  ·  "
    "experiments/demo-app/runs/eval_run_combined.json (tokens)  ·  "
    "Groq llama-3.3-70b-versatile @ $0.59 / 1M input tokens (cost)"
)


def _coerce(value: object) -> float:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _draw_rounded_bar(ax, x_pos: float, height: float, color: str) -> None:
    """Add a single bar with subtly rounded corners."""
    if height <= 0:
        return
    radius = BAR_WIDTH * BAR_RADIUS_FRAC
    patch = FancyBboxPatch(
        (x_pos - BAR_WIDTH / 2, 0.0),
        BAR_WIDTH,
        height,
        boxstyle=f"round,pad=0,rounding_size={radius}",
        mutation_aspect=1.0,
        linewidth=0,
        facecolor=color,
        clip_on=True,
        zorder=3,
    )
    ax.add_patch(patch)


def _fmt_count(v: float) -> str:
    return f"{int(round(v))}" if v == int(v) else f"{v:g}"


def _fmt_minutes(v: float) -> str:
    return f"{int(round(v))} min" if v == int(v) else f"{v:.1f} min"


def _fmt_tokens(v: float) -> str:
    return f"{int(round(v))} tok"


def _fmt_usd(v: float) -> str:
    if v >= 1.0:
        return f"${v:,.2f}"
    if v >= 0.01:
        return f"${v:.3f}"
    return f"${v:.4f}"


def _style_panel(
    ax,
    *,
    title: str,
    unit: str,
    naive_v: float,
    planned_v: float,
    fmt: Callable[[float], str],
) -> None:
    """Render a single small panel with two color-coded bars."""
    ax.set_facecolor(BG_COLOR)

    positions = [0, 1]
    values = [naive_v, planned_v]
    colors = [NAIVE_COLOR, PLANNED_COLOR]
    labels = ["Naive", "ACG"]

    for x_pos, val, color in zip(positions, values, colors):
        _draw_rounded_bar(ax, x_pos, val, color)
        ax.annotate(
            fmt(val),
            (x_pos, val),
            xytext=(0, 8),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=12,
            fontweight="bold",
            color=TEXT_COLOR,
        )

    ymax = max([*values, 1.0])
    ax.set_ylim(0, ymax * 1.32)
    ax.set_xlim(-0.7, 1.7)

    # Color-coded x-tick labels double as the legend.
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, fontsize=11)
    for tick_label, color in zip(ax.get_xticklabels(), colors):
        tick_label.set_color(color)
        tick_label.set_fontweight("bold")

    ax.tick_params(axis="x", length=0, pad=10)
    ax.tick_params(axis="y", length=0, pad=4, labelsize=9, colors=TEXT_MUTED)

    # Faint horizontal grid only.
    ax.yaxis.grid(True, color=GRID_COLOR, linewidth=0.8, zorder=1)
    ax.xaxis.grid(False)
    ax.set_axisbelow(True)

    # Editorial spine treatment: keep only a thin baseline.
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(TEXT_COLOR)
    ax.spines["bottom"].set_linewidth(1.0)

    # Panel headline + tiny unit hint.
    ax.set_title(title, fontsize=13.5, fontweight="bold", color=TEXT_COLOR, loc="left", pad=22)
    ax.text(
        0.0, 1.02, unit,
        transform=ax.transAxes,
        fontsize=9.5, color=TEXT_MUTED, ha="left", va="bottom",
    )


def build_chart(naive_path: Path, planned_path: Path, out_path: Path) -> None:
    """Render the 2x2 panel chart from two metric JSON files.

    Args:
        naive_path: Path to the naive-run metrics JSON.
        planned_path: Path to the ACG-planned-run metrics JSON.
        out_path: Output PNG path. Parent directories are created on demand.
    """
    naive = json.loads(Path(naive_path).read_text())
    planned = json.loads(Path(planned_path).read_text())

    overlap_naive = _coerce(naive.get("overlapping_writes"))
    overlap_planned = _coerce(planned.get("overlapping_writes"))
    wall_naive = _coerce(naive.get("wall_time_minutes"))
    wall_planned = _coerce(planned.get("wall_time_minutes"))

    # Linear projection at PROJECTION_N tasks. Planned carries a one-time
    # orchestrator-overhead pass; naive has no orchestrator.
    naive_tokens_at_n = NAIVE_TOKENS_PER_TASK * PROJECTION_N
    planned_tokens_at_n = PLANNED_ORCH_OVERHEAD + PLANNED_TOKENS_PER_TASK * PROJECTION_N
    naive_cost = naive_tokens_at_n / 1_000_000 * PRICE_USD_PER_M_TOKENS
    planned_cost = planned_tokens_at_n / 1_000_000 * PRICE_USD_PER_M_TOKENS

    rc_overrides = {
        "font.family": "sans-serif",
        "font.sans-serif": PREFERRED_FONTS,
        "axes.edgecolor": TEXT_COLOR,
        "axes.labelcolor": TEXT_COLOR,
    }
    with plt.rc_context(rc_overrides):
        fig, axes = plt.subplots(
            2, 2,
            figsize=(FIG_WIDTH_IN, FIG_HEIGHT_IN),
            dpi=FIG_DPI,
        )
        fig.patch.set_facecolor(BG_COLOR)
        flat = axes.flatten()

        _style_panel(
            flat[0],
            title="Overlapping writes",
            unit="cross-task file collisions  ·  n=4 tasks  ·  lower is better",
            naive_v=overlap_naive,
            planned_v=overlap_planned,
            fmt=_fmt_count,
        )
        _style_panel(
            flat[1],
            title="Wall time",
            unit="minutes  ·  n=4 tasks  ·  lower is better",
            naive_v=wall_naive,
            planned_v=wall_planned,
            fmt=_fmt_minutes,
        )
        _style_panel(
            flat[2],
            title="Prompt tokens / task",
            unit="per worker call  ·  scoped repo slice vs full context",
            naive_v=NAIVE_TOKENS_PER_TASK,
            planned_v=PLANNED_TOKENS_PER_TASK,
            fmt=_fmt_tokens,
        )
        _style_panel(
            flat[3],
            title=f"Projected cost @ N = {PROJECTION_N:,} tasks",
            unit="USD  ·  Groq llama-3.3-70b @ $0.59 / 1M input tokens",
            naive_v=naive_cost,
            planned_v=planned_cost,
            fmt=_fmt_usd,
        )

        # Headline + subtitle, left-aligned editorial style.
        fig.suptitle(
            CHART_TITLE,
            fontsize=22,
            fontweight="bold",
            color=TEXT_COLOR,
            x=0.05,
            y=0.965,
            ha="left",
        )
        fig.text(
            0.05, 0.925,
            CHART_SUBTITLE,
            fontsize=11.5,
            color=TEXT_MUTED,
            ha="left",
        )
        # Footer with full data-source citations.
        fig.text(
            0.05, 0.018,
            CHART_FOOTER,
            fontsize=8.5,
            color=TEXT_MUTED,
            ha="left",
        )

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Reserve top ~14% for title/subtitle, bottom ~5% for footer.
        fig.tight_layout(rect=(0.03, 0.05, 0.98, 0.86), h_pad=3.5, w_pad=3.0)
        fig.savefig(out_path, facecolor=fig.get_facecolor())
        plt.close(fig)
