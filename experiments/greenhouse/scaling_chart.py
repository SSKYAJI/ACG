"""Scaling extrapolation chart — when does ACG win on total tokens?

ACG's planned strategy carries a fixed orchestrator overhead (one thinking
pass per run) and saves per-task tokens by scoping each worker's repo
context to its lockfile ``allowed_paths``. We measured both numbers
empirically on two codebases:

* Greenhouse (Java, Spring repo, broad scopes):  ~80 tokens/task saved,
  ~1240 tokens orchestrator overhead.
* demo-app (TypeScript, T3 stack, narrow scopes): ~96 tokens/task saved,
  ~880 tokens orchestrator overhead.

Breakeven N (where planned ties naive on total tokens) is
``orchestrator_overhead / per_task_savings``. Beyond that, planned wins.

This script reads ``eval_run_combined.json`` artifacts for each codebase,
computes the per-task delta and the orchestrator overhead, and produces a
PNG showing total tokens vs N for both strategies side-by-side.

Run::

    ./.venv/bin/python -m experiments.greenhouse.scaling_chart \
        --combined experiments/greenhouse/runs/_local/eval_run_combined.json \
                   experiments/demo-app/runs/eval_run_combined.json \
        --out docs/scaling_breakeven.png
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless rendering — no display required
import matplotlib.pyplot as plt


@dataclass
class CodebasePoint:
    """Per-codebase numbers extracted from one combined eval_run."""

    label: str
    naive_per_task: float
    planned_per_task: float
    orchestrator_overhead: float

    @property
    def per_task_savings(self) -> float:
        return self.naive_per_task - self.planned_per_task

    @property
    def breakeven_n(self) -> float:
        if self.per_task_savings <= 0:
            return float("inf")
        return self.orchestrator_overhead / self.per_task_savings


def load_codebase(path: Path, label: str) -> CodebasePoint:
    """Read a combined eval_run.json and extract per-task / orch numbers."""
    payload = json.loads(path.read_text())
    strategies = payload.get("strategies", {})
    naive = strategies.get("naive_parallel", {}).get("summary_metrics", {}) or {}
    planned = strategies.get("acg_planned", {}).get("summary_metrics", {}) or {}

    naive_total = naive.get("tokens_prompt_total")
    planned_total = planned.get("tokens_prompt_total")
    n_tasks = naive.get("tasks_total") or planned.get("tasks_total") or 0
    orch = planned.get("tokens_orchestrator_overhead") or 0

    if not naive_total or not planned_total or not n_tasks:
        raise ValueError(
            f"{path} is missing prompt-token totals; ensure both strategies "
            "ran and tokens_prompt_total is populated"
        )

    return CodebasePoint(
        label=label,
        naive_per_task=naive_total / n_tasks,
        planned_per_task=planned_total / n_tasks,
        orchestrator_overhead=orch,
    )


def render_chart(points: list[CodebasePoint], out_path: Path, *, max_n: int = 40) -> None:
    """Render a side-by-side breakeven chart for each codebase point."""
    fig, axes = plt.subplots(1, len(points), figsize=(6 * len(points), 4.5), sharey=False)
    if len(points) == 1:
        axes = [axes]
    ns = list(range(1, max_n + 1))
    for ax, point in zip(axes, points, strict=True):
        naive_curve = [point.naive_per_task * n for n in ns]
        planned_curve = [point.orchestrator_overhead + point.planned_per_task * n for n in ns]
        ax.plot(ns, naive_curve, label="naive_parallel", linewidth=2.0)
        ax.plot(ns, planned_curve, label="acg_planned", linewidth=2.0)
        if 0 < point.breakeven_n <= max_n:
            ax.axvline(
                point.breakeven_n,
                color="grey",
                linestyle="--",
                linewidth=1.0,
                label=f"breakeven N={point.breakeven_n:.1f}",
            )
        ax.set_title(
            f"{point.label}\n"
            f"per-task save: {point.per_task_savings:.0f} tok | "
            f"orch overhead: {point.orchestrator_overhead:.0f} tok"
        )
        ax.set_xlabel("Number of tasks (N)")
        ax.set_ylabel("Total prompt tokens")
        ax.legend(loc="upper left")
        ax.grid(alpha=0.25)
    fig.suptitle(
        "ACG planned vs naive: total prompt tokens scale linearly; orchestrator "
        "overhead is one-time fixed",
        fontsize=11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=140, bbox_inches="tight")
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--combined",
        type=Path,
        nargs="+",
        required=True,
        help="One or more eval_run_combined.json files (each yields one panel).",
    )
    parser.add_argument(
        "--label",
        nargs="*",
        default=None,
        help=(
            "Optional human-readable labels per codebase. Length must match "
            "--combined; defaults to parent dir name."
        ),
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("docs/scaling_breakeven.png"),
        help="PNG output path.",
    )
    parser.add_argument(
        "--max-n",
        type=int,
        default=40,
        help="Right edge of N axis (default 40).",
    )
    args = parser.parse_args(argv)

    labels = args.label or [p.parent.name for p in args.combined]
    if len(labels) != len(args.combined):
        parser.error("--label count must match --combined count")

    points = [load_codebase(p, label) for p, label in zip(args.combined, labels, strict=True)]
    render_chart(points, args.out, max_n=args.max_n)

    for p in points:
        print(
            f"  {p.label:>18s}: naive {p.naive_per_task:6.1f} tok/task | "
            f"planned {p.planned_per_task:6.1f} tok/task + {p.orchestrator_overhead:5.0f} orch | "
            f"breakeven N={p.breakeven_n:5.1f}"
        )
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
