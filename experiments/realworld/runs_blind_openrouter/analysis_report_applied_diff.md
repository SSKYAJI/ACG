# ACG run-trace analysis

_Aggregated across 1 run artifact(s)._

> **Note:** This is diff-only smoke evidence (`acg-applied-diff-smoke` branch adds a TODO comment to `src/main.ts`). It measures how files were identified via `git diff --name-only`, not implementation-backed changes. Do not treat this as evidence of generated code quality or full implementation correctness.

## Runs

| file                           | suite          | strategy    | backend      | status_completed | tested_passed/tests_ran | overlap_pairs | proposal_oob | posthoc_diff_oob | validator_blocked | prompt_tokens | all_in_tokens | prompt_token_method | cost_usd     |
| ------------------------------ | -------------- | ----------- | ------------ | ---------------- | ----------------------- | ------------- | ------------ | ---------------- | ----------------- | ------------- | ------------- | ------------------- | ------------ |
| eval_run_applied_diff_acg.json | realworld-eval | acg_planned | applied-diff | 1/1              | 0/0                     | 0             | 0            | 0                | 0                 | —             | —             | —                   | not recorded |

## Predictor accuracy (per task, across runs)

| task              | runs | TP  | FP  | FN  | precision | recall | F1   |
| ----------------- | ---- | --- | --- | --- | --------- | ------ | ---- |
| add-rate-limiting | 1    | 1   | 0   | 0   | 1.00      | 1.00   | 1.00 |

**Overall: precision=1.00 recall=1.00 F1=1.00**

## Contract enforcement

- Proposal out-of-bounds files across proposal-only runs: **0**
- Planned validator-blocked write events across all runs: **0**
- Post-hoc out-of-bounds files detected in applied/manual diffs: **0**

> All agents stayed within their `allowed_paths` on every observed run. The contract acted as a safety net but did not need to fire — agents behaved within bounds. To stress-test the validator, consider tightening `allowed_paths` so the predicted set is closer to the minimal write set.

## Refinement suggestions

_No refinements suggested — predictor and contract are well-calibrated for the observed runs._
