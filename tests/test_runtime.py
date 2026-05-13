"""Runtime tests with a stubbed async LLM client.

Uses :func:`asyncio.run` to drive the async entrypoints so we don't need a
``pytest-asyncio`` plugin. Mirrors the style of :mod:`tests.test_predictor`.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

import pytest

from acg.predictor import LLM_SEED_EXPANSION_REASON
from acg.runtime import (
    LLMReply,
    OrchestratorResult,
    RunResult,
    RuntimeConfig,
    WorkerResult,
    _build_worker_prompt,
    _parse_apply_envelope,
    run_group,
    run_lockfile,
    run_orchestrator,
    run_worker,
)
from acg.schema import AgentLock, ExecutionPlan, FileScope, Group, PredictedWrite, Task

# ---------------------------------------------------------------------------
# Test doubles.
# ---------------------------------------------------------------------------


class StubRuntimeLLM:
    """Async stand-in returning fixed replies based on the user-content blob.

    Two construction modes:

    1. ``replies={"oauth": '{"writes": [...]}', "billing": ...}`` — keyed by
       ``Task id: <id>`` substring match (mirrors ``MockLLMClient``).
    2. ``router=callable`` — for the orchestrator path or anything more
       complex; receives the joined user content and returns a string.
    """

    def __init__(
        self,
        replies: dict[str, str] | None = None,
        router: Callable[[str], str] | None = None,
        reasoning: str = "",
        url: str = "stub://runtime",
        model: str = "stub",
        finish_reason: str = "stop",
    ) -> None:
        self._replies = replies or {}
        self._router = router
        self._reasoning = reasoning
        self.url = url
        self.model = model
        self._finish_reason = finish_reason
        # Recorded for ordering assertions.
        self.calls: list[tuple[str, float]] = []
        self.delays: dict[str, float] = {}

    def set_delay(self, task_id: str, seconds: float) -> None:
        self.delays[task_id] = seconds

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> LLMReply:
        del max_tokens, temperature
        user_blob = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
        # Identify which task or whether this is the orchestrator.
        matched_task: str | None = None
        for tid in self._replies:
            if f"Task id: {tid}" in user_blob:
                matched_task = tid
                break

        if self._router is not None and matched_task is None:
            content = self._router(user_blob)
        elif matched_task is not None:
            content = self._replies[matched_task]
        else:
            # Orchestrator default reply when no router supplied.
            content = json.dumps({"approved": True, "concerns": [], "dispatch_order": [1, 2, 3]})

        delay = self.delays.get(matched_task or "_orchestrator_", 0.0)
        if delay:
            await asyncio.sleep(delay)
        else:
            await asyncio.sleep(0)

        self.calls.append((matched_task or "_orchestrator_", time.perf_counter()))

        return LLMReply(
            content=content,
            reasoning=self._reasoning if matched_task is None else "",
            completion_tokens=len(content) // 4,
            finish_reason=self._finish_reason,
            wall_s=delay,
        )

    async def aclose(self) -> None:
        return None


class RecordingConsole:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def print(self, *args: object, **kwargs: object) -> None:
        del kwargs
        self.messages.append(" ".join(str(arg) for arg in args))


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture
def lock(example_dag_lockfile_path: Path) -> AgentLock:
    return AgentLock.model_validate_json(example_dag_lockfile_path.read_text())


@pytest.fixture
def empty_repo_graph() -> dict[str, object]:
    return {"language": "typescript", "files": [], "hotspots": []}


def test_worker_prompt_invites_candidate_writes_with_runtime_guard(
    empty_repo_graph: dict[str, object],
) -> None:
    """Candidate context must not be labeled read-only; guard still described."""
    task = Task(
        id="oauth",
        prompt="Example task",
        predicted_writes=[PredictedWrite(path="src/must.js", confidence=0.9)],
        allowed_paths=["src/**"],
        candidate_context_paths=["lib/coordinated.js"],
    )
    messages = _build_worker_prompt(task, empty_repo_graph)
    user = next(m["content"] for m in messages if m.get("role") == "user")
    assert "You may propose writes here when changes to" in user
    assert "runtime auto-approval guard" in user
    assert "read-only unless a replan expands scope" not in user


def test_build_worker_prompt_blind_omits_predicted_writes(
    empty_repo_graph: dict[str, object],
) -> None:
    task = Task(
        id="t1",
        prompt="Do something",
        predicted_writes=[PredictedWrite(path="src/foo.ts", confidence=0.9)],
        allowed_paths=["src/**"],
        candidate_context_paths=[],
    )
    messages = _build_worker_prompt(task, empty_repo_graph, include_lockfile_hints=False)
    user = next(m["content"] for m in messages if m.get("role") == "user")
    assert "Predicted writable files" not in user
    assert "foo.ts" not in user


def test_build_worker_prompt_blind_omits_candidate_context(
    empty_repo_graph: dict[str, object],
) -> None:
    task = Task(
        id="t1",
        prompt="Do something",
        predicted_writes=[PredictedWrite(path="src/a.ts", confidence=0.9)],
        allowed_paths=["src/**"],
        candidate_context_paths=["src/bar.ts", "lib/other.ts"],
    )
    messages = _build_worker_prompt(task, empty_repo_graph, include_lockfile_hints=False)
    user = next(m["content"] for m in messages if m.get("role") == "user")
    assert "Candidate context" not in user
    assert "bar.ts" not in user


def test_build_worker_prompt_full_still_includes_hints(
    empty_repo_graph: dict[str, object],
) -> None:
    task = Task(
        id="t1",
        prompt="Do something",
        predicted_writes=[PredictedWrite(path="src/foo.ts", confidence=0.9)],
        allowed_paths=["src/**"],
        candidate_context_paths=["src/bar.ts"],
    )
    messages = _build_worker_prompt(task, empty_repo_graph)
    user = next(m["content"] for m in messages if m.get("role") == "user")
    assert "Predicted writable files" in user
    assert "foo.ts" in user
    assert "Candidate context" in user
    assert "bar.ts" in user


def _worker_replies(paths: list[str]) -> dict[str, str]:
    return {
        "oauth": json.dumps(
            {
                "writes": [
                    {"file": path, "description": f"write {idx}"} for idx, path in enumerate(paths)
                ]
            }
        )
    }


def _candidate_replan_events(output: str | list[str]) -> list[dict[str, object]]:
    prefix = "[candidate_replan] "
    events: list[dict[str, object]] = []
    lines = output if isinstance(output, list) else output.splitlines()
    for line in lines:
        if prefix in line:
            events.append(json.loads(line.split(prefix, 1)[1]))
    return events


# ---------------------------------------------------------------------------
# Tests — minimum 4 per HANDOFF_NEXT.
# ---------------------------------------------------------------------------


def test_runtime_executes_groups_in_order(
    lock: AgentLock, empty_repo_graph: dict[str, object]
) -> None:
    """Group N+1 must not start before every worker in group N has finished."""
    sub_replies = {
        "oauth": json.dumps({"writes": []}),
        "settings": json.dumps({"writes": []}),
        "billing": json.dumps({"writes": []}),
        "tests": json.dumps({"writes": []}),
    }
    sub = StubRuntimeLLM(replies=sub_replies)
    # Force group-1 workers to take measurable wall-clock time so a group-2
    # worker that started early would be detectable.
    sub.set_delay("oauth", 0.05)
    sub.set_delay("settings", 0.05)

    orch = StubRuntimeLLM()

    result: RunResult = asyncio.run(
        run_lockfile(lock, empty_repo_graph, orch, sub, lockfile_path="x.json")
    )

    starts: dict[str, float] = {}
    for task_id, ts in sub.calls:
        starts.setdefault(task_id, ts)

    g1_max = max(starts["oauth"], starts["settings"])
    assert starts["billing"] >= g1_max, "group 2 began before group 1 finished"
    assert starts["tests"] >= starts["billing"], "group 3 began before group 2 finished"
    # Sanity: every task ran exactly once.
    task_calls = [tid for tid, _ in sub.calls if tid != "_orchestrator_"]
    assert sorted(task_calls) == sorted(["oauth", "settings", "billing", "tests"])
    # Run trace recorded all four groups in order.
    assert [g.id for g in result.groups_executed] == [1, 2, 3]


def test_runtime_blocks_writes_outside_allowed_paths(
    lock: AgentLock, empty_repo_graph: dict[str, object]
) -> None:
    """A path the worker invents outside its allowed_paths must land BLOCKED."""
    sub = StubRuntimeLLM(
        replies={
            "oauth": json.dumps(
                {
                    "writes": [
                        {
                            "file": "src/utils/random.ts",
                            "description": "out-of-bounds helper",
                        }
                    ]
                }
            ),
            "settings": json.dumps({"writes": []}),
            "billing": json.dumps({"writes": []}),
            "tests": json.dumps({"writes": []}),
        }
    )
    orch = StubRuntimeLLM()
    result = asyncio.run(run_lockfile(lock, empty_repo_graph, orch, sub, lockfile_path="x.json"))
    oauth_worker = next(w for w in result.workers if w.task_id == "oauth")
    assert oauth_worker.blocked_count == 1
    assert oauth_worker.allowed_count == 0
    blocked = [p for p in oauth_worker.proposals if not p.allowed]
    assert len(blocked) == 1
    assert blocked[0].file == "src/utils/random.ts"
    assert blocked[0].reason and "src/utils/random.ts" in blocked[0].reason
    assert blocked[0].scope_status == "blocked"


def test_runtime_marks_candidate_context_write_as_needs_replan(
    lock: AgentLock, empty_repo_graph: dict[str, object]
) -> None:
    """Candidate context is visible to workers but still not write authority."""
    scoped_lock = lock.model_copy(deep=True)
    oauth_task = next(t for t in scoped_lock.tasks if t.id == "oauth")
    oauth_task.candidate_context_paths.append("src/server/oauth-provider.ts")
    sub = StubRuntimeLLM(
        replies={
            "oauth": json.dumps(
                {
                    "writes": [
                        {
                            "file": "src/server/oauth-provider.ts",
                            "description": "candidate-only helper",
                        }
                    ]
                }
            ),
            "settings": json.dumps({"writes": []}),
            "billing": json.dumps({"writes": []}),
            "tests": json.dumps({"writes": []}),
        }
    )
    orch = StubRuntimeLLM()

    result = asyncio.run(
        run_lockfile(scoped_lock, empty_repo_graph, orch, sub, lockfile_path="x.json")
    )

    oauth_worker = next(w for w in result.workers if w.task_id == "oauth")
    proposal = oauth_worker.proposals[0]
    assert oauth_worker.needs_replan_count == 1
    assert oauth_worker.blocked_count == 1
    assert proposal.allowed is False
    assert proposal.scope_status == "needs_replan"
    assert proposal.reason and "candidate_context only" in proposal.reason


def test_runtime_auto_replan_approves_supported_candidate_context(
    lock: AgentLock,
    empty_repo_graph: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scoped_lock = lock.model_copy(deep=True)
    oauth_task = next(t for t in scoped_lock.tasks if t.id == "oauth")
    oauth_task.candidate_context_paths.append("src/server/oauth-provider.ts")
    oauth_task.file_scopes.append(
        FileScope(
            path="src/server/oauth-provider.ts",
            tier="candidate_context",
            score=0.86,
            signals=["scope_review"],
            reason="Scope review kept this as a supported candidate.",
        )
    )
    sub = StubRuntimeLLM(
        replies={
            "oauth": json.dumps(
                {
                    "writes": [
                        {
                            "file": "src/server/oauth-provider.ts",
                            "description": "candidate helper",
                        }
                    ]
                }
            ),
            "settings": json.dumps({"writes": []}),
            "billing": json.dumps({"writes": []}),
            "tests": json.dumps({"writes": []}),
        }
    )
    recorder = RecordingConsole()
    monkeypatch.setattr("acg.runtime._console", recorder)

    result = asyncio.run(
        run_lockfile(
            scoped_lock,
            empty_repo_graph,
            StubRuntimeLLM(),
            sub,
            lockfile_path="x.json",
            config=RuntimeConfig(auto_replan=True),
        )
    )

    events = _candidate_replan_events(recorder.messages)
    assert len(events) == 1
    event = events[0]
    assert event["task_id"] == "oauth"
    assert event["path"] == "src/server/oauth-provider.ts"
    assert event["signals"] == ["scope_review"]
    assert event["score"] == 0.86
    assert event["can_auto_approve_replan"] is True
    assert event["has_hard_conflict"] is False
    assert event["final_outcome"] == "approved_replan"

    oauth_worker = next(w for w in result.workers if w.task_id == "oauth")
    proposal = oauth_worker.proposals[0]
    assert proposal.allowed is True
    assert proposal.scope_status == "approved_replan"
    assert oauth_worker.replan_approved_count == 1
    assert "src/server/oauth-provider.ts" in oauth_task.allowed_paths


def test_runtime_auto_replan_approves_must_write_neighbor_candidate(
    lock: AgentLock,
    empty_repo_graph: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A candidate scope whose ONLY signal is must_write_neighbor should
    auto-approve when the worker proposes a write.

    Guards Fix B (post-LLM typings expansion) ↔ Fix D (auto-replan) composition.
    """
    scoped_lock = lock.model_copy(deep=True)
    oauth_task = next(t for t in scoped_lock.tasks if t.id == "oauth")
    oauth_task.candidate_context_paths.append("types/oauth-provider.d.ts")
    oauth_task.file_scopes.append(
        FileScope(
            path="types/oauth-provider.d.ts",
            tier="candidate_context",
            score=0.78,
            signals=["must_write_neighbor"],
            reason="Post-LLM typings expansion of a must_write file.",
        )
    )
    sub = StubRuntimeLLM(
        replies={
            "oauth": json.dumps(
                {"writes": [{"file": "types/oauth-provider.d.ts", "description": "typings"}]}
            ),
            "settings": json.dumps({"writes": []}),
            "billing": json.dumps({"writes": []}),
            "tests": json.dumps({"writes": []}),
        }
    )
    recorder = RecordingConsole()
    monkeypatch.setattr("acg.runtime._console", recorder)

    result = asyncio.run(
        run_lockfile(
            scoped_lock,
            empty_repo_graph,
            StubRuntimeLLM(),
            sub,
            lockfile_path="x.json",
            config=RuntimeConfig(auto_replan=True),
        )
    )

    events = _candidate_replan_events(recorder.messages)
    assert len(events) == 1
    event = events[0]
    assert event["path"] == "types/oauth-provider.d.ts"
    assert event["signals"] == ["must_write_neighbor"]
    assert event["can_auto_approve_replan"] is True
    assert event["final_outcome"] == "approved_replan"

    oauth_worker = next(w for w in result.workers if w.task_id == "oauth")
    proposal = oauth_worker.proposals[0]
    assert proposal.allowed is True
    assert proposal.scope_status == "approved_replan"


def test_runtime_auto_replan_approves_planner_seed_expansion_candidate(
    lock: AgentLock,
    empty_repo_graph: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Planner-only rows from _llm_seed_expansion (score 0.72) must auto-approve."""
    path = "src/server/seed-expansion-target.ts"
    scoped_lock = lock.model_copy(deep=True)
    oauth_task = next(t for t in scoped_lock.tasks if t.id == "oauth")
    oauth_task.candidate_context_paths.append(path)
    oauth_task.file_scopes.append(
        FileScope(
            path=path,
            tier="candidate_context",
            score=0.72,
            signals=["planner"],
            reason=(
                f"{LLM_SEED_EXPANSION_REASON} Candidate context only; requires replan before write. "
                "Signals: planner."
            ),
        )
    )
    sub = StubRuntimeLLM(
        replies={
            "oauth": json.dumps({"writes": [{"file": path, "description": "coordination edit"}]}),
            "settings": json.dumps({"writes": []}),
            "billing": json.dumps({"writes": []}),
            "tests": json.dumps({"writes": []}),
        }
    )
    recorder = RecordingConsole()
    monkeypatch.setattr("acg.runtime._console", recorder)

    result = asyncio.run(
        run_lockfile(
            scoped_lock,
            empty_repo_graph,
            StubRuntimeLLM(),
            sub,
            lockfile_path="x.json",
            config=RuntimeConfig(auto_replan=True),
        )
    )

    events = _candidate_replan_events(recorder.messages)
    assert len(events) == 1
    event = events[0]
    assert event["task_id"] == "oauth"
    assert event["path"] == path
    assert event["signals"] == ["planner"]
    assert event["score"] == 0.72
    assert event["can_auto_approve_replan"] is True
    assert event["has_hard_conflict"] is False
    assert event["final_outcome"] == "approved_replan"

    oauth_worker = next(w for w in result.workers if w.task_id == "oauth")
    proposal = oauth_worker.proposals[0]
    assert proposal.allowed is True
    assert proposal.scope_status == "approved_replan"


def test_runtime_logs_needs_replan_for_unsupported_candidate_context(
    lock: AgentLock,
    empty_repo_graph: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scoped_lock = lock.model_copy(deep=True)
    oauth_task = next(t for t in scoped_lock.tasks if t.id == "oauth")
    oauth_task.candidate_context_paths.append("src/server/oauth-provider.ts")
    oauth_task.file_scopes.append(
        FileScope(
            path="src/server/oauth-provider.ts",
            tier="candidate_context",
            score=0.86,
            signals=["manual_review"],
            reason="Scope review did not surface an auto-replan signal.",
        )
    )
    sub = StubRuntimeLLM(
        replies={
            "oauth": json.dumps(
                {
                    "writes": [
                        {
                            "file": "src/server/oauth-provider.ts",
                            "description": "candidate helper",
                        }
                    ]
                }
            ),
            "settings": json.dumps({"writes": []}),
            "billing": json.dumps({"writes": []}),
            "tests": json.dumps({"writes": []}),
        }
    )
    recorder = RecordingConsole()
    monkeypatch.setattr("acg.runtime._console", recorder)

    result = asyncio.run(
        run_lockfile(
            scoped_lock,
            empty_repo_graph,
            StubRuntimeLLM(),
            sub,
            lockfile_path="x.json",
            config=RuntimeConfig(auto_replan=True),
        )
    )

    events = _candidate_replan_events(recorder.messages)
    assert len(events) == 1
    event = events[0]
    assert event["task_id"] == "oauth"
    assert event["path"] == "src/server/oauth-provider.ts"
    assert event["signals"] == ["manual_review"]
    assert event["score"] == 0.86
    assert event["can_auto_approve_replan"] is False
    assert event["has_hard_conflict"] is False
    assert event["final_outcome"] == "needs_replan"

    oauth_worker = next(w for w in result.workers if w.task_id == "oauth")
    proposal = oauth_worker.proposals[0]
    assert proposal.allowed is False
    assert proposal.scope_status == "needs_replan"
    assert oauth_worker.needs_replan_count == 1


def test_runtime_auto_replan_blocked_for_hard_conflict(
    lock: AgentLock,
    empty_repo_graph: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A candidate path that another task is allowed to write must not auto-approve."""
    scoped_lock = lock.model_copy(deep=True)
    oauth_task = next(t for t in scoped_lock.tasks if t.id == "oauth")
    # src/server/stripe.ts is in billing's allowed_paths but not oauth's.
    path = "src/server/stripe.ts"
    oauth_task.candidate_context_paths.append(path)
    oauth_task.file_scopes.append(
        FileScope(
            path=path,
            tier="candidate_context",
            score=0.86,
            signals=["explicit"],
            reason="Shared endpoint candidate.",
        )
    )
    sub = StubRuntimeLLM(
        replies={
            "oauth": json.dumps({"writes": [{"file": path, "description": "coordination edit"}]}),
            "settings": json.dumps({"writes": []}),
            "billing": json.dumps({"writes": []}),
            "tests": json.dumps({"writes": []}),
        }
    )
    recorder = RecordingConsole()
    monkeypatch.setattr("acg.runtime._console", recorder)

    result = asyncio.run(
        run_lockfile(
            scoped_lock,
            empty_repo_graph,
            StubRuntimeLLM(),
            sub,
            lockfile_path="x.json",
            config=RuntimeConfig(auto_replan=True),
        )
    )

    events = _candidate_replan_events(recorder.messages)
    assert len(events) == 1
    event = events[0]
    assert event["task_id"] == "oauth"
    assert event["path"] == path
    assert event["can_auto_approve_replan"] is False
    assert event["has_hard_conflict"] is True
    assert event["final_outcome"] == "needs_replan"

    oauth_worker = next(w for w in result.workers if w.task_id == "oauth")
    proposal = oauth_worker.proposals[0]
    assert proposal.allowed is False
    assert proposal.scope_status == "needs_replan"


def test_runtime_auto_replan_blocked_for_below_threshold_score(
    lock: AgentLock,
    empty_repo_graph: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Candidate context with score < 0.72 must not auto-approve."""
    scoped_lock = lock.model_copy(deep=True)
    oauth_task = next(t for t in scoped_lock.tasks if t.id == "oauth")
    path = "src/server/low-confidence.ts"
    oauth_task.candidate_context_paths.append(path)
    oauth_task.file_scopes.append(
        FileScope(
            path=path,
            tier="candidate_context",
            score=0.71,
            signals=["explicit"],
            reason="Weak signal candidate.",
        )
    )
    sub = StubRuntimeLLM(
        replies={
            "oauth": json.dumps({"writes": [{"file": path, "description": "coordination edit"}]}),
            "settings": json.dumps({"writes": []}),
            "billing": json.dumps({"writes": []}),
            "tests": json.dumps({"writes": []}),
        }
    )
    recorder = RecordingConsole()
    monkeypatch.setattr("acg.runtime._console", recorder)

    result = asyncio.run(
        run_lockfile(
            scoped_lock,
            empty_repo_graph,
            StubRuntimeLLM(),
            sub,
            lockfile_path="x.json",
            config=RuntimeConfig(auto_replan=True),
        )
    )

    events = _candidate_replan_events(recorder.messages)
    assert len(events) == 1
    event = events[0]
    assert event["score"] == 0.71
    assert event["can_auto_approve_replan"] is False
    assert event["final_outcome"] == "needs_replan"

    oauth_worker = next(w for w in result.workers if w.task_id == "oauth")
    proposal = oauth_worker.proposals[0]
    assert proposal.allowed is False
    assert proposal.scope_status == "needs_replan"


def test_runtime_candidate_stays_blocked_when_auto_replan_false(
    lock: AgentLock,
    empty_repo_graph: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With auto_replan=False, a valid candidate context write stays blocked."""
    scoped_lock = lock.model_copy(deep=True)
    oauth_task = next(t for t in scoped_lock.tasks if t.id == "oauth")
    path = "src/server/oauth-provider.ts"
    oauth_task.candidate_context_paths.append(path)
    oauth_task.file_scopes.append(
        FileScope(
            path=path,
            tier="candidate_context",
            score=0.86,
            signals=["explicit"],
            reason="High-confidence candidate.",
        )
    )
    sub = StubRuntimeLLM(
        replies={
            "oauth": json.dumps({"writes": [{"file": path, "description": "coordination edit"}]}),
            "settings": json.dumps({"writes": []}),
            "billing": json.dumps({"writes": []}),
            "tests": json.dumps({"writes": []}),
        }
    )
    recorder = RecordingConsole()
    monkeypatch.setattr("acg.runtime._console", recorder)

    result = asyncio.run(
        run_lockfile(
            scoped_lock,
            empty_repo_graph,
            StubRuntimeLLM(),
            sub,
            lockfile_path="x.json",
            config=RuntimeConfig(auto_replan=False),
        )
    )

    events = _candidate_replan_events(recorder.messages)
    assert len(events) == 1
    event = events[0]
    assert event["can_auto_approve_replan"] is True  # conditions met, but gate is off
    assert event["final_outcome"] == "needs_replan"

    oauth_worker = next(w for w in result.workers if w.task_id == "oauth")
    proposal = oauth_worker.proposals[0]
    assert proposal.allowed is False
    assert proposal.scope_status == "needs_replan"
    assert oauth_worker.replan_approved_count == 0


def test_runtime_allows_writes_within_allowed_paths(
    lock: AgentLock, empty_repo_graph: dict[str, object]
) -> None:
    """Happy path: in-bounds writes flagged ALLOWED with no reason."""
    sub = StubRuntimeLLM(
        replies={
            "oauth": json.dumps(
                {
                    "writes": [
                        {
                            "file": "src/server/auth/config.ts",
                            "description": "Add Google provider",
                        },
                        {
                            "file": "prisma/schema.prisma",
                            "description": "Add Account/Session models",
                        },
                    ]
                }
            ),
            "settings": json.dumps({"writes": []}),
            "billing": json.dumps({"writes": []}),
            "tests": json.dumps({"writes": []}),
        }
    )
    orch = StubRuntimeLLM()
    result = asyncio.run(run_lockfile(lock, empty_repo_graph, orch, sub, lockfile_path="x.json"))
    oauth_worker = next(w for w in result.workers if w.task_id == "oauth")
    assert oauth_worker.allowed_count == 2
    assert oauth_worker.blocked_count == 0
    for proposal in oauth_worker.proposals:
        assert proposal.allowed
        assert proposal.reason is None


def test_grace_overlap_uses_thread_pool(
    lock: AgentLock, empty_repo_graph: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    main_thread_id = threading.get_ident()
    recorded_thread_ids: list[int] = []

    def spy_validate_write(
        lockfile: AgentLock, task_id: str, write_path: str
    ) -> tuple[bool, str | None]:
        del lockfile, task_id, write_path
        recorded_thread_ids.append(threading.get_ident())
        return True, None

    monkeypatch.setattr("acg.enforce.validate_write", spy_validate_write)
    sub = StubRuntimeLLM(
        replies=_worker_replies(["src/server/auth/config.ts", "src/server/auth/index.ts"])
    )
    oauth_task = next(t for t in lock.tasks if t.id == "oauth")

    worker = asyncio.run(
        run_worker(
            oauth_task,
            lock,
            empty_repo_graph,
            sub,
            group_id=1,
            config=RuntimeConfig(grace_overlap=True),
        )
    )

    assert len(recorded_thread_ids) == 2
    assert all(thread_id != main_thread_id for thread_id in recorded_thread_ids)
    assert [p.file for p in worker.proposals] == [
        "src/server/auth/config.ts",
        "src/server/auth/index.ts",
    ]


def test_grace_overlap_off_runs_synchronously(
    lock: AgentLock, empty_repo_graph: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    main_thread_id = threading.get_ident()
    recorded_thread_ids: list[int] = []

    def spy_validate_write(
        lockfile: AgentLock, task_id: str, write_path: str
    ) -> tuple[bool, str | None]:
        del lockfile, task_id, write_path
        recorded_thread_ids.append(threading.get_ident())
        return True, None

    monkeypatch.setattr("acg.enforce.validate_write", spy_validate_write)
    sub = StubRuntimeLLM(
        replies=_worker_replies(["src/server/auth/config.ts", "src/server/auth/index.ts"])
    )
    oauth_task = next(t for t in lock.tasks if t.id == "oauth")

    worker = asyncio.run(
        run_worker(
            oauth_task,
            lock,
            empty_repo_graph,
            sub,
            group_id=1,
            config=RuntimeConfig(grace_overlap=False),
        )
    )

    assert len(recorded_thread_ids) == 2
    assert recorded_thread_ids == [main_thread_id, main_thread_id]
    assert [p.file for p in worker.proposals] == [
        "src/server/auth/config.ts",
        "src/server/auth/index.ts",
    ]


def test_grace_overlap_preserves_proposal_order(
    lock: AgentLock, empty_repo_graph: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = [
        "src/server/auth/config.ts",
        "prisma/schema.prisma",
        "src/app/api/auth/[...nextauth]/route.ts",
        "src/server/auth/index.ts",
        "src/server/auth/session.ts",
    ]
    delays = dict(zip(paths, [0.05, 0.04, 0.03, 0.02, 0.01], strict=True))

    def spy_validate_write(
        lockfile: AgentLock, task_id: str, write_path: str
    ) -> tuple[bool, str | None]:
        del lockfile, task_id
        time.sleep(delays[write_path])
        return True, None

    monkeypatch.setattr("acg.enforce.validate_write", spy_validate_write)
    oauth_task = next(t for t in lock.tasks if t.id == "oauth")

    for grace_overlap in (False, True):
        sub = StubRuntimeLLM(replies=_worker_replies(paths))
        worker = asyncio.run(
            run_worker(
                oauth_task,
                lock,
                empty_repo_graph,
                sub,
                group_id=1,
                config=RuntimeConfig(grace_overlap=grace_overlap),
            )
        )
        assert [proposal.file for proposal in worker.proposals] == paths


def test_runtime_handles_malformed_worker_reply(
    lock: AgentLock, empty_repo_graph: dict[str, object]
) -> None:
    """A non-JSON reply must produce zero proposals and not crash the run."""
    sub = StubRuntimeLLM(
        replies={
            "oauth": "sure thing! here's what I'd do.",  # not JSON at all
            "settings": json.dumps({"writes": []}),
            "billing": json.dumps({"writes": []}),
            "tests": json.dumps({"writes": []}),
        }
    )
    orch = StubRuntimeLLM()
    result = asyncio.run(run_lockfile(lock, empty_repo_graph, orch, sub, lockfile_path="x.json"))
    oauth_worker = next(w for w in result.workers if w.task_id == "oauth")
    assert oauth_worker.proposals == []
    assert oauth_worker.allowed_count == 0
    assert oauth_worker.blocked_count == 0
    # Run still completed end-to-end.
    assert len(result.workers) == 4


# ---------------------------------------------------------------------------
# Bonus tests — cheap, high value.
# ---------------------------------------------------------------------------


def test_runtime_handles_json_fenced_reply(
    lock: AgentLock, empty_repo_graph: dict[str, object]
) -> None:
    """The forgiving parser must strip ```json fences before json.loads."""
    fenced = (
        "```json\n"
        + json.dumps(
            {
                "writes": [
                    {
                        "file": "src/server/auth/config.ts",
                        "description": "fenced reply",
                    }
                ]
            }
        )
        + "\n```"
    )
    sub = StubRuntimeLLM(
        replies={
            "oauth": fenced,
            "settings": json.dumps({"writes": []}),
            "billing": json.dumps({"writes": []}),
            "tests": json.dumps({"writes": []}),
        }
    )
    orch = StubRuntimeLLM()
    result = asyncio.run(run_lockfile(lock, empty_repo_graph, orch, sub, lockfile_path="x.json"))
    oauth_worker = next(w for w in result.workers if w.task_id == "oauth")
    assert oauth_worker.allowed_count == 1
    assert oauth_worker.proposals[0].file == "src/server/auth/config.ts"


def test_runtime_records_orchestrator_reasoning(
    lock: AgentLock, empty_repo_graph: dict[str, object]
) -> None:
    """Orchestrator reasoning_content must appear verbatim in the run trace."""
    reasoning = "Looking at the lockfile, conflicts on prisma/schema.prisma…"
    orch = StubRuntimeLLM(reasoning=reasoning)
    sub = StubRuntimeLLM(
        replies={
            tid: json.dumps({"writes": []}) for tid in ["oauth", "settings", "billing", "tests"]
        }
    )
    result = asyncio.run(run_lockfile(lock, empty_repo_graph, orch, sub, lockfile_path="x.json"))
    assert result.orchestrator.reasoning_content == reasoning
    assert result.orchestrator.parsed is not None
    assert result.orchestrator.parsed.get("approved") is True


def test_runtime_emits_required_trace_fields(
    lock: AgentLock, empty_repo_graph: dict[str, object]
) -> None:
    """asdict(RunResult) must contain every top-level key the schema declares."""
    orch = StubRuntimeLLM()
    sub = StubRuntimeLLM(
        replies={
            tid: json.dumps({"writes": []}) for tid in ["oauth", "settings", "billing", "tests"]
        }
    )
    result = asyncio.run(run_lockfile(lock, empty_repo_graph, orch, sub, lockfile_path="x.json"))
    payload = asdict(result)
    required = {
        "version",
        "generated_at",
        "lockfile",
        "config",
        "orchestrator",
        "workers",
        "groups_executed",
        "started_at",
        "finished_at",
        "total_wall_s",
    }
    missing = required - set(payload.keys())
    assert not missing, f"trace missing fields: {missing}"
    assert payload["version"] == "1.0"
    assert payload["lockfile"] == "x.json"
    assert isinstance(payload["workers"], list) and len(payload["workers"]) == 4
    assert isinstance(payload["groups_executed"], list) and len(payload["groups_executed"]) == 3


def test_run_orchestrator_parses_dispatch(lock: AgentLock) -> None:
    """run_orchestrator must populate parsed{} when content is valid JSON."""
    orch = StubRuntimeLLM(
        router=lambda _user: json.dumps(
            {"approved": False, "concerns": ["risky"], "dispatch_order": [1, 2, 3]}
        ),
        reasoning="(thinking)",
    )
    res: OrchestratorResult = asyncio.run(run_orchestrator(lock, orch))
    assert res.parsed == {
        "approved": False,
        "concerns": ["risky"],
        "dispatch_order": [1, 2, 3],
    }
    assert res.reasoning_content == "(thinking)"


def _build_four_task_lock() -> tuple[AgentLock, Group]:
    """Build a synthetic 4-task / 1-group lockfile for concurrency-lane tests.

    Each task whitelists ``src/**`` so the per-worker stub replies fall in
    bounds and don't introduce extra branching unrelated to scheduling.
    """
    tasks = [
        Task(
            id=f"t{i}",
            prompt=f"task {i}",
            predicted_writes=[],
            allowed_paths=["src/**"],
            depends_on=[],
            parallel_group=1,
        )
        for i in range(1, 5)
    ]
    group = Group(id=1, tasks=[t.id for t in tasks], type="parallel", waits_for=[])
    lock = AgentLock(
        generated_at=AgentLock.utcnow(),
        repo={"root": "demo", "languages": ["typescript"]},
        tasks=tasks,
        execution_plan=ExecutionPlan(groups=[group]),
    )
    return lock, group


def test_sequential_mode_serializes_workers(
    empty_repo_graph: dict[str, object],
) -> None:
    """sequential=True must run 4 × 100ms workers in serial (>= 0.4s wall)."""
    lock, group = _build_four_task_lock()
    sub = StubRuntimeLLM(replies={f"t{i}": json.dumps({"writes": []}) for i in range(1, 5)})
    for i in range(1, 5):
        sub.set_delay(f"t{i}", 0.1)

    cfg = RuntimeConfig(sequential=True)

    t0 = time.perf_counter()
    asyncio.run(run_group(group, lock, empty_repo_graph, sub, config=cfg))
    elapsed = time.perf_counter() - t0

    assert elapsed >= 0.4, f"sequential lane finished too fast: {elapsed:.3f}s"


def test_concurrent_mode_parallelizes_workers(
    empty_repo_graph: dict[str, object],
) -> None:
    """worker_concurrency=4 lets all 4 × 100ms workers run together (< 0.2s)."""
    lock, group = _build_four_task_lock()
    sub = StubRuntimeLLM(replies={f"t{i}": json.dumps({"writes": []}) for i in range(1, 5)})
    for i in range(1, 5):
        sub.set_delay(f"t{i}", 0.1)

    cfg = RuntimeConfig(worker_concurrency=4)

    t0 = time.perf_counter()
    asyncio.run(run_group(group, lock, empty_repo_graph, sub, config=cfg))
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.2, f"concurrent lane did not parallelize: {elapsed:.3f}s"


def test_mutex_flags_rejected(tmp_path: Path, example_dag_lockfile_path: Path) -> None:
    """`acg run --sequential --worker-concurrency 4` must exit non-zero."""
    from typer.testing import CliRunner

    from acg.cli import app

    runner = CliRunner()
    out_path = tmp_path / "run.json"
    result = runner.invoke(
        app,
        [
            "run",
            "--mock",
            "--sequential",
            "--worker-concurrency",
            "4",
            "--lock",
            str(example_dag_lockfile_path),
            "--repo",
            str(example_dag_lockfile_path.parent),
            "--out",
            str(out_path),
        ],
    )
    assert result.exit_code != 0, result.stdout
    assert not out_path.exists()


def test_env_worker_concurrency_preserved_when_flag_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ACG_WORKER_CONCURRENCY must survive when --worker-concurrency is omitted.

    Regression guard against the override bug where the CLI default of 0
    silently clobbers any value picked up from the environment.
    """
    monkeypatch.setenv("ACG_WORKER_CONCURRENCY", "2")
    monkeypatch.setenv("ACG_SEQUENTIAL", "1")
    cfg = RuntimeConfig.from_env()
    assert cfg.worker_concurrency == 2
    assert cfg.sequential is True

    # Simulate the CLI's "no flags supplied" branch: env values must survive.
    cli_sequential: bool | None = None
    cli_worker_concurrency: int | None = None
    if cli_sequential is not None:
        cfg.sequential = cli_sequential
    if cli_worker_concurrency is not None:
        cfg.worker_concurrency = cli_worker_concurrency
    assert cfg.worker_concurrency == 2
    assert cfg.sequential is True

    # Simulate the CLI's "user passed --worker-concurrency 0" branch: explicit
    # 0 must override the env var (i.e. user opted into unbounded).
    cli_worker_concurrency = 0
    if cli_worker_concurrency is not None:
        cfg.worker_concurrency = cli_worker_concurrency
    assert cfg.worker_concurrency == 0


def test_runtime_llm_includes_seed_only_when_env_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from acg.runtime import RuntimeLLM

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}
        text = "{}"

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {"content": "ok", "reasoning_content": ""},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout
            self.calls: list[dict[str, object]] = []

        async def post(
            self,
            endpoint: str,
            json: dict[str, object],
            headers: dict[str, str],
        ) -> FakeResponse:
            self.calls.append({"endpoint": endpoint, "json": json, "headers": headers})
            return FakeResponse()

        async def aclose(self) -> None:
            return None

    fake_client = FakeAsyncClient(timeout=1.0)
    monkeypatch.setattr("acg.runtime.httpx.AsyncClient", lambda timeout: fake_client)
    monkeypatch.delenv("ACG_LLM_SEED", raising=False)
    llm = RuntimeLLM(base_url="http://example.invalid/v1", model="demo", api_key="key")
    asyncio.run(llm.complete([{"role": "user", "content": "hello"}]))
    assert "seed" not in fake_client.calls[-1]["json"]

    fake_client = FakeAsyncClient(timeout=1.0)
    monkeypatch.setattr("acg.runtime.httpx.AsyncClient", lambda timeout: fake_client)
    monkeypatch.setenv("ACG_LLM_SEED", "17")
    llm = RuntimeLLM(base_url="http://example.invalid/v1", model="demo", api_key="key")
    asyncio.run(llm.complete([{"role": "user", "content": "hello"}]))
    assert fake_client.calls[-1]["json"]["seed"] == 17


def test_runtime_llm_omits_max_tokens_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from acg.runtime import RuntimeLLM

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}
        text = "{}"

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {"content": "ok", "reasoning_content": ""},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.calls: list[dict[str, object]] = []

        async def post(
            self,
            endpoint: str,
            json: dict[str, object],
            headers: dict[str, str],
        ) -> FakeResponse:
            self.calls.append({"json": json})
            return FakeResponse()

        async def aclose(self) -> None:
            return None

    fake_client = FakeAsyncClient(timeout=1.0)
    monkeypatch.setattr("acg.runtime.httpx.AsyncClient", lambda timeout: fake_client)
    monkeypatch.delenv("ACG_LLM_SEED", raising=False)
    llm = RuntimeLLM(base_url="http://example.invalid/v1", model="demo", api_key="key")
    asyncio.run(llm.complete([{"role": "user", "content": "hello"}], max_tokens=None))
    assert "max_tokens" not in fake_client.calls[-1]["json"]


def test_runtime_llm_includes_max_tokens_when_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from acg.runtime import RuntimeLLM

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}
        text = "{}"

        def json(self) -> dict[str, object]:
            return {
                "choices": [
                    {
                        "message": {"content": "ok", "reasoning_content": ""},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.calls: list[dict[str, object]] = []

        async def post(
            self,
            endpoint: str,
            json: dict[str, object],
            headers: dict[str, str],
        ) -> FakeResponse:
            self.calls.append({"json": json})
            return FakeResponse()

        async def aclose(self) -> None:
            return None

    fake_client = FakeAsyncClient(timeout=1.0)
    monkeypatch.setattr("acg.runtime.httpx.AsyncClient", lambda timeout: fake_client)
    monkeypatch.delenv("ACG_LLM_SEED", raising=False)
    llm = RuntimeLLM(base_url="http://example.invalid/v1", model="demo", api_key="key")
    asyncio.run(llm.complete([{"role": "user", "content": "hello"}], max_tokens=2048))
    assert fake_client.calls[-1]["json"]["max_tokens"] == 2048


def test_runtime_llm_retries_429_with_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    from acg.runtime import RuntimeLLM

    class FakeResponse:
        def __init__(
            self,
            status_code: int,
            headers: dict[str, str] | None = None,
            text: str = "",
            payload: dict[str, object] | None = None,
        ) -> None:
            self.status_code = status_code
            self.headers = headers or {}
            self.text = text or "{}"
            self._payload = payload

        def json(self) -> dict[str, object]:
            if self._payload is not None:
                return self._payload
            return {
                "choices": [
                    {
                        "message": {"content": "ok", "reasoning_content": ""},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    ok_payload: dict[str, object] = {
        "choices": [
            {
                "message": {"content": "ok-after-retry", "reasoning_content": ""},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }
    calls = {"n": 0}

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            del timeout

        async def post(
            self,
            endpoint: str,
            json: dict[str, object],
            headers: dict[str, str],
        ) -> FakeResponse:
            calls["n"] += 1
            if calls["n"] == 1:
                return FakeResponse(429, headers={"retry-after": "0"})
            return FakeResponse(200, payload=ok_payload)

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("acg.runtime.httpx.AsyncClient", lambda timeout: FakeAsyncClient(timeout))
    monkeypatch.delenv("ACG_LLM_SEED", raising=False)
    llm = RuntimeLLM(base_url="http://example.invalid/v1", model="demo", api_key="key")
    reply = asyncio.run(llm.complete([{"role": "user", "content": "hello"}]))
    assert reply.content == "ok-after-retry"
    assert calls["n"] == 2


def test_run_worker_treats_finish_length_as_error(
    lock: AgentLock,
    empty_repo_graph: dict[str, object],
) -> None:
    task = next(t for t in lock.tasks if t.id == "oauth")
    sub = StubRuntimeLLM(
        replies={"oauth": json.dumps({"writes": []})},
        finish_reason="length",
    )
    worker: WorkerResult = asyncio.run(run_worker(task, lock, empty_repo_graph, sub, group_id=1))
    assert worker.error and worker.error.startswith("finish_reason=length")
    assert worker.proposals == []


def test_run_worker_emits_heartbeat_on_long_call(
    lock: AgentLock,
    empty_repo_graph: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import acg.runtime as runtime_mod

    monkeypatch.setattr(runtime_mod, "WORKER_HEARTBEAT_S", 0.02)
    rec = RecordingConsole()
    monkeypatch.setattr(runtime_mod, "_console_err", rec)

    task = next(t for t in lock.tasks if t.id == "oauth")
    sub = StubRuntimeLLM(replies={"oauth": json.dumps({"writes": []})})
    sub.set_delay("oauth", 0.05)
    asyncio.run(run_worker(task, lock, empty_repo_graph, sub, group_id=1))
    assert any("still waiting" in m for m in rec.messages)


def test_banner_includes_env_values(
    lock: AgentLock,
    empty_repo_graph: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Setting ACG_LLM_* env vars must surface their values in the stderr banner."""
    monkeypatch.setenv("ACG_LLM_ENGINE", "llama.cpp")
    monkeypatch.setenv("ACG_LLM_DTYPE", "Q4_K_M")
    monkeypatch.setenv("ACG_LLM_PARALLEL", "4")
    monkeypatch.setenv("ACG_LLM_KV_QUANT", "q8_0")
    monkeypatch.setenv("ACG_LLM_FLASH_ATTN", "1")
    monkeypatch.setenv("ACG_WORKER_CONCURRENCY", "2")
    monkeypatch.setenv("ACG_GRACE_OVERLAP", "1")

    sub = StubRuntimeLLM(
        replies={
            tid: json.dumps({"writes": []}) for tid in ["oauth", "settings", "billing", "tests"]
        }
    )
    orch = StubRuntimeLLM()
    asyncio.run(run_lockfile(lock, empty_repo_graph, orch, sub, lockfile_path="x.json"))

    err = capsys.readouterr().err
    assert "engine=llama.cpp" in err
    assert "dtype=Q4_K_M" in err
    assert "parallel=4" in err
    assert "kv-quant=q8_0" in err
    assert "flash-attn=True" in err
    assert "worker-concurrency=2" in err
    assert "grace-overlap=True" in err


def test_banner_falls_back_to_unknown(
    lock: AgentLock,
    empty_repo_graph: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without ACG_LLM_* env vars the banner must show ``unknown`` defaults."""
    for var in [
        "ACG_LLM_ENGINE",
        "ACG_LLM_DTYPE",
        "ACG_LLM_KV_QUANT",
        "ACG_LLM_FLASH_ATTN",
        "ACG_LLM_PARALLEL",
    ]:
        monkeypatch.delenv(var, raising=False)

    sub = StubRuntimeLLM(
        replies={
            tid: json.dumps({"writes": []}) for tid in ["oauth", "settings", "billing", "tests"]
        }
    )
    orch = StubRuntimeLLM()
    asyncio.run(run_lockfile(lock, empty_repo_graph, orch, sub, lockfile_path="x.json"))

    err = capsys.readouterr().err
    assert "engine=unknown" in err
    assert "dtype=unknown" in err
    assert "kv-quant=unknown" in err


def test_run_worker_handles_directory_predicted_write(
    lock: AgentLock, empty_repo_graph: dict[str, object]
) -> None:
    """For a task whose predicted_writes is a bare directory (e.g. 'tests'),
    the worker prompt should include the directory hint and a concrete file
    proposal under that directory should be ALLOWED.
    """
    tests_task = next(t for t in lock.tasks if t.id == "tests")
    sub = StubRuntimeLLM(
        replies={
            "tests": json.dumps(
                {"writes": [{"file": "tests/e2e/checkout.spec.ts", "description": "spec"}]}
            )
        }
    )
    worker: WorkerResult = asyncio.run(
        run_worker(tests_task, lock, empty_repo_graph, sub, group_id=3)
    )
    assert worker.allowed_count == 1
    assert worker.blocked_count == 0
    assert worker.proposals[0].file == "tests/e2e/checkout.spec.ts"


def test_worker_prompt_requires_apply_patch_envelope(
    lock: AgentLock, empty_repo_graph: dict[str, object]
) -> None:
    task = lock.tasks[0]
    messages = _build_worker_prompt(task, empty_repo_graph)
    system = next(m["content"] for m in messages if m["role"] == "system")
    assert "*** Begin Patch" in system
    assert "MUST" in system


def test_parse_apply_envelope_returns_one_proposal_per_file() -> None:
    raw = """*** Begin Patch
*** Update File: a.ts
@@
-old
+new
*** Add File: b.ts
+hello
*** End Patch
"""
    rows = _parse_apply_envelope(raw)
    assert len(rows) == 2
    kinds = {r["description"] for r in rows}
    assert kinds == {"Update", "Add"}
    paths = {r["file"] for r in rows}
    assert paths == {"a.ts", "b.ts"}
    for r in rows:
        assert r["envelope"].startswith("*** Begin Patch")
        assert r["envelope"].endswith("*** End Patch")


def test_worker_proposal_with_content_is_parsed(
    lock: AgentLock, empty_repo_graph: dict[str, object]
) -> None:
    task = next(t for t in lock.tasks if t.id == "oauth")
    body = "export const x = 1;\n"
    raw = json.dumps(
        {
            "writes": [
                {
                    "file": "src/server/auth/hello.ts",
                    "description": "add helper",
                    "content": body,
                }
            ]
        }
    )
    sub = StubRuntimeLLM(replies={"oauth": raw})
    worker = asyncio.run(run_worker(task, lock, empty_repo_graph, sub, group_id=1))
    assert len(worker.proposals) == 1
    prop = worker.proposals[0]
    assert prop.file == "src/server/auth/hello.ts"
    assert prop.allowed is True
    assert prop.content == body
