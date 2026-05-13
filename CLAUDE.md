# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is

**Agent Context Graph (ACG)** — a compiler from `tasks.json` (NL task list) + repo scan into `agent_lock.json`, a per-task write contract (`predicted_writes`, `allowed_paths`, `depends_on`, `parallel_group`). Two surfaces consume the lockfile:

1. **Pre-flight**: Cascade / Devin / Claude get the lockfile, scope writes to `allowed_paths`. `acg validate-write` and `acg validate-diff` enforce.
2. **Runtime**: `acg run` executes the plan against a live orchestrator + sub-agent LLM pair, validating each proposed write against the contract and recording allow/block events to `run_trace.json`.

The viz (`viz/`) replays `run_trace.json` against the static lockfile DAG.

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

The CLI itself is `acg <subcommand>` — see `acg/cli.py` for the full surface (`compile`, `plan-tasks`, `init-graph`, `explain`, `validate-write`, `validate-diff`, `validate-lockfile`, `run`, `run-benchmark`, `report`, `analyze-runs`, `mcp`).

### LLM configuration

The predictor / orchestrator / worker LLMs are configured by env vars (see `.env.example`). At minimum one of:

- `ACG_LLM_URL` + `ACG_LLM_MODEL` + `ACG_LLM_API_KEY` (OpenAI-compatible — Groq, OpenRouter, vLLM…)
- `ACG_MOCK_LLM=1` (offline deterministic mock — required for CI and unit tests that exercise the predictor without network)
- `ACG_ORCH_*` overrides the orchestrator endpoint; `ACG_SUB_*` is aliased from `ACG_LLM_*` if unset.

For the live two-server demo, the orchestrator hits port 8081 (thinking) and sub-agents hit 8080 (`--reasoning-budget 0`). Override host/port via `make compile-gemma GEMMA_HOST=… GEMMA_PORT=…`.

## Architecture

### Pipeline (compile path)

```
tasks.json + repo
   │
   ▼  graph_builder/scan.ts (TS/JS, ts-morph)
   │  acg/repo_graph.py + scan_java.py + scan_python.py (multi-language dispatcher)
   ▼
.acg/context_graph.json  (cached; reused unless --rescan-graph)
   │
   ▼  acg/predictor.py  (7 seeds: regex/symbol/topical/index/… → LLM rerank)
   ▼  acg/index/aggregate.py (PageRank + BM25 + co-change + framework fusion)
   │
   ▼  acg/compiler.py  (build allowed_paths globs, test-task heuristic)
   ▼  acg/solver.py  (conflict detection → DAG → topological groups)
   │
   ▼
agent_lock.json (Pydantic v2 + JSON Schema validated)
```

### Runtime path

`acg run` (in `acg/runtime.py`) reads `agent_lock.json`, fans tasks out by `parallel_group`. Per worker: builds prompt → calls sub-agent LLM → parses proposed writes → calls `acg.enforce.validate_write` per path. All ALLOWED/BLOCKED events stream to `run_trace.json`. `acg/perf.py` optionally records a `perf_trace.json`.

### Key invariants (don't break)

- **Solver determinism**: edges ordered by `(conflict_count, input_index)`. Three layers added in order — conflict-derived → heuristic test-task → explicit `depends_on` — with an SCC-collapse pass between layers 2 and 3. Cycles formed by user-declared `depends_on` raise `ValueError`; cycles formed by heuristics serialize by input order. (`acg/solver.py`)
- **Enforce exit codes are stable**: `0` allowed, `1` user error, `2` blocked. Cascade hooks consume this. (`acg/enforce.py`)
- **Schema versioning**: `agent_lock.schema.json` `version` is `const "1.0"`. Bumping the major is a breaking change for Devin / Windsurf / MCP consumers. Both JSON Schema and Pydantic models in `acg/schema.py` must accept the lockfile.
- **LLM failure → seed fallback, never abort**: `acg/predictor.py` and `acg/llm.py` catch transport / JSON-parse / schema errors, log a warning, and fall through to the deterministic seed path. Don't add exceptions that escape compile.
- **Module size discipline**: every `acg/*.py` module is kept under 300 lines. If a module grows past that, split before merging.

### Multi-language graph scanning

`acg compile --language` accepts `typescript` (default, runs `graph_builder/scan.ts` via `tsx`), `javascript`, `python` (in-process AST, `acg/repo_graph.py` → `scan_python.py` integration), `java` (in-process tree-sitter scanner, `graph_builder/scan_java.py`), or `auto`. Adding a new language means a new scanner + `predictor.py` heuristics — see `experiments/python_fastapi/` for a worked example.

### Experiments layout

- `experiments/greenhouse/` — Java legacy demo; head-to-head harness (`headtohead.py`) with backends `mock`, `local`, `applied-diff`, `devin-manual`, `devin-api`. Results in `RESULTS.md`.
- `experiments/realworld/` — NestJS / OpenRouter blind-evaluation pipeline.
- `experiments/python_fastapi/` — Python-FastAPI mock evaluation.
- `experiments/real_repos/` — upstream OSS repos cloned to `checkout/` at runtime (gitignored).

The greenhouse harness is reused by other experiments (`python -m experiments.greenhouse.headtohead --suite-name …`). Test discovery (`pyproject.toml`) is pinned to `tests/` so upstream `checkout/` test suites don't get collected.

### Cascade integration

`.windsurf/hooks.json` wires `scripts/precheck_write.sh` (pre_write_code) and `postcheck_write.sh` (post_write_code). The pre-hook reads `tool_info.file_path` from stdin JSON, normalises to repo-relative, and invokes `acg validate-write` with `ACG_LOCK` + `ACG_CURRENT_TASK` env vars. Exit code 2 = block. Soft-fails (allows) when env is unset so it never accidentally blocks non-ACG sessions.

### MCP

`acg mcp --transport stdio` exposes `analyze_repo`, `predict_writes`, `compile_lockfile`, `validate_writes` over FastMCP. Requires the `mcp` extra: `pip install -e '.[mcp]'`. See `docs/MCP_SERVER.md`.

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
