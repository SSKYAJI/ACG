# RealWorld Blind OpenRouter Seed Aggregate

Prompt-token reduction is deterministic for a fixed lockfile, task suite, and worker prompt. Across N=5 OpenRouter seeds on the RealWorld NestJS blind task suite, scoped planned execution reduced worker prompt context by 49.8% with zero variance across seeds. Bootstrap intervals are reported only for stochastic measurements below.

## Deterministic Measurements

| metric | value across all seeds | stdev |
| --- | ---: | ---: |
| `acg_planned.tokens_prompt_total` | 1098 | 0.000000 |
| `acg_planned_full_context.tokens_prompt_total` | 2187 | 0.000000 |
| `naive_parallel.tokens_prompt_total` | 2187 | 0.000000 |
| `1 - acg_planned / acg_planned_full_context` | 0.497942 | 0.000000 |

## Stochastic Measurements

| strategy | metric | mean | stdev | 95% CI |
| --- | --- | ---: | ---: | ---: |
| `acg_planned` | `cost_usd_total` | 0.000315 | 0.000080 | 0.000255-0.000375 |
| `acg_planned` | `wall_time_seconds` | 21.367700 | 8.098034 | 14.665260-27.941092 |
| `acg_planned_full_context` | `blocked_invalid_write_count` | 2.600000 | 0.547723 | 2.200000-3.000000 |
| `acg_planned_full_context` | `cost_usd_total` | 0.000406 | 0.000089 | 0.000341-0.000475 |
| `acg_planned_full_context` | `wall_time_seconds` | 19.247180 | 4.607650 | 16.135820-23.411440 |
| `naive_parallel` | `out_of_bounds_write_count` | 2.800000 | 0.836660 | 2.200000-3.400000 |
| `naive_parallel` | `cost_usd_total` | 0.000377 | 0.000079 | 0.000338-0.000449 |
| `naive_parallel` | `wall_time_seconds` | 6.790560 | 0.911655 | 6.116080-7.451020 |

## Zero-Variance Safety Observations

| strategy | metric | value across all seeds |
| --- | --- | ---: |
| `acg_planned` | `out_of_bounds_write_count` | 0 |
| `acg_planned` | `blocked_invalid_write_count` | 0 |
| `acg_planned_full_context` | `out_of_bounds_write_count` | 0 |
| `naive_parallel` | `blocked_invalid_write_count` | 0 |

## Seeds

| seed | acg_planned tokens | full_context tokens | naive tokens | reduction ratio |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 1098 | 2187 | 2187 | 0.497942 |
| 2 | 1098 | 2187 | 2187 | 0.497942 |
| 3 | 1098 | 2187 | 2187 | 0.497942 |
| 4 | 1098 | 2187 | 2187 | 0.497942 |
| 5 | 1098 | 2187 | 2187 | 0.497942 |
