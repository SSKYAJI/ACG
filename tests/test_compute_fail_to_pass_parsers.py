"""Unit tests for the test-runner parsers in experiments.real_repos._parsers."""

from __future__ import annotations

from experiments.real_repos._parsers import (
    parse_pytest_output,
    parse_tap_output,
    parse_vitest_json,
)


def test_pytest_parses_passed_and_failed():
    stdout = """\
tests/test_foo.py::test_a PASSED                  [ 33%]
tests/test_foo.py::test_b FAILED                  [ 66%]
tests/test_foo.py::test_c[asyncio] PASSED         [100%]
"""
    out = parse_pytest_output(stdout)
    assert out == {
        "tests/test_foo.py::test_a": "PASSED",
        "tests/test_foo.py::test_b": "FAILED",
        "tests/test_foo.py::test_c[asyncio]": "PASSED",
    }


def test_pytest_handles_error():
    stdout = "tests/test_x.py::test_y ERROR\n"
    out = parse_pytest_output(stdout)
    assert out == {"tests/test_x.py::test_y": "ERROR"}


def test_pytest_ignores_non_test_lines():
    stdout = """\
============================= test session starts =============================
collected 2 items

tests/test_a.py::test_ok PASSED                                          [ 50%]
tests/test_a.py::test_bad FAILED                                         [100%]

============================== short test summary ==============================
FAILED tests/test_a.py::test_bad - AssertionError
"""
    out = parse_pytest_output(stdout)
    assert out == {
        "tests/test_a.py::test_ok": "PASSED",
        "tests/test_a.py::test_bad": "FAILED",
    }


def test_vitest_parses_basic_results():
    stdout = """\
RUN  v1.6.0 /repo

 ✓ tests/foo.test.ts (2)

{"numTotalTests":2,"testResults":[{"name":"/repo/tests/foo.test.ts","assertionResults":[{"fullName":"foo > works","title":"works","status":"passed","ancestorTitles":["foo"]},{"fullName":"foo > fails","title":"fails","status":"failed","ancestorTitles":["foo"]}]}]}
"""
    out = parse_vitest_json(stdout)
    assert out == {
        "/repo/tests/foo.test.ts::foo > works": "PASSED",
        "/repo/tests/foo.test.ts::foo > fails": "FAILED",
    }


def test_vitest_handles_skipped_as_error():
    stdout = (
        '{"testResults":[{"name":"f.test.ts","assertionResults":'
        '[{"fullName":"x","title":"x","status":"skipped","ancestorTitles":[]}]}]}'
    )
    out = parse_vitest_json(stdout)
    assert out == {"f.test.ts::x": "ERROR"}


def test_vitest_returns_empty_on_no_json():
    assert parse_vitest_json("vitest hung — no output") == {}
    assert parse_vitest_json("") == {}


def test_tap_parses_ok_and_not_ok():
    stdout = """\
TAP version 13
# Subtest: foo
ok 1 - foo works
# Subtest: bar
not ok 2 - bar fails
  ---
  duration_ms: 1.2
  ...
1..2
"""
    out = parse_tap_output(stdout, ["test/example.test.js"])
    assert out == {
        "test/example.test.js::foo works": "PASSED",
        "test/example.test.js::bar fails": "FAILED",
    }


def test_tap_handles_multiple_test_files_no_prefix():
    stdout = "ok 1 - alpha\nnot ok 2 - beta\n"
    out = parse_tap_output(stdout, ["a.test.js", "b.test.js"])
    assert out == {"alpha": "PASSED", "beta": "FAILED"}


def test_tap_ignores_diagnostic_lines():
    stdout = """\
TAP version 14
# tests 3
# pass 2
# fail 1
ok 1 - first
ok 2 - second
not ok 3 - third
"""
    out = parse_tap_output(stdout, ["test/x.js"])
    assert out == {
        "test/x.js::first": "PASSED",
        "test/x.js::second": "PASSED",
        "test/x.js::third": "FAILED",
    }


def test_tap_strips_trailing_directive():
    stdout = "ok 1 - skipped test # SKIP not on this platform\n"
    out = parse_tap_output(stdout, ["t.js"])
    assert out == {"t.js::skipped test": "PASSED"}
