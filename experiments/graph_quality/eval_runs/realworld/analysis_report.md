# ACG run-trace analysis

_Aggregated across 1 run artifact(s)._

## Runs

| file | suite | strategy | backend | status_completed | tested_passed/tests_ran | overlap_pairs | proposal_oob | posthoc_diff_oob | validator_blocked | prompt_tokens | prompt_token_method | cost_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eval_run_acg.json | realworld-openrouter-graph-quality-v2 | acg_planned | local | 6/6 | 0/0 | 4 | 0 | 0 | 0 | 1342 | provider_usage_prompt_tokens | 0.000316 |

## Predictor accuracy (per task, across runs)

| task | runs | TP | FP | FN | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| add-article-bookmarks | 1 | 5 | 2 | 0 | 0.71 | 1.00 | 0.83 |
| add-article-search | 1 | 3 | 5 | 0 | 0.38 | 1.00 | 0.55 |
| add-health-check | 1 | 3 | 3 | 0 | 0.50 | 1.00 | 0.67 |
| add-rate-limiting | 1 | 3 | 5 | 0 | 0.38 | 1.00 | 0.55 |
| add-user-roles | 1 | 5 | 3 | 0 | 0.62 | 1.00 | 0.77 |
| extend-tag-crud | 1 | 3 | 5 | 0 | 0.38 | 1.00 | 0.55 |

**Overall: precision=0.49 recall=1.00 F1=0.66**

## Contract enforcement

- Proposal out-of-bounds files across proposal-only runs: **0**
- Planned validator-blocked write events across all runs: **0**
- Post-hoc out-of-bounds files detected in applied/manual diffs: **0**

> All agents stayed within their `allowed_paths` on every observed run. The contract acted as a safety net but did not need to fire — agents behaved within bounds. To stress-test the validator, consider tightening `allowed_paths` so the predicted set is closer to the minimal write set.

## Refinement suggestions

### `add-article-search`

- predictor over-predicts (precision=0.38); consider removing ['src/article/article.interface.ts', 'src/article/dto/create-article.dto.ts', 'src/article/dto/create-comment.ts'] from predicted_writes seeds

### `add-health-check`

- predictor over-predicts (precision=0.50); consider removing ['package.js', 'src/app.module.ts', 'src/main.ts'] from predicted_writes seeds

### `add-rate-limiting`

- predictor over-predicts (precision=0.38); consider removing ['.env.example', 'package.js', 'src/main.ts'] from predicted_writes seeds

### `extend-tag-crud`

- predictor over-predicts (precision=0.38); consider removing ['src/article/dto/create-article.dto.ts', 'src/article/dto/create-comment.ts', 'src/article/dto/index.ts'] from predicted_writes seeds
