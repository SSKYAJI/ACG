"""Test-runner output parsers used by compute_fail_to_pass.

Each `run_*` function executes the test runner against a checkout and returns
`{nodeid: "PASSED" | "FAILED" | "ERROR"}` so the FTP/PTP set logic in the caller
stays language-agnostic. nodeids are stable identifiers the canonical-overlay
step can match against.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

_PYTEST_RE = re.compile(
    r"^(\S+)\s+(PASSED|FAILED|ERROR)(?:\s+\[.*?\])?\s*$",
    re.MULTILINE,
)

_TAP_OK_RE = re.compile(r"^(ok|not ok)\s+\d+\s+-\s+(.+?)(?:\s+#.*)?$", re.MULTILINE)


def run_pytest(cwd: Path, test_command: str, test_files: list[str]) -> dict[str, str]:
    try:
        cmd_parts = shlex.split(test_command)
    except ValueError:
        return {}
    cmd = cmd_parts + ["--tb=no", "-v", *test_files]
    proc = subprocess.run(
        cmd, cwd=str(cwd.resolve()), capture_output=True, text=True, timeout=300
    )
    return parse_pytest_output(proc.stdout or "")


def parse_pytest_output(stdout: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in _PYTEST_RE.finditer(stdout)}


def run_vitest(
    cwd: Path, test_command: str, test_files: list[str], timeout: float = 300.0
) -> dict[str, str]:
    """Run vitest with --reporter=json. test_command may already include the reporter."""
    try:
        cmd_parts = shlex.split(test_command)
    except ValueError:
        return {}
    if "--reporter=json" not in test_command and "--reporter" not in test_command:
        cmd_parts.extend(["--reporter=json"])
    cmd_parts.extend(test_files)
    proc = subprocess.run(
        cmd_parts, cwd=str(cwd.resolve()), capture_output=True, text=True, timeout=timeout
    )
    return parse_vitest_json(proc.stdout or "")


def parse_vitest_json(stdout: str) -> dict[str, str]:
    """Parse vitest's --reporter=json output into {nodeid: PASSED|FAILED}.

    nodeid format: "<relative-file-path>::<fullName>" — mirrors pytest's
    "<file>::<test>" convention so downstream FTP/PTP set ops are uniform.
    Vitest prints non-JSON warnings before the JSON blob; locate the first '{'.
    """
    start = stdout.find("{")
    if start < 0:
        return {}
    try:
        data = json.loads(stdout[start:])
    except json.JSONDecodeError:
        # Sometimes vitest interleaves; fall back to extracting per-line JSON.
        for line in stdout.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    data = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        else:
            return {}

    results: dict[str, str] = {}
    for tr in data.get("testResults", []):
        file_rel = tr.get("name", "")
        for ar in tr.get("assertionResults", []):
            full = ar.get("fullName") or ar.get("title", "")
            status = ar.get("status", "").lower()
            mapped = {"passed": "PASSED", "failed": "FAILED"}.get(status, "ERROR")
            nodeid = f"{file_rel}::{full}"
            results[nodeid] = mapped
    return results


def run_node_test(
    cwd: Path, test_command: str, test_files: list[str], timeout: float = 300.0
) -> dict[str, str]:
    """Run node --test with --test-reporter=tap."""
    try:
        cmd_parts = shlex.split(test_command)
    except ValueError:
        return {}
    if "--test-reporter" not in test_command:
        cmd_parts.extend(["--test-reporter=tap"])
    cmd_parts.extend(test_files)
    proc = subprocess.run(
        cmd_parts, cwd=str(cwd.resolve()), capture_output=True, text=True, timeout=timeout
    )
    return parse_tap_output(proc.stdout or "", test_files)


def parse_tap_output(stdout: str, test_files: list[str]) -> dict[str, str]:
    """Parse TAP `ok N - <name>` / `not ok N - <name>` lines.

    Top-level assertion lines look like:
        ok 1 - test name
        not ok 2 - failing test
    Subtest assertion lines under a `# Subtest:` header use the same syntax
    but with indentation; the regex catches both — we strip leading whitespace.

    nodeid format: when test_files has exactly one file, prefix with that file;
    otherwise return bare test names. The caller's `_filter_test_files` ensures
    test_files is non-empty and scoped, so this is a reasonable convention.
    """
    file_prefix = test_files[0] if len(test_files) == 1 else ""
    results: dict[str, str] = {}
    for line in stdout.splitlines():
        stripped = line.lstrip()
        m = _TAP_OK_RE.match(stripped)
        if not m:
            continue
        marker, name = m.group(1), m.group(2).strip()
        status = "PASSED" if marker == "ok" else "FAILED"
        nodeid = f"{file_prefix}::{name}" if file_prefix else name
        results[nodeid] = status
    return results
