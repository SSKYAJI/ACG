#!/usr/bin/env bash
# scripts/postcheck_write.sh
# Cascade post_write_code hook — emits a receipt confirming that the
# write was processed through ACG's Cascade hook integration.
#
# Cascade's hook contract (verified 2026-04-26 from
# https://docs.windsurf.com/windsurf/cascade/hooks):
#   - Hook input is a JSON object on stdin with
#     tool_info.file_path pointing at the written file and
#     tool_info.edits[] containing the diffs applied.
#   - post_write_code fires AFTER the write has landed; it cannot
#     block (exit code 2 has no blocking effect for post-hooks).
#   - stdout/stderr are shown in the Cascade UI when show_output
#     is true.
#
# This hook is purely informational: it logs the write receipt to
# show that ACG's Cascade integration is active. It never fails.
set -euo pipefail

FILE_PATH=""

# Parse file_path from Cascade's JSON stdin envelope.
if [[ ! -t 0 ]]; then
  STDIN_PAYLOAD="$(cat)"
  if [[ -n "${STDIN_PAYLOAD}" ]]; then
    if command -v jq >/dev/null 2>&1; then
      FILE_PATH="$(printf '%s' "${STDIN_PAYLOAD}" | jq -r '.tool_info.file_path // empty' 2>/dev/null)"
    fi
    if [[ -z "${FILE_PATH}" ]]; then
      FILE_PATH="$(
        printf '%s' "${STDIN_PAYLOAD}" \
          | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
          | head -n1
      )"
    fi
  fi
fi

# Also accept $1 for direct invocation / testing.
FILE_PATH="${FILE_PATH:-${1:-}}"

TASK_ID="${ACG_CURRENT_TASK:-}"
LOCK="${ACG_LOCK:-agent_lock.json}"

# Skip receipt when running outside an ACG task context.
if [[ -z "${TASK_ID}" || ! -f "${LOCK}" ]]; then
  exit 0
fi

# Normalise absolute paths to repo-relative.
if [[ -n "${FILE_PATH}" && "${FILE_PATH}" = /* ]]; then
  CWD_PREFIX="${PWD%/}/"
  if [[ "${FILE_PATH}" == "${CWD_PREFIX}"* ]]; then
    FILE_PATH="${FILE_PATH#"${CWD_PREFIX}"}"
  fi
fi

echo "[acg-hook] write receipt: ${FILE_PATH:-<unknown>} landed (task=${TASK_ID}, hook=post_write_code)"
exit 0
