#!/usr/bin/env python3
"""Manifest PR pairs: intersect ``ground_truth_files``, diff hunks → textual vs semantic.

Uses ``checkout_path`` git diffs (same-parent or linear stacked merges). Unsupported
geometry skips that file. Writes ``experiments/PAPER_NUMBERS.md`` unless ``--no-paper-numbers``."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "experiments" / "real_repos" / "manifest.json"
PAPER = ROOT / "experiments" / "PAPER_NUMBERS.md"
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

SECTION = "## Manifest pairwise conflict texture"


@dataclass(frozen=True)
class Task:
    pr_number: int
    parent: str
    merge: str
    gt_files: frozenset[str]
    merged_at: str


def _parse_hunk_ranges(diff: str, *, old: bool) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for line in diff.splitlines():
        m = HUNK.match(line)
        if not m:
            continue
        a_s, a_c, b_s, b_c = m.groups()
        a, b = int(a_s), int(b_s)
        ac = int(a_c) if a_c is not None else (1 if a else 0)
        bc = int(b_c) if b_c is not None else (1 if b else 0)
        if old:
            if ac > 0:
                ranges.append((a, a + ac - 1))
        elif bc > 0:
            ranges.append((b, b + bc - 1))
    return ranges


def _git_diff(repo: Path, a: str, b: str, path: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), "diff", "-U0", "--no-color", a, b, "--", path],
        capture_output=True,
        text=True,
        check=False,
    )
    if r.returncode != 0:
        return ""
    return r.stdout


def _overlap(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> bool:
    for x0, x1 in a:
        for y0, y1 in b:
            if x0 <= y1 and y0 <= x1:
                return True
    return False


def _load_tasks(repo_entry: dict) -> list[Task]:
    out: list[Task] = []
    for t in repo_entry["tasks"]:
        out.append(
            Task(
                pr_number=int(t["pr_number"]),
                parent=str(t["parent_commit_sha"]),
                merge=str(t["merge_commit_sha"]),
                gt_files=frozenset(t["ground_truth_files"]),
                merged_at=str(t["merged_at"]),
            )
        )
    out.sort(key=lambda x: x.merged_at)
    return out


def _ranges_for_pair(
    repo: Path, ta: Task, tb: Task, path: str
) -> tuple[list[tuple[int, int]], list[tuple[int, int]], str] | None:
    """Return (ranges_a, ranges_b) in a common line-number space, or None."""
    if ta.parent == tb.parent:
        ra = _parse_hunk_ranges(_git_diff(repo, ta.parent, ta.merge, path), old=True)
        rb = _parse_hunk_ranges(_git_diff(repo, tb.parent, tb.merge, path), old=True)
        return ra, rb, "same_parent"
    if tb.parent == ta.merge:
        ra = _parse_hunk_ranges(_git_diff(repo, ta.parent, ta.merge, path), old=False)
        rb = _parse_hunk_ranges(_git_diff(repo, tb.parent, tb.merge, path), old=True)
        return ra, rb, "stacked_tb_on_ta"
    if ta.parent == tb.merge:
        rb = _parse_hunk_ranges(_git_diff(repo, tb.parent, tb.merge, path), old=False)
        ra = _parse_hunk_ranges(_git_diff(repo, ta.parent, ta.merge, path), old=True)
        return ra, rb, "stacked_ta_on_tb"
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", type=Path, default=MANIFEST)
    ap.add_argument("--no-paper-numbers", action="store_true", help="Skip PAPER_NUMBERS.md")
    args = ap.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))

    rows: list[tuple[str, int, int, int, int, int]] = []
    totals = [0, 0, 0, 0, 0]  # pairs, files, textual, semantic, skip

    for repo_entry in manifest["repos"]:
        name = repo_entry["short_name"]
        checkout = ROOT / repo_entry["checkout_path"]
        if not (checkout / ".git").exists():
            print(f"[skip] {name}: no git checkout at {checkout}", file=sys.stderr)
            continue
        tasks = _load_tasks(repo_entry)
        if len(tasks) < 2:
            continue
        rpairs = rfiles = text = sem = skip = 0
        for i, ta in enumerate(tasks):
            for tb in tasks[i + 1 :]:
                common = sorted(ta.gt_files & tb.gt_files)
                if not common:
                    continue
                rpairs += 1
                for path in common:
                    rfiles += 1
                    geom = _ranges_for_pair(checkout, ta, tb, path)
                    if geom is None:
                        skip += 1
                        continue
                    ra, rb, _mode = geom
                    if not ra and not rb:
                        text += 1
                    elif _overlap(ra, rb):
                        sem += 1
                    else:
                        text += 1
        rows.append((name, rpairs, rfiles, text, sem, skip))
        totals[0] += rpairs
        totals[1] += rfiles
        totals[2] += text
        totals[3] += sem
        totals[4] += skip

    judged = totals[2] + totals[3]
    pct = (100.0 * totals[2] / judged) if judged else 0.0
    print("| repo | PR pairs w/ file overlap | overlap files | textual | semantic | no-geometry |")
    print("| --- | ---: | ---: | ---: | ---: | ---: |")
    for r in rows:
        print(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} |")
    print(f"| **overall** | {totals[0]} | {totals[1]} | {totals[2]} | {totals[3]} | {totals[4]} |")
    headline = (
        f"Across manifest repos with pairwise overlapping ground-truth files, "
        f"{pct:.1f}% of comparable same-file overlaps are textual (non-overlapping "
        f"hunks); {totals[3]} semantic / {judged} judged."
    )
    print()
    print(headline)

    if not args.no_paper_numbers:
        tail = (
            f"{SECTION}\n\n"
            f"- {headline} "
            f"(PR pairs with shared ground-truth files: {totals[0]}; "
            f"overlap files: {totals[1]}; skipped geometry: {totals[4]}). "
            f"Regenerate: ``python scripts/analysis/conflict_fraction.py``.\n"
        )
        block = "\n\n" + tail
        text = PAPER.read_text(encoding="utf-8")
        if SECTION in text:
            text = re.sub(
                rf"^{re.escape(SECTION)}\s*\n.*?(?=^## |\Z)",
                tail,
                text,
                count=1,
                flags=re.MULTILINE | re.DOTALL,
            )
        else:
            text = text.rstrip() + block
        PAPER.write_text(text, encoding="utf-8")
        print(f"Appended/updated {SECTION} in {PAPER}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
