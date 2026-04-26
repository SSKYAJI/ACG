"""Predicted-conflict-pair concurrency vs parallelism N.

Reads an ``agent_lock.json`` and computes, for each parallelism level N
from 1..max_n:

* **Naive parallel**: tasks dispatched in declaration order with N concurrent
  slots and unit duration. With unit task time the schedule reduces to a
  sequence of size-N batches; we count distinct conflict pairs that fall in
  the same batch.
* **ACG planned**: walks ``execution_plan.groups`` in order. Within a group,
  parallelism is bounded by N; groups themselves are serialised. We count
  distinct conflict pairs that fall in the same in-group batch.

Honest framing: this is a *predicted* concurrency count derived from the
lockfile's ``conflicts_detected`` list, not an observed runtime conflict
count. The point is that ACG's execution plan keeps predicted-conflict
pairs serialised regardless of N, while naive's concurrent-pair count grows
monotonically with N.

Run::

    ./.venv/bin/python -m experiments.microservice.collision_vs_parallelism \\
        --lock experiments/microservice/agent_lock_brocoders.json \\
        --label "Brocoders NestJS" \\
        --out-png docs/collision_vs_parallelism.png \\
        --out-json docs/collision_vs_parallelism.json
"""

from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless rendering — no display required
import matplotlib.pyplot as plt


def load_lock(path: Path) -> dict:
    """Load an ``agent_lock.json`` payload."""
    return json.loads(path.read_text())


def conflict_pair_set(lock: dict) -> set[frozenset[str]]:
    """Project ``conflicts_detected`` into a deduped set of unordered pairs."""
    pairs: set[frozenset[str]] = set()
    for entry in lock.get("conflicts_detected", []) or []:
        between = entry.get("between_tasks") or []
        for a, b in combinations(between, 2):
            if a != b:
                pairs.add(frozenset((a, b)))
    return pairs


def _count_in_batches(
    batches: list[list[str]], pairs: set[frozenset[str]]
) -> int:
    """Count conflict pairs whose members all live inside the same batch."""
    total = 0
    for batch in batches:
        members = set(batch)
        for pair in pairs:
            if pair <= members:
                total += 1
    return total


def naive_concurrent_pairs(
    task_order: list[str], pairs: set[frozenset[str]], parallelism: int
) -> int:
    """Naive: split ``task_order`` into FIFO batches of size ``parallelism``."""
    if parallelism <= 0:
        return 0
    batches = [
        task_order[i : i + parallelism]
        for i in range(0, len(task_order), parallelism)
    ]
    return _count_in_batches(batches, pairs)


def acg_concurrent_pairs(
    lock: dict, pairs: set[frozenset[str]], parallelism: int
) -> int:
    """ACG planned: walk execution plan groups, parallelism within each group."""
    if parallelism <= 0:
        return 0
    batches: list[list[str]] = []
    for group in sorted(lock["execution_plan"]["groups"], key=lambda g: g["id"]):
        members = list(group.get("tasks") or [])
        for i in range(0, len(members), parallelism):
            batches.append(members[i : i + parallelism])
    return _count_in_batches(batches, pairs)


def compute_curve(
    lock: dict, max_n: int | None = None
) -> list[dict[str, int]]:
    """Compute the (parallelism, naive, acg) curve up to ``max_n``."""
    task_order = [t["id"] for t in lock["tasks"]]
    pairs = conflict_pair_set(lock)
    cap = max_n or len(task_order)
    rows: list[dict[str, int]] = []
    for n in range(1, cap + 1):
        rows.append(
            {
                "parallelism": n,
                "naive": naive_concurrent_pairs(task_order, pairs, n),
                "acg_planned": acg_concurrent_pairs(lock, pairs, n),
            }
        )
    return rows


def render_chart(
    rows: list[dict[str, int]],
    out_path: Path,
    *,
    label: str | None,
    total_pairs: int,
) -> None:
    """Render a 2-line chart: naive (rising) vs ACG planned (flat at 0)."""
    xs = [r["parallelism"] for r in rows]
    naive_ys = [r["naive"] for r in rows]
    acg_ys = [r["acg_planned"] for r in rows]

    fig, ax = plt.subplots(figsize=(7.0, 4.5))
    ax.plot(
        xs,
        naive_ys,
        marker="o",
        linewidth=2.5,
        color="#d62728",
        label="naive parallel",
    )
    ax.plot(
        xs,
        acg_ys,
        marker="s",
        linewidth=2.5,
        color="#2ca02c",
        label="acg_planned",
    )
    ax.set_xlabel("Parallelism N (concurrent agents)")
    ax.set_ylabel("Predicted-conflict pairs concurrent")
    ax.set_xticks(xs)
    title = "Predicted-conflict pair concurrency vs parallelism"
    if label:
        title = f"{title}\n{label} — {total_pairs} conflict pairs total"
    ax.set_title(title, fontsize=11)
    ax.grid(alpha=0.25)
    ax.legend(loc="upper left")
    ax.set_ylim(bottom=-0.5, top=max(total_pairs, max(naive_ys, default=1)) + 1)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=160, bbox_inches="tight")
    plt.close(fig)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lock",
        type=Path,
        required=True,
        help="Path to an ``agent_lock.json`` produced by ``acg compile``.",
    )
    parser.add_argument(
        "--label",
        type=str,
        default=None,
        help="Codebase label for the chart title (e.g. 'Brocoders NestJS').",
    )
    parser.add_argument(
        "--max-n",
        type=int,
        default=None,
        help="Right edge of parallelism axis (default = number of tasks).",
    )
    parser.add_argument(
        "--out-png",
        type=Path,
        required=True,
        help="PNG output path.",
    )
    parser.add_argument(
        "--out-json",
        type=Path,
        required=True,
        help="JSON output path with the per-N rows.",
    )
    args = parser.parse_args(argv)

    lock = load_lock(args.lock)
    rows = compute_curve(lock, max_n=args.max_n)
    pairs = conflict_pair_set(lock)
    total_tasks = len(lock["tasks"])

    payload = {
        "lockfile": str(args.lock),
        "label": args.label,
        "total_tasks": total_tasks,
        "total_conflict_pairs": len(pairs),
        "by_parallelism": rows,
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(payload, indent=2) + "\n")

    render_chart(
        rows,
        args.out_png,
        label=args.label,
        total_pairs=len(pairs),
    )

    # Pretty-print the table to stdout for quick inspection.
    label = args.label or args.lock.stem
    print(f"\n  {label}: {total_tasks} tasks, {len(pairs)} predicted conflict pairs")
    print(f"  {'N':>3} | {'naive':>6} | {'acg_planned':>11}")
    print(f"  {'-' * 3:>3} | {'-' * 6:>6} | {'-' * 11:>11}")
    for r in rows:
        print(
            f"  {r['parallelism']:>3} | {r['naive']:>6} | {r['acg_planned']:>11}"
        )
    print(f"\n  wrote {args.out_png}")
    print(f"  wrote {args.out_json}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
