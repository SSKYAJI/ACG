#!/usr/bin/env bash
# scripts/precheck_write.sh
# Cascade pre_write_code hook — defers to ACG's write validator.
#
# Cascade's hook contract (verified 2026-04-25 from
# https://docs.windsurf.com/windsurf/cascade/hooks):
#   - Hook input is a JSON object on stdin with
#     tool_info.file_path pointing at the write target.
#   - Exit code 2 blocks the write; any other exit allows it.
#
# For ergonomics (and to keep the test harness simple), this script
# also accepts the write path as $1 and only falls back to parsing
# stdin JSON when no positional argument is given. Cascade never
# passes a positional argument, so the stdin path is the production
# hot path.
#
# ACG_LOCK and ACG_CURRENT_TASK env vars locate the lockfile and the
# task being executed. Both must be set for the hook to enforce; when
# either is missing we soft-fail (allow) so the hook never accidentally
# blocks writes outside of a planned ACG run.
set -euo pipefail

WRITE_PATH="${1:-}"

# When invoked by Cascade, the file path arrives via JSON on stdin.
# Parse it out without taking a dependency on jq/python — the hook
# runs on every write and Python startup alone would blow the Cascade
# timeout budget on cold disks.
if [[ -z "${WRITE_PATH}" && ! -t 0 ]]; then
  STDIN_PAYLOAD="$(cat)"
  if [[ -n "${STDIN_PAYLOAD}" ]]; then
    WRITE_PATH="$(
      printf '%s' "${STDIN_PAYLOAD}" \
        | sed -n 's/.*"file_path"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        | head -n1
    )"
  fi
fi

LOCK="${ACG_LOCK:-agent_lock.json}"
TASK_ID="${ACG_CURRENT_TASK:-}"

# Normalise: if Cascade handed us an absolute path under $PWD, strip
# the prefix so the path matches the lockfile's repo-relative globs.
if [[ -n "${WRITE_PATH}" && "${WRITE_PATH}" = /* ]]; then
  CWD_PREFIX="${PWD%/}/"
  if [[ "${WRITE_PATH}" == "${CWD_PREFIX}"* ]]; then
    WRITE_PATH="${WRITE_PATH#"${CWD_PREFIX}"}"
  fi
fi

# Allow Cascade's own internal writes (config, history, caches) without
# bothering the validator.
case "${WRITE_PATH}" in
  .windsurf/*|.git/*|.acg/*)
    exit 0
    ;;
esac

# Soft-fail when the hook is invoked outside an ACG task context: no
# lockfile or no current task means we can't enforce, so allow.
if [[ -z "${WRITE_PATH}" || -z "${TASK_ID}" || ! -f "${LOCK}" ]]; then
  echo "[acg-hook] no ACG task context (lock=${LOCK}, task=${TASK_ID:-unset}); allowing" >&2
  exit 0
fi

# Resolve the acg binary: prefer PATH, fall back to the repo venv.
if command -v acg >/dev/null 2>&1; then
  ACG_BIN="acg"
elif [[ -x "./.venv/bin/acg" ]]; then
  ACG_BIN="./.venv/bin/acg"
else
  echo "[acg-hook] could not find 'acg' on PATH or in ./.venv/bin/; allowing" >&2
  exit 0
fi

if "${ACG_BIN}" validate-write \
      --lock "${LOCK}" \
      --task "${TASK_ID}" \
      --path "${WRITE_PATH}" \
      --quiet; then
  exit 0
fi

cat >&2 <<EOF

[acg-hook] BLOCKED: ${WRITE_PATH} is outside task ${TASK_ID}'s allowed_paths.
[acg-hook] See ${LOCK} for the allowed write boundary, or unset
[acg-hook] ACG_CURRENT_TASK to disable enforcement temporarily.

EOF
exit 2
