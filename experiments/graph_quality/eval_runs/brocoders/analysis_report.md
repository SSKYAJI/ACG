# ACG run-trace analysis

_Aggregated across 1 run artifact(s)._

## Runs

| file | suite | strategy | backend | status_completed | tested_passed/tests_ran | overlap_pairs | proposal_oob | posthoc_diff_oob | validator_blocked | prompt_tokens | prompt_token_method | cost_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eval_run_acg.json | brocoders-openrouter-graph-quality-v2 | acg_planned | local | 7/7 | 0/0 | 4 | 0 | 0 | 6 | 1651 | provider_usage_prompt_tokens | 0.000520 |

## Predictor accuracy (per task, across runs)

| task | runs | TP | FP | FN | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| api-key-auth | 1 | 5 | 3 | 1 | 0.62 | 0.83 | 0.71 |
| deployment-config | 1 | 5 | 3 | 0 | 0.62 | 1.00 | 0.77 |
| files-e2e-tests | 1 | 1 | 7 | 0 | 0.12 | 1.00 | 0.22 |
| notifications-webhook | 1 | 5 | 3 | 0 | 0.62 | 1.00 | 0.77 |
| products-domain | 1 | 4 | 4 | 3 | 0.50 | 0.57 | 0.53 |
| registration-email-job | 1 | 7 | 1 | 0 | 0.88 | 1.00 | 0.93 |
| users-search | 1 | 4 | 4 | 0 | 0.50 | 1.00 | 0.67 |

**Overall: precision=0.55 recall=0.89 F1=0.68**

## Contract enforcement

- Proposal out-of-bounds files across proposal-only runs: **0**
- Planned validator-blocked write events across all runs: **6**
- Post-hoc out-of-bounds files detected in applied/manual diffs: **0**

## Refinement suggestions

### `files-e2e-tests`

- predictor over-predicts (precision=0.12); consider removing ['__tests__/authorization.test.ts', '__tests__/configuration.test.ts', 'src/app.module.ts'] from predicted_writes seeds
- allowed_paths declares 8 globs but agent touched only 1 files; consider tightening scope

### `products-domain`

- predictor over-predicts (precision=0.50); consider removing ['src/app.module.ts', 'src/database/migrations/1715028537217-CreateUser.ts', 'src/files/infrastructure/persistence/relational/relational-persistence.module.ts'] from predicted_writes seeds
- predictor missed files (recall=0.57); consider seeding ['src/database/migrations/1715028537218-CreateProduct.ts', 'src/products/infrastructure/persistence/relational/entities/product.entity.ts', 'src/products/infrastructure/persistence/relational/repositories/product.repository.ts'] into the predictor

### `users-search`

- predictor over-predicts (precision=0.50); consider removing ['app.module.ts', 'src/users/domain/user.ts', 'src/users/dto/create-user.dto.ts'] from predicted_writes seeds
