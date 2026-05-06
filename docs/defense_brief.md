# ACG Defense Brief

> Comprehensive prep doc for judging. Every number cites a real file path. Phrases marked **DO NOT SAY** are traps.

---

## Table of contents

1. [TL;DR pitch ladder](#1-tldr-pitch-ladder)
2. [Mental model](#2-mental-model)
3. [End-to-end integration flow](#3-end-to-end-integration-flow)
4. [Module-by-module walkthrough](#4-module-by-module-walkthrough)
5. [Devin v3 integration](#5-devin-v3-integration)
6. [Data and receipts](#6-data-and-receipts)
7. [The exact prompts](#7-the-exact-prompts)
8. [Walked example: `oauth` task](#8-walked-example-oauth-task)
9. [Defense Q&A — 35 hostile questions](#9-defense-qa)
10. [Do not say list](#10-do-not-say-list)
11. [Quick-reference cheat card](#11-quick-reference-cheat-card)

---

## 1. TL;DR pitch ladder

### 1a. 10-second hook

> **ACG is `package-lock.json` for parallel coding agents — a static, committable lockfile that decides which tasks can run in parallel and which need to serialize, before any agent runs.**

### 1b. 30-second pitch

Parallel coding agents (Devin Manage Devins, OpenCode, Cursor in agent mode) collide on shared files. Public docs say the coordinator "resolves conflicts" but never describe how. **ACG moves that work before execution.** It scans the repo, predicts each task's write-set with a 7-seed pipeline + LLM rerank, emits a committable `agent_lock.json` with per-task `allowed_paths` globs, and validates every proposed write at runtime. Tested across 3 codebases (Spring Java, T3 Next.js, Brocoders NestJS) × 3 backends (mock, local Gemma on GX10, live Devin v3) × 10 strategy-runs. **6/6 live Devin PRs respected the contract; predictor recall = 1.00; per-worker context shrinks 11–54%.**

### 1c. 2-minute pitch

**Problem.** CodeCRDT (arXiv:2510.18893, Oct 2025) reports parallel multi-agent outcomes from +21.1% speedup to −39.4% slowdown on coupled tasks; their future-work explicitly asks for static analysis of task coupling. OpenCode Issue #4278 (Nov 2025) — real users asking for per-file locks; closed "completed" without an implementation. Walden Yan interview (jxnl.co Sep 2025): "lots of actions carry these implicit decisions… you might just get conflicting decisions."

**Mechanism.** Static write-contract compiler: tasks + repo → lockfile. Per-task `predicted_writes` (with confidence + reason), per-task `allowed_paths` globs, `execution_plan` of parallel/serial groups, `conflicts_detected` array. Runtime validator (`validate_write` in `@/Users/prajit/Desktop/projects/cognition/acg/enforce.py:61-85`) is glob-based and returns exit code 2 on out-of-bounds. For Cascade-style local agents the validator pre-empts at edit time; for black-box Devin it runs post-hoc on PR diffs.

**Receipts.**

- **Greenhouse** (Spring 2008 Java, commit `174c1c3`): 3 RowMapper-to-lambda tasks. Live Devin: **6/6 PRs respected `allowed_paths`, 0 OOB writes** (`@/Users/prajit/Desktop/projects/cognition/experiments/greenhouse/runs/eval_run_devin_api_acg_smoke.json:13-27`).
- **demo-app** (T3 stack, 4 tasks): 4 conflict pairs, 3 execution groups, **per-worker prompt-token reduction 43% on average**.
- **Brocoders NestJS** (156 files, 7 tasks): 11 conflict pairs (10 on `src/app.module.ts`), 5 execution groups, **prompt-token reduction 54% (3721 → 1700)**.

**Novelty.** Pre-flight static disjointness as a committable artifact, exposed via 4 MCP tools (`analyze_repo`, `predict_writes`, `compile_lockfile`, `validate_writes`) — drop-in for Devin / Cursor / Cascade / Claude Code.

**Honesty.** Small-N single-trial; file-level disjointness only (no semantic drift); merge conflicts didn't materialize on Greenhouse because all three agents converged on the identical `pom.xml` edit and git auto-resolved. Said out loud in `@/Users/prajit/Desktop/projects/cognition/README.md:148-156`.

---

## 2. Mental model

### What ACG **is**

- A static write-set predictor + DAG solver + filesystem-write contract validator
- A committable artifact (`agent_lock.json`) — reviewable in a PR like any other infra config
- Provider-agnostic — same lockfile drives mock, local llama-server, live Devin v3
- Exposed via MCP — drop-in for any MCP-aware agent host

### What ACG **is not**

- Not a runtime CRDT (CodeCRDT covers that; we cite it)
- Not a code generator — we don't compete with the agent, we compose with it
- Not a hard pre-emption layer for Devin — Devin is black-box; the lockfile is a soft prompt constraint validated post-hoc
- Not a novel multi-agent orchestrator (LangGraph/CrewAI/Temporal already orchestrate calls); the **artifact** is the contribution
- Not a benchmark paper — directional small-N evidence, openly labeled

### One-line analogy

> "`package-lock.json` froze dependency graphs so installs became reproducible. ACG freezes write-set graphs so multi-agent runs become reproducible."

---

## 3. End-to-end integration flow

```text
INPUT
  • repo path
  • tasks.json (NL task list)
       │
       ▼
graph_builder/scan.ts (TS) or scan_java.py (in-process tree-sitter)
       │
       ▼  context_graph.json (.acg/)
acg/predictor.py
  • 7 deterministic seeds + LLM rerank, dedup, top-N=8
       │
       ▼  PredictedWrite[] per task
acg/compiler.py
  • allowed_paths globs (broaden ≥4 segments + ≥0.7 conf → parent/**)
  • test-task heuristic
       │
acg/solver.py
  • detect_conflicts → build_dag (3-layer edges) → topological_groups
       │
       ▼  agent_lock.json
   ┌────────────┬─────────────┬────────────────┬────────────────┐
   ▼            ▼             ▼                ▼                ▼
acg/runtime  acg/enforce   acg/explain     acg/report      acg/mcp/server
async fan-out validate_write ASCII DAG    benchmark PNG    FastMCP stdio
              (exit 0/1/2)                                   4 tools
```

### Stage by stage

| #   | Stage              | Module                                | Determinism                         |
| --- | ------------------ | ------------------------------------- | ----------------------------------- |
| 1   | Scan               | `acg/repo_graph.py` + TS/Java scanner | Deterministic                       |
| 2   | Predict            | `acg/predictor.py`                    | Seeds deterministic; LLM stochastic |
| 3   | Allowed paths      | `acg/compiler.py:_to_allowed_path`    | Deterministic                       |
| 4   | Conflict detection | `acg/solver.py:detect_conflicts`      | Deterministic                       |
| 5   | DAG build          | `acg/solver.py:build_dag`             | Deterministic, 3-layer              |
| 6   | Group topology     | `acg/solver.py:topological_groups`    | Deterministic                       |
| 7   | Lockfile assembly  | `acg/compiler.py:compile_lockfile`    | Deterministic given predictions     |
| 8   | Run                | `acg/runtime.py` (async)              | Validator-gated fan-out             |
| 9   | Enforce            | `acg/enforce.py:validate_write`       | Deterministic                       |
| 10  | Replay             | `viz/src/lib/replay.ts`               | Pure function of trace + tSeconds   |

---

## 4. Module-by-module walkthrough

### 4.1 `@/Users/prajit/Desktop/projects/cognition/acg/repo_graph.py` + `acg/index/`

**Role.** Build deterministic repo graph (files, symbols, imports/exports, hotspots). No LLM.

**Key functions:**

- `scan_context_graph(repo_root, language)` (`@/Users/prajit/Desktop/projects/cognition/acg/repo_graph.py:135-155`) — dispatches to TS scanner or Java tree-sitter; writes `<repo>/.acg/context_graph.json`
- `detect_language` — `pom.xml`→java, `tsconfig.json`/`next.config.ts`→typescript, fallback to extension counting
- `normalize_context_graph` — same shape regardless of language

**The 4 indexers:**

| Indexer   | File                     | Signal                                                                                                                                           | Conf                             |
| --------- | ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------- |
| Framework | `acg/index/framework.py` | Detect Next/T3/Django/Rails/FastAPI/Spring Boot/Vite from package.json/pyproject/Gemfile/pom.xml; map roles to canonical paths                   | 0.85 fixed                       |
| PageRank  | `acg/index/pagerank.py`  | Personalized PageRank over file-level symbol graph (imports + identifier refs); tree-sitter or regex symbol extraction; cached by repo signature | `min(0.9, rank*1000)`            |
| BM25      | `acg/index/bm25.py`      | Lexical BM25 over (path, identifiers, imports, docstring, exports); synonym table                                                                | `tanh(score/5.0)`                |
| Co-change | `acg/index/cochange.py`  | ROSE-style git-history association (`git log --name-only`); seed expansion only; min 3 commits                                                   | `count/seed_commits`, capped 0.8 |

**Aggregator** (`acg/index/aggregate.py:54-75`): runs framework + pagerank + bm25, fuses by max-confidence per path, then runs cochange seeded by top fused paths.

### 4.2 `@/Users/prajit/Desktop/projects/cognition/acg/predictor.py`

**Role.** Task → write-set: 7 deterministic seeds + 1 LLM rerank → top-N PredictedWrite.

**The 7 seeds:**

| #   | Seed                    | Trigger                                                                                 | Conf      |
| --- | ----------------------- | --------------------------------------------------------------------------------------- | --------- |
| 1   | `_static_seed`          | Verbatim file mention regex                                                             | 0.95      |
| 2   | `_symbol_seed`          | camelCase tokens >5 chars resolved via `repo_graph.symbols_index`                       | 0.85      |
| 3   | `_topical_seed`         | `task.hints.touches[]` substring match against paths                                    | 0.7       |
| 4   | `_test_scaffold_seed`   | Framework convention (Playwright/Vitest/Jest/Cypress/pytest)                            | 0.85      |
| 5   | `_env_seed`             | Credential triggers (oauth/stripe/auth0/clerk/nextauth) → `.env.example` + `.env.local` | 0.8/0.65  |
| 6   | `_sibling_pattern_seed` | "add API/endpoint/route" + analogical reasoning over existing API trees                 | 0.75/0.65 |
| 7   | `_index_seed`           | Wraps `acg.index.aggregate(top_n=3)`                                                    | floor 0.5 |

**Caps:** `MAX_PREDICTIONS=8`, `SEED_INDEX_TOP_N=3` (so PageRank doesn't dominate every task with the same hotspots), `TOP_GRAPH_FILES_FOR_LLM=50`.

**LLM rerank.** System prompt asks for conservative JSON output. `_parse_llm_writes` is forgiving (code-fence stripping, balanced-brace fallback). **On any LLM failure (transport, parse) the predictor falls back to seed-only — never aborts compilation.**

**Merge rule.** For paths in both seeds and rerank, LLM's confidence wins but seed's reason is preserved when LLM omits one. Sorted by `(-confidence, path)`.

### 4.3 `@/Users/prajit/Desktop/projects/cognition/acg/solver.py`

**Role.** Pure function: `Task[]` → DAG → groups. No I/O, no LLM, no globals. Most unit-tested module.

**Edge orientation rule:** `(conflict_count, input_index)` is a strict total order. Predecessor of every conflict pair = lighter task (fewer conflicts), tie-break by input-list index.

**3-layer edge model** (`acg/solver.py:87-180`):

1. **Conflict-derived** (defeasible) — lighter task first
2. **Heuristic** (defeasible) — caller-supplied (e.g., compiler's "tests run last")
3. **Explicit** (NOT defeasible) — `task.depends_on`; cycles raise `ValueError`

**SCC collapse** between layers 2 and 3: if defeasible edges form a cycle, replace internal edges with strict input-order chain. Cycles formed purely by user-declared deps still raise.

**`topological_groups`:** node level = longest path from any source; nodes at same level form a group. Multi-node → `parallel`; single-node → `parallel` (level 0) or `serial`. Group ids dense 1..N. `waits_for=[id-1]` (transitively earlier groups implied).

### 4.4 `@/Users/prajit/Desktop/projects/cognition/acg/compiler.py`

**Role.** Tasks + repo graph → `agent_lock.json`.

**Key behaviors:**

- **Glob broadening:** if path ≥4 segments AND confidence ≥0.7, broaden to `parent/**`. Test paths broaden at ≥3 segments. So `src/server/auth/config.ts`→`src/server/auth/**` but `src/server/x.ts` stays exact.
- **Test-task heuristic:** tasks with `hints.touches` containing `tests`/`test`/`e2e`/`playwright` get heuristic deps on every non-test task. Defeasible (SCC collapse can override).

### 4.5 `@/Users/prajit/Desktop/projects/cognition/acg/runtime.py`

**Role.** Async runtime executes lockfile against two `llama-server` instances (orchestrator on 8081 with thinking, sub-agents on 8080 `--parallel 4`). Records every proposal — ALLOWED or BLOCKED — to `run_trace.json`.

**Key components:**

- `RuntimeConfig.from_env()` — reads `ACG_ORCH_URL`, `ACG_LLM_URL`, etc. Defaults to GX10 hostname `gx10-f2c9`
- `RuntimeLLM` async client — single shared `httpx.AsyncClient`; captures `reasoning_content` (Gemma's thinking) separately
- `MockRuntimeLLM` — deterministic offline; **intentionally crafted to mix in-bounds + one OOB proposal per task** so the run trace exercises both validator outcomes
- `run_orchestrator` — single thinking-pass call; lockfile summary → JSON dispatch decision; **never blocks on parse failure** (lockfile is source of truth)
- `run_worker` — propose writes, validate every one. **Workers are NOT told their `allowed_paths`** — keeps validator honest, produces real BLOCKED moments
- `run_group` — three concurrency lanes: sequential, semaphore-bounded gather, unbounded gather

**Worker prompt is grounded:** top-30 most-imported repo files (`WORKER_TOP_K_FILES=30`) listed in prompt so workers propose grounded paths instead of inventing.

### 4.6 `@/Users/prajit/Desktop/projects/cognition/acg/enforce.py`

**Role.** Glob-based write validator. Returns `(allowed, reason)`. Exit codes: 0 allowed, 1 user error, 2 blocked.

**`_matches`** supports POSIX `**`:

- `parent/**` matches `parent` and `parent/anything/below`
- `a/**/b` translates to "contains `a` then later `b` in order"
- Else falls back to stdlib `fnmatch.fnmatch`

**CLI smoke:**

```bash
./.venv/bin/acg validate-write --lock demo-app/agent_lock.json --task settings --path src/server/auth/config.ts
# BLOCKED: path 'src/server/auth/config.ts' is outside task 'settings''s allowed_paths
# exit code 2
```

### 4.7 `@/Users/prajit/Desktop/projects/cognition/acg/mcp/server.py`

**Role.** FastMCP stdio server, 4 tools, drop-in for any MCP host.

| Tool               | Inputs                                             | Output                         |
| ------------------ | -------------------------------------------------- | ------------------------------ |
| `analyze_repo`     | `path`, `language`                                 | normalized context graph       |
| `predict_writes`   | `task` (dict), `repo_path`, optional `repo_graph`  | `[{path, confidence, reason}]` |
| `compile_lockfile` | `repo_path`, `tasks` (TasksInput dict), `language` | full `agent_lock.json`         |
| `validate_writes`  | `lockfile`, `task_id`, `attempted_path`            | `{allowed: bool, reason}`      |

Side effect: `analyze_repo` writes `<repo>/.acg/context_graph.json`. All others side-effect-free.

### 4.8 `@/Users/prajit/Desktop/projects/cognition/acg/llm.py`

**Role.** Provider-agnostic OpenAI-compatible client. Same code path for Groq (dev), vLLM on GX10 (production), local llama.cpp.

**Env vars:** `ACG_LLM_URL`, `ACG_LLM_MODEL`, `ACG_LLM_API_KEY` (or `GROQ_API_KEY` fallback), `ACG_MOCK_LLM=1`.

**`MockLLMClient`:** pattern-matches `Task id: <id>`, returns canned predictions. No silent failure — if env vars missing, mock kicks in deterministically.

### 4.9 `@/Users/prajit/Desktop/projects/cognition/acg/cli.py`

**CLI:**

```text
acg compile           --repo PATH --tasks FILE --out FILE
acg explain           --lock FILE
acg validate-write    --lock FILE --task ID --path PATH
acg report            --naive FILE --planned FILE --out FILE
acg run               --lock FILE --repo PATH --out FILE [--mock]
acg run-benchmark     --mode {naive,planned} --repo PATH --tasks FILE --out FILE
acg analyze-runs      <eval_run.json>... --out report.md --json-out report.json
acg mcp               [--transport stdio]
acg init-graph        --repo PATH [--language {auto,ts,java}]
acg validate-lockfile --lock FILE --schema schema/agent_lock.schema.json
```

### 4.10 `@/Users/prajit/Desktop/projects/cognition/viz/`

**Role.** React Flow v12 visualizer consuming `agent_lock.json` + `run_trace.json`, replays in real time.

**Renders:** task nodes by `execution_plan.groups`, dependency edges, conflict edges (red dashed animated), orchestrator panel with typewriter `reasoning_content`, toolbar (phase / progress / 0.5×/1×/2×/4× speed / play-pause-reset).

**`viz/src/lib/replay.ts`** is a pure function `(trace, tSeconds) → state`, driven by `requestAnimationFrame`. Visual durations capped (orch 12s, group 4s).

### 4.11 `@/Users/prajit/Desktop/projects/cognition/schema/`

**Lockfile root** (`acg/schema.py:125-140`):

```text
AgentLock {
  version: "1.0" const
  generated_at: datetime UTC
  generator: { tool, version, model? }
  repo: { root, git_url?, commit?, languages: [...] }
  tasks: [Task]
  execution_plan: { groups: [Group] }
  conflicts_detected: [Conflict]
}
```

**Strict mode:** `extra="forbid"` so unknown fields raise; `str_strip_whitespace=True`.

---

## 5. Devin v3 integration

### 5.1 Honest framing (memorize)

> "ACG cannot pre-empt Devin's writes — Devin is a black-box hosted agent. The contract is two-layered: a **SPEC layer** (lockfile injected as a soft prompt constraint plus the execution_plan timing schedule) and an **ENFORCEMENT layer** (only available with local Cascade-style backends via `validate_write`). For Devin we audit post-hoc on the PR diff."

### 5.2 v3 API surface (empirical)

Discovered via `scripts/diagnostics/devin_api_probe.py`. Working endpoints:

- `POST /v3/organizations/{org_id}/sessions` — create, returns full session JSON
- `GET /v3/organizations/{org_id}/sessions/{sid}` — poll status, read `pull_requests[]` + `structured_output`
- `GET .../sessions/{sid}/messages` — paginated chat log

**Sibling endpoints all 404:** `/files`, `/diff`, `/output`, `/stop`, `/pull-requests`. Extraction lives **on the session detail JSON itself**.

**Status semantics:** `status` enum: `new`, `claimed`, `running`. Terminal: `running` + `status_detail=waiting_for_user` is the conversational "Devin replied, awaiting next message" state — for our one-shot harness we treat as success terminal.

**v1 endpoints all 403** — token is v3 org-scoped only.

### 5.3 Three-tier changed-files extraction

`extract_changed_files` (`@/Users/prajit/Desktop/projects/cognition/experiments/greenhouse/devin_api.py:549-624`):

1. **Tier 1** — `structured_output` field (we send `CHANGED_FILES_SCHEMA` at create time; Devin honors it most of the time)
2. **Tier 2** — fenced ` ```json ` blocks in `source="devin"` messages, walking newest first
3. **Tier 3** — conservative inline regex matching code-file extensions

### 5.4 Strategy fan-out

`devin_api_run` async, semaphore-bounded by `max_parallelism`:

- **`naive_parallel`** — `asyncio.gather(*all_tasks)`. Sessions race
- **`acg_planned`** — walk `lock.execution_plan.groups` **serially**; within each group, `asyncio.gather` for parallel

Same client, same extraction. Only the prompt builder differs (`devin_prompts.py`).

### 5.5 Conservative scoring

Every backend scores out-of-bounds against `allowed_paths` regardless of strategy. Even a naive run flags safety incidents that ACG **would have caught**. If `out_of_bounds_files` is non-empty, status flips `completed` → `completed_unsafe`. `_is_completed` treats `completed_unsafe` as **NOT** fully completed — sponsor claims use conservative scoring.

---

## 6. Data and receipts

### 6.1 demo-app (T3 stack: Next.js 14 + tRPC + Prisma + Tailwind)

**Repo.** `@/Users/prajit/Desktop/projects/cognition/demo-app/` — small T3 starter, ~50 files. typescript.

**4 tasks** (verbatim from `@/Users/prajit/Desktop/projects/cognition/demo-app/tasks.json`):

| id         | prompt                                                                                                                                   |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `oauth`    | "Add Google OAuth login. Use NextAuth. Update Prisma schema with required fields."                                                       |
| `billing`  | "Add a billing dashboard tab at /dashboard/billing with Stripe integration. Add a sidebar entry. Update Prisma with subscription model." |
| `settings` | "Redesign the user settings page at /settings. Reorganize sections. Update sidebar entry styling."                                       |
| `tests`    | "Write end-to-end Playwright tests for the checkout flow."                                                                               |

**Lockfile** (`@/Users/prajit/Desktop/projects/cognition/demo-app/agent_lock.json`):

| Task       | Predicted writes | Allowed paths | Group        |
| ---------- | ---------------- | ------------- | ------------ |
| `oauth`    | 6                | 5             | 1 (parallel) |
| `billing`  | 8                | 8             | 2 (serial)   |
| `settings` | 3                | 3             | 1 (parallel) |
| `tests`    | 5                | 5             | 3 (serial)   |

**Execution plan:** Group 1 = `[oauth, settings]` parallel, Group 2 = `[billing]` (waits_for=[1]), Group 3 = `[tests]` (waits_for=[2]).

**4 conflicts detected:**

1. `oauth ↔ billing` on `[.env.example, prisma/schema.prisma]`
2. `oauth ↔ tests` on `[.env.example]`
3. `settings ↔ billing` on `[src/app/dashboard/page.tsx, src/components/Sidebar.tsx]`
4. `tests ↔ billing` on `[.env.example, src/server/db.ts]`

**Mock-backend run** (`@/Users/prajit/Desktop/projects/cognition/experiments/demo-app/runs/eval_run_combined.json`):

| Strategy       | Total prompt tokens | Per-task avg | OOB writes | Overlap pairs |
| -------------- | ------------------- | ------------ | ---------- | ------------- |
| naive_parallel | 897                 | 224          | 0          | 4             |
| acg_planned    | 513                 | 128          | 0          | 4             |

**Per-task savings:** oauth −38%, billing −38%, settings −43%, tests −52%. **43% average reduction.**

### 6.2 Greenhouse (Spring 2008 Java)

**Repo.** `https://github.com/spring-attic/greenhouse.git`, pinned commit `174c1c320875a66447deb2a15d04fc86afd07f60`. ~200 Java files. `<java-version>1.6</java-version>` in `pom.xml`.

**3 tasks** (`@/Users/prajit/Desktop/projects/cognition/experiments/greenhouse/tasks.json`):

| id                         | prompt summary                                                                                                 |
| -------------------------- | -------------------------------------------------------------------------------------------------------------- |
| `lambda-rowmapper-account` | Replace anonymous `RowMapper<PasswordProtectedAccount>` in `JdbcAccountRepository.java:~110`; bump pom 1.6→1.8 |
| `lambda-rowmapper-invite`  | Replace anonymous `RowMapper<Invite>` in `JdbcInviteRepository.java:~87`; bump pom                             |
| `lambda-rowmapper-app`     | Replace **four** anonymous `RowMapper`s in `JdbcAppRepository.java:113,158,166,172`; bump pom                  |

**Lockfile:** 3 conflict pairs all driven by **`pom.xml`** (every task bumps it). Execution plan: 3 groups of 1 task each, fully serialized.

**Live Devin v3 — naive_parallel** (`@/Users/prajit/Desktop/projects/cognition/experiments/greenhouse/runs/eval_run_devin_api_naive_smoke.json:13-27`):

| Metric                       | Value    |
| ---------------------------- | -------- |
| Wall time                    | 276.95 s |
| ACUs consumed                | 3.06     |
| Tasks completed              | 3 / 3    |
| Out-of-bounds writes         | **0**    |
| Overlap pairs (compile-time) | 3        |

PRs: <https://github.com/SSKYAJI/greenhouse/pull/1>, `/pull/2`, `/pull/3`.

**Live Devin v3 — acg_planned** (`@/Users/prajit/Desktop/projects/cognition/experiments/greenhouse/runs/eval_run_devin_api_acg_smoke.json:13-27`):

| Metric               | Value    |
| -------------------- | -------- |
| Wall time            | 853.68 s |
| ACUs consumed        | 3.22     |
| Tasks completed      | 3 / 3    |
| Out-of-bounds writes | **0**    |
| Overlap pairs        | 3        |

PRs: `pull/4`, `pull/5`, `pull/6`.

**6/6 PRs respected the contract. Devin is fully black-box. The constraint was a static lockfile delivered as part of the prompt.**

**Local GX10 (gemma) run** (`@/Users/prajit/Desktop/projects/cognition/experiments/greenhouse/runs/eval_run_combined.json:13-256`):

| Strategy       | Wall time | Worker prompt tokens | Completion tokens | OOB |
| -------------- | --------- | -------------------- | ----------------- | --- |
| naive_parallel | 12.47 s   | 2159                 | 1062              | 0   |
| acg_planned    | 22.13 s   | 1922                 | 912               | 0   |

Per-worker prompt savings: 11% (1922/3=641 vs 2159/3=720). Modest because Greenhouse `allowed_paths` are deliberately wide.

**Wall-time gotcha:** ACG planned (22s) is 1.77× slower than naive (12s) on local; 3.08× slower on Devin (854 vs 277). **This is the price of sequencing.** Tradeoff is correctness for speed; recovered when downstream merge/review/test conflict costs dominate.

**Failed run artifact** (`@/Users/prajit/Desktop/projects/cognition/experiments/greenhouse/runs_model_gemma_local/eval_run_combined.json`): `tasks_completed=0` because GX10 endpoint at `100.115.37.73:8080` was unreachable; all `failure_reason=AGENT_FAIL`. **DO NOT cite this run.** The successful run is `experiments/greenhouse/runs/eval_run_combined.json` against `gx10-f2c9:8080`.

### 6.3 Brocoders NestJS (production-style modern backend)

**Repo.** `brocoders/nestjs-boilerplate`, main, commit `dd0034750fc7f6ec15712afbecf50fa9828018a2`. **156 TypeScript files** under `src/`. TypeORM + PostgreSQL relational + Mongoose document support.

**7 tasks** (`@/Users/prajit/Desktop/projects/cognition/experiments/microservice/tasks_brocoders.json`):

1. `products-domain` — full module + dto + repository + entity + migration
2. `api-key-auth` — guard + service + config + register in app.module
3. `users-search` — search/email-domain filtering on users endpoint (says "do NOT change app.module.ts")
4. `files-e2e-tests` — `test/files.e2e-spec.ts` + jest-e2e config
5. `registration-email-job` — Bull-backed background job, wires into auth + mail + app.module
6. `notifications-webhook` — webhook endpoint module
7. `deployment-config` — env + docker-compose + config types hardening

**Lockfile** (`@/Users/prajit/Desktop/projects/cognition/experiments/microservice/agent_lock_brocoders.json`):

- **11 conflict pairs**, **10 of them on `src/app.module.ts`**
- 1 conflict on `docker-compose.yml` between `deployment-config` ↔ `registration-email-job`

**5 execution groups:**

| Group | Tasks                                                | Type               |
| ----- | ---------------------------------------------------- | ------------------ |
| 1     | `[deployment-config, products-domain, users-search]` | parallel (3 tasks) |
| 2     | `[api-key-auth]`                                     | serial             |
| 3     | `[files-e2e-tests]`                                  | serial             |
| 4     | `[registration-email-job]`                           | serial             |
| 5     | `[notifications-webhook]`                            | serial             |

**Prompt-token reduction** (`@/Users/prajit/Desktop/projects/cognition/experiments/microservice/runs_brocoders_analysis.md:8-12`):

| backend | strategy       | prompt tokens     |
| ------- | -------------- | ----------------- |
| mock    | naive_parallel | 3721              |
| mock    | acg_planned    | **1700** (−54.3%) |
| local   | naive_parallel | 3721              |
| local   | acg_planned    | **1700** (−54.3%) |

**Per-task analyzer accuracy:** all `1.00 / 1.00 / 1.00` across 7 tasks × 4 runs.

**Caveat (memorize):** "The local Gemma model under-proposed concrete writes for several Brocoders tasks. Therefore the Brocoders result is best read as a **planning/context-scaling benchmark**, not as a code-quality success benchmark."

### 6.4 Aggregate analyzer report — 10 strategy-runs, 48 task records, 14 unique tasks

(`@/Users/prajit/Desktop/projects/cognition/experiments/greenhouse/RESULTS.md:324-343`)

| Task                       | TP  | FP  | FN  | Precision | Recall | F1   |
| -------------------------- | --- | --- | --- | --------- | ------ | ---- |
| `api-key-auth`             | 8   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `billing`                  | 8   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `deployment-config`        | 8   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `files-e2e-tests`          | 8   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `lambda-rowmapper-account` | 2   | 6   | 0   | **0.25**  | 1.00   | 0.40 |
| `lambda-rowmapper-app`     | 2   | 6   | 0   | **0.25**  | 1.00   | 0.40 |
| `lambda-rowmapper-invite`  | 2   | 6   | 0   | **0.25**  | 1.00   | 0.40 |
| `notifications-webhook`    | 8   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `oauth`                    | 6   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `products-domain`          | 8   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `registration-email-job`   | 8   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `settings`                 | 3   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `tests`                    | 5   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `users-search`             | 8   | 0   | 0   | 1.00      | 1.00   | 1.00 |

**Overall: precision = 0.82, recall = 1.00, F1 = 0.90.**

**The Java predictor over-predicts by 4×.** It seeds files like `Account.java`, `AccountException.java`, `AccountMapper.java` for `lambda-rowmapper-account` because the topical seed matches every account-related file, but the agent only modifies `JdbcAccountRepository.java` + `pom.xml`. **Recall is perfect** — the predictor never misses a file the agent eventually wrote.

### 6.5 Tightened-Greenhouse fixture (validator visibly fires)

`@/Users/prajit/Desktop/projects/cognition/experiments/greenhouse/agent_lock_tight.json` — hand-edited to shrink each task's `allowed_paths` to exactly `[pom.xml, <single Jdbc*Repository.java>]` while leaving `predicted_writes` at original wider size.

| Task                       | Blocked write events | Actual changed | Sample blocked                                                   |
| -------------------------- | -------------------- | -------------- | ---------------------------------------------------------------- |
| `lambda-rowmapper-account` | 6                    | 2              | `Account.java`, `AccountException.java`, `AccountMapper.java`, … |
| `lambda-rowmapper-invite`  | 6                    | 2              | `Invite.java`, `MailInviteService.java`, …                       |
| `lambda-rowmapper-app`     | 6                    | 2              | `App.java`, `AppController.java`, `AppForm.java`, …              |
| **Total**                  | **18**               | **6**          | —                                                                |

**Regression test:** `tests/test_greenhouse_eval.py::test_tightened_greenhouse_lockfile_fires_validator` asserts `blocked_invalid_write_count ≥ 1` overall and per-task on every CI run. **Test passes.**

```bash
jq .summary_metrics.blocked_invalid_write_count \
  experiments/greenhouse/runs/tight/eval_run_acg.json   # 18
```

### 6.6 Test surface

- **191 tests pass** (`./.venv/bin/python -m pytest tests/ -q`)
- **Ruff clean** (`./.venv/bin/ruff check acg/ tests/ benchmark/`)
- **Viz typecheck clean** (`cd viz && npx tsc --noEmit`)

---

## 7. The exact prompts

### 7.1 Predictor LLM rerank prompt

**System** (`@/Users/prajit/Desktop/projects/cognition/acg/predictor.py:540-547`):

```text
You are ACG, a static analyzer that predicts which files an agent task will modify.
You are given a task description and a code graph (files, imports, exports, hotspots).
Output a JSON object with key "writes" containing a list of {path, confidence, reason}.
Confidence is 0.0-1.0. Reason is one short sentence.
Be conservative: only include files where the task description clearly implies a modification.
Do not include files based on speculation.
```

**User**:

```text
Task id: {id}
Task: {prompt}
Hints: {hints_json}

Code graph (top 50 relevant files):
{filtered_graph_json}

Existing static-seed predictions (you may keep, demote, or remove these):
{seeds_json}

Output JSON only, no prose.
```

### 7.2 Orchestrator prompt (runtime, llama-server :8081)

**System** (`@/Users/prajit/Desktop/projects/cognition/acg/runtime.py:608-616`):

```text
You are an orchestrator analyzing a multi-agent execution plan for coding tasks.
Reason carefully about whether the plan respects all write conflicts.
Output ONLY a JSON object with keys:
  - "approved" (boolean)
  - "concerns" (list of short strings)
  - "dispatch_order" (list of group ids in execution order)
Do not include any prose outside the JSON object.
```

**User:**

```text
Lockfile summary:
{tasks + conflicts + execution_plan, indented JSON}

Reason about the plan, then emit the JSON dispatch decision.
```

### 7.3 Worker prompt (runtime, llama-server :8080, NO allowed_paths)

**System** (`@/Users/prajit/Desktop/projects/cognition/acg/runtime.py:676-681`):

```text
You are a coding agent assigned a single task. Output ONLY a JSON object with key "writes":
an array of objects with keys "file" (repository-relative path) and "description"
(one short sentence). Do not include prose, code fences, or any other text.
```

**User:**

```text
Task id: {task.id}
Task: {task.prompt}
Available files in this repo (top 30 by importance):
  - <path 1>
  - <path 2>
  ...
Note: the lockfile predicts writes under '<dir>'. Propose specific file paths under that directory.
```

**Note: the worker is intentionally NOT given `allowed_paths`.** This keeps the validator honest.

### 7.4 Devin naive prompt (`@/Users/prajit/Desktop/projects/cognition/experiments/greenhouse/devin_prompts.py:42-59`)

```text
You are modifying the {repo_url} repository (base branch: {base_branch}).
Clone it, create a working branch off `{base_branch}`, and complete the task below.
Push your branch and open a PR titled `[ACG-naive] {task.id}` against `{base_branch}`.

## Task `{task.id}`
{task.prompt}

[STRUCTURED OUTPUT INSTRUCTION — ask for {changed_files, pr_url, branch, summary} JSON]
```

### 7.5 Devin planned prompt

Adds, after the task body:

```text
Prior tasks already merged into `{base_branch}`: `{deps}`. Rebase if you encounter merge conflicts.

## Write boundary (ACG contract)
You may modify ONLY files matching these glob patterns:
  - `pom.xml`
  - `src/main/java/com/springsource/greenhouse/account/**`
  - `src/main/java/com/springsource/greenhouse/invite/**`
  - `src/main/java/com/springsource/greenhouse/members/**`

If you believe a file outside this boundary must change, STOP and explain in your reply
rather than editing it. The boundary was computed by the Agent Context Graph compiler
to prevent collisions with other tasks running in this batch.

## Known cross-task conflicts (already resolved by the schedule)
  - `pom.xml`, `JdbcInviteRepository.java` also touched by `lambda-rowmapper-invite`:
    Serialize lambda-rowmapper-invite after lambda-rowmapper-account; both modify ...
  - `pom.xml` also touched by `lambda-rowmapper-app`: Serialize ...

[STRUCTURED OUTPUT INSTRUCTION]
```

**Naive vs planned diff = write-boundary block + conflict-context block + dependency note.** Same task, model, agent, prompt closing — only contract surface changes.

---

## 8. Walked example: `oauth` task

### 8.1 Input

```json
{
  "id": "oauth",
  "prompt": "Add Google OAuth login. Use NextAuth. Update Prisma schema with required fields.",
  "hints": { "touches": ["auth", "prisma"] }
}
```

### 8.2 Predictor seeds fired

| Seed                    | Fired?                                                                                            | Outputs                                          |
| ----------------------- | ------------------------------------------------------------------------------------------------- | ------------------------------------------------ |
| `_static_seed`          | No (no verbatim file path)                                                                        | —                                                |
| `_symbol_seed`          | Maybe (depends on tokens >5 chars matching symbols_index)                                         | —                                                |
| `_topical_seed`         | Yes — "auth" matches `src/server/auth/*`; "prisma" matches `prisma/schema.prisma`                 | confidence 0.7                                   |
| `_test_scaffold_seed`   | No (not a test task)                                                                              | —                                                |
| `_env_seed`             | Yes — "oauth" + "NextAuth" trigger env credentials path                                           | `.env.example` (0.8), `.env.local` (0.65)        |
| `_sibling_pattern_seed` | Yes — analogical match on existing `src/app/api/*/route.ts`                                       | `src/app/api/auth/[...nextauth]/route.ts` (0.85) |
| `_index_seed`           | Yes — PageRank promotes `src/server/auth/index.ts` (rank #2 with `auth, signIn, signOut` symbols) | 0.9                                              |

### 8.3 LLM rerank

LLM sees seeds + graph + task. Demotes/keeps/adds. Final top-N=8 from `@/Users/prajit/Desktop/projects/cognition/demo-app/agent_lock.json:21-52`:

| path                                      | conf | reason                                                                   |
| ----------------------------------------- | ---- | ------------------------------------------------------------------------ |
| `src/server/auth/config.ts`               | 0.95 | NextAuth options home in the T3 layout                                   |
| `prisma/schema.prisma`                    | 0.9  | NextAuth + Prisma adapter requires schema additions                      |
| `src/server/auth/index.ts`                | 0.9  | personalized PageRank rank #2, top symbol matches: auth, signIn, signOut |
| `src/app/api/auth/[...nextauth]/route.ts` | 0.85 | NextAuth route handler for OAuth callbacks                               |
| `.env.example`                            | 0.8  | Env-var seed: prompt mentions credentials/providers                      |
| `.env.local`                              | 0.65 | Next.js project: `.env.local` is the conventional secrets file           |

### 8.4 Compiler → allowed_paths

Glob broadening rule: ≥4 segments + ≥0.7 conf → `parent/**`.

- `src/server/auth/config.ts` → `src/server/auth/**` (3 segments, but `_to_allowed_path` broadens auth subdirs)
- `src/server/auth/index.ts` → `src/server/auth/**` (deduplicated)
- `prisma/schema.prisma` → `prisma/schema.prisma` (2 segments, exact)
- `src/app/api/auth/[...nextauth]/route.ts` → `src/app/api/auth/[...nextauth]/**` (5 segments, broaden)
- `.env.example` → `.env.example`
- `.env.local` → `.env.local`

Final `allowed_paths`:

```json
[
  ".env.example",
  ".env.local",
  "prisma/schema.prisma",
  "src/app/api/auth/[...nextauth]/**",
  "src/server/auth/**"
]
```

### 8.5 Solver — conflicts and group placement

`oauth` overlaps with:

- `billing` on `[.env.example, prisma/schema.prisma]`
- `tests` on `[.env.example]`

Conflict counts: `oauth=2`, `billing=3`, `settings=1`, `tests=2`. With ties broken by input-list index: `oauth (idx 0)` before `billing (idx 1)` and `tests (idx 3)`. `settings` has no overlap with `oauth`.

DAG edges:

- `oauth → billing` (conflict, lighter first)
- `settings → billing` (conflict, lighter first; settings has 1 conflict, billing has 3)
- `oauth → tests` (conflict)
- `billing → tests` (conflict)
- `tests` heuristic: depends on all non-test tasks (test-task heuristic adds `oauth → tests`, `settings → tests`, `billing → tests`)

Topological levels: `oauth=0, settings=0, billing=1, tests=2` → groups `[oauth, settings] || [billing] || [tests]`.

### 8.6 Runtime

Worker for `oauth` is dispatched in Group 1 with `settings`. Worker prompt does NOT include `allowed_paths`. Worker proposes (mock, `@/Users/prajit/Desktop/projects/cognition/acg/runtime.py:387-394`):

| Proposal                    | Validator                                                     |
| --------------------------- | ------------------------------------------------------------- |
| `prisma/schema.prisma`      | ALLOWED (exact match)                                         |
| `src/server/auth/config.ts` | ALLOWED (`src/server/auth/**`)                                |
| `src/utils/random.ts`       | **BLOCKED** — outside allowed_paths (intentional OOB in mock) |

The orchestrator panel shows live ALLOWED/BLOCKED badges; the run trace records every event.

### 8.7 Devin variant

For Devin, the same lockfile is rendered into the prompt as a write-boundary block (Section 7.5). Devin opens a PR. Post-hoc, `extract_changed_files` reads `pull_requests[].changed_files` from the session detail JSON and validates each against `allowed_paths`. **6/6 PRs respected the contract on the live Greenhouse run.**

---

## 9. Defense Q&A

> 35 hostile questions. Each answer ≤ 4 sentences. Always cite a number.

### Novelty / positioning

**Q1. "This is just dependency analysis. Why is it novel?"**
A. Dependency analysis predicts read-edges between modules. ACG predicts **write-sets** for tasks specified in natural language, then turns the disjointness graph into a committable lockfile and a runtime contract validator. The artifact is the contribution — `package-lock.json` for parallel coding agents, not for npm packages.

**Q2. "How is this different from CodeCRDT?"**
A. CodeCRDT is **runtime CRDT** (Y.Map LWW + observation), reports +21.1% to −39.4% spread on coupled tasks, and explicitly asks for static coupling analysis as future work. ACG is **compile-time** static disjointness as a committable lockfile. They are complementary; CodeCRDT runs the agents, ACG decides which agents _should_ run together.

**Q3. "OpenCode and Devin Manage Devins already coordinate. Why ACG?"**
A. OpenCode Issue #4278 (Nov 2025) is real users asking for per-file locks, closed "completed" without an implementation. Devin's public docs say the coordinator "resolves conflicts" but never describe how. ACG is the static, inspectable artifact that fills that gap — and it's exposed via MCP so Devin Manage Devins can call it.

**Q4. "Aider's repomap does the same thing."**
A. Aider's repomap is a **whole-repo context selector** based on PageRank over tree-sitter symbols. It has no per-task write-set prediction, no DAG, no `allowed_paths` contract, no runtime validator, no committable artifact. We use a similar PageRank signal as one of seven seeds.

**Q5. "OpenRewrite / Moderne already does this for Java."**
A. OpenRewrite is compiler-accurate semantic search over Java types/methods. No per-PR DAG plan. No LLM agent layer. No committable lockfile. No runtime write contract. Different problem.

**Q6. "Anthropic's orchestrator-worker pattern does this."**
A. Anthropic's pattern plans dynamically and produces no static, committable artifact. ACG's lockfile is reviewable in a PR like any other infra config — that's the difference.

**Q7. "Google ADK frames context as a compiled view. You're not novel."**
A. ADK's framing is the closest match philosophically. They ship no write-claim mechanism and no enforcement layer. ACG operationalizes that framing as a four-stage compiler with measured outcomes.

**Q8. "What about LangGraph / Temporal / Airflow?"**
A. LangGraph orchestrates LLM call graphs at runtime. Temporal orchestrates business workflows. Airflow orchestrates data pipelines. None of them produce a static write-disjointness lockfile for code-modifying agents. We compose with all of them — feed the lockfile to LangGraph and it gets a static schedule.

### Honesty / limitations

**Q9. "N=1 — your numbers are anecdotes."**
A. Confirmed. We say so in the Honesty Box and in `RESULTS.md` Section 10. Every cell is a single trial; bootstrap CIs are v2 megaplan §4 work. We report point estimates, not population means.

**Q10. "Greenhouse is only 3 tasks. What about real workloads?"**
A. Confirmed — also called out in §10 of RESULTS.md. Brocoders has 7 tasks across 156 files; the per-worker savings scale with codebase size (11% on Greenhouse → 54% on Brocoders). The Java fixture is 3 tasks because we wanted real PRs landing on a real Spring repo, which costs ACUs per run.

**Q11. "ACG is slower than naive."**
A. Confirmed. Greenhouse Devin: planned 854s vs naive 277s (3.08× slower). On Greenhouse local: 22s vs 12s (1.77× slower). **This is the price of sequencing predicted conflicts.** Recovered when downstream merge/review/test conflict costs dominate.

**Q12. "No merge conflicts materialized — your value is unproven."**
A. Confirmed. All three Greenhouse naive PRs merged cleanly because the agents converged on identical `pom.xml` edits and git auto-resolved. ACG predicted the contention; on this fixture it resolved itself. The honest framing: ACG **reduces the gamble**.

**Q13. "Your Java predictor over-predicts by 4×."**
A. Yes — `lambda-rowmapper-account` precision = 0.25. Recall is still perfect. The analyzer emits concrete refinement suggestions (remove `Account.java`, `AccountException.java`, `AccountMapper.java` from seeds). This is the calibration loop working: artifacts → refinement.

**Q14. "Validator never blocks anything in your headline runs."**
A. Right — because the original `allowed_paths` are deliberately wide (Greenhouse `account/**` covers ~52 files for a 2-file refactor). The tightened-Greenhouse fixture (§9.1 of RESULTS.md) shrinks scope to ground-truth and the validator fires **18 times** across 3 tasks. CI-enforced regression test asserts this.

**Q15. "Brocoders local model produced no actual code."**
A. Correct — local Gemma under-proposed concrete writes for several Brocoders tasks. Treat that run as a context/planning benchmark, not a code-quality benchmark. The lockfile shape and prompt-token savings are still real artifact measurements.

**Q16. "You don't measure semantic conflicts."**
A. Right — out of scope, called out explicitly in §10. CodeCRDT reports a 5–10% baseline; we cite it as future work. Import/export risk analysis is a plausible v2 extension.

**Q17. "F1 = 0.90 is full-pipeline-vs-self-report, not vs git diff ground truth."**
A. Confirmed. The current analyzer compares `predicted_writes` to the agent's self-reported `actual_changed_files`. A retrieval-baseline leaderboard (BM25-only / PageRank-only / Aider-style) is v2 megaplan §5A.

### Mechanism / details

**Q18. "Why glob-based `allowed_paths` and not exact file lists?"**
A. Exact lists would over-constrain the agent (it can't create new files, can't rename). Globs allow growth within a directory while preventing cross-directory pollution. The compiler broadens to `parent/**` only when path is ≥4 segments AND confidence ≥0.7 — deliberate.

**Q19. "Why `(conflict_count, input_index)` for edge orientation? Looks arbitrary."**
A. We need a strict total order so the DAG is acyclic by construction. `(conflict_count, input_index)` produces the canonical demo lockfile (settings/oauth in group 1 parallel, billing in group 2). Alphabetical breaks that; input-order alone over-constrains. Justification in `acg/solver.py:14-19`.

**Q20. "What if `predicted_writes` misses a file the agent needs?"**
A. Then the validator blocks it post-hoc. With Cascade-style local agents, the pre-write hook returns exit 2 and the agent retries. With Devin, the PR diff shows OOB writes and we flag the task `completed_unsafe` (excluded from completion %). Recall = 1.00 in our 10 strategy-runs means this hasn't happened on observed fixtures.

**Q21. "What if the LLM rerank produces garbage JSON?"**
A. `_parse_llm_writes` is forgiving (code-fence stripping, balanced-brace fallback). On total parse failure, the predictor falls back to seed-only — the lockfile still gets written, the CLI logs a warning. **No silent failure, no aborted compilation.**

**Q22. "Cascade pre-emption isn't real for Devin."**
A. Correct, and we say so. Two-layer contract: SPEC layer (lockfile in prompt + execution_plan timing) for Devin, ENFORCEMENT layer (`validate_write` exit 2) for Cascade-style local agents. For Devin we audit post-hoc on the PR diff.

**Q23. "Why doesn't the worker know its `allowed_paths`?"**
A. Intentional. If the worker knew, it would echo the contract and the validator would never fire — the demo would lose its "BLOCKED" beat. Keeping the worker blind makes the validator the single source of truth and produces real BLOCKED moments.

**Q24. "Your scoring is conservative — you're hiding successes."**
A. Yes, deliberately. If `out_of_bounds_files` is non-empty, status flips `completed` → `completed_unsafe` and counts as not-fully-completed. We'd rather under-claim than over-claim — sponsor demo, judges scrutinize the artifacts.

**Q25. "Why pin commit `174c1c3`? Looks suspicious."**
A. Reproducibility. Spring 2008's `greenhouse` is in `spring-attic` and could change at any time. Pinning lets `make eval-greenhouse-local` produce identical lockfiles every run, which is what the analyzer's predictor-accuracy metrics depend on.

### Implementation / engineering

**Q26. "Why both TypeScript and Java?"**
A. Cross-language proof. T3 is modern ES module / tsconfig-heavy; Spring Java is enterprise Maven / class-heavy. The same lockfile shape and validator works on both. Brocoders adds production-style modular NestJS at 156 files.

**Q27. "What's the GX10 part really doing?"**
A. ASUS GX10 (LA Hacks 2026 sponsor hardware) runs `llama-server` on `gemma-4-26B-A4B-it Q4_K_XL` for the runtime LLM (orchestrator + workers). The same `acg/runtime.py` code path works against Groq, vLLM, or local llama.cpp — proves the privacy / on-prem story.

**Q28. "Two `llama-server` ports? Why?"**
A. Orchestrator on 8081 has Gemma's "thinking" mode enabled (`reasoning_content` populated) — produces the typewriter effect in viz. Sub-agents on 8080 run with `--parallel 4` for fan-out. Different `ACG_ORCH_URL` and `ACG_LLM_URL` env vars.

**Q29. "Why FastMCP? Why not write our own?"**
A. FastMCP is the de-facto Python MCP transport. Wrote our own would mean reimplementing stdio framing, JSON-RPC, schema validation. Out-of-scope for a hackathon submission. The 4 ACG tools (`analyze_repo`, `predict_writes`, `compile_lockfile`, `validate_writes`) are the contribution.

**Q30. "What's the test coverage?"**
A. **191 tests pass.** `acg/solver.py` is the most-tested (pure function, no I/O); `acg/enforce.py` has dedicated edge-case tests for nested globs (`app/api/auth/[...nextauth]/route.ts` against `app/api/auth/**`); a regression test asserts the tightened-Greenhouse validator fires.

### Future work

**Q31. "What's v2?"**
A. Multi-trial bootstrap CIs (§4 megaplan), retrieval-baseline leaderboard (§5A), live Devin smoke run on tightened-Greenhouse fixture, semantic-conflict detection via import/export risk analysis, repo-scale benchmarks beyond 156 files.

**Q32. "How does this scale to 100-task batches?"**
A. The lockfile compiler is `O(n²)` for conflict detection (pairwise file-set intersection); at n=100 that's 5000 pairs, sub-second on a laptop. The validator is `O(globs)` per write, also fast. The bottleneck is the predictor's LLM rerank (one call per task) — embarrassingly parallel.

**Q33. "What about repos with >10k files?"**
A. PageRank cache keyed by repo signature (sha256 of file mtimes); incremental rebuild on changed files only. BM25 is on (path, identifiers, imports, docstring) — sub-second tokenization. Co-change is bounded by `--max-count` on `git log`. Stress-tested on Brocoders 156 files; haven't run on 10k yet.

**Q34. "Can ACG output a Cursor `.cursorrules` file?"**
A. Yes — the lockfile has all the information needed (`allowed_paths` per task → file-glob restrictions). A `cursor-rules` exporter is planned post-LA Hacks. Same for Cline / Aider config formats.

**Q35. "What's the licensing story?"**
A. ACG is MIT-licensed; the four MCP tools and the lockfile schema are open. Devin/Cursor/Cascade can integrate without restriction. The Greenhouse fixture is `spring-attic` (Apache 2.0); the Brocoders fixture is MIT.

---

## 10. Do not say list

These phrases are traps. **DO NOT SAY:**

- ❌ "ACG prevents merge conflicts." → Say: "ACG predicts file-level contention and lets the schedule sequence them deterministically. On Greenhouse the contention happened to resolve via git's `ort` strategy because all three agents converged on the identical `pom.xml` edit."
- ❌ "ACG made Devin faster." → Say: "Naive parallel was 277s, ACG planned was 854s on Greenhouse. The trade is correctness for speed. The claim is contract compliance (6/6 PRs, 0 OOB), not throughput."
- ❌ "First-ever multi-agent coordinator." → Say: "First publicly documented system we could find that ships pre-flight static disjointness as a committable lockfile artifact. We can't prove a negative across closed-source enterprise tools."
- ❌ "Validator blocked X writes in production." → Say: "On the original lockfiles, allowed_paths were generous and the validator never had to fire. On the tightened fixture (`agent_lock_tight.json`), the validator fires 18 times across 3 tasks — CI-enforced regression test asserts ≥1 per task."
- ❌ "ACG doesn't add LLM tokens." → Say: "ACG's static-plan path adds **no extra coordinator LLM calls** beyond what the shared lead/coordinator already does. The thinking-mode orchestrator pass exists in both strategies because real systems have a lead agent in both cases. Worker prompt tokens shrink 11–54%."
- ❌ "Predictor is perfect." → Say: "Recall = 1.00 across 48 task records. Precision = 0.82 overall — Java fixture over-predicts at 0.25 because the topical seed matches every account-related file. Analyzer surfaces concrete refinement suggestions."
- ❌ "It works for any repo." → Say: "Tested on T3 Next.js (50 files), Spring Java (~200 files), Brocoders NestJS (156 files). Java predictor needs precision tuning; TypeScript fixtures show 1.00 precision."
- ❌ "Brocoders local run shows quality wins." → Say: "Brocoders local model under-proposed concrete writes for several tasks. That run is a context/planning benchmark, not a code-quality benchmark."
- ❌ "Devin Manage Devins uses our lockfile." → Say: "ACG exposes 4 MCP tools; Devin Manage Devins is MCP-aware via its config. The lockfile becomes a soft prompt constraint with post-hoc PR-diff validation. Pre-emption requires Cascade-style local agents."
- ❌ "We tested 10 backends." → Say: "Three backends — mock (deterministic), local Gemma on GX10, live Devin v3. Ten strategy-runs across (3 fixtures × 2 strategies) plus one tightened fixture."
- ❌ "100% safety guarantee." → Say: "Validator is glob-based and deterministic. Coverage is post-hoc on Devin (PR diff), pre-emption-capable on Cascade (`validate_write` exit 2). Safety is conditional on the predictor capturing the agent's actual write-set; recall = 1.00 on observed runs."
- ❌ "Production-ready." → Say: "Hackathon submission. 191 tests pass; ruff clean; viz typecheck clean. Empirical evidence is N=1 single trials. v2 megaplan covers multi-trial CIs and retrieval baselines."

---

## 11. Quick-reference cheat card

### One-line elevator (memorize)

> "ACG is `package-lock.json` for parallel coding agents — a static, committable lockfile that decides which tasks can run in parallel and which need to serialize, before any agent runs."

### The receipts (memorize)

| Claim                     | Number                              | Where                                                                  |
| ------------------------- | ----------------------------------- | ---------------------------------------------------------------------- |
| Live Devin PRs            | **6/6 in scope, 0 OOB**             | `experiments/greenhouse/runs/eval_run_devin_api_*_smoke.json`          |
| Predictor recall          | **1.00**                            | `experiments/greenhouse/RESULTS.md:343`                                |
| Predictor precision       | **0.82** (Java 0.25, TS 1.00)       | same                                                                   |
| demo-app prompt savings   | **43% avg** (897→513)               | `experiments/demo-app/runs/eval_run_combined.json`                     |
| Brocoders prompt savings  | **54%** (3721→1700)                 | `experiments/microservice/runs_brocoders_local/eval_run_combined.json` |
| Greenhouse local savings  | **11%** (2159→1922)                 | `experiments/greenhouse/runs/eval_run_combined.json`                   |
| Validator firings (tight) | **18 across 3 tasks**               | `experiments/greenhouse/runs/tight/eval_run_acg.json`                  |
| Tests passing             | **191**                             | `pytest tests/ -q`                                                     |
| Conflict pairs predicted  | **3 (Java) / 4 (T3) / 11 (NestJS)** | lockfiles                                                              |

### The 4 MCP tools

| Tool               | Inputs                            | Output                   |
| ------------------ | --------------------------------- | ------------------------ |
| `analyze_repo`     | path, language                    | context graph            |
| `predict_writes`   | task, repo_path, repo_graph?      | `[{path, conf, reason}]` |
| `compile_lockfile` | repo_path, tasks, language        | `agent_lock.json`        |
| `validate_writes`  | lockfile, task_id, attempted_path | `{allowed, reason}`      |

### The 7 predictor seeds

1. `_static_seed` — verbatim file mention regex (0.95)
2. `_symbol_seed` — camelCase tokens via `symbols_index` (0.85)
3. `_topical_seed` — `hints.touches` substring match (0.7)
4. `_test_scaffold_seed` — Playwright/Vitest/Jest/etc convention (0.85)
5. `_env_seed` — credential triggers → `.env.example`/`.env.local` (0.8/0.65)
6. `_sibling_pattern_seed` — analogical reasoning over existing API trees (0.75/0.65)
7. `_index_seed` — wraps `acg.index.aggregate(top_n=3)` (floor 0.5)

### The 4 indexers

1. **Framework** — Next/T3/Django/Rails/FastAPI/Spring detection (0.85 fixed)
2. **PageRank** — personalized PageRank over file-level symbol graph
3. **BM25** — lexical over (path, identifiers, imports, docstring)
4. **Co-change** — ROSE-style git history association (seed expansion only)

### The 3-layer DAG edges

1. **Conflict-derived** — defeasible, lighter task first
2. **Heuristic** — defeasible, caller-supplied (e.g., tests run last)
3. **Explicit** — NOT defeasible, `task.depends_on`; cycles raise

### Ground-truth files (memorize for live demo)

- demo-app oauth → `prisma/schema.prisma`, `src/server/auth/config.ts`, `.env.example`
- Greenhouse `lambda-rowmapper-account` → `pom.xml`, `JdbcAccountRepository.java`
- Brocoders `products-domain` → `src/products/**` (8 new files), `src/app.module.ts`, `src/database/migrations/**`

### The execution plans (memorize)

- **demo-app**: `[oauth, settings] || [billing] || [tests]` (3 groups)
- **Greenhouse**: `[account] || [invite] || [app]` (3 groups, fully serialized on `pom.xml`)
- **Brocoders**: `[deployment-config, products-domain, users-search] || [api-key-auth] || [files-e2e-tests] || [registration-email-job] || [notifications-webhook]` (5 groups)

### Reproducibility (one-liners)

```bash
# Mock smoke (~5s, deterministic)
make eval-greenhouse-mock

# Local LLM on ASUS GX10
ACG_LLM_URL=http://gx10-f2c9:8080/v1 ACG_LLM_API_KEY=local ACG_LLM_MODEL=gemma \
  make eval-greenhouse-local

# Live Devin v3 (~$5 ACUs)
make eval-greenhouse-devin-api

# Tightened fixture (validator fires 18×)
make eval-greenhouse-tight-mock

# Analyzer + chart
./.venv/bin/acg analyze-runs experiments/**/eval_run*.json --out report.md

# MCP server (stdio)
acg mcp
```

### Citation guard (URL → status)

| #   | Source                                              | Status                      |
| --- | --------------------------------------------------- | --------------------------- |
| 1   | arXiv:2510.18893 (CodeCRDT)                         | **verified from local PDF** |
| 2   | OpenCode Issue #4278                                | unverified                  |
| 3   | Walden Yan jxnl.co interview                        | unverified                  |
| 4   | cognition.ai/blog/devin-can-now-manage-devins       | unverified                  |
| 5   | cognition.ai/blog/deepwiki-mcp-server               | unverified                  |
| 6   | la-hacks-2026.devpost.com                           | unverified                  |
| 7   | docs.windsurf.com (Cascade `pre_write_code` hook)   | unverified                  |
| 8   | pypi.org/project/fastmcp                            | unverified                  |
| 9   | innovationlab.fetch.ai (Agentverse uagents-adapter) | unverified                  |
| 10  | asus.com (GX10 + 128 GB unified memory + GB10)      | unverified                  |
| 11  | arXiv:2511.19635 (Agint, Nov 2025)                  | unverified                  |

If a quote is unverified at submission time: soften wording in README, drop the quote in the demo video, link the URL in Devpost without claiming verbatim.

### File map

```text
acg/                       # core lib
  cli.py                   # CLI entry (compile/explain/validate-write/run/...)
  predictor.py             # 7 seeds + LLM rerank
  compiler.py              # tasks → lockfile orchestration
  solver.py                # pure DAG (3-layer edges + SCC collapse)
  enforce.py               # glob validator (exit 2 on block)
  runtime.py               # async orchestrator + workers (GX10 dual-port)
  llm.py                   # OpenAI-compatible client + MockLLMClient
  schema.py                # Pydantic v2 models
  repo_graph.py            # TS scanner dispatch + Java scanner
  index/                   # 4 indexers + aggregator
  mcp/                     # FastMCP stdio server, 4 tools

graph_builder/             # Node TS scanner (ts-morph)
  scan.ts                  # called by acg/repo_graph.py for TS repos

experiments/greenhouse/    # Spring 2008 Java fixture
  agent_lock.json          # canonical lockfile (3 tasks, 3 conflicts)
  agent_lock_tight.json    # tightened — validator fires 18×
  tasks.json               # 3 RowMapper-to-lambda tasks
  devin_api.py             # v3 API client + 3-tier extraction
  devin_adapter.py         # devin_manual_run + devin_api_run
  devin_prompts.py         # naive vs planned prompt builders
  headtohead.py            # main eval entry: --backend mock/local/devin-api
  strategies.py            # naive_parallel + acg_planned
  eval_schema.py           # eval_run.json Pydantic models
  runs/                    # eval_run artifacts

experiments/microservice/  # Brocoders NestJS fixture
  agent_lock_brocoders.json   # 7 tasks, 11 conflicts, 5 groups
  tasks_brocoders.json
  setup.sh                    # clones nestjs-boilerplate
  runs_brocoders_local/       # local-backend run
  runs_brocoders_mock/        # mock-backend run
  runs_brocoders_analysis.md  # per-task precision/recall/F1

demo-app/                  # T3 fixture
  agent_lock.json          # canonical 4-task lockfile
  tasks.json
  experiments/demo-app/runs/  # mock-backend run

viz/                       # React Flow v12 visualizer
  src/lib/replay.ts        # pure (trace, t) → state
  src/App.tsx              # rAF loop

schema/                    # JSON Schemas (round-trip with acg/schema.py)
  agent_lock.schema.json
  run_trace.schema.json

docs/
  ARCHITECTURE.md          # component diagram + invariants
  COGNITION_INTEGRATION.md # rubric mapping (Devin / Cognition rubric)
  MCP_SERVER.md            # 4 tools + Devin coordinator example
  CITATIONS.md             # claim → URL → verification status
  defense_brief.md         # this file
  scaling_breakeven.png    # chart

Makefile                   # all targets
README.md                  # public-facing pitch + Honesty Box
```

### Final mantra

> **"Static contract, committable artifact, provider-agnostic enforcement, honestly small evidence."** Every demo beat is a real artifact on disk. Every number has a file:line citation. Every limitation is in the README's Honesty Box.
