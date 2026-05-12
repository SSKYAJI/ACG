"""Apply OpenAI ``apply_patch`` envelopes on disk via ``codex-apply-patch``.

The runtime and greenhouse strategies call :func:`apply_envelope` so the
patch implementation stays swappable (PyPI wheel vs a future vendored
parser) without threading third-party types through ``acg.runtime``.
"""

from __future__ import annotations

import re
from contextlib import chdir
from dataclasses import dataclass
from pathlib import Path

from codex_apply_patch import ApplyPatchError, apply_patch, parse_patch

_FILE_HEADER = re.compile(r"^\*\*\* (Update|Add|Delete) File: (.+?)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class AppliedPatchResult:
    """Outcome of applying a single ``apply_patch`` envelope on disk.

    The ``patch_na`` / ``patch_na_reason`` fields are the structured signal
    the eval harness needs to mark a task ``failed/PATCH_NA`` instead of
    silently swallowing the ``ApplyPatchError`` and downgrading to
    ``EMPTY_PATCH``. ``patch_na`` is true whenever the underlying Rust
    parser/applier rejected the input, the envelope was malformed, or an
    OS-level subprocess error tripped the adapter. ``patch_na_reason`` is
    a short, truncated ``str(exc)`` (<=300 chars) suitable for inclusion
    in artifact JSON without bloating it.
    """

    changed_files: list[str]
    errors: list[str]
    envelope_parsed: bool
    patch_na: bool = False
    patch_na_reason: str | None = None


# Plan-aligned alias so callers reading the megaplan can import
# ``AppliedPatchOutcome`` instead of ``AppliedPatchResult``.
AppliedPatchOutcome = AppliedPatchResult


_REASON_MAX_CHARS = 300


def _truncate_reason(reason: str) -> str:
    reason = reason.strip()
    if len(reason) <= _REASON_MAX_CHARS:
        return reason
    return reason[: _REASON_MAX_CHARS - 1].rstrip() + "…"


def _paths_in_envelope(envelope: str) -> list[str]:
    return [m.group(2).strip() for m in _FILE_HEADER.finditer(envelope)]


def _safe_repo_relative(path: str, repo_root: Path) -> bool:
    raw = path.strip().replace("\\", "/")
    if not raw or raw.startswith("/"):
        return False
    p = Path(raw)
    if p.is_absolute():
        return False
    if ".." in p.parts:
        return False
    try:
        (repo_root / p).resolve().relative_to(repo_root.resolve())
    except ValueError:
        return False
    return True


def _parse_success_paths(message: str) -> list[str]:
    paths: list[str] = []
    for line in message.splitlines():
        line = line.strip()
        if len(line) > 2 and line[0] in "MAD" and line[1] == " ":
            paths.append(line[2:].strip().replace("\\", "/"))
    return paths


def apply_envelope(envelope: str, repo_root: Path) -> AppliedPatchResult:
    """Parse and apply ``envelope`` under ``repo_root`` (must be a git checkout root).

    Never raises: any underlying ``ApplyPatchError``, malformed-envelope
    ``ValueError``, or unhandled subprocess/OS error is captured into
    ``AppliedPatchResult.patch_na`` + ``patch_na_reason`` so callers can
    treat the outcome as a structured task-level signal.
    """
    root = repo_root.resolve()
    try:
        parsed = parse_patch(envelope)
    except (TypeError, ValueError) as exc:
        return AppliedPatchResult(
            changed_files=[],
            errors=[str(exc)],
            envelope_parsed=False,
            patch_na=True,
            patch_na_reason=_truncate_reason(str(exc)),
        )
    except ApplyPatchError as exc:  # pragma: no cover - parse_patch typically raises ValueError
        return AppliedPatchResult(
            changed_files=[],
            errors=[str(exc)],
            envelope_parsed=False,
            patch_na=True,
            patch_na_reason=_truncate_reason(str(exc)),
        )
    if not parsed:
        return AppliedPatchResult(
            changed_files=[],
            errors=["empty patch"],
            envelope_parsed=False,
            patch_na=True,
            patch_na_reason="empty patch",
        )
    for path in _paths_in_envelope(envelope):
        if not _safe_repo_relative(path, root):
            reason = f"unsafe or absolute path in patch: {path!r}"
            return AppliedPatchResult(
                changed_files=[],
                errors=[reason],
                envelope_parsed=True,
                patch_na=True,
                patch_na_reason=_truncate_reason(reason),
            )
    try:
        with chdir(root):
            message = apply_patch(envelope)
    except (ApplyPatchError, OSError, RuntimeError) as exc:
        return AppliedPatchResult(
            changed_files=[],
            errors=[str(exc)],
            envelope_parsed=True,
            patch_na=True,
            patch_na_reason=_truncate_reason(str(exc)),
        )
    return AppliedPatchResult(
        changed_files=_parse_success_paths(str(message)),
        errors=[],
        envelope_parsed=True,
        patch_na=False,
        patch_na_reason=None,
    )
