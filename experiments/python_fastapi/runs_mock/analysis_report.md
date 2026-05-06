# ACG run-trace analysis

_Aggregated across 2 run artifact(s)._

## Runs

| file | suite | strategy | backend | status_completed | tested_passed/tests_ran | overlap_pairs | proposal_oob | posthoc_diff_oob | validator_blocked | prompt_tokens | prompt_token_method | cost_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eval_run_combined.json | python-fastapi-template | acg_planned | mock | 5/5 | 0/0 | 10 | 0 | 0 | 0 | 1684 | estimated_chars_div_4 | not recorded |
| eval_run_combined.json | python-fastapi-template | naive_parallel | mock | 5/5 | 0/0 | 10 | 0 | 0 | 0 | 2257 | estimated_chars_div_4 | not recorded |

## Predictor accuracy (per task, across runs)

| task | runs | TP | FP | FN | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| admin-audit-route | 2 | 8 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| health-route | 2 | 8 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| oauth-callback-route | 2 | 8 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| request-logging-config | 2 | 8 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| smtp-timeout-config | 2 | 7 | 0 | 0 | 1.00 | 1.00 | 1.00 |

**Overall: precision=1.00 recall=1.00 F1=1.00**

## Contract enforcement

- Proposal out-of-bounds files across proposal-only runs: **0**
- Planned validator-blocked write events across all runs: **0**
- Post-hoc out-of-bounds files detected in applied/manual diffs: **0**

> All agents stayed within their `allowed_paths` on every observed run. The contract acted as a safety net but did not need to fire — agents behaved within bounds. To stress-test the validator, consider tightening `allowed_paths` so the predicted set is closer to the minimal write set.

## Refinement suggestions

_No refinements suggested — predictor and contract are well-calibrated for the observed runs._
