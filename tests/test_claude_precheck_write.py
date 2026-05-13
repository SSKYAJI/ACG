"""Claude Code PreToolUse hook integration tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "claude_precheck_write.py"


def _payload(file_path: str, *, tool_name: str = "Write", cwd: Path) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "session_id": "abc123",
        "transcript_path": str(cwd / ".claude" / "transcript.jsonl"),
        "cwd": str(cwd),
        "permission_mode": "default",
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_use_id": "toolu_01ABC123",
        "tool_input": {
            "file_path": file_path,
        },
    }
    if tool_name == "Edit":
        payload["tool_input"]["old_string"] = "before"
        payload["tool_input"]["new_string"] = "after"
    else:
        payload["tool_input"]["content"] = "new file contents"
    return payload


def _run(
    payload: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
    cwd: Path,
) -> subprocess.CompletedProcess[str]:
    merged_env = {**os.environ}
    for key in ("ACG_LOCK", "ACG_CURRENT_TASK"):
        merged_env.pop(key, None)
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        env=merged_env,
        cwd=str(cwd),
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _decision(proc: subprocess.CompletedProcess[str]) -> dict[str, Any] | None:
    stdout = proc.stdout.strip()
    if not stdout:
        return None
    return json.loads(stdout)


def test_allows_when_no_task_context(tmp_path: Path) -> None:
    proc = _run(_payload("src/foo.ts", cwd=tmp_path), cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == ""


def test_allows_internal_paths(tmp_path: Path) -> None:
    proc = _run(_payload(".claude/settings.json", cwd=tmp_path), cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    decision = _decision(proc)
    assert decision is not None
    hook_output = decision["hookSpecificOutput"]
    assert hook_output["permissionDecision"] == "allow"
    assert ".claude/settings.json" in hook_output["permissionDecisionReason"]


def test_allows_in_scope_write(tmp_path: Path, example_dag_lockfile_path: Path) -> None:
    lock_copy = tmp_path / "agent_lock.json"
    shutil.copy(example_dag_lockfile_path, lock_copy)
    proc = _run(
        _payload("src/app/settings/page.tsx", cwd=REPO_ROOT),
        env={"ACG_LOCK": str(lock_copy), "ACG_CURRENT_TASK": "settings"},
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    decision = _decision(proc)
    assert decision is not None
    hook_output = decision["hookSpecificOutput"]
    assert hook_output["permissionDecision"] == "allow"
    assert "ACG ALLOWED" in hook_output["permissionDecisionReason"]
    assert "src/app/settings/page.tsx" in hook_output["permissionDecisionReason"]


def test_blocks_out_of_scope_write(tmp_path: Path, example_dag_lockfile_path: Path) -> None:
    lock_copy = tmp_path / "agent_lock.json"
    shutil.copy(example_dag_lockfile_path, lock_copy)
    proc = _run(
        _payload("src/server/auth/config.ts", cwd=REPO_ROOT),
        env={"ACG_LOCK": str(lock_copy), "ACG_CURRENT_TASK": "settings"},
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    decision = _decision(proc)
    assert decision is not None
    hook_output = decision["hookSpecificOutput"]
    assert hook_output["permissionDecision"] == "deny"
    assert "ACG BLOCKED" in hook_output["permissionDecisionReason"]
    assert "src/server/auth/config.ts" in hook_output["permissionDecisionReason"]


def test_parses_official_pretooluse_stdin_json(
    tmp_path: Path, example_dag_lockfile_path: Path
) -> None:
    lock_copy = tmp_path / "agent_lock.json"
    shutil.copy(example_dag_lockfile_path, lock_copy)
    absolute = str(REPO_ROOT / "src" / "server" / "auth" / "config.ts")
    proc = _run(
        _payload(absolute, tool_name="Edit", cwd=REPO_ROOT),
        env={"ACG_LOCK": str(lock_copy), "ACG_CURRENT_TASK": "settings"},
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    decision = _decision(proc)
    assert decision is not None
    hook_output = decision["hookSpecificOutput"]
    assert hook_output["hookEventName"] == "PreToolUse"
    assert hook_output["permissionDecision"] == "deny"
    assert "src/server/auth/config.ts" in hook_output["permissionDecisionReason"]


def test_normalizes_absolute_path_under_cwd(
    tmp_path: Path, example_dag_lockfile_path: Path
) -> None:
    lock_copy = tmp_path / "agent_lock.json"
    shutil.copy(example_dag_lockfile_path, lock_copy)
    absolute = str(REPO_ROOT / "src" / "app" / "settings" / "page.tsx")
    proc = _run(
        _payload(absolute, cwd=REPO_ROOT),
        env={"ACG_LOCK": str(lock_copy), "ACG_CURRENT_TASK": "settings"},
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    decision = _decision(proc)
    assert decision is not None
    hook_output = decision["hookSpecificOutput"]
    assert hook_output["permissionDecision"] == "allow"
    assert "src/app/settings/page.tsx" in hook_output["permissionDecisionReason"]
    assert str(REPO_ROOT) not in hook_output["permissionDecisionReason"]


def test_missing_acg_binary_soft_fails(
    tmp_path: Path, example_dag_lockfile_path: Path
) -> None:
    lock_copy = tmp_path / "agent_lock.json"
    shutil.copy(example_dag_lockfile_path, lock_copy)
    proc = _run(
        _payload("src/server/auth/config.ts", cwd=tmp_path),
        env={
            "ACG_LOCK": str(lock_copy),
            "ACG_CURRENT_TASK": "settings",
            "PATH": "/usr/bin:/bin",
        },
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == ""
