#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

BASE_OUT="experiments/realworld/runs_blind_openrouter_seeds"
FAILURE="$BASE_OUT/FAILURE.md"

mkdir -p "$BASE_OUT"
rm -f "$FAILURE"

run_seed() {
  local seed="$1"
  local attempt="$2"
  local out_dir="$BASE_OUT/seed${seed}"
  local suite_name="realworld-nestjs-blind-openrouter-seed${seed}"

  mkdir -p "$out_dir"

  set +e
  {
    set -a
    . ./.env
    set +a
    ./.venv/bin/python -m experiments.greenhouse.headtohead \
      --lock experiments/realworld/agent_lock_blind.json \
      --tasks experiments/realworld/tasks_blind.json \
      --repo experiments/realworld/checkout \
      --backend local --strategy ablation \
      --out-dir "$out_dir" \
      --suite-name "$suite_name"
  } >"$out_dir/run_attempt${attempt}.log" 2>&1
  local status=$?
  set -e
  return "$status"
}

for seed in 1 2 3 4 5; do
  for attempt in 1 2; do
    if run_seed "$seed" "$attempt"; then
      break
    fi

    if [[ "$attempt" == "2" ]]; then
      {
        echo "# FAILURE"
        echo
        echo "Seed ${seed} failed twice while running RealWorld blind OpenRouter ablation."
        echo
        echo "## Trace"
        echo
        echo "- seed: ${seed}"
        echo "- out_dir: ${BASE_OUT}/seed${seed}"
        echo "- failed_attempt_log: ${BASE_OUT}/seed${seed}/run_attempt${attempt}.log"
        echo
        echo '```text'
        tail -n 120 "${BASE_OUT}/seed${seed}/run_attempt${attempt}.log" || true
        echo '```'
      } >"$FAILURE"
      exit 1
    fi
  done

  if [[ "$seed" != "5" ]]; then
    sleep 5
  fi
done
