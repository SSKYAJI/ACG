"""Measured wall-time + token sweep across parallelism levels N=1..max.

For each N in ``[--n-min, --n-max]`` and each strategy in
``{naive_parallel, acg_planned}``, this script:

1. Caps the in-flight worker concurrency at N (via the new
   ``cap_parallelism`` kwarg threaded through
   :func:`experiments.greenhouse.strategies.run_strategy`).
2. Runs the worker harness against the live LLM backend.
3. Records wall-time, total prompt tokens, total completion tokens,
   tasks-completed, OOB writes, and predicted-overlap pairs.

Output is a single JSON file with one row per (N, strategy) plus a
two-panel PNG chart:

* Left panel: measured wall-time vs N (one line per strategy).
* Right panel: predicted concurrent conflict pairs vs N (one line per
  strategy) — re-uses the analysis in
  :mod:`experiments.microservice.collision_vs_parallelism`.

Honest framing: the wall-time panel is *measured* (live LLM calls);
the conflict-pairs panel is *predicted* (mechanical from the
lockfile). Both are labelled accordingly in the chart legend.

Run::

    ACG_LLM_URL=http://gx10-f2c9:8080/v1 \\
    ACG_LLM_API_KEY=local ACG_LLM_MODEL=gemma \\
        ./.venv/bin/python -m experiments.microservice.parallelism_sweep \\
        --lock experiments/microservice/agent_lock_brocoders.json \\
        --tasks experiments/microservice/tasks_brocoders.json \\
        --repo experiments/microservice/nestjs-boilerplate \\
        --label "Brocoders NestJS" \\
        --n-min 1 --n-max 5 \\
        --backend local \\
        --out-json docs/parallelism_sweep_brocoders.json \\
        --out-png  docs/parallelism_sweep_brocoders.png
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from acg.repo_graph import load_context_graph
from acg.schema import AgentLock, TasksInput

from experiments.greenhouse.strategies import run_strategy
from experiments.microservice.collision_vs_parallelism import (
    acg_concurrent_pairs,
    conflict_pair_set,
    naive_concurrent_pairs,
)


def _load_lockfile(path: Path) -> AgentLock:
    return AgentLock.model_validate_json(path.read_text())


def _load_prompts(tasks_path: Path) -> dict[str, str]:
    ti = TasksInput.model_validate(json.loads(tasks_path.read_text()))
    return {t.id: t.prompt for t in ti.tasks}


def _load_repo_graph(repo_path: Path) -> dict:
    return load_context_graph(repo_path)


def run_one(
    *,
    strategy: str,
    backend: str,
    lock: AgentLock,
    repo_graph: dict,
    prompts: dict[str, str],
    lockfile_path: str,
    cap: int,
) -> dict:
    """Run a single (strategy, N) cell and return a flat row dict."""
    t0 = time.perf_counter()
    run = run_strategy(
        strategy=strategy,
        backend=backend,
        lock=lock,
        repo_graph=repo_graph,
        lockfile_path=lockfile_path,
        prompts_by_task=prompts,
        cap_parallelism=cap,
    )
    elapsed = time.perf_counter() - t0
    summary = run.summary_metrics
    return {
        "strategy": strategy,
        "cap_parallelism": cap,
        "wall_s_summary": float(summary.wall_time_seconds or 0.0),
        "wall_s_outer": elapsed,
        "tokens_prompt_total": int(summary.tokens_prompt_total or 0),
        "tokens_completion_total": int(summary.tokens_completion_total or 0),
        "tasks_completed": int(summary.tasks_completed),
        "tasks_total": int(summary.tasks_total),
        "out_of_bounds_write_count": int(summary.out_of_bounds_write_count),
        "blocked_invalid_write_count": int(summary.blocked_invalid_write_count),
        "overlapping_write_pairs": int(summary.overlapping_write_pairs or 0),
    }


def render_chart(
    rows: list[dict],
    *,
    lock_dict: dict,
    label: str | None,
    out_path: Path,
) -> None:
    """Render a 2-panel chart: measured wall-time × N + predicted conflicts × N."""
    pairs = conflict_pair_set(lock_dict)
    task_order = [t["id"] for t in lock_dict["tasks"]]
    ns = sorted({r["cap_parallelism"] for r in rows})

    naive_wall = [
        next(r["wall_s_summary"] for r in rows if r["strategy"] == "naive_parallel" and r["cap_parallelism"] == n)
        for n in ns
    ]
    acg_wall = [
        next(r["wall_s_summary"] for r in rows if r["strategy"] == "acg_planned" and r["cap_parallelism"] == n)
        for n in ns
    ]
    naive_pred = [naive_concurrent_pairs(task_order, pairs, n) for n in ns]
    acg_pred = [acg_concurrent_pairs(lock_dict, pairs, n) for n in ns]

    fig, (ax_wall, ax_pred) = plt.subplots(1, 2, figsize=(13.0, 4.6))

    ax_wall.plot(ns, naive_wall, marker="o", linewidth=2.5, color="#d62728", label="naive parallel")
    ax_wall.plot(ns, acg_wall, marker="s", linewidth=2.5, color="#2ca02c", label="acg_planned")
    ax_wall.set_xlabel("Parallelism cap N (concurrent agents)")
    ax_wall.set_ylabel("Measured wall time (s)")
    ax_wall.set_xticks(ns)
    ax_wall.set_title("Measured wall time vs N\n(live LLM, lower is faster)")
    ax_wall.grid(alpha=0.25)
    ax_wall.legend(loc="upper right")
    ax_wall.set_ylim(bottom=0)

    ax_pred.plot(ns, naive_pred, marker="o", linewidth=2.5, color="#d62728", label="naive parallel")
    ax_pred.plot(ns, acg_pred, marker="s", linewidth=2.5, color="#2ca02c", label="acg_planned")
    ax_pred.set_xlabel("Parallelism cap N (concurrent agents)")
    ax_pred.set_ylabel("Predicted concurrent conflict pairs")
    ax_pred.set_xticks(ns)
    ax_pred.set_title("Predicted-conflict pair concurrency vs N\n(mechanical from lockfile, lower is safer)")
    ax_pred.grid(alpha=0.25)
    ax_pred.legend(loc="upper left")
    ax_pred.set_ylim(bottom=-0.5)

    suptitle = "Parallelism sweep"
    if label:
        suptitle = f"{suptitle} — {label}"
    fig.suptitle(suptitle, fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--backend", choices=("mock", "local"), default="local")
    parser.add_argument("--label", type=str, default=None)
    parser.add_argument("--n-min", type=int, default=1)
    parser.add_argument("--n-max", type=int, default=5)
    parser.add_argument("--strategy", choices=("naive_parallel", "acg_planned", "both"), default="both")
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-png", type=Path, required=True)
    args = parser.parse_args(argv)

    lock = _load_lockfile(args.lock)
    lock_dict = json.loads(args.lock.read_text())
    prompts = _load_prompts(args.tasks)
    repo_graph = _load_repo_graph(args.repo)

    strategies = (
        ["naive_parallel", "acg_planned"]
        if args.strategy == "both"
        else [args.strategy]
    )

    rows: list[dict] = []
    print(f"\n  parallelism sweep — {args.label or args.lock.stem}")
    print(f"  {'strategy':>16} | {'N':>3} | {'wall_s':>7} | {'prompt':>7} | {'completion':>10} | {'OOB':>3}")
    print(f"  {'-' * 16:>16} | {'-' * 3:>3} | {'-' * 7:>7} | {'-' * 7:>7} | {'-' * 10:>10} | {'-' * 3:>3}")
    for n in range(args.n_min, args.n_max + 1):
        for strategy in strategies:
            row = run_one(
                strategy=strategy,
                backend=args.backend,
                lock=lock,
                repo_graph=repo_graph,
                prompts=prompts,
                lockfile_path=str(args.lock),
                cap=n,
            )
            rows.append(row)
            print(
                f"  {strategy:>16} | {n:>3} | "
                f"{row['wall_s_summary']:>7.2f} | "
                f"{row['tokens_prompt_total']:>7d} | "
                f"{row['tokens_completion_total']:>10d} | "
                f"{row['out_of_bounds_write_count']:>3d}"
            )

    payload = {
        "lockfile": str(args.lock),
        "label": args.label,
        "backend": args.backend,
        "n_min": args.n_min,
        "n_max": args.n_max,
        "rows": rows,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"\n  wrote {args.out_json}")

    render_chart(rows, lock_dict=lock_dict, label=args.label, out_path=args.out_png)
    print(f"  wrote {args.out_png}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
