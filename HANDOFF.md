# ACG Handoff — current state

## What's shipped

- **Tier 1-6** (compiler / solver / predictor / runtime CLI / viz static / benchmark): see git history.
- **Phase 1: async runtime** (`acg/runtime.py` + `acg run` CLI + `make run-gemma`/`run-mock`)
- **Phase 2: viz live mode** (RAF replay, ALLOWED/BLOCKED badges, orchestrator panel)
- **Track A: test-scaffold predictor seed** (framework convention detection for greenfield test tasks)

## What's in flight (open PRs)

- **PR 1 — Track B index scaffolding** (Devin): `acg/index/` package + benchmark
- **PR 2 — Predictor coverage** (Devin): env-file / sibling-pattern / multi-entity seeds + compiler glob-broadening
- **PR 3 — This PR** (Devin): CI + docs

## How to run the demo

```bash
make install              # one-time
make gemma-ping           # confirms GX10 reachable
make compile-gemma        # generates demo-app/agent_lock.json
make run-gemma            # generates demo-app/.acg/run_trace.json (~30s)
make viz                  # opens http://localhost:5174
```

## Architecture pointers

- `acg/predictor.py` — seed pipeline + LLM rerank
- `acg/runtime.py` — async orchestrator + worker fan-out
- `acg/enforce.py` — `validate_write()` glob enforcement
- `viz/src/lib/replay.ts` — pure replay state machine
- `schema/` — JSON Schemas for `agent_lock.json` and `run_trace.json`

## Next priorities

(Pull from `docs/plans/acg-index-rewrite.md` once PR 1 lands.)
