#!/usr/bin/env python3
"""Per-PR Markdown: predictor scope vs agent match to human ground truth.

Uses :func:`acg.analyze.analyze_paths` on a single-strategy slice of the eval
artifact (combined files are flattened to one strategy in a temp file).
"""

from __future__ import annotations

import argparse
import json
import tempfile
from fnmatch import fnmatch
from pathlib import Path

from acg.analyze import analyze_paths

DEFAULT_STRATEGY = "acg_planned"
REAL_REPOS = Path("experiments/real_repos")


def _pr_key(pr: str) -> str:
    p = pr.strip()
    return p if p.startswith("pr-") else f"pr-{p}"


def _path_in_allowed(path: str, allowed: list[str]) -> bool:
    for g in allowed:
        if path == g or fnmatch(path, g):
            return True
    return False


def _extract_strategy_payload(eval_path: Path, strategy: str) -> dict:
    data = json.loads(eval_path.read_text())
    if isinstance(data, dict) and "strategies" in data:
        strat = data["strategies"]
        if strategy not in strat:
            raise SystemExit(f"strategy {strategy!r} not in {list(strat)!r}")
        block = dict(strat[strategy])
    else:
        block = dict(data)
    block["_source_path"] = str(eval_path)
    return block


def _analyze_single_strategy(eval_path: Path, strategy: str):
    payload = _extract_strategy_payload(eval_path, strategy)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(payload, tmp)
        tmp_path = Path(tmp.name)
    try:
        return analyze_paths([tmp_path])
    finally:
        tmp_path.unlink(missing_ok=True)


def _load_ground_truth(repo: str, pr_key: str, override: Path | None) -> list[str]:
    if override is not None:
        data = json.loads(override.read_text())
    else:
        gt_file = REAL_REPOS / repo / "runs_kimi_v2" / "ground_truth_score.json"
        if not gt_file.is_file():
            raise SystemExit(f"missing ground truth: {gt_file} (use --ground-truth)")
        data = json.loads(gt_file.read_text())
    tasks = data.get("tasks") or {}
    entry = tasks.get(pr_key)
    if not entry:
        raise SystemExit(f"no tasks[{pr_key!r}] in ground truth JSON")
    files = entry.get("ground_truth_files") or []
    if not isinstance(files, list):
        raise SystemExit("ground_truth_files must be a list")
    return [str(x) for x in files]


def _emit_markdown(
    repo: str,
    pr_key: str,
    strategy: str,
    eval_path: Path,
    gt_files: list[str],
    report,
) -> str:
    lines: list[str] = []
    lines.append(f"## `{repo}` / `{pr_key}` — `{strategy}`")
    lines.append("")
    lines.append(f"_eval artifact_: `{eval_path}`")
    lines.append("")

    payload = _extract_strategy_payload(eval_path, strategy)
    raw_tasks: list = payload.get("tasks") or []
    if not raw_tasks:
        lines.append("_no tasks in strategy payload_")
        return "\n".join(lines)

    gt_set = set(gt_files)
    blocks: list[tuple[str, set[str], set[str], set[str], list[str]]] = []

    for task in raw_tasks:
        tid = str(task.get("task_id", "?"))
        allowed = list(task.get("allowed_write_globs") or [])
        ta = report.tasks.get(tid)
        agent: set[str] = set(ta.actual_files_seen) if ta else set()

        pred_miss = {f for f in gt_set if not _path_in_allowed(f, allowed)}
        scoped_gt = {f for f in gt_set if _path_in_allowed(f, allowed)}
        agent_miss = scoped_gt - agent
        overshoot = agent - gt_set

        blocks.append((tid, pred_miss, scoped_gt, agent_miss, sorted(overshoot)))

    for tid, pred_miss, scoped_gt, agent_miss, overshoot in blocks:
        lines.append(f"### task `{tid}`")
        lines.append("")
        lines.append("### Predictor miss (ground truth not in `allowed_paths`)")
        lines.append("")
        if pred_miss:
            for f in sorted(pred_miss):
                lines.append(f"- `{f}`")
        else:
            lines.append("_none_")
        lines.append("")
        lines.append("### Agent miss within scope (in `allowed_paths`, not proposed)")
        lines.append("")
        if agent_miss:
            for f in sorted(agent_miss):
                lines.append(f"- `{f}`")
        else:
            lines.append("_none_")
        lines.append("")
        lines.append("### Agent overshoot (proposed, not in ground truth)")
        lines.append("")
        if overshoot:
            for f in overshoot:
                lines.append(f"- `{f}`")
        else:
            lines.append("_none_")
        lines.append("")
        if scoped_gt:
            hit = len(
                scoped_gt & (report.tasks[tid].actual_files_seen if tid in report.tasks else set())
            )
            recall = hit / len(scoped_gt)
            lines.append(
                f"**Recall within scope** (human-touched files that fall under "
                f"`allowed_write_globs`, vs agent proposals): **{recall:.2f}** "
                f"({hit}/{len(scoped_gt)} files)."
            )
        else:
            lines.append(
                "**Recall within scope:** _n/a_ — no ground-truth files intersect "
                "`allowed_write_globs` (contract scope excludes the human diff)."
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Predictor vs agent breakdown (stdout Markdown).")
    ap.add_argument("--repo", required=True, help="Subdir under experiments/real_repos/")
    ap.add_argument("--pr", required=True, help="PR number or pr-NNNN")
    ap.add_argument("--eval-run", type=Path, required=True, dest="eval_run")
    ap.add_argument("--strategy", default=DEFAULT_STRATEGY)
    ap.add_argument("--ground-truth", type=Path, default=None, dest="ground_truth")
    args = ap.parse_args()

    pr_key = _pr_key(args.pr)
    eval_path = args.eval_run.expanduser().resolve()
    if not eval_path.is_file():
        raise SystemExit(f"eval-run not found: {eval_path}")

    gt_files = _load_ground_truth(args.repo, pr_key, args.ground_truth)
    report = _analyze_single_strategy(eval_path, args.strategy)
    text = _emit_markdown(args.repo, pr_key, args.strategy, eval_path, gt_files, report)
    print(text, end="")


if __name__ == "__main__":
    main()
