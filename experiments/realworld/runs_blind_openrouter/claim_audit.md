# RealWorld Blind OpenRouter Claim Audit

Artifact:

- `experiments/realworld/runs_blind_openrouter/eval_run_combined.json`
- `experiments/realworld/runs_blind_openrouter/analysis_report.md`

Repository:

- `https://github.com/lujakob/nestjs-realworld-example-app.git`
- commit `c1c2cc4e448b279ff083272df1ac50d20c3304fa`

Backend/model:

- `backend`: `local`
- provider: `openai-compatible`
- URL: `https://openrouter.ai/api/v1`
- model: `qwen/qwen3-coder-30b-a3b-instruct`
- mock-provider object count in `eval_run_combined.json`: `0`

## Results

| strategy | completed | prompt tokens | completion tokens | OOB proposals | validator blocked | wall time | cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `naive_parallel` | 5/6 | 2128 | 644 | 2 | 0 | 4.3603s | $0.00058952 |
| `acg_planned_full_context` | 6/6 | 2128 | 653 | 0 | 4 | 14.4070s | $0.00046567 |
| `acg_planned` | 6/6 | 1105 | 714 | 0 | 0 | 17.6957s | $0.00027043 |

## Paper-Safe Claims

- On a cloned RealWorld NestJS repository, scoped planned execution reduced worker prompt context by 48.1% versus the planned-full-context ablation (`2128 -> 1105` estimated prompt tokens).
- The token reduction is attributable to scoped worker context, not the planned schedule: naive and planned-full-context both used `2128` prompt tokens.
- This was a real OpenRouter run, not the mock backend: provider/model/url/cost fields are populated from the OpenAI-compatible path and no mock provider objects appear in the combined artifact.
- The live validator path fired on real OpenRouter proposals in the planned-full-context ablation: `blocked_invalid_write_count = 4`.
- Naive parallel surfaced unsafe proposal behavior on the blind task set: `add-user-roles` proposed 2 out-of-bounds files and the conservative completed count dropped to `5/6`.

## Not Paper-Safe

- Do not claim generated-code correctness or code quality from this run. `execution_mode` is `propose_validate`, `evidence_kind` is `proposed_write_set`, no patches were applied, and `tests_ran_count = 0`.
- Do not claim ACG is faster. Scoped planned was slower than naive on wall-clock (`17.6957s` vs `4.3603s`) because the lockfile serialized groups.
- Do not claim end-to-end token savings. Compile-time lockfile/predictor cost is not included.
- Do not claim the predictor has perfect recall on blind/general tasks. This run reports overall precision `0.51`, recall `0.87`, and F1 `0.65` against proposal artifacts.
- Do not generalize the 48.1% context reduction beyond this single run. It is one stochastic trial on one repo/task suite.

## Open Provenance Gap

The artifact records provider URL, model, provider completion-token usage, and provider-reported cost, but it does not persist raw OpenRouter response IDs or routing metadata. If the paper needs stronger provider provenance, add response-id/provider-routing fields to the runtime artifact schema and rerun.
