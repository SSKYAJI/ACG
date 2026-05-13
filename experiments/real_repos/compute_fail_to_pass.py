"""Compute FAIL_TO_PASS / PASS_TO_PASS per task and write them into manifest.json.

For each task in manifest.json the script:
  1. Records the current HEAD so it can restore it.
  2. Checks out ``parent_commit_sha`` and runs ``<test_command> <test_files> -v --tb=no``.
  3. Checks out ``merge_commit_sha`` and runs the same command.
  4. Derives:
     - FAIL_TO_PASS: tests that FAIL at parent and PASS at merge.
     - PASS_TO_PASS: tests that PASS at both parent and merge.
  5. Writes the two lists into the task entry in manifest.json.
  6. Restores the checkout to the original HEAD (try/finally).

Usage:
    python -m experiments.real_repos.compute_fail_to_pass [--repo starlette] [--force]

Flags:
    --repo  Short name of the repo to process (default: ALL repos in manifest).
    --force Recompute even when fail_to_pass/pass_to_pass already exist.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MANIFEST_PATH = Path(__file__).parent / "manifest.json"

# Pytest per-test line pattern from -v output.
# Examples:
#   "tests/test_templates.py::test_foo PASSED                  [ 7%]"
#   "tests/test_templates.py::test_foo[asyncio] PASSED         [ 7%]"
#   "tests/test_templates.py::test_foo FAILED"
# We want: nodeid (everything before the status word), status.
_PYTEST_ITEM_RE = re.compile(
    r"^(\S+)\s+(PASSED|FAILED|ERROR)(?:\s+\[.*?\])?\s*$",
    re.MULTILINE,
)


def _git(repo: Path, *args: str) -> str:
    """Run a git command in repo and return stdout (stripped)."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _run_tests(checkout: Path, test_command: str, test_files: list[str]) -> dict[str, str]:
    """Run pytest with -v --tb=no and parse per-test PASSED/FAILED/ERROR.

    Returns a dict of {nodeid: "PASSED" | "FAILED" | "ERROR"}.
    """
    import shlex

    try:
        cmd_parts = shlex.split(test_command)
    except ValueError:
        return {}

    cmd = cmd_parts + ["--tb=no", "-v"] + test_files
    proc = subprocess.run(
        cmd,
        cwd=str(checkout.resolve()),
        capture_output=True,
        text=True,
        timeout=300,
    )
    stdout = proc.stdout or ""
    results: dict[str, str] = {}
    for m in _PYTEST_ITEM_RE.finditer(stdout):
        nodeid, status = m.group(1), m.group(2)
        results[nodeid] = status
    return results


def _filter_test_files(files: list[str]) -> list[str]:
    """Return only files that look like test files."""
    out = []
    for path in files:
        norm = path.replace("\\", "/")
        p = Path(path)
        if (
            "/tests/" in norm
            or "/test/" in norm
            or norm.startswith("tests/")
            or norm.startswith("test/")
            or p.name.startswith("test_")
            or p.stem.endswith("_test")
            or p.stem.endswith(".test")
        ):
            out.append(path)
    return out


def compute_for_task(
    checkout: Path,
    test_command: str,
    test_files: list[str],
    parent_sha: str,
    merge_sha: str,
    *,
    verbose: bool = True,
) -> tuple[list[str], list[str]]:
    """Return (fail_to_pass, pass_to_pass) for a single task.

    Leaves checkout at the original HEAD (the caller's try/finally handles repo restore).
    """
    orig_head = _git(checkout, "rev-parse", "HEAD")

    try:
        if verbose:
            print(f"  [parent] checkout {parent_sha[:12]}")
        _git(checkout, "checkout", "--quiet", parent_sha)
        parent_results = _run_tests(checkout, test_command, test_files)
        if verbose:
            print(f"    → {len(parent_results)} test results at parent")

        if verbose:
            print(f"  [merge ] checkout {merge_sha[:12]}")
        _git(checkout, "checkout", "--quiet", merge_sha)
        merge_results = _run_tests(checkout, test_command, test_files)
        if verbose:
            print(f"    → {len(merge_results)} test results at merge")
    finally:
        _git(checkout, "checkout", "--quiet", orig_head)

    # Compute FAIL_TO_PASS: tests that PASS at merge but were not PASSING at parent.
    # This covers:
    #  - Tests that explicitly FAIL or ERROR at parent.
    #  - Tests that are newly added in the PR (absent at parent — not collected → not in
    #    parent_results). SWE-Bench treats these as "failing" at parent because the spec
    #    behavior they exercise did not exist.
    fail_to_pass = sorted(
        nodeid
        for nodeid in merge_results
        if merge_results[nodeid] == "PASSED"
        and parent_results.get(nodeid) != "PASSED"
    )
    # Compute PASS_TO_PASS: PASSed at both parent and merge (regression coverage)
    pass_to_pass = sorted(
        nodeid
        for nodeid in merge_results
        if merge_results[nodeid] == "PASSED"
        and parent_results.get(nodeid) == "PASSED"
    )

    if verbose:
        print(f"    FAIL_TO_PASS: {len(fail_to_pass)}")
        print(f"    PASS_TO_PASS: {len(pass_to_pass)}")

    return fail_to_pass, pass_to_pass


def process_repo(repo_entry: dict, *, force: bool = False, verbose: bool = True) -> bool:
    """Process all tasks for one repo entry. Returns True if any tasks were updated."""
    short_name = repo_entry.get("short_name", "?")
    checkout_rel = repo_entry.get("checkout_path", "")
    # checkout_path in manifest is relative to the project root (cognition/)
    # MANIFEST_PATH = experiments/real_repos/manifest.json → .parent.parent.parent = project root
    project_root = MANIFEST_PATH.parent.parent.parent
    checkout = project_root / checkout_rel

    if not checkout.exists():
        print(f"  [SKIP] {short_name}: checkout not found at {checkout}")
        return False

    test_command = repo_entry.get("test_command")
    if not test_command:
        print(f"  [SKIP] {short_name}: no test_command in manifest")
        return False

    tasks = repo_entry.get("tasks", [])
    if not tasks:
        print(f"  [SKIP] {short_name}: no tasks in manifest")
        return False

    orig_head = _git(checkout, "rev-parse", "HEAD")
    updated = False

    try:
        for task in tasks:
            pr_number = task.get("pr_number", "?")
            has_ftp = "fail_to_pass" in task and "pass_to_pass" in task

            if has_ftp and not force:
                if verbose:
                    print(f"  [skip] pr{pr_number}: already has fail_to_pass/pass_to_pass (use --force to recompute)")
                continue

            parent_sha = task.get("parent_commit_sha")
            merge_sha = task.get("merge_commit_sha")
            if not parent_sha or not merge_sha:
                print(f"  [SKIP] pr{pr_number}: missing parent_commit_sha or merge_commit_sha")
                continue

            ground_truth_files = task.get("ground_truth_files", [])
            test_files = _filter_test_files(ground_truth_files)
            if not test_files:
                print(f"  [SKIP] pr{pr_number}: no test files in ground_truth_files")
                continue

            if verbose:
                print(f"  pr{pr_number} — test files: {test_files}")

            try:
                fail_to_pass, pass_to_pass = compute_for_task(
                    checkout,
                    test_command,
                    test_files,
                    parent_sha,
                    merge_sha,
                    verbose=verbose,
                )
                task["fail_to_pass"] = fail_to_pass
                task["pass_to_pass"] = pass_to_pass
                updated = True
            except subprocess.CalledProcessError as exc:
                print(f"  [ERROR] pr{pr_number}: git error: {exc}")
            except subprocess.TimeoutExpired:
                print(f"  [ERROR] pr{pr_number}: test run timed out")
            except Exception as exc:
                print(f"  [ERROR] pr{pr_number}: {type(exc).__name__}: {exc}")
    finally:
        _git(checkout, "checkout", "--quiet", orig_head)
        if verbose:
            print(f"  Restored {short_name} checkout to {orig_head[:12]}")

    return updated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=None, help="Short name of repo to process (default: all)")
    parser.add_argument("--force", action="store_true", help="Recompute even if fields already exist")
    parser.add_argument("--manifest", default=str(MANIFEST_PATH), help="Path to manifest.json")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    repos = manifest.get("repos", [])
    if args.repo:
        repos_to_process = [r for r in repos if r.get("short_name") == args.repo]
        if not repos_to_process:
            print(f"ERROR: repo '{args.repo}' not found in manifest", file=sys.stderr)
            sys.exit(1)
    else:
        repos_to_process = repos

    any_updated = False
    for repo_entry in repos_to_process:
        short = repo_entry.get("short_name", "?")
        print(f"\n=== {short} ===")
        updated = process_repo(repo_entry, force=args.force)
        if updated:
            any_updated = True

    if any_updated:
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)
            f.write("\n")
        print(f"\nWrote updated manifest to {manifest_path}")
    else:
        print("\nNo tasks updated.")


if __name__ == "__main__":
    main()
