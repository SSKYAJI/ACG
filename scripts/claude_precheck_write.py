#!/usr/bin/env python3
"""Claude Code PreToolUse hook for ACG write validation.

Verified against the official Claude Code hook docs on 2026-05-13:
- project hooks live in .claude/settings.json
- write-like tools are Write and Edit
- attempted file paths arrive at tool_input.file_path
- PreToolUse uses JSON stdout with hookSpecificOutput.permissionDecision
"""

from __future__ import annotations

import json
import os
import posixpath
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


INTERNAL_PATH_PREFIXES = (
    ".acg/",
    ".claude/",
    ".cursor/",
    ".git/",
    ".idea/",
    ".vscode/",
    ".windsurf/",
)


def _emit_permission_decision(decision: str, reason: str) -> int:
    payload = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")
    return 0


def _load_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _normalize_attempted_path(raw_path: str, cwd: str) -> tuple[str | None, str | None]:
    if not raw_path:
        return None, "missing file path"

    candidate = raw_path.replace("\\", "/")
    cwd_normalized = cwd.replace("\\", "/").rstrip("/")

    if candidate.startswith("/") and cwd_normalized:
        prefix = f"{cwd_normalized}/"
        if candidate == cwd_normalized:
            candidate = ""
        elif candidate.startswith(prefix):
            candidate = candidate[len(prefix) :]

    if candidate.startswith("/"):
        return candidate, None

    normalized = posixpath.normpath(candidate or ".")
    if normalized in (".", ""):
        return "", None
    if normalized == ".." or normalized.startswith("../"):
        return None, "path escapes the repository"
    return normalized, None


def _is_internal_path(path: str) -> bool:
    return path in {
        ".acg",
        ".claude",
        ".cursor",
        ".git",
        ".idea",
        ".vscode",
        ".windsurf",
    } or any(path.startswith(prefix) for prefix in INTERNAL_PATH_PREFIXES)


def _resolve_acg_binary(cwd: Path) -> str | None:
    venv_binary = cwd / ".venv" / "bin" / "acg"
    if venv_binary.is_file() and os.access(venv_binary, os.X_OK):
        return str(venv_binary)

    path_binary = shutil.which("acg")
    if path_binary:
        return path_binary
    return None


def main() -> int:
    payload = _load_payload()
    if payload.get("tool_name") not in {"Write", "Edit"}:
        return 0

    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return 0

    raw_path = tool_input.get("file_path")
    if not isinstance(raw_path, str) or not raw_path:
        return 0

    cwd = payload.get("cwd")
    if not isinstance(cwd, str) or not cwd:
        cwd = os.getcwd()
    cwd_path = Path(cwd)

    attempted_path, error = _normalize_attempted_path(raw_path, cwd)
    if error:
        return _emit_permission_decision("deny", f"ACG BLOCKED: {raw_path} {error}.")
    assert attempted_path is not None

    if _is_internal_path(attempted_path):
        return _emit_permission_decision("allow", f"ACG ALLOWED: internal path {attempted_path}.")

    lock_path = os.environ.get("ACG_LOCK", "agent_lock.json")
    task_id = os.environ.get("ACG_CURRENT_TASK", "")
    if not task_id or not Path(lock_path).is_file():
        return 0

    acg_binary = _resolve_acg_binary(cwd_path)
    if not acg_binary:
        return 0

    result = subprocess.run(
        [
            acg_binary,
            "validate-write",
            "--lock",
            lock_path,
            "--task",
            task_id,
            "--path",
            attempted_path,
            "--quiet",
        ],
        cwd=str(cwd_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return _emit_permission_decision(
            "allow", f"ACG ALLOWED: {attempted_path} (task={task_id})."
        )

    if result.returncode == 2:
        return _emit_permission_decision(
            "deny",
            f"ACG BLOCKED: {attempted_path} is outside task {task_id}'s allowed_paths.",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
