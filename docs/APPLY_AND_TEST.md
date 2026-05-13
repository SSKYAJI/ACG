# Apply-and-test (Greenhouse `eval_run`)

This document describes how the **experiments.greenhouse** harness materializes worker output on disk, runs **TypeScript** `tsc --noEmit`, and records verdicts in `eval_run*.json`. It matches the fields emitted today (`eval_schema.SummaryMetrics` and per-task `EvalTask`).

## Backends (`--backend`)

| Backend | Proposals | Apply + typecheck | Notes |
| --- | --- | --- | --- |
| `mock` | Lockfile-echo LLM | **Yes** when `--repo` points at an existing checkout (same code path as `--applied-diff-live`); **no** disk apply when `--repo` is omitted (fast CI / token baselines). | No live LLM. |
| `local` | Real sub-agent LLM | Only if you pass `--applied-diff-live` | Uses `ACG_*` / `ACG_MOCK_LLM`. |
| `applied-diff` | N/A (sidecar) | Reads pre-applied git state from `--diff-results` | `evidence_kind` is applied-diff style; typecheck may be skipped depending on sidecar. |
| `devin-manual` | N/A | Sidecar + manual extraction | `--devin-results`. |
| `devin-api` | N/A | Live Devin sessions | Requires Devin credentials and `--repo-url`. |

## Strategies (`--strategy`)

Concrete runners (groups like `both` / `ablation` expand to these):

| Strategy | Meaning |
| --- | --- |
| `single_agent` | One suite-level call; no lockfile execution plan. |
| `naive_parallel` | All tasks at once; full-repo context; no serialize-by-`parallel_group`. |
| `naive_parallel_blind` | Same as naive but prompts omit lockfile hints. |
| `acg_planned` | Serialized by lockfile groups; **scoped** repo graph per task. |
| `acg_planned_full_context` | Same schedule as planned; **full** repo graph (ablation). |
| `acg_planned_replan` | Planned + runtime auto-replan when allowed. |
| `acg_planned_applied` | Planned + git apply + typecheck (always applied path). |

## `summary_metrics` keys (paper-facing)

These keys appear on every `eval_run*.json` under `summary_metrics` (values vary by backend):

- **Completion**: `tasks_total`, `tasks_completed`, `task_completion_rate`, `proposal_completion_rate`, `first_run_pass_rate`, `wall_time_seconds`, `tasks_completed_per_hour`
- **Apply / honesty**: `patch_na_count`, `applied_changed_files_total`, `merge_conflicts`, `replan_rescued_count`
- **Scope / collisions**: `out_of_bounds_write_count`, `overlapping_write_pairs`, `blocked_invalid_write_count`, `oob_files_per_task_mean`, `integration_burden` (nested object with `overlapping_files`, `overlapping_task_pairs`, `unique_changed_files`, etc.)
- **Typecheck**: `typecheck_pass_count`, `typecheck_fail_count`, `typecheck_skipped_count`
- **Tests** (when wired): `tests_ran_count`, `tested_tasks_completed`, `tested_completion_rate`
- **Cost / tokens** (live backends): `cost_usd_total`, `tokens_prompt_total`, `tokens_completion_total`, `tokens_planner_total`, …

Per-task mirrors live under each `tasks[]` entry in `metrics`: `typecheck_ran`, `typecheck_exit_code`, `typecheck_diagnostic_count`, `typecheck_wall_seconds`, `patch_applies`, plus `test` (`ran`, `exit_code`, `passed`, …) when populated.

## `PATCH_NA`

When a worker returns files but **`apply_envelope`** cannot parse or apply a valid OpenAI-style `*** Begin Patch` … `*** End Patch` block for a proposal, the apply step sets **`patch_na`** and a **`patch_na_reason`**. That task does **not** count as successfully applied; summaries increment **`patch_na_count`** and the task is scored as failed for completion (same spirit as “no parseable diff”).

## Typecheck command

`acg.typecheck.run_tsc_noemit` prefers, in order:

1. `node_modules/typescript/bin/tsc`
2. `node_modules/.bin/tsc`
3. `npx --no-install tsc --noEmit`

Run **`npm install`** (or `npm ci`) in the checkout first. For **nestjs-realworld-example-app**, copy `src/config.ts.example` → `src/config.ts` so the baseline project typechecks (secrets file is intentionally absent upstream).

## One-shot smoke (mock, no paid LLM)

```bash
ACG_MOCK_LLM=1 ./.venv/bin/python -m experiments.greenhouse.headtohead \
  --lock experiments/realworld/agent_lock_blind.json \
  --tasks experiments/realworld/tasks_blind.json \
  --repo experiments/realworld/checkout \
  --backend mock --strategy ablation \
  --out-dir /tmp/acg-apply-smoke --suite-name apply-and-test-smoke
```

With dependencies + `src/config.ts` in place, `eval_run_acg.json` should show non-zero **`typecheck_pass_count`**.

## Worked example (summary fragment)

**Live applied run with model silence (all typecheck failures, zero tests)** — fields exist but scores are zero:

```json
"typecheck_pass_count": 0,
"typecheck_fail_count": 6,
"typecheck_skipped_count": 0,
"tests_ran_count": 0,
"first_run_pass_rate": 0.0,
"task_completion_rate": 0.0,
"patch_na_count": 0
```

**Mock apply-and-test smoke after wiring above** — same keys, non-zero pass count when `tsc` exits 0 per task:

```json
"typecheck_pass_count": 6,
"typecheck_fail_count": 0,
"typecheck_skipped_count": 0,
"tests_ran_count": 0,
"first_run_pass_rate": 0.0,
"task_completion_rate": 1.0,
"patch_na_count": 0
```

(`first_run_pass_rate` / `tests_ran_count` stay at paper defaults until a Jest/npm test step is hooked per-task.)

## Optional explicit flag

`--applied-diff-live` forces the git apply + `tsc` path for **local** (and for **mock** is redundant when `--repo` is set). It can be combined with multi-strategy runs if every expanded strategy supports the applied path.
