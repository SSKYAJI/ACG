"""Run ``npx tsc --noEmit`` over a checkout and capture a structured outcome.

This module is deliberately decoupled from the greenhouse strategies. It
exposes a single synchronous function so the apply-step wiring can call it
inside an ``asyncio.to_thread`` without bringing async into a subprocess
codepath. Failure modes (npx missing, tsc timeout, weird exit codes) are
surfaced as fields on :class:`TypecheckOutcome` so the caller can decide
whether to mark a task ``failed`` or ``completed_unverified``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 180.0
_TS_ERROR_RE = re.compile(r": error TS\d+:")


@dataclass(frozen=True)
class TypecheckOutcome:
    ran: bool
    exit_code: int | None
    diagnostic_count: int | None
    wall_seconds: float | None
    skip_reason: str | None = None
    stderr_tail: str | None = None  # last ~512 chars of stderr for debugging
    stdout_tail: str | None = None  # last ~512 chars of stdout for debugging


def run_tsc_noemit(
    checkout: Path,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    npx_executable: str | None = None,
) -> TypecheckOutcome:
    """Run ``npx --no-install tsc --noEmit`` in ``checkout`` and capture results.

    - When ``npx`` is not on PATH the outcome has ``ran=False`` and
      ``skip_reason="NPX_MISSING"``. The caller should treat the task as
      ``completed_unverified``, not ``failed``.
    - When the subprocess times out the outcome has ``ran=True``,
      ``exit_code=-1``, and ``skip_reason="TIMEOUT"``. The caller can mark
      the task ``failed/TYPECHECK_TIMEOUT``.
    - When tsc emits an error count > 0 in stdout the outcome's
      ``diagnostic_count`` is populated by counting ``": error TS<n>:"``
      occurrences. Exit code is whatever tsc returned.
    """
    if not isinstance(checkout, Path):
        checkout = Path(checkout)
    if not checkout.exists() or not checkout.is_dir():
        return TypecheckOutcome(
            ran=False,
            exit_code=None,
            diagnostic_count=None,
            wall_seconds=None,
            skip_reason="CHECKOUT_MISSING",
        )
    npx = npx_executable or shutil.which("npx")
    if npx is None:
        return TypecheckOutcome(
            ran=False,
            exit_code=None,
            diagnostic_count=None,
            wall_seconds=None,
            skip_reason="NPX_MISSING",
        )
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [npx, "--no-install", "tsc", "--noEmit"],
            cwd=str(checkout),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        wall = time.perf_counter() - t0
        return TypecheckOutcome(
            ran=True,
            exit_code=-1,
            diagnostic_count=None,
            wall_seconds=round(wall, 4),
            skip_reason="TIMEOUT",
            stdout_tail=(exc.stdout or "")[-512:] if isinstance(exc.stdout, str) else None,
            stderr_tail=(exc.stderr or "")[-512:] if isinstance(exc.stderr, str) else None,
        )
    except OSError as exc:
        wall = time.perf_counter() - t0
        return TypecheckOutcome(
            ran=False,
            exit_code=None,
            diagnostic_count=None,
            wall_seconds=round(wall, 4),
            skip_reason=f"OSERROR:{exc.errno}",
        )
    wall = time.perf_counter() - t0
    out = proc.stdout or ""
    diagnostic_count = sum(1 for line in out.splitlines() if _TS_ERROR_RE.search(line))
    return TypecheckOutcome(
        ran=True,
        exit_code=proc.returncode,
        diagnostic_count=diagnostic_count,
        wall_seconds=round(wall, 4),
        stdout_tail=proc.stdout[-512:] if proc.stdout else None,
        stderr_tail=proc.stderr[-512:] if proc.stderr else None,
    )
