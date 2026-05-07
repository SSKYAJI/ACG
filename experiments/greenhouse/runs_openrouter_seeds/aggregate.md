# Lane B Greenhouse OpenRouter Seed Aggregate

Deterministic scoped prompt-token reduction across N=5 seeds: 9.71%. This earlier `--strategy both` run is retained for provenance and is superseded for paper claims by the clean 3-arm ablation rerun in `experiments/greenhouse/runs_openrouter_seeds_ablation/`.

- Base directory: `experiments/greenhouse/runs_openrouter_seeds`
- Seed directories found: 5
- Deterministic prompt-token quantities: fixed by strategy prompt construction; variance is zero and no bootstrap CI is reported.
- Stochastic metrics: bootstrap intervals use 10000 paired resamples, seed 20260506.

## Prompt-Token Reduction

| Seed | Full-context source | Reduction | Variance treatment |
| --- | --- | ---: | --- |
| seed1 | `naive_parallel` | 9.71% | deterministic, zero variance |
| seed2 | `naive_parallel` | 9.71% | deterministic, zero variance |
| seed3 | `naive_parallel` | 9.71% | deterministic, zero variance |
| seed4 | `naive_parallel` | 9.71% | deterministic, zero variance |
| seed5 | `naive_parallel` | 9.71% | deterministic, zero variance |

Pure scope reduction: `1 - acg_planned / naive_parallel` = 9.71%.

## Prompt-Token Aggregates

| Strategy | N | Mean | Stdev | Variance treatment |
| --- | ---: | ---: | ---: | --- |
| `acg_planned` | 5 | 2018.00 | 0.000000 | deterministic, zero variance; no CI |
| `naive_parallel` | 5 | 2235.00 | 0.000000 | deterministic, zero variance; no CI |

## Stochastic Metric Aggregates

| Strategy | Metric | N | Mean | Stdev | Bootstrap 95% CI |
| --- | --- | ---: | ---: | ---: | ---: |
| `acg_planned` | `cost_usd_total` | 5 | 0.000223 | 0.000001 | 0.000222 to 0.000223 |
| `acg_planned` | `wall_time_seconds` | 5 | 9.7909 | 2.1669 | 8.1154 to 11.3953 |
| `naive_parallel` | `cost_usd_total` | 5 | 0.000349 | 0.000249 | 0.000236 to 0.000572 |
| `naive_parallel` | `wall_time_seconds` | 5 | 5.2647 | 2.1642 | 3.5038 to 6.9259 |

## Zero-Variance Safety Observations

| Strategy | Metric | N | Value |
| --- | --- | ---: | ---: |
| `acg_planned` | `out_of_bounds_write_count` | 5 | 0.000000 |
| `acg_planned` | `blocked_invalid_write_count` | 5 | 0.000000 |
| `naive_parallel` | `out_of_bounds_write_count` | 5 | 0.000000 |
| `naive_parallel` | `blocked_invalid_write_count` | 5 | 0.000000 |
