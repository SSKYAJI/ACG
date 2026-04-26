# Cognition Integration

Cognition's stated direction is "Devin Manage Devins": a coordinator Devin that fans out work to child Devins. Public docs say the coordinator "resolves conflicts," but they don't describe how. ACG is the pre-flight artifact that closes that loop — a committable plan the coordinator can read **before** dispatching child Devins, declaring which workstreams are write-disjoint (parallelize) and which need to serialize.

## The gap ACG fills

Three pieces of public evidence shaped this design:

1. **CodeCRDT (arXiv:2510.18893, Oct 2025)** reports that task coupling determines whether parallel agents speed up or slow down, notes that coupling was measured post-hoc, and lists static analysis plus semantic-conflict detection as future work. ACG is a static pre-flight write-contract approach in that direction.
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
5. For every write a child Devin attempts, calls `acg validate_writes(lock, task_id, path)` post-hoc on Devin PR diffs to confirm the write is in the task's `allowed_paths`.

## MCP tool surface (shipped)

```python
analyze_repo(path: str) -> dict
predict_writes(task: dict, repo_graph: dict) -> list[dict]
compile_lockfile(repo: str, tasks: dict) -> dict
validate_writes(lockfile: dict, task_id: str, attempted_path: str) -> dict
```

These mirror the four CLI commands one-to-one. The FastMCP stdio wrapper ships in [`acg/mcp/`](../acg/mcp/) — install with `pip install -e '.[mcp]'` and run `acg mcp`. See [`docs/MCP_SERVER.md`](MCP_SERVER.md) for tool schemas and a Devin worked example. An Agentverse submission via `uagents-adapter` MCPServerAdapter is a thin shim on top of the same surface.

## What we explicitly did **not** build (v1)

- **Provider-native pre-emption for Devin.** Devin sessions are validated post-hoc from PR diffs. The Cascade hook script can pre-empt writes in Windsurf once registered, but Devin itself is not pre-empted at write time.
- **CRDT runtime layer.** CodeCRDT covers character-level merge resolution. We cite it; we do not duplicate it.
- **Large-N benchmark.** We ran live Devin smoke tests for Greenhouse and local/mock tests for the broader harness, but the artifact set is still small-N directional evidence rather than a benchmark paper.

## Cognition rubric mapping

| Rubric column               | What ACG delivers                                                                                                                                                                                                                            |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Product Value**           | Closes a documented gap (Issue #4278), moves toward CodeCRDT's static-analysis and semantic-conflict-detection future-work direction, addresses a Walden-acknowledged problem. The lockfile is a real reviewable artifact, not a screenshot. |
| **Engineering Quality**     | ts-morph + LLM re-rank with seed fallback; networkx DAG with conflict-count ordering; Pydantic v2 schema; 178-test pytest suite; ruff-clean codebase.                                                                                        |
| **Process**                 | We dogfood ACG on multi-task work via the same `agent_lock.json` we generate for the demo-app; every Tier acceptance gate is run before the next tier starts.                                                                                |
| **Bonus (Cognition stack)** | Devin Manage Devins is the consumer of our MCP. Windsurf hooks are a local enforcement path once configured. DeepWiki / Codemaps are graph fallback inputs. We use the stack on its own terms.                                               |

## Devin sessions

Live Greenhouse smoke-test PRs opened by Devin:

- Naive strategy: <https://github.com/SSKYAJI/greenhouse/pull/1>, <https://github.com/SSKYAJI/greenhouse/pull/2>, <https://github.com/SSKYAJI/greenhouse/pull/3>
- ACG-planned strategy: <https://github.com/SSKYAJI/greenhouse/pull/4>, <https://github.com/SSKYAJI/greenhouse/pull/5>, <https://github.com/SSKYAJI/greenhouse/pull/6>

Artifacts: `experiments/greenhouse/runs/eval_run_devin_api_naive_smoke.json`
and `experiments/greenhouse/runs/eval_run_devin_api_acg_smoke.json`.
