"""Validate applied git diffs against an ACG lockfile."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .enforce import validate_write
from .schema import AgentLock


class DiffValidationError(ValueError):
    """Raised when git diff data cannot be collected."""


@dataclass(frozen=True)
class DiffFileVerdict:
    """Validation result for one changed file."""

    path: str
    allowed: bool
    reason: str | None


@dataclass(frozen=True)
class DiffValidationResult:
    """Validation result for all files changed by one task diff."""

    task_id: str
    base_ref: str
    head_ref: str | None
    changed_files: list[str]
    verdicts: list[DiffFileVerdict]

    @property
    def blocked_count(self) -> int:
        return sum(1 for verdict in self.verdicts if not verdict.allowed)

    @property
    def allowed_count(self) -> int:
        return sum(1 for verdict in self.verdicts if verdict.allowed)

    @property
    def ok(self) -> bool:
        return self.blocked_count == 0


def _git_text(repo_path: Path, args: list[str]) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise DiffValidationError(f"git {' '.join(args)} failed in {repo_path}: {detail}") from exc
    return proc.stdout


def changed_files_from_git_diff(
    repo_path: Path,
    *,
    base_ref: str,
    head_ref: str | None = None,
) -> list[str]:
    """Return repo-relative files changed between refs or in the worktree.

    When ``head_ref`` is omitted, tracked changes are diffed against
    ``base_ref`` and untracked files are included. This is the local-agent
    workflow: run the worker, then validate the actual worktree diff.
    """
    repo_path = Path(repo_path)
    if not repo_path.exists():
        raise DiffValidationError(f"repo path does not exist: {repo_path}")
    diff_target = f"{base_ref}...{head_ref}" if head_ref else base_ref
    changed = {
        line.strip()
        for line in _git_text(
            repo_path,
            ["diff", "--name-only", "--diff-filter=ACMRTUXB", diff_target, "--"],
        ).splitlines()
        if line.strip()
    }
    if head_ref is None:
        changed.update(
            line.strip()
            for line in _git_text(
                repo_path, ["ls-files", "--others", "--exclude-standard"]
            ).splitlines()
            if line.strip()
        )
    return sorted(changed)


def validate_changed_files(
    lock: AgentLock,
    *,
    task_id: str,
    changed_files: list[str],
    base_ref: str = "HEAD",
    head_ref: str | None = None,
) -> DiffValidationResult:
    """Validate an explicit changed-file list against a task contract."""
    verdicts: list[DiffFileVerdict] = []
    for path in sorted(set(changed_files)):
        allowed, reason = validate_write(lock, task_id, path)
        verdicts.append(DiffFileVerdict(path=path, allowed=allowed, reason=reason))
    return DiffValidationResult(
        task_id=task_id,
        base_ref=base_ref,
        head_ref=head_ref,
        changed_files=[verdict.path for verdict in verdicts],
        verdicts=verdicts,
    )


def validate_git_diff(
    lock: AgentLock,
    *,
    repo_path: Path,
    task_id: str,
    base_ref: str = "HEAD",
    head_ref: str | None = None,
) -> DiffValidationResult:
    """Collect changed files from git and validate them against ``allowed_paths``."""
    changed_files = changed_files_from_git_diff(repo_path, base_ref=base_ref, head_ref=head_ref)
    return validate_changed_files(
        lock,
        task_id=task_id,
        changed_files=changed_files,
        base_ref=base_ref,
        head_ref=head_ref,
    )


__all__ = [
    "DiffFileVerdict",
    "DiffValidationError",
    "DiffValidationResult",
    "changed_files_from_git_diff",
    "validate_changed_files",
    "validate_git_diff",
]
