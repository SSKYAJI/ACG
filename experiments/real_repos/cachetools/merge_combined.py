#!/usr/bin/env python3
"""Rebuild ``eval_run_combined.json`` per seed dir from the per-strategy files.

When a follow-up partial run (``--strategy top_up`` or any sub-set group)
writes a fresh ``eval_run_combined.json`` that only contains the strategies
from that latest invocation, this helper walks each seed directory under a
base dir, reads every ``eval_run_<short>.json`` it finds, and produces a
unified combined sidecar with one entry per strategy. Run after a top-up
sweep so that ``aggregate.py`` (which keys off the combined file) sees
all five strategies.

**Risk:** globbing every ``eval_run_*.json`` in a seed dir without an
allowlist will drop strategies missing from disk. Use ``--strategies`` when
merging after a partial re-run so untouched strategy JSONs are not omitted.

The short-name <-> canonical-strategy mapping mirrors
``experiments.greenhouse.headtohead._short_name``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_BASE = Path("experiments/real_repos/cachetools/runs_sonnet_test_gate_n5")

SHORT_TO_STRATEGY: dict[str, str] = {
    "single_agent": "single_agent",
    "naive": "naive_parallel",
    "naive_parallel_blind": "naive_parallel_blind",
    "acg": "acg_planned",
    "acg_full_context": "acg_planned_full_context",
    "acg_replan": "acg_planned_replan",
    "acg_applied": "acg_planned_applied",
}


def _eval_run_files(seed_dir: Path) -> list[Path]:
    return sorted(p for p in seed_dir.glob("eval_run_*.json") if p.name != "eval_run_combined.json")


def _short_from_filename(path: Path) -> str:
    stem = path.stem
    assert stem.startswith("eval_run_")
    return stem[len("eval_run_"):]


def rebuild_seed(seed_dir: Path, *, strategy_allowlist: set[str] | None = None) -> dict[str, Path]:
    """Merge per-strategy files in ``seed_dir`` into ``eval_run_combined.json``.

    Returns a mapping of canonical-strategy-name -> source-file Path that
    contributed to the rewrite. Raises ``FileNotFoundError`` if the seed dir
    contains no per-strategy files.

    If ``strategy_allowlist`` is set (canonical names like ``acg_planned``),
    only those strategies are read from ``eval_run_*.json`` files; any
    existing ``eval_run_combined.json`` is ignored as a source — callers
    should pass the full allowlist when folding a partial run into a prior
    combined artifact (see module docstring).
    """
    files = _eval_run_files(seed_dir)
    if not files:
        raise FileNotFoundError(f"no eval_run_*.json files under {seed_dir}")

    strategies: dict[str, dict] = {}
    sources: dict[str, Path] = {}
    for path in files:
        short = _short_from_filename(path)
        strategy = SHORT_TO_STRATEGY.get(short)
        if strategy is None:
            print(
                f"[merge_combined] WARN: unknown short-name {short!r} in {path}; skipping",
                file=sys.stderr,
            )
            continue
        if strategy_allowlist is not None and strategy not in strategy_allowlist:
            continue
        with path.open("r", encoding="utf-8") as f:
            strategies[strategy] = json.load(f)
        sources[strategy] = path

    if not strategies:
        raise ValueError(f"no recognisable per-strategy files in {seed_dir}")

    combined = {
        "version": "0.1",
        "strategies": strategies,
    }
    combo_path = seed_dir / "eval_run_combined.json"
    combo_path.write_text(json.dumps(combined, sort_keys=True, indent=2) + "\n")
    return sources


def _parse_strategy_allowlist(raw: str) -> set[str] | None:
    if not raw.strip():
        return None
    out: set[str] = set()
    for part in raw.split(","):
        token = part.strip()
        if not token:
            continue
        mapped = SHORT_TO_STRATEGY.get(token, token)
        out.add(mapped)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=DEFAULT_BASE,
        help="Directory holding seed*/eval_run_<strategy>.json files.",
    )
    parser.add_argument(
        "--seeds",
        type=str,
        default="1,2,3,4,5",
        help="Comma-separated seed indices (default 1,2,3,4,5).",
    )
    parser.add_argument(
        "--strategies",
        type=str,
        default="",
        help=(
            "Comma-separated short or canonical strategy names to fold "
            "(e.g. `single_agent,naive`). Empty: include every eval_run_*.json "
            "found (unsafe after partial runs)."
        ),
    )
    args = parser.parse_args(argv)

    seeds = [int(s.strip()) for s in args.seeds.split(",") if s.strip()]
    base_dir: Path = args.base_dir
    allow = _parse_strategy_allowlist(args.strategies)
    if not base_dir.exists():
        print(f"error: base dir does not exist: {base_dir}", file=sys.stderr)
        return 1

    missing: list[str] = []
    for seed in seeds:
        seed_dir = base_dir / f"seed{seed}"
        if not seed_dir.exists():
            missing.append(str(seed_dir))
            continue
        try:
            sources = rebuild_seed(seed_dir, strategy_allowlist=allow)
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        strategy_names = ", ".join(sorted(sources))
        print(f"[merge_combined] {seed_dir / 'eval_run_combined.json'} <- {strategy_names}")

    if missing:
        print(
            "warning: missing seed dirs: " + ", ".join(missing),
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
