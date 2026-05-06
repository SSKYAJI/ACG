# ACG run-trace analysis

_Aggregated across 8 run artifact(s)._

## Runs

| file | suite | strategy | backend | completed | overlap_pairs | oob | blocked | prompt_tokens |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eval_run_combined.json | greenhouse-java6-modernization | acg_planned | local | 6/6 | 4 | 0 | 0 | 1380 |
| eval_run_combined.json | greenhouse-java6-modernization | naive_parallel | local | 6/6 | 5 | 0 | 0 | 2451 |
| eval_run_combined.json | greenhouse-java6-modernization | acg_planned | local | 6/6 | 5 | 0 | 0 | 1105 |
| eval_run_combined.json | greenhouse-java6-modernization | naive_parallel | local | 4/6 | 3 | 3 | 0 | 2128 |
| eval_run_devin_api_naive_smoke.json | greenhouse-java6-modernization | naive_parallel | devin-api | 3/3 | 3 | 0 | 0 | — |
| eval_run_devin_api_acg_smoke.json | greenhouse-java6-modernization | acg_planned | devin-api | 3/3 | 3 | 0 | 0 | — |
| eval_run_combined.json | greenhouse-java6-modernization | acg_planned | local | 3/3 | 3 | 0 | 0 | 1922 |
| eval_run_combined.json | greenhouse-java6-modernization | naive_parallel | local | 3/3 | 3 | 0 | 0 | 2159 |

## Predictor accuracy (per task, across runs)

| task | runs | TP | FP | FN | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| add-article-bookmarks | 4 | 6 | 3 | 1 | 0.67 | 0.86 | 0.75 |
| add-article-search | 4 | 5 | 6 | 0 | 0.45 | 1.00 | 0.62 |
| add-health-check | 4 | 5 | 4 | 0 | 0.56 | 1.00 | 0.71 |
| add-rate-limiting | 4 | 4 | 8 | 0 | 0.33 | 1.00 | 0.50 |
| add-user-roles | 4 | 8 | 4 | 1 | 0.67 | 0.89 | 0.76 |
| extend-tag-crud | 4 | 4 | 5 | 0 | 0.44 | 1.00 | 0.62 |
| lambda-rowmapper-account | 4 | 2 | 6 | 0 | 0.25 | 1.00 | 0.40 |
| lambda-rowmapper-app | 4 | 2 | 6 | 0 | 0.25 | 1.00 | 0.40 |
| lambda-rowmapper-invite | 4 | 2 | 6 | 0 | 0.25 | 1.00 | 0.40 |

**Overall: precision=0.44 recall=0.95 F1=0.60**

## Contract enforcement

- Total out-of-bounds proposals across all runs: **3**
- Total validator-blocked write events across all runs: **0**

## Refinement suggestions

### `add-article-bookmarks`

- agent proposed 1 OOB write(s) (['src/article/dto/index.ts']); decide: widen allowed_paths to include them, or audit the agent prompt

### `add-article-search`

- predictor over-predicts (precision=0.45); consider removing ['src/article/article.entity.ts', 'src/article/article.module.ts', 'src/article/comment.entity.ts'] from predicted_writes seeds

### `add-health-check`

- predictor over-predicts (precision=0.56); consider removing ['package.js', 'prisma/.env', 'src/article/article.service.ts'] from predicted_writes seeds

### `add-rate-limiting`

- predictor over-predicts (precision=0.33); consider removing ['.env.example', 'package.js', 'src/article/article.controller.ts'] from predicted_writes seeds

### `add-user-roles`

- agent proposed 2 OOB write(s) (['src/user/user.decorator.ts', 'src/user/user.module.ts']); decide: widen allowed_paths to include them, or audit the agent prompt

### `extend-tag-crud`

- predictor over-predicts (precision=0.44); consider removing ['src/article/dto/create-article.dto.ts', 'src/article/dto/create-comment.ts', 'src/article/dto/index.ts'] from predicted_writes seeds

### `lambda-rowmapper-account`

- predictor over-predicts (precision=0.25); consider removing ['src/main/java/com/springsource/greenhouse/account/Account.java', 'src/main/java/com/springsource/greenhouse/account/AccountException.java', 'src/main/java/com/springsource/greenhouse/account/AccountMapper.java'] from predicted_writes seeds

### `lambda-rowmapper-app`

- predictor over-predicts (precision=0.25); consider removing ['src/main/java/com/springsource/greenhouse/develop/App.java', 'src/main/java/com/springsource/greenhouse/develop/AppConnection.java', 'src/main/java/com/springsource/greenhouse/develop/AppController.java'] from predicted_writes seeds

### `lambda-rowmapper-invite`

- predictor over-predicts (precision=0.25); consider removing ['src/main/java/com/springsource/greenhouse/invite/FacebookInviteController.java', 'src/main/java/com/springsource/greenhouse/invite/Invite.java', 'src/main/java/com/springsource/greenhouse/invite/InviteAcceptAction.java'] from predicted_writes seeds
