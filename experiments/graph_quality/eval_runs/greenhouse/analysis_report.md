# ACG run-trace analysis

_Aggregated across 1 run artifact(s)._

## Runs

| file | suite | strategy | backend | status_completed | tested_passed/tests_ran | overlap_pairs | proposal_oob | posthoc_diff_oob | validator_blocked | prompt_tokens | prompt_token_method | cost_usd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| eval_run_acg.json | greenhouse-openrouter-graph-quality-v2 | acg_planned | local | 3/3 | 0/0 | 3 | 0 | 0 | 0 | 2018 | provider_usage_prompt_tokens | 0.000222 |

## Predictor accuracy (per task, across runs)

| task | runs | TP | FP | FN | precision | recall | F1 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| lambda-rowmapper-account | 1 | 2 | 6 | 0 | 0.25 | 1.00 | 0.40 |
| lambda-rowmapper-app | 1 | 2 | 6 | 0 | 0.25 | 1.00 | 0.40 |
| lambda-rowmapper-invite | 1 | 2 | 6 | 0 | 0.25 | 1.00 | 0.40 |

**Overall: precision=0.25 recall=1.00 F1=0.40**

## Contract enforcement

- Proposal out-of-bounds files across proposal-only runs: **0**
- Planned validator-blocked write events across all runs: **0**
- Post-hoc out-of-bounds files detected in applied/manual diffs: **0**

> All agents stayed within their `allowed_paths` on every observed run. The contract acted as a safety net but did not need to fire — agents behaved within bounds. To stress-test the validator, consider tightening `allowed_paths` so the predicted set is closer to the minimal write set.

## Refinement suggestions

### `lambda-rowmapper-account`

- predictor over-predicts (precision=0.25); consider removing ['src/main/java/com/springsource/greenhouse/account/Account.java', 'src/main/java/com/springsource/greenhouse/account/AccountException.java', 'src/main/java/com/springsource/greenhouse/account/AccountMapper.java'] from predicted_writes seeds

### `lambda-rowmapper-app`

- predictor over-predicts (precision=0.25); consider removing ['src/main/java/com/springsource/greenhouse/develop/App.java', 'src/main/java/com/springsource/greenhouse/develop/AppConnection.java', 'src/main/java/com/springsource/greenhouse/develop/AppController.java'] from predicted_writes seeds

### `lambda-rowmapper-invite`

- predictor over-predicts (precision=0.25); consider removing ['src/main/java/com/springsource/greenhouse/invite/FacebookInviteController.java', 'src/main/java/com/springsource/greenhouse/invite/Invite.java', 'src/main/java/com/springsource/greenhouse/invite/InviteAcceptAction.java'] from predicted_writes seeds
