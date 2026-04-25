# Cognition Integration

Cognition's stated direction is "Devin Manage Devins": a coordinator Devin that fans out work to child Devins. Public docs say the coordinator "resolves conflicts," but they don't describe how. ACG is the pre-flight artifact that closes that loop — a committable plan the coordinator can read **before** dispatching child Devins, declaring which workstreams are write-disjoint (parallelize) and which need to serialize.

## The gap ACG fills

Three pieces of public evidence shaped this design:

1. **CodeCRDT (arXiv:2510.18893, Oct 2025)** explicitly names static disjointness analysis as future work and reports 5–10% semantic conflicts that runtime CRDTs can't resolve. ACG is that future-work paragraph, shipped as a tool.
2. **OpenCode Issue #4278 (Nov 2025)** is real OpenCode users asking for a per-file lock subsystem so multiple agents stop overwriting each other's changes. The issue closed "completed" without an implementation. We supply the missing primitive.
3. **Walden Yan's interview (jxnl.co, Sep 11 2025)** acknowledges the implicit-decision problem in agentic systems: each action carries a decision, and conflicting decisions corrupt downstream work. ACG makes the write-set decision explicit, reviewable, and committable.

(See `docs/CITATIONS.md` for the verbatim quotes once Prajit verifies them.)

## How Devin Manage Devins would consume ACG

```text
human → coordinator Devin
              │
              ▼
   acg compile (MCP tool)
              │
              ▼
       agent_lock.json
              │
   ┌──────────┼──────────┐
   ▼          ▼          ▼
child Devin  child Devin  child Devin
   (oauth)    (settings)  (billing — waits)
```

The coordinator reads the lockfile and:

1. Spawns one child Devin per task in **group 1** (parallel-safe).
2. Awaits their completion.
3. Spawns the children in **group 2** only after group 1 settles.
4. Repeats per group until the DAG is exhausted.
5. For every write a child Devin attempts, calls `acg validate_writes(lock, task_id, path)` (post-hoc on Devin sessions, pre-empted on Cascade) to confirm the write is in the task's `allowed_paths`.

## MCP tool surface (roadmap)

```python
analyze_repo(path: str) -> dict
predict_writes(task: dict, repo_graph: dict) -> list[dict]
compile_lockfile(repo: str, tasks: dict) -> dict
validate_writes(lockfile: dict, task_id: str, attempted_path: str) -> dict
```

These mirror the four CLI commands one-to-one. A FastMCP wrapper that exposes them over stdio is staged for a follow-up release; the substantive logic already lives in `acg/{compiler,predictor,enforce}.py` and is called directly by the CLI today. Devin Manage Devins, Claude Code, Cursor, and OpenCode can all consume the four tools via the same MCP transport once the wrapper lands. An Agentverse submission via `uagents-adapter` MCPServerAdapter is a thin shim on top of that.

## What we explicitly did **not** build (v1)

- **MCP wrapper itself.** Roadmap. The four primitives are stable; only the FastMCP transport binding is missing.
- **Cascade `pre_write_code` runtime hook.** Deferred to a separate stretch plan; v1 ships the validator as a CLI exit-code contract that the hook can `subprocess` straight into.
- **CRDT runtime layer.** CodeCRDT covers character-level merge resolution. We cite it; we do not duplicate it.
- **Live Devin sessions in the demo.** Devin platform availability is too volatile for hackathon timing. The benchmark chart in `docs/benchmark.png` is simulator-derived; if Devin sessions become available before submission, we re-run with real session metadata.

## Cognition rubric mapping

| Rubric column | What ACG delivers |
| --- | --- |
| **Product Value** | Closes a documented gap (Issue #4278), implements named future work (CodeCRDT), addresses a Walden-acknowledged problem. The lockfile is a real reviewable artifact, not a screenshot. |
| **Engineering Quality** | ts-morph + LLM re-rank with seed fallback; networkx DAG with conflict-count ordering; Pydantic v2 schema; 22-test pytest suite; ruff-clean codebase; module-size cap enforced. |
| **Process** | We dogfood ACG on multi-task work via the same `agent_lock.json` we generate for the demo-app; every Tier acceptance gate is run before the next tier starts. |
| **Bonus (Cognition stack)** | Devin Manage Devins is the consumer of our MCP. Windsurf hooks are the runtime enforcer (stretch). DeepWiki / Codemaps are graph fallback inputs. We use the entire stack on its own terms. |

## Devin sessions (placeholder until run)

Once Devin platform availability allows it, paste session links here:

- `acg-bench-demo-app-oauth-naive` — link
- `acg-bench-demo-app-oauth-planned` — link
- `acg-bench-demo-app-billing-naive` — link
- `acg-bench-demo-app-billing-planned` — link
- `acg-bench-demo-app-tests-planned` — link
