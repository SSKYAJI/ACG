"""Greenhouse head-to-head harness — naive parallel vs ACG-planned.

This is the runtime sibling of ``acg/runtime.py`` for the legacy-Java
Greenhouse experiment. The orchestrator+sub-agent fan-out in
``acg.runtime.run_lockfile`` already powers the demo-app TypeScript run; this
script wraps the *same* runtime around the Greenhouse lockfile produced by
``make compile-greenhouse`` and contrasts it with a deliberately
uncoordinated baseline.

Two strategies, one lockfile, one shared mock LLM:

1. **Naive parallel** — every task fires a worker concurrently with no
   group ordering, no ``waits_for``, and no ``validate_write`` enforcement.
   Cross-task overlaps are recorded but not blocked; this is the cost
   surface ACG claims to remove.
2. **ACG-planned** — calls :func:`acg.runtime.run_lockfile` directly so
   the orchestrator pass, group-by-group serialisation, and validator
   enforcement run exactly the way the live ``acg run`` command does.

The output JSON is shaped so ``acg report`` can chart the two blocks
side-by-side without any custom adapter.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from typing import Any

# Make ``acg`` importable when this file is invoked directly via
# ``python experiments/greenhouse/headtohead.py``. Mirrors the bootstrap in
# ``tests/conftest.py``.
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from acg import runtime as _runtime  # noqa: E402  (import after sys.path tweak)
from acg.repo_graph import load_context_graph  # noqa: E402
from acg.runtime import (  # noqa: E402
    LLMReply,
    MockRuntimeLLM,
    RuntimeConfig,
    RuntimeLLM,
    RuntimeLLMProtocol,
)
from acg.schema import AgentLock, Task  # noqa: E402
from benchmark.runner import (  # noqa: E402
    MANUAL_MERGE_STEPS_PER_OVERLAP,
    NAIVE_BASE_MIN_PER_TASK,
    NAIVE_OVERLAP_PENALTY_MIN,
)

VERSION = "1.0"
WORKER_MAX_TOKENS = 700
SECONDS_PER_MINUTE = 60.0


# ---------------------------------------------------------------------------
# Lockfile-backed mock LLM.
# ---------------------------------------------------------------------------


class _GreenhouseMockLLM(MockRuntimeLLM):
    """Deterministic mock that derives per-task writes from the lockfile.

    The stock :class:`acg.runtime.MockRuntimeLLM` only knows the four
    demo-app task ids (``oauth``, ``billing``, ``settings``, ``tests``).
    For Greenhouse-style lockfiles whose task ids are different, this
    subclass mirrors the worker / orchestrator prompt protocol but answers
    from each task's ``predicted_writes``. This keeps the harness fully
    deterministic and CI-runnable without touching ``acg/runtime.py``.
    """

    def __init__(
        self,
        lock: AgentLock,
        role: str = "worker",
        model: str = "mock-greenhouse",
    ) -> None:
        super().__init__(role=role, model=model)
        self._tasks_by_id: dict[str, Task] = {t.id: t for t in lock.tasks}
        self._dispatch_order: list[int] = [
            g.id for g in sorted(lock.execution_plan.groups, key=lambda g: g.id)
        ]

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = WORKER_MAX_TOKENS,
        temperature: float = 0.2,
    ) -> LLMReply:
        del max_tokens, temperature
        await asyncio.sleep(0)
        user_blob = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
        if self.role == "orchestrator" or "Lockfile summary" in user_blob:
            content = json.dumps(
                {
                    "approved": True,
                    "concerns": [],
                    "dispatch_order": self._dispatch_order,
                },
                indent=2,
            )
            return LLMReply(
                content=content,
                reasoning="",
                completion_tokens=64,
                finish_reason="stop",
                wall_s=0.001,
            )
        for task_id, task in self._tasks_by_id.items():
            if f"Task id: {task_id}" in user_blob:
                writes = [
                    {
                        "file": pw.path,
                        "description": pw.reason or f"predicted write for {task_id}",
                    }
                    for pw in task.predicted_writes
                ]
                return LLMReply(
                    content=json.dumps({"writes": writes}),
                    reasoning="",
                    completion_tokens=32,
                    finish_reason="stop",
                    wall_s=0.001,
                )
        return LLMReply(
            content=json.dumps({"writes": []}),
            reasoning="",
            completion_tokens=8,
            finish_reason="stop",
            wall_s=0.0001,
        )


# ---------------------------------------------------------------------------
# Tiny standalone parser so the naive simulator does not need to import
# ``run_worker`` (which would call ``validate_write`` and break the
# "naive does not enforce" contract).
# ---------------------------------------------------------------------------


def _parse_writes_simple(raw: str) -> list[dict[str, str]]:
    """Best-effort ``{"writes": [...]}`` parser; mirrors :mod:`acg.runtime`'s
    private helper but stays out of its enforcement path."""
    if not raw or not raw.strip():
        return []
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, dict):
        items = payload.get("writes") or payload.get("proposals") or []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []
    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        file_path = item.get("file") or item.get("path")
        if not isinstance(file_path, str) or not file_path.strip():
            continue
        description = item.get("description") or item.get("reason") or ""
        if not isinstance(description, str):
            description = str(description)
        out.append({"file": file_path.strip(), "description": description.strip()})
    return out


def _build_worker_messages(task: Task) -> list[dict[str, str]]:
    """Worker prompt that is structurally compatible with ``run_worker``.

    Kept self-contained so the naive simulator does not depend on private
    helpers in :mod:`acg.runtime`.
    """
    system = (
        "You are a coding agent assigned a single task. Output ONLY a JSON "
        'object with key "writes": an array of objects with keys "file" '
        '(repository-relative path) and "description" (one short sentence).'
    )
    user = f"Task id: {task.id}\nTask: {task.prompt}\n"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# Naive simulator.
# ---------------------------------------------------------------------------


async def _naive_propose(
    task: Task, sub_llm: RuntimeLLMProtocol
) -> tuple[str, list[dict[str, str]]]:
    """Ask the sub-agent for proposed writes; do NOT validate."""
    reply = await sub_llm.complete(_build_worker_messages(task), max_tokens=WORKER_MAX_TOKENS)
    return task.id, _parse_writes_simple(reply.content)


async def simulate_naive(
    lock: AgentLock,
    repo_graph: dict[str, Any],
    sub_llm: RuntimeLLMProtocol,
) -> dict[str, Any]:
    """Run every task's worker concurrently with no coordination.

    Returns a metric dict shaped to match :mod:`acg.report`'s expected keys
    (overlapping_writes, blocked_bad_writes, manual_merge_steps,
    tests_passing_first_run, wall_time_minutes, acu_consumed) plus the
    raw proposal list so reviewers can audit overlap calls.
    """
    del repo_graph  # accepted for symmetry with the planned path; unused.

    coroutines = [_naive_propose(task, sub_llm) for task in lock.tasks]
    results = await asyncio.gather(*coroutines)

    paths_per_task: dict[str, set[str]] = {tid: set() for tid, _ in results}
    descriptions: dict[tuple[str, str], str] = {}
    for tid, writes in results:
        for w in writes:
            paths_per_task[tid].add(w["file"])
            descriptions.setdefault((tid, w["file"]), w.get("description", ""))

    file_owners: dict[str, list[str]] = defaultdict(list)
    for tid, paths in paths_per_task.items():
        for path in paths:
            file_owners[path].append(tid)

    overlapping_writes = sum(len(owners) for owners in file_owners.values() if len(owners) > 1)
    overlap_pairs = sum(
        1 for a, b in combinations(paths_per_task, 2) if paths_per_task[a] & paths_per_task[b]
    )

    proposals_out: list[dict[str, Any]] = []
    for tid, _ in results:
        for path in sorted(paths_per_task[tid]):
            owners = sorted(file_owners[path])
            other_owners = [o for o in owners if o != tid]
            overlapping = len(owners) > 1
            entry: dict[str, Any] = {
                "task_id": tid,
                "file": path,
                "allowed": not overlapping,
            }
            if overlapping:
                entry["reason"] = "naive overlap with " + ", ".join(other_owners)
            else:
                entry["reason"] = None
            proposals_out.append(entry)

    wall_time_minutes = (
        NAIVE_BASE_MIN_PER_TASK * len(lock.tasks) + NAIVE_OVERLAP_PENALTY_MIN * overlap_pairs
    )

    return {
        "tasks": len(lock.tasks),
        "overlapping_writes": overlapping_writes,
        "overlap_pairs": overlap_pairs,
        "blocked_bad_writes": 0,
        "manual_merge_steps": MANUAL_MERGE_STEPS_PER_OVERLAP * overlap_pairs,
        "tests_passing_first_run": overlapping_writes == 0,
        "wall_time_minutes": wall_time_minutes,
        "acu_consumed": None,
        "proposals": proposals_out,
    }


# ---------------------------------------------------------------------------
# Planned simulator (delegates to ``acg.runtime.run_lockfile``).
# ---------------------------------------------------------------------------


def _planned_metrics_from_run(result: Any, lock: AgentLock) -> dict[str, Any]:
    """Derive the planned-mode metric block from a :class:`RunResult`."""
    file_owners_allowed: dict[str, set[str]] = defaultdict(set)
    blocked_bad_writes = 0
    for worker in result.workers:
        blocked_bad_writes += worker.blocked_count
        for proposal in worker.proposals:
            if proposal.allowed:
                file_owners_allowed[proposal.file].add(worker.task_id)

    overlapping_writes = sum(
        len(owners) for owners in file_owners_allowed.values() if len(owners) > 1
    )
    # Pairwise across allowed-write owners (consistent with naive metric).
    allowed_paths_per_task: dict[str, set[str]] = defaultdict(set)
    for path, owners in file_owners_allowed.items():
        for owner in owners:
            allowed_paths_per_task[owner].add(path)
    overlap_pairs = sum(
        1
        for a, b in combinations(allowed_paths_per_task, 2)
        if allowed_paths_per_task[a] & allowed_paths_per_task[b]
    )

    groups_executed = [
        {
            "id": g.id,
            "type": g.type,
            "wall_s": round(g.wall_s, 4),
            "tasks": list(g.worker_ids),
        }
        for g in result.groups_executed
    ]

    wall_time_minutes = round(result.total_wall_s / SECONDS_PER_MINUTE, 1)

    return {
        "tasks": len(lock.tasks),
        "overlapping_writes": overlapping_writes,
        "overlap_pairs": overlap_pairs,
        "blocked_bad_writes": blocked_bad_writes,
        "manual_merge_steps": 0,
        "tests_passing_first_run": overlapping_writes <= 1,
        "wall_time_minutes": wall_time_minutes,
        "acu_consumed": None,
        "groups_executed": groups_executed,
    }


async def simulate_planned(
    lock: AgentLock,
    repo_graph: dict[str, Any],
    orch_llm: RuntimeLLMProtocol,
    sub_llm: RuntimeLLMProtocol,
    *,
    lockfile_path: str,
) -> dict[str, Any]:
    """Run the lockfile through ``acg.runtime.run_lockfile`` and reduce to metrics."""
    # Look up ``run_lockfile`` via the module so ``monkeypatch.setattr`` on
    # ``acg.runtime.run_lockfile`` is observed.
    result = await _runtime.run_lockfile(
        lock=lock,
        repo_graph=repo_graph,
        orch=orch_llm,
        sub=sub_llm,
        lockfile_path=lockfile_path,
    )
    return _planned_metrics_from_run(result, lock)


# ---------------------------------------------------------------------------
# CLI entrypoint.
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare naive parallel agents vs ACG-planned execution against "
            "the same Greenhouse lockfile. Writes a deterministic JSON "
            "metrics file consumable by ``acg report``."
        ),
    )
    parser.add_argument(
        "--lock",
        type=Path,
        required=True,
        help="Path to the Greenhouse agent_lock.json (built by `make compile-greenhouse`).",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        required=True,
        help="Path to the Greenhouse checkout directory.",
    )
    parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Destination JSON file for the combined metrics.",
    )
    parser.add_argument(
        "--mode",
        choices=("both", "naive", "planned"),
        default="both",
        help="Which strategy block(s) to emit. Defaults to both.",
    )
    parser.add_argument(
        "--mock",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Use the deterministic mock LLM (default). "
            "Pass --no-mock to drive the live runtime via RuntimeConfig.from_env()."
        ),
    )
    return parser.parse_args(argv)


def _build_llms(lock: AgentLock, *, mock: bool) -> tuple[RuntimeLLMProtocol, RuntimeLLMProtocol]:
    """Construct (orchestrator, sub-agent) clients."""
    if mock:
        orch = _GreenhouseMockLLM(lock, role="orchestrator")
        sub = _GreenhouseMockLLM(lock, role="worker")
        return orch, sub
    cfg = RuntimeConfig.from_env()
    orch = RuntimeLLM(
        cfg.orch_url,
        model=cfg.orch_model,
        api_key=cfg.orch_api_key,
        timeout=cfg.request_timeout_s,
    )
    sub = RuntimeLLM(
        cfg.sub_url,
        model=cfg.sub_model,
        api_key=cfg.sub_api_key,
        timeout=cfg.request_timeout_s,
    )
    return orch, sub


async def _aclose_all(llms: Iterable[RuntimeLLMProtocol]) -> None:
    for llm in llms:
        try:
            await llm.aclose()
        except Exception:  # noqa: BLE001 — best-effort cleanup
            pass


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    lock_path: Path = args.lock
    repo_path: Path = args.repo
    if not lock_path.exists():
        raise FileNotFoundError(
            f"--lock not found: {lock_path}. Run `make compile-greenhouse` first."
        )
    if not repo_path.exists():
        raise FileNotFoundError(
            f"--repo not found: {repo_path}. Run `make setup-greenhouse` first; "
            "the harness refuses to fabricate the checkout."
        )

    lock = AgentLock.model_validate_json(lock_path.read_text())
    repo_graph = load_context_graph(repo_path)

    orch_llm, sub_llm = _build_llms(lock, mock=args.mock)

    out: dict[str, Any] = {
        "version": VERSION,
        "generated_at": _now_iso(),
        "lockfile": str(lock_path),
        "repo": str(repo_path),
        "mode": args.mode,
    }
    try:
        if args.mode in ("both", "naive"):
            out["naive"] = await simulate_naive(lock, repo_graph, sub_llm)
        if args.mode in ("both", "planned"):
            out["planned"] = await simulate_planned(
                lock,
                repo_graph,
                orch_llm,
                sub_llm,
                lockfile_path=str(lock_path),
            )
    finally:
        await _aclose_all([orch_llm, sub_llm])

    return out


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    started = time.perf_counter()
    payload = asyncio.run(_run(args))
    elapsed = time.perf_counter() - started

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(
        f"[headtohead] mode={args.mode} mock={args.mock} elapsed={elapsed:.3f}s -> {args.out}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
