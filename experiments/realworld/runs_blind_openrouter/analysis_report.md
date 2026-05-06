# ACG run-trace analysis

_Aggregated across 3 run artifact(s)._

## Runs

| file | suite | strategy | backend | status_completed | tested_passed/tests_ran | overlap_pairs | proposal_oob | posthoc_diff_oob | validator_blocked | prompt_tokens | prompt_token_method | cost_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eval_run_combined.json | realworld-nestjs-blind-openrouter-v2 | acg_planned | local | 6/6 | 0/0 | 5 | 0 | 0 | 0 | 1105 | estimated_chars_div_4 | 0.000270 |
| eval_run_combined.json | realworld-nestjs-blind-openrouter-v2 | acg_planned_full_context | local | 6/6 | 0/0 | 3 | 0 | 0 | 4 | 2128 | estimated_chars_div_4 | 0.000466 |
| eval_run_combined.json | realworld-nestjs-blind-openrouter-v2 | naive_parallel | local | 5/6 | 0/0 | 3 | 2 | 0 | 0 | 2128 | estimated_chars_div_4 | 0.000590 |

## Predictor accuracy (per task, across runs)

| task | runs | TP | FP | FN | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| add-article-bookmarks | 3 | 5 | 3 | 0 | 0.62 | 1.00 | 0.77 |
| add-article-search | 3 | 3 | 5 | 1 | 0.38 | 0.75 | 0.50 |
| add-health-check | 3 | 2 | 2 | 0 | 0.50 | 1.00 | 0.67 |
| add-rate-limiting | 3 | 2 | 4 | 0 | 0.33 | 1.00 | 0.50 |
| add-user-roles | 3 | 5 | 3 | 2 | 0.62 | 0.71 | 0.67 |
| extend-tag-crud | 3 | 3 | 2 | 0 | 0.60 | 1.00 | 0.75 |

**Overall: precision=0.51 recall=0.87 F1=0.65**

## Contract enforcement

- Proposal out-of-bounds files across proposal-only runs: **2**
- Planned validator-blocked write events across all runs: **4**
- Post-hoc out-of-bounds files detected in applied/manual diffs: **0**

## Refinement suggestions

### `add-article-search`

- predictor over-predicts (precision=0.38); consider removing ['src/article/article.entity.ts', 'src/article/article.module.ts', 'src/article/comment.entity.ts'] from predicted_writes seeds

### `add-health-check`

- predictor over-predicts (precision=0.50); consider removing ['prisma/.env', 'src/article/article.service.ts'] from predicted_writes seeds

### `add-rate-limiting`

- predictor over-predicts (precision=0.33); consider removing ['src/article/article.controller.ts', 'src/article/article.service.ts', 'src/profile/profile.service.ts'] from predicted_writes seeds

### `add-user-roles`

- agent proposed 2 OOB write(s) (['src/user/user.decorator.ts', 'src/user/user.module.ts']); decide: widen allowed_paths to include them, or audit the agent prompt
