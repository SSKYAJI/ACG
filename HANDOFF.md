# ACG Handoff — current state

## What's shipped (merged into local `main` 2026-04-25)

- **Tier 1-6** (compiler / solver / predictor / runtime CLI / viz static / benchmark): see git history.
- **Phase 1: async runtime** (`acg/runtime.py` + `acg run` CLI + `make run-gemma`/`run-mock`)
- **Phase 2: viz live mode** (RAF replay, ALLOWED/BLOCKED badges, orchestrator panel)
- **Track A: test-scaffold predictor seed** (framework convention detection for greenfield test tasks)
- **PR 1 — Track B index scaffolding**: `acg/index/` package — framework / PageRank / BM25 / co-change indexers + fusion aggregator + 3-fixture benchmark (`benchmark/predictor_eval.py`, mean recall@5 ≈ 0.65)
- **PR 2 — Predictor coverage**: env-file / sibling-pattern / multi-entity test seeds + compiler glob-broadening
- **PR 3 — Housekeeping**: GitHub Actions CI (pytest + ruff + tsc + viz build), refreshed README/HANDOFF, diagnostics moved to `scripts/diagnostics/`, `acg validate-lockfile` CLI, schema field-level descriptions
- **Index aggregator wired into the live predictor** (this session): `_index_seed` calls `acg.index.aggregate(top_n=3)` as a 7th seed; solver hardened with SCC-collapse + explicit/heuristic edge separation so test-task heuristic cycles defeat without breaking user-declared cycle detection
- **PR 6 — MCP server wrapper**: `acg.mcp` package + `acg mcp` CLI +
  `docs/MCP_SERVER.md`. Exposes `analyze_repo`, `predict_writes`,
  `compile_lockfile`, `validate_writes` over FastMCP stdio.

## In flight

- **PR 4 — Java graph builder + Greenhouse seed** (Devin): `docs/devin/pr4-java-scanner.md` — ready to launch once `git push origin main` runs and the Greenhouse build spike confirms `mvn clean test` works at commit `174c1c3`.

## How to run the demo

```bash
make install              # one-time
make gemma-ping           # confirms GX10 reachable
make compile-gemma        # generates demo-app/agent_lock.json
make run-gemma            # generates demo-app/.acg/run_trace.json (~30s)
make viz                  # opens http://localhost:5174
```

## Architecture pointers

- `acg/predictor.py` — 7-seed pipeline + LLM rerank. `_index_seed` is the
  bridge into `acg/index/aggregate.py` and is gracefully bypassed when
  `repo_root` is unset or the aggregator raises.
- `acg/index/` — deterministic indexers (framework, PageRank, BM25,
  co-change) with fusion in `aggregate.py`. Capped at `top_n=3` per task
  inside `_index_seed` (see `SEED_INDEX_TOP_N`) to preserve the original
  3-group parallel demo structure.
- `acg/solver.py` — `build_dag(tasks, heuristic_deps=None)` adds edges in
  three layers (conflict-derived → heuristic → explicit user `depends_on`)
  with an SCC-collapse pass between layers 2 and 3. Cycles formed by the
  first two layers serialize deterministically by input-list order;
  cycles formed by explicit user deps still raise `ValueError`.
- `acg/compiler.py` — splits dependency resolution into
  `_explicit_dependencies` (preserved as `Task.depends_on`) and
  `_heuristic_dependencies` (passed separately to `build_dag`).
- `acg/runtime.py` — async orchestrator + worker fan-out
- `acg/enforce.py` — `validate_write()` glob enforcement
- `viz/src/lib/replay.ts` — pure replay state machine
- `schema/` — JSON Schemas for `agent_lock.json` and `run_trace.json`

## Test surface

- 97 tests, all green (`./.venv/bin/python -m pytest tests/ -q`)
- Lint clean (`./.venv/bin/ruff check acg/ tests/ benchmark/`)
- Viz typecheck clean (`cd viz && npx tsc --noEmit`)

## Next priorities

- Land PR 4 (Java scanner + Greenhouse seed)
- Build the Devin-session head-to-head harness (`experiments/greenhouse/headtohead.py`)
- Slides + 90-second pitch
- See `docs/plans/acg-index-rewrite.md` for the deeper indexer roadmap.
