#!/usr/bin/env bash
# One-task smoke: PR 3148 (first task in agent_lock_combined) + acg_planned + applied-diff-live.
# Caps wall clock at 10 minutes. Requires .env with OpenRouter / ACG_LLM_* configured.
#
# TODO: ACG_OPENROUTER_MAX_COST_USD=0.50 — cost ceiling is not implemented in acg/runtime yet.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
export ROOT
cd "$ROOT"

OUT_DIR="experiments/real_repos/starlette/runs_smoke"
SUITE_NAME="starlette-smoke-pr3148"
export OUT_DIR SUITE_NAME

DRY_RUN=0
for arg in "$@"; do
  if [[ "$arg" == "--dry-run" || "$arg" == "-n" ]]; then
    DRY_RUN=1
  fi
done

_smoke_inner() {
  set -euo pipefail
  cd "$ROOT"
  # Capture parent-shell overrides BEFORE sourcing .env so callers can
  # swap the worker model / URL / key on the command line:
  #   ACG_LLM_URL=https://api.anthropic.com/v1/openai \
  #   ACG_LLM_API_KEY=sk-ant-... \
  #   ACG_LLM_MODEL=claude-sonnet-4-5 \
  #     bash smoke.sh
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
  export ACG_LLM_MODEL="${ACG_LLM_MODEL:-moonshotai/kimi-k2.6}"
  export ACG_LLM_URL ACG_LLM_API_KEY
  # Kimi K2.6 emits long reasoning-style preambles before structured output,
  # so applied-diff tasks (which need full file content) routinely blow past
  # 8k tokens. Cap is intentionally generous; the harness still surfaces a
  # ``proposal_status=truncated`` row if even this isn't enough.
  export ACG_WORKER_MAX_TOKENS="${ACG_WORKER_MAX_TOKENS:-32768}"
  mkdir -p "$OUT_DIR"
  ./.venv/bin/python -m experiments.greenhouse.headtohead \
    --lock experiments/real_repos/starlette/agent_lock_combined.json \
    --tasks experiments/real_repos/starlette/tasks_combined.json \
    --repo experiments/real_repos/starlette/checkout \
    --backend local \
    --strategy acg_planned \
    --smoke \
    --applied-diff-live \
    --out-dir "$OUT_DIR" \
    --suite-name "$SUITE_NAME"
}

export -f _smoke_inner

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "DRY-RUN: timeout 600 bash -lc '_smoke_inner'"
  echo "DRY-RUN: (sources .env, ACG_WORKER_MAX_TOKENS from .env or default 32768, headtohead --smoke --applied-diff-live)"
  echo "DRY-RUN: OUT_DIR=$OUT_DIR"
  exit 0
fi

# Pick a portable timeout: GNU coreutils ships ``timeout`` on Linux;
# macOS ships ``gtimeout`` (via ``brew install coreutils``) or nothing.
# Fall back to running without an external cap when neither is on PATH —
# the harness already honors ACG_REQUEST_TIMEOUT_S internally so the run
# is still bounded.
if command -v timeout >/dev/null 2>&1; then
  _TIMEOUT_CMD=(timeout 600)
elif command -v gtimeout >/dev/null 2>&1; then
  _TIMEOUT_CMD=(gtimeout 600)
else
  _TIMEOUT_CMD=()
  echo "warning: no 'timeout' or 'gtimeout' on PATH; running without external wall cap" >&2
fi

echo "Smoke: 10m wall cap, output -> $OUT_DIR"
start=$(date +%s)
# ``set -u`` makes ``"${arr[@]}"`` fail on empty arrays, so use the
# ``${arr[@]+...}`` indirection: expands to nothing when the array is unset
# or empty, expands normally otherwise.
if ! ${_TIMEOUT_CMD[@]+"${_TIMEOUT_CMD[@]}"} bash -lc '_smoke_inner'; then
  echo "FAIL smoke (timeout or headtohead error)"
  exit 1
fi
end=$(date +%s)
wall=$((end - start))

eval_json="$OUT_DIR/eval_run_acg.json"
if [[ ! -f "$eval_json" ]]; then
  echo "FAIL: missing $eval_json"
  exit 1
fi

# ``readarray`` is bash 4+; macOS ships bash 3.2 by default. Use a portable
# while-read loop so the smoke runs on a stock macOS shell too.
lines=()
while IFS= read -r _line; do
  lines+=("$_line")
done < <(./.venv/bin/python - <<PY
import json
from pathlib import Path
p = Path("$eval_json")
d = json.loads(p.read_text())
m = d.get("summary_metrics") or {}
print(m.get("task_completion_rate"))
print(m.get("cost_usd_total"))
print(m.get("wall_time_seconds"))
print(d.get("strategy", ""))
print(m.get("proposal_status_counts") or {})
print(m.get("typecheck_pass_count"))
print(m.get("typecheck_fail_count"))
PY
)
tc="${lines[0]:-}"
cost="${lines[1]:-}"
wall_h="${lines[2]:-}"
strat="${lines[3]:-}"
status_counts="${lines[4]:-}"
tcp="${lines[5]:-}"
tcf="${lines[6]:-}"

echo "--- summary ---"
echo "strategy: $strat"
echo "task_completion_rate: $tc"
echo "typecheck_pass_count: $tcp"
echo "typecheck_fail_count: $tcf"
echo "proposal_status_counts: $status_counts"
echo "cost_usd_total: $cost"
echo "harness_wall_time_seconds: $wall"
echo "reported_wall_time_seconds: $wall_h"
