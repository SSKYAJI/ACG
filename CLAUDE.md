# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**Agent Context Graph (ACG)** — a compiler from `tasks.json` (NL task list) + repo scan into **`agent_lock.json`**, a per-task write contract: **`predicted_writes`**, **`candidate_context_paths`**, **`allowed_paths`**, **`file_scopes`**, **`depends_on`**, **`parallel_group`**. Consumers include:

1. **Pre-flight / editors**: Coordinators read the lockfile; `acg validate-write` and `acg validate-diff` enforce boundaries (e.g. Windsurf Cascade hooks).
2. **Runtime**: `acg run` drives orchestrator + worker LLMs (`acg/runtime.py`): workers propose **`apply_patch`** envelopes; the runtime applies patches, runs **`validate_write`** per path, optionally handles **candidate-context / replan** flows, and records events to **`run_trace.json`**.
3. **Viz**: `viz/` replays `run_trace.json` against the static DAG.

Behavioral contracts and field semantics are defined in **`acg/schema.py`**; compilation logic in **`acg/compiler.py`**; runtime loop in **`acg/runtime.py`**; CLI entrypoints and flags in **`acg/cli.py`**.

## Common commands

All Python commands assume the project venv at `.venv/`. Install once with `make install` (creates venv, pip-installs `.[dev]`, npm-installs the `ts-morph` graph builder).

| Task | Command |
| --- | --- |
| Run all Python tests | `./.venv/bin/python -m pytest tests/ -q` |
| Run a single test file | `./.venv/bin/python -m pytest tests/test_solver.py -v` |
| Run a single test | `./.venv/bin/python -m pytest tests/test_solver.py::test_name -v` |
| Skip live-LLM smoke tests | `./.venv/bin/python -m pytest tests/ -m 'not smoke'` |
| Lint | `./.venv/bin/ruff check acg/ tests/ benchmark/` |
| Format check | `./.venv/bin/ruff format --check acg/ tests/ benchmark/` |
| Viz typecheck | `cd viz && npx tsc --noEmit` |
| Viz build | `cd viz && npm run build` |
| Compile demo lockfile (offline) | `make compile` |
| Run end-to-end demo (offline) | `make demo` |
| Run mock runtime + viz | `make run-mock && make viz` |
| Run live LLM runtime | `make compile-gemma && make run-gemma && make viz` |
| Clean working artifacts | `make clean` |

### CLI by surface (`acg/cli.py`)

- **Compile / graph**: `compile` ( `--language`, `--use-cached-graph` / `--rescan-graph`, `--localization-backend`), `plan-tasks`, `init-graph`
- **Validation / introspection**: `explain`, `validate-write`, `validate-diff`, `validate-lockfile`
- **Runtime**: `run` (`--mock`, concurrency / `--perf-trace`, etc.)
- **Eval / charts**: `run-benchmark`, `report`
- **Analysis**: `analyze-runs`
- **MCP**: `mcp --transport stdio` (requires `.[mcp]` extra)

### LLM configuration

The predictor / orchestrator / worker LLMs are configured by env vars (see `.env.example`). At minimum one of:

- `ACG_LLM_URL` + `ACG_LLM_MODEL` + `ACG_LLM_API_KEY` (OpenAI-compatible — Groq, OpenRouter, vLLM…)
- `ACG_MOCK_LLM=1` (offline deterministic mock — required for CI and unit tests that exercise the predictor without network)
- `ACG_ORCH_*` overrides the orchestrator endpoint; `ACG_SUB_*` is aliased from `ACG_LLM_*` if unset.

Compile can pin separate endpoints via **`ACG_COMPILE_*`** (see `LLMClient.from_env_for_compile` in `acg/llm.py`).

Runtime optional behavior includes **`ACG_AUTO_REPLAN`** (candidate-context promotion path—see `RuntimeConfig` in `acg/runtime.py`).

For the live two-server demo, the orchestrator hits port 8081 (thinking) and sub-agents hit 8080 (`--reasoning-budget 0`). Override host/port via `make compile-gemma GEMMA_HOST=… GEMMA_PORT=…`.

## Architecture

### Pipeline (compile path)

```
tasks.json + repo
 │
 ▼  acg/repo_graph.py — multi-language scan + localization merge
 │    • TS/JS: graph_builder/scan.ts (ts-morph)
 │    • Python: in-process AST (scan_python)
 │    • Java: tree-sitter (scan_java)
 │    • Optional SCIP: acg/localization/ + metadata on context graph
 ▼
.acg/context_graph.json  (cached; reused unless --rescan-graph / backend mismatch)
 │
 ▼  acg/predictor.py
 │    • Eight documented baseline seed strategies (module docstring:
 │      static, symbol, topical, test scaffold, env, sibling pattern,
 │      index aggregate, module name) plus merges such as graph expansion,
 │      planner hints, test/source links, auth/package seeds where applicable
 │    • LLM re-rank when configured; deterministic fallback on failure
 │    • Outputs PredictedWrite[] and tiered FileScope[] (must_write /
 │      candidate_context / needs_replan)
 ▼  acg/index/aggregate.py (BM25 / PageRank / co-change / framework / optional SCIP)
 │
 ▼  acg/compiler.py — predicted_writes, candidate_context_paths,
 │                    allowed_paths, promote_candidate_paths (runtime/helper)
 ▼  acg/solver.py — conflict DAG + parallel groups
 │
 ▼
agent_lock.json (Pydantic v2 + JSON Schema validated)
```

### Runtime path

`acg run` reads `agent_lock.json`, executes tasks by **`parallel_group`**, parses worker **`apply_patch`** output, applies patches via **`acg/apply_patch_adapter.py`**, validates with **`acg/enforce.validate_write`**, optionally auto-approves **candidate_context** paths via **`promote_candidate_paths`**, streams trace rows to **`run_trace.json`**. Optional **`perf_trace.json`** via `acg/perf.py`.

### Key invariants (don't break)

- **Solver determinism**: edges ordered by `(conflict_count, input_index)`. Three layers added in order — conflict-derived → heuristic test-task → explicit `depends_on` — with an SCC-collapse pass between layers 2 and 3. Cycles formed by user-declared `depends_on` raise `ValueError`; cycles formed by heuristics serialize by input order. (`acg/solver.py`)
- **Enforce exit codes are stable**: `0` allowed, `1` user error, `2` blocked. Cascade hooks consume this. (`acg/enforce.py`)
- **Schema versioning**: `agent_lock.schema.json` `version` is `const "1.0"`. Bumping the major is a breaking change for Devin / Windsurf / MCP consumers. Both JSON Schema and Pydantic models in `acg/schema.py` must accept the lockfile.
- **LLM failure → seed fallback, never abort**: `acg/predictor.py` and `acg/llm.py` catch transport / JSON-parse / schema errors, log a warning, and fall through to the deterministic seed path. Don't add exceptions that escape compile.
- **Module size**: Prefer keeping new logic in focused modules. **Do not assume a 300-line cap still holds**—`acg/runtime.py`, `acg/predictor.py`, `acg/cli.py`, and `acg/repo_graph.py` are already larger; grow by **splitting new submodules** rather than inflating monoliths further.

### Multi-language graph scanning

`acg compile --language` accepts `typescript` (default, runs `graph_builder/scan.ts` via `tsx`), `javascript`, `python` (in-process AST), `java` (tree-sitter), or `auto`. **`--localization-backend`** is `native` (default), `scip`, or `auto`.

### Experiments layout

- `experiments/greenhouse/` — Java legacy demo; head-to-head harness (`headtohead.py`) with backends `mock`, `local`, `applied-diff`, `devin-manual`, `devin-api`. Results in `RESULTS.md`.
- `experiments/realworld/` — NestJS / OpenRouter blind-evaluation pipeline.
- `experiments/python_fastapi/` — Python-FastAPI mock evaluation.
- `experiments/real_repos/` — upstream OSS repos cloned to `checkout/` at runtime (gitignored); aggregator notes under `aggregate_all.md`.

The greenhouse harness is reused by other experiments (`python -m experiments.greenhouse.headtohead --suite-name …`). Test discovery (`pyproject.toml`) is pinned to `tests/` so upstream `checkout/` test suites don't get collected.

### Cascade integration

`.windsurf/hooks.json` wires `scripts/precheck_write.sh` (pre_write_code) and `postcheck_write.sh` (post_write_code). The pre-hook reads `tool_info.file_path` from stdin JSON, normalises to repo-relative, and invokes `acg validate-write` with `ACG_LOCK` + `ACG_CURRENT_TASK` env vars. Exit code 2 = block. Soft-fails (allows) when env is unset so it never accidentally blocks non-ACG sessions.

### MCP

`acg mcp --transport stdio` exposes `analyze_repo`, `predict_writes`, `compile_lockfile`, `validate_writes` over FastMCP. Implementation: **`acg/mcp/server.py`**. Requires the `mcp` extra: `pip install -e '.[mcp]'`. See `docs/MCP_SERVER.md`.

## Testing gotchas

- The `smoke` pytest marker (`tests/*` with `@pytest.mark.smoke`) hits real LLM endpoints and costs money / requires network. CI runs `-m 'not smoke'`.
- Index / co-change tests require a real git identity — CI sets `user.email` + `user.name` globally before pytest.
- Several tests construct lockfiles by round-tripping `examples/*.example.json` through both Pydantic and JSON Schema; if you change the schema, update both `schema/agent_lock.schema.json` and `acg/schema.py` in the same commit.

## Working with the lockfile schema

When extending the lockfile shape:

1. Update `schema/agent_lock.schema.json` (JSON Schema, source of truth for external consumers).
2. Update `acg/schema.py` Pydantic models to match.
3. Round-trip the canonical fixture `examples/lockfile.dag.example.json` through both — covered by `tests/test_schema.py`.
4. If the change is breaking, bump the schema `version` const and update viz parsing in `viz/src/lib/`.
