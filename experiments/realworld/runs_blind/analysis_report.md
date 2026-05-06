# ACG run-trace analysis

_Aggregated across 2 run artifact(s)._

## Runs

| file | suite | strategy | backend | completed | overlap_pairs | oob | blocked | prompt_tokens |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eval_run_combined.json | greenhouse-java6-modernization | acg_planned | local | 6/6 | 5 | 0 | 0 | 1105 |
| eval_run_combined.json | greenhouse-java6-modernization | naive_parallel | local | 4/6 | 3 | 3 | 0 | 2128 |

## Predictor accuracy (per task, across runs)

| task | runs | TP | FP | FN | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| add-article-bookmarks | 2 | 4 | 4 | 1 | 0.50 | 0.80 | 0.62 |
| add-article-search | 2 | 3 | 5 | 1 | 0.38 | 0.75 | 0.50 |
| add-health-check | 2 | 2 | 2 | 0 | 0.50 | 1.00 | 0.67 |
| add-rate-limiting | 2 | 2 | 4 | 0 | 0.33 | 1.00 | 0.50 |
| add-user-roles | 2 | 5 | 3 | 2 | 0.62 | 0.71 | 0.67 |
| extend-tag-crud | 2 | 3 | 2 | 0 | 0.60 | 1.00 | 0.75 |

**Overall: precision=0.49 recall=0.83 F1=0.61**

## Contract enforcement

- Total out-of-bounds proposals across all runs: **3**
- Total validator-blocked write events across all runs: **0**

## Refinement suggestions

### `add-article-bookmarks`

- predictor over-predicts (precision=0.50); consider removing ['src/article/article.interface.ts', 'src/article/article.module.ts', 'src/article/comment.entity.ts'] from predicted_writes seeds
- agent proposed 1 OOB write(s) (['src/article/dto/index.ts']); decide: widen allowed_paths to include them, or audit the agent prompt

### `add-article-search`

- predictor over-predicts (precision=0.38); consider removing ['src/article/article.entity.ts', 'src/article/article.module.ts', 'src/article/comment.entity.ts'] from predicted_writes seeds

### `add-health-check`

- predictor over-predicts (precision=0.50); consider removing ['prisma/.env', 'src/article/article.service.ts'] from predicted_writes seeds

### `add-rate-limiting`

- predictor over-predicts (precision=0.33); consider removing ['src/article/article.controller.ts', 'src/article/article.service.ts', 'src/profile/profile.service.ts'] from predicted_writes seeds

### `add-user-roles`

- agent proposed 2 OOB write(s) (['src/user/user.decorator.ts', 'src/user/user.module.ts']); decide: widen allowed_paths to include them, or audit the agent prompt
