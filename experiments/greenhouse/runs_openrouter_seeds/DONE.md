Greenhouse scoped prompt-token reduction across N=5 seeds was 9.71% with zero variance. This `--strategy both` run is superseded for paper claims by the clean 3-arm ablation rerun in `experiments/greenhouse/runs_openrouter_seeds_ablation/`.

Artifacts:

- `aggregate.json`
- `aggregate.md`
- `aggregate.png`
- `seed1/eval_run_combined.json`
- `seed2/eval_run_combined.json`
- `seed3/eval_run_combined.json`
- `seed4/eval_run_combined.json`
- `seed5/eval_run_combined.json`

Validation:

- `./.venv/bin/python -m pytest tests/ -q` passed with 211 tests and 11 warnings.
