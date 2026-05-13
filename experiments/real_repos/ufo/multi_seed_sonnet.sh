#!/usr/bin/env bash
# N=5 seeds x comparison_full (5 strategies) on ufo + Claude Sonnet 4.6 via Anthropic-direct.
# ufo is a TypeScript pnpm package; vitest test runner.
# Do not run unattended: estimated cost is several USD per full suite.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

BASE_OUT="experiments/real_repos/ufo/runs_sonnet_test_gate_n5"
FAILURE="$BASE_OUT/FAILURE.md"

DRY_RUN=0
AUTO_YES=0
for arg in "$@"; do
  if [[ "$arg" == "--dry-run" || "$arg" == "-n" ]]; then
    DRY_RUN=1
  fi
  if [[ "$arg" == "--yes" || "$arg" == "-y" ]]; then
    AUTO_YES=1
  fi
done
if [[ "${ACG_AUTO_CONFIRM:-}" == "1" ]]; then
  AUTO_YES=1
fi

run_seed() {
  local seed="$1"
  local attempt="$2"
  local out_dir="$BASE_OUT/seed${seed}"
  local suite_name="ufo-sonnet-test-gate-n5-seed${seed}"

  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "DRY-RUN: mkdir -p \"$out_dir\""
    echo "DRY-RUN: ( seed=$seed attempt=$attempt suite_name=$suite_name )"
    echo "DRY-RUN: set -a; . ./.env; set +a"
    echo "DRY-RUN: export ACG_LLM_URL=\${ACG_LLM_URL:-https://api.anthropic.com/v1}"
    echo "DRY-RUN: export ACG_LLM_MODEL=\${ACG_LLM_MODEL:-claude-sonnet-4-6}"
    echo "DRY-RUN: export ACG_SEED=${seed}"
    echo "DRY-RUN: ./.venv/bin/python -m experiments.greenhouse.headtohead \\"
    echo "DRY-RUN:   --lock experiments/real_repos/ufo/agent_lock_combined.json \\"
    echo "DRY-RUN:   --tasks experiments/real_repos/ufo/tasks_combined.json \\"
    echo "DRY-RUN:   --repo experiments/real_repos/ufo/checkout \\"
    echo "DRY-RUN:   --backend local --strategy \"${ACG_STRATEGY:-comparison_full}\" \\"
    echo "DRY-RUN:   --applied-diff-live \\"
    echo "DRY-RUN:   --out-dir \"$out_dir\" \\"
    echo "DRY-RUN:   --suite-name \"$suite_name\""
    echo "DRY-RUN: log: \"$out_dir/run_attempt${attempt}.log\""
    return 0
  fi

  mkdir -p "$out_dir"
  set +e
  {
    # Capture parent-shell overrides BEFORE sourcing .env so callers can
    # swap worker model / URL / key on the command line.
    local _parent_llm_model="${ACG_LLM_MODEL:-}"
    local _parent_llm_url="${ACG_LLM_URL:-}"
    local _parent_llm_api_key="${ACG_LLM_API_KEY:-}"
    local _parent_worker_max_tokens="${ACG_WORKER_MAX_TOKENS:-}"
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
    if [[ -n "$_parent_llm_model" ]]; then
      ACG_LLM_MODEL="$_parent_llm_model"
    fi
    if [[ -n "$_parent_llm_url" ]]; then
      ACG_LLM_URL="$_parent_llm_url"
    fi
    if [[ -n "$_parent_llm_api_key" ]]; then
      ACG_LLM_API_KEY="$_parent_llm_api_key"
    fi
    if [[ -n "$_parent_worker_max_tokens" ]]; then
      ACG_WORKER_MAX_TOKENS="$_parent_worker_max_tokens"
    fi
    export ACG_LLM_URL="${ACG_LLM_URL:-https://api.anthropic.com/v1}"
    export ACG_LLM_MODEL="${ACG_LLM_MODEL:-claude-sonnet-4-6}"
    export ACG_LLM_URL ACG_LLM_API_KEY
    export ACG_SEED="${seed}"
    ./.venv/bin/python -m experiments.greenhouse.headtohead \
      --lock experiments/real_repos/ufo/agent_lock_combined.json \
      --tasks experiments/real_repos/ufo/tasks_combined.json \
      --repo experiments/real_repos/ufo/checkout \
      --backend local --strategy "${ACG_STRATEGY:-comparison_full}" \
      --applied-diff-live \
      --out-dir "$out_dir" \
      --suite-name "$suite_name"
  } >"$out_dir/run_attempt${attempt}.log" 2>&1
  local status=$?
  set -e
  return "$status"
}

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "DRY-RUN: would use BASE_OUT=$BASE_OUT"
  for seed in 1 2 3 4 5; do
    for attempt in 1 2; do
      run_seed "$seed" "$attempt" && break
      if [[ "$attempt" == "2" ]]; then
        echo "DRY-RUN: on failure would write $FAILURE and exit 1"
      fi
    done
    [[ "$seed" != "5" ]] && echo "DRY-RUN: sleep 10"
  done
  exit 0
fi

mkdir -p "$BASE_OUT"
rm -f "$FAILURE"

echo "Estimated cost: several USD for 5 seeds x comparison_full x 1 task (varies by pricing)."
echo "Default: Claude Sonnet 4.6 via Anthropic-direct (see .env.example). Override via env, e.g.:"
echo "  ACG_LLM_URL=... ACG_LLM_API_KEY=... ACG_LLM_MODEL=claude-sonnet-4-6"
if [[ "$AUTO_YES" -eq 1 ]]; then
  echo "AUTO_YES set (--yes / ACG_AUTO_CONFIRM=1) — proceeding without prompt."
else
  read -r -p "Type y to continue, anything else aborts: " consent
  if [[ "${consent:-}" != "y" && "${consent:-}" != "Y" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

for seed in 1 2 3 4 5; do
  for attempt in 1 2; do
    if run_seed "$seed" "$attempt"; then
      break
    fi

    if [[ "$attempt" == "2" ]]; then
      {
        echo "# FAILURE"
        echo
        echo "Seed ${seed} failed twice while running ufo Sonnet comparison_full."
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
    sleep 10
  fi
done
