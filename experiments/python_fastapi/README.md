# Python FastAPI Benchmark

This benchmark compares ACG and naive within Python. It is not a cross-language comparison. The experiment uses a pinned `tiangolo/full-stack-fastapi-template` checkout plus a five-task, contention-rich prompt set to measure whether ACG reduces prompt-token usage and overlapping writes on a real FastAPI service.

## What this measures

The benchmark targets two effects of coordination on the same Python codebase:

- Scoped-graph prompt reduction, where `acg_planned` sends each task a narrower context than `naive_parallel`.
- Planner-driven overlap reduction, where the compiled execution plan separates tasks that contend on shared files such as `backend/app/api/main.py` and `backend/app/core/config.py`.

The headline artifact is `experiments/python_fastapi/runs_mock/eval_run_combined.json`, generated from the existing greenhouse harness with the mock backend. The prompts are designed to express genuine contention rather than synthetic lockfile tightening.

## One-line reproduction

```bash
make setup-python-fastapi compile-python-fastapi eval-python-fastapi-mock analyze-python-fastapi-mock
```

## Reading the result

The combined run file contains one entry per strategy under `.strategies.naive_parallel` and `.strategies.acg_planned`. The most important paper-facing comparisons are task completion, prompt-token cost, overlapping writes, invalid-write safety metrics, and wall-clock time.

On the current mock harness, `overlapping_write_pairs` is computed from the per-task claimed write sets after the run. It does not discount pairs that ACG serialized into separate execution groups, so the lockfile's `execution_plan.groups` remains the primary evidence that coordination separated contending tasks.

```bash
RUN=experiments/python_fastapi/runs_mock/eval_run_combined.json

jq '.strategies.naive_parallel.summary_metrics.tasks_completed' "$RUN"
jq '.strategies.acg_planned.summary_metrics.tasks_completed' "$RUN"

jq '.strategies.naive_parallel.summary_metrics.tokens_prompt_total' "$RUN"
jq '.strategies.acg_planned.summary_metrics.tokens_prompt_total' "$RUN"

jq '.strategies.naive_parallel.summary_metrics.overlapping_write_pairs' "$RUN"
jq '.strategies.acg_planned.summary_metrics.overlapping_write_pairs' "$RUN"

jq '.strategies.naive_parallel.summary_metrics.out_of_bounds_write_count' "$RUN"
jq '.strategies.acg_planned.summary_metrics.out_of_bounds_write_count' "$RUN"

jq '.strategies.naive_parallel.summary_metrics.blocked_invalid_write_count' "$RUN"
jq '.strategies.acg_planned.summary_metrics.blocked_invalid_write_count' "$RUN"

jq '.strategies.naive_parallel.summary_metrics.wall_time_seconds' "$RUN"
jq '.strategies.acg_planned.summary_metrics.wall_time_seconds' "$RUN"
```

## Pinned commit

The upstream repository is pinned to `13652b51ea0acca7dfe243ac25e2bbdc066f3c4f` for reproducibility. That fixed SHA ensures the path layout, compile-time conflict detection, and resulting benchmark artifacts are anchored to one exact Python FastAPI template revision rather than drifting with upstream changes.

## Phase 2

This README covers only the mock-backend headline artifact. Local backend or live runs are intentionally out of scope here and should land in a follow-up change using the same lockfile and task set.

| Metric | naive_parallel | acg_planned | Δ (acg - naive) |
| --- | ---: | ---: | ---: |
| tasks_completed | _ | _ | _ |
| tokens_prompt_total | _ | _ | _ |
| overlapping_write_pairs | _ | _ | _ |
| out_of_bounds_write_count | _ | _ | _ |
| blocked_invalid_write_count | _ | _ | _ |
| wall_time_seconds | _ | _ | _ |
