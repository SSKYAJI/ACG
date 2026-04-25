# Cascade Hook — Stretch Goal Plan

A stretch-goal plan for wiring a real Windsurf Cascade `pre_write_code` hook on top of the script emulator that ships in v1; do this only if the primary build (CLI + lockfile + emulator + benchmark + video) is complete with at least 2 hours of buffer.

---

## What this is

The script-based enforcement emulator (`acg/enforce.py`) ships in v1 and is enough to demo. This file describes the **upgrade path** to a real Windsurf Cascade hook that fires before any Cascade-driven file write. If wired, the demo can show Cascade itself blocking an out-of-bounds write — which is materially more impressive than a Python wrapper doing it.

---

## When to attempt this

**Do not start until ALL of these are true:**

- v1 CLI compiles a real lockfile from real `tasks.json` against real `demo-app`
- v1 emulator demonstrably blocks an illegal write
- Benchmark chart populated with real numbers
- Video script written
- At least 2 hours of buffer remain before submission cutoff

If any of those are false, **skip this entirely**. The emulator is enough.

---

## Architecture

```
[ Cascade attempts to write file X ]
              │
              ▼
[ Hook script reads agent_lock.json + current task context ]
              │
              ▼
[ Hook script calls `acg validate-write --task <id> --path X` ]
              │
              ▼
[ Exit code 0 → allow. Exit code != 0 → block, print reason. ]
```

---

## Files to add

```
.windsurf/
└── hooks.json               (Cascade config)
scripts/
└── precheck_write.sh        (the hook script)
```

---

## .windsurf/hooks.json (skeleton)

```json
{
  "version": "1.0",
  "hooks": {
    "pre_write_code": {
      "command": "./scripts/precheck_write.sh",
      "args": ["${WRITE_PATH}", "${WRITE_CONTENT_PATH}"],
      "block_on_nonzero_exit": true,
      "timeout_ms": 5000
    }
  }
}
```

Verify the exact field names and env-var names against `docs.windsurf.com` before committing. If the docs disagree, adapt to whatever the docs actually say.

---

## scripts/precheck_write.sh

```bash
#!/usr/bin/env bash
set -euo pipefail

WRITE_PATH="${1:-}"
WRITE_CONTENT_PATH="${2:-}"
TASK_ID="${ACG_CURRENT_TASK:-unknown}"

if [[ -z "${WRITE_PATH}" ]]; then
  echo "[acg-hook] missing write path; allowing" >&2
  exit 0
fi

if ! acg validate-write \
      --lock agent_lock.json \
      --task "${TASK_ID}" \
      --path "${WRITE_PATH}" \
      --quiet; then
  echo ""
  echo "[acg-hook] BLOCKED: ${WRITE_PATH} is outside task ${TASK_ID}'s allowed_paths"
  echo "[acg-hook] See agent_lock.json for the allowed write boundary."
  exit 2
fi

exit 0
```

---

## Acceptance criteria

- Cascade attempts to write a path that violates the lockfile → hook blocks with exit code 2 → user sees "[acg-hook] BLOCKED" message
- Cascade attempts a legal write → hook exits 0 → write proceeds normally
- Hook completes in under 200 ms (otherwise it stalls Cascade noticeably)
- `ACG_CURRENT_TASK` env var is settable from the user's shell so the demo can swap tasks between recordings

---

## Risk register

| Risk | Mitigation |
|---|---|
| Windsurf hook docs may not match actual behavior | Test with a no-op hook first (just `echo + exit 0`); confirm Cascade actually invokes it before integrating ACG logic |
| `WRITE_PATH` env var name may differ | Read the actual hook docs at runtime; do not trust this plan |
| Hook may fire on Cascade's own internal writes (config, history) | Add an early-return for paths under `.windsurf/` |
| Exit code 2 may not block in all Cascade versions | Try exit codes 1, 2, 99; whichever blocks, use |

---

## Demo upgrade if this works

Replace this segment of the video (1:20–1:50, "show enforcement"):

> Old: a Python script wrapper attempts a write, our emulator says BLOCKED.

With:

> New: a Cascade chat attempts a write through Windsurf's normal edit flow. The Cascade pre_write_code hook fires, runs our validator, and Cascade itself displays "BLOCKED — outside this task's allowed_paths" with the message we authored.

That's the materially-more-impressive version. Worth the 2 hours **only if v1 is already done**.

---

## Fallback if hook does not work

Keep the emulator. Reword that segment to say honestly:

> "Our enforcement layer wraps file edits and blocks out-of-bounds writes. The Windsurf hook integration is designed for production deployment but uses the same validator under the hood."

That is fine. Do not panic if this stretch fails.
