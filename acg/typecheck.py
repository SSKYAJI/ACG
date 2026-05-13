"""Run ``tsc --noEmit`` over a checkout and capture a structured outcome.

This module is deliberately decoupled from the greenhouse strategies. It
exposes a single synchronous function so the apply-step wiring can call it
inside an ``asyncio.to_thread`` without bringing async into a subprocess
codepath. Failure modes (missing compiler, tsc timeout, weird exit codes) are
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

_TS_SOURCE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts")
_TS_PROBE_SKIP_DIRS = {
    "node_modules",
    ".git",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
    ".next",
    ".acg",
}


def _has_typescript_sources(root: Path, *, probe_limit: int = 2000) -> bool:
    """Cheap probe: do any TypeScript source files live under ``root``?

    Walks at most ``probe_limit`` directory entries to avoid stalling on
    monster checkouts. Returns True as soon as one ``.ts``/``.tsx`` source
    is found outside obvious noise directories.
    """
    seen = 0
    for entry in root.rglob("*"):
        seen += 1
        if seen > probe_limit:
            return False
        if entry.is_dir() and entry.name in _TS_PROBE_SKIP_DIRS:
            # rglob doesn't honor early-exit pruning, but skipping the dir
            # name is enough to ignore most of node_modules / .venv noise.
            continue
        if entry.is_file() and entry.suffix in _TS_SOURCE_SUFFIXES:
            # Ignore .d.ts type stubs alone — they aren't enough signal.
            if entry.suffix == ".ts" and entry.name.endswith(".d.ts"):
                continue
            return True
    return False


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
    """Run ``tsc --noEmit`` in ``checkout`` and capture results.

    Prefers ``node_modules/typescript/bin/tsc``, then ``node_modules/.bin/tsc``,
    then ``npx --no-install tsc`` so bare checkouts do not pick up npm's
    unrelated ``tsc`` package when TypeScript is not installed locally.

    - When no compiler can be resolved the outcome has ``ran=False`` and
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
    checkout = checkout.resolve()
    # ``tsc --noEmit`` always exits non-zero on a non-TypeScript repo because
    # "no inputs were found". That makes the typecheck verdict useless for
    # Python / Java / mixed-language fixtures: a perfectly-applied Python
    # patch still shows up as ``typecheck_fail_count=1``. Detect and skip.
    has_tsconfig = (checkout / "tsconfig.json").is_file()
    has_ts_sources = _has_typescript_sources(checkout)
    if not has_tsconfig and not has_ts_sources:
        return TypecheckOutcome(
            ran=False,
            exit_code=None,
            diagnostic_count=None,
            wall_seconds=None,
            skip_reason="NOT_TYPESCRIPT",
        )
    bundled = checkout / "node_modules" / "typescript" / "bin" / "tsc"
    linked = checkout / "node_modules" / ".bin" / "tsc"
    if bundled.is_file():
        cmd: list[str] = [str(bundled), "--noEmit"]
    elif linked.is_file():
        cmd = [str(linked), "--noEmit"]
    else:
        npx = npx_executable or shutil.which("npx")
        if npx is None:
            return TypecheckOutcome(
                ran=False,
                exit_code=None,
                diagnostic_count=None,
                wall_seconds=None,
                skip_reason="NPX_MISSING",
            )
        cmd = [npx, "--no-install", "tsc", "--noEmit"]
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
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
