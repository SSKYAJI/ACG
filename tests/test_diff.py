"""Tests for applied git diff validation."""

from __future__ import annotations

import subprocess
from pathlib import Path

from acg.diff import changed_files_from_git_diff, validate_changed_files
from acg.schema import AgentLock, ExecutionPlan, Group, PredictedWrite, Repo, Task


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init_repo(repo: Path) -> None:
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    _git(repo, "config", "user.email", "acg@example.com")
    _git(repo, "config", "user.name", "ACG Test")
    (repo / "README.md").write_text("initial\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")


def _lock() -> AgentLock:
    task = Task(
        id="docs",
        prompt="Update docs.",
        predicted_writes=[PredictedWrite(path="README.md", confidence=0.9, reason="docs task")],
        allowed_paths=["README.md"],
        depends_on=[],
        parallel_group=1,
    )
    return AgentLock(
        generated_at=AgentLock.utcnow(),
        repo=Repo(root="repo", languages=["markdown"]),
        tasks=[task],
        execution_plan=ExecutionPlan(groups=[Group(id=1, tasks=["docs"], type="serial")]),
    )


def test_changed_files_from_git_diff_includes_worktree_and_untracked(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "README.md").write_text("changed\n")
    (repo / "notes.md").write_text("new\n")

    changed = changed_files_from_git_diff(repo, base_ref="HEAD")

    assert changed == ["README.md", "notes.md"]


def test_validate_changed_files_flags_out_of_contract_paths() -> None:
    result = validate_changed_files(
        _lock(),
        task_id="docs",
        changed_files=["README.md", "src/oops.ts"],
        base_ref="HEAD",
    )

    assert result.allowed_count == 1
    assert result.blocked_count == 1
    assert result.ok is False
    blocked = [verdict for verdict in result.verdicts if not verdict.allowed]
    assert blocked[0].path == "src/oops.ts"
    assert blocked[0].reason and "outside task 'docs'" in blocked[0].reason
