from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from acg.typecheck import run_tsc_noemit

_FAKE_NPX = "/fake/npx"
_FAKE_CMD = [_FAKE_NPX, "--no-install", "tsc", "--noEmit"]


@pytest.fixture
def checkout_dir(tmp_path: Path) -> Path:
    p = tmp_path / "checkout"
    p.mkdir()
    return p


def test_run_tsc_noemit_returns_npx_missing_when_npx_absent(checkout_dir: Path) -> None:
    with patch("acg.typecheck.shutil.which", return_value=None):
        outcome = run_tsc_noemit(checkout_dir)
    assert outcome.ran is False
    assert outcome.skip_reason == "NPX_MISSING"
    assert outcome.exit_code is None


def test_run_tsc_noemit_returns_checkout_missing_when_path_does_not_exist(
    tmp_path: Path,
) -> None:
    outcome = run_tsc_noemit(tmp_path / "nope")
    assert outcome.ran is False
    assert outcome.skip_reason == "CHECKOUT_MISSING"


def test_run_tsc_noemit_returns_zero_diagnostics_on_clean_run(checkout_dir: Path) -> None:
    proc = subprocess.CompletedProcess(args=_FAKE_CMD, returncode=0, stdout="", stderr="")
    with (
        patch("acg.typecheck.shutil.which", return_value=_FAKE_NPX),
        patch("acg.typecheck.subprocess.run", return_value=proc),
    ):
        outcome = run_tsc_noemit(checkout_dir)
    assert outcome.ran is True
    assert outcome.exit_code == 0
    assert outcome.diagnostic_count == 0


def test_run_tsc_noemit_counts_diagnostics_from_stdout(checkout_dir: Path) -> None:
    stdout = (
        "src/foo.ts(12,5): error TS2322: Type 'string' is not assignable to type 'number'.\n"
        "src/bar.ts(3,1): error TS2554: Expected 1 arguments, but got 0.\n"
        "non-error line\n"
        "src/baz.ts(7,2): error TS7006: Parameter 'x' implicitly has an 'any' type.\n"
    )
    proc = subprocess.CompletedProcess(args=_FAKE_CMD, returncode=2, stdout=stdout, stderr="")
    with (
        patch("acg.typecheck.shutil.which", return_value=_FAKE_NPX),
        patch("acg.typecheck.subprocess.run", return_value=proc),
    ):
        outcome = run_tsc_noemit(checkout_dir)
    assert outcome.diagnostic_count == 3
    assert outcome.exit_code == 2


def test_run_tsc_noemit_marks_timeout(checkout_dir: Path) -> None:
    exc = subprocess.TimeoutExpired(_FAKE_CMD, timeout=180)
    with (
        patch("acg.typecheck.shutil.which", return_value=_FAKE_NPX),
        patch("acg.typecheck.subprocess.run", side_effect=exc),
    ):
        outcome = run_tsc_noemit(checkout_dir)
    assert outcome.ran is True
    assert outcome.exit_code == -1
    assert outcome.skip_reason == "TIMEOUT"


def test_run_tsc_noemit_marks_oserror(checkout_dir: Path) -> None:
    with (
        patch("acg.typecheck.shutil.which", return_value=_FAKE_NPX),
        patch("acg.typecheck.subprocess.run", side_effect=OSError(2, "No such file")),
    ):
        outcome = run_tsc_noemit(checkout_dir)
    assert outcome.ran is False
    assert outcome.skip_reason is not None
    assert outcome.skip_reason.startswith("OSERROR:")


def test_run_tsc_noemit_truncates_stdout_and_stderr_to_512_chars(checkout_dir: Path) -> None:
    proc = subprocess.CompletedProcess(
        args=_FAKE_CMD, returncode=0, stdout="a" * 2048, stderr="b" * 2048
    )
    with (
        patch("acg.typecheck.shutil.which", return_value=_FAKE_NPX),
        patch("acg.typecheck.subprocess.run", return_value=proc),
    ):
        outcome = run_tsc_noemit(checkout_dir)
    assert outcome.stdout_tail is not None and outcome.stderr_tail is not None
    assert len(outcome.stdout_tail) == len(outcome.stderr_tail) == 512
    assert outcome.stdout_tail == "a" * 512
    assert outcome.stderr_tail == "b" * 512


def test_run_tsc_noemit_accepts_custom_npx_executable(checkout_dir: Path) -> None:
    proc = subprocess.CompletedProcess(
        args=["/custom/path/npx", "--no-install", "tsc", "--noEmit"],
        returncode=0,
        stdout="",
        stderr="",
    )
    with patch("acg.typecheck.subprocess.run", return_value=proc) as mock_run:
        outcome = run_tsc_noemit(checkout_dir, npx_executable="/custom/path/npx")
    assert outcome.ran is True
    mock_run.assert_called_once()
    pos_args, _kwargs = mock_run.call_args
    assert pos_args[0] == ["/custom/path/npx", "--no-install", "tsc", "--noEmit"]


def test_run_tsc_noemit_uses_custom_timeout(checkout_dir: Path) -> None:
    exc = subprocess.TimeoutExpired(_FAKE_CMD, timeout=5)
    with (
        patch("acg.typecheck.shutil.which", return_value=_FAKE_NPX),
        patch("acg.typecheck.subprocess.run", side_effect=exc) as mock_run,
    ):
        outcome = run_tsc_noemit(checkout_dir, timeout_seconds=5)
    assert outcome.skip_reason == "TIMEOUT"
    mock_run.assert_called_once()
    _args, kwargs = mock_run.call_args
    assert kwargs["timeout"] == 5
