# Agent Context Graph (ACG)

> **It's `package-lock.json` for parallel coding agents.**

Multi-agent coding systems often coordinate work only after edits land—merge tools and manual cleanup absorb overlapping changes. **Agent Context Graph (ACG) compiles natural-language task lists plus repository context into a committable `agent_lock.json` write contract.** The lockfile scopes each task’s filesystem authority (`allowed_paths`), records predicted and contextual paths, sequences contending work into parallel-safe groups, and supports mid-flight validation of proposed edits.

## Live execution mode

ACG ships with a runtime (`acg/runtime.py`) that executes a lockfile against two local LLM endpoints (or a deterministic mock):

- **Orchestrator** (port 8081 by convention) — planning / dispatch narrative
- **Sub-agents** (port 8080 by convention) — per-task workers

Workers emit **OpenAI-style `apply_patch` envelopes**; successful patches are applied on disk, then each touched path is checked with **`validate_write`**. Writes that only match **`candidate_context_paths`** (not yet promoted to the hard write contract) can be marked **needs replan**; with `ACG_AUTO_REPLAN=1`, the runtime may **promote** approved candidates via `promote_candidate_paths` in `acg/compiler.py`. ALLOWED / BLOCKED / replan-related outcomes are recorded to `run_trace.json` (for example under `demo-app/.acg/` in the default Gemma flow). The visualizer replays the trace against the static lockfile DAG.

```bash
make compile-gemma   # build the lockfile against your local OpenAI-compatible servers
make run-gemma       # execute it; writes run_trace.json
make viz             # open the live-replay visualizer
```

Offline / CI mode uses a deterministic mock:

```bash
make run-mock && make viz
```

See `viz/README.md` for the visualizer and `acg/runtime.py` for prompts, patch application, and enforcement. **Evaluation write-ups in this repository** (not an external `papers/` tree) include [`experiments/greenhouse/RESULTS.md`](experiments/greenhouse/RESULTS.md) (Greenhouse + broader claims with cited artifacts) and [`experiments/real_repos/aggregate_all.md`](experiments/real_repos/aggregate_all.md) (multi-repo aggregator notes).

## Architecture

End-to-end flow:

```text
tasks.json + repo
      │
      ▼ scan + localization (native / scip / auto)
   context_graph.json  ←  ts-morph (TS/JS), in-process scanners (Python, Java),
                          optional SCIP metadata merged in acg/repo_graph.py
      │
      ▼ predictor (acg/predictor.py)
   PredictedWrite[] + tiered FileScope[]  ←  deterministic seeds + LLM re-rank
      │
      ▼ compiler (acg/compiler.py)
   predicted_writes[], candidate_context_paths[], allowed_paths[] (glob contract)
      │
      ▼ solver (acg/solver.py)
   depends_on[], parallel_group, execution_plan  →  agent_lock.json
      │
      ├──────── runtime (acg/runtime.py): apply_patch → validate_write
      │         optional auto-replan / promotion for candidate-context paths
      └──────── hooks / MCP / validate-diff consumers
```

1. **Repository scan and localization**: `acg/repo_graph.scan_context_graph` dispatches by `--language`, writes `<repo>/.acg/context_graph.json`, and merges **native** graph fields with optional **SCIP** metadata when `--localization-backend` is `scip` or `auto`.

2. **Predictor**: Produces **`predicted_writes`** (tight targets) and **`file_scopes`** with tiers such as **must_write**, **candidate_context**, and **needs_replan**, using multiple deterministic seed paths plus optional scope review / SCIP-informed signals and a single LLM re-rank when configured.

3. **Compiler**: Assembles **`predicted_writes`**, **`candidate_context_paths`**, **`allowed_paths`**, **`file_scopes`**, task metadata, then hands tasks to the solver.

4. **Solver**: Builds the contention DAG and assigns **`parallel_group`** and **`depends_on`** (deterministic ordering rules in `acg/solver.py`).

At **runtime**, scoped prompts and graph context feed workers; **`apply_patch_adapter`** applies patches; **`validate_write`** enforces **`allowed_paths`**. Replans are an **optional** path (environment-gated), not a guarantee for every deployment.

Full detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Evidence and demos (what the repo actually contains)

This repository includes **point-in-time evaluation artifacts** (JSON runs, lockfiles, analyzer output) alongside narrative summaries. Figures and tables should be read as **illustrative of the bundled fixtures**, not as a fully general multi-trial benchmark—see `experiments/greenhouse/RESULTS.md` for scope and caveats.

The **`demo-app`** Makefile targets (`make compile`, `make demo`, `make run-gemma`, `make run-mock`, …) exercise the canonical TypeScript demo and offline benchmark chart.

**Separate experiment harnesses** (additional checkouts and suites) are driven by dedicated Makefile targets:

- **Greenhouse** (Java legacy harness): `setup-greenhouse`, `compile-greenhouse`, `eval-greenhouse-mock`, `eval-greenhouse-local`, variants for applied-diff / Devin—see Makefile and `experiments/greenhouse/`.
- **Realworld** (NestJS/OpenRouter pipelines): `setup-realworld`, `compile-realworld`, `eval-realworld-*`, `analyze-realworld*`—see Makefile and `experiments/realworld/`.
- **Python FastAPI** mock eval: `setup-python-fastapi`, `compile-python-fastapi`, `eval-python-fastapi-mock`, `analyze-python-fastapi-mock`—see `experiments/python_fastapi/`.

## Demo snapshot (demo-app)

![Agent coordination tax — naive vs ACG-planned](docs/benchmark.png)

Same four tasks (`oauth`, `billing`, `settings`, `tests`) on `demo-app`, comparing naive parallel simulation vs planned execution from **`make demo`**:

| Metric               | Naive parallel | ACG-planned |
| -------------------- | -------------- | ----------- |
| Overlapping writes   | 4              | 1           |
| Blocked bad writes   | 0              | 2           |
| Manual merge steps   | 4              | 0           |
| Tests pass first run | no             | yes         |
| Wall time (min)      | 20             | 13          |

`oauth` and `settings` are write-disjoint and can run in parallel; `billing` overlaps both and is serialized; `tests` follows prior groups. Exact numbers depend on the pinned tasks and simulator—re-run **`make demo`** to regenerate.

## 60-second quickstart

```bash
git clone <this repo>
cd <repository-root>

make install           # creates .venv, pip installs ACG, npm installs ts-morph graph builder
cp .env.example .env   # API keys for compile/runtime LLMs (or use mock / offline flows)

make demo              # scan + compile + benchmark + chart in one shot
```

`make demo` produces (under default paths):

- `demo-app/.acg/context_graph.json` — repo graph snapshot
- `demo-app/agent_lock.json` — committable plan
- `.acg/run_naive.json` + `.acg/run_acg.json` — benchmark metrics
- `docs/benchmark.png` — comparison chart

To watch the validator reject an out-of-bounds proposal:

```bash
./.venv/bin/acg validate-write \
  --lock demo-app/agent_lock.json \
  --task settings \
  --path src/server/auth/config.ts
# BLOCKED … exit code 2
```

## What is `agent_lock.json`?

A schema-validated artifact (`schema/agent_lock.schema.json`, `acg/schema.py`) that declares for each task:

- `prompt` — natural-language task description
- `predicted_writes[]` — predicted write targets
- `allowed_paths[]` — glob patterns enforced by the validator
- `candidate_context_paths[]` — broader localization / context references
- `file_scopes[]` — tiered scope records bridging predictions and enforcement
- `depends_on[]` — explicit DAG edges
- `parallel_group` — execution tier within the solver plan

…and lockfile-level `execution_plan.groups[]`, `conflicts_detected[]`, etc. Example: `examples/lockfile.dag.example.json`.

## CLI surface

Authoritative enumeration and flags live in **`acg/cli.py`**. High-level grouping:

**Compile / graph**

- `acg plan-tasks` — goal → `tasks.json` (`--localization-backend`, `--language`, …)
- `acg init-graph` — scan repo → `.acg/context_graph.json`
- `acg compile` — `tasks.json` + repo graph → `agent_lock.json` (`--language`, `--use-cached-graph` / `--rescan-graph`, `--localization-backend`)

**Validation / introspection**

- `acg explain` — human-readable lockfile summary
- `acg validate-lockfile` — JSON Schema check
- `acg validate-write` — single path vs task contract (exit **2** = blocked)
- `acg validate-diff` — git diff vs task contract

**Runtime**

- `acg run` — execute lockfile, emit trace (`--mock`, concurrency / perf options—see `--help`)

**Eval / charts**

- `acg run-benchmark` — naive vs planned simulator metrics
- `acg report` — PNG chart from metric JSON pairs

**Analysis**

- `acg analyze-runs` — aggregate `eval_run*.json` style artifacts into Markdown / JSON summaries

**MCP**

- `acg mcp --transport stdio` — FastMCP server (`pip install -e '.[mcp]'`): see [`docs/MCP_SERVER.md`](docs/MCP_SERVER.md)

## Integrations

- **Cognition (Devin)** — pre-flight contracts and MCP: [`docs/COGNITION_INTEGRATION.md`](docs/COGNITION_INTEGRATION.md)
- **Cascade (Windsurf)** — hooks: [`docs/CASCADE_INTEGRATION.md`](docs/CASCADE_INTEGRATION.md)
- **Model Context Protocol** — [`docs/MCP_SERVER.md`](docs/MCP_SERVER.md)

## Limitations and future work

- **File-level scope**: Enforcement is path / glob oriented; finer-grained semantic coupling is future work.
- **Languages**: TypeScript/JavaScript, Python, and Java scanners ship in-tree; additional languages need new scanners and predictor tuning.
- **Runtime**: Replans and SCIP backends are optional; production behavior depends on environment, lockfile tiering, and provider capabilities.

## License

MIT — see `LICENSE`.

## Team

Shashank · Prajit
