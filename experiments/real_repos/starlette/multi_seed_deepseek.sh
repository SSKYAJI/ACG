#!/usr/bin/env bash
# N seeds × comparison_full (5 strategies) on Starlette + DeepSeek V4 Flash via OpenRouter.
# Baseline comparison target: Sonnet v2 runs (multi_seed_sonnet.sh → runs_sonnet_v2_n5).
# Do not run unattended: estimated cost depends on OpenRouter pricing for deepseek/deepseek-v4-flash.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

BASE_OUT="${BASE_OUT:-experiments/real_repos/starlette/runs_deepseek_n5}"
SEED_SUFFIX="${SEED_SUFFIX:-}"
FAILURE="$BASE_OUT/FAILURE.md"

SEEDS="${SEEDS:-1 2 3 4 5}"

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

_ACG_LLM_EXTRA_PARAMS_JSON_DEFAULT='{"reasoning":{"effort":"none","exclude":true},"include_reasoning":false}'

last_seed_in_list() {
  local _last=""
  local _s
  for _s in $SEEDS; do
    _last="$_s"
  done
  printf '%s' "$_last"
}

run_seed() {
  local seed="$1"
  local attempt="$2"
  local out_dir="$BASE_OUT/seed${seed}${SEED_SUFFIX}"
  local suite_name="starlette-deepseek-v4-flash-n5-seed${seed}"

  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "DRY-RUN: mkdir -p \"$out_dir\""
    echo "DRY-RUN: ( seed=$seed attempt=$attempt suite_name=$suite_name )"
    echo "DRY-RUN: set -a; . ./.env; set +a"
    echo "DRY-RUN: export ACG_LLM_URL=\${ACG_LLM_URL:-https://openrouter.ai/api/v1}"
    echo "DRY-RUN: export ACG_LLM_MODEL=\${ACG_LLM_MODEL:-deepseek/deepseek-v4-flash}"
    echo "DRY-RUN: export ACG_WORKER_MAX_TOKENS=\${ACG_WORKER_MAX_TOKENS:-64000}"
    echo "DRY-RUN: export ACG_SINGLE_AGENT_MAX_TOKENS=\${ACG_SINGLE_AGENT_MAX_TOKENS:-128000}"
    echo 'DRY-RUN: export ACG_LLM_EXTRA_PARAMS_JSON=${ACG_LLM_EXTRA_PARAMS_JSON:-'"$_ACG_LLM_EXTRA_PARAMS_JSON_DEFAULT"'}'
    echo "DRY-RUN: export ACG_SEED=${seed}"
    echo "DRY-RUN: ./.venv/bin/python -m experiments.greenhouse.headtohead \\"
    echo "DRY-RUN:   --lock experiments/real_repos/starlette/agent_lock_combined.json \\"
    echo "DRY-RUN:   --tasks experiments/real_repos/starlette/tasks_combined.json \\"
    echo "DRY-RUN:   --repo experiments/real_repos/starlette/checkout \\"
    echo "DRY-RUN:   --backend local --strategy \"${ACG_STRATEGY:-comparison_full}\" \\"
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
    local _parent_single_agent_max_tokens="${ACG_SINGLE_AGENT_MAX_TOKENS:-}"
    local _parent_llm_extra_params="${ACG_LLM_EXTRA_PARAMS_JSON:-}"
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
    if [[ -n "$_parent_single_agent_max_tokens" ]]; then
      ACG_SINGLE_AGENT_MAX_TOKENS="$_parent_single_agent_max_tokens"
    fi
    if [[ -n "$_parent_llm_extra_params" ]]; then
      ACG_LLM_EXTRA_PARAMS_JSON="$_parent_llm_extra_params"
    fi
    export ACG_LLM_URL="${ACG_LLM_URL:-https://openrouter.ai/api/v1}"
    export ACG_LLM_MODEL="${ACG_LLM_MODEL:-deepseek/deepseek-v4-flash}"
    export ACG_WORKER_MAX_TOKENS="${ACG_WORKER_MAX_TOKENS:-64000}"
    export ACG_SINGLE_AGENT_MAX_TOKENS="${ACG_SINGLE_AGENT_MAX_TOKENS:-128000}"
    export ACG_LLM_EXTRA_PARAMS_JSON="${ACG_LLM_EXTRA_PARAMS_JSON:-$_ACG_LLM_EXTRA_PARAMS_JSON_DEFAULT}"
    export ACG_LLM_URL ACG_LLM_API_KEY ACG_LLM_EXTRA_PARAMS_JSON ACG_WORKER_MAX_TOKENS ACG_SINGLE_AGENT_MAX_TOKENS
    export ACG_SEED="${seed}"
    ./.venv/bin/python -m experiments.greenhouse.headtohead \
      --lock experiments/real_repos/starlette/agent_lock_combined.json \
      --tasks experiments/real_repos/starlette/tasks_combined.json \
      --repo experiments/real_repos/starlette/checkout \
      --backend local --strategy "${ACG_STRATEGY:-comparison_full}" \
      --out-dir "$out_dir" \
      --suite-name "$suite_name"
  } >"$out_dir/run_attempt${attempt}.log" 2>&1
  local status=$?
  set -e
  return "$status"
}

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "========== DRY-RUN (no commands executed) =========="
  echo "BASE_OUT=$BASE_OUT"
  echo "ACG_LLM_URL effective default: https://openrouter.ai/api/v1"
  echo "ACG_LLM_MODEL effective default: deepseek/deepseek-v4-flash"
  echo "ACG_WORKER_MAX_TOKENS effective default: 64000"
  echo "ACG_SINGLE_AGENT_MAX_TOKENS effective default: 128000"
  echo "ACG_LLM_EXTRA_PARAMS_JSON default (reasoning off): ${_ACG_LLM_EXTRA_PARAMS_JSON_DEFAULT}"
  echo "SEEDS=${SEEDS}"
  echo "ACG_STRATEGY effective default: ${ACG_STRATEGY:-comparison_full}"
  echo "====================================================="
  echo "DRY-RUN: would use BASE_OUT=$BASE_OUT"
  _last="$(last_seed_in_list)"
  for seed in $SEEDS; do
    for attempt in 1 2; do
      run_seed "$seed" "$attempt" && break
      if [[ "$attempt" == "2" ]]; then
        echo "DRY-RUN: on failure would write $FAILURE and exit 1"
      fi
    done
    [[ "$seed" != "$_last" ]] && echo "DRY-RUN: sleep 10"
  done
  exit 0
fi

mkdir -p "$BASE_OUT"
rm -f "$FAILURE"

echo "Estimated cost: depends on OpenRouter pricing for deepseek/deepseek-v4-flash (5 seeds × comparison_full × 3 tasks by default)."
echo "Default: DeepSeek V4 Flash via OpenRouter (see .env.example). Override via env, e.g.:"
echo "  ACG_LLM_URL=https://openrouter.ai/api/v1 ACG_LLM_API_KEY=... ACG_LLM_MODEL=deepseek/deepseek-v4-flash"
echo "  ACG_WORKER_MAX_TOKENS=64000 ACG_LLM_EXTRA_PARAMS_JSON='...'"
if [[ "$AUTO_YES" -eq 1 ]]; then
  echo "AUTO_YES set (--yes / ACG_AUTO_CONFIRM=1) — proceeding without prompt."
else
  read -r -p "Type y to continue, anything else aborts: " consent
  if [[ "${consent:-}" != "y" && "${consent:-}" != "Y" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

_last="$(last_seed_in_list)"
for seed in $SEEDS; do
  for attempt in 1 2; do
    if run_seed "$seed" "$attempt"; then
      break
    fi

    if [[ "$attempt" == "2" ]]; then
      {
        echo "# FAILURE"
        echo
        echo "Seed ${seed} failed twice while running Starlette DeepSeek V4 Flash comparison_full."
        echo
        echo "## Trace"
        echo
        echo "- seed: ${seed}"
        echo "- out_dir: ${BASE_OUT}/seed${seed}${SEED_SUFFIX}"
        echo "- failed_attempt_log: ${BASE_OUT}/seed${seed}${SEED_SUFFIX}/run_attempt${attempt}.log"
        echo
        echo '```text'
        tail -n 120 "${BASE_OUT}/seed${seed}${SEED_SUFFIX}/run_attempt${attempt}.log" || true
        echo '```'
      } >"$FAILURE"
      exit 1
    fi
  done

  if [[ "$seed" != "$_last" ]]; then
    sleep 10
  fi
done
