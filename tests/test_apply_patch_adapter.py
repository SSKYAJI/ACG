"""Tests for :mod:`acg.apply_patch_adapter`."""

from __future__ import annotations

import tempfile
from pathlib import Path

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


def test_apply_envelope_returns_patch_na_outcome_on_apply_error() -> None:
    """A patch missing its terminator triggers ``parse_patch``'s ValueError
    path. The adapter must surface that as ``patch_na=True`` with a truncated
    reason and ``envelope_parsed=False`` rather than raising."""
    root = Path(tempfile.mkdtemp())
    (root / "foo.txt").write_text("hello\n", encoding="utf-8")
    env = "*** Begin Patch\n*** Update File: foo.txt\n@@\n-hello\n+HELLO\n"  # no End Patch
    res = apply_envelope(env, root)
    assert res.envelope_parsed is False
    assert res.patch_na is True
    assert isinstance(res.patch_na_reason, str)
    assert res.patch_na_reason  # non-empty
    assert len(res.patch_na_reason) <= 300
    assert res.changed_files == []


def test_apply_envelope_returns_patch_na_on_malformed_input() -> None:
    """Malformed input must not raise — it must surface as ``patch_na=True``
    with ``envelope_parsed=False`` and a non-empty reason."""
    root = Path(tempfile.mkdtemp())
    res = apply_envelope("this is not a patch", root)
    assert res.envelope_parsed is False
    assert res.patch_na is True
    assert res.patch_na_reason
    assert res.changed_files == []


def test_apply_envelope_marks_path_traversal_as_patch_na() -> None:
    """Path-traversal rejections also mean we don't have an applied patch,
    so ``patch_na`` must be true with an unsafe-path reason."""
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
    assert res.patch_na is True
    assert res.patch_na_reason and "unsafe" in res.patch_na_reason.lower()


def test_apply_envelope_success_marks_patch_na_false() -> None:
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
    assert res.patch_na is False
    assert res.patch_na_reason is None
    assert "foo.txt" in res.changed_files


def test_applied_patch_outcome_is_alias_for_applied_patch_result() -> None:
    """The plan-aligned ``AppliedPatchOutcome`` name must be the same type
    so callers can import either spelling."""
    from acg.apply_patch_adapter import AppliedPatchOutcome

    assert AppliedPatchOutcome is AppliedPatchResult
