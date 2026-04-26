# ACG run-trace analysis

_Aggregated across 4 run artifact(s)._

## Runs

| file | suite | strategy | backend | completed | overlap_pairs | oob | blocked | prompt_tokens |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eval_run_combined.json | greenhouse-java6-modernization | acg_planned | mock | 7/7 | 11 | 0 | 0 | 1700 |
| eval_run_combined.json | greenhouse-java6-modernization | naive_parallel | mock | 7/7 | 11 | 0 | 0 | 3721 |
| eval_run_combined.json | greenhouse-java6-modernization | acg_planned | local | 7/7 | 0 | 0 | 0 | 1700 |
| eval_run_combined.json | greenhouse-java6-modernization | naive_parallel | local | 7/7 | 0 | 0 | 0 | 3721 |

## Predictor accuracy (per task, across runs)

| task | runs | TP | FP | FN | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| api-key-auth | 4 | 8 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| deployment-config | 4 | 8 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| files-e2e-tests | 4 | 8 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| notifications-webhook | 4 | 8 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| products-domain | 4 | 8 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| registration-email-job | 4 | 8 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| users-search | 4 | 8 | 0 | 0 | 1.00 | 1.00 | 1.00 |

**Overall: precision=1.00 recall=1.00 F1=1.00**

## Contract enforcement

- Total out-of-bounds proposals across all runs: **0**
- Total validator-blocked write events across all runs: **0**

> All agents stayed within their `allowed_paths` on every observed run. The contract acted as a safety net but did not need to fire — agents behaved within bounds. To stress-test the validator, consider tightening `allowed_paths` so the predicted set is closer to the minimal write set.

## Refinement suggestions

_No refinements suggested — predictor and contract are well-calibrated for the observed runs._
