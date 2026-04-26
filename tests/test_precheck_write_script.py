"""Cascade pre_write_code hook script integration tests.

Exercises ``scripts/precheck_write.sh`` with a fixture lockfile,
asserting the exit-code contract documented at
https://docs.windsurf.com/windsurf/cascade/hooks (exit 2 blocks; any
other exit allows) and the BLOCKED message format.

Windsurf Cascade invokes the hook on macOS/Linux via ``bash -c`` and
passes the write target as JSON on stdin (``tool_info.file_path``);
the script also accepts the write path as ``$1`` so the tests can
exercise the exit-code behaviour without reconstructing Cascade's
input envelope in every case.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "precheck_write.sh"

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
    # Clear inherited ACG_* vars so tests are hermetic regardless of
    # whatever the caller's shell had exported.
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


def test_script_is_executable() -> None:
    assert SCRIPT.exists(), f"hook script missing at {SCRIPT}"
    assert os.access(SCRIPT, os.X_OK), f"hook script not executable: {SCRIPT}"


def test_allows_when_no_task_context(tmp_path: Path) -> None:
    """Soft-fail: no lockfile + no current-task env means allow."""
    proc = _run(
        ["src/foo.ts"],
        env={"ACG_LOCK": str(tmp_path / "missing.json")},
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "no ACG task context" in proc.stderr


def test_allows_internal_write_paths(tmp_path: Path) -> None:
    """Cascade internals (.windsurf, .git, .acg) are never validated."""
    for internal in (".windsurf/state.json", ".git/HEAD", ".acg/cache/x"):
        proc = _run(
            [internal],
            env={"ACG_CURRENT_TASK": "tests"},
            cwd=tmp_path,
        )
        assert proc.returncode == 0, f"{internal}: {proc.stderr}"


def test_allows_in_path_write(tmp_path: Path, example_dag_lockfile_path: Path) -> None:
    """Write inside the task's allowed_paths glob → exit 0 + ALLOWED banner."""
    lock_copy = tmp_path / "agent_lock.json"
    shutil.copy(example_dag_lockfile_path, lock_copy)
    proc = _run(
        ["src/app/settings/page.tsx"],
        env={"ACG_LOCK": str(lock_copy), "ACG_CURRENT_TASK": "settings"},
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ALLOWED" in proc.stdout
    assert "Cascade pre_write_code hook" in proc.stdout


def test_blocks_out_of_path_write(tmp_path: Path, example_dag_lockfile_path: Path) -> None:
    """Write outside the task's allowed_paths → exit 2 + BLOCKED message."""
    lock_copy = tmp_path / "agent_lock.json"
    shutil.copy(example_dag_lockfile_path, lock_copy)
    proc = _run(
        ["src/server/auth/config.ts"],
        env={"ACG_LOCK": str(lock_copy), "ACG_CURRENT_TASK": "settings"},
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 2, proc.stderr
    assert "BLOCKED" in proc.stderr
    assert "settings" in proc.stderr
    assert "src/server/auth/config.ts" in proc.stderr


def test_reads_file_path_from_stdin_json(tmp_path: Path, example_dag_lockfile_path: Path) -> None:
    """Cascade's actual invocation: no argv, JSON envelope on stdin."""
    lock_copy = tmp_path / "agent_lock.json"
    shutil.copy(example_dag_lockfile_path, lock_copy)
    payload = (
        '{"agent_action_name":"pre_write_code",'
        '"tool_info":{"file_path":"src/server/auth/config.ts"}}'
    )
    proc = _run(
        [],
        env={"ACG_LOCK": str(lock_copy), "ACG_CURRENT_TASK": "settings"},
        cwd=REPO_ROOT,
        stdin=payload,
    )
    assert proc.returncode == 2, proc.stderr
    assert "BLOCKED" in proc.stderr
    assert "src/server/auth/config.ts" in proc.stderr


def test_normalises_absolute_cwd_prefixed_path(
    tmp_path: Path, example_dag_lockfile_path: Path
) -> None:
    """Cascade sends absolute paths; script strips the cwd prefix."""
    lock_copy = tmp_path / "agent_lock.json"
    shutil.copy(example_dag_lockfile_path, lock_copy)
    absolute = str(REPO_ROOT / "src" / "server" / "auth" / "config.ts")
    proc = _run(
        [absolute],
        env={"ACG_LOCK": str(lock_copy), "ACG_CURRENT_TASK": "settings"},
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 2, proc.stderr
    assert "src/server/auth/config.ts" in proc.stderr


def test_allows_when_acg_binary_missing(tmp_path: Path, example_dag_lockfile_path: Path) -> None:
    """If neither `acg` on PATH nor the venv binary resolves, soft-fail."""
    lock_copy = tmp_path / "agent_lock.json"
    shutil.copy(example_dag_lockfile_path, lock_copy)
    # tmp_path has no ./.venv/bin/acg, and we blank PATH so `command -v
    # acg` also fails. We keep /usr/bin on PATH so core utilities in
    # the script (sed, cat, head) still resolve.
    proc = _run(
        ["src/server/auth/config.ts"],
        env={
            "ACG_LOCK": str(lock_copy),
            "ACG_CURRENT_TASK": "settings",
            "PATH": "/usr/bin:/bin",
        },
        cwd=tmp_path,
    )
    assert proc.returncode == 0, proc.stderr
    assert "could not find 'acg'" in proc.stderr
