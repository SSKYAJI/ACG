# Lane B Greenhouse OpenRouter Ablation Seed Aggregate

Deterministic scoped prompt-token reduction across N=5 seeds: 9.71%.

- Base directory: `experiments/greenhouse/runs_openrouter_seeds_ablation`
- Seed directories found: 5
- Deterministic prompt-token quantities: fixed by strategy prompt construction; variance is zero and no bootstrap CI is reported.
- Stochastic metrics: bootstrap intervals use 10000 paired resamples, seed 20260506.

## Prompt-Token Reduction

| Seed | Full-context source | Reduction | Variance treatment |
| --- | --- | ---: | --- |
| seed1 | `acg_planned_full_context` | 9.71% | deterministic, zero variance |
| seed2 | `acg_planned_full_context` | 9.71% | deterministic, zero variance |
| seed3 | `acg_planned_full_context` | 9.71% | deterministic, zero variance |
| seed4 | `acg_planned_full_context` | 9.71% | deterministic, zero variance |
| seed5 | `acg_planned_full_context` | 9.71% | deterministic, zero variance |

Pure scope reduction: `1 - acg_planned / acg_planned_full_context` = 9.71%.

## Prompt-Token Aggregates

| Strategy | N | Mean | Stdev | Variance treatment |
| --- | ---: | ---: | ---: | --- |
| `acg_planned` | 5 | 2018.00 | 0.000000 | deterministic, zero variance; no CI |
| `acg_planned_full_context` | 5 | 2235.00 | 0.000000 | deterministic, zero variance; no CI |
| `naive_parallel` | 5 | 2235.00 | 0.000000 | deterministic, zero variance; no CI |

## Stochastic Metric Aggregates

| Strategy | Metric | N | Mean | Stdev | Bootstrap 95% CI |
| --- | --- | ---: | ---: | ---: | ---: |
| `acg_planned` | `cost_usd_total` | 5 | 0.000269 | 0.000108 | 0.000221 to 0.000366 |
| `acg_planned` | `wall_time_seconds` | 5 | 9.6765 | 5.5096 | 6.0102 to 14.5709 |
| `acg_planned_full_context` | `cost_usd_total` | 5 | 0.000237 | 0.000001 | 0.000236 to 0.000238 |
| `acg_planned_full_context` | `wall_time_seconds` | 5 | 11.1129 | 2.4148 | 9.2238 to 13.0019 |
| `naive_parallel` | `cost_usd_total` | 5 | 0.000237 | 0.000002 | 0.000236 to 0.000238 |
| `naive_parallel` | `wall_time_seconds` | 5 | 4.5148 | 1.6964 | 3.1642 to 5.7087 |

## Zero-Variance Safety Observations

| Strategy | Metric | N | Value |
| --- | --- | ---: | ---: |
| `acg_planned` | `out_of_bounds_write_count` | 5 | 0.000000 |
| `acg_planned` | `blocked_invalid_write_count` | 5 | 0.000000 |
| `acg_planned_full_context` | `out_of_bounds_write_count` | 5 | 0.000000 |
| `acg_planned_full_context` | `blocked_invalid_write_count` | 5 | 0.000000 |
| `naive_parallel` | `out_of_bounds_write_count` | 5 | 0.000000 |
| `naive_parallel` | `blocked_invalid_write_count` | 5 | 0.000000 |
