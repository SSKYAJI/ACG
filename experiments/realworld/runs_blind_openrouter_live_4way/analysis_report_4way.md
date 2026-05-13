# ACG run-trace analysis

_Aggregated across 4 run artifact(s)._

## Runs

| file | suite | strategy | backend | status_completed | tested_passed/tests_ran | overlap_pairs | proposal_oob | posthoc_diff_oob | validator_blocked | prompt_tokens | all_in_tokens | prompt_token_method | cost_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eval_run_single_agent.json | realworld-nestjs-blind-live-4way | single_agent | local | 6/6 | 0/0 | 2 | 0 | 0 | 0 | 630 | 630 | provider_usage_prompt_tokens | 0.000219 |
| eval_run_naive_parallel.json | realworld-nestjs-blind-live-4way | naive_parallel | local | 2/6 | 0/0 | 2 | 11 | 0 | 0 | 2656 | 12802 | provider_usage_prompt_tokens | 0.000382 |
| eval_run_acg_planned.json | realworld-nestjs-blind-live-4way | acg_planned | local | 5/6 | 0/0 | 1 | 0 | 0 | 2 | 1339 | 11485 | provider_usage_prompt_tokens | 0.000204 |
| eval_run_acg_planned_replan.json | realworld-nestjs-blind-live-4way | acg_planned_replan | local | 6/6 | 0/0 | 1 | 0 | 0 | 3 | 1339 | 11485 | provider_usage_prompt_tokens | 0.000228 |

## Predictor accuracy (per task, across runs)

| task | runs | TP | FP | FN | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| add-article-bookmarks | 4 | 1 | 0 | 4 | 1.00 | 0.20 | 0.33 |
| add-article-search | 4 | 2 | 1 | 1 | 0.67 | 0.67 | 0.67 |
| add-health-check | 4 | 1 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| add-rate-limiting | 4 | 1 | 0 | 1 | 1.00 | 0.50 | 0.67 |
| add-user-roles | 4 | 4 | 0 | 4 | 1.00 | 0.50 | 0.67 |
| extend-tag-crud | 4 | 2 | 0 | 1 | 1.00 | 0.67 | 0.80 |

**Overall: precision=0.92 recall=0.50 F1=0.65**

## Contract enforcement

- Proposal out-of-bounds files across proposal-only runs: **11**
- Planned validator-blocked write events across all runs: **5**
- Post-hoc out-of-bounds files detected in applied/manual diffs: **0**

## Refinement suggestions

### `add-article-bookmarks`

- predictor missed files (recall=0.20); consider seeding ['src/article/article.controller.ts', 'src/article/article.entity.ts', 'src/article/article.service.ts'] into the predictor
- agent proposed 4 OOB write(s) (['src/article/article.controller.ts', 'src/article/article.entity.ts', 'src/article/article.service.ts']); decide: widen allowed_paths to include them, or audit the agent prompt

### `add-article-search`

- agent proposed 3 OOB write(s) (['src/article/article.controller.ts', 'src/article/article.entity.ts', 'src/article/article.service.ts']); decide: widen allowed_paths to include them, or audit the agent prompt

### `add-rate-limiting`

- predictor missed files (recall=0.50); consider seeding ['package.json'] into the predictor
- agent proposed 1 OOB write(s) (['package.json']); decide: widen allowed_paths to include them, or audit the agent prompt

### `add-user-roles`

- predictor missed files (recall=0.50); consider seeding ['src/user/auth.middleware.ts', 'src/user/user.decorator.ts', 'src/user/user.interface.ts'] into the predictor
- agent proposed 3 OOB write(s) (['src/user/auth.middleware.ts', 'src/user/user.decorator.ts', 'src/user/user.interface.ts']); decide: widen allowed_paths to include them, or audit the agent prompt
