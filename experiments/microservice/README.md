# Microservice benchmark

This fixture adds a production-style TypeScript backend benchmark using [`brocoders/nestjs-boilerplate`](https://github.com/brocoders/nestjs-boilerplate) on commit `dd0034750fc7f6ec15712afbecf50fa9828018a2`.

The upstream checkout is intentionally not committed. Recreate it with:

```bash
bash experiments/microservice/setup.sh
```

Pinned artifacts:

- `tasks_brocoders.json` — seven realistic NestJS backend tasks.
- `agent_lock_brocoders.json` — compiled ACG lockfile: 7 tasks, 5 execution groups, 11 conflict pairs.
- `runs_brocoders_mock/eval_run_combined.json` — deterministic mock run.
- `runs_brocoders_local/eval_run_combined.json` — local llama.cpp/GX10 run.
- `runs_brocoders_analysis.{md,json}` — Brocoders-only analyzer report.

The local run is best read as a planning/context-scaling benchmark: the worker prompt context drops from 3721 to 1700 tokens, but the local model under-proposed concrete writes for several tasks.
