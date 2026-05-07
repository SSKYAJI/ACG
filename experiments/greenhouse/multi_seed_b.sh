#!/usr/bin/env bash
set -euo pipefail

set -a && . ./.env && set +a

for i in 1 2 3 4 5; do
  ./.venv/bin/python -m experiments.greenhouse.headtohead \
    --lock experiments/greenhouse/agent_lock.json \
    --tasks experiments/greenhouse/tasks.json \
    --repo experiments/greenhouse/checkout \
    --backend local --strategy both \
    --out-dir experiments/greenhouse/runs_openrouter_seeds/seed${i} \
    --suite-name greenhouse-java6-openrouter-seed${i}
  sleep 5
done
