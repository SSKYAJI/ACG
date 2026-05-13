"""PR-scoped test execution gate for applied_diff evaluation mode.

Loads manifest.json metadata, resolves test_command + test_files per task,
shells out to the test runner, parses pytest-style output, returns a
structured outcome. Modeled after acg/typecheck.py.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_TIMEOUT_SECONDS = 120.0

_PYTEST_DURATION_RE = re.compile(r"in ([\d.]+)s", re.IGNORECASE)


@dataclass
class CorrectnessOutcome:
    ran: bool
    exit_code: int | None = None
    passed_count: int | None = None
    failed_count: int | None = None
    total_count: int | None = None
    duration_seconds: float | None = None
    stdout_tail: str = ""
    skip_reason: str = ""
    test_files_run: list = field(default_factory=list)
    # FAIL_TO_PASS / PASS_TO_PASS counts (populated when manifest has the lists)
    fail_to_pass_passed: int | None = None
    fail_to_pass_total: int | None = None
    pass_to_pass_passed: int | None = None
    pass_to_pass_total: int | None = None
    # True when exit_code==2 and zero tests were collected successfully
    collection_error: bool = False
    # SWE-Bench-style canonical test overlay fields
    overlay_applied: bool = False
    overlay_skip_reason: str = ""


def load_manifest(manifest_path: Path) -> dict:
    """Load and return manifest.json contents."""
    with open(manifest_path, encoding="utf-8") as f:
        return json.load(f)


def resolve_task_meta(
    manifest: dict, repo_short_name: str, task_id: str
) -> tuple[str | None, list[str], str | None, list[str], list[str], str | None]:
    """Return (test_command, ground_truth_files, parent_commit_sha, fail_to_pass, pass_to_pass, merge_commit_sha).

    task_id is e.g. "pr3166-session-middleware"; match by pr_number parsed from
    the leading "prNNNN" prefix.
    Returns (None, [], None, [], [], None) if not found.
    """
    # Parse leading prNNNN prefix from task_id
    pr_match = re.match(r"pr(\d+)", task_id, re.IGNORECASE)
    if not pr_match:
        return None, [], None, [], [], None
    pr_number = int(pr_match.group(1))

    for repo in manifest.get("repos", []):
        if repo.get("short_name") != repo_short_name:
            continue
        test_command = repo.get("test_command")
        for task in repo.get("tasks", []):
            if task.get("pr_number") == pr_number:
                return (
                    test_command,
                    task.get("ground_truth_files", []),
                    task.get("parent_commit_sha"),
                    task.get("fail_to_pass", []),
                    task.get("pass_to_pass", []),
                    task.get("merge_commit_sha"),
                )
    return None, [], None, [], [], None


def filter_test_files(ground_truth_files: list[str]) -> list[str]:
    """Filter to paths that look like test files.

    Heuristic: contains '/tests/' or '/test/' OR basename starts with 'test_'
    OR basename ends with '_test.<ext>' or '.test.<ext>'.
    """
    result = []
    for path in ground_truth_files:
        p = Path(path)
        norm = path.replace("\\", "/")
        if (
            "/tests/" in norm
            or "/test/" in norm
            or norm.startswith("tests/")
            or norm.startswith("test/")
            or p.name.startswith("test_")
            or p.stem.endswith("_test")
            or p.stem.endswith(".test")
        ):
            result.append(path)
    return result


def overlay_canonical_tests(
    checkout: Path,
    repo_short_name: str,
    task_id: str,
    manifest_path: Path | None = None,
) -> dict:
    """Reset test files to their canonical merge_commit_sha state.

    SWE-Bench-style overlay: whatever the agent wrote to test files is
    discarded and the canonical test suite (from the merged PR commit) is
    checked out in its place.  Only the test files listed in
    ``ground_truth_files`` are touched — source files remain as the agent
    wrote them.

    Returns a dict with keys:
      - ``overlay_applied`` (bool): True when at least one file was reset.
      - ``test_files`` (list[str]): Files that were overlaid.
      - ``skip_reason`` (str): Non-empty when overlay was skipped.

    Skips silently (``overlay_applied=False``) when:
      - manifest is missing or unparseable
      - task not found in manifest
      - manifest entry lacks ``merge_commit_sha``
      - ``merge_commit_sha`` equals ``parent_commit_sha`` (no real PR diff)
      - no test files in ``ground_truth_files``

    The git checkout + add steps are idempotent: running overlay twice
    produces identical working-tree state.
    """
    if not isinstance(checkout, Path):
        checkout = Path(checkout)

    if manifest_path is None:
        manifest_path = checkout.parent.parent / "manifest.json"

    try:
        manifest = load_manifest(manifest_path)
    except (OSError, json.JSONDecodeError):
        return {"overlay_applied": False, "test_files": [], "skip_reason": "manifest_not_found"}

    _tc, ground_truth_files, parent_sha, _ftp, _ptp, merge_sha = resolve_task_meta(
        manifest, repo_short_name, task_id
    )

    if not merge_sha:
        return {"overlay_applied": False, "test_files": [], "skip_reason": "no_merge_commit_sha"}

    if merge_sha == parent_sha:
        return {
            "overlay_applied": False,
            "test_files": [],
            "skip_reason": "merge_sha_equals_parent_sha",
        }

    test_files = filter_test_files(ground_truth_files)
    if not test_files:
        return {"overlay_applied": False, "test_files": [], "skip_reason": "no_test_files"}

    repo = str(checkout.resolve())
    overlaid: list[str] = []
    for rel_path in test_files:
        try:
            subprocess.run(
                ["git", "-C", repo, "checkout", merge_sha, "--", rel_path],
                check=True,
                capture_output=True,
                text=True,
            )
            subprocess.run(
                ["git", "-C", repo, "add", rel_path],
                check=True,
                capture_output=True,
                text=True,
            )
            overlaid.append(rel_path)
        except subprocess.CalledProcessError:
            # File may not exist at merge_sha (e.g. deleted test); skip silently.
            pass

    return {
        "overlay_applied": bool(overlaid),
        "test_files": overlaid,
        "skip_reason": "" if overlaid else "git_checkout_failed_for_all_files",
    }


_PYTEST_ITEM_RE = re.compile(
    r"^(\S+)\s+(PASSED|FAILED|ERROR)(?:\s+\[.*?\])?\s*$",
    re.MULTILINE,
)


def _normalize_test_name(raw: str) -> str:
    """Strip file path prefix and parametrize suffix from a test ID.

    Examples:
        "tests/test_x.py::test_foo[asyncio]" -> "test_foo"
        "test_foo"                            -> "test_foo"
        "TestClass::test_foo[a-b]"            -> "test_foo"
        "tests/test_x.py::TestClass::test_foo" -> "test_foo"
    """
    body = raw.rsplit("::", 1)[-1]  # take last :: segment (function name)
    return body.split("[", 1)[0]    # strip parametrize suffix


def parse_pytest_per_test(stdout: str) -> dict[str, str]:
    """Parse per-test status from pytest -v output.

    Returns a dict of {nodeid: "PASSED" | "FAILED" | "ERROR"}.
    Handles lines like:
        tests/test_foo.py::test_bar PASSED                  [  7%]
        tests/test_foo.py::test_baz[asyncio] FAILED
    """
    results: dict[str, str] = {}
    for m in _PYTEST_ITEM_RE.finditer(stdout):
        results[m.group(1)] = m.group(2)
    return results


def _count_normalized_passed(
    per_test: dict[str, str],
    manifest_list: list[str],
) -> int:
    """Count manifest entries where ALL parametrize variants passed.

    A manifest entry (bare name or full node ID) is considered "passed" iff
    every observed test whose normalized name matches the entry's normalized
    name has status "PASSED". If no matching observed tests exist, counts as
    not passed.
    """
    # Build normalized name -> list of statuses from observed tests
    norm_to_statuses: dict[str, list[str]] = {}
    for nodeid, status in per_test.items():
        norm = _normalize_test_name(nodeid)
        norm_to_statuses.setdefault(norm, []).append(status)

    passed = 0
    for entry in manifest_list:
        norm_entry = _normalize_test_name(entry)
        statuses = norm_to_statuses.get(norm_entry)
        if statuses and all(s == "PASSED" for s in statuses):
            passed += 1
    return passed


def score_fail_to_pass(
    per_test: dict[str, str],
    fail_to_pass: list[str],
    pass_to_pass: list[str],
) -> tuple[int, int, int, int]:
    """Return (ftp_passed, ftp_total, ptp_passed, ptp_total).

    ``per_test`` is the {nodeid: status} dict from the current run.
    ``fail_to_pass`` / ``pass_to_pass`` are the pre-computed lists from manifest.

    Matching uses normalized test names so bare names like "test_foo" match
    parametrized observed IDs like "tests/test_x.py::test_foo[asyncio]".
    An entry is "passed" only when ALL parametrize variants pass.
    """
    ftp_passed = _count_normalized_passed(per_test, fail_to_pass)
    ptp_passed = _count_normalized_passed(per_test, pass_to_pass)
    return ftp_passed, len(fail_to_pass), ptp_passed, len(pass_to_pass)


def parse_pytest_output(stdout: str) -> tuple[int | None, int | None, int | None]:
    """Parse the trailing summary line from pytest stdout.

    Patterns: 'X passed', 'X passed, Y failed', 'X passed, Y skipped',
    'X failed', 'no tests ran', etc.
    Returns (passed, failed, total) or (None, None, None) if unparseable.
    """
    if not stdout:
        return None, None, None

    # Scan from the end for the summary line
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if not line:
            continue

        if re.search(r"no tests ran", line, re.IGNORECASE):
            return 0, 0, 0

        # Look for lines with 'passed' or 'failed' counts
        passed_m = re.search(r"(\d+) passed", line, re.IGNORECASE)
        failed_m = re.search(r"(\d+) failed", line, re.IGNORECASE)
        error_m = re.search(r"(\d+) error", line, re.IGNORECASE)

        if passed_m or failed_m or error_m:
            passed = int(passed_m.group(1)) if passed_m else 0
            failed = int(failed_m.group(1)) if failed_m else 0
            if error_m:
                failed += int(error_m.group(1))
            total = passed + failed
            return passed, failed, total

    return None, None, None


def run_pr_tests(
    checkout: Path,
    repo_short_name: str,
    task_id: str,
    manifest_path: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> CorrectnessOutcome:
    """High-level entry. Looks up manifest, resolves test files, shells out, parses, returns outcome.

    - If manifest_path is None, defaults to checkout.parent.parent / "manifest.json".
    - If test_command is missing → CorrectnessOutcome(ran=False, skip_reason="no_test_command").
    - If no test files in ground_truth_files → CorrectnessOutcome(ran=False, skip_reason="no_test_files").
    - On TimeoutExpired: exit_code=-1, skip_reason="timeout".
    - On FileNotFoundError (test_command not found): ran=False, skip_reason="test_command_not_found".
    """
    if not isinstance(checkout, Path):
        checkout = Path(checkout)

    if manifest_path is None:
        manifest_path = checkout.parent.parent / "manifest.json"

    try:
        manifest = load_manifest(manifest_path)
    except (OSError, json.JSONDecodeError):
        return CorrectnessOutcome(ran=False, skip_reason="manifest_not_found")

    test_command, ground_truth_files, _sha, fail_to_pass, pass_to_pass, _merge_sha = resolve_task_meta(
        manifest, repo_short_name, task_id
    )

    if not test_command:
        return CorrectnessOutcome(ran=False, skip_reason="no_test_command")

    # Always use manifest's ground_truth_files filtered to test files — the gate
    # verifies whether the bug was fixed, not whatever the agent thought was a test.
    test_files = filter_test_files(ground_truth_files)
    if not test_files:
        return CorrectnessOutcome(ran=False, skip_reason="no_test_files")

    try:
        cmd_parts = shlex.split(test_command)
    except ValueError:
        return CorrectnessOutcome(ran=False, skip_reason="test_command_parse_error")

    # Run with -v to get per-test status lines for FAIL_TO_PASS / PASS_TO_PASS scoring.
    cmd = cmd_parts + ["-v", "--tb=short"] + test_files
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(checkout.resolve()),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return CorrectnessOutcome(
            ran=True,
            exit_code=-1,
            skip_reason="timeout",
            test_files_run=test_files,
        )
    except FileNotFoundError:
        return CorrectnessOutcome(ran=False, skip_reason="test_command_not_found")

    wall = time.perf_counter() - t0
    stdout = proc.stdout or ""
    passed, failed, total = parse_pytest_output(stdout)

    # Per-test scoring for FAIL_TO_PASS / PASS_TO_PASS.
    per_test = parse_pytest_per_test(stdout)
    ftp_passed: int | None = None
    ftp_total: int | None = None
    ptp_passed: int | None = None
    ptp_total: int | None = None
    if fail_to_pass or pass_to_pass:
        ftp_passed, ftp_total, ptp_passed, ptp_total = score_fail_to_pass(
            per_test, fail_to_pass, pass_to_pass
        )

    # Detect collection errors using extended heuristics:
    # - pytest exit codes 2/3/4/5 all indicate non-test-result failures
    # - exit_code!=0 with zero total tests collected
    # - exit_code!=0 but none of the canonical FTP/PTP tests appear in output
    _ftp_t = ftp_total or 0
    _ptp_t = ptp_total or 0
    _ftp_p = ftp_passed or 0
    _ptp_p = ptp_passed or 0
    _observed_canonical_count = sum(
        1 for nodeid in per_test
        if any(
            _normalize_test_name(nodeid) == _normalize_test_name(e)
            for e in (fail_to_pass + pass_to_pass)
        )
    ) if (fail_to_pass or pass_to_pass) else None
    is_collection_error = (
        proc.returncode in (2, 3, 4, 5)
        or (proc.returncode != 0 and (total is None or total == 0))
        or (
            proc.returncode != 0
            and _ftp_t + _ptp_t > 0
            and _observed_canonical_count == 0
        )
    )

    # Extract duration from pytest's own summary when available
    duration: float | None = None
    dur_m = _PYTEST_DURATION_RE.search(stdout)
    if dur_m:
        try:
            duration = float(dur_m.group(1))
        except ValueError:
            duration = round(wall, 4)
    else:
        duration = round(wall, 4)

    return CorrectnessOutcome(
        ran=True,
        exit_code=proc.returncode,
        passed_count=passed,
        failed_count=failed,
        total_count=total,
        duration_seconds=duration,
        stdout_tail=stdout[-2000:],
        test_files_run=test_files,
        fail_to_pass_passed=ftp_passed,
        fail_to_pass_total=ftp_total,
        pass_to_pass_passed=ptp_passed,
        pass_to_pass_total=ptp_total,
        collection_error=is_collection_error,
    )
