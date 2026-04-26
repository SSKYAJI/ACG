# Agent Context Graph (ACG)

> **It's `package-lock.json` for parallel coding agents.**

Parallel coding agents are powerful, but they collide on shared files. Devin now manages teams of Devins, but public docs only say the coordinator resolves conflicts after the fact. **ACG moves that work before execution.** It scans the repo, predicts each task's write-set, emits a committable `agent_lock.json`, and validates writes against that contract. In local/Cascade-style execution, the validator can block illegal writes before they corrupt the diff; for black-box Devin, the same contract is injected up front and audited post-hoc from PR diffs.

LA Hacks 2026 — Cognition track (primary) · ASUS track (secondary).

## Live execution mode

ACG ships with a runtime (`acg/runtime.py`) that executes a lockfile
against two `llama-server` instances:

- **Orchestrator** (port 8081) — thinks aloud about the dispatch plan
- **Sub-agents** (port 8080) — propose write-sets per task, no thinking

Each worker's proposed writes are validated against its task's
`allowed_paths` via `validate_write()`. Both ALLOWED and BLOCKED proposals
are recorded to `demo-app/.acg/run_trace.json`. The viz replays the trace
in real time:

```bash
make compile-gemma   # build the lockfile against live Gemma
make run-gemma       # execute it; ~30s, writes run_trace.json
make viz             # open the live-replay visualizer
```

Offline / CI mode uses a deterministic mock:

```bash
make run-mock && make viz
```

See `viz/README.md` for the visualizer architecture and `acg/runtime.py`
for the runtime's prompt construction and validation pipeline.

Full multi-codebase results, including live Devin PRs and the Brocoders
NestJS microservice benchmark, are in
[`experiments/greenhouse/RESULTS.md`](experiments/greenhouse/RESULTS.md).

## Demo

![Agent coordination tax — naive vs ACG-planned](docs/benchmark.png)

Same 4 tasks (`oauth`, `billing`, `settings`, `tests`) on the same `demo-app`, two strategies:

| Metric               | Naive parallel | ACG-planned |
| -------------------- | -------------- | ----------- |
| Overlapping writes   | 4              | 1           |
| Blocked bad writes   | 0              | 2           |
| Manual merge steps   | 4              | 0           |
| Tests pass first run | no             | yes         |
| Wall time (min)      | 20             | 13          |

The `oauth` and `settings` tasks are write-disjoint — ACG runs them in parallel.
`billing` overlaps with both (`prisma/schema.prisma` with `oauth`, `src/components/Sidebar.tsx` with `settings`) — ACG serializes it after group 1.
`tests` waits for everything because tests should target the final state.

## 60-second quickstart

```bash
git clone <this repo>
cd cognition

make install           # creates .venv, pip installs ACG, npm installs ts-morph
cp .env.example .env   # then put your Groq key in ACG_LLM_API_KEY (or leave blank for offline mock)

make demo              # scan + compile + benchmark + chart in one shot
```

`make demo` produces:

- `demo-app/.acg/context_graph.json` — ts-morph repo graph (16 files, 3 hotspots)
- `demo-app/agent_lock.json` — committable plan (4 tasks, 3 groups, 2 conflicts)
- `.acg/run_naive.json` + `.acg/run_acg.json` — benchmark metrics
- `docs/benchmark.png` — the chart shown above

To watch the enforcement layer block an out-of-bounds write:

```bash
./.venv/bin/acg validate-write \
  --lock demo-app/agent_lock.json \
  --task settings \
  --path src/server/auth/config.ts
# BLOCKED: path 'src/server/auth/config.ts' is outside task 'settings''s allowed_paths
# exit code 2
```

## What is `agent_lock.json`?

A committable, human-reviewable, schema-validated artifact that declares for each task:

- `prompt` — the natural-language task
- `predicted_writes[]` — every file the task is expected to modify, with confidence and reason
- `allowed_paths[]` — globs the task is permitted to write
- `depends_on[]` — explicit upstream tasks
- `parallel_group` — which DAG level this task belongs to

…plus an `execution_plan.groups[]` array that orders tasks into parallel-safe and serial groups, and a `conflicts_detected[]` array that documents every cross-task overlap the planner found.

See `examples/lockfile.dag.example.json` for the full demo lockfile and `schema/agent_lock.schema.json` for the JSON Schema.

## CLI surface

```text
acg compile          --repo PATH --tasks FILE --out FILE
acg explain          --lock FILE
acg validate-write   --lock FILE --task ID --path PATH
acg report           --naive FILE --planned FILE --out FILE
acg run-benchmark    --mode {naive,planned} --repo PATH --tasks FILE --out FILE
acg mcp              [--transport stdio]    # MCP server (requires .[mcp] extra)
```

The same four primitives are exposed as MCP tools — see [`docs/MCP_SERVER.md`](docs/MCP_SERVER.md). Compatible with Devin Manage Devins, Claude Code, Cursor, and OpenCode.

## Architecture

```text
tasks.json + repo  ──► graph_builder/scan.ts (ts-morph)  ──► context_graph.json
                                                              │
                                                              ▼
                       acg.predictor (seeds + LLM re-rank)  ──► PredictedWrite[]
                                                              │
                                                              ▼
                       acg.solver (conflict-count DAG)      ──► execution_plan
                                                              │
                                                              ▼
                                                          agent_lock.json
                                                              │
                                ┌─────────────────────────────┴─────────────────────────────┐
                                ▼                             ▼                             ▼
                       acg.enforce (validator)        acg.report (chart)         MCP wrapper (roadmap)
```

Long form in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Sponsor narratives

- **Cognition** — Devin Manage Devins coordinates child Devins but doesn't publish how it resolves conflicts. ACG is the pre-flight artifact the coordinator can consume before fanning out. See [`docs/COGNITION_INTEGRATION.md`](docs/COGNITION_INTEGRATION.md).
- **ASUS GX10** — Local-first AI infrastructure for compliance-heavy enterprises that cannot ship code to cloud LLMs. The same OpenAI-compatible client that talks to Groq talks to vLLM on the GX10. See [`docs/ASUS_DEPLOYMENT.md`](docs/ASUS_DEPLOYMENT.md).

## Cascade integration

ACG includes a Windsurf `pre_write_code` hook script that can block
out-of-bounds Cascade writes before the diff lands once `.windsurf/hooks.json`
is configured. See [`docs/CASCADE_INTEGRATION.md`](docs/CASCADE_INTEGRATION.md).

## Honesty box (non-negotiable)

1. **Small-N single-trial evidence.** Directional evidence only. Not a benchmark paper.
2. **File-level disjointness only.** Semantic drift across disjoint files is out of scope; import/export risk analysis is future work.
3. **Java, JavaScript / TypeScript, and Python coverage today.** Other languages would need their own parsers and write-set heuristics.
4. **Cascade hook enforcement is Windsurf-specific.** Devin sessions are validated post-hoc, not pre-empted at write time.
5. **Task → file prediction precision/recall are reported openly** on the hand-labelled set when available.
6. **The merge-tax metric is novel and self-defined.** We argue it matters; we don't claim industry consensus.
7. **CodeCRDT, Agint, LangGraph, and OpenCode locks are cited as related work.** We do not claim to be the first to think about multi-agent coordination; ACG's contribution is a pre-flight static disjointness lockfile exposed through CLI/MCP surfaces.

## Citations

See [`docs/CITATIONS.md`](docs/CITATIONS.md) for verbatim quotes and links.

## Limitations and roadmap

- **CRDT runtime layer.** Explicitly out of scope (CodeCRDT covers it).
- **Live Devin sessions.** Implemented for the Greenhouse smoke test; see `experiments/greenhouse/RESULTS.md`. Broader benchmark numbers remain small-N and should be treated as directional.
- **Multi-language support beyond Java / TS / Python** still requires language-specific parsers and write-set heuristics.

## License

MIT — see `LICENSE`.

## Team

Shashank · Prajit
