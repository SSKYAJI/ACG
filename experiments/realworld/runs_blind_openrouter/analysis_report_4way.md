# ACG run-trace analysis

_Aggregated across 4 run artifact(s)._

## Runs

| file | suite | strategy | backend | status_completed | tested_passed/tests_ran | overlap_pairs | proposal_oob | posthoc_diff_oob | validator_blocked | prompt_tokens | all_in_tokens | prompt_token_method | cost_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eval_run_single_agent.json | realworld-nestjs-blind-openrouter | single_agent | local | 6/6 | 0/0 | 2 | 0 | 0 | 0 | 630 | 630 | provider_usage_prompt_tokens | 0.000212 |
| eval_run_naive_parallel.json | realworld-nestjs-blind-openrouter | naive_parallel | local | 2/6 | 0/0 | 2 | 11 | 0 | 0 | 2636 | 12145 | provider_usage_prompt_tokens | 0.000367 |
| eval_run_acg_planned.json | realworld-nestjs-blind-openrouter | acg_planned | local | 6/6 | 0/0 | 1 | 0 | 0 | 3 | 1301 | 10810 | provider_usage_prompt_tokens | 0.000199 |
| eval_run_acg_planned_replan.json | realworld-nestjs-blind-openrouter | acg_planned_replan | local | 6/6 | 0/0 | 1 | 0 | 0 | 0 | 1301 | 10810 | provider_usage_prompt_tokens | 0.000294 |

## Predictor accuracy (per task, across runs)

| task | runs | TP | FP | FN | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| add-article-bookmarks | 4 | 2 | 0 | 3 | 1.00 | 0.40 | 0.57 |
| add-article-search | 4 | 2 | 0 | 1 | 1.00 | 0.67 | 0.80 |
| add-health-check | 4 | 1 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| add-rate-limiting | 4 | 1 | 0 | 1 | 1.00 | 0.50 | 0.67 |
| add-user-roles | 4 | 3 | 0 | 3 | 1.00 | 0.50 | 0.67 |
| extend-tag-crud | 4 | 2 | 0 | 1 | 1.00 | 0.67 | 0.80 |

**Overall: precision=1.00 recall=0.55 F1=0.71**

## Contract enforcement

- Proposal out-of-bounds files across proposal-only runs: **11**
- Planned validator-blocked write events across all runs: **3**
- Post-hoc out-of-bounds files detected in applied/manual diffs: **0**

## Refinement suggestions

### `add-article-bookmarks`

- predictor missed files (recall=0.40); consider seeding ['src/article/article.controller.ts', 'src/article/article.service.ts', 'src/user/user.service.ts'] into the predictor
- agent proposed 4 OOB write(s) (['src/article/article.controller.ts', 'src/article/article.entity.ts', 'src/article/article.service.ts']); decide: widen allowed_paths to include them, or audit the agent prompt

### `add-article-search`

- agent proposed 3 OOB write(s) (['src/article/article.controller.ts', 'src/article/article.interface.ts', 'src/article/article.service.ts']); decide: widen allowed_paths to include them, or audit the agent prompt

### `add-rate-limiting`

- predictor missed files (recall=0.50); consider seeding ['package.json'] into the predictor
- agent proposed 1 OOB write(s) (['package.json']); decide: widen allowed_paths to include them, or audit the agent prompt

### `add-user-roles`

- predictor missed files (recall=0.50); consider seeding ['src/user/auth.middleware.ts', 'src/user/user.controller.ts', 'src/user/user.service.ts'] into the predictor
- agent proposed 3 OOB write(s) (['src/user/auth.middleware.ts', 'src/user/user.controller.ts', 'src/user/user.interface.ts']); decide: widen allowed_paths to include them, or audit the agent prompt
