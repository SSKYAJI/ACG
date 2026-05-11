"""Tests for mock/local applied-diff-live greenhouse strategy."""

from __future__ import annotations

import asyncio
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from acg.schema import AgentLock
from experiments.greenhouse.eval_schema import EvalRepo, write_eval_run
from experiments.greenhouse.strategies import ACG_PLANNED_APPLIED_STRATEGY, run_strategy


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-b", "main")
    (path / "README.md").write_text("# base\n", encoding="utf-8")
    (path / "app").mkdir(parents=True, exist_ok=True)
    (path / "app" / "x.ts").write_text("// seed\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(
        path,
        "-c",
        "user.name=ACG Test",
        "-c",
        "user.email=acg@example.com",
        "commit",
        "-m",
        "base",
    )
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _minimal_lock(*, repo_root: str, task_id: str, file_path: str) -> AgentLock:
    return AgentLock.model_validate(
        {
            "version": "1.0",
            "generated_at": datetime.now(UTC),
            "repo": {"root": repo_root, "languages": ["ts"]},
            "tasks": [
                {
                    "id": task_id,
                    "prompt": "touch file",
                    "predicted_writes": [
                        {"path": file_path, "confidence": 0.9, "reason": "predicted"},
                    ],
                    "allowed_paths": ["app/**"],
                    "depends_on": [],
                    "parallel_group": 1,
                    "rationale": None,
                }
            ],
            "execution_plan": {
                "groups": [
                    {"id": 1, "tasks": [task_id], "type": "parallel", "waits_for": []},
                ]
            },
            "conflicts_detected": [],
        }
    )


def test_applied_diff_live_writes_files_to_checkout(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    base_sha = _init_repo(repo)
    lock = _minimal_lock(repo_root=str(repo), task_id="task_a", file_path="app/x.ts")
    lock.repo.commit = base_sha
    graph: dict = {}
    run = run_strategy(
        strategy=ACG_PLANNED_APPLIED_STRATEGY,
        backend="mock",
        lock=lock,
        repo_graph=graph,
        lockfile_path="agent_lock.json",
        repo=EvalRepo(url="", commit=base_sha, local_path=str(repo)),
    )
    et = run.tasks[0]
    assert et.status == "completed"
    assert et.actual_changed_files_kind == "applied_diff"
    assert "app/x.ts" in et.actual_changed_files
    branch = "acg-applied/task_a"
    names = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", base_sha, branch],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert "app/x.ts" in names


def test_applied_diff_live_blocked_write_does_not_land_on_disk(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    base_sha = _init_repo(repo)
    lock = _minimal_lock(repo_root=str(repo), task_id="task_b", file_path="app/x.ts")
    lock.repo.commit = base_sha
    graph: dict = {}

    class OobContentLLM:
        url = "stub://oob"
        model = "stub"

        async def complete(self, messages, *, max_tokens=700, temperature=0.2):
            del max_tokens, temperature
            bad = (
                "*** Begin Patch\n"
                "*** Add File: outside/secret.ts\n"
                "+evil\n"
                "*** End Patch\n"
            )
            from acg.runtime import LLMReply

            return LLMReply(
                content=bad,
                reasoning="",
                completion_tokens=4,
                finish_reason="stop",
                wall_s=0.0,
            )

        async def aclose(self) -> None:
            return None

    from experiments.greenhouse import strategies as gs

    def factory():
        return OobContentLLM()

    tasks, wall_s, method = asyncio.run(
        gs._run_acg_planned_applied(
            lock,
            graph,
            factory,
            checkout_path=repo,
            lockfile_path="agent_lock.json",
            scope_repo_graph=True,
            auto_replan=False,
        )
    )
    del wall_s, method
    et = tasks[0]
    assert et.status == "blocked"
    assert et.failure_reason == "BLOCKED_BY_SCOPE"
    assert et.actual_changed_files == []
    assert not (repo / "outside" / "secret.ts").exists()


def test_applied_diff_live_emits_applied_diff_evidence_kind(tmp_path: Path) -> None:
    repo = tmp_path / "checkout"
    base_sha = _init_repo(repo)
    lock = _minimal_lock(repo_root=str(repo), task_id="task_c", file_path="app/x.ts")
    lock.repo.commit = base_sha
    out = tmp_path / "eval_run_acg_planned_applied.json"
    run = run_strategy(
        strategy=ACG_PLANNED_APPLIED_STRATEGY,
        backend="mock",
        lock=lock,
        repo_graph={},
        lockfile_path="agent_lock.json",
        repo=EvalRepo(url="", commit=base_sha, local_path=str(repo)),
    )
    write_eval_run(run, out)
    payload = json.loads(out.read_text())
    assert payload["evidence_kind"] == "applied_diff"
    assert payload["execution_mode"] == "applied_diff_live"
    assert payload["tasks"][0]["actual_changed_files_kind"] == "applied_diff"
