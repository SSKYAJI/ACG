"""Unit tests for the Greenhouse eval harness.

Covers the four phases the megaplan calls out:

1. Schema shape of ``eval_run.json``.
2. Overlap-pair counting across synthetic Greenhouse tasks.
3. Allowed-path validation for naive vs ACG-planned strategies.
4. Summary metric calculation from an in-memory ``EvalRun``.
5. CLI smoke: ``--backend mock --strategy both`` writes both artifacts.

All tests use the deterministic ``mock`` backend so they pass on CI
without GX10 / Devin access.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from acg.runtime import LLMReply, Proposal, WorkerResult
from acg.schema import (
    AgentLock,
    Conflict,
    ExecutionPlan,
    Generator,
    Group,
    PredictedWrite,
    Repo,
    Task,
)
from experiments.greenhouse import devin_adapter, headtohead, report
from experiments.greenhouse.eval_schema import (
    EVAL_VERSION,
    BlockedWriteEvent,
    EvalRun,
    EvalTask,
    SummaryMetrics,
    TaskMetrics,
    TaskTest,
    annotate_overlaps,
    compute_integration_burden,
    compute_overlap_pairs,
    compute_summary_metrics,
    task_from_lock,
    to_dict,
    validate_actual_files,
    write_eval_run,
)
from experiments.greenhouse.strategies import (
    NAIVE_PARALLEL_BLIND_STRATEGY,
    SINGLE_AGENT_STRATEGY,
    LockfileEchoMockLLM,
    TaskApplyOutcome,
    _build_single_agent_prompt,
    _extract_task_id,
    _parse_single_agent_applied_envelopes,
    _PromptCountingLLM,
    _proposals_for_task_envelope_blob,
    _proposals_to_planned_applied_eval_task,
    _proposals_to_planned_eval_task,
    _scoped_repo_graph,
    estimate_prompt_tokens,
    run_strategy,
)

# ---------------------------------------------------------------------------
# Test helpers — synthetic Greenhouse-style lockfile.
# ---------------------------------------------------------------------------


def _greenhouse_task(
    task_id: str,
    service_path: str,
    *,
    extra_predicted: list[str] | None = None,
) -> Task:
    """Build a Greenhouse-shaped lockfile :class:`Task`.

    Each task touches ``pom.xml`` (for the Java 6 → 8 bump) and one
    service-specific file. ``allowed_paths`` is intentionally narrow per
    task so out-of-bounds proposals can be detected by validate_write.
    """
    paths = [service_path, "pom.xml"] + list(extra_predicted or [])
    return Task(
        id=task_id,
        prompt=f"replace anonymous RowMapper in {service_path} with a Java 8 lambda",
        predicted_writes=[
            PredictedWrite(path=p, confidence=0.95 - 0.05 * i, reason=f"hint-{i}")
            for i, p in enumerate(paths)
        ],
        allowed_paths=[service_path, "pom.xml"],
        depends_on=[],
        parallel_group=None,
        rationale=None,
    )


def _build_lock(*, serialized: bool) -> AgentLock:
    """Three Greenhouse-style tasks all colliding on ``pom.xml``.

    When ``serialized=True`` we emit the planner's expected output: three
    serial groups (one task each). When ``False`` we build the unsafe
    parallel plan that ACG should never produce — useful for naive
    behavior tests.
    """
    tasks = [
        _greenhouse_task(
            "lambda-rowmapper-account",
            "src/main/java/com/springsource/greenhouse/account/JdbcAccountRepository.java",
        ),
        _greenhouse_task(
            "lambda-rowmapper-invite",
            "src/main/java/com/springsource/greenhouse/invite/JdbcInviteRepository.java",
        ),
        _greenhouse_task(
            "lambda-rowmapper-app",
            "src/main/java/com/springsource/greenhouse/develop/JdbcAppRepository.java",
        ),
    ]
    if serialized:
        groups = [
            Group(id=1, tasks=[tasks[0].id], type="serial", waits_for=[]),
            Group(id=2, tasks=[tasks[1].id], type="serial", waits_for=[1]),
            Group(id=3, tasks=[tasks[2].id], type="serial", waits_for=[2]),
        ]
        conflicts = [
            Conflict(
                files=["pom.xml"],
                between_tasks=[tasks[0].id, tasks[1].id],
                resolution="serialize on pom.xml",
            ),
            Conflict(
                files=["pom.xml"],
                between_tasks=[tasks[0].id, tasks[2].id],
                resolution="serialize on pom.xml",
            ),
            Conflict(
                files=["pom.xml"],
                between_tasks=[tasks[1].id, tasks[2].id],
                resolution="serialize on pom.xml",
            ),
        ]
    else:
        groups = [
            Group(id=1, tasks=[t.id for t in tasks], type="parallel", waits_for=[]),
        ]
        conflicts = []

    return AgentLock(
        version="1.0",
        generated_at=AgentLock.utcnow(),
        generator=Generator(tool="acg", version="test", model="mock"),
        repo=Repo(
            root="experiments/greenhouse/checkout",
            git_url="https://github.com/spring-attic/greenhouse.git",
            commit="174c1c320875a66447deb2a15d04fc86afd07f60",
            languages=["java"],
        ),
        tasks=tasks,
        execution_plan=ExecutionPlan(groups=groups),
        conflicts_detected=conflicts,
    )


# ---------------------------------------------------------------------------
# Schema + builders.
# ---------------------------------------------------------------------------


def test_eval_run_serializes_with_required_top_level_keys(tmp_path: Path) -> None:
    """`write_eval_run` must produce JSON containing every megaplan v0.1 key."""
    run = EvalRun(
        run_id="test-run",
        created_at="2026-04-25T00:00:00Z",
        strategy="naive_parallel",
        backend="mock",
        lockfile="x.json",
        tasks=[
            EvalTask(
                task_id="t1",
                status="completed",
                actual_changed_files=["pom.xml"],
            )
        ],
        summary_metrics=SummaryMetrics(tasks_total=1, tasks_completed=1),
    )
    out = write_eval_run(run, tmp_path / "eval_run.json")
    payload = json.loads(out.read_text())
    required = {
        "version",
        "run_id",
        "created_at",
        "suite_name",
        "strategy",
        "backend",
        "model",
        "repo",
        "lockfile",
        "tasks",
        "summary_metrics",
    }
    assert required.issubset(payload.keys())
    assert payload["version"] == EVAL_VERSION
    assert "provider" in payload["model"]
    assert payload["repo"]["commit"]  # Greenhouse pin always present.
    assert payload["tasks"][0]["task_id"] == "t1"
    assert "integration_burden" in payload["summary_metrics"]
    # JSON must be sort_keys=True for stable diffs.
    serialized = out.read_text()
    assert serialized.index('"backend"') < serialized.index('"strategy"')


def test_task_from_lock_copies_predicted_and_allowed() -> None:
    lock = _build_lock(serialized=True)
    eval_task = task_from_lock(lock.tasks[0], prompt="custom prompt")
    assert eval_task.task_id == "lambda-rowmapper-account"
    assert "pom.xml" in eval_task.predicted_write_files
    assert "pom.xml" in eval_task.allowed_write_globs
    assert eval_task.prompt == "custom prompt"
    # Defaults intact for fields the lockfile doesn't fill.
    assert eval_task.actual_changed_files == []
    assert eval_task.test.ran is False


# ---------------------------------------------------------------------------
# Overlap counting.
# ---------------------------------------------------------------------------


def test_compute_overlap_pairs_three_tasks_share_pom_xml() -> None:
    """All three Greenhouse tasks edit pom.xml ⇒ C(3, 2) = 3 overlap pairs."""
    tasks = [
        EvalTask(task_id="account", actual_changed_files=["JdbcAccountRepository.java", "pom.xml"]),
        EvalTask(task_id="invite", actual_changed_files=["JdbcInviteRepository.java", "pom.xml"]),
        EvalTask(task_id="app", actual_changed_files=["JdbcAppRepository.java", "pom.xml"]),
    ]
    assert compute_overlap_pairs(tasks) == 3


def test_compute_overlap_pairs_disjoint_tasks_zero() -> None:
    tasks = [
        EvalTask(task_id="a", actual_changed_files=["a.java"]),
        EvalTask(task_id="b", actual_changed_files=["b.java"]),
    ]
    assert compute_overlap_pairs(tasks) == 0


def test_annotate_overlaps_populates_each_task() -> None:
    tasks = [
        EvalTask(task_id="account", actual_changed_files=["pom.xml"]),
        EvalTask(task_id="invite", actual_changed_files=["pom.xml"]),
        EvalTask(task_id="app", actual_changed_files=["solo.txt"]),
    ]
    annotate_overlaps(tasks)
    assert tasks[0].overlaps_with == ["invite"]
    assert tasks[1].overlaps_with == ["account"]
    assert tasks[2].overlaps_with == []


def test_compute_integration_burden_three_tasks_share_pom_xml() -> None:
    """Three two-file task outputs sharing pom.xml expose duplicate touches."""
    tasks = [
        EvalTask(task_id="account", actual_changed_files=["JdbcAccountRepository.java", "pom.xml"]),
        EvalTask(task_id="invite", actual_changed_files=["JdbcInviteRepository.java", "pom.xml"]),
        EvalTask(task_id="app", actual_changed_files=["JdbcAppRepository.java", "pom.xml"]),
    ]

    burden = compute_integration_burden(tasks)

    assert burden.changed_file_mentions_total == 6
    assert burden.unique_changed_files == 4
    assert burden.duplicate_file_touches == 2
    assert burden.overlapping_task_pairs == 3
    assert burden.overlapping_files == ["pom.xml"]


def test_compute_integration_burden_disjoint_tasks_zero_duplicates() -> None:
    tasks = [
        EvalTask(task_id="a", actual_changed_files=["a.java"]),
        EvalTask(task_id="b", actual_changed_files=["b.java"]),
    ]

    burden = compute_integration_burden(tasks)

    assert burden.duplicate_file_touches == 0
    assert burden.overlapping_task_pairs == 0
    assert burden.overlapping_files == []


def test_compute_integration_burden_counts_blocked_events_for_review_mentions() -> None:
    tasks = [
        EvalTask(
            task_id="a",
            actual_changed_files=["a.java"],
            blocked_write_events=[
                BlockedWriteEvent(
                    file="a.java",
                    description="blocked duplicate",
                    reason="outside allowed_paths",
                ),
                BlockedWriteEvent(
                    file="x.java",
                    description="blocked extra",
                    reason="outside allowed_paths",
                ),
            ],
        )
    ]

    burden = compute_integration_burden(tasks)

    assert burden.changed_file_mentions_total == 1
    assert burden.blocked_events_total == 2
    assert burden.review_file_mentions_total == 3
    assert burden.review_unique_files_total == 2


# ---------------------------------------------------------------------------
# Allowed-path validation.
# ---------------------------------------------------------------------------


def test_validate_actual_files_flags_out_of_bounds_for_account_task() -> None:
    """A task that allegedly modified `develop/Foo.java` is out-of-bounds."""
    lock = _build_lock(serialized=True)
    out_of_bounds, blocked = validate_actual_files(
        lock,
        "lambda-rowmapper-account",
        [
            "src/main/java/com/springsource/greenhouse/account/JdbcAccountRepository.java",
            "src/main/java/com/springsource/greenhouse/develop/Stranger.java",
        ],
    )
    assert out_of_bounds == ["src/main/java/com/springsource/greenhouse/develop/Stranger.java"]
    assert len(blocked) == 1
    assert "outside" in (blocked[0].reason or "").lower() or "allowed_paths" in (
        blocked[0].reason or ""
    )


# ---------------------------------------------------------------------------
# Summary metric arithmetic.
# ---------------------------------------------------------------------------


def test_compute_summary_metrics_basic_arithmetic() -> None:
    """Two completed (one with passing test), one failed → known summary."""
    tasks = [
        EvalTask(
            task_id="a",
            status="completed",
            actual_changed_files=["a.java"],
            test=TaskTest(ran=True, passed=True),
        ),
        EvalTask(
            task_id="b",
            status="completed",
            actual_changed_files=["b.java"],
            test=TaskTest(ran=False),
        ),
        EvalTask(
            task_id="c",
            status="failed",
            actual_changed_files=[],
            failure_reason="AGENT_FAIL",
        ),
    ]
    summary = compute_summary_metrics(tasks, wall_time_seconds=1800.0)  # 0.5 hours
    assert summary.tasks_total == 3
    assert summary.tasks_completed == 2
    assert summary.task_completion_rate == round(2 / 3, 4)
    # 2 completions in half an hour ⇒ 4 tasks/hour.
    assert summary.tasks_completed_per_hour == pytest.approx(4.0, rel=1e-6)
    assert summary.first_run_pass_rate == round(1 / 3, 4)
    assert summary.successful_parallel_speedup is None


def test_compute_summary_metrics_speedup_when_baseline_provided() -> None:
    tasks = [EvalTask(task_id="a", status="completed", actual_changed_files=["x"])]
    summary = compute_summary_metrics(
        tasks,
        wall_time_seconds=600.0,
        sequential_wall_time_seconds=1800.0,
    )
    assert summary.successful_parallel_speedup == pytest.approx(3.0, rel=1e-6)


def test_compute_summary_metrics_unsafe_completion_does_not_count() -> None:
    """Megaplan rule: ``completed_unsafe`` must not increment ``tasks_completed``."""
    tasks = [
        EvalTask(task_id="a", status="completed_unsafe", actual_changed_files=["a"]),
        EvalTask(task_id="b", status="completed", actual_changed_files=["b"]),
    ]
    summary = compute_summary_metrics(tasks, wall_time_seconds=600.0)
    assert summary.tasks_completed == 1
    assert summary.task_completion_rate == 0.5


# ---------------------------------------------------------------------------
# Strategy execution against the mock backend.
# ---------------------------------------------------------------------------


def test_lockfile_echo_mock_emits_predicted_writes_for_known_task() -> None:
    """The mock LLM should echo lockfile predicted_writes when prompted."""
    import asyncio

    lock = _build_lock(serialized=True)
    llm = LockfileEchoMockLLM(lock)
    user_blob = "Task id: lambda-rowmapper-account\nTask: …"
    reply = asyncio.run(llm.complete([{"role": "user", "content": user_blob}]))
    payload = json.loads(reply.content)
    files = [w["file"] for w in payload["writes"]]
    assert "pom.xml" in files
    assert any("JdbcAccountRepository" in f for f in files)


def test_naive_parallel_persists_raw_replies(tmp_path: Path) -> None:
    """Worker raw replies land under ``<eval_dump_dir>/naive_parallel_raw/``."""
    lock = _build_lock(serialized=False)
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(lock.model_dump_json(indent=2))
    dump = tmp_path / "eval_dump"
    dump.mkdir()
    run_strategy(
        strategy="naive_parallel",
        backend="mock",
        lock=lock,
        repo_graph={},
        lockfile_path=str(lock_path),
        eval_dump_dir=dump,
    )
    for t in lock.tasks:
        p = dump / "naive_parallel_raw" / f"{t.id}.txt"
        assert p.is_file(), f"missing {p}"
        assert p.read_text(encoding="utf-8")


def test_naive_strategy_does_not_inherit_acg_auto_replan_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_run_naive_parallel`` must override ``ACG_AUTO_REPLAN`` from env.

    Regression for the harness bug where naive workers, which run first in
    the seeded strategy loop, were promoting candidate paths via the runtime
    auto-replan branch because they fell back to ``RuntimeConfig.from_env()``.
    The mutated lock then bled into the planned/replan strategies and
    erased the distinction between planned writes and approved replans in
    the serialized artifacts.
    """
    monkeypatch.setenv("ACG_AUTO_REPLAN", "1")

    captured: list[bool] = []
    from acg import runtime as runtime_mod

    real_run_worker = runtime_mod.run_worker

    async def spy(*args, config=None, **kwargs):  # type: ignore[no-untyped-def]
        captured.append(bool(config and config.auto_replan))
        return await real_run_worker(*args, config=config, **kwargs)

    monkeypatch.setattr(runtime_mod, "run_worker", spy)
    # The strategies module imports run_worker at module load time.
    from experiments.greenhouse import strategies as strategies_mod

    monkeypatch.setattr(strategies_mod, "run_worker", spy)

    lock = _build_lock(serialized=False)
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(lock.model_dump_json(indent=2))
    run_strategy(
        strategy="naive_parallel",
        backend="mock",
        lock=lock,
        repo_graph={},
        lockfile_path=str(lock_path),
    )

    assert captured, "expected naive_parallel to invoke run_worker"
    assert not any(captured), (
        "naive_parallel must pass auto_replan=False even when "
        f"ACG_AUTO_REPLAN=1 is set in env; saw {captured}"
    )


def test_naive_parallel_blind_dispatch_uses_blind_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[bool] = []
    from acg import runtime as runtime_mod

    real_run_worker = runtime_mod.run_worker

    async def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured.append(bool(kwargs.get("include_lockfile_hints", True)))
        return await real_run_worker(*args, **kwargs)

    monkeypatch.setattr(runtime_mod, "run_worker", spy)
    from experiments.greenhouse import strategies as strategies_mod

    monkeypatch.setattr(strategies_mod, "run_worker", spy)

    lock = _build_lock(serialized=False)
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(lock.model_dump_json(indent=2))
    run_strategy(
        strategy=NAIVE_PARALLEL_BLIND_STRATEGY,
        backend="mock",
        lock=lock,
        repo_graph={},
        lockfile_path=str(lock_path),
    )
    assert captured
    assert all(h is False for h in captured)


def test_applied_truncation_marks_failed_truncated_by_max_tokens() -> None:
    lock = _build_lock(serialized=False)
    lock_task = lock.tasks[0]
    worker = WorkerResult(
        task_id=lock_task.id,
        group_id=0,
        url="stub",
        model="stub",
        wall_s=1.0,
        completion_tokens=100,
        finish_reason="length",
        raw_content="",
        proposals=[],
        allowed_count=0,
        blocked_count=0,
        error="finish_reason=length; output truncated at max_tokens=provider-native",
    )
    outcome = TaskApplyOutcome(changed_files=[], patch_na=False)
    eval_task = _proposals_to_planned_applied_eval_task(
        worker,
        lock_task,
        started_at="t0",
        finished_at="t1",
        prompt=None,
        task_outcome=outcome,
    )
    assert eval_task.status == "failed"
    assert eval_task.failure_reason == "TRUNCATED_BY_MAX_TOKENS"
    assert eval_task.patch_na_reason == worker.error


def test_run_strategy_uncapped_when_acg_worker_concurrency_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ACG_WORKER_CONCURRENCY", raising=False)
    captured: list[int | None] = []
    from experiments.greenhouse import strategies as strategies_mod

    real = strategies_mod._gather_capped

    async def spy(coros, cap):  # type: ignore[no-untyped-def]
        captured.append(cap)
        return await real(coros, cap)

    monkeypatch.setattr(strategies_mod, "_gather_capped", spy)

    lock = _build_lock(serialized=False)
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(lock.model_dump_json(indent=2))
    run_strategy(
        strategy="naive_parallel",
        backend="mock",
        lock=lock,
        repo_graph={},
        lockfile_path=str(lock_path),
        cap_parallelism=None,
    )
    assert captured == [None]


def test_run_strategy_honors_acg_worker_concurrency_when_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACG_WORKER_CONCURRENCY", "2")
    captured: list[int | None] = []
    from experiments.greenhouse import strategies as strategies_mod

    real = strategies_mod._gather_capped

    async def spy(coros, cap):  # type: ignore[no-untyped-def]
        captured.append(cap)
        return await real(coros, cap)

    monkeypatch.setattr(strategies_mod, "_gather_capped", spy)

    lock = _build_lock(serialized=False)
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(lock.model_dump_json(indent=2))
    run_strategy(
        strategy="naive_parallel",
        backend="mock",
        lock=lock,
        repo_graph={},
        lockfile_path=str(lock_path),
        cap_parallelism=None,
    )
    assert captured == [2]


def test_naive_parallel_dispatch_keeps_lockfile_hints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[bool] = []
    from acg import runtime as runtime_mod

    real_run_worker = runtime_mod.run_worker

    async def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        captured.append(bool(kwargs.get("include_lockfile_hints", True)))
        return await real_run_worker(*args, **kwargs)

    monkeypatch.setattr(runtime_mod, "run_worker", spy)
    from experiments.greenhouse import strategies as strategies_mod

    monkeypatch.setattr(strategies_mod, "run_worker", spy)

    lock = _build_lock(serialized=False)
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(lock.model_dump_json(indent=2))
    run_strategy(
        strategy="naive_parallel",
        backend="mock",
        lock=lock,
        repo_graph={},
        lockfile_path=str(lock_path),
    )
    assert captured
    assert all(h is True for h in captured)


def test_naive_strategy_records_overlap_on_pom_xml(tmp_path: Path) -> None:
    """Mock naive run on the 3-task Greenhouse lockfile must surface overlaps."""
    lock = _build_lock(serialized=False)
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(lock.model_dump_json(indent=2))
    run = run_strategy(
        strategy="naive_parallel",
        backend="mock",
        lock=lock,
        repo_graph={},
        lockfile_path=str(lock_path),
    )
    assert run.summary_metrics.tasks_total == 3
    assert run.summary_metrics.overlapping_write_pairs == 3
    assert run.model.provider == "mock"
    assert run.model.model == "lockfile-echo"
    assert run.execution_mode == "propose_validate"
    assert run.evidence_kind == "proposed_write_set"
    assert run.summary_metrics.tests_ran_count == 0
    assert run.summary_metrics.tokens_prompt_method == "estimated_chars_div_4"
    # Every task's actual_changed_files contains pom.xml because naive does
    # not enforce; validate_write also does not flag in-bounds writes since
    # pom.xml is allowed for every task.
    for task in run.tasks:
        assert "pom.xml" in task.actual_changed_files
        assert task.actual_changed_files_kind == "proposed_write_set"


def test_single_agent_prompt_excludes_lockfile_contract_terms() -> None:
    lock = _build_lock(serialized=True)
    messages = _build_single_agent_prompt(lock, _greenhouse_repo_graph())
    blob = "\n".join(message["content"] for message in messages)

    assert "Predicted writable files" not in blob
    assert "Candidate context" not in blob
    assert "allowed_paths" not in blob
    assert "allowed path" not in blob.lower()
    assert "candidate context" not in blob.lower()
    assert "execution plan" not in blob.lower()


def test_single_agent_strategy_records_suite_level_no_lock_run(tmp_path: Path) -> None:
    lock = _build_lock(serialized=True)
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(lock.model_dump_json(indent=2))

    run = run_strategy(
        strategy=SINGLE_AGENT_STRATEGY,
        backend="mock",
        lock=lock,
        repo_graph=_greenhouse_repo_graph(),
        lockfile_path=str(lock_path),
    )

    assert run.strategy == SINGLE_AGENT_STRATEGY
    assert run.execution_mode == "single_agent_no_lock"
    assert run.evidence_kind == "suite_proposed_write_set"
    assert run.model.model == "mock-no-lock-suite"
    assert run.summary_metrics.tokens_planner_total is None
    assert run.summary_metrics.tokens_scope_review_total is None
    assert run.summary_metrics.tokens_prompt_total is not None
    assert any(task.actual_changed_files for task in run.tasks)


def test_acg_planned_strategy_zero_merge_conflicts_via_serialization(tmp_path: Path) -> None:
    """With the serialized plan, no concurrent writes to pom.xml occur.

    ``overlapping_write_pairs`` stays at 3 because that metric scores the
    *lockfile fact* that all three tasks claim ``pom.xml`` — it is not a
    runtime metric. The runtime story shows up in ``merge_conflicts=0``
    (planner serialized them) and in wall time on real backends.
    """
    lock = _build_lock(serialized=True)
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(lock.model_dump_json(indent=2))
    run = run_strategy(
        strategy="acg_planned",
        backend="mock",
        lock=lock,
        repo_graph={},
        lockfile_path=str(lock_path),
    )
    # The mock echoes predicted_writes, all of which fall within the
    # task's own allowed_paths ⇒ zero blocked, zero out-of-bounds.
    assert run.summary_metrics.blocked_invalid_write_count == 0
    assert run.summary_metrics.out_of_bounds_write_count == 0
    # Each worker only writes its own service file + pom.xml — but planned
    # serializes the three pom.xml-edits, so by the time it runs they are
    # *sequential*; the eval still records each task's actual writes, so
    # post-hoc overlap math sees three pom.xml claimants. That is the
    # "ACG correctly serializes the collision" story; the diagnostic is in
    # `merge_conflicts=0` (planner never let them apply concurrently).
    assert run.summary_metrics.merge_conflicts == 0
    # All three tasks are conservatively "completed" (mock backend).
    assert run.summary_metrics.tasks_completed == 3


def test_run_strategy_rejects_unknown_backend() -> None:
    lock = _build_lock(serialized=True)
    with pytest.raises(ValueError, match="devin"):
        run_strategy(
            strategy="naive_parallel",
            backend="devin-manual",
            lock=lock,
            repo_graph={},
            lockfile_path="x.json",
        )


# ---------------------------------------------------------------------------
# Context savings: token estimation, scoped graphs, naive vs planned delta.
# ---------------------------------------------------------------------------


def _greenhouse_repo_graph() -> dict:
    """Synthetic repo graph mixing in-scope service files with unrelated noise.

    Each of the three tasks' ``allowed_paths`` matches its own service
    package + ``pom.xml``. The graph ALSO contains files in unrelated
    packages (``signup/``, ``utils/``, etc.) that no task should see in a
    scoped run \u2014 they're the input-token waste the planned strategy
    should eliminate.
    """
    in_scope = [
        "pom.xml",
        "src/main/java/com/springsource/greenhouse/account/JdbcAccountRepository.java",
        "src/main/java/com/springsource/greenhouse/account/AccountRepository.java",
        "src/main/java/com/springsource/greenhouse/account/Account.java",
        "src/main/java/com/springsource/greenhouse/invite/JdbcInviteRepository.java",
        "src/main/java/com/springsource/greenhouse/invite/InviteRepository.java",
        "src/main/java/com/springsource/greenhouse/invite/Invite.java",
        "src/main/java/com/springsource/greenhouse/develop/JdbcAppRepository.java",
        "src/main/java/com/springsource/greenhouse/develop/AppRepository.java",
        "src/main/java/com/springsource/greenhouse/develop/App.java",
    ]
    noise = (
        [
            f"src/main/java/com/springsource/greenhouse/signup/{n}.java"
            for n in ("Signup", "SignupForm", "SignupController", "SignupValidator")
        ]
        + [
            f"src/main/java/com/springsource/greenhouse/utils/{n}.java"
            for n in ("StringUtils", "DateUtils", "FormatUtils", "JsonUtils")
        ]
        + [
            f"src/main/java/com/springsource/greenhouse/connect/{n}.java"
            for n in ("ConnectController", "ConnectInterceptor")
        ]
        + [
            f"src/main/java/com/springsource/greenhouse/database/{n}.java"
            for n in ("DatabaseConfig", "JdbcTemplate")
        ]
    )
    paths = in_scope + noise
    files = [
        {"path": p, "import_fan_in": 10 - i // 5}  # roughly descending fan-in
        for i, p in enumerate(paths)
    ]
    return {"files": files}


def test_estimate_prompt_tokens_chars_per_4_heuristic() -> None:
    """``estimate_prompt_tokens`` must return ``ceil(total_chars / 4)`` (floored at 1)."""
    assert estimate_prompt_tokens([]) == 1  # floor
    assert estimate_prompt_tokens([{"role": "user", "content": ""}]) == 1
    assert estimate_prompt_tokens([{"role": "user", "content": "a" * 4}]) == 1
    assert estimate_prompt_tokens([{"role": "user", "content": "a" * 8}]) == 2
    # Sum across messages.
    assert (
        estimate_prompt_tokens(
            [
                {"role": "system", "content": "x" * 100},
                {"role": "user", "content": "y" * 200},
            ]
        )
        == 75
    )


def test_extract_task_id_finds_worker_marker() -> None:
    """``Task id: <id>`` line must be recovered from the user message."""
    msgs = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "Task id: lambda-rowmapper-account\nTask: foo\n"},
    ]
    assert _extract_task_id(msgs) == "lambda-rowmapper-account"


def test_extract_task_id_returns_none_for_orchestrator_messages() -> None:
    """Orchestrator prompts have no Task id marker \u2192 None (overhead)."""
    msgs = [
        {"role": "system", "content": "You are an orchestrator..."},
        {"role": "user", "content": "Lockfile summary:\n{...}"},
    ]
    assert _extract_task_id(msgs) is None


def test_prompt_counter_prefers_provider_prompt_tokens() -> None:
    """OpenRouter/OpenAI-compatible usage.prompt_tokens should beat estimates."""
    import asyncio

    class ProviderTokenLLM:
        url = "stub://provider"
        model = "stub"

        async def complete(self, messages, *, max_tokens=700, temperature=0.2):
            del messages, max_tokens, temperature
            return LLMReply(
                content='{"writes":[]}',
                reasoning="",
                completion_tokens=3,
                finish_reason="stop",
                wall_s=0.0,
                prompt_tokens=123,
            )

        async def aclose(self):
            return None

    wrapped = _PromptCountingLLM(ProviderTokenLLM())
    asyncio.run(
        wrapped.complete(
            [{"role": "user", "content": "Task id: lambda-rowmapper-account\nTask: x"}]
        )
    )

    assert wrapped.tokens_by_task["lambda-rowmapper-account"] == 123
    assert wrapped.prompt_token_method == "provider_usage_prompt_tokens"


def test_scoped_repo_graph_filters_files_to_allowed_paths() -> None:
    """A scoped graph must only contain files inside ``allowed_paths``."""
    lock = _build_lock(serialized=True)
    full = _greenhouse_repo_graph()
    # account task's allowed_paths = its JdbcAccountRepository.java + pom.xml
    scoped = _scoped_repo_graph(full, lock, "lambda-rowmapper-account")
    paths = {f["path"] for f in scoped["files"]}
    assert "pom.xml" in paths
    assert "src/main/java/com/springsource/greenhouse/account/JdbcAccountRepository.java" in paths
    # Other-service files must not leak through.
    assert not any("invite/" in p for p in paths)
    assert not any("develop/" in p for p in paths)
    assert not any("signup/" in p for p in paths)
    assert not any("utils/" in p for p in paths)
    # Scoped graph must be a strict subset.
    assert len(scoped["files"]) < len(full["files"])


def test_planned_uses_fewer_prompt_tokens_than_naive_on_mock(tmp_path: Path) -> None:
    """The pitch in numbers: planned worker prompts < naive worker prompts.

    Both strategies hit the same mock LLM; the only difference is the
    repo_graph each worker receives. Naive sees the global top-K of the
    full graph; planned sees only files inside its task's allowed_paths.
    The summary must reflect that delta in ``tokens_prompt_total``.
    """
    lock = _build_lock(serialized=True)
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(lock.model_dump_json(indent=2))
    repo_graph = _greenhouse_repo_graph()

    naive = run_strategy(
        strategy="naive_parallel",
        backend="mock",
        lock=lock,
        repo_graph=repo_graph,
        lockfile_path=str(lock_path),
    )
    planned = run_strategy(
        strategy="acg_planned",
        backend="mock",
        lock=lock,
        repo_graph=repo_graph,
        lockfile_path=str(lock_path),
    )

    # Both must populate per-task tokens_prompt for every task.
    assert all(t.metrics.tokens_prompt is not None for t in naive.tasks)
    assert all(t.metrics.tokens_prompt is not None for t in planned.tasks)

    # The headline savings claim: planned prompt tokens strictly less.
    assert (
        planned.summary_metrics.tokens_prompt_total < naive.summary_metrics.tokens_prompt_total
    ), (
        f"expected planned ({planned.summary_metrics.tokens_prompt_total}) "
        f"< naive ({naive.summary_metrics.tokens_prompt_total})"
    )


def test_full_context_planned_ablation_isolates_scoped_context_tokens(
    tmp_path: Path,
) -> None:
    """Planned-full-context keeps the schedule but removes scoped prompts.

    This is the paper-safe ablation: naive and planned-full-context see the
    same full repo graph, while scoped planned sees only allowed-path files.
    Any token delta between the two planned variants is therefore caused by
    context scoping, not by serialization or a runtime orchestrator.
    """
    lock = _build_lock(serialized=True)
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(lock.model_dump_json(indent=2))
    repo_graph = _greenhouse_repo_graph()

    naive = run_strategy(
        strategy="naive_parallel",
        backend="mock",
        lock=lock,
        repo_graph=repo_graph,
        lockfile_path=str(lock_path),
    )
    full_context = run_strategy(
        strategy="acg_planned_full_context",
        backend="mock",
        lock=lock,
        repo_graph=repo_graph,
        lockfile_path=str(lock_path),
    )
    scoped = run_strategy(
        strategy="acg_planned",
        backend="mock",
        lock=lock,
        repo_graph=repo_graph,
        lockfile_path=str(lock_path),
    )

    assert full_context.strategy == "acg_planned_full_context"
    assert full_context.summary_metrics.tokens_prompt_total == (
        naive.summary_metrics.tokens_prompt_total
    )
    assert scoped.summary_metrics.tokens_prompt_total < (
        full_context.summary_metrics.tokens_prompt_total
    )
    assert full_context.summary_metrics.tokens_orchestrator_overhead is None


def test_planned_uses_static_lockfile_without_extra_orchestrator_tax(tmp_path: Path) -> None:
    """Default planned execution walks the compiled lockfile directly."""
    lock = _build_lock(serialized=True)
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(lock.model_dump_json(indent=2))
    repo_graph = _greenhouse_repo_graph()

    naive = run_strategy(
        strategy="naive_parallel",
        backend="mock",
        lock=lock,
        repo_graph=repo_graph,
        lockfile_path=str(lock_path),
    )
    planned = run_strategy(
        strategy="acg_planned",
        backend="mock",
        lock=lock,
        repo_graph=repo_graph,
        lockfile_path=str(lock_path),
    )

    assert naive.summary_metrics.tokens_orchestrator_overhead is None
    assert planned.summary_metrics.tokens_orchestrator_overhead is None


# ---------------------------------------------------------------------------
# Devin manual + API stub.
# ---------------------------------------------------------------------------


def test_devin_manual_loads_sidecar_and_flags_oob(tmp_path: Path) -> None:
    """A Devin-reported file outside allowed_paths flips the task to unsafe."""
    lock = _build_lock(serialized=True)
    sidecar = tmp_path / "devin_naive.json"
    sidecar.write_text(
        json.dumps(
            {
                "strategy": "naive_parallel",
                "wall_time_seconds": 1800.0,
                "tasks": [
                    {
                        "task_id": "lambda-rowmapper-account",
                        "session_id": "devin-aaa",
                        "status": "completed",
                        "actual_changed_files": [
                            "src/main/java/com/springsource/greenhouse/account/JdbcAccountRepository.java",
                            "src/main/java/com/springsource/greenhouse/develop/Stranger.java",
                        ],
                        "wall_time_seconds": 600.0,
                        "human_interventions": 1,
                    },
                    {
                        "task_id": "lambda-rowmapper-invite",
                        "session_id": "devin-bbb",
                        "status": "completed",
                        "actual_changed_files": [
                            "src/main/java/com/springsource/greenhouse/invite/JdbcInviteRepository.java",
                            "pom.xml",
                        ],
                        "wall_time_seconds": 700.0,
                    },
                    {
                        "task_id": "lambda-rowmapper-app",
                        "session_id": "devin-ccc",
                        "status": "failed",
                        "actual_changed_files": [],
                        "failure_reason": "INFRA_ERROR",
                    },
                ],
            }
        )
    )
    run = devin_adapter.run_devin_manual(
        strategy="naive_parallel",
        lock=lock,
        lockfile_path="agent_lock.json",
        devin_results_path=sidecar,
    )
    assert run.backend == "devin-manual"
    by_id = {t.task_id: t for t in run.tasks}
    assert by_id["lambda-rowmapper-account"].status == "completed_unsafe"
    assert by_id["lambda-rowmapper-account"].out_of_bounds_files == [
        "src/main/java/com/springsource/greenhouse/develop/Stranger.java"
    ]
    assert by_id["lambda-rowmapper-invite"].status == "completed"
    assert by_id["lambda-rowmapper-app"].status == "failed"
    assert run.summary_metrics.tasks_completed == 1
    assert run.summary_metrics.out_of_bounds_write_count == 1
    assert run.summary_metrics.human_interventions == 1


def test_manual_applied_diff_can_extract_changed_files_from_git_diff(tmp_path: Path) -> None:
    """Generic applied-diff sidecars derive actual files from a task branch."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    service = repo / "src/main/java/com/springsource/greenhouse/account/JdbcAccountRepository.java"
    service.parent.mkdir(parents=True)
    service.write_text("class Before {}\n")
    (repo / "pom.xml").write_text("<java-version>1.6</java-version>\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=ACG Test",
            "-c",
            "user.email=acg@example.com",
            "commit",
            "-m",
            "base",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "task/account"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    service.write_text("class After {}\n")
    (repo / "pom.xml").write_text("<java-version>1.8</java-version>\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=ACG Test",
            "-c",
            "user.email=acg@example.com",
            "commit",
            "-m",
            "task account",
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )

    lock = _build_lock(serialized=True)
    sidecar = tmp_path / "devin_git_diff.json"
    sidecar.write_text(
        json.dumps(
            {
                "strategy": "acg_planned",
                "repo_path": str(repo),
                "base_ref": "main",
                "wall_time_seconds": 60.0,
                "tasks": [
                    {
                        "task_id": "lambda-rowmapper-account",
                        "status": "completed",
                        "branch": "task/account",
                    }
                ],
            }
        )
    )

    run = devin_adapter.run_applied_diff_manual(
        strategy="acg_planned",
        lock=lock,
        lockfile_path="agent_lock.json",
        diff_results_path=sidecar,
    )

    assert run.backend == "applied-diff"
    assert run.execution_mode == "applied_diff"
    assert run.evidence_kind == "applied_diff"
    task = run.tasks[0]
    assert task.actual_changed_files_kind == "applied_diff"
    assert task.actual_changed_files == [
        "pom.xml",
        "src/main/java/com/springsource/greenhouse/account/JdbcAccountRepository.java",
    ]
    assert task.out_of_bounds_files == []
    assert task.metrics.changed_lines_added == 2
    assert task.metrics.changed_lines_deleted == 2
    assert task.metrics.changed_lines_kind == "git_numstat"
    assert run.summary_metrics.tasks_completed == 1
    assert run.summary_metrics.integration_burden.changed_lines_added == 2
    assert run.summary_metrics.integration_burden.changed_lines_deleted == 2
    assert run.summary_metrics.integration_burden.changed_lines_total == 4
    assert run.summary_metrics.integration_burden.diff_stats_kind == "git_numstat"
    assert run.repo.local_path == str(repo.resolve())

    devin_run = devin_adapter.run_devin_manual(
        strategy="acg_planned",
        lock=lock,
        lockfile_path="agent_lock.json",
        devin_results_path=sidecar,
    )
    assert devin_run.backend == "devin-manual"
    assert devin_run.tasks[0].actual_changed_files == task.actual_changed_files
    assert devin_run.summary_metrics.integration_burden.changed_lines_total == 4


def test_devin_manual_rejects_strategy_mismatch(tmp_path: Path) -> None:
    lock = _build_lock(serialized=True)
    sidecar = tmp_path / "x.json"
    sidecar.write_text(json.dumps({"strategy": "acg_planned", "tasks": [{"task_id": "x"}]}))
    with pytest.raises(devin_adapter.DevinManualError):
        devin_adapter.run_devin_manual(
            strategy="naive_parallel",
            lock=lock,
            lockfile_path="x.json",
            devin_results_path=sidecar,
        )


def test_devin_api_run_raises_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without ``DEVIN_API_KEY`` / ``DEVIN_ORG_ID``, the live backend bails cleanly."""
    monkeypatch.delenv("DEVIN_API_KEY", raising=False)
    monkeypatch.delenv("DEVIN_ORG_ID", raising=False)
    lock = _build_lock(serialized=True)
    with pytest.raises(devin_adapter.DevinAPINotConfigured):
        devin_adapter.devin_api_run(
            strategy="naive_parallel",
            lock=lock,
            lockfile_path="x.json",
            repo_url="https://github.com/example/greenhouse.git",
        )


def test_devin_api_run_rejects_unknown_strategy() -> None:
    lock = _build_lock(serialized=True)
    with pytest.raises(ValueError, match="naive_parallel or acg_planned"):
        devin_adapter.devin_api_run(
            strategy="something_else",
            lock=lock,
            lockfile_path="x.json",
            repo_url="https://github.com/example/greenhouse.git",
        )


def test_devin_api_run_requires_repo_url() -> None:
    lock = _build_lock(serialized=True)
    with pytest.raises(ValueError, match="repo_url"):
        devin_adapter.devin_api_run(
            strategy="naive_parallel",
            lock=lock,
            lockfile_path="x.json",
            repo_url="",
        )


# ---------------------------------------------------------------------------
# CLI smoke + report rendering.
# ---------------------------------------------------------------------------


def test_cli_writes_both_eval_run_files_under_out_dir(tmp_path: Path) -> None:
    """`--strategy both --backend mock` lays down naive + acg artifacts."""
    lock = _build_lock(serialized=True)
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(lock.model_dump_json(indent=2))
    out_dir = tmp_path / "runs"

    rc = headtohead.main(
        [
            "--lock",
            str(lock_path),
            "--strategy",
            "both",
            "--backend",
            "mock",
            "--out-dir",
            str(out_dir),
        ]
    )
    assert rc == 0
    naive = out_dir / "eval_run_naive.json"
    acg = out_dir / "eval_run_acg.json"
    combined = out_dir / "eval_run_combined.json"
    assert naive.exists()
    assert acg.exists()
    assert combined.exists()
    naive_payload = json.loads(naive.read_text())
    acg_payload = json.loads(acg.read_text())
    assert naive_payload["strategy"] == "naive_parallel"
    assert acg_payload["strategy"] == "acg_planned"
    assert naive_payload["execution_mode"] == "propose_validate"
    assert naive_payload["summary_metrics"]["tokens_prompt_method"] == "estimated_chars_div_4"
    assert naive_payload["summary_metrics"]["cost_usd_total"] is None
    # Hard correctness gate: planned must catch ≥ as many bad writes as naive,
    # AND naive must show overlaps that planned does not.
    assert (
        acg_payload["summary_metrics"]["blocked_invalid_write_count"]
        >= naive_payload["summary_metrics"]["blocked_invalid_write_count"]
    )
    assert naive_payload["summary_metrics"]["overlapping_write_pairs"] >= 1


def test_cli_ablation_writes_all_three_eval_run_files_under_out_dir(
    tmp_path: Path,
) -> None:
    """`--strategy ablation` writes naive, planned-full, and planned-scoped."""
    lock = _build_lock(serialized=True)
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(lock.model_dump_json(indent=2))
    out_dir = tmp_path / "runs"

    rc = headtohead.main(
        [
            "--lock",
            str(lock_path),
            "--strategy",
            "ablation",
            "--backend",
            "mock",
            "--out-dir",
            str(out_dir),
        ]
    )

    assert rc == 0
    naive = out_dir / "eval_run_naive.json"
    full_context = out_dir / "eval_run_acg_full_context.json"
    acg = out_dir / "eval_run_acg.json"
    combined = out_dir / "eval_run_combined.json"
    assert naive.exists()
    assert full_context.exists()
    assert acg.exists()
    assert combined.exists()

    combo = json.loads(combined.read_text())
    assert set(combo["strategies"]) == {
        "naive_parallel",
        "acg_planned_full_context",
        "acg_planned",
    }
    assert combo["strategies"]["acg_planned_full_context"]["strategy"] == (
        "acg_planned_full_context"
    )


def test_cli_comparison_writes_single_agent_and_acg_runs(tmp_path: Path) -> None:
    lock = _build_lock(serialized=True)
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(lock.model_dump_json(indent=2))
    out_dir = tmp_path / "runs"

    rc = headtohead.main(
        [
            "--lock",
            str(lock_path),
            "--strategy",
            "comparison",
            "--backend",
            "mock",
            "--out-dir",
            str(out_dir),
        ]
    )

    assert rc == 0
    assert (out_dir / "eval_run_single_agent.json").exists()
    combo = json.loads((out_dir / "eval_run_combined.json").read_text())
    assert set(combo["strategies"]) == {
        "single_agent",
        "naive_parallel",
        "acg_planned_full_context",
        "acg_planned",
    }
    assert combo["strategies"]["single_agent"]["execution_mode"] == "single_agent_no_lock"


def test_cli_applied_diff_backend_writes_applied_diff_artifact(tmp_path: Path) -> None:
    """`--backend applied-diff` records real-diff evidence, not proposals."""
    lock = _build_lock(serialized=True)
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(lock.model_dump_json(indent=2))
    sidecar = tmp_path / "applied_diff.json"
    sidecar.write_text(
        json.dumps(
            {
                "strategy": "acg_planned",
                "wall_time_seconds": 42.0,
                "tasks": [
                    {
                        "task_id": "lambda-rowmapper-account",
                        "status": "completed",
                        "actual_changed_files": [
                            "src/main/java/com/springsource/greenhouse/account/JdbcAccountRepository.java",
                            "pom.xml",
                        ],
                    }
                ],
            }
        )
    )
    out = tmp_path / "eval_run_applied.json"

    rc = headtohead.main(
        [
            "--lock",
            str(lock_path),
            "--strategy",
            "acg_planned",
            "--backend",
            "applied-diff",
            "--diff-results",
            str(sidecar),
            "--out",
            str(out),
        ]
    )

    assert rc == 0
    payload = json.loads(out.read_text())
    assert payload["backend"] == "applied-diff"
    assert payload["execution_mode"] == "applied_diff"
    assert payload["evidence_kind"] == "applied_diff"
    assert payload["tasks"][0]["actual_changed_files_kind"] == "applied_diff"
    assert "integration_burden" in payload["summary_metrics"]


def test_cli_realworld_like_run_does_not_emit_greenhouse_metadata(tmp_path: Path) -> None:
    """Non-Greenhouse checkouts must carry explicit repo/suite metadata."""
    lock = _build_lock(serialized=True)
    lock.repo.root = str(tmp_path / "realworld" / "checkout")
    lock.repo.git_url = None
    lock.repo.commit = None
    repo_path = Path(lock.repo.root)
    repo_path.mkdir(parents=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "init", "-b", "main"],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo_path),
            "-c",
            "user.name=ACG Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-m",
            "init",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    lock_path = tmp_path / "agent_lock.json"
    lock_path.write_text(lock.model_dump_json(indent=2))
    out = tmp_path / "eval_run.json"

    rc = headtohead.main(
        [
            "--lock",
            str(lock_path),
            "--repo",
            str(repo_path),
            "--suite-name",
            "realworld-nestjs",
            "--repo-url",
            "https://github.com/example/nestjs-realworld-example-app.git",
            "--repo-commit",
            "abc123",
            "--strategy",
            "acg_planned",
            "--backend",
            "mock",
            "--out",
            str(out),
        ]
    )

    assert rc == 0
    payload = json.loads(out.read_text())
    assert payload["suite_name"] == "realworld-nestjs"
    assert payload["repo"]["local_path"] == str(repo_path.resolve())
    assert payload["repo"]["url"] == "https://github.com/example/nestjs-realworld-example-app.git"
    assert payload["repo"]["commit"] == "abc123"
    assert payload["repo"]["url"] != "https://github.com/spring-attic/greenhouse.git"


def test_cli_rejects_missing_lockfile(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = headtohead.main(
        [
            "--lock",
            str(tmp_path / "missing.json"),
            "--strategy",
            "naive_parallel",
            "--backend",
            "mock",
            "--out",
            str(tmp_path / "out.json"),
        ]
    )
    assert rc == headtohead.EXIT_USER_ERROR
    captured = capsys.readouterr()
    assert "lockfile" in captured.err.lower()


def test_report_renders_markdown_table_with_both_strategies(tmp_path: Path) -> None:
    runs = [
        to_dict(
            EvalRun(
                run_id="naive",
                created_at="2026-04-25T00:00:00Z",
                strategy="naive_parallel",
                backend="mock",
                lockfile="x.json",
                tasks=[],
                summary_metrics=SummaryMetrics(
                    tasks_total=3,
                    tasks_completed=0,
                    overlapping_write_pairs=3,
                ),
            )
        ),
        to_dict(
            EvalRun(
                run_id="acg",
                created_at="2026-04-25T00:00:00Z",
                strategy="acg_planned",
                backend="mock",
                lockfile="x.json",
                tasks=[],
                summary_metrics=SummaryMetrics(
                    tasks_total=3,
                    tasks_completed=3,
                    overlapping_write_pairs=0,
                ),
            )
        ),
    ]
    table = report.render_markdown_table(runs)
    assert "Strategy" in table and "Tasks completed" in table
    assert "Evidence" in table and "proposed_write_set" in table
    assert "naive_parallel" in table
    assert "acg_planned" in table
    line = report.render_demo_line(runs)
    assert "Greenhouse" not in line  # demo line speaks about repo, not name
    assert "ACG" in line or "acg" in line.lower()


def test_report_chart_writes_png(tmp_path: Path) -> None:
    runs = [
        to_dict(
            EvalRun(
                run_id="naive",
                created_at="2026-04-25T00:00:00Z",
                strategy="naive_parallel",
                backend="mock",
                lockfile="x.json",
                tasks=[],
                summary_metrics=SummaryMetrics(
                    tasks_total=3, tasks_completed=1, overlapping_write_pairs=3
                ),
            )
        ),
        to_dict(
            EvalRun(
                run_id="acg",
                created_at="2026-04-25T00:00:00Z",
                strategy="acg_planned",
                backend="mock",
                lockfile="x.json",
                tasks=[],
                summary_metrics=SummaryMetrics(
                    tasks_total=3, tasks_completed=3, overlapping_write_pairs=0
                ),
            )
        ),
    ]
    out = tmp_path / "chart.png"
    report.render_chart(runs, out)
    assert out.exists() and out.stat().st_size > 0


# ---------------------------------------------------------------------------
# EvalTask defaults sanity (catches accidental mutable-default bugs).
# ---------------------------------------------------------------------------


def test_eval_task_default_metrics_independent_per_instance() -> None:
    a = EvalTask(task_id="a")
    b = EvalTask(task_id="b")
    a.metrics.human_interventions = 5
    assert b.metrics.human_interventions == 0
    assert a.metrics is not b.metrics
    # Same protection on TaskMetrics directly.
    assert TaskMetrics() is not TaskMetrics()


# ---------------------------------------------------------------------------
# Tightened-Greenhouse fixture: validator must visibly fire.
# ---------------------------------------------------------------------------


def test_tightened_greenhouse_lockfile_fires_validator(tmp_path: Path) -> None:
    """The hand-tightened ``agent_lock_tight.json`` must produce blocks.

    This is the v2 megaplan's negative-control fixture: ``allowed_paths``
    is hand-shrunk to the exact ground-truth files per task, while
    ``predicted_writes`` is left at the original (wider) size. The
    ``LockfileEchoMockLLM`` echoes ``predicted_writes`` so the worker
    proposes paths outside the tightened allowed_paths — and
    ``acg.enforce.validate_write`` records each as a
    :class:`BlockedWriteEvent`.

    Without this test, the project's safety claim is unfalsifiable
    (RESULTS.md §9): no committed artifact has ever shown the validator
    actually firing. With this test, ``blocked_invalid_write_count >= 1``
    is enforced in CI.
    """
    repo_root = Path(__file__).resolve().parent.parent
    tight_lock_path = repo_root / "experiments" / "greenhouse" / "agent_lock_tight.json"
    assert tight_lock_path.exists(), f"tightened lockfile missing at {tight_lock_path}"
    lock = AgentLock.model_validate_json(tight_lock_path.read_text())

    # ground-truth tightening: each task's allowed_paths is exactly
    # {pom.xml, <single-service-file>}.
    for task in lock.tasks:
        assert len(task.allowed_paths) == 2, (
            f"{task.id}: expected exactly 2 allowed paths in the tight "
            f"fixture, got {task.allowed_paths!r}"
        )
        assert "pom.xml" in task.allowed_paths
        assert all("**" not in p for p in task.allowed_paths), (
            f"{task.id}: tightened fixture must use exact paths, not globs"
        )

    run = run_strategy(
        strategy="acg_planned",
        backend="mock",
        lock=lock,
        repo_graph={},
        lockfile_path=str(tight_lock_path),
    )

    # The headline assertion: at least one blocked write event in total.
    assert run.summary_metrics.blocked_invalid_write_count >= 1, (
        "tightened lockfile produced zero blocked_write_events; "
        "the negative-control fixture is not actually firing the validator"
    )

    # And per-task: every task should block at least its predictor's
    # over-eager false positives. Three tasks × ≥1 block each.
    blocked_per_task = {t.task_id: len(t.blocked_write_events) for t in run.tasks}
    assert all(count >= 1 for count in blocked_per_task.values()), (
        f"every task should produce blocked events; got {blocked_per_task!r}"
    )

    # And the in-bounds writes should still land — pom.xml is allowed for
    # every task and JdbcAccountRepository.java is allowed for the account
    # task — so ``actual_changed_files`` is non-empty.
    actual_per_task = {t.task_id: t.actual_changed_files for t in run.tasks}
    assert all(len(files) >= 1 for files in actual_per_task.values()), (
        f"in-bounds proposals must still land; got {actual_per_task!r}"
    )


# ---------------------------------------------------------------------------
# Planned-mode status assignment for blocked-only proposals.
# ---------------------------------------------------------------------------


def _planned_task() -> Task:
    """Single lockfile task with one allowed path, used by status tests."""
    return Task(
        id="blocked-task",
        prompt="placeholder",
        predicted_writes=[PredictedWrite(path="src/in_scope.ts", confidence=0.9, reason="x")],
        allowed_paths=["src/in_scope.ts"],
        depends_on=[],
        parallel_group=None,
        rationale=None,
    )


def _worker_result(*, proposals: list[Proposal], error: str | None = None) -> WorkerResult:
    """Minimal WorkerResult shaped for `_proposals_to_planned_eval_task`."""
    allowed_count = sum(1 for p in proposals if p.allowed)
    blocked_count = len(proposals) - allowed_count
    return WorkerResult(
        task_id="blocked-task",
        group_id=1,
        url="mock://",
        model="mock",
        wall_s=0.1,
        completion_tokens=4,
        finish_reason="stop",
        raw_content="{}",
        proposals=proposals,
        allowed_count=allowed_count,
        blocked_count=blocked_count,
        error=error,
    )


def test_planned_eval_task_marks_blocked_when_all_proposals_rejected() -> None:
    """Regression: smoke run flagged `add-article-search` as completed even
    though every proposed write was rejected by the lock."""
    task = _planned_task()
    worker = _worker_result(
        proposals=[
            Proposal(
                file="src/out_of_scope.ts",
                description="agent guessed wrong",
                allowed=False,
                reason="path 'src/out_of_scope.ts' is candidate_context only",
                scope_status="needs_replan",
            )
        ]
    )
    eval_task = _proposals_to_planned_eval_task(
        worker,
        task,
        started_at="2026-05-11T22:20:23Z",
        finished_at="2026-05-11T22:20:59Z",
        prompt=None,
    )
    assert eval_task.actual_changed_files == []
    assert len(eval_task.blocked_write_events) == 1
    assert eval_task.status == "blocked"
    assert eval_task.failure_reason == "BLOCKED_BY_SCOPE"


def test_planned_eval_task_partial_block_still_completed() -> None:
    """Tasks with mixed allowed + blocked proposals stay completed —
    real progress happened, the burden metric records the rejects."""
    task = _planned_task()
    worker = _worker_result(
        proposals=[
            Proposal(
                file="src/in_scope.ts",
                description="allowed write",
                allowed=True,
                reason=None,
                scope_status="allowed",
            ),
            Proposal(
                file="src/out_of_scope.ts",
                description="rejected write",
                allowed=False,
                reason="outside allowed_paths",
                scope_status="blocked",
            ),
        ]
    )
    eval_task = _proposals_to_planned_eval_task(
        worker,
        task,
        started_at="2026-05-11T22:20:23Z",
        finished_at="2026-05-11T22:20:59Z",
        prompt=None,
    )
    assert eval_task.actual_changed_files == ["src/in_scope.ts"]
    assert len(eval_task.blocked_write_events) == 1
    assert eval_task.status == "completed"
    assert eval_task.failure_reason is None


def test_planned_eval_task_no_proposals_no_blocks_is_completed() -> None:
    """A worker that proposed nothing is a no-op, not a block. Status stays
    ``completed`` so we do not retroactively fail historic mock-backend
    artifacts."""
    task = _planned_task()
    worker = _worker_result(proposals=[])
    eval_task = _proposals_to_planned_eval_task(
        worker,
        task,
        started_at="2026-05-11T22:20:23Z",
        finished_at="2026-05-11T22:20:59Z",
        prompt=None,
    )
    assert eval_task.actual_changed_files == []
    assert eval_task.blocked_write_events == []
    assert eval_task.status == "completed"


def test_planned_eval_task_worker_error_takes_priority_over_blocks() -> None:
    """If the worker raised, status is `failed` even if there are blocked
    events recorded — the agent-fail signal is the more honest root cause."""
    task = _planned_task()
    worker = _worker_result(
        proposals=[
            Proposal(
                file="src/out_of_scope.ts",
                description="rejected",
                allowed=False,
                reason="outside allowed_paths",
                scope_status="blocked",
            )
        ],
        error="boom",
    )
    eval_task = _proposals_to_planned_eval_task(
        worker,
        task,
        started_at="2026-05-11T22:20:23Z",
        finished_at="2026-05-11T22:20:59Z",
        prompt=None,
    )
    assert eval_task.status == "failed"
    assert eval_task.failure_reason == "AGENT_FAIL"


def test_summary_metrics_excludes_blocked_from_tasks_completed() -> None:
    """Three tasks: two completed, one blocked → tasks_completed must be 2."""
    tasks = [
        EvalTask(task_id="a", status="completed", actual_changed_files=["a.ts"]),
        EvalTask(task_id="b", status="completed", actual_changed_files=["b.ts"]),
        EvalTask(
            task_id="c",
            status="blocked",
            failure_reason="BLOCKED_BY_SCOPE",
            actual_changed_files=[],
            blocked_write_events=[
                BlockedWriteEvent(
                    file="src/out.ts",
                    description="agent guessed wrong",
                    reason="outside allowed_paths",
                )
            ],
        ),
    ]
    summary = compute_summary_metrics(tasks, wall_time_seconds=1.0)
    assert summary.tasks_total == 3
    assert summary.tasks_completed == 2
    assert summary.task_completion_rate == 0.6667
    # `proposal_completion_rate` must also exclude blocked-only tasks because
    # `_is_proposal_completed` requires status in {"completed", "completed_unsafe"}.
    assert summary.proposal_completion_rate == 0.6667
    # The blocked event must still surface in burden metrics.
    assert summary.blocked_invalid_write_count == 1


def test_applied_diff_task_marks_failed_when_no_envelope() -> None:
    task = _planned_task()
    worker = _worker_result(
        proposals=[
            Proposal(
                file="src/in_scope.ts",
                description="x",
                allowed=True,
                reason=None,
                scope_status="allowed",
                envelope=None,
            )
        ]
    )
    eval_task = _proposals_to_planned_applied_eval_task(
        worker,
        task,
        started_at="2026-05-11T22:20:23Z",
        finished_at="2026-05-11T22:20:59Z",
        prompt=None,
        task_outcome=TaskApplyOutcome(),
    )
    assert eval_task.status == "failed"
    assert eval_task.failure_reason == "NO_APPLIED_CONTENT"


def test_applied_diff_task_marks_failed_on_empty_patch() -> None:
    task = _planned_task()
    env = "*** Begin Patch\n*** Update File: src/in_scope.ts\n@@\n*** End Patch\n"
    worker = _worker_result(
        proposals=[
            Proposal(
                file="src/in_scope.ts",
                description="x",
                allowed=True,
                reason=None,
                scope_status="allowed",
                envelope=env,
            )
        ]
    )
    eval_task = _proposals_to_planned_applied_eval_task(
        worker,
        task,
        started_at="2026-05-11T22:20:23Z",
        finished_at="2026-05-11T22:20:59Z",
        prompt=None,
        task_outcome=TaskApplyOutcome(),
    )
    assert eval_task.status == "failed"
    assert eval_task.failure_reason == "EMPTY_PATCH"


def test_naive_applied_writes_oob_files(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    (repo / "app").mkdir()
    (repo / "app" / "x.ts").write_text("// x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@e",
            "commit",
            "-m",
            "init",
        ],
        check=True,
        capture_output=True,
    )
    base_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    lock = AgentLock.model_validate(
        {
            "version": "1.0",
            "generated_at": datetime.now(UTC),
            "repo": {"root": str(repo), "commit": base_sha, "languages": ["ts"]},
            "tasks": [
                {
                    "id": "task_a",
                    "prompt": "x",
                    "predicted_writes": [{"path": "app/x.ts", "confidence": 0.9, "reason": "r"}],
                    "allowed_paths": ["app/**"],
                    "depends_on": [],
                    "parallel_group": 1,
                    "rationale": None,
                }
            ],
            "execution_plan": {
                "groups": [{"id": 1, "tasks": ["task_a"], "type": "parallel", "waits_for": []}]
            },
            "conflicts_detected": [],
        }
    )

    class OobEnvelopeLLM:
        url = "mock://oob-naive"
        model = "mock"

        async def complete(self, messages, *, max_tokens=700, temperature=0.2):
            del max_tokens, temperature
            return LLMReply(
                content=("*** Begin Patch\n*** Add File: evil/out.ts\n+bad\n*** End Patch\n"),
                reasoning="",
                completion_tokens=4,
                finish_reason="stop",
                wall_s=0.0,
            )

        async def aclose(self) -> None:
            return None

    from experiments.greenhouse import strategies as gs

    tasks, _wall_s, _m = asyncio.run(
        gs._run_naive_parallel_applied(
            lock,
            {},
            lambda: OobEnvelopeLLM(),
            checkout_path=repo,
            prompts_by_task=None,
            cap_parallelism=None,
        )
    )
    et = tasks[0]
    assert "evil/out.ts" in et.actual_changed_files
    assert "evil/out.ts" in et.out_of_bounds_files
    assert et.status == "completed_unsafe"


def test_single_agent_applied_splits_per_task() -> None:
    lock = AgentLock.model_validate(
        {
            "version": "1.0",
            "generated_at": datetime.now(UTC),
            "repo": {"root": "/tmp", "languages": ["ts"]},
            "tasks": [
                {
                    "id": "t1",
                    "prompt": "p1",
                    "predicted_writes": [{"path": "src/a.ts", "confidence": 0.9, "reason": "r"}],
                    "allowed_paths": ["src/a.ts"],
                    "depends_on": [],
                    "parallel_group": 1,
                    "rationale": None,
                },
                {
                    "id": "t2",
                    "prompt": "p2",
                    "predicted_writes": [{"path": "src/b.ts", "confidence": 0.9, "reason": "r"}],
                    "allowed_paths": ["src/b.ts"],
                    "depends_on": [],
                    "parallel_group": 1,
                    "rationale": None,
                },
            ],
            "execution_plan": {
                "groups": [{"id": 1, "tasks": ["t1", "t2"], "type": "parallel", "waits_for": []}]
            },
            "conflicts_detected": [],
        }
    )
    raw = (
        "Task id: t1\n"
        "*** Begin Patch\n*** Update File: src/a.ts\n@@\n+// t1\n*** End Patch\n\n"
        "Task id: t2\n"
        "*** Begin Patch\n*** Update File: src/b.ts\n@@\n+// t2\n*** End Patch\n"
    )
    by_task = _parse_single_agent_applied_envelopes(raw, lock)
    p1 = _proposals_for_task_envelope_blob(by_task.get("t1", ""))
    p2 = _proposals_for_task_envelope_blob(by_task.get("t2", ""))
    assert len(p1) == 1 and p1[0].file == "src/a.ts"
    assert len(p2) == 1 and p2[0].file == "src/b.ts"


# ---------------------------------------------------------------------------
# honest-completion-metrics: PATCH_NA / typecheck status transitions.
# ---------------------------------------------------------------------------


def _planned_proposal_with_envelope() -> Proposal:
    """A scope-allowed proposal carrying a non-empty apply_patch envelope."""
    env = "*** Begin Patch\n*** Update File: src/in_scope.ts\n@@\n+x\n*** End Patch\n"
    return Proposal(
        file="src/in_scope.ts",
        description="x",
        allowed=True,
        reason=None,
        scope_status="allowed",
        envelope=env,
    )


def _tc_outcome(*, ran: bool, exit_code: int | None, diagnostics: int | None = None):
    from acg.typecheck import TypecheckOutcome

    return TypecheckOutcome(
        ran=ran,
        exit_code=exit_code,
        diagnostic_count=diagnostics,
        wall_seconds=0.1 if ran else None,
        skip_reason=None if ran else "NPX_MISSING",
    )


def test_applied_diff_marks_failed_patch_na_when_outcome_says_so() -> None:
    """A patch_na outcome must short-circuit status to failed/PATCH_NA and
    populate ``patch_na_reason`` plus ``metrics.patch_applies = False``."""
    task = _planned_task()
    worker = _worker_result(proposals=[_planned_proposal_with_envelope()])
    outcome = TaskApplyOutcome(
        changed_files=[],
        patch_na=True,
        patch_na_reason="apply_patch error: invalid hunk",
    )
    eval_task = _proposals_to_planned_applied_eval_task(
        worker,
        task,
        started_at="2026-05-11T22:20:23Z",
        finished_at="2026-05-11T22:20:59Z",
        prompt=None,
        task_outcome=outcome,
    )
    assert eval_task.status == "failed"
    assert eval_task.failure_reason == "PATCH_NA"
    assert eval_task.patch_na_reason == "apply_patch error: invalid hunk"
    assert eval_task.metrics.patch_applies is False


def test_applied_diff_marks_failed_typecheck_on_nonzero_exit() -> None:
    """When the patch lands but tsc exits non-zero, status is failed/FAILED_TYPECHECK."""
    task = _planned_task()
    worker = _worker_result(proposals=[_planned_proposal_with_envelope()])
    outcome = TaskApplyOutcome(
        changed_files=["src/in_scope.ts"],
        patch_na=False,
        patch_na_reason=None,
        typecheck=_tc_outcome(ran=True, exit_code=1, diagnostics=3),
    )
    eval_task = _proposals_to_planned_applied_eval_task(
        worker,
        task,
        started_at="2026-05-11T22:20:23Z",
        finished_at="2026-05-11T22:20:59Z",
        prompt=None,
        task_outcome=outcome,
    )
    assert eval_task.status == "failed"
    assert eval_task.failure_reason == "FAILED_TYPECHECK"
    assert eval_task.metrics.patch_applies is True
    assert eval_task.metrics.typecheck_ran is True
    assert eval_task.metrics.typecheck_exit_code == 1
    assert eval_task.metrics.typecheck_diagnostic_count == 3


def test_applied_diff_marks_completed_unverified_when_typecheck_skipped() -> None:
    """If tsc could not run (e.g. NPX_MISSING) and the patch landed, the
    status is the new ``completed_unverified`` tag, not ``completed``."""
    task = _planned_task()
    worker = _worker_result(proposals=[_planned_proposal_with_envelope()])
    outcome = TaskApplyOutcome(
        changed_files=["src/in_scope.ts"],
        patch_na=False,
        patch_na_reason=None,
        typecheck=_tc_outcome(ran=False, exit_code=None),
    )
    eval_task = _proposals_to_planned_applied_eval_task(
        worker,
        task,
        started_at="2026-05-11T22:20:23Z",
        finished_at="2026-05-11T22:20:59Z",
        prompt=None,
        task_outcome=outcome,
    )
    assert eval_task.status == "completed_unverified"
    assert eval_task.metrics.patch_applies is True
    assert eval_task.metrics.typecheck_ran is False


def test_applied_diff_marks_completed_when_typecheck_passes() -> None:
    """Patch lands AND tsc exit code 0 ⇒ status is the strong ``completed``."""
    task = _planned_task()
    worker = _worker_result(proposals=[_planned_proposal_with_envelope()])
    outcome = TaskApplyOutcome(
        changed_files=["src/in_scope.ts"],
        patch_na=False,
        patch_na_reason=None,
        typecheck=_tc_outcome(ran=True, exit_code=0, diagnostics=0),
    )
    eval_task = _proposals_to_planned_applied_eval_task(
        worker,
        task,
        started_at="2026-05-11T22:20:23Z",
        finished_at="2026-05-11T22:20:59Z",
        prompt=None,
        task_outcome=outcome,
    )
    assert eval_task.status == "completed"
    assert eval_task.metrics.patch_applies is True
    assert eval_task.metrics.typecheck_ran is True
    assert eval_task.metrics.typecheck_exit_code == 0


def test_naive_parallel_blind_applied_uses_blind_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The blind applied dispatch must call ``run_worker`` with
    ``include_lockfile_hints=False`` so the prompt does not enumerate
    predicted_writes / candidate_context."""
    repo = tmp_path / "checkout"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True, capture_output=True)
    (repo / "app").mkdir()
    (repo / "app" / "x.ts").write_text("// x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@e",
            "commit",
            "-m",
            "init",
        ],
        check=True,
        capture_output=True,
    )
    base_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    lock = AgentLock.model_validate(
        {
            "version": "1.0",
            "generated_at": datetime.now(UTC),
            "repo": {"root": str(repo), "commit": base_sha, "languages": ["ts"]},
            "tasks": [
                {
                    "id": "task_blind",
                    "prompt": "blind",
                    "predicted_writes": [{"path": "app/x.ts", "confidence": 0.9, "reason": "r"}],
                    "allowed_paths": ["app/**"],
                    "depends_on": [],
                    "parallel_group": 1,
                    "rationale": None,
                }
            ],
            "execution_plan": {
                "groups": [{"id": 1, "tasks": ["task_blind"], "type": "parallel", "waits_for": []}]
            },
            "conflicts_detected": [],
        }
    )

    captured: dict[str, object] = {}

    async def _fake_run_worker(*args, **kwargs):
        captured["include_lockfile_hints"] = kwargs.get("include_lockfile_hints")
        captured["task_id"] = args[0].id
        return WorkerResult(
            task_id=args[0].id,
            group_id=0,
            url="mock://",
            model="mock",
            wall_s=0.0,
            completion_tokens=0,
            finish_reason="stop",
            raw_content="",
            proposals=[],
            allowed_count=0,
            blocked_count=0,
            error=None,
        )

    def _fake_tsc(_checkout):
        from acg.typecheck import TypecheckOutcome

        return TypecheckOutcome(
            ran=False,
            exit_code=None,
            diagnostic_count=None,
            wall_seconds=None,
            skip_reason="NPX_MISSING",
        )

    from experiments.greenhouse import strategies as gs

    monkeypatch.setattr(gs, "run_worker", _fake_run_worker)
    monkeypatch.setattr(gs, "run_tsc_noemit", _fake_tsc)

    class _NullLLM:
        url = "mock://blind"
        model = "mock"

        async def complete(self, *_a, **_k):
            return LLMReply(
                content="",
                reasoning="",
                completion_tokens=0,
                finish_reason="stop",
                wall_s=0.0,
            )

        async def aclose(self) -> None:
            return None

    tasks, _wall_s, _m = asyncio.run(
        gs._run_naive_parallel_blind_applied(
            lock,
            {},
            lambda: _NullLLM(),
            checkout_path=repo,
            prompts_by_task=None,
            cap_parallelism=None,
        )
    )
    assert captured.get("include_lockfile_hints") is False
    assert captured.get("task_id") == "task_blind"
    assert tasks and tasks[0].actual_changed_files_kind == "naive_parallel_blind_applied_diff"
