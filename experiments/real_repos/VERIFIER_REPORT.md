# Lane P Verifier Report

On 2 production repos with 6 historical PRs, the predictor achieved 0.583 mean ground-truth recall and 0.375 mean ground-truth precision. 0 PR(s) verified end-to-end via apply-and-test.

## Overall

| Metric | Value |
| --- | ---: |
| Wait condition O2/O3/O4 satisfied | yes |
| Completed DONE production repos scored | 2 |
| Historical PRs scored | 6 |
| Mean ground-truth recall | 0.583 |
| Mean ground-truth precision | 0.375 |
| PASS-PROPOSE tasks | 4 |
| WEAK-PROPOSE tasks | 2 |
| FAILED-PROPOSE tasks | 0 |
| PASS-APPLY tasks | 0 |
| PARTIAL-APPLY tasks | 0 |
| FAILED-APPLY tasks | 0 |
| SKIPPED-APPLY tasks | 6 |

## Lane Sentinels

| Lane | Repo | Status | Sentinel |
| --- | --- | --- | --- |
| O2 | fastify | DONE | `experiments/real_repos/fastify/LANE_O2_DONE.md` |
| O3 | starlette | DONE | `experiments/real_repos/starlette/LANE_O3_DONE.md` |
| O4 | black | FAILURE | `experiments/real_repos/black/LANE_O4_FAILURE.md` |

## Per-Repo Summary

| Repo | Lane | PRs | Mean Recall | Mean Precision | PASS-PROPOSE | WEAK-PROPOSE | FAILED-PROPOSE | Apply Verdicts |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| fastify | O2 | 3 | 0.278 | 0.194 | 1 | 2 | 0 | SKIPPED=3 |
| starlette | O3 | 3 | 0.889 | 0.556 | 3 | 0 | 0 | SKIPPED=3 |

## Per-Task Verdicts

| Repo | Task | Recall | Precision | F1 | Propose | Apply | Notes |
| --- | --- | ---: | ---: | ---: | --- | --- | --- |
| fastify | pr-6653 | 0.000 | 0.000 | 0.000 | WEAK-PROPOSE | SKIPPED-APPLY | unsafe strategy status: naive_parallel |
| fastify | pr-6692 | 0.500 | 0.333 | 0.400 | PASS-PROPOSE | SKIPPED-APPLY | unsafe strategy status: naive_parallel |
| fastify | pr-6694 | 0.333 | 0.250 | 0.286 | WEAK-PROPOSE | SKIPPED-APPLY | unsafe strategy status: naive_parallel |
| starlette | pr3137-cors-credentials-origin | 1.000 | 0.667 | 0.800 | PASS-PROPOSE | SKIPPED-APPLY | OK |
| starlette | pr3148-jinja2-autoescape | 1.000 | 0.333 | 0.500 | PASS-PROPOSE | SKIPPED-APPLY | unsafe strategy status: naive_parallel |
| starlette | pr3166-session-middleware | 0.667 | 0.667 | 0.667 | PASS-PROPOSE | SKIPPED-APPLY | unsafe strategy status: naive_parallel |

## Sanity Checks

- Token accounting: all loaded `eval_run_combined.json` strategy summaries use `provider_usage_prompt_tokens`.
- Cost accounting: all loaded strategy and task metrics report provider cost from `body.usage.cost`; `cost_method` is `sum_provider_reported_task_costs`.
- Mock providers: no mock provider markers were found in the completed DONE lane JSON artifacts.
- Ground truth files: every scored task has a non-empty `ground_truth_files` list and each file exists in the corresponding checkout.
- Path safety: `predicted_writes`, `predicted_write_files`, and scored `proposed_write_files` are repo-relative; no absolute paths, parent traversal, or `/etc/passwd` paths were found.
- Harness safety flags: 5 strategy task(s) had `completed_unsafe` status and 10 out-of-bounds proposed file(s), all in strategy-level proposal artifacts; these are captured in `VERIFIER_REPORT.json`.
- Apply-and-test: both completed DONE lanes only have `apply_summary.md` skip notes, with no `apply_diff.patch` or `apply_test_output.txt`; all scored PRs are `SKIPPED-APPLY`.

## Lane Failures

- black/O4: Lane O4 failure for `black`. - Starting pytest: `211 passed`. - Task attempted: `pr-5080`. - Parent commit checked out: `866c350cec7edd999ef55a0edb7b2202aa917f15`.

## Pytest Verification

- Start: `211 passed, 11 warnings`.
- End: `211 passed, 11 warnings`.

## Artifacts Loaded

- `experiments/real_repos/fastify/runs/ground_truth_score.json`
- `experiments/real_repos/fastify/runs/pr-6653/eval_run_combined.json`
- `experiments/real_repos/fastify/runs/pr-6692/eval_run_combined.json`
- `experiments/real_repos/fastify/runs/pr-6694/eval_run_combined.json`
- `experiments/real_repos/starlette/runs/ground_truth_score.json`
- `experiments/real_repos/starlette/runs/pr3137-cors-credentials-origin/eval_run_combined.json`
- `experiments/real_repos/starlette/runs/pr3148-jinja2-autoescape/eval_run_combined.json`
- `experiments/real_repos/starlette/runs/pr3166-session-middleware/eval_run_combined.json`
