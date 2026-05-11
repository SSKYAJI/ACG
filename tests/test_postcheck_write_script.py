"""Cascade post_write_code hook script integration tests.

Exercises ``scripts/postcheck_write.sh`` — the receipt-emitting hook
that fires after Cascade writes land.  The hook never blocks (post-hooks
cannot block per Cascade's contract); it only emits a receipt message
to stdout when ACG task context is present.

Hook contract reference:
https://docs.windsurf.com/windsurf/cascade/hooks
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "postcheck_write.sh"

pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="bash hook is POSIX-only; Windows users use the CLI validator directly.",
)


def _run(
    args: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = {**os.environ}
    for key in ("ACG_LOCK", "ACG_CURRENT_TASK"):
        merged_env.pop(key, None)
    if env:
        merged_env.update(env)
    return subprocess.run(
        [str(SCRIPT), *args],
        env=merged_env,
        cwd=str(cwd),
        input=stdin if stdin is not None else "",
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def test_postcheck_script_is_executable() -> None:
    assert SCRIPT.exists(), f"hook script missing at {SCRIPT}"
    assert os.access(SCRIPT, os.X_OK), f"hook script not executable: {SCRIPT}"


def test_silent_without_task_context(tmp_path: Path) -> None:
    """No ACG_CURRENT_TASK → exit 0, no receipt."""
    proc = _run([], cwd=tmp_path)
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_silent_without_lockfile(tmp_path: Path) -> None:
    """ACG_CURRENT_TASK set but no lockfile on disk → exit 0, no receipt."""
    proc = _run(
        [],
        env={"ACG_CURRENT_TASK": "settings", "ACG_LOCK": str(tmp_path / "missing.json")},
        cwd=tmp_path,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == ""


def test_emits_receipt_with_task_context(tmp_path: Path, example_dag_lockfile_path: Path) -> None:
    """With valid task context and lockfile → receipt message on stdout."""
    lock_copy = tmp_path / "agent_lock.json"
    shutil.copy(example_dag_lockfile_path, lock_copy)
    payload = (
        '{"agent_action_name":"post_write_code",'
        '"tool_info":{"file_path":"src/app/settings/page.tsx","edits":[]}}'
    )
    proc = _run(
        [],
        env={"ACG_LOCK": str(lock_copy), "ACG_CURRENT_TASK": "settings"},
        cwd=tmp_path,
        stdin=payload,
    )
    assert proc.returncode == 0
    assert "write receipt" in proc.stdout
    assert "post_write_code" in proc.stdout
    assert "settings" in proc.stdout


def test_emits_receipt_with_positional_arg(tmp_path: Path, example_dag_lockfile_path: Path) -> None:
    """Direct invocation with $1 for testing ergonomics."""
    lock_copy = tmp_path / "agent_lock.json"
    shutil.copy(example_dag_lockfile_path, lock_copy)
    proc = _run(
        ["src/app/settings/page.tsx"],
        env={"ACG_LOCK": str(lock_copy), "ACG_CURRENT_TASK": "settings"},
        cwd=tmp_path,
    )
    assert proc.returncode == 0
    assert "write receipt" in proc.stdout
    assert "src/app/settings/page.tsx" in proc.stdout


def test_never_exits_nonzero(tmp_path: Path, example_dag_lockfile_path: Path) -> None:
    """Post-hooks must never return exit 2 (cannot block per Cascade contract)."""
    lock_copy = tmp_path / "agent_lock.json"
    shutil.copy(example_dag_lockfile_path, lock_copy)
    proc = _run(
        ["src/server/auth/config.ts"],
        env={"ACG_LOCK": str(lock_copy), "ACG_CURRENT_TASK": "settings"},
        cwd=tmp_path,
    )
    assert proc.returncode == 0
