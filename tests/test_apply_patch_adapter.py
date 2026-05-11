"""Tests for :mod:`acg.apply_patch_adapter`."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from acg.apply_patch_adapter import AppliedPatchResult, apply_envelope


def test_apply_envelope_updates_existing_file() -> None:
    root = Path(tempfile.mkdtemp())
    (root / "foo.txt").write_text("hello\nworld\n", encoding="utf-8")
    env = """*** Begin Patch
*** Update File: foo.txt
@@
-hello
+HELLO
*** End Patch
"""
    res = apply_envelope(env, root)
    assert isinstance(res, AppliedPatchResult)
    assert res.envelope_parsed is True
    assert res.errors == []
    assert "foo.txt" in res.changed_files
    assert "HELLO" in (root / "foo.txt").read_text(encoding="utf-8")


def test_apply_envelope_creates_new_file() -> None:
    root = Path(tempfile.mkdtemp())
    env = """*** Begin Patch
*** Add File: new.txt
+alpha
+beta
*** End Patch
"""
    res = apply_envelope(env, root)
    assert res.envelope_parsed is True
    assert res.errors == []
    assert "new.txt" in res.changed_files
    assert (root / "new.txt").read_text(encoding="utf-8") == "alpha\nbeta\n"


def test_apply_envelope_returns_empty_on_malformed_input() -> None:
    root = Path(tempfile.mkdtemp())
    res = apply_envelope("this is not a patch", root)
    assert res.envelope_parsed is False
    assert res.changed_files == []
    assert res.errors


def test_apply_envelope_rejects_path_traversal() -> None:
    root = Path(tempfile.mkdtemp())
    env = "\n".join(
        [
            "*** Begin Patch",
            "*** Add File: ../../etc/passwd",
            "+oops",
            "*** End Patch",
        ]
    )
    res = apply_envelope(env, root)
    assert res.envelope_parsed is True
    assert res.changed_files == []
    assert any("unsafe" in e.lower() for e in res.errors)
