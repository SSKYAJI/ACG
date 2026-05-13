"""Regression tests for worker ``proposal_status`` classification."""

from __future__ import annotations

import asyncio

import pytest

from acg.runtime import (
    LLMReply,
    MockRuntimeLLM,
    RuntimeConfig,
    RuntimeLLMError,
    RuntimeLLMProtocol,
    run_worker,
)
from acg.runtime_proposal import (
    PROPOSAL_DECLINED,
    PROPOSAL_TRANSPORT_ERROR,
    PROPOSAL_TRUNCATED,
    PROPOSAL_UNPARSEABLE,
)
from acg.schema import AgentLock, ExecutionPlan, Group, PredictedWrite, Repo, Task


def _lock(task_id: str) -> AgentLock:
    task = Task(
        id=task_id,
        prompt=f"Update src/a.ts ({task_id})",
        predicted_writes=[PredictedWrite(path="src/a.ts", confidence=0.9, reason="test")],
        allowed_paths=["src/**"],
        parallel_group=1,
    )
    return AgentLock(
        generated_at=AgentLock.utcnow(),
        repo=Repo(root="demo", languages=["typescript"]),
        tasks=[task],
        execution_plan=ExecutionPlan(groups=[Group(id=1, tasks=[task_id], type="parallel")]),
    )


@pytest.fixture
def tiny_repo_graph() -> dict[str, object]:
    return {"files": [{"path": "src/a.ts", "imported_by_count": 1}]}


def test_worker_proposal_status_truncated_mock(tiny_repo_graph: dict[str, object]) -> None:
    tid = "sil_trunc"
    lock = _lock(tid)
    llm = MockRuntimeLLM(
        worker_replies_by_task_id={
            tid: LLMReply(
                content="*** Begin Patch\n*** Update File: src/a.ts\n",
                reasoning="",
                completion_tokens=4096,
                finish_reason="length",
                wall_s=0.1,
            ),
        },
    )
    wr = asyncio.run(run_worker(lock.tasks[0], lock, tiny_repo_graph, llm, group_id=1))
    assert wr.proposal_status == PROPOSAL_TRUNCATED
    assert wr.proposals == []


def test_worker_proposal_status_unparseable_mock(tiny_repo_graph: dict[str, object]) -> None:
    tid = "sil_unparse"
    lock = _lock(tid)
    llm = MockRuntimeLLM(
        worker_replies_by_task_id={
            tid: LLMReply(
                content="this is not valid json or an apply_patch envelope {",
                reasoning="",
                completion_tokens=12,
                finish_reason="stop",
                wall_s=0.02,
            ),
        },
    )
    wr = asyncio.run(run_worker(lock.tasks[0], lock, tiny_repo_graph, llm, group_id=1))
    assert wr.proposal_status == PROPOSAL_UNPARSEABLE
    assert wr.proposals == []


def test_worker_proposal_status_declined_mock(tiny_repo_graph: dict[str, object]) -> None:
    tid = "sil_decline"
    lock = _lock(tid)
    llm = MockRuntimeLLM(
        worker_replies_by_task_id={
            tid: LLMReply(
                content="I'm sorry, but I can't help with that request.",
                reasoning="",
                completion_tokens=20,
                finish_reason="stop",
                wall_s=0.02,
            ),
        },
    )
    wr = asyncio.run(run_worker(lock.tasks[0], lock, tiny_repo_graph, llm, group_id=1))
    assert wr.proposal_status == PROPOSAL_DECLINED
    assert wr.proposals == []


class _BoomLLM:
    """Raises :class:`RuntimeLLMError` like a failed transport."""

    url = "mock://boom"
    model = "boom"

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> LLMReply:
        del messages, max_tokens, temperature
        raise RuntimeLLMError("transport error contacting https://example.invalid/v1: boom")

    async def aclose(self) -> None:
        return None


def test_worker_proposal_status_transport_error(tiny_repo_graph: dict[str, object]) -> None:
    lock = _lock("sil_transport")
    llm: RuntimeLLMProtocol = _BoomLLM()
    wr = asyncio.run(run_worker(lock.tasks[0], lock, tiny_repo_graph, llm, group_id=1))
    assert wr.proposal_status == PROPOSAL_TRANSPORT_ERROR
    assert wr.error is not None


def test_runtime_config_worker_max_tokens_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACG_WORKER_MAX_TOKENS", "8192")
    cfg = RuntimeConfig.from_env()
    assert cfg.worker_max_tokens == 8192
