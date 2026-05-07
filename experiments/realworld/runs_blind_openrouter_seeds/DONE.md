# DONE

Prompt-token reduction is deterministic for a fixed lockfile, task suite, and worker prompt. Across N=5 OpenRouter seeds on the RealWorld NestJS blind task suite, scoped planned execution reduced worker prompt context by 49.8% with zero variance across seeds.

## Key Numbers

- Reduction ratio `1 - acg_planned.tokens_prompt_total / acg_planned_full_context.tokens_prompt_total`: deterministic value `0.4979423868312757`, stdev `0.0`.
- `acg_planned.tokens_prompt_total`: deterministic value `1098.0`, stdev `0.0`.
- `acg_planned_full_context.tokens_prompt_total`: deterministic value `2187.0`, stdev `0.0`.
- `naive_parallel.tokens_prompt_total`: deterministic value `2187.0`, stdev `0.0`.
- `acg_planned.out_of_bounds_write_count`: observed `0.0` in every seed.
- `acg_planned.blocked_invalid_write_count`: observed `0.0` in every seed.
- `acg_planned_full_context.blocked_invalid_write_count`: mean `2.6`, stdev `0.5477225575051661`, 95% CI `2.2`-`3.0`.
- `naive_parallel.out_of_bounds_write_count`: mean `2.8`, stdev `0.8366600265340756`, 95% CI `2.2`-`3.4`.
- `acg_planned.cost_usd_total`: mean `0.000314934`, stdev `7.976088878642214e-05`, 95% CI `0.000254808`-`0.000376214`.
- `acg_planned_full_context.cost_usd_total`: mean `0.000406014`, stdev `8.932048158177384e-05`, 95% CI `0.00033999000000000003`-`0.000474518`.
- `naive_parallel.cost_usd_total`: mean `0.000376816`, stdev `7.940812634737075e-05`, 95% CI `0.00033806395`-`0.00044864`.
- `acg_planned.wall_time_seconds`: mean `21.3677`, stdev `8.098034416140747`, 95% CI `15.188559999999999`-`28.18494`.
- `acg_planned_full_context.wall_time_seconds`: mean `19.24718`, stdev `4.607649954911939`, 95% CI `16.10974`-`23.253619999999998`.
- `naive_parallel.wall_time_seconds`: mean `6.79056`, stdev `0.9116545963247261`, 95% CI `6.11608`-`7.4513704999999995`.

## Sanity Checks

- Seed 1 prompt-token counts: `acg_planned=1098`, `acg_planned_full_context=2187`, `naive_parallel=2187`; all are within 30% of the reference `1105` planned and `2128` naive/full-context values.
- Reduction-ratio mean `0.4979423868312757` is within the required `0.42`-`0.55` range.
- Tests: `./.venv/bin/python -m pytest tests/ -q` passed with `211 passed, 11 warnings in 6.85s`.
