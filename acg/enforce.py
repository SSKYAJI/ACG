"""Lockfile-aware write validator — the demo's "BLOCKED" moment.

This module implements the runtime check that an agent (or a Windsurf hook,
or a plain CLI invocation) consults before mutating a file. Given a task id
and a candidate write path, :func:`validate_write` returns whether the write
is permitted by the task's ``allowed_paths``.
"""

from __future__ import annotations

import fnmatch
import json
from pathlib import Path

from .schema import AgentLock, Task

EXIT_ALLOWED = 0
EXIT_USER_ERROR = 1
EXIT_BLOCKED = 2


def _matches(pattern: str, path: str) -> bool:
    """Glob-match ``path`` against ``pattern``.

    Supports POSIX-style ``**`` for recursive directory matches in addition to
    the standard ``fnmatch`` wildcards.
    """
    # Normalise: drop leading ./
    candidate = path.lstrip("./")
    glob = pattern.lstrip("./")

    if glob.endswith("/**"):
        prefix = glob[:-3]
        return candidate == prefix or candidate.startswith(prefix + "/")
    if "**" in glob:
        # Translate ``a/**/b`` to ``a/*/b`` semantics by splitting on ``**``
        # and ensuring each segment is contained in order.
        parts = glob.split("**")
        cursor = 0
        for idx, part in enumerate(parts):
            stripped = part.strip("/")
            if not stripped:
                continue
            found = candidate.find(stripped, cursor)
            if found == -1:
                return False
            if idx == 0 and found != 0:
                return False
            cursor = found + len(stripped)
        return True
    return fnmatch.fnmatch(candidate, glob)


def _find_task(lock: AgentLock, task_id: str) -> Task:
    for task in lock.tasks:
        if task.id == task_id:
            return task
    raise KeyError(f"task {task_id!r} not found in lockfile")


def validate_write(
    lock: AgentLock, task_id: str, write_path: str
) -> tuple[bool, str | None]:
    """Return whether ``task_id`` is permitted to write ``write_path``.

    Args:
        lock: Loaded :class:`AgentLock`.
        task_id: Id of the task attempting the write.
        write_path: Repository-relative path the task wants to modify.

    Returns:
        ``(True, None)`` when permitted; ``(False, reason)`` otherwise.

    Raises:
        KeyError: when ``task_id`` is not present in ``lock``.
    """
    task = _find_task(lock, task_id)
    for pattern in task.allowed_paths:
        if _matches(pattern, write_path):
            return True, None
    reason = (
        f"path {write_path!r} is outside task {task_id!r}'s allowed_paths "
        f"({', '.join(task.allowed_paths) or 'empty'})"
    )
    return False, reason


def cli_validate(lockfile_path: Path, task_id: str, write_path: str) -> tuple[int, str]:
    """CLI-friendly wrapper returning ``(exit_code, message)``.

    Exit codes follow the convention used across the ACG CLI:
    ``0`` allowed, ``1`` user error (task missing, file unreadable),
    ``2`` blocked.
    """
    try:
        lock = AgentLock.model_validate_json(Path(lockfile_path).read_text())
    except (OSError, ValueError) as exc:
        return EXIT_USER_ERROR, f"could not load lockfile: {exc}"

    try:
        allowed, reason = validate_write(lock, task_id, write_path)
    except KeyError as exc:
        return EXIT_USER_ERROR, str(exc)

    if allowed:
        return EXIT_ALLOWED, f"ALLOWED: {task_id} → {write_path}"
    return EXIT_BLOCKED, f"BLOCKED: {reason}"


def load_lock(lockfile_path: Path) -> AgentLock:
    """Convenience loader used by tests and other CLIs."""
    payload = json.loads(Path(lockfile_path).read_text())
    return AgentLock.model_validate(payload)
