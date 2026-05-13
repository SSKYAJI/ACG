"""Compute FAIL_TO_PASS / PASS_TO_PASS per task and write into manifest.json.

For each task in manifest.json the script:
  1. Records the current HEAD so it can restore it.
  2. Checks out ``parent_commit_sha`` and runs the test runner on the ground-truth
     test files.
  3. Checks out ``merge_commit_sha`` and runs the same.
  4. Derives:
     - FAIL_TO_PASS: tests that PASS at merge but did NOT PASS at parent.
     - PASS_TO_PASS: tests that PASS at both.
  5. Writes both lists into the task entry.
  6. Restores the checkout to the original HEAD (try/finally).

Test runner dispatch uses the manifest entry's ``test_runner`` field
("pytest" | "vitest" | "node:test"). See ._parsers.

Concurrency: manifest writes are guarded by ``fcntl.flock`` on
``manifest.json.lock`` so multiple parallel invocations (one per --repo) can
safely merge their FTP/PTP updates into the shared manifest. The flock is
only held during the read-merge-write window, not while tests are running.

Usage:
    python -m experiments.real_repos.compute_fail_to_pass [--repo NAME] [--force]
"""

from __future__ import annotations

import argparse
import fcntl
import json
import subprocess
import sys
from pathlib import Path

from experiments.real_repos._parsers import run_node_test, run_pytest, run_vitest

MANIFEST_PATH = Path(__file__).parent / "manifest.json"


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _dispatch(
    cwd: Path,
    test_command: str,
    test_files: list[str],
    test_runner: str,
) -> dict[str, str]:
    if test_runner == "pytest":
        return run_pytest(cwd, test_command, test_files)
    if test_runner == "vitest":
        return run_vitest(cwd, test_command, test_files)
    if test_runner == "node:test":
        return run_node_test(cwd, test_command, test_files)
    raise ValueError(f"Unknown test_runner: {test_runner!r}")


def _filter_test_files(files: list[str]) -> list[str]:
    out: list[str] = []
    for path in files:
        norm = path.replace("\\", "/")
        p = Path(path)
        name_lower = p.name.lower()
        in_test_dir = (
            "/tests/" in norm
            or "/test/" in norm
            or norm.startswith("tests/")
            or norm.startswith("test/")
        )
        looks_like_test = (
            p.name.startswith("test_")
            or p.stem.endswith("_test")
            or p.stem.endswith(".test")
            or p.stem.endswith(".spec")
            or "spec" in name_lower
        )
        if in_test_dir or looks_like_test:
            out.append(path)
    return out


def compute_for_task(
    checkout: Path,
    test_command: str,
    test_files: list[str],
    parent_sha: str,
    merge_sha: str,
    *,
    test_runner: str = "pytest",
    working_directory: str | None = None,
    verbose: bool = True,
) -> tuple[list[str], list[str]]:
    cwd = checkout / working_directory if working_directory else checkout
    orig_head = _git(checkout, "rev-parse", "HEAD")
    try:
        if verbose:
            print(f"  [parent] checkout {parent_sha[:12]}")
        _git(checkout, "checkout", "--quiet", parent_sha)
        parent_results = _dispatch(cwd, test_command, test_files, test_runner)
        if verbose:
            print(f"    → {len(parent_results)} test results at parent")

        if verbose:
            print(f"  [merge ] checkout {merge_sha[:12]}")
        _git(checkout, "checkout", "--quiet", merge_sha)
        merge_results = _dispatch(cwd, test_command, test_files, test_runner)
        if verbose:
            print(f"    → {len(merge_results)} test results at merge")
    finally:
        _git(checkout, "checkout", "--quiet", orig_head)

    fail_to_pass = sorted(
        nid for nid in merge_results
        if merge_results[nid] == "PASSED" and parent_results.get(nid) != "PASSED"
    )
    pass_to_pass = sorted(
        nid for nid in merge_results
        if merge_results[nid] == "PASSED" and parent_results.get(nid) == "PASSED"
    )
    if verbose:
        print(f"    FAIL_TO_PASS: {len(fail_to_pass)}")
        print(f"    PASS_TO_PASS: {len(pass_to_pass)}")
    return fail_to_pass, pass_to_pass


def process_repo(repo_entry: dict, *, force: bool = False, verbose: bool = True) -> bool:
    short_name = repo_entry.get("short_name", "?")
    checkout_rel = repo_entry.get("checkout_path", "")
    project_root = MANIFEST_PATH.parent.parent.parent
    checkout = project_root / checkout_rel
    if not checkout.exists():
        print(f"  [SKIP] {short_name}: checkout not found at {checkout}")
        return False
    test_command = repo_entry.get("test_command")
    if not test_command:
        print(f"  [SKIP] {short_name}: no test_command in manifest")
        return False
    test_runner = repo_entry.get("test_runner", "pytest")
    working_directory = repo_entry.get("working_directory")
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
                    print(f"  [skip] pr{pr_number}: already populated (use --force)")
                continue
            parent_sha = task.get("parent_commit_sha")
            merge_sha = task.get("merge_commit_sha")
            if not parent_sha or not merge_sha:
                print(f"  [SKIP] pr{pr_number}: missing parent/merge commit sha")
                continue
            gtruth = task.get("ground_truth_files", [])
            test_files = _filter_test_files(gtruth)
            if not test_files:
                print(f"  [SKIP] pr{pr_number}: no test files in ground_truth_files")
                continue
            if verbose:
                print(f"  pr{pr_number} — test files: {test_files}")
            try:
                ftp, ptp = compute_for_task(
                    checkout, test_command, test_files, parent_sha, merge_sha,
                    test_runner=test_runner,
                    working_directory=working_directory,
                    verbose=verbose,
                )
                task["fail_to_pass"] = ftp
                task["pass_to_pass"] = ptp
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


def _atomic_merge_write(manifest_path: Path, repo_name: str, updated_repo: dict) -> None:
    lock_path = manifest_path.with_suffix(".json.lock")
    lock_path.touch(exist_ok=True)
    with open(lock_path) as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            with open(manifest_path, encoding="utf-8") as f:
                fresh = json.load(f)
            for i, r in enumerate(fresh.get("repos", [])):
                if r.get("short_name") == repo_name:
                    fresh["repos"][i] = updated_repo
                    break
            else:
                fresh.setdefault("repos", []).append(updated_repo)
            tmp = manifest_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(fresh, f, indent=2)
                f.write("\n")
            tmp.replace(manifest_path)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=None, help="Short name of repo (default: all)")
    parser.add_argument("--force", action="store_true", help="Recompute even if populated")
    parser.add_argument("--manifest", default=str(MANIFEST_PATH))
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    repos = manifest.get("repos", [])
    targets = [r for r in repos if r.get("short_name") == args.repo] if args.repo else repos
    if args.repo and not targets:
        print(f"ERROR: repo '{args.repo}' not found in manifest", file=sys.stderr)
        sys.exit(1)

    for repo_entry in targets:
        short = repo_entry.get("short_name", "?")
        print(f"\n=== {short} ===")
        if process_repo(repo_entry, force=args.force):
            _atomic_merge_write(manifest_path, short, repo_entry)
            print(f"  → merged {short} update into {manifest_path}")


if __name__ == "__main__":
    main()
