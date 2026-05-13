# Cascade Integration

ACG includes Windsurf/Cascade hook scripts for both `pre_write_code` and
`post_write_code`. When registered in `.windsurf/hooks.json`, a
Cascade-driven edit outside the current task's `allowed_paths` exits with
a BLOCKED message authored by ACG's validator before the write lands, and
successful writes emit a short receipt after the write completes.

The hook contract is documented at
[docs.windsurf.com/windsurf/cascade/hooks](https://docs.windsurf.com/windsurf/cascade/hooks):
Cascade invokes the hook script with a JSON envelope on stdin
(`tool_info.file_path`); exit code `2` blocks the write, any other
exit allows it.

## How it works

```text
Cascade attempts write → configured .windsurf/hooks.json fires →
  scripts/precheck_write.sh → acg validate-write →
    exit 0 ⇒ write proceeds
    exit 2 ⇒ Cascade displays BLOCKED message, write rolled back
```

The hook script (`scripts/precheck_write.sh`) is a POSIX-`bash`
wrapper with no runtime dependencies beyond `sed`, `cat`, and the
`acg` CLI (resolved from `$PATH`, falling back to `./.venv/bin/acg`).
It parses Cascade's JSON input on stdin and also accepts the write
path as `$1` for direct invocation and testing.

## Setup

1. Open the repo in Windsurf.
2. Register both `scripts/precheck_write.sh` and `scripts/postcheck_write.sh`
   in `.windsurf/hooks.json` according to the Windsurf hook contract.
3. Set the per-task env var before invoking Cascade:

```bash
export ACG_CURRENT_TASK=settings        # the task id from agent_lock.json
export ACG_LOCK=demo-app/agent_lock.json
```

4. Use Cascade as normal. Out-of-bounds writes will be blocked by
   Cascade itself with a `[acg-hook] BLOCKED: ...` message.

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
| `acg` binary unreachable                | allowed (soft-fail with warning)   |

## Demo upgrade

The original demo's "show enforcement" segment used to invoke the
validator via a Python wrapper. With this hook installed, Cascade
itself blocks the out-of-bounds write inline.

## Cascade hook messages

With `show_output: true` in `.windsurf/hooks.json`, both hooks surface
messages directly in Cascade's UI:

| Hook              | Message                                                        |
| ----------------- | -------------------------------------------------------------- |
| `pre_write_code`  | `[acg-hook] ALLOWED: <path> validated via Cascade pre_write_code hook (task=<id>)` |
| `pre_write_code`  | `[acg-hook] BLOCKED: <path> is outside task <id>'s allowed_paths` |
| `post_write_code` | `[acg-hook] write receipt: <path> landed (task=<id>, hook=post_write_code)` |

The `pre_write_code` hook validates and optionally blocks the write;
the `post_write_code` hook fires after the write lands and emits a
receipt confirming the Cascade hook integration is active. Together
they provide end-to-end visibility into ACG enforcement through
Cascade's native hook system.

## Limitations

- macOS / Linux only (the hook script is `bash`). Windows users should
  fall back to the post-hoc validator (`acg validate-write` on a
  per-write basis from PowerShell).
- Cascade re-reads `.windsurf/hooks.json` on workspace open; restart
  Windsurf after editing the file.
- The hook does not fire for non-Cascade writes (e.g. running
  `git apply` directly). The runtime validator (`acg run`) covers
  those.
- The hook path normaliser strips only the `$PWD` prefix. If Cascade
  is pointed at a workspace root that differs from the lockfile's
  `repo.root`, set `working_directory` in `.windsurf/hooks.json`
  accordingly.
