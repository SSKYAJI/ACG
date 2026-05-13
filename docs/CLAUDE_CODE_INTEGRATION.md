# Claude Code Integration

ACG includes a Claude Code `PreToolUse` hook that validates `Write` and `Edit`
tool calls before they land.

The project hook lives in `.claude/settings.json` and runs
`scripts/claude_precheck_write.py`. The hook reads `tool_input.file_path` from
Claude's stdin JSON payload, normalizes paths under the current workspace, and
checks them with:

```bash
acg validate-write --lock <lockfile> --task <task_id> --path <path>
```

For Claude `PreToolUse`, this integration uses the documented JSON response
format on stdout:

- `permissionDecision: "allow"` for in-scope or internal writes
- `permissionDecision: "deny"` for out-of-scope writes

## Setup

```bash
export ACG_CURRENT_TASK=settings
export ACG_LOCK=demo-app/agent_lock.json
```

Then run Claude Code normally in this repository. The hook is project-scoped, so
Claude picks it up from `.claude/settings.json`.

## Behavior

- `.git/`, `.acg/`, `.claude/`, and common editor metadata directories are always allowed
- missing `ACG_CURRENT_TASK`, missing lockfile, missing path, or missing `acg` binary soft-fails and allows execution to continue
- in-scope writes surface a short `ACG ALLOWED` reason
- out-of-scope writes surface a short `ACG BLOCKED` reason before the tool runs

## Source Of Truth

- `docs/HOOKS_VERIFICATION.md`
- `.claude/settings.json`
- `scripts/claude_precheck_write.py`
