#!/bin/sh
# ACG-managed Claude Code PreToolUse hook.
# Reads Claude's hook JSON from stdin and returns structured PreToolUse JSON.
exec npx acg guard --platform claude
