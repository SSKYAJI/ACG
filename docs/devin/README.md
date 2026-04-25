# Devin task bundle — Track B + coverage + housekeeping

Three independent PRs, scoped so they will not merge-conflict if launched in
parallel. Each PR has its own prompt file in this directory; copy that file's
body into Devin's UI verbatim.

## Status of the repo as of handoff

- **Phase 1 (runtime, `acg/runtime.py`)** — shipped, 9 tests, `acg run` CLI
  command, `make run-gemma` and `make run-mock` targets.
- **Phase 2 (viz live mode)** — shipped, RAF-driven replay of
  `demo-app/.acg/run_trace.json`, ALLOWED/BLOCKED badges, orchestrator
  reasoning panel, per-task proposal drawer.
- **Track A (test-scaffold seed in `acg/predictor.py`)** — shipped, 9 new
  tests. Demo trace = 10 ALLOWED / 4 BLOCKED.
- **Tests/lint/build:** `pytest -q` → 51 passed; `ruff check acg/ tests/
  benchmark/` → clean; `cd viz && npx tsc --noEmit && npm run build` → clean.

**Before launching Devin:** commit the unstaged Track A changes so Devin
branches from a clean tree:

```bash
git add .gitignore acg/compiler.py acg/predictor.py tests/test_predictor.py \
        demo-app/agent_lock.json demo-app/.acg/run_trace.json \
        demo-app/.acg/context_graph.json
git commit -m "predictor: add deterministic test-scaffold seed (Track A)"
git push
```

## The three PRs

| PR | Prompt file | Branch | Touches | Estimated Devin compute |
|----|-------------|--------|---------|-------------------------|
| 1  | [`pr1-track-b-scaffolding.md`](pr1-track-b-scaffolding.md) | `track-b-index-scaffolding` | new files only (`acg/index/`, `benchmark/predictor_eval.py`, `docs/plans/acg-index-rewrite.md`) | ~1 day |
| 2  | [`pr2-predictor-coverage.md`](pr2-predictor-coverage.md) | `predictor-coverage-extensions` | `acg/predictor.py`, `acg/compiler.py`, `tests/test_predictor.py` | ~½ day |
| 3  | [`pr3-housekeeping.md`](pr3-housekeeping.md) | `housekeeping-ci-docs` | `.github/workflows/`, `HANDOFF.md`, `README.md`, `scripts/diagnostics/`, `schema/*.schema.json` | ~2 hours |

PR 1 and PR 2 both *eventually* want to be wired into `acg/predictor.py`, but
only PR 2 modifies that file directly. PR 1 leaves its aggregator un-wired
behind a public `acg.index.aggregate()` callable so the human author can
integrate it post-demo without merge surprises.

## What Devin must NOT touch (across all three PRs)

- `acg/runtime.py` — frozen; produces the live demo trace
- `acg/cli.py` — frozen except where PR 3 explicitly adds a new command
- `viz/` — frozen
- `demo-app/agent_lock.json` and `demo-app/.acg/run_trace.json` — frozen demo
  artefacts; do not regenerate
- `acg/predictor.py::_test_scaffold_seed` and its helpers — Track A;
  PR 2 may *extend* the file with new seeds but must not alter the existing
  test-scaffold seed's behaviour or break its 9 tests

## Acceptance gates (every PR must pass these)

```bash
# Python
./.venv/bin/python -m pytest tests/ -q       # all existing tests still pass
./.venv/bin/ruff check acg/ tests/ benchmark/

# TypeScript / viz
cd viz && npx tsc --noEmit && npm run build && cd ..
```

Plus PR-specific tests/benchmarks listed in each prompt.

## Launching strategy

- **Sequential safer:** launch PR 3 first (lowest risk), then PR 2 (touches
  predictor), then PR 1 (biggest blast radius) — each gates the next.
- **Parallel faster:** launch all three at once; merge in this order: PR 3 →
  PR 2 → PR 1. Track B (PR 1) is the most complex review, so handle it last.

The recommended call given an 18-hour clock is **parallel**, with a personal
review pass on each PR as it lands.
