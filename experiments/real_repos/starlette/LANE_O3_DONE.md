# Lane O3 Done

Repo: starlette (Kludex/starlette)

Baseline cognition pytest: 211 passed.
Final cognition pytest: 211 passed.

Harness artifacts:
- pr3166-session-middleware: eval_run_combined.json present
- pr3148-jinja2-autoescape: eval_run_combined.json present
- pr3137-cors-credentials-origin: eval_run_combined.json present

Ground-truth metrics:

| task_id | precision | recall | F1 | agent_match_to_human |
| --- | ---: | ---: | ---: | ---: |
| pr3166-session-middleware | 0.666667 | 0.666667 | 0.666667 | 0.800000 |
| pr3148-jinja2-autoescape | 0.333333 | 1.000000 | 0.500000 | 1.000000 |
| pr3137-cors-credentials-origin | 0.666667 | 1.000000 | 0.800000 | 0.666667 |

Analysis outputs:
- experiments/real_repos/starlette/runs/analysis_report.md
- experiments/real_repos/starlette/runs/analysis_report.json
- experiments/real_repos/starlette/runs/ground_truth_score.json

Cost:
- Provider-reported harness cost total: 0.00043472 USD
- Compile cost was not reported by the synchronous compile client.
- Total known reported cost is safely below the 1.50 USD lane cap.

Apply-test summary:
- Stretch application was skipped to keep additional full-file generation and repository test-command spend out of the required lane budget.
- Documented in experiments/real_repos/starlette/runs/apply_summary.md.

Note:
- This checkout's `acg compile` CLI does not accept `--language python`; a cached Python context graph was generated per parent commit and compile was invoked with `--language auto` so it reused the graph without modifying forbidden `acg/` code. The generated checkout `.acg` cache was removed after the required artifacts were produced.
