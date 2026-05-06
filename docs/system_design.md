# ACG System & Architectural Design

> The full system and architectural design of the Agent Context Graph (ACG) project, framed against the two dominant schools of multi-agent code generation. Both burn tokens. ACG fixes both with a static, committable write-contract.

---

## Table of contents

1. [The problem: two schools, both wasteful](#1-the-problem-two-schools-both-wasteful)
2. [School A — Centralized orchestrator (Devin "Manage Devins")](#2-school-a--centralized-orchestrator-devin-manage-devins)
3. [School B — Peer-to-peer message passing (Claude Code / CRDT extreme)](#3-school-b--peer-to-peer-message-passing-claude-code--crdt-extreme)
4. [Token waste — concrete math](#4-token-waste--concrete-math)
5. [The ACG fix — static write-contract](#5-the-acg-fix--static-write-contract)
6. [System design — eight layers](#6-system-design--eight-layers)
7. [Architectural diagram (full)](#7-architectural-diagram-full)
8. [Sequence diagrams — same task, three schools](#8-sequence-diagrams--same-task-three-schools)
9. [Why this works (invariants & complexity)](#9-why-this-works-invariants--complexity)

---

## 1. The problem: two schools, both wasteful

When you fan out a coding job to N parallel agents, you must answer one question: **what guarantees that two agents don't write the same file at the same time?**

Today's answer falls into one of two camps:

| School                          | Topology                              | Coordination signal                                    | Token cost                                  |
| ------------------------------- | ------------------------------------- | ------------------------------------------------------ | ------------------------------------------- |
| **A. Centralized orchestrator** | hub-and-spoke                         | orchestrator polls workers + re-dispatches             | O(rounds × workers × full-state-replay)     |
| **B. Peer-to-peer / CRDT**      | mesh                                  | each worker observes every other worker                | O(workers² × edits × context)               |
| **C. ACG (this project)**       | feed-forward DAG with static contract | compile-time write-set disjointness, runtime validator | O(workers + 1) compile, O(1) per worker run |

Schools A and B have a structural problem in common: **they treat the conflict question as a runtime decision.** Every round, every agent, the model re-derives the same `who can touch what` answer from a slightly different context — and pays for it in tokens.

ACG asks the question **once**, in a deterministic compiler, and emits the answer as a committable artifact (`agent_lock.json`) that every agent reads exactly once.

---

## 2. School A — Centralized orchestrator (Devin "Manage Devins")

### 2.1 The pattern

A single orchestrator agent holds the master plan. It dispatches tasks to sub-agents, polls them, reads their reports, decides on the next dispatch, and resolves conflicts as they surface.

Examples in the wild:

- **Devin "Manage Devins"** — a parent Devin spawns child Devins, monitors them
- **LangGraph supervisor pattern** — `supervisor` node routes to worker nodes
- **OpenAI Swarm** — handoffs between agents via shared context
- **Anthropic orchestrator-worker** — orchestrator decomposes the task, workers execute, orchestrator merges
- **AutoGPT / BabyAGI** — single planner agent

### 2.2 Diagram

```text
                         ┌───────────────────────┐
                         │     Orchestrator      │
                         │                       │
                         │  context window:      │  ← grows EVERY round
                         │  • full task list     │
                         │  • W1 progress        │
                         │  • W2 progress        │
                         │  • W3 progress        │
                         │  • conflicts so far   │
                         │  • files seen so far  │
                         │  ...                  │
                         └───────────────────────┘
                              ↑↓     ↑↓     ↑↓     ↑↓
                              ↑↓     ↑↓     ↑↓     ↑↓     re-dispatch
                              ↑↓     ↑↓     ↑↓     ↑↓     re-paste full
                              ↑↓     ↑↓     ↑↓     ↑↓     state per round
                         ┌───┐  ┌───┐  ┌───┐  ┌───┐
                         │W1 │  │W2 │  │W3 │  │W4 │
                         └───┘  └───┘  └───┘  └───┘
                           ↓     ↓      ↓      ↓
                         repo files (collisions discovered LATE)

Token cost per round:  rounds × (orchestrator_full_state) + Σ(worker_context)
                       ≈ R × (5–20K) + N × 2K
```

### 2.3 Why it wastes tokens

1. **The orchestrator's context only grows.** Every poll adds another worker report. Every dispatch re-pastes the task list, the conflict log, the progress so far. By round 5 you're paying for round 1's tokens five times.
2. **Implicit-decision blindness.** Walden Yan (jxnl.co interview, Sep 2025): _"lots of actions carry these implicit decisions… you might just get conflicting decisions."_ The orchestrator can't see what each worker decided implicitly inside its context window — it only sees the outputs.
3. **Bottleneck.** "Manage Devins" parents handle every child's report. Past ~5–10 children the parent's context window saturates and decisions degrade.
4. **Late conflict detection.** Worker A and worker B both edit `pom.xml`. The orchestrator finds out when both PRs come back. Now it has to roll one back, re-prompt, re-pay.

### 2.4 Symptom in real systems

Cognition's own public docs say the coordinator "resolves conflicts" but never describe how. The "Manage Devins" blog explicitly leaves the conflict-resolution policy as an exercise for the parent agent. That hand-wave is the cost.

---

## 3. School B — Peer-to-peer message passing (Claude Code / CRDT extreme)

### 3.1 The pattern

No central authority. Agents observe each other's edits in real time and react. When two agents touch the same character, a CRDT merge rule (last-write-wins, observed-remove) decides the outcome. When two agents disagree on direction, they retry, re-plan, re-read.

Examples in the wild:

- **CodeCRDT** (arXiv:2510.18893, Oct 2025) — Y.Map LWW + observation; explicit research artifact
- **OpenCode multi-agent mode** (Issue #4278, Nov 2025) — real users asking for per-file locks, currently unimplemented
- **Cursor agent-to-agent handoffs**
- **MetaGPT collaborative coding**
- **Claude Code's `--watch` style** where one agent re-reads diffs as another writes

### 3.2 Diagram

```text
                  observe   observe   observe   observe
              ┌────W1────┐  ┌────W2────┐
              │ writes    ├──┤  writes  │
              │ context:  │  │ context: │
              │ +W2 obs   │  │ +W1 obs  │
              │ +W3 obs   │  │ +W3 obs  │
              │ +W4 obs   │  │ +W4 obs  │
              └─────┬─────┘  └─────┬────┘
                    │  ╲      ╱    │       n×(n-1) edges
                    │    ╲  ╱      │       observation flow
                    │     ╲╱       │
                    │     ╱╲       │
                    │   ╱    ╲     │
              ┌─────┴───┐  ┌──┴───────┐
              │ W3      ├──┤  W4      │
              │ context:│  │ context: │
              │ +W1 obs │  │ +W1 obs  │
              │ +W2 obs │  │ +W2 obs  │
              │ +W4 obs │  │ +W3 obs  │
              └─────────┘  └──────────┘
                   ↓    ↓    ↓    ↓
         repo files (CRDT merge: LWW; conflicts → RETRY)

Token cost per round: workers × (workers - 1) × delta_size
                      + retries × full_redo_per_task
```

### 3.3 Why it wastes tokens

1. **O(n²) observation.** With 4 workers, each worker reads 3 other workers' edits → 12 cross-observations per round. With 10 workers it's 90.
2. **Conflicts surface at runtime, not compile time.** CRDT can resolve character-level merges deterministically, but it can't tell W1 not to redesign the API while W2 implements consumers of the old one. CodeCRDT measured +21% speedup to **−39% slowdown** spread on coupled tasks.
3. **Retry tax.** When the merge produces semantic garbage, agents redo work. Each redo is a full task re-run.
4. **No shared static context.** Every agent re-derives "what files matter for my task" because it has no committed plan to read. That's the same BM25/PageRank work, paid for N times.
5. **Variance kills throughput.** A team can't ship if their median improvement is +5% but their P95 is −30%.

### 3.4 Symptom in real systems

CodeCRDT's own future-work section explicitly asks for "static analysis of task coupling to predict conflicts before execution" — that is verbatim what ACG does. The CRDT layer is necessary but not sufficient.

---

## 4. Token waste — concrete math

Concrete worked example: **5 tasks, 4 worker agents, 3 dispatch rounds, 1000 token average task context.**

### 4.1 School A (centralized orchestrator)

| Cost component                  | Per round                                        | × Rounds | Total               |
| ------------------------------- | ------------------------------------------------ | -------- | ------------------- |
| Orchestrator full-state context | 5 tasks × 1000 + 4 workers × 500 progress = 7000 | × 3      | **21,000**          |
| Worker context (each round)     | 4 × 1500 (task + relevant context)               | × 3      | **18,000**          |
| Conflict resolution re-prompts  | ~2 conflicts × 3000 token re-paste               | —        | **6,000**           |
| **TOTAL**                       |                                                  |          | **≈ 45,000 tokens** |

### 4.2 School B (peer-to-peer CRDT)

| Cost component                             | Per round                  | × Rounds | Total               |
| ------------------------------------------ | -------------------------- | -------- | ------------------- |
| Per-worker base context                    | 4 × 1500                   | × 3      | **18,000**          |
| Cross-worker observation (n×(n−1))         | 4 × 3 × 400 (delta) = 4800 | × 3      | **14,400**          |
| Retry tax (CodeCRDT ~2 retries on coupled) | 2 × 1500 full redo         | —        | **3,000**           |
| Semantic-conflict re-plan                  | ~1 task × 3000             | —        | **3,000**           |
| **TOTAL**                                  |                            |          | **≈ 38,400 tokens** |

### 4.3 School C (ACG static contract)

| Cost component                                                      | Calls     | Token                        | Total              |
| ------------------------------------------------------------------- | --------- | ---------------------------- | ------------------ |
| Predictor LLM rerank (1 call/task)                                  | 5         | ≈ 800 (graph + task + seeds) | **4,000**          |
| Compiler / Solver (deterministic)                                   | —         | 0                            | **0**              |
| Worker run (1 call/task, allowed_paths in prompt)                   | 5         | ≈ 400 (task + boundary)      | **2,000**          |
| Validator (deterministic glob match)                                | per write | 0                            | **0**              |
| Optional thinking-pass orchestrator (single call, lockfile summary) | 1         | ≈ 500                        | **500**            |
| **TOTAL**                                                           |           |                              | **≈ 6,500 tokens** |

### 4.4 The reduction

| School                      | Tokens | vs ACG   |
| --------------------------- | ------ | -------- |
| A. Centralized orchestrator | 45,000 | **6.9×** |
| B. Peer-to-peer CRDT        | 38,400 | **5.9×** |
| C. ACG                      | 6,500  | 1.0×     |

This matches the empirical evidence in the project:

- **demo-app per-worker prompt savings: 43%** (897 → 513) — `experiments/demo-app/runs/eval_run_combined.json`
- **Brocoders per-worker prompt savings: 54%** (3721 → 1700) — `experiments/microservice/runs_brocoders_local/eval_run_combined.json`
- **Greenhouse per-worker prompt savings: 11%** (2159 → 1922) — `experiments/greenhouse/runs/eval_run_combined.json`

The Greenhouse number is small because the canonical lockfile uses generous `allowed_paths`. The Brocoders number is large because the lockfile carves a 156-file repo into per-task slices.

---

## 5. The ACG fix — static write-contract

### 5.1 The core idea (one paragraph)

> **Move the conflict question from runtime to compile time.** Predict each task's write-set with a deterministic seed pipeline plus an LLM rerank. Compute pairwise file-set intersections. Emit a committable lockfile whose `allowed_paths` define a per-task glob contract and whose `execution_plan` schedules parallel and serial groups so disjointness is structural, not negotiated. At runtime, every proposed write is checked against the lockfile by a deterministic glob matcher — no LLM call, no peer observation, no orchestrator round-trip.

### 5.2 Diagram

```text
┌─────────────────────── COMPILE TIME (once, deterministic + 1 LLM/task) ───────────────────────┐
│                                                                                                │
│   repo + tasks.json                                                                            │
│         │                                                                                      │
│         ▼                                                                                      │
│   [scan]──[indexers]──[predictor]──[compiler]──[solver]──▶ agent_lock.json (committable)       │
│   deterministic   det.    1 LLM/task   det.       det.        ↑                                │
│                                                                │                               │
│                                                          reviewed in PR                        │
│                                                          like any infra config                 │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
                                          │
                                          ▼
┌─────────────────────────── RUN TIME (parallel by group, no peer comms) ───────────────────────┐
│                                                                                                │
│   group 1 (parallel)            group 2 (after group 1)         group 3 (after group 2)        │
│   ┌──┐ ┌──┐                     ┌──┐                            ┌──┐                           │
│   │W1│ │W2│   ← read only       │W3│   ← read only              │W4│  ← read only             │
│   └──┘ └──┘     allowed_paths   └──┘     allowed_paths          └──┘    allowed_paths          │
│     ↓    ↓        block in        ↓        block in               ↓       block in             │
│   [validate_write — deterministic glob match — exit 0/1/2 — NO LLM]                            │
│     ↓    ↓                        ↓                               ↓                            │
│   repo files (disjoint by construction; OOB writes blocked or flagged completed_unsafe)       │
│                                                                                                │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 5.3 Why this beats both schools

| Property                    | School A (orch)           | School B (CRDT)              | School C (ACG)                              |
| --------------------------- | ------------------------- | ---------------------------- | ------------------------------------------- |
| Conflict detection          | runtime, late             | runtime, late                | **compile time, early**                     |
| Coordination tokens / round | grows per round           | grows quadratic with workers | **zero at runtime**                         |
| Worker context size         | full task list + progress | own task + n−1 peer obs      | **own task + own allowed_paths**            |
| Determinism                 | low (orchestrator LLM)    | low (merge timing)           | **high (validator is glob match)**          |
| Reviewable artifact         | none                      | none                         | **agent_lock.json** in git                  |
| Failure mode                | orch context saturates    | retry storms / variance      | predictor miss → validator catches post-hoc |
| Pre-emption                 | LLM call                  | not really                   | **glob match, exit 2**                      |

### 5.4 What we do NOT claim

- ACG does **not** prevent semantic drift between tasks (e.g., task A renames a function while task B uses the old name). That's CodeCRDT's lane. We compose with it.
- ACG does **not** replace orchestration runtimes (LangGraph, Temporal, Airflow). We provide the schedule and contract; they execute.
- ACG cannot pre-empt a black-box agent like Devin. For Devin we audit post-hoc on PR diff. For Cascade-style local agents the validator can pre-empt at edit time.

---

## 6. System design — eight layers

Each layer has a single responsibility, a single output type, and a clear determinism property.

### Layer 1 — Static analysis (deterministic, offline, no LLM)

| Module                   | Role                                                                                                                   | Output                           |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------- | -------------------------------- |
| `acg/repo_graph.py`      | Dispatch to TS scanner (`graph_builder/scan.ts`) or Java scanner (in-process tree-sitter). Detect language. Normalize. | `<repo>/.acg/context_graph.json` |
| `acg/index/framework.py` | Detect Next/T3/Django/Rails/FastAPI/Spring Boot/Vite from manifests; map roles to canonical paths                      | role → path map (conf 0.85)      |
| `acg/index/pagerank.py`  | Personalized PageRank over file-level symbol graph (imports + identifier refs); cached by repo signature               | top-N paths with PR rank         |
| `acg/index/bm25.py`      | BM25 lexical over (path, identifiers, imports, docstring); synonym table                                               | top-N paths with BM25 score      |
| `acg/index/cochange.py`  | ROSE-style git history association (`git log --name-only`); seed expansion only; min 3 commits                         | co-occurring paths               |
| `acg/index/aggregate.py` | Fuse framework + PR + BM25 by max-conf per path; cochange seeded from top fused                                        | unified `[(path, conf, reason)]` |

**Determinism.** Fully deterministic given the same repo state. PageRank is cached and incrementally invalidated by file mtimes.

**The 4 indexers in plain English:**

- **Framework** — _"what kind of project is this?"_ Reads `package.json`, `pom.xml`, `pyproject.toml`, `Gemfile` and recognizes Next.js / T3 / Django / Rails / FastAPI / Spring Boot / Vite. For each task role ("add an API route", "add a page", "add a model") it knows the _canonical_ place to put it (e.g., Next.js routes go in `src/app/api/<name>/route.ts`). Confidence fixed at 0.85 because conventions are reliable.
- **PageRank** — _"which files is everyone else importing?"_ Builds a graph where nodes are files and edges are imports. Runs personalized PageRank biased by the task's keywords. Files like `src/server/db.ts` (imported by 30 things) score high. Caches the graph keyed by file mtimes so repeat runs are instant.
- **BM25** — _"which files mention the same words as the task?"_ Classic search-engine ranking. Tokenizes path, identifiers, imports, and the first line of the docstring; scores against the task prompt. Has a synonym table so "login" matches "auth" and "nav" matches "sidebar".
- **Co-change** — _"which files change together in git history?"_ Runs `git log --name-only` on a seed file and counts how often other files appear in the same commit. If `auth/config.ts` and `prisma/schema.prisma` were edited together in 6 of the last 100 commits, they're related — predict them together. Capped at 0.8 confidence and ignored unless ≥3 commits, so noise doesn't dominate.

### Layer 2 — Predictor (1 LLM call per task)

`acg/predictor.py` — **the only LLM call in the compile path.** Seven deterministic seeds + one rerank.

| Seed                    | Trigger                                                                     | Default conf |
| ----------------------- | --------------------------------------------------------------------------- | ------------ |
| `_static_seed`          | verbatim file mention regex                                                 | 0.95         |
| `_symbol_seed`          | camelCase tokens >5 chars resolved via `symbols_index`                      | 0.85         |
| `_topical_seed`         | `task.hints.touches[]` substring match                                      | 0.7          |
| `_test_scaffold_seed`   | Playwright/Vitest/Jest/Cypress/pytest convention                            | 0.85         |
| `_env_seed`             | "oauth"/"stripe"/"auth0"/"clerk"/"nextauth" → `.env.example` + `.env.local` | 0.8 / 0.65   |
| `_sibling_pattern_seed` | "add API/endpoint/route" + analogical reasoning over existing API trees     | 0.75 / 0.65  |
| `_index_seed`           | wraps `acg.index.aggregate(top_n=3)`                                        | floor 0.5    |

The LLM rerank sees the task + filtered top-50 graph + existing seeds and may keep, demote, remove, or add files. **On any LLM failure (transport, JSON parse) the predictor falls back to seeds — never aborts compilation** (`@/Users/prajit/Desktop/projects/cognition/acg/predictor.py:663-672`).

Output cap: `MAX_PREDICTIONS = 8` per task.

**The 7 seeds in plain English:**

1. **`_static_seed`** — _"the prompt literally typed a file path."_ If the user wrote `prisma/schema.prisma` in the prompt, that file is obviously going to change. Confidence 0.95 because the user told you so.
2. **`_symbol_seed`** — _"the prompt mentioned a function name."_ User wrote `getCurrentUser` — look it up in our index of every symbol in the repo and add the file that defines it. Only triggers on camelCase tokens >5 chars to avoid noise on words like "add" or "page". Confidence 0.85.
3. **`_topical_seed`** — _"the task hint says it touches X."_ User passed `hints: { touches: ['auth'] }` → match every path containing "auth". Broadest signal, lowest trust at 0.7.
4. **`_test_scaffold_seed`** — _"this is a test task — predict the spec file even if it doesn't exist."_ Detects Playwright/Jest/Vitest/Cypress/pytest from the prompt and synthesizes the conventional spec path (e.g., `tests/e2e/checkout.spec.ts`). Confidence 0.85.
5. **`_env_seed`** — _"agents always extend `.env.example` for credentialed integrations."_ Triggers on "oauth", "stripe", "auth0", "clerk", "nextauth". Confidence 0.8 for `.env.example`, 0.65 for `.env.local` (probably needed but less certain).
6. **`_sibling_pattern_seed`** — _"learn the pattern from existing siblings."_ If the user says "add a billing API" and the repo already has `src/app/api/auth/route.ts` and `src/app/api/users/route.ts`, the pattern is `src/app/api/<thing>/route.ts`. Substitute "billing". Confidence 0.75.
7. **`_index_seed`** — _"trust the deterministic indexers' top 3."_ Wraps the framework + PageRank + BM25 + co-change aggregator and takes its top 3 fused predictions. Confidence floor 0.5 because they ranked it, so it's at least plausible.

**The caps in plain English:**

- **`MAX_PREDICTIONS = 8`** per task — keeps the lockfile human-readable. Even if seeds + LLM produce 30 predictions, only the top 8 by confidence make it. Beyond 8 the precision falls off a cliff.
- **`SEED_INDEX_TOP_N = 3`** — the indexers (especially PageRank) are _so_ good at finding hot files that without this cap, every task would predict the same 5 high-PageRank files (auth, db, router). Capping at 3 leaves room for task-specific predictions.
- **`TOP_GRAPH_FILES_FOR_LLM = 50`** — the LLM rerank gets the top 50 most-relevant files in its prompt, not the entire repo. A 1000-file codebase would blow the LLM's context window; 50 is enough signal at a sane token cost.

### Layer 3 — Compiler (deterministic)

`acg/compiler.py` — orchestrates the predictor over each task, builds `allowed_paths`, applies the test heuristic.

- **Glob broadening:** if path ≥ 4 segments AND confidence ≥ 0.7, broaden to `parent/**`. Test paths broaden at ≥ 3 segments. So `src/server/auth/config.ts` → `src/server/auth/**` but `src/server/x.ts` stays exact.
- **Test-task heuristic:** tasks with `hints.touches` containing `tests`/`test`/`e2e`/`playwright` get heuristic deps on every non-test task. Defeasible (SCC collapse can override).

**What "≥4 segments" means in plain English:** A path's _segments_ are the chunks between slashes. Count them:

| Path                                      | Segments                                | Count            |
| ----------------------------------------- | --------------------------------------- | ---------------- |
| `prisma/schema.prisma`                    | `prisma` / `schema.prisma`              | **2**            |
| `src/server/x.ts`                         | `src` / `server` / `x.ts`               | **3**            |
| `src/server/auth/config.ts`               | `src` / `server` / `auth` / `config.ts` | **4** ← broadens |
| `src/app/api/auth/[...nextauth]/route.ts` | 6                                       | **6** ← broadens |

**Why broaden at ≥4 + conf ≥0.7:** Deeper paths usually live inside a _feature directory_. If `oauth` is predicted to write `src/server/auth/config.ts`, it's probably also going to write `src/server/auth/index.ts` or `src/server/auth/providers.ts`. Broadening to `src/server/auth/**` lets the agent grow naturally inside the auth feature without escaping into `src/server/db.ts` (a different feature). The confidence gate prevents broadening on speculative guesses.

### Layer 4 — Solver (deterministic, pure function)

`acg/solver.py` — no I/O, no LLM, no globals. The most-tested module.

**Edge orientation rule:** `(conflict_count, input_index)` is a strict total order. Predecessor of every conflict pair = lighter task (fewer conflicts), tie-break by input-list index (`@/Users/prajit/Desktop/projects/cognition/acg/solver.py:14-19`).

**3-layer edge model:**

1. **Conflict-derived** (defeasible) — lighter first
2. **Heuristic** (defeasible) — caller-supplied (e.g., tests last)
3. **Explicit** (NOT defeasible) — `task.depends_on`; cycles raise

**SCC collapse** between layers 2 and 3: if defeasible edges form a cycle, replace internal edges with strict input-order chain.

**`topological_groups`:** node level = longest path from any source. Same level → same group. Multi-node → `parallel`; single → `parallel` (level 0) or `serial`. Group ids dense 1..N.

**The whole solver in plain English:** It's a pure math function — give it tasks, get back a schedule. Two questions to answer:

1. _"Which pairs of tasks collide?"_ — for every pair, intersect their `predicted_writes` paths. If non-empty, they collide.
2. _"What order resolves the collisions while keeping non-colliding tasks parallel?"_ — build a DAG, topologically sort it, group by level.

The "3 layers of edges" mean three kinds of ordering rules, in priority order:

- **Conflict-derived (defeasible)** — _"if A and B both write `pom.xml`, one goes first."_ We pick the lighter one (fewer total conflicts). "Defeasible" = the solver can override this if a stronger rule disagrees.
- **Heuristic (defeasible)** — _"compiler-added rules of thumb."_ The current one: "tests run after the code they test." Also overridable.
- **Explicit (NOT defeasible)** — _"the user wrote `depends_on: [auth]` in tasks.json. That's a hard rule."_ If these form a cycle, the solver crashes.

**SCC collapse in plain English:** _"if soft rules accidentally form a circle (A → B → A), don't crash. Just serialize them in input order and move on."_ Only hard user-declared cycles raise.

**Topological groups in plain English:** _"after building the DAG, every task gets a level number — its longest distance from any starting point. Tasks at the same level can run in parallel; level 1 waits for level 0 to finish; etc."_ That's our parallel/serial schedule.

### Layer 5 — Lockfile artifact (committable)

`agent_lock.json`, validated by Pydantic v2 (`acg/schema.py`) and JSON Schema (`schema/agent_lock.schema.json`). Strict mode: `extra="forbid"`.

```text
AgentLock {
  version: "1.0" const
  generated_at: datetime UTC
  generator: { tool, version, model? }
  repo: { root, git_url?, commit?, languages: [...] }
  tasks: [Task { id, prompt, predicted_writes[], allowed_paths[], depends_on[], parallel_group? }]
  execution_plan: { groups: [Group { id, tasks[], type: parallel|serial, waits_for[] }] }
  conflicts_detected: [Conflict { files[], between_tasks[], resolution }]
}
```

**This is the deliverable.** Reviewable in a PR like any other infra config. Diffable. Versionable. Signable.

### Layer 6 — Runtime (orchestrator + workers)

`acg/runtime.py` — async, validator-gated.

- **Orchestrator** (`run_orchestrator`) — single thinking-pass call against `llama-server` on port 8081. Lockfile summary → JSON dispatch decision (`approved`, `concerns`, `dispatch_order`). **Never blocks on parse failure** — lockfile is source of truth.
- **Workers** (`run_worker`) — propose writes, validate every one. Worker prompt does NOT include `allowed_paths` (`@/Users/prajit/Desktop/projects/cognition/acg/runtime.py:660-663`) — keeps validator honest, produces real BLOCKED moments.
- **Concurrency lanes** (`run_group`) — sequential / semaphore-bounded gather / unbounded gather.
- **`MockRuntimeLLM`** — deterministic offline; intentionally mixes in-bounds + one OOB proposal per task so the run trace exercises both validator outcomes.

**The runtime in plain English:** This is what actually runs the lockfile against real LLMs. Two server roles, both `llama-server` instances on the GX10:

- **Orchestrator (port 8081)** — _one big "thinking" call._ Reads the lockfile, says "yep, looks good, dispatch in this order." Mostly cosmetic — the lockfile is already correct from compile time — but it produces the visible `reasoning_content` stream that the viz panel types out.
- **Workers (port 8080)** — _the actual coding agents._ One LLM call per task. Each worker gets only its own task prompt and the top 30 most-imported files in the repo (just enough context to be grounded). **Crucially, the worker is NOT told its `allowed_paths`** — we want the validator to be the single source of truth, not the worker's self-restraint. If a worker proposes an out-of-bounds write, we want to _see_ the BLOCKED badge fire.

**Concurrency lanes (3 ways to fan out workers):**

- `sequential` — one worker at a time. Debug mode.
- `semaphore-bounded gather` — N workers in parallel, capped (e.g., 2 at a time on a small GPU).
- `unbounded gather` — fire every worker in the group at once. Only safe when `llama-server` is started with `--parallel 4` (it already is).

**`MockRuntimeLLM`** is the offline stand-in — no GX10 needed. It pretends to be the model but returns canned proposals. Crucially it's _crafted_ to include one out-of-bounds write per task so the demo always shows both ALLOWED and BLOCKED badges firing.

### Layer 7 — Enforcement (deterministic, no LLM)

`acg/enforce.py` — glob-based write validator.

```text
validate_write(lock, task_id, write_path) → (allowed: bool, reason: str)
exit 0 = allowed, exit 1 = user error, exit 2 = blocked
```

**`_matches`** supports POSIX `**`:

- `parent/**` matches `parent` and any descendant
- `a/**/b` translates to "contains `a` then later `b` in order"
- Else `fnmatch.fnmatch`

**Two delivery modes:**

- **Pre-emption (Cascade-style local agents):** `pre_write_code` hook calls `validate_write`; exit 2 cancels the write
- **Post-hoc (Devin / Cursor / hosted agents):** PR diff scored against `allowed_paths`; OOB → status flips `completed_unsafe`

### Layer 8 — Integration surface

| Surface                        | File                | Purpose                                                                                                                                                 |
| ------------------------------ | ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **MCP server (FastMCP stdio)** | `acg/mcp/server.py` | 4 tools: `analyze_repo`, `predict_writes`, `compile_lockfile`, `validate_writes` — drop-in for Devin Manage Devins / Cursor / Claude Desktop / OpenCode |
| **CLI**                        | `acg/cli.py`        | 10 commands: `compile`, `explain`, `validate-write`, `report`, `run`, `run-benchmark`, `analyze-runs`, `mcp`, `init-graph`, `validate-lockfile`         |
| **LLM client**                 | `acg/llm.py`        | provider-agnostic OpenAI-compatible (Groq dev, vLLM/llama.cpp on GX10, mock)                                                                            |
| **Visualizer**                 | `viz/`              | React Flow v12; replays `run_trace.json` as a live ALLOWED/BLOCKED ticker                                                                               |

---

## 7. Architectural diagram (full)

```text
┌──────────────────────────── INPUT ────────────────────────────┐
│  • repo_root (filesystem)                                     │
│  • tasks.json (NL task list with optional hints)              │
└────────────────────────────┬──────────────────────────────────┘
                             │
                             ▼
┌──────────────────────────── L1: STATIC ANALYSIS (deterministic) ────────────────────────────┐
│                                                                                              │
│   acg/repo_graph.py                                                                          │
│      ├── graph_builder/scan.ts   (TypeScript / JavaScript)                                  │
│      └── graph_builder/scan_java.py (Java, in-process tree-sitter)                          │
│                                                                                              │
│      ▼ writes ▼                                                                              │
│   <repo>/.acg/context_graph.json                                                             │
│      { files, symbols_index, imports, exports, hotspots, routes, configs, tests }            │
│                                                                                              │
│   acg/index/                                                                                 │
│      ├── framework.py   (Next/T3/Django/Rails/FastAPI/Spring/Vite detection, conf 0.85)     │
│      ├── pagerank.py    (personalized PR over file-symbol graph; cached)                     │
│      ├── bm25.py        (lexical over path/idents/imports/docstring; synonym table)          │
│      ├── cochange.py    (ROSE-style git log --name-only seed expansion; min 3 commits)      │
│      └── aggregate.py   (fuse by max-conf per path; cochange runs LAST seeded by top fused)  │
│                                                                                              │
└──────────────────────────────────────────┬───────────────────────────────────────────────────┘
                                           │
                                           ▼
┌──────────────────────────── L2: PREDICTOR (1 LLM call/task) ────────────────────────────────┐
│                                                                                              │
│   acg/predictor.py                                                                           │
│      • 7 deterministic seeds → seed list                                                     │
│      • LLM rerank (top-50 filtered graph + seeds + task)                                     │
│      • merge: LLM conf wins, seed reason preserved on omission                               │
│      • cap MAX_PREDICTIONS=8, sort by (-conf, path)                                          │
│      • LLM failure → fallback to seeds, log warning, never abort                             │
│                                                                                              │
│      ▼ output: PredictedWrite[] per task ▼                                                   │
│         [{ path, confidence, reason }]                                                       │
│                                                                                              │
└──────────────────────────────────────────┬───────────────────────────────────────────────────┘
                                           │
                                           ▼
┌──────────────────────────── L3: COMPILER (deterministic) ───────────────────────────────────┐
│                                                                                              │
│   acg/compiler.py                                                                            │
│      • _to_allowed_path: ≥4 segs + conf ≥0.7 → parent/**; tests at ≥3 segs                  │
│      • _heuristic_dependencies: test-task heuristic (defeasible)                             │
│      • _explicit_dependencies: from task.depends_on (NOT defeasible)                         │
│      • assemble Task[] with predicted_writes + allowed_paths + depends_on                    │
│                                                                                              │
└──────────────────────────────────────────┬───────────────────────────────────────────────────┘
                                           │
                                           ▼
┌──────────────────────────── L4: SOLVER (pure, deterministic) ───────────────────────────────┐
│                                                                                              │
│   acg/solver.py                                                                              │
│      • detect_conflicts(tasks)         → Conflict[]   (pairwise file-set intersection)       │
│      • build_dag(tasks, heuristics)    → nx.DiGraph                                          │
│            edge layers: conflict (defeasible) → heuristic (defeasible) → explicit (hard)     │
│            SCC collapse for defeasible cycles; explicit cycles raise ValueError              │
│            edge orientation: (conflict_count, input_index) → strict total order              │
│      • topological_groups(dag)         → Group[]                                             │
│            node level = longest path from any source; same level → same group                │
│                                                                                              │
└──────────────────────────────────────────┬───────────────────────────────────────────────────┘
                                           │
                                           ▼
┌──────────────────────────── L5: LOCKFILE ARTIFACT (committable) ────────────────────────────┐
│                                                                                              │
│                      ╔════════════════════════════════╗                                      │
│                      ║      agent_lock.json           ║                                      │
│                      ║                                ║                                      │
│                      ║  • version: "1.0" const        ║  ◀── Pydantic v2 + JSON Schema       │
│                      ║  • generated_at, generator     ║      Strict mode (extra=forbid)      │
│                      ║  • repo { root, commit, langs }║      Round-trippable                 │
│                      ║  • tasks[]                     ║      Reviewable in PR                │
│                      ║  • execution_plan { groups[] } ║      Diffable in git                 │
│                      ║  • conflicts_detected[]        ║      Signable                        │
│                      ╚════════════════════════════════╝                                      │
│                                                                                              │
└────────────┬───────────────┬────────────────┬─────────────────┬──────────────────────────────┘
             │               │                │                 │
             ▼               ▼                ▼                 ▼
┌─────────────────────────┐ ┌────────────────────┐ ┌──────────────────────────┐ ┌─────────────────┐
│ L6: RUNTIME             │ │ L7: ENFORCEMENT    │ │ L8: INTEGRATION SURFACE  │ │ DOCS / VIZ      │
│                         │ │                    │ │                          │ │                 │
│ acg/runtime.py          │ │ acg/enforce.py     │ │ acg/mcp/server.py        │ │ viz/ (React     │
│  • run_orchestrator     │ │  • _matches (glob) │ │   4 tools (FastMCP stdio)│ │   Flow v12)     │
│    (1 thinking call,    │ │  • validate_write  │ │   • analyze_repo         │ │  • replay.ts    │
│     port 8081)          │ │    → (allowed,     │ │   • predict_writes       │ │    pure (trace, │
│  • run_worker           │ │       reason)      │ │   • compile_lockfile     │ │     t) → state  │
│    (no allowed_paths    │ │  • exit 0/1/2      │ │   • validate_writes      │ │  • rAF loop     │
│     in prompt)          │ │                    │ │                          │ │  • ALLOWED/     │
│  • run_group            │ │ Two delivery modes:│ │ acg/cli.py               │ │    BLOCKED      │
│    (seq / sema / unb.)  │ │   pre-emption      │ │   10 commands            │ │    badges       │
│  • MockRuntimeLLM       │ │   (Cascade hook)   │ │                          │ │                 │
│    (deterministic OOB)  │ │   post-hoc         │ │ acg/llm.py               │ │ docs/           │
│                         │ │   (Devin PR diff)  │ │   provider-agnostic      │ │  ARCHITECTURE   │
│  ▼ writes ▼             │ │                    │ │   OpenAI-compatible      │ │  COGNITION_INT  │
│  run_trace.json         │ │                    │ │                          │ │  MCP_SERVER     │
└─────────────────────────┘ └────────────────────┘ └──────────────────────────┘ │  CITATIONS      │
                                                                                 │  defense_brief  │
                                                                                 └─────────────────┘
```

---

## 8. Sequence diagrams — same task, three schools

Same scenario: 4 tasks (`oauth`, `billing`, `settings`, `tests`) on a Next.js repo where `oauth ↔ billing` and `oauth ↔ tests` and `billing ↔ tests` and `billing ↔ settings` overlap.

### 8.1 School A — centralized orchestrator

```text
T=0    User → Orch:    "do these 4 tasks"
T=1    Orch reads task list (1k tok) + repo snapshot (3k tok)         ── 4 K
T=2    Orch dispatches: W1=oauth, W2=billing, W3=settings, W4=tests
T=3    W1, W2, W3, W4 each receive: full plan + their slice           ── 4 × 1.5K = 6 K
T=4    W1 writes prisma/schema.prisma  ─┐
       W2 writes prisma/schema.prisma  ─┴── COLLISION (discovered at PR)
       W3 writes Sidebar.tsx          ─┐
       W2 writes Sidebar.tsx          ─┴── COLLISION
T=5    Orch polls workers → reads ALL their reports                   ── 7 K (cumulative)
T=6    Orch detects collision, RE-DISPATCHES W2 with rebase context   ── 3 K re-paste
T=7    W2 redo (full task re-cost)                                    ── 1.5 K
T=8    Orch polls, accepts → done

TOTAL ≈ 4K + 6K + 7K + 3K + 1.5K + (W4 retry tax) = ~25 K tokens for coordination
```

### 8.2 School B — peer-to-peer CRDT

```text
T=0    All 4 workers spawn, each gets task slice + shared CRDT doc     ── 4 × 2K = 8 K
T=1    W1, W2, W3, W4 begin. Each subscribes to others' edits.
T=2    W1 starts editing schema.prisma ─┐
       W2 starts editing schema.prisma ─┘── observes W1's edit (CRDT delta)
       W2 reconsiders, reads W1's intent  ── 0.5 K observation
T=3    W2 retries with merged context                                  ── 1.5 K full redo
T=4    W2 starts editing Sidebar.tsx ─┐
       W3 starts editing Sidebar.tsx ─┘── observes
       W3 reconsiders                    ── 0.5 K observation
T=5    Each of 4 workers paid n×(n−1) = 12 cross-deltas × 0.4K         ── 4.8 K
T=6    Semantic conflict (W1 renames a util W2 used) → both retry      ── 3 K extra
T=7    All converge (or DON'T — CodeCRDT range −39%..+21%)

TOTAL ≈ 8K + 1.5K + 4.8K + 3K + variance = ~17 K tokens, with ~30% chance of regression
```

### 8.3 School C — ACG (this project)

```text
COMPILE TIME (offline, runs once before any agent spawns):

T=−1   Build context_graph.json (deterministic, 0 LLM tokens)
T=0    For each of 4 tasks: predictor → 1 LLM call (0.8K)              ── 3.2 K
T=1    Compiler: glob broadening, test heuristic                       ── 0 LLM
T=2    Solver: pairwise conflict detection, DAG, groups                ── 0 LLM
T=3    Emit agent_lock.json with execution_plan:
         group 1 = [oauth, settings]   parallel
         group 2 = [billing]           after group 1
         group 3 = [tests]             after group 2

       └─── HUMAN reviews lockfile in PR. Diffable. Approves.

RUN TIME (4 workers, 0 cross-comms, 0 orch polls):

T=4    Group 1 spawns: W_oauth, W_settings (parallel)
       Each gets: own task + own allowed_paths block (≈ 400 tok)       ── 2 × 0.4K = 0.8 K
       Each writes within own allowed_paths.
       validate_write checks every write — deterministic, 0 LLM tokens.
T=5    Group 1 done. Group 2 spawns: W_billing
       W_billing gets task + own allowed_paths (≈ 0.4K)                ── 0.4 K
       Writes validated.
T=6    Group 2 done. Group 3 spawns: W_tests
       W_tests gets task + own allowed_paths                           ── 0.4 K
       Writes validated.
T=7    All done. NO collisions. NO retries. NO peer obs. NO orch poll.

TOTAL ≈ 3.2 K (compile) + 1.6 K (workers) + 0 (validator) = ~4.8 K
```

The compile-time cost is paid once and **the lockfile is reusable** across re-runs of the same task batch.

---

## 9. Why this works (invariants & complexity)

### 9.1 Complexity

| Operation                        | Complexity                  | Notes                                    |
| -------------------------------- | --------------------------- | ---------------------------------------- |
| Static analysis (L1)             | O(files)                    | tree-sitter / regex; cached              |
| Predictor (L2)                   | O(tasks) LLM calls          | embarrassingly parallel; 1 call per task |
| Compiler (L3)                    | O(tasks × predictions)      | glob broadening; deterministic           |
| Solver — conflict detection (L4) | O(tasks²)                   | pairwise file-set intersection           |
| Solver — DAG topology            | O(tasks + edges)            | NetworkX DAG longest-path                |
| Validator (L7)                   | O(globs per task) per write | constant-time per write in practice      |

For a typical 10-task batch: predictor = 10 LLM calls, conflict detection = 45 pairs, validator = O(1) per write. **The whole compile path runs sub-second on a laptop** for 100-file repos and a few seconds for 1000-file repos (PageRank dominates).

### 9.2 Invariants

1. **Acyclic by construction.** The conflict-derived edge orientation `(conflict_count, input_index)` is a strict total order, so all conflict edges agree on direction. No cycle from layer-1 alone. Layer-2 (heuristic) cycles are SCC-collapsed. Only layer-3 (explicit user deps) can raise.
2. **No silent failures in predictor.** LLM transport, parse, schema errors all fall back to seed-only prediction; CLI logs a warning; lockfile still gets written (`@/Users/prajit/Desktop/projects/cognition/acg/predictor.py:663-672`).
3. **Validator is total.** Every (task_id, path) maps to exactly one verdict — `allowed` or `(blocked, reason)`. No undefined behavior. Exit codes are stable: 0/1/2.
4. **Schema strict mode.** Pydantic v2 + JSON Schema both reject unknown fields. Lockfiles can't drift.
5. **Recall = 1.00 across all 48 task records** in the analyzer report. No file the agent eventually wrote was missing from `predicted_writes` (across observed runs). Precision = 0.82 — the failure mode is over-prediction, not miss.

### 9.3 What scales nicely

- **More tasks per batch.** Conflict detection is O(n²) but n is the task count, typically <50. The predictor is embarrassingly parallel.
- **Larger repos.** PageRank cache keyed by repo signature; incremental rebuild on changed files. BM25 is sub-second on 10k files. Co-change is bounded by `--max-count` on `git log`.
- **Tighter contracts.** The tightened-Greenhouse fixture (`agent_lock_tight.json`) shrinks `allowed_paths` to ground-truth and the validator fires **18 times** across 3 tasks. The system gracefully tightens.
- **More backends.** Same lockfile drives mock, local Gemma on GX10, live Devin v3 — three backends already wired in `experiments/greenhouse/`.

### 9.4 What's the trade-off (honest)

- **Wall time is slower in serialized groups.** Greenhouse Devin: ACG planned 854s vs naive 277s (3.08× slower). The trade is correctness for speed; recovered when downstream merge/review/test conflict costs dominate.
- **N=1 single-trial evidence.** Every benchmark cell is a single run. v2 plan covers multi-trial bootstrap CIs.
- **Java predictor over-predicts (precision 0.25).** Topical seed matches every account-related file. Recall is still 1.00. Analyzer surfaces concrete refinement suggestions.
- **Pre-emption requires Cascade-style local agents.** For Devin (black-box) we're post-hoc-only.

### 9.5 The mantra

> **"Static contract, committable artifact, provider-agnostic enforcement, honestly small evidence."**

ACG is `package-lock.json` for parallel coding agents. School A burns tokens on coordination polls. School B burns tokens on peer observation. ACG burns tokens **once**, in a deterministic compiler, and the answer goes to disk as a reviewable file. Every agent then runs in its own lane with its own contract — no orchestrator polling, no peer messaging, no retry storms.

That's the whole pitch.
