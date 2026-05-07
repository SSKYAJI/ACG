# ACG run-trace analysis

_Aggregated across 1 run artifact(s)._

## Runs

| file | suite | strategy | backend | status_completed | tested_passed/tests_ran | overlap_pairs | proposal_oob | posthoc_diff_oob | validator_blocked | prompt_tokens | prompt_token_method | cost_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eval_run_acg.json | demo-app-openrouter-graph-quality-v2 | acg_planned | local | 4/4 | 0/0 | 2 | 0 | 0 | 1 | 548 | provider_usage_prompt_tokens | 0.000150 |

## Predictor accuracy (per task, across runs)

| task | runs | TP | FP | FN | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| billing | 1 | 3 | 5 | 0 | 0.38 | 1.00 | 0.55 |
| oauth | 1 | 5 | 1 | 0 | 0.83 | 1.00 | 0.91 |
| settings | 1 | 2 | 1 | 0 | 0.67 | 1.00 | 0.80 |
| tests | 1 | 0 | 5 | 0 | 0.00 | 0.00 | 0.00 |

**Overall: precision=0.45 recall=1.00 F1=0.62**

## Contract enforcement

- Proposal out-of-bounds files across proposal-only runs: **0**
- Planned validator-blocked write events across all runs: **1**
- Post-hoc out-of-bounds files detected in applied/manual diffs: **0**

## Refinement suggestions

### `billing`

- predictor over-predicts (precision=0.38); consider removing ['.env.example', 'src/app/api/billing/route.ts', 'src/app/dashboard/billing/page.tsx'] from predicted_writes seeds

### `tests`

- predictor over-predicts (precision=0.00); consider removing ['.env.example', 'playwright.config.ts', 'src/app/api/checkout/route.ts'] from predicted_writes seeds
