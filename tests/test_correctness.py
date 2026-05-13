"""Tests for acg/correctness.py — PR-scoped test execution gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from acg.correctness import (
    CorrectnessOutcome,
    filter_test_files,
    overlay_canonical_tests,
    parse_pytest_output,
    parse_pytest_per_test,
    resolve_task_meta,
    run_pr_tests,
    score_fail_to_pass,
)

_MANIFEST_PATH = Path(__file__).parent.parent / "experiments" / "real_repos" / "manifest.json"


# ---------------------------------------------------------------------------
# parse_pytest_output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "stdout,expected",
    [
        # Clean pass
        (
            "collected 5 items\n\n...\n\n5 passed in 0.12s",
            (5, 0, 5),
        ),
        # Mixed pass + fail
        (
            "collected 6 items\n\n...\n\n4 passed, 2 failed in 1.34s",
            (4, 2, 6),
        ),
        # Fail only
        (
            "collected 3 items\n\n...\n\n3 failed in 0.98s",
            (0, 3, 3),
        ),
        # Pass + skipped (skipped does not count as failure)
        (
            "collected 4 items\n\n...\n\n3 passed, 1 skipped in 0.22s",
            (3, 0, 3),
        ),
        # No tests ran
        (
            "no tests ran",
            (0, 0, 0),
        ),
        # Completely unparseable
        (
            "something something something",
            (None, None, None),
        ),
    ],
)
def test_parse_pytest_output_handles_common_formats(stdout, expected):
    assert parse_pytest_output(stdout) == expected


# ---------------------------------------------------------------------------
# resolve_task_meta
# ---------------------------------------------------------------------------


def test_resolve_task_meta_finds_starlette_pr3166():
    if not _MANIFEST_PATH.exists():
        pytest.skip("manifest.json not present")
    from acg.correctness import load_manifest

    manifest = load_manifest(_MANIFEST_PATH)
    test_command, ground_truth_files, parent_sha, fail_to_pass, pass_to_pass, _merge_sha = resolve_task_meta(
        manifest, "starlette", "pr3166-session-middleware"
    )
    assert test_command == "./.venv/bin/python -m pytest"
    assert any("test_session.py" in f for f in ground_truth_files), (
        f"expected test_session.py in {ground_truth_files}"
    )
    assert parent_sha is not None
    # After compute_fail_to_pass.py runs for starlette, these should be populated.
    assert isinstance(fail_to_pass, list)
    assert isinstance(pass_to_pass, list)


# ---------------------------------------------------------------------------
# filter_test_files
# ---------------------------------------------------------------------------


def test_filter_test_files_keeps_only_tests():
    mixed = [
        "starlette/middleware/sessions.py",  # source — excluded
        "tests/middleware/test_session.py",  # /tests/ segment — included
        "test/helpers/util.py",  # /test/ segment — included
        "src/black/__init__.py",  # source — excluded
        "tests/test_templates.py",  # /tests/ segment — included
        "lib/command.js",  # source — excluded
        "tests/argument.variadic.test.js",  # /tests/ segment — included
        "src/foo_test.py",  # _test suffix — included
        "src/bar.test.ts",  # .test. suffix — included
    ]
    result = filter_test_files(mixed)
    assert "starlette/middleware/sessions.py" not in result
    assert "src/black/__init__.py" not in result
    assert "lib/command.js" not in result
    assert "tests/middleware/test_session.py" in result
    assert "test/helpers/util.py" in result
    assert "tests/test_templates.py" in result
    assert "tests/argument.variadic.test.js" in result
    assert "src/foo_test.py" in result
    assert "src/bar.test.ts" in result


# ---------------------------------------------------------------------------
# run_pr_tests — skip path (no subprocess required)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# parse_pytest_per_test
# ---------------------------------------------------------------------------


def test_parse_pytest_per_test_handles_percentage_suffix():
    stdout = (
        "tests/test_foo.py::test_bar[asyncio] PASSED                  [  7%]\n"
        "tests/test_foo.py::test_baz FAILED\n"
        "tests/test_foo.py::test_qux PASSED         [ 15%]\n"
        "tests/test_foo.py::test_err ERROR\n"
    )
    result = parse_pytest_per_test(stdout)
    assert result["tests/test_foo.py::test_bar[asyncio]"] == "PASSED"
    assert result["tests/test_foo.py::test_baz"] == "FAILED"
    assert result["tests/test_foo.py::test_qux"] == "PASSED"
    assert result["tests/test_foo.py::test_err"] == "ERROR"


def test_score_fail_to_pass_counts_correctly():
    per_test = {
        "tests/test_foo.py::test_new": "PASSED",   # new test: FTP
        "tests/test_foo.py::test_existing": "PASSED",  # existing: PTP
        "tests/test_foo.py::test_broken": "FAILED",   # existing now fails
        "tests/test_foo.py::test_unrelated": "PASSED",
    }
    fail_to_pass = ["tests/test_foo.py::test_new"]
    pass_to_pass = ["tests/test_foo.py::test_existing", "tests/test_foo.py::test_broken"]

    ftp_p, ftp_t, ptp_p, ptp_t = score_fail_to_pass(per_test, fail_to_pass, pass_to_pass)
    assert ftp_p == 1  # test_new passes
    assert ftp_t == 1
    assert ptp_p == 1  # test_existing passes; test_broken does not
    assert ptp_t == 2


def test_score_fail_to_pass_empty_lists():
    per_test = {"tests/test_foo.py::test_bar": "PASSED"}
    ftp_p, ftp_t, ptp_p, ptp_t = score_fail_to_pass(per_test, [], [])
    assert ftp_p == 0 and ftp_t == 0 and ptp_p == 0 and ptp_t == 0


# ---------------------------------------------------------------------------
# run_pr_tests — skip path (no subprocess required)
# ---------------------------------------------------------------------------


def test_run_pr_tests_skips_when_no_test_command(tmp_path: Path):
    """A repo_short_name that doesn't appear in the manifest should produce ran=False."""
    if not _MANIFEST_PATH.exists():
        pytest.skip("manifest.json not present")

    # "nonexistent_repo" is not in the manifest so resolve_task_meta returns
    # (None, [], None) → skip_reason="no_test_command"
    outcome = run_pr_tests(
        checkout=tmp_path,
        repo_short_name="nonexistent_repo",
        task_id="pr9999-fake-task",
        manifest_path=_MANIFEST_PATH,
    )
    assert isinstance(outcome, CorrectnessOutcome)
    assert outcome.ran is False
    assert outcome.skip_reason == "no_test_command"


# ---------------------------------------------------------------------------
# overlay_canonical_tests
# ---------------------------------------------------------------------------


def _make_git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with two commits and return its path."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.local"],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True, capture_output=True,
    )
    # Commit 1 — "parent" state
    test_file = repo / "tests" / "test_foo.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("def test_old(): pass\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "parent"],
        check=True, capture_output=True,
    )
    # Commit 2 — "merge" state with canonical test
    test_file.write_text("def test_canonical(): pass\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "merge"],
        check=True, capture_output=True,
    )
    return repo


def _get_sha(repo: Path, ref: str = "HEAD") -> str:
    import subprocess
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", ref],
        check=True, capture_output=True, text=True,
    )
    return proc.stdout.strip()


def test_overlay_canonical_tests_resets_to_merge_commit(tmp_path: Path):
    """Overlay replaces test file content with canonical merge_commit_sha version."""
    import json
    import subprocess

    repo = _make_git_repo(tmp_path)
    merge_sha = _get_sha(repo, "HEAD")
    parent_sha = _get_sha(repo, "HEAD~1")

    # Simulate agent writing a different test name on top of parent
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-b", "agent-work", parent_sha],
        check=True, capture_output=True,
    )
    test_file = repo / "tests" / "test_foo.py"
    test_file.write_text("def test_agent_wrote_this(): pass\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "agent"],
        check=True, capture_output=True,
    )
    assert "test_agent_wrote_this" in test_file.read_text()

    # Build a minimal manifest
    manifest = {
        "repos": [
            {
                "short_name": "myrepo",
                "test_command": "./.venv/bin/python -m pytest",
                "tasks": [
                    {
                        "pr_number": 42,
                        "ground_truth_files": ["tests/test_foo.py"],
                        "parent_commit_sha": parent_sha,
                        "merge_commit_sha": merge_sha,
                        "fail_to_pass": ["test_canonical"],
                        "pass_to_pass": [],
                    }
                ],
            }
        ]
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    result = overlay_canonical_tests(repo, "myrepo", "pr42-foo", manifest_path=manifest_path)

    assert result["overlay_applied"] is True
    assert "tests/test_foo.py" in result["test_files"]
    # File content should now be the canonical version
    assert "test_canonical" in test_file.read_text()
    assert "test_agent_wrote_this" not in test_file.read_text()


def test_overlay_skips_when_no_merge_commit_sha(tmp_path: Path):
    """When manifest entry lacks merge_commit_sha, overlay returns overlay_applied=False."""
    import json

    repo = _make_git_repo(tmp_path)
    parent_sha = _get_sha(repo, "HEAD~1")

    manifest = {
        "repos": [
            {
                "short_name": "myrepo",
                "test_command": "./.venv/bin/python -m pytest",
                "tasks": [
                    {
                        "pr_number": 7,
                        "ground_truth_files": ["tests/test_foo.py"],
                        "parent_commit_sha": parent_sha,
                        # No merge_commit_sha key
                        "fail_to_pass": [],
                        "pass_to_pass": [],
                    }
                ],
            }
        ]
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    result = overlay_canonical_tests(repo, "myrepo", "pr7-bar", manifest_path=manifest_path)

    assert result["overlay_applied"] is False
    assert result["skip_reason"] == "no_merge_commit_sha"
