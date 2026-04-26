"""Runtime tests with a stubbed async LLM client.

Uses :func:`asyncio.run` to drive the async entrypoints so we don't need a
``pytest-asyncio`` plugin. Mirrors the style of :mod:`tests.test_predictor`.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path

import pytest

from acg.runtime import (
    LLMReply,
    OrchestratorResult,
    RunResult,
    RuntimeConfig,
    WorkerResult,
    run_group,
    run_lockfile,
    run_orchestrator,
    run_worker,
)
from acg.schema import AgentLock, ExecutionPlan, Group, Task

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
    ) -> None:
        self._replies = replies or {}
        self._router = router
        self._reasoning = reasoning
        self.url = url
        self.model = model
        # Recorded for ordering assertions.
        self.calls: list[tuple[str, float]] = []
        self.delays: dict[str, float] = {}

    def set_delay(self, task_id: str, seconds: float) -> None:
        self.delays[task_id] = seconds

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = 700,
        temperature: float = 0.2,
    ) -> LLMReply:
        del max_tokens, temperature
        user_blob = "\n".join(
            m.get("content", "") for m in messages if m.get("role") == "user"
        )
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
            content = json.dumps(
                {"approved": True, "concerns": [], "dispatch_order": [1, 2, 3]}
            )

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
            finish_reason="stop",
            wall_s=delay,
        )

    async def aclose(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


@pytest.fixture
def lock(example_dag_lockfile_path: Path) -> AgentLock:
    return AgentLock.model_validate_json(example_dag_lockfile_path.read_text())


@pytest.fixture
def empty_repo_graph() -> dict[str, object]:
    return {"language": "typescript", "files": [], "hotspots": []}


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
    result = asyncio.run(
        run_lockfile(lock, empty_repo_graph, orch, sub, lockfile_path="x.json")
    )
    oauth_worker = next(w for w in result.workers if w.task_id == "oauth")
    assert oauth_worker.blocked_count == 1
    assert oauth_worker.allowed_count == 0
    blocked = [p for p in oauth_worker.proposals if not p.allowed]
    assert len(blocked) == 1
    assert blocked[0].file == "src/utils/random.ts"
    assert blocked[0].reason and "src/utils/random.ts" in blocked[0].reason


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
    result = asyncio.run(
        run_lockfile(lock, empty_repo_graph, orch, sub, lockfile_path="x.json")
    )
    oauth_worker = next(w for w in result.workers if w.task_id == "oauth")
    assert oauth_worker.allowed_count == 2
    assert oauth_worker.blocked_count == 0
    for proposal in oauth_worker.proposals:
        assert proposal.allowed
        assert proposal.reason is None


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
    result = asyncio.run(
        run_lockfile(lock, empty_repo_graph, orch, sub, lockfile_path="x.json")
    )
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
    result = asyncio.run(
        run_lockfile(lock, empty_repo_graph, orch, sub, lockfile_path="x.json")
    )
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
        replies={tid: json.dumps({"writes": []}) for tid in ["oauth", "settings", "billing", "tests"]}
    )
    result = asyncio.run(
        run_lockfile(lock, empty_repo_graph, orch, sub, lockfile_path="x.json")
    )
    assert result.orchestrator.reasoning_content == reasoning
    assert result.orchestrator.parsed is not None
    assert result.orchestrator.parsed.get("approved") is True


def test_runtime_emits_required_trace_fields(
    lock: AgentLock, empty_repo_graph: dict[str, object]
) -> None:
    """asdict(RunResult) must contain every top-level key the schema declares."""
    orch = StubRuntimeLLM()
    sub = StubRuntimeLLM(
        replies={tid: json.dumps({"writes": []}) for tid in ["oauth", "settings", "billing", "tests"]}
    )
    result = asyncio.run(
        run_lockfile(lock, empty_repo_graph, orch, sub, lockfile_path="x.json")
    )
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
    sub = StubRuntimeLLM(
        replies={f"t{i}": json.dumps({"writes": []}) for i in range(1, 5)}
    )
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
    sub = StubRuntimeLLM(
        replies={f"t{i}": json.dumps({"writes": []}) for i in range(1, 5)}
    )
    for i in range(1, 5):
        sub.set_delay(f"t{i}", 0.1)

    cfg = RuntimeConfig(worker_concurrency=4)

    t0 = time.perf_counter()
    asyncio.run(run_group(group, lock, empty_repo_graph, sub, config=cfg))
    elapsed = time.perf_counter() - t0

    assert elapsed < 0.2, f"concurrent lane did not parallelize: {elapsed:.3f}s"


def test_mutex_flags_rejected(
    tmp_path: Path, example_dag_lockfile_path: Path
) -> None:
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
