# Architecture

This is the deep-dive an engineering judge reads after the README. It explains how the four primary modules cooperate, what invariants the solver maintains, and what the lockfile schema guarantees.

## Component diagram

```text
┌─────────────────────────────────────────────────────────────────┐
│                            INPUT                                │
│  • repo path (--repo)                                           │
│  • tasks.json (--tasks): natural-language task list             │
└──────────────┬──────────────────────────────────────────────────┘
               │
   ┌───────────▼────────────┐
   │  graph_builder/scan.ts │
   │  • ts-morph Project    │
   │  • imports / exports   │
   │  • symbols index       │
   │  • tsconfig path       │
   │    aliases (~/, @/)    │
   │  • hotspots ≥ 3 imp.   │
   └───────────┬────────────┘
               │
               ▼  context_graph.json
   ┌────────────────────────┐
   │  acg/predictor.py      │
   │  • static seed (regex) │
   │  • symbol seed (graph) │
   │  • topical seed (hint) │
   │  • LLM re-rank (Groq / │
   │    vLLM / mock)        │
   │  • dedup, top-N        │
   └───────────┬────────────┘
               │
               ▼  PredictedWrite[] per task
   ┌────────────────────────┐
   │  acg/compiler.py       │
   │  • allowed_paths globs │
   │  • test-task heuristic │
   │  • assemble Tasks      │
   └───────────┬────────────┘
               │
               ▼
   ┌────────────────────────┐
   │  acg/solver.py         │
   │  • detect_conflicts    │
   │  • build_dag           │
   │  • topological_groups  │
   └───────────┬────────────┘
               │
               ▼  agent_lock.json
   ┌─────────────┬─────────────────┬───────────────────────┐
   ▼             ▼                 ▼                       ▼
 acg/explain  acg/enforce      acg/report          MCP wrapper (roadmap)
  ASCII DAG   validate write  benchmark PNG       Devin / Cascade clients
              (exit 2 = block)
```

## Key invariants

### Solver

- The conflict graph is acyclic by construction. The predecessor of every conflict pair is uniquely determined by `(conflict_count, input_index)`, which is a strict total order, so all edges point in the same direction in the order; no cycles can form.
- Explicit `depends_on` declarations are honoured but cycle-checked: if a user-supplied edge would create a cycle, `build_dag` raises `ValueError("cycle detected: …")` listing the offending nodes.
- Group ids are dense 1..N. `waits_for` for group `id > 1` is the singleton `[id - 1]`; transitively earlier groups are implied. This is what keeps the lockfile readable when the DAG is wide.

### Predictor

- The LLM is optional. With no API key (or `ACG_MOCK_LLM=1`), `LLMClient.from_env()` returns a `MockLLMClient` that pattern-matches on `Task id: <id>` and returns canned predictions for the demo task set; unknown task ids fall back to the seed-only path.
- LLM failures (transport, parse, schema) fall back to seeds rather than aborting compilation. The CLI logs a warning; the lockfile still gets written.
- Predictions are capped at 8 entries per task and sorted by descending confidence so the lockfile stays human-skimmable.

### Enforcement

- `validate_write` matches the candidate path against each pattern in `task.allowed_paths`. `pattern/**` matches the prefix and any descendant; otherwise we fall back to `fnmatch`.
- Exit codes are stable: `0` = allowed, `1` = user error (bad lockfile, missing task), `2` = blocked. These are the codes a Cascade `pre_write_code` hook would consume in the stretch plan.

### Schema

- `agent_lock.json` is validated by both the JSON Schema (`schema/agent_lock.schema.json`) and the Pydantic v2 models in `acg/schema.py`. The example lockfiles in `examples/` round-trip through both.
- The schema's `version` is a `const "1.0"`. Bumping the major version is a breaking change for downstream consumers (Devin, Windsurf, Agentverse).

## Compile sequence (one task)

```text
TaskInput("oauth", prompt="Add Google OAuth ...", hints={touches=[auth, prisma]})
    │
    │ acg.predictor.predict_writes
    ▼
seeds  = [/* regex file mentions: none for oauth */,
          /* symbol seed: getCurrentUser → src/server/auth/config.ts */,
          /* topical: src/server/auth/{config,index}.ts, src/app/api/auth/.../route.ts, prisma/schema.prisma */]
    │
    │ LLMClient.complete(messages=[system, user]) → JSON {writes: [...]}
    ▼
rerank = [(src/server/auth/config.ts, 0.95),
          (prisma/schema.prisma, 0.9),
          (src/app/api/auth/[...nextauth]/route.ts, 0.85)]
    │
    │ merge(seeds, rerank); dedup by path; LLM confidence wins
    ▼
PredictedWrite list (top 8)
    │
    │ acg.compiler._build_allowed_paths
    ▼
allowed_paths = [prisma/schema.prisma,
                 src/app/api/auth/[...nextauth]/**,
                 src/server/auth/**]
```

After every task is processed the same way, the compiler hands the `Task[]` to the solver and the solver returns `Group[]` plus `Conflict[]`, which the compiler stamps onto the final `AgentLock`.

## Why the conflict-count rule (not alphabetical, not input-order)

An earlier draft of the planning doc said "edge i → j alphabetical first." That doesn't reproduce the canonical demo lockfile in `examples/lockfile.dag.example.json`: settings/oauth need to land in **group 1** parallel even though they sort differently than billing.

The rule we ship is "the task with fewer total conflicts runs first; ties break by input-list index." This produces:

- `oauth` (1 conflict) before `billing` (2 conflicts) — `oauth` is lighter.
- `settings` (1 conflict) before `billing` (2 conflicts) — `settings` is lighter.
- `oauth` and `settings` (no overlap) — both at level 0, parallel.
- `tests` (no overlap, but flagged as a test task) — solver layers on a test-task heuristic in the compiler that injects `depends_on = [non-test-tasks]`.

The full justification is in the comment in `acg/solver.py`.

## Failure modes and how we handle them

| Failure | Where caught | Behaviour |
| --- | --- | --- |
| LLM endpoint 5xx / timeout | `acg/llm.py` `LLMClient.complete` | one retry; then `LLMError`; predictor falls back to seeds; CLI logs warning |
| LLM JSON parse error | `acg/predictor.py` `_parse_llm_writes` | caller treats as no re-rank; seeds carry the lockfile |
| Cycle in declared `depends_on` | `acg/solver.py` `build_dag` | `ValueError("cycle detected: …")`; CLI exits non-zero |
| Unknown task id in `depends_on` | `acg/solver.py` `build_dag` | `ValueError(f"task X depends_on unknown task Y")` |
| Invalid `tasks.json` schema | `acg/schema.py` `TasksInput.model_validate_json` | Pydantic raises with field path |
| Missing repo graph | `acg/cli.py` `_load_repo_graph` | warns and continues with empty graph; predictor uses seeds + LLM only |
| Lockfile fails JSON Schema | `tests/test_schema.py` (CI) | unit test fails at PR review time |
| Path validation matcher edge case | `tests/test_enforce.py` | covers nested glob (`app/api/auth/[...nextauth]/route.ts` against `app/api/auth/**`) |

## Module size discipline

Per the megaplan's code-quality requirements, every Python module is under 300 lines. Current sizes:

```text
acg/__init__.py       3
acg/cli.py          ~140
acg/compiler.py     ~120
acg/enforce.py      ~110
acg/explain.py      ~100
acg/llm.py          ~190
acg/predictor.py    ~190
acg/report.py       ~100
acg/schema.py       ~140
acg/solver.py       ~180
```

If any of them grows past 300 in a future iteration, split it before merging.
