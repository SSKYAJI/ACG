# ACG run-trace analysis

_Aggregated across 2 run artifact(s)._

## Runs

| file | suite | strategy | backend | status_completed | tested_passed/tests_ran | overlap_pairs | proposal_oob | posthoc_diff_oob | validator_blocked | prompt_tokens | prompt_token_method | cost_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eval_run_combined.json | realworld-nestjs-explicit-openrouter-v1 | acg_planned | local | 6/6 | 0/0 | 5 | 0 | 0 | 0 | 1380 | estimated_chars_div_4 | 0.000322 |
| eval_run_combined.json | realworld-nestjs-explicit-openrouter-v1 | naive_parallel | local | 6/6 | 0/0 | 5 | 0 | 0 | 0 | 2451 | estimated_chars_div_4 | 0.000403 |

## Predictor accuracy (per task, across runs)

| task | runs | TP | FP | FN | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| add-article-bookmarks | 2 | 5 | 2 | 0 | 0.71 | 1.00 | 0.83 |
| add-article-search | 2 | 3 | 5 | 0 | 0.38 | 1.00 | 0.55 |
| add-health-check | 2 | 4 | 2 | 0 | 0.67 | 1.00 | 0.80 |
| add-rate-limiting | 2 | 3 | 5 | 0 | 0.38 | 1.00 | 0.55 |
| add-user-roles | 2 | 5 | 3 | 0 | 0.62 | 1.00 | 0.77 |
| extend-tag-crud | 2 | 3 | 5 | 0 | 0.38 | 1.00 | 0.55 |

**Overall: precision=0.51 recall=1.00 F1=0.68**

## Contract enforcement

- Proposal out-of-bounds files across proposal-only runs: **0**
- Planned validator-blocked write events across all runs: **0**
- Post-hoc out-of-bounds files detected in applied/manual diffs: **0**

> All agents stayed within their `allowed_paths` on every observed run. The contract acted as a safety net but did not need to fire — agents behaved within bounds. To stress-test the validator, consider tightening `allowed_paths` so the predicted set is closer to the minimal write set.

## Refinement suggestions

### `add-article-search`

- predictor over-predicts (precision=0.38); consider removing ['src/article/article.interface.ts', 'src/article/dto/create-article.dto.ts', 'src/article/dto/create-comment.ts'] from predicted_writes seeds

### `add-rate-limiting`

- predictor over-predicts (precision=0.38); consider removing ['.env.example', 'package.js', 'src/main.ts'] from predicted_writes seeds

### `extend-tag-crud`

- predictor over-predicts (precision=0.38); consider removing ['src/article/dto/create-article.dto.ts', 'src/article/dto/create-comment.ts', 'src/article/dto/index.ts'] from predicted_writes seeds
