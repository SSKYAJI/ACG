#!/usr/bin/env python3
"""Plot Brocoders parallelism sweep wall times; writes ``docs/parallel_subgroup_walltime.png``.

Series: naive parallel (no contract), ACG planned (serialized groups at cap=1 scale
with outer wall), and a sequential baseline reference at cap=1.  Strategy (d)
(parallel-safe ACG subgroups) is not present in the JSON; the figure annotates it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "docs" / "parallelism_sweep_brocoders.json"
OUT = ROOT / "docs" / "parallel_subgroup_walltime.png"


def main() -> int:
    try:
        import matplotlib.pyplot as plt
    except ImportError as e:
        print("matplotlib import failed; install project deps (``pip install -e .``).", file=sys.stderr)
        raise SystemExit(1) from e

    data = json.loads(SRC.read_text(encoding="utf-8"))
    rows = data["rows"]
    naive = sorted(
        {(r["cap_parallelism"], r["wall_s_summary"]) for r in rows if r["strategy"] == "naive_parallel"}
    )
    acg = sorted(
        {(r["cap_parallelism"], r["wall_s_summary"]) for r in rows if r["strategy"] == "acg_planned"}
    )
    xs = [c for c, _ in naive]
    ys_naive = [w for _, w in naive]
    ys_acg = dict(acg)

    fig, ax = plt.subplots(figsize=(9, 5.2))
    ax.plot(xs, ys_naive, marker="o", label="(b) naive parallel (no validation)")
    ax.plot(xs, [ys_acg[c] for c in xs], marker="s", label="(c) ACG planned (outer wall)")
    seq = ys_naive[0] if ys_naive else None
    if seq is not None:
        ax.axhline(seq, color="gray", linestyle="--", linewidth=1.2, label="(a) sequential ref (naive @ cap=1)")
    ax.annotate(
        "(d) ACG parallel-safe subgroups: not in this JSON — future sweep.",
        xy=(0.5, 0.02),
        xycoords="axes fraction",
        ha="center",
        fontsize=9,
        color="#553311",
    )
    ax.set_xlabel("Parallelism cap (worker count)")
    ax.set_ylabel("Wall time (s, wall_s_summary)")
    ax.set_title(data.get("label", "Parallelism sweep"))
    ax.legend(loc="upper right")
    ax.grid(True, alpha=0.3)
    caption = (
        "Brocoders NestJS microservice benchmark (7 tasks). "
        "(a) uses naive wall time at parallelism cap 1 as a sequential baseline. "
        "(b) scales naive fan-out without ACG write contracts. "
        "(c) is ACG-planned runtime with increasing outer parallelism cap; "
        "serialization of conflicting tasks still dominates relative to naive at higher caps. "
        "(d) would require a dedicated run that executes conflict-free parallel groups inside "
        "each serialized wave — data not captured in parallelism_sweep_brocoders.json."
    )
    fig.text(0.5, -0.02, caption, ha="center", va="top", fontsize=8)
    fig.tight_layout()
    fig.subplots_adjust(bottom=0.22)
    fig.savefig(OUT, dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
