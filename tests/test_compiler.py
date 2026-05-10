"""Compiler allowed-path tests."""

from __future__ import annotations

from acg.compiler import (
    _build_allowed_paths,
    _candidate_context_paths,
    _must_writes,
    _to_allowed_path,
    promote_candidate_paths,
)
from acg.schema import AgentLock, ExecutionPlan, FileScope, Group, PredictedWrite, Repo, Task


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


def test_allowed_paths_use_only_must_write_scopes() -> None:
    scopes = [
        FileScope(
            path="src/server/stripe.ts",
            tier="must_write",
            score=0.9,
            signals=["llm"],
            reason="Strong LLM signal.",
        ),
        FileScope(
            path="src/server/billing.ts",
            tier="candidate_context",
            score=0.78,
            signals=["bm25", "graph"],
            reason="Context only.",
        ),
    ]

    writes = _must_writes(scopes)

    assert [write.path for write in writes] == ["src/server/stripe.ts"]
    assert _build_allowed_paths(writes) == ["src/server/stripe.ts"]
    assert _candidate_context_paths(scopes) == ["src/server/billing.ts"]


def test_promote_candidate_paths_recomputes_allowed_paths_and_conflicts() -> None:
    lock = AgentLock(
        generated_at=AgentLock.utcnow(),
        repo=Repo(root="/repo", languages=["typescript"]),
        tasks=[
            Task(
                id="a",
                prompt="task a",
                predicted_writes=[],
                allowed_paths=[],
                candidate_context_paths=["src/shared.ts"],
                file_scopes=[
                    FileScope(
                        path="src/shared.ts",
                        tier="candidate_context",
                        score=0.86,
                        signals=["scope_review"],
                        reason="candidate",
                    )
                ],
                depends_on=[],
            ),
            Task(
                id="b",
                prompt="task b",
                predicted_writes=[
                    PredictedWrite(path="src/shared.ts", confidence=0.9, reason="seed")
                ],
                allowed_paths=["src/shared.ts"],
                depends_on=[],
            ),
        ],
        execution_plan=ExecutionPlan(
            groups=[Group(id=1, tasks=["a", "b"], type="parallel", waits_for=[])]
        ),
    )

    promoted = promote_candidate_paths(lock, "a", ["src/shared.ts"])

    assert promoted == ["src/shared.ts"]
    assert lock.tasks[0].allowed_paths == ["src/shared.ts"]
    assert lock.conflicts_detected
    assert any(group.type == "serial" for group in lock.execution_plan.groups)
