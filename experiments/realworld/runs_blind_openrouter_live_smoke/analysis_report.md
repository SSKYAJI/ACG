# ACG run-trace analysis

_Aggregated across 1 run artifact(s)._

## Runs

| file | suite | strategy | backend | status_completed | tested_passed/tests_ran | overlap_pairs | proposal_oob | posthoc_diff_oob | validator_blocked | prompt_tokens | all_in_tokens | prompt_token_method | cost_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eval_run_acg_planned_replan.json | realworld-nestjs-blind-live-smoke | acg_planned_replan | local | 6/6 | 0/0 | 2 | 0 | 0 | 6 | 1839 | 20305 | provider_usage_prompt_tokens | 0.000305 |

## Predictor accuracy (per task, across runs)

| task | runs | TP | FP | FN | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| add-article-bookmarks | 1 | 2 | 3 | 0 | 0.40 | 1.00 | 0.57 |
| add-article-search | 1 | 0 | 4 | 0 | 0.00 | 0.00 | 0.00 |
| add-health-check | 1 | 1 | 6 | 0 | 0.14 | 1.00 | 0.25 |
| add-rate-limiting | 1 | 2 | 7 | 0 | 0.22 | 1.00 | 0.36 |
| add-user-roles | 1 | 5 | 4 | 0 | 0.56 | 1.00 | 0.71 |
| extend-tag-crud | 1 | 2 | 7 | 0 | 0.22 | 1.00 | 0.36 |

**Overall: precision=0.28 recall=1.00 F1=0.44**

## Contract enforcement

- Proposal out-of-bounds files across proposal-only runs: **0**
- Planned validator-blocked write events across all runs: **6**
- Post-hoc out-of-bounds files detected in applied/manual diffs: **0**

## Refinement suggestions

### `add-article-bookmarks`

- predictor over-predicts (precision=0.40); consider removing ['src/profile/profile.controller.ts', 'src/profile/profile.interface.ts', 'src/profile/profile.module.ts'] from predicted_writes seeds

### `add-article-search`

- predictor over-predicts (precision=0.00); consider removing ['src/profile/profile.controller.ts', 'src/profile/profile.interface.ts', 'src/profile/profile.module.ts'] from predicted_writes seeds

### `add-health-check`

- predictor over-predicts (precision=0.14); consider removing ['src/article/article.controller.ts', 'src/article/article.entity.ts', 'src/article/article.interface.ts'] from predicted_writes seeds
- allowed_paths declares 7 globs but agent touched only 1 files; consider tightening scope

### `add-rate-limiting`

- predictor over-predicts (precision=0.22); consider removing ['src/app.controller.ts', 'src/article/article.module.ts', 'src/profile/profile.controller.ts'] from predicted_writes seeds
- allowed_paths declares 9 globs but agent touched only 2 files; consider tightening scope

### `add-user-roles`

- predictor over-predicts (precision=0.56); consider removing ['src/article/article.module.ts', 'src/profile/profile.controller.ts', 'src/profile/profile.interface.ts'] from predicted_writes seeds

### `extend-tag-crud`

- predictor over-predicts (precision=0.22); consider removing ['src/app.controller.ts', 'src/article/article.module.ts', 'src/profile/profile.controller.ts'] from predicted_writes seeds
- allowed_paths declares 9 globs but agent touched only 2 files; consider tightening scope
