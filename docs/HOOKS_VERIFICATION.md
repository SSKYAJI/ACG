# Hooks Verification

Verified against the official docs on 2026-05-13.

## Claude Code

- Project hook config belongs in `.claude/settings.json` under `hooks`.
- The config shape used here is `hooks.PreToolUse[] -> { matcher, hooks[] }`.
- File-write tool matching should use `Write|Edit`.
- Claude sends the attempted write path in stdin JSON at `tool_input.file_path`.
- For `PreToolUse`, the current documented response format is JSON on stdout with `hookSpecificOutput.hookEventName = "PreToolUse"` plus `permissionDecision` / `permissionDecisionReason`.
- Claude still documents exit code `2` as a blocking code for command hooks, but this integration uses the event-specific JSON decision contract for `PreToolUse`.
- The older top-level `decision` / `reason` format is deprecated for `PreToolUse` and should not be used here.

## Windsurf / Cascade

- Workspace hook config belongs in `.windsurf/hooks.json` with top-level shape `{ "hooks": { ... } }`.
- The config entries used here are `pre_write_code` and `post_write_code` shell commands with `show_output: true`.
- Cascade sends stdin JSON with `agent_action_name` and `tool_info`; attempted write paths arrive at `tool_info.file_path`, and write diffs are available in `tool_info.edits`.
- Only pre-hooks can block. `pre_write_code` blocks by exiting with code `2`; other nonzero exit codes are errors but do not block.
- `post_write_code` cannot block because it fires after the write lands.
