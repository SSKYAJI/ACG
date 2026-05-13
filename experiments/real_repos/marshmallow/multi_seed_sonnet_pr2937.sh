#!/usr/bin/env bash
# N=5 seeds x comparison_full on the validated marshmallow PR #2937 only.
# This avoids combined-lock parent/checkout drift and bootstraps a real editable
# test environment before scoring.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

REPO="experiments/real_repos/marshmallow/checkout"
LOCK="experiments/real_repos/marshmallow/agent_lock_pr-2937.json"
TASKS="experiments/real_repos/marshmallow/tasks_canary.json"
BASE_OUT="experiments/real_repos/marshmallow/runs_sonnet_test_gate_pr2937_n5"
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

ensure_test_env() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "DRY-RUN: add local git excludes for .venv/cache files"
    echo "DRY-RUN: bootstrap $REPO/.venv with editable marshmallow[tests]"
    return 0
  fi

  mkdir -p "$REPO/.git/info"
  touch "$REPO/.git/info/exclude"
  for pattern in ".venv/" ".pytest_cache/" "__pycache__/" "*.pyc"; do
    if ! grep -qxF "$pattern" "$REPO/.git/info/exclude"; then
      printf '%s\n' "$pattern" >>"$REPO/.git/info/exclude"
    fi
  done

  if [[ ! -f "$REPO/.venv/pyvenv.cfg" || ! -x "$REPO/.venv/bin/python" ]]; then
    rm -rf "$REPO/.venv"
    "${PYTHON:-python3}" -m venv "$REPO/.venv"
  fi
  "$REPO/.venv/bin/python" -m pip install --quiet --upgrade pip
  "$REPO/.venv/bin/python" -m pip install --quiet -e "$REPO[tests]"
  "$REPO/.venv/bin/python" - <<'PY'
from pathlib import Path
import marshmallow

path = Path(marshmallow.__file__).resolve()
expected = Path("experiments/real_repos/marshmallow/checkout/src/marshmallow").resolve()
if expected not in path.parents:
    raise SystemExit(f"marshmallow imports from {path}, expected under {expected}")
PY
}

run_seed() {
  local seed="$1"
  local attempt="$2"
  local out_dir="$BASE_OUT/seed${seed}"
  local suite_name="marshmallow-pr2937-sonnet-seed${seed}"

  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "DRY-RUN: mkdir -p \"$out_dir\""
    echo "DRY-RUN: set -a; . ./.env; set +a"
    echo "DRY-RUN: export ACG_SEED=${seed}"
    echo "DRY-RUN: ./.venv/bin/python -m experiments.greenhouse.headtohead --lock $LOCK --tasks $TASKS --repo $REPO --backend local --strategy \"${ACG_STRATEGY:-comparison_full}\" --applied-diff-live --out-dir \"$out_dir\" --suite-name \"$suite_name\""
    return 0
  fi

  mkdir -p "$out_dir"
  set +e
  {
    local _parent_llm_model="${ACG_LLM_MODEL:-}"
    local _parent_llm_url="${ACG_LLM_URL:-}"
    local _parent_llm_api_key="${ACG_LLM_API_KEY:-}"
    local _parent_worker_max_tokens="${ACG_WORKER_MAX_TOKENS:-}"
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
    if [[ -n "$_parent_llm_model" ]]; then ACG_LLM_MODEL="$_parent_llm_model"; fi
    if [[ -n "$_parent_llm_url" ]]; then ACG_LLM_URL="$_parent_llm_url"; fi
    if [[ -n "$_parent_llm_api_key" ]]; then ACG_LLM_API_KEY="$_parent_llm_api_key"; fi
    if [[ -n "$_parent_worker_max_tokens" ]]; then ACG_WORKER_MAX_TOKENS="$_parent_worker_max_tokens"; fi
    export ACG_LLM_URL="${ACG_LLM_URL:-https://api.anthropic.com/v1}"
    export ACG_LLM_MODEL="${ACG_LLM_MODEL:-claude-sonnet-4-6}"
    export ACG_LLM_URL ACG_LLM_API_KEY ACG_WORKER_MAX_TOKENS
    export ACG_SEED="${seed}"
    ./.venv/bin/python -m experiments.greenhouse.headtohead \
      --lock "$LOCK" \
      --tasks "$TASKS" \
      --repo "$REPO" \
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
  ensure_test_env
  for seed in 1 2 3 4 5; do
    run_seed "$seed" 1
  done
  exit 0
fi

mkdir -p "$BASE_OUT"
rm -f "$FAILURE"

echo "Estimated cost: several USD for 5 seeds x comparison_full x PR #2937."
if [[ "$AUTO_YES" -eq 1 ]]; then
  echo "AUTO_YES set (--yes / ACG_AUTO_CONFIRM=1) - proceeding without prompt."
else
  read -r -p "Type y to continue, anything else aborts: " consent
  if [[ "${consent:-}" != "y" && "${consent:-}" != "Y" ]]; then
    echo "Aborted."
    exit 1
  fi
fi

ensure_test_env

for seed in 1 2 3 4 5; do
  for attempt in 1 2; do
    if run_seed "$seed" "$attempt"; then
      break
    fi
    if [[ "$attempt" == "2" ]]; then
      {
        echo "# FAILURE"
        echo
        echo "Seed ${seed} failed twice while running marshmallow PR #2937."
        echo
        echo '```text'
        tail -n 120 "${BASE_OUT}/seed${seed}/run_attempt${attempt}.log" || true
        echo '```'
      } >"$FAILURE"
      exit 1
    fi
  done
done
