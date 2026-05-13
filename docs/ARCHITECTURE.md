# Architecture

This is the deep-dive an engineering judge reads after the README. It explains how the primary modules cooperate, what invariants the solver maintains, what the lockfile encodes, and how runtime enforcement differs from compilation.

## Component diagram

```text
┌─────────────────────────────────────────────────────────────────┐
│                            INPUT                                │
│  • repo path (--repo)                                           │
│  • tasks.json (--tasks): natural-language task list             │
└──────────────┬──────────────────────────────────────────────────┘
               │
   ┌───────────▼────────────────────────────────────────────────┐
   │  acg/repo_graph.py :: scan_context_graph                  │
   │  • Dispatch by --language (tsx scan, Python AST, Java TS)   │
   │  • --localization-backend native | scip | auto            │
   │  • Merge optional SCIP aggregates (acg/localization/*)      │
   └───────────┬────────────────────────────────────────────────┘
               │
               ▼  context_graph.json
   ┌───────────────────────────────────────────────────────────┐
   │  acg/predictor.py                                         │
   │  • Eight baseline seed strategies (module doc) + merges   │
   │    (graph expansion, planner hints, tests, auth, …)       │
   │  • acg/index/* (BM25, PageRank, co-change, SCIP entity)   │
   │  • LLM re-rank (+ scope review JSON when used)            │
   │  • PredictedWrite[] + FileScope[] tiers                   │
   └───────────┬─────────────────────────────────────────────┘
               │
               ▼
   ┌───────────────────────────────────────────────────────────┐
   │  acg/compiler.py                                          │
   │  • predicted_writes, candidate_context_paths              │
   │  • allowed_paths globs, file_scopes serialization         │
   │  • test-task heuristic • promote_candidate_paths (runtime) │
   └───────────┬─────────────────────────────────────────────┘
               │
               ▼
   ┌───────────────────────────────────────────────────────────┐
   │  acg/solver.py                                            │
   │  • detect_conflicts → build_dag → topological_groups     │
   └───────────┬─────────────────────────────────────────────┘
               │
               ▼  agent_lock.json
   ┌─────────────┬─────────────────┬──────────────────────────┐
   ▼             ▼                 ▼                          ▼
 acg/explain  acg/enforce    acg/report /            acg/mcp/server.py
  summary     validate write  run-benchmark            (shipped FastMCP)
              (exit 2=block)  + acg analyze-runs
               │                 │
               └────────┬────────┘
                        ▼
               acg/runtime.py
               • apply_patch apply + validate_write
               • optional candidate_context / auto_replan
               • run_trace.json (+ optional perf_trace.json)
```

## Key invariants

### Solver

- The conflict graph is acyclic by construction. The predecessor of every conflict pair is uniquely determined by `(conflict_count, input_index)`, which is a strict total order, so all edges point in the same direction in the order; no cycles can form from contention alone.
- Explicit `depends_on` declarations are honoured but cycle-checked: if a user-supplied edge would create a cycle, `build_dag` raises `ValueError("cycle detected: …")` listing the offending nodes.
- Group ids are dense 1..N. `waits_for` for group `id > 1` is the singleton `[id - 1]`; transitively earlier groups are implied. This is what keeps the lockfile readable when the DAG is wide.

### Predictor

- The LLM is optional. With no API key (or `ACG_MOCK_LLM=1`), `LLMClient.from_env()` can return a `MockLLMClient` that supplies canned predictions for known demo tasks; unknown task ids fall back to the seed-only path.
- LLM failures (transport, parse, schema) fall back to seeds rather than aborting compilation. The CLI logs a warning; the lockfile still gets written.
- **`file_scopes`** carry tier and evidence signals; **`predicted_writes`** remain the concise must-touch list for solver contention and human review caps.

### Compiler and scope tiers

- **`allowed_paths`** is the filesystem contract consumed by **`validate_write`**.
- **`candidate_context_paths`** and **`file_scopes`** widen what the runtime may show agents without granting write authority until promotion or explicit replan.
- **`promote_candidate_paths`** (used from runtime when policy allows) rewrites **`predicted_writes` / scopes** after an approved candidate-context edit.

### Enforcement

- `validate_write` matches the candidate path against each pattern in `task.allowed_paths`. Prefix globs ending in `/**` match descendants; otherwise the code falls back to `fnmatch` semantics documented in tests.
- Exit codes are stable: `0` = allowed, `1` = user error (bad lockfile, missing task), `2` = blocked. These are the codes Cascade hooks rely on.

### Schema

- `agent_lock.json` is validated by both the JSON Schema (`schema/agent_lock.schema.json`) and the Pydantic v2 models in `acg/schema.py`. The example lockfiles in `examples/` round-trip through both.
- The schema's `version` is a `const "1.0"`. Bumping the major version is a breaking change for downstream consumers (Devin, Windsurf, MCP clients).

### Runtime vs compile

Compilation produces a **static** contract; execution mutates workspace files via **`apply_patch`**. Unauthorized paths should be rejected before mutation when possible; **candidate_context** paths may trace as **needs_replan** until promoted.

## Maintainability note (module sizes)

Several core modules intentionally bundle related logic (CLI wiring, predictor seeds, runtime loop). Rough current scale (single snapshot—run `wc -l acg/*.py` to refresh):

```text
acg/cli.py         ~786 lines   entrypoints + Typer wiring
acg/repo_graph.py  ~820 lines   scan dispatch + localization merge
acg/runtime.py    ~1684 lines   async execution + patch + enforcement
acg/predictor.py  ~2092 lines   seeds + indexer fusion + scopes
```

New features should **prefer new focused modules or helpers** rather than further growing these files when a clean seam exists.

## Compile sequence (one task)

```text
TaskInput("oauth", prompt="Add Google OAuth ...", hints={touches=[auth, prisma]})
    │
    │ acg.predictor.predict_writes (→ scopes + predicted writes after merge)
    ▼
seeds / index / SCIP-informed hits → rerank → PredictedWrite[] + FileScope[]
    │
    │ acg.compiler paths for contract fields
    ▼
predicted_writes, candidate_context_paths, allowed_paths[]
    │
    │ after all tasks: acg.solver.solve → groups + conflicts on predicted_writes
    ▼
AgentLock.tasks[] + execution_plan
```

Runtime workers then consume **`allowed_paths`**, **`file_scopes`** tier, and **`candidate_context_paths`** depending on prompt construction (`acg/runtime.py`).

## Why the conflict-count rule (not alphabetical, not input-order)

An earlier draft of the planning doc said "edge i → j alphabetical first." That doesn't reproduce the canonical demo lockfile in `examples/lockfile.dag.example.json`: settings/oauth need to land in **group 1** parallel even though they sort differently than billing.

The rule we ship is "the task with fewer total conflicts runs first; ties break by input-list index."

The full justification is in the comments in `acg/solver.py`.

## Failure modes and how we handle them

| Failure | Where caught | Behaviour |
| --- | --- | --- |
| LLM endpoint 5xx / timeout | `acg/llm.py` `LLMClient.complete` | one retry; then `LLMError`; predictor falls back to seeds; CLI logs warning |
| LLM JSON parse error | `acg/predictor.py` `_parse_llm_writes` | caller treats as no re-rank; seeds carry the lockfile |
| Cycle in declared `depends_on` | `acg/solver.py` `build_dag` | `ValueError("cycle detected: …")`; CLI exits non-zero |
| Unknown task id in `depends_on` | `acg/solver.py` `build_dag` | `ValueError(f"task X depends_on unknown task Y")` |
| Invalid `tasks.json` schema | `acg/schema.py` `TasksInput.model_validate_json` | Pydantic raises with field path |
| Missing repo graph | `acg/cli.py` `_load_repo_graph` | compile path rescans until a graph exists; runtime may degrade context |
| Lockfile fails JSON Schema | `tests/test_schema.py` (CI) | unit test fails at PR review time |
| Path validation matcher edge case | `tests/test_enforce.py` | covers nested glob (`app/api/auth/[...nextauth]/route.ts` against `app/api/auth/**`) |
