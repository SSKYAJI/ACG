"""Regression tests for single_agent apply_patch envelope parsing and scoring."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from acg.runtime import LLMReply
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
from experiments.greenhouse.strategies import (
    UNPARSEABLE_APPLY_PATCH_ENVELOPE,
    _parse_single_agent_applied_envelopes,
    _run_single_agent,
    _writes_from_single_agent_patch_blob,
)


def _minimal_lock(*task_ids: str) -> AgentLock:
    tasks = [
        Task(
            id=tid,
            prompt=f"prompt {tid}",
            predicted_writes=[PredictedWrite(path="starlette/x.py", confidence=0.9, reason="hint")],
            allowed_paths=["starlette/x.py"],
            depends_on=[],
            parallel_group=None,
            rationale=None,
        )
        for tid in task_ids
    ]
    return AgentLock(
        version="1.0",
        generated_at=AgentLock.utcnow(),
        generator=Generator(tool="acg", version="test", model="mock"),
        repo=Repo(
            root="experiments/real_repos/starlette/checkout",
            git_url="https://github.com/encode/starlette",
            commit="2b73aecd8377e0c189943a5f30d3dbab134f6104",
            languages=["python"],
        ),
        tasks=tasks,
        execution_plan=ExecutionPlan(
            groups=[
                Group(
                    id=1,
                    tasks=[t.id for t in tasks],
                    type="parallel",
                    waits_for=[],
                )
            ]
        ),
        conflicts_detected=[
            Conflict(
                files=["starlette/x.py"],
                between_tasks=[t.id for t in tasks],
                resolution="test",
            )
        ]
        if len(tasks) > 1
        else [],
    )


def test_parse_single_agent_applied_envelopes_empty_reply() -> None:
    lock = _minimal_lock("t1")
    assert _parse_single_agent_applied_envelopes("", lock) == {}
    assert _parse_single_agent_applied_envelopes("   \n", lock) == {}


def test_parse_accepts_markdown_bold_task_id_header() -> None:
    lock = _minimal_lock("pr3137-cors")
    raw = (
        "**Task id:** pr3137-cors\n"
        "*** Begin Patch\n"
        "*** Update File: starlette/middleware/cors.py\n"
        "@@\n"
        "+x\n"
        "*** End Patch\n"
    )
    envs = _parse_single_agent_applied_envelopes(raw, lock)
    assert "pr3137-cors" in envs
    assert "*** Begin Patch" in envs["pr3137-cors"]


def test_parse_accepts_outer_patch_fence() -> None:
    lock = _minimal_lock("t1")
    inner = "Task id: t1\n*** Begin Patch\n*** Update File: starlette/x.py\n+a\n*** End Patch\n"
    raw = "```patch\n" + inner + "\n```\n"
    envs = _parse_single_agent_applied_envelopes(raw, lock)
    assert envs.get("t1", "").strip().startswith("*** Begin Patch")


def test_parse_envelope_accepts_unified_diff_paths() -> None:
    lock = _minimal_lock("t1")
    raw_udiff = (
        "Task id: t1\n"
        "*** Begin Patch\n"
        "--- a/foo.py\n"
        "+++ b/foo.py\n"
        "@@ -1,2 +1,3 @@\n"
        " a\n"
        " b\n"
        "+c\n"
        "*** End Patch\n"
    )
    env_udiff = _parse_single_agent_applied_envelopes(raw_udiff, lock)
    writes_udiff = {
        tid: _writes_from_single_agent_patch_blob(blob)
        for tid, blob in env_udiff.items()
    }
    assert writes_udiff == {"t1": [{"file": "foo.py", "description": ""}]}

    raw_legacy = (
        "Task id: t1\n"
        "*** Begin Patch\n"
        "*** Update File: foo.py\n"
        "@@ -1,2 +1,3 @@\n"
        " a\n"
        " b\n"
        "+c\n"
        "*** End Patch\n"
    )
    env_legacy = _parse_single_agent_applied_envelopes(raw_legacy, lock)
    writes_legacy = {
        tid: _writes_from_single_agent_patch_blob(blob)
        for tid, blob in env_legacy.items()
    }
    assert writes_legacy == {"t1": [{"file": "foo.py", "description": ""}]}


class _FixedReplyLLM:
    def __init__(self, content: str) -> None:
        self._content = content

    @property
    def url(self) -> str:
        return "mock://fixed"

    @property
    def model(self) -> str:
        return "mock-fixed"

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> LLMReply:
        del messages, max_tokens, temperature
        return LLMReply(
            content=self._content,
            reasoning="",
            completion_tokens=10,
            finish_reason="stop",
            wall_s=0.0,
        )

    async def aclose(self) -> None:
        return None


def test_run_single_agent_apply_patch_malformed_fails_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ACG_SINGLE_AGENT_APPLY_PATCH", "1")
    lock = _minimal_lock("t1", "t2")

    async def _go() -> None:
        tasks, _, _ = await _run_single_agent(
            lock,
            {"files": [{"path": "starlette/x.py"}]},
            lambda: _FixedReplyLLM("this is not an envelope or json"),
        )
        assert len(tasks) == 2
        for et in tasks:
            assert et.status == "failed"
            assert et.failure_reason == UNPARSEABLE_APPLY_PATCH_ENVELOPE
            assert et.proposal_status == "unparseable"
            assert et.actual_changed_files == []

    asyncio.run(_go())


def test_run_single_agent_apply_patch_empty_reply_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ACG_SINGLE_AGENT_APPLY_PATCH", "1")
    lock = _minimal_lock("t1")
    out = tmp_path / "eval_out"
    out.mkdir()

    async def _go() -> None:
        tasks, _, _ = await _run_single_agent(
            lock,
            {"files": [{"path": "starlette/x.py"}]},
            lambda: _FixedReplyLLM(""),
            eval_dump_dir=out,
        )
        assert len(tasks) == 1
        assert tasks[0].status == "failed"
        assert tasks[0].failure_reason == UNPARSEABLE_APPLY_PATCH_ENVELOPE
        assert tasks[0].proposal_status == "unparseable"
        suite = out / "single_agent_raw" / "suite_reply.txt"
        assert suite.exists()
        assert suite.read_text(encoding="utf-8") == ""
        assert (out / "single_agent_raw" / "t1.txt").exists()

    asyncio.run(_go())


def test_run_single_agent_apply_patch_wellformed_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ACG_SINGLE_AGENT_APPLY_PATCH", "1")
    lock = _minimal_lock("t1")
    raw = "Task id: t1\n*** Begin Patch\n*** Update File: starlette/x.py\n+ok\n*** End Patch\n"
    out = tmp_path / "o"
    out.mkdir()

    async def _go() -> None:
        tasks, _, _ = await _run_single_agent(
            lock,
            {"files": [{"path": "starlette/x.py"}]},
            lambda: _FixedReplyLLM(raw),
            eval_dump_dir=out,
        )
        assert tasks[0].status == "completed"
        assert tasks[0].actual_changed_files == ["starlette/x.py"]
        assert tasks[0].proposal_status == "ok"
        assert tasks[0].artifacts.raw_reply is not None
        assert "Begin Patch" in tasks[0].artifacts.raw_reply
        assert tasks[0].artifacts.log_path == "single_agent_raw/t1.txt"
        assert (
            (out / "single_agent_raw" / "t1.txt")
            .read_text(encoding="utf-8")
            .startswith("*** Begin Patch")
        )

    asyncio.run(_go())
