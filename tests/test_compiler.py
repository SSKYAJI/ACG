"""Compiler allowed-path tests."""

from __future__ import annotations

from acg.compiler import _to_allowed_path
from acg.schema import PredictedWrite


def test_to_allowed_path_broadens_shallow_test_path() -> None:
    write = PredictedWrite(
        path="tests/e2e/checkout.spec.ts",
        confidence=0.85,
        reason="Playwright convention.",
    )
    assert _to_allowed_path(write) == "tests/e2e/**"


def test_to_allowed_path_keeps_non_test_shallow_path_exact() -> None:
    write = PredictedWrite(
        path="src/server/stripe.ts",
        confidence=0.85,
        reason="Stripe service.",
    )
    assert _to_allowed_path(write) == "src/server/stripe.ts"
