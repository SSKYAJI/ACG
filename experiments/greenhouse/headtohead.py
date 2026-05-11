"""Greenhouse head-to-head harness CLI.

Single-purpose entry point that turns a lockfile + a backend into one or
two ``eval_run*.json`` artifacts on disk. Designed to run against the
strict megaplan: ``eval_run.json`` is the only thing reviewers should
need to score the experiment.

Usage:

```bash
# Mock backend, both strategies (writes eval_run_naive.json + eval_run_acg.json)
python -m experiments.greenhouse.headtohead \
  --lock experiments/greenhouse/agent_lock.json \
  --tasks experiments/greenhouse/tasks.json \
  --repo experiments/greenhouse/checkout \
  --backend mock --strategy both \
  --out-dir experiments/greenhouse/runs

# Live local LLM (GX10) for one strategy
python -m experiments.greenhouse.headtohead \
  --lock experiments/greenhouse/agent_lock.json \
  --tasks experiments/greenhouse/tasks.json \
  --repo experiments/greenhouse/checkout \
  --backend local --strategy acg_planned \
  --out experiments/greenhouse/eval_run_acg.json

# Manual Devin sidecar
python -m experiments.greenhouse.headtohead \
  --lock experiments/greenhouse/agent_lock.json \
  --backend devin-manual --strategy naive_parallel \
  --devin-results experiments/greenhouse/runs/devin_naive_raw.json \
  --out experiments/greenhouse/eval_run_devin_naive.json

# Generic applied-diff sidecar (task branches/worktrees/PR heads)
python -m experiments.greenhouse.headtohead \
  --lock experiments/greenhouse/agent_lock.json \
  --backend applied-diff --strategy acg_planned \
  --diff-results experiments/greenhouse/runs/applied_diff_acg_raw.json \
  --out experiments/greenhouse/eval_run_applied_diff_acg.json
```
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Load .env so DEVIN_API_KEY / DEVIN_ORG_ID / ACG_LLM_* vars are available
# without the caller needing to remember to source the file. Optional —
# missing dotenv (or missing .env) is silently ignored.
try:  # pragma: no cover - dotenv is a project dep, but be tolerant.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

from acg.repo_graph import load_context_graph
from acg.schema import AgentLock, TasksInput

from .devin_adapter import (
    DevinAPINotConfigured,
    DevinManualError,
    devin_api_run,
    run_applied_diff_manual,
    run_devin_manual,
)
from .eval_schema import EvalRepo, EvalRun, repo_from_path, suite_name_from_lock, write_eval_run
from .strategies import (
    ACG_PLANNED_APPLIED_STRATEGY,
    ACG_PLANNED_FULL_CONTEXT_STRATEGY,
    ACG_PLANNED_REPLAN_STRATEGY,
    ACG_PLANNED_STRATEGY,
    NAIVE_STRATEGY,
    SINGLE_AGENT_STRATEGY,
    run_strategy,
)

STRATEGY_GROUPS = {
    "both": [NAIVE_STRATEGY, ACG_PLANNED_STRATEGY],
    "ablation": [
        NAIVE_STRATEGY,
        ACG_PLANNED_FULL_CONTEXT_STRATEGY,
        ACG_PLANNED_STRATEGY,
    ],
    "ablation_replan": [
        NAIVE_STRATEGY,
        ACG_PLANNED_FULL_CONTEXT_STRATEGY,
        ACG_PLANNED_STRATEGY,
        ACG_PLANNED_REPLAN_STRATEGY,
    ],
    "comparison": [
        SINGLE_AGENT_STRATEGY,
        NAIVE_STRATEGY,
        ACG_PLANNED_FULL_CONTEXT_STRATEGY,
        ACG_PLANNED_STRATEGY,
    ],
}
VALID_STRATEGIES = (
    SINGLE_AGENT_STRATEGY,
    NAIVE_STRATEGY,
    ACG_PLANNED_STRATEGY,
    ACG_PLANNED_FULL_CONTEXT_STRATEGY,
    ACG_PLANNED_REPLAN_STRATEGY,
    ACG_PLANNED_APPLIED_STRATEGY,
    *STRATEGY_GROUPS.keys(),
)
VALID_BACKENDS = ("mock", "local", "applied-diff", "devin-manual", "devin-api")
EXIT_OK = 0
EXIT_USER_ERROR = 1
EXIT_BACKEND_ERROR = 3


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="experiments.greenhouse.headtohead",
        description="Greenhouse head-to-head eval harness — emits eval_run.json.",
    )
    parser.add_argument(
        "--lock",
        type=Path,
        required=True,
        help="Path to agent_lock.json (e.g. experiments/greenhouse/agent_lock.json).",
    )
    parser.add_argument(
        "--tasks",
        type=Path,
        default=None,
        help=(
            "Optional path to tasks.json. Used to recover the original prompts "
            "(lockfile prompts may be paraphrased). Defaults to no override."
        ),
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=None,
        help=(
            "Path to the Greenhouse checkout. Used to load .acg/context_graph.json. "
            "Required for backends that talk to LLMs (mock/local). Optional for "
            "devin-manual when the sidecar already declares everything."
        ),
    )
    parser.add_argument(
        "--strategy",
        choices=VALID_STRATEGIES,
        default="both",
        help="Which strategy to run.",
    )
    parser.add_argument(
        "--backend",
        choices=VALID_BACKENDS,
        default="mock",
        help="Which backend drives proposals/changes.",
    )
    parser.add_argument(
        "--devin-results",
        type=Path,
        default=None,
        help="Sidecar JSON of Devin session outputs (required for --backend devin-manual).",
    )
    parser.add_argument(
        "--diff-results",
        type=Path,
        default=None,
        help=(
            "Generic sidecar JSON of task branches/worktrees used to derive "
            "git diff changed files (required for --backend applied-diff)."
        ),
    )
    parser.add_argument(
        "--repo-url",
        default=None,
        help=(
            "Repo URL metadata override, and the GitHub URL Devin should clone for "
            "--backend devin-api. Pre-link this repo to your Devin org first."
        ),
    )
    parser.add_argument(
        "--repo-commit",
        default=None,
        help="Repo commit metadata override. Defaults to `git -C <repo> rev-parse HEAD`.",
    )
    parser.add_argument(
        "--suite-name",
        default=None,
        help=(
            "Evaluation suite name to embed in artifacts. Defaults to Greenhouse for "
            "Greenhouse checkouts, otherwise '<repo-name>-eval'."
        ),
    )
    parser.add_argument(
        "--base-branch",
        default="master",
        help="Base branch Devin should branch from and target with PRs.",
    )
    parser.add_argument(
        "--max-parallelism",
        type=int,
        default=5,
        help="Concurrent in-flight Devin sessions (--backend devin-api only).",
    )
    parser.add_argument(
        "--poll-interval-s",
        type=float,
        default=30.0,
        help="Devin session poll cadence (seconds, --backend devin-api only).",
    )
    parser.add_argument(
        "--max-wait-s",
        type=float,
        default=2700.0,
        help="Per-session terminal-state timeout (seconds, --backend devin-api only).",
    )
    parser.add_argument(
        "--max-acu-limit",
        type=int,
        default=None,
        help="Optional ACU guardrail per Devin session (--backend devin-api only).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help=(
            "Single output path for one-strategy runs. Ignored when --strategy both "
            "or --strategy ablation (use --out-dir)."
        ),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help=(
            "Directory to write multiple eval_run files into "
            "(eval_run_naive.json + eval_run_acg.json, plus ablation files when "
            "requested). Required for multi-strategy runs."
        ),
    )
    parser.add_argument(
        "--sequential-wall-time-seconds",
        type=float,
        default=None,
        help=(
            "Optional sequential baseline (seconds). When provided, populates "
            "summary_metrics.successful_parallel_speedup."
        ),
    )
    parser.add_argument(
        "--applied-diff-live",
        action="store_true",
        help=(
            "For acg_planned / acg_planned_replan with mock or local backends, "
            "materialize allowed worker ``content`` proposals as git commits on "
            "per-task branches and set evidence_kind=applied_diff."
        ),
    )
    return parser.parse_args(argv)


def _load_prompts(tasks_path: Path | None) -> dict[str, str]:
    if tasks_path is None:
        return {}
    try:
        ti = TasksInput.model_validate_json(Path(tasks_path).read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"could not load tasks {tasks_path}: {exc}") from exc
    return {t.id: t.prompt for t in ti.tasks}


def _load_lockfile(lock_path: Path) -> AgentLock:
    try:
        return AgentLock.model_validate_json(Path(lock_path).read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"could not load lockfile {lock_path}: {exc}") from exc


def _load_repo_graph(repo_path: Path | None) -> dict:
    if repo_path is None:
        return {}
    return load_context_graph(repo_path)


def _resolve_outputs(
    args: argparse.Namespace,
    strategies: list[str],
) -> dict[str, Path]:
    """Map each strategy ⇒ output Path."""
    if len(strategies) == 1:
        out = args.out
        if out is None:
            if args.out_dir is None:
                raise SystemExit("either --out or --out-dir must be provided")
            out = args.out_dir / f"eval_run_{_short_name(strategies[0])}.json"
        return {strategies[0]: out}

    if args.out_dir is None:
        raise SystemExit("multi-strategy runs require --out-dir")
    return {strat: args.out_dir / f"eval_run_{_short_name(strat)}.json" for strat in strategies}


def _short_name(strategy: str) -> str:
    return {
        SINGLE_AGENT_STRATEGY: "single_agent",
        NAIVE_STRATEGY: "naive",
        ACG_PLANNED_STRATEGY: "acg",
        ACG_PLANNED_FULL_CONTEXT_STRATEGY: "acg_full_context",
        ACG_PLANNED_REPLAN_STRATEGY: "acg_replan",
        ACG_PLANNED_APPLIED_STRATEGY: "acg_planned_applied",
    }.get(strategy, strategy)


def _selected_strategies(selection: str) -> list[str]:
    """Expand a CLI strategy or strategy group into concrete strategy names."""
    if selection in STRATEGY_GROUPS:
        return list(STRATEGY_GROUPS[selection])
    return [selection]


def _run_one(
    *,
    strategy: str,
    backend: str,
    lock: AgentLock,
    repo_graph: dict,
    lockfile_path: str,
    prompts_by_task: dict[str, str],
    sequential_wall_time_seconds: float | None,
    diff_results: Path | None,
    devin_results: Path | None,
    suite_name: str,
    repo: EvalRepo | None,
    devin_api_kwargs: dict | None = None,
    applied_diff_live: bool = False,
) -> EvalRun:
    devin_api_kwargs = devin_api_kwargs or {}
    if backend in ("mock", "local"):
        return run_strategy(
            strategy=strategy,
            backend=backend,
            lock=lock,
            repo_graph=repo_graph,
            lockfile_path=lockfile_path,
            prompts_by_task=prompts_by_task,
            sequential_wall_time_seconds=sequential_wall_time_seconds,
            suite_name=suite_name,
            repo=repo,
            applied_diff_live=applied_diff_live,
        )
    if strategy == ACG_PLANNED_FULL_CONTEXT_STRATEGY:
        raise SystemExit(
            "acg_planned_full_context is only supported by mock/local proposal "
            "backends"
        )
    if backend == "applied-diff":
        results_path = diff_results or devin_results
        if results_path is None:
            raise SystemExit(
                "--backend applied-diff requires --diff-results "
                "(or --devin-results for compatibility)"
            )
        return run_applied_diff_manual(
            strategy=strategy,
            lock=lock,
            lockfile_path=lockfile_path,
            diff_results_path=results_path,
            prompts_by_task=prompts_by_task,
            sequential_wall_time_seconds=sequential_wall_time_seconds,
            suite_name=suite_name,
            repo=repo,
        )
    if backend == "devin-manual":
        if devin_results is None:
            raise SystemExit("--backend devin-manual requires --devin-results")
        return run_devin_manual(
            strategy=strategy,
            lock=lock,
            lockfile_path=lockfile_path,
            devin_results_path=devin_results,
            prompts_by_task=prompts_by_task,
            sequential_wall_time_seconds=sequential_wall_time_seconds,
            suite_name=suite_name,
            repo=repo,
        )
    if backend == "devin-api":
        if not devin_api_kwargs.get("repo_url"):
            raise SystemExit("--backend devin-api requires --repo-url")
        return devin_api_run(
            strategy=strategy,
            lock=lock,
            lockfile_path=lockfile_path,
            sequential_wall_time_seconds=sequential_wall_time_seconds,
            suite_name=suite_name,
            repo=repo,
            **devin_api_kwargs,
        )
    raise SystemExit(f"unsupported backend {backend!r}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    if not args.lock.exists():
        print(f"error: lockfile not found: {args.lock}", file=sys.stderr)
        return EXIT_USER_ERROR
    if args.repo is not None and not args.repo.exists():
        print(
            f"error: repo path does not exist: {args.repo} (run `make setup-greenhouse` first)",
            file=sys.stderr,
        )
        return EXIT_USER_ERROR

    lock = _load_lockfile(args.lock)
    prompts = _load_prompts(args.tasks)
    repo_graph = _load_repo_graph(args.repo)
    suite_name = suite_name_from_lock(lock, args.suite_name)
    repo_meta = repo_from_path(
        args.repo,
        repo_url=args.repo_url,
        repo_commit=args.repo_commit,
    )
    repo_for_run = repo_meta
    if (
        args.backend in {"applied-diff", "devin-manual"}
        and args.repo is None
        and args.repo_url is None
        and args.repo_commit is None
    ):
        # Let manual/applied-diff sidecars supply their own repo_path metadata
        # instead of falling back to Greenhouse defaults.
        repo_for_run = None
    strategies = _selected_strategies(args.strategy)
    outputs = _resolve_outputs(args, strategies)

    if args.applied_diff_live:
        if len(strategies) > 1:
            print(
                "error: --applied-diff-live requires a single concrete strategy "
                "(not a group like both or ablation)",
                file=sys.stderr,
            )
            return EXIT_USER_ERROR
        allowed_applied = {
            ACG_PLANNED_STRATEGY,
            ACG_PLANNED_REPLAN_STRATEGY,
            ACG_PLANNED_APPLIED_STRATEGY,
        }
        if strategies[0] not in allowed_applied:
            print(
                "error: --applied-diff-live is only valid with "
                "acg_planned, acg_planned_replan, or acg_planned_applied",
                file=sys.stderr,
            )
            return EXIT_USER_ERROR

    written: list[Path] = []
    for strategy in strategies:
        try:
            run = _run_one(
                strategy=strategy,
                backend=args.backend,
                lock=lock,
                repo_graph=repo_graph,
                lockfile_path=str(args.lock),
                prompts_by_task=prompts,
                sequential_wall_time_seconds=args.sequential_wall_time_seconds,
                diff_results=args.diff_results,
                devin_results=args.devin_results,
                suite_name=suite_name,
                repo=repo_for_run,
                applied_diff_live=args.applied_diff_live,
                devin_api_kwargs={
                    "repo_url": args.repo_url,
                    "base_branch": args.base_branch,
                    "max_parallelism": args.max_parallelism,
                    "poll_interval_s": args.poll_interval_s,
                    "max_wait_s": args.max_wait_s,
                    "max_acu_limit": args.max_acu_limit,
                },
            )
        except DevinAPINotConfigured as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_BACKEND_ERROR
        except DevinManualError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_BACKEND_ERROR
        out_path = outputs[strategy]
        write_eval_run(run, out_path)
        written.append(out_path)
        summary = run.summary_metrics
        print(
            f"[{strategy}/{args.backend}] wrote {out_path} — "
            f"completed={summary.tasks_completed}/{summary.tasks_total}, "
            f"evidence={run.evidence_kind}, "
            f"overlaps={summary.overlapping_write_pairs}, "
            f"oob={summary.out_of_bounds_write_count}, "
            f"blocked={summary.blocked_invalid_write_count}, "
            f"wall={summary.wall_time_seconds:.3f}s"
        )

    # Convenience: when multiple strategies land in the same dir, also drop a
    # combined sidecar with all summary blocks for quick chart consumption.
    if len(strategies) > 1 and args.out_dir is not None:
        combined = {
            "version": "0.1",
            "strategies": {
                strat: json.loads(Path(outputs[strat]).read_text()) for strat in strategies
            },
        }
        combo_path = args.out_dir / "eval_run_combined.json"
        combo_path.write_text(json.dumps(combined, sort_keys=True, indent=2) + "\n")
        print(f"[combined] wrote {combo_path}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - convenience entry-point.
    raise SystemExit(main())
