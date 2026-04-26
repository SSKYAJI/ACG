> **Goal:** ship a real Windsurf Cascade hook that fires before any
> Cascade-driven file write, runs ACG's validator, and blocks
> out-of-bounds writes at the IDE layer. This upgrades the demo from
> "Python wrapper enforces" to "Cascade itself enforces."

---

## Project context

You are working on **`cognition`** — a Python+TypeScript repo whose product
is **ACG (Agent Context Graph)**. ACG ships a write-validator
(`acg validate-write` CLI + `acg.enforce.validate_write`) that returns
exit code `0` for ALLOWED writes and `2` for BLOCKED. Today the
validator is invoked by the runtime (`acg/runtime.py`) post-hoc
against worker proposals.

The stretch goal documented at
[`docs/plans/cascade-hook-stretch-308cc2.md`](../plans/cascade-hook-stretch-308cc2.md)
is to wire that validator into Cascade's `pre_write_code` hook so the
IDE itself displays a BLOCKED message before the write lands. The
plan was written before v1 shipped; v1 is now done, so this PR is in
scope.

Your job is to ship the script + Cascade config + integration tests +
demo doc — but **NOT** to record the upgraded demo video itself
(that's the human author's job).

## Repo state to assume

- `main` contains PR 1-4 + `init-graph` + (likely) PR 5/6/7. Your branch
  only adds new files under `.windsurf/`, `scripts/`, `tests/`, and one
  new doc + one README addition.
- `acg validate-write` accepts `--lock`, `--task`, `--path`, `--quiet`
  and exits 0 on ALLOWED, 2 on BLOCKED, 1 on user error. Read
  `acg/cli.py::cmd_validate_write` and `acg/enforce.py` to confirm
  before you depend on these codes.
- The CLI is installed as `./.venv/bin/acg` after `make install`. The
  hook script must NOT hard-code that path; it must resolve `acg` from
  `$PATH` first and fall back to `./.venv/bin/acg` second.

## Verify Cascade's hook contract first

Before writing any code, **read the Windsurf docs at
https://docs.windsurf.com** for the current `pre_write_code` hook
contract. The plan from 2026-04-25 expects:

- A config file at `.windsurf/hooks.json` (or possibly
  `.windsurf/hooks/pre_write_code.json` — verify which path Cascade
  reads).
- Substitution variables `${WRITE_PATH}` and `${WRITE_CONTENT_PATH}`
  injected by Cascade.
- An exit-code-based block contract (non-zero → block).
- A 5-second timeout budget.

If the docs disagree with the plan in any of these, **adapt to the
docs**, not the plan. Cite the exact doc URL in your PR description.

If Cascade does not currently support `pre_write_code` at all,
**skip this PR and document the finding** in
`docs/CASCADE_INTEGRATION.md` (just write a "currently blocked on
Cascade hook availability" stub) — do not invent a contract.

## Deliverables — file by file

### 1. `scripts/precheck_write.sh`

A POSIX-`bash` script (idempotent, side-effect-free, deterministic).

```bash
#!/usr/bin/env bash
# scripts/precheck_write.sh
# Cascade pre_write_code hook — defers to ACG's validator.
#
# Invocation contract (subject to Cascade's actual hook contract; see
# docs.windsurf.com): script receives the target write path as $1.
# ACG_LOCK and ACG_CURRENT_TASK env vars locate the lockfile and the
# task being executed. Exit code 0 allows the write; non-zero blocks.
set -euo pipefail

WRITE_PATH="${1:-}"
LOCK="${ACG_LOCK:-agent_lock.json}"
TASK_ID="${ACG_CURRENT_TASK:-}"

# Allow Cascade's own internal writes (config, history) without bothering
# the validator.
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

# Resolve the acg binary: prefer PATH, fall back to repo venv.
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
```

The script must be **executable** (`chmod +x`); commit the executable
bit. Add `#!/usr/bin/env bash` rather than `#!/bin/bash` for macOS / linux
portability.

### 2. `.windsurf/hooks.json`

The Cascade hook config. **The exact key names depend on the docs you
read in step 0**; the schema below is the 2026-04 plan's best guess.
Update it to match whatever the live docs say:

```json
{
  "version": "1.0",
  "hooks": {
    "pre_write_code": {
      "command": "./scripts/precheck_write.sh",
      "args": ["${WRITE_PATH}"],
      "block_on_nonzero_exit": true,
      "timeout_ms": 5000
    }
  }
}
```

If the docs use a different filename (e.g. `.windsurf/config.json`,
`.windsurf/hooks/pre_write_code.json`), put the file at the documented
path. **Do not** ship a config at multiple paths.

If the docs require a different field shape (e.g. an array of hook
entries, or per-hook env-var declaration), conform to the docs. Always
preserve `block_on_nonzero_exit: true` (or the docs' equivalent) and a
≤ 5000 ms timeout.

### 3. `tests/test_precheck_write_script.py`

A new pytest module that exercises the **shell script** itself via
`subprocess.run`. **Do not** test Cascade's hook host (we have no
sandbox for that); test the script's exit-code and stderr behaviour
end-to-end.

```python
"""Cascade pre_write_code hook script integration tests.

Exercises scripts/precheck_write.sh with a fixture lockfile, asserting
the exit-code contract and the BLOCKED message format.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "precheck_write.sh"


def _run(args, env, cwd):
    return subprocess.run(
        [str(SCRIPT), *args],
        env={**os.environ, **env},
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_allows_when_no_task_context(tmp_path: Path):
    proc = _run(["src/foo.ts"], env={"ACG_LOCK": str(tmp_path / "missing.json")}, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "no ACG task context" in proc.stderr


def test_allows_internal_write_paths(tmp_path: Path):
    for internal in (".windsurf/state.json", ".git/HEAD", ".acg/cache/x"):
        proc = _run([internal], env={"ACG_CURRENT_TASK": "tests"}, cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr


def test_allows_in_path_write(tmp_path: Path, example_dag_lockfile_path: Path):
    shutil.copy(example_dag_lockfile_path, tmp_path / "agent_lock.json")
    proc = _run(
        ["src/components/Settings.tsx"],
        env={"ACG_LOCK": str(tmp_path / "agent_lock.json"), "ACG_CURRENT_TASK": "settings"},
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr


def test_blocks_out_of_path_write(tmp_path: Path, example_dag_lockfile_path: Path):
    shutil.copy(example_dag_lockfile_path, tmp_path / "agent_lock.json")
    proc = _run(
        ["src/server/auth/config.ts"],
        env={"ACG_LOCK": str(tmp_path / "agent_lock.json"), "ACG_CURRENT_TASK": "settings"},
        cwd=tmp_path,
    )
    assert proc.returncode == 2, proc.stderr
    assert "BLOCKED" in proc.stderr
    assert "settings" in proc.stderr
```

Notes:

- The `example_dag_lockfile_path` fixture lives in `tests/conftest.py`
  already — reuse it.
- Tests must `skip` cleanly on Windows (the script is bash-only):
  `pytest.skipif(os.name == "nt", reason="bash hook is POSIX-only")`.
- The script depends on `./.venv/bin/acg` being on disk for the
  validator path. The `tmp_path` working-dir means the script will fall
  through the `./.venv/bin/acg` check — make sure your tests `cwd=`
  into the **repo root** (not `tmp_path`) for the in-path / out-of-path
  cases, and copy the lockfile under the repo root's `tmp_path` to keep
  isolation. Adjust if needed; the goal is tests pass on a fresh CI
  checkout.

Aim for at least 4 tests, expand if there are more interesting edge
cases (e.g. missing `acg` binary returns 0 with the soft-fail message,
trailing newlines in `WRITE_PATH`, etc.).

### 4. `docs/CASCADE_INTEGRATION.md`

A new ~1-page doc, structured like `docs/COGNITION_INTEGRATION.md`:

````markdown
# Cascade Integration

ACG's enforcement layer runs **inside Cascade** via the
`pre_write_code` hook. When a Cascade-driven edit attempts a write
outside the current task's `allowed_paths`, Cascade displays a
BLOCKED message authored by ACG's validator before the write lands.

## How it works

```text
Cascade attempts write → .windsurf/hooks.json fires →
  scripts/precheck_write.sh → acg validate-write →
    exit 0 ⇒ write proceeds
    exit 2 ⇒ Cascade displays BLOCKED message, write rolled back
```
````

## Setup

1. Open the repo in Windsurf.
2. Confirm `.windsurf/hooks.json` is committed (it is, in this repo).
3. Set the per-task env var before invoking Cascade:

```bash
export ACG_CURRENT_TASK=settings        # the task id from agent_lock.json
export ACG_LOCK=demo-app/agent_lock.json
```

4. Use Cascade as normal. Out-of-bounds writes will be blocked by
   Cascade itself with a "[acg-hook] BLOCKED: ..." message.

## Switching tasks mid-session

`ACG_CURRENT_TASK` is the only knob. Re-export it to switch tasks:

```bash
export ACG_CURRENT_TASK=billing
```

Cascade re-reads the env on the next write attempt.

## Disabling enforcement temporarily

Unset `ACG_CURRENT_TASK`. The hook short-circuits to "no ACG task
context; allowing" and Cascade behaves as if the hook were absent.

## What's enforced

| Path                                    | Behaviour                          |
| --------------------------------------- | ---------------------------------- |
| Inside the task's `allowed_paths` glob  | allowed                            |
| Outside the task's `allowed_paths` glob | blocked, exit 2                    |
| `.windsurf/`, `.git/`, `.acg/`          | always allowed (Cascade internals) |
| No lockfile / no `ACG_CURRENT_TASK`     | allowed (soft-fail)                |

## Demo upgrade

The hackathon demo's "show enforcement" segment used to invoke the
validator via a Python wrapper. With this hook installed, the upgraded
recording shows Cascade itself blocking the out-of-bounds write
inline — see the segment notes in
[`docs/plans/cascade-hook-stretch-308cc2.md`](plans/cascade-hook-stretch-308cc2.md).

## Limitations

- macOS / Linux only (the hook script is bash). Windows users should
  fall back to the post-hoc validator (`acg validate-write` on a
  per-write basis from PowerShell).
- Cascade re-reads `.windsurf/hooks.json` on workspace open; restart
  Windsurf after editing the file.
- The hook does not fire for non-Cascade writes (e.g. running `git
apply` directly). The runtime validator (`acg run`) covers those.

````

### 5. `README.md` — small additive section

Append a new `## Cascade integration` section **after** the existing
`## Sponsor narratives` section and **before** `## Honesty box`:

```markdown
## Cascade integration

ACG includes a Windsurf `pre_write_code` hook script that can block
out-of-bounds Cascade writes before the diff lands once `.windsurf/hooks.json`
is configured. See [`docs/CASCADE_INTEGRATION.md`](docs/CASCADE_INTEGRATION.md).
````

Do **not** rewrite any other README copy.

### 6. `Makefile` — small smoke test target

Append `cascade-hook-test` to `.PHONY` on line 1. Add at the bottom:

```makefile
# Quick smoke test of the Cascade hook script (exercises ALLOWED + BLOCKED).
cascade-hook-test:
	./.venv/bin/python -m pytest tests/test_precheck_write_script.py -v
```

## Branch / commit / PR conventions

- Branch from `main`: `git checkout -b cascade-pre-write-hook`
- Commits:
  ```
  scripts: add Cascade pre_write_code hook script
  windsurf: register pre_write_code hook in .windsurf/hooks.json
  tests: cover the Cascade hook script's exit-code contract (4 cases)
  docs: add CASCADE_INTEGRATION.md and README pointer
  make: add cascade-hook-test smoke target
  ```
- PR title: `cascade: ship pre_write_code hook + script + integration tests`
- PR description: cite the Windsurf docs URL you used for the hook
  contract; paste the ALLOWED + BLOCKED stderr output of the script
  invoked manually.

## Acceptance gates

```bash
./.venv/bin/python -m pytest tests/ -q          # all existing + 4 new tests pass
./.venv/bin/ruff check acg/ tests/ benchmark/
chmod +x scripts/precheck_write.sh && \
  ./scripts/precheck_write.sh "src/components/Settings.tsx" 2>&1 | \
  grep -q "no ACG task context"                  # soft-fail path works
shellcheck scripts/precheck_write.sh             # if shellcheck is available
make cascade-hook-test                           # green
```

## DO NOT

- Modify `acg/cli.py`, `acg/enforce.py`, `acg/runtime.py`, or any other
  Python module. The hook is a thin shell wrapper; behaviour changes
  belong in a separate predictor/enforce PR.
- Hardcode `/path/to/acg` anywhere; resolve via `$PATH` and the venv
  fallback.
- Use Python in the hook script. Bash startup is < 5 ms; Python is
  > 100 ms which eats the timeout budget on cold disk.
- Set `ACG_CURRENT_TASK` automatically anywhere. The user controls the
  task scope; this PR ships the plumbing only.
- Modify `viz/`, `demo-app/`, `experiments/`, or any of the other
  in-flight PR surfaces.
- Skip the Windsurf doc verification in step 0. If you cannot reach
  the docs from the sandbox, document that and ship the plan's best
  guess; the human author will adjust on review.

## When in doubt

- `acg/cli.py::cmd_validate_write` is the source-of-truth for the
  validator's exit codes (`EXIT_ALLOWED=0`, `EXIT_BLOCKED=2`,
  `EXIT_USER_ERROR=1`). Read it before depending on the codes.
- `tests/conftest.py` already exposes `example_dag_lockfile_path` —
  reuse it for the script tests.
- The plan in `docs/plans/cascade-hook-stretch-308cc2.md` is the
  intent; the live Windsurf docs are the contract. When they
  disagree, the docs win.
- The Cascade hook config schema may have evolved since the plan was
  written. Do not assume; verify.

Good luck. Once this lands, the demo's "blocked write" beat upgrades
from "Python wrapper enforces" to "Cascade itself enforces" — which
is materially more impressive on the hackathon stage.
