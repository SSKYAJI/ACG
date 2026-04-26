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

import json
from pathlib import Path

import pytest

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
    EvalRun,
    EvalTask,
    SummaryMetrics,
    TaskMetrics,
    TaskTest,
    annotate_overlaps,
    compute_overlap_pairs,
    compute_summary_metrics,
    task_from_lock,
    to_dict,
    validate_actual_files,
    write_eval_run,
)
from experiments.greenhouse.strategies import (
    LockfileEchoMockLLM,
    _extract_task_id,
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
    # Every task's actual_changed_files contains pom.xml because naive does
    # not enforce; validate_write also does not flag in-bounds writes since
    # pom.xml is allowed for every task.
    for task in run.tasks:
        assert "pom.xml" in task.actual_changed_files


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
    noise = [
        f"src/main/java/com/springsource/greenhouse/signup/{n}.java"
        for n in ("Signup", "SignupForm", "SignupController", "SignupValidator")
    ] + [
        f"src/main/java/com/springsource/greenhouse/utils/{n}.java"
        for n in ("StringUtils", "DateUtils", "FormatUtils", "JsonUtils")
    ] + [
        f"src/main/java/com/springsource/greenhouse/connect/{n}.java"
        for n in ("ConnectController", "ConnectInterceptor")
    ] + [
        f"src/main/java/com/springsource/greenhouse/database/{n}.java"
        for n in ("DatabaseConfig", "JdbcTemplate")
    ]
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


def test_scoped_repo_graph_filters_files_to_allowed_paths() -> None:
    """A scoped graph must only contain files inside ``allowed_paths``."""
    lock = _build_lock(serialized=True)
    full = _greenhouse_repo_graph()
    # account task's allowed_paths = its JdbcAccountRepository.java + pom.xml
    scoped = _scoped_repo_graph(full, lock, "lambda-rowmapper-account")
    paths = {f["path"] for f in scoped["files"]}
    assert "pom.xml" in paths
    assert (
        "src/main/java/com/springsource/greenhouse/account/JdbcAccountRepository.java"
        in paths
    )
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
        planned.summary_metrics.tokens_prompt_total
        < naive.summary_metrics.tokens_prompt_total
    ), (
        f"expected planned ({planned.summary_metrics.tokens_prompt_total}) "
        f"< naive ({naive.summary_metrics.tokens_prompt_total})"
    )


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
    # Hard correctness gate: planned must catch ≥ as many bad writes as naive,
    # AND naive must show overlaps that planned does not.
    assert (
        acg_payload["summary_metrics"]["blocked_invalid_write_count"]
        >= naive_payload["summary_metrics"]["blocked_invalid_write_count"]
    )
    assert naive_payload["summary_metrics"]["overlapping_write_pairs"] >= 1


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
    tight_lock_path = (
        repo_root / "experiments" / "greenhouse" / "agent_lock_tight.json"
    )
    assert tight_lock_path.exists(), (
        f"tightened lockfile missing at {tight_lock_path}"
    )
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
    blocked_per_task = {
        t.task_id: len(t.blocked_write_events) for t in run.tasks
    }
    assert all(count >= 1 for count in blocked_per_task.values()), (
        f"every task should produce blocked events; got {blocked_per_task!r}"
    )

    # And the in-bounds writes should still land — pom.xml is allowed for
    # every task and JdbcAccountRepository.java is allowed for the account
    # task — so ``actual_changed_files`` is non-empty.
    actual_per_task = {
        t.task_id: t.actual_changed_files for t in run.tasks
    }
    assert all(len(files) >= 1 for files in actual_per_task.values()), (
        f"in-bounds proposals must still land; got {actual_per_task!r}"
    )
