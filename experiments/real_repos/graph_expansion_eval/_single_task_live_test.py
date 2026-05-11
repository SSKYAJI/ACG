"""Spot-check driver: live eval on a filtered task list (default: fastify/pr-6653).

Use this to validate predictor + runtime fixes without re-running the full suite.
Artifacts use ``--ablation-name single_fastify_verify`` (see ``sys.argv`` below)
so ``*_strategy_scores.csv`` rows are isolated — when comparing metrics, filter
by ``ablation_name``, ``repo``, and ``task_id``; do not aggregate this CSV with
full multi-task outputs unfiltered.

For paper-level macro numbers, run the main ``evaluate.py`` entrypoint over all
tasks and seeds once API quota allows.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from experiments.real_repos.graph_expansion_eval import evaluate as ev  # noqa: E402

_original_discover = ev.discover_tasks


def _single_fastify_task() -> list[ev.EvalTask]:
    all_tasks = _original_discover()
    filtered = [t for t in all_tasks if t.repo == "fastify" and t.pr_number == "6653"]
    print(f"[driver] filtered to {len(filtered)} task(s): "
          f"{[(t.repo, t.task_id) for t in filtered]}", flush=True)
    return filtered


ev.discover_tasks = _single_fastify_task

sys.argv = [
    "evaluate.py",
    "--mode", "after",
    "--llm-mode", "live",
    "--seeds", "1",
    "--ablation-name", "single_fastify_verify",
]
ev.main()
