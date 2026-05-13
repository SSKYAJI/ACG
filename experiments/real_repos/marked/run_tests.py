#!/usr/bin/env python3
"""Wrapper: build lib, run node --test, and adapt output format.

marked compiles TypeScript source to lib/marked.esm.js via esbuild. The lib
is gitignored, so tests always need a fresh build from the checked-out source.
This wrapper runs `npm run build:esbuild` before the tests, then adapts output.

This script bridges node:test to two consumers:
1. experiments/real_repos/_parsers.py::run_node_test (called by compute_fail_to_pass.py)
   - Passes --test-reporter=tap → this script emits raw TAP (passthrough mode)
2. acg/correctness.py::run_pr_tests (called by headtohead evaluation)
   - Passes -v --tb=short → this script converts TAP to pytest-style output

Auto-detects mode from flags:
  --test-reporter=tap  → build + TAP passthrough
  -v / --tb=*          → build + pytest-compat mode (TAP converted to PASSED/FAILED lines)

Usage:
    python3 run_tests.py [--test-reporter=tap] [-v] [--tb=SHORT] <test-files...>
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time

_TAP_OK_RE = re.compile(r"^(ok|not ok)\s+\d+\s+-\s+(.+?)(?:\s+#.*)?$")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CHECKOUT_DIR = os.path.join(_SCRIPT_DIR, "checkout")


def _is_file_level_line(name: str) -> bool:
    """Detect file-summary lines (the top-level ok/not-ok per file)."""
    return "/" in name or name.endswith((".js", ".ts", ".cjs", ".mjs"))


def _build_lib(cwd: str) -> tuple[bool, str]:
    """Run npm run build:esbuild in cwd. Returns (success, error_message)."""
    result = subprocess.run(
        ["npm", "run", "build:esbuild"],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        return False, result.stderr or result.stdout or "build failed"
    return True, ""


def main() -> int:
    args = sys.argv[1:]
    tap_mode = False
    pytest_compat = False
    filtered: list[str] = []

    for a in args:
        if a.startswith("--test-reporter"):
            tap_mode = True
            # Don't add to filtered — we control the reporter ourselves
        elif a == "-v":
            pytest_compat = True
        elif a.startswith("--tb"):
            pytest_compat = True
        else:
            filtered.append(a)

    # Build the lib from current source before running tests.
    # This is critical: lib/ is gitignored, so without a build step the tests
    # always run against whatever was compiled last (not the checked-out source).
    build_ok, build_err = _build_lib(_CHECKOUT_DIR)
    if not build_ok:
        print(f"ERROR: build:esbuild failed: {build_err}", file=sys.stderr)
        if pytest_compat:
            print("\n0 passed, 1 failed in 0.0s")
        return 2

    # Always run node --test with TAP reporter internally
    cmd = ["node", "--test", "--test-reporter=tap"] + filtered
    t0 = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=_CHECKOUT_DIR)
    wall = time.perf_counter() - t0

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    if tap_mode:
        # Raw TAP passthrough — _parsers.py::parse_tap_output will handle it
        sys.stdout.write(stdout)
        if stderr:
            sys.stderr.write(stderr)
        return proc.returncode

    # pytest-compat mode: convert TAP to pytest-style output for acg/correctness.py
    test_files = [f for f in filtered if not f.startswith("-")]
    file_prefix = test_files[0] if len(test_files) == 1 else ""

    results: dict[str, str] = {}
    for line in stdout.splitlines():
        stripped = line.lstrip()
        m = _TAP_OK_RE.match(stripped)
        if not m:
            continue
        marker, name = m.group(1), m.group(2).strip()
        if _is_file_level_line(name):
            continue
        status = "PASSED" if marker == "ok" else "FAILED"
        nodeid = f"{file_prefix}::{name}" if file_prefix else name
        results[nodeid] = status

    passed = sum(1 for s in results.values() if s == "PASSED")
    failed = sum(1 for s in results.values() if s == "FAILED")

    for nodeid, status in results.items():
        print(f"{nodeid} {status}")

    duration = round(wall, 2)
    if failed:
        print(f"\n{passed} passed, {failed} failed in {duration}s")
    else:
        print(f"\n{passed} passed in {duration}s")

    if stderr:
        sys.stderr.write(stderr)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
