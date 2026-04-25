# ACG Implementation Megaplan (v1)

A self-contained implementation manual for Agent Context Graph (ACG) that another Cascade conversation can execute end-to-end without reading prior chat history; references `agent-context-graph-decision-plan-308cc2.md` and `acg-execution-kickoff-308cc2.md` only for "why" context.

---

## How to use this document

You are a fresh Cascade conversation. Build the codebase below in tier order. Do not skip ahead. After each tier, run the acceptance check listed; only proceed if it passes. Code quality matters — judges will read it. Write tests for solver, schema validation, and predictor (with mock LLM).

If anything in this document conflicts with what the user (Shashank) tells you in chat, the user wins. Ask the user when:
- A library version pin in `pyproject.toml` fails to resolve
- The Windsurf docs disagree with this plan about hook behavior
- A demo-app starter you pick is not what the user expected

Do not ask the user about: file naming, formatting choices, test coverage thresholds, internal helper function design, error message wording. Make those calls yourself per the style guide below.

---

## Project context (read once, then proceed)

ACG is a pre-flight compiler for multi-agent code workflows. Input: a code repo + a list of agent tasks in plain English. Output: a committable `agent_lock.json` that declares (a) which files each task is allowed to write, and (b) a DAG of execution groups — tasks with disjoint write-sets are parallelizable; tasks with overlapping write-sets are serialized.

Pitch sentence: *"It's `package-lock.json` for parallel coding agents."*

Target sponsor track 1: **Cognition** at LA Hacks 2026 (Devin Manage Devins narrative — the pre-flight artifact the coordinator consumes before fanning out child Devins).

Target sponsor track 2: **ASUS** (local-first hardware narrative — runs entirely on GX10, no cloud LLM, no data leaves the box).

Same code, two Devpost submissions, two value props.

Demo flow (2:40 video): naive parallel agents collide → ACG compile → lockfile shows DAG → enforcement layer blocks an illegal write → benchmark chart shows fewer conflicts → 20-second Cognition close.

---

## Locked decisions

| Decision | Choice | Why |
|---|---|---|
| Primary language | Python 3.11+ | CLI, predictor, solver, MCP, benchmark all in Python |
| Parser tooling | ts-morph (Node) + tree-sitter-python | Polyglot; ts-morph is gold-standard for TS/JS |
| LLM client | OpenAI-compatible HTTP via httpx | Provider-agnostic; works with Groq, vLLM, Anthropic |
| LLM dev provider | Groq free tier (Llama 3.3-70B Versatile) | No credits needed; ~250 tok/s |
| LLM demo provider | vLLM on ASUS GX10 (Llama 3.3-70B Q4) | ASUS narrative; ~25-40 tok/s; same client code |
| CLI framework | Typer | Type-hinted, modern, terse |
| Schema models | Pydantic v2 | Strict validation; serde to JSON |
| DAG ops | networkx | Topological sort, cycle detection, path enumeration |
| Charts | matplotlib (single file out) | One chart, write to PNG |
| Tests | pytest | Standard |
| Lint/format | ruff | Single tool, fast |
| Cascade hook | Out of v1 (separate stretch plan) | Risk too high for critical path |
| Frontend | None in v1 | Single PNG chart is enough |
| MCP server | Tier 7, stretch | Build CLI first, wrap last |

---

## Tech stack pins

```toml
# pyproject.toml — copy-paste-ready
[project]
name = "acg"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
  "typer>=0.12,<1.0",
  "pydantic>=2.7,<3.0",
  "httpx>=0.27,<1.0",
  "jsonschema>=4.22,<5.0",
  "networkx>=3.3,<4.0",
  "matplotlib>=3.9,<4.0",
  "rich>=13.7,<14.0",
  "python-dotenv>=1.0,<2.0",
  "pyyaml>=6.0,<7.0",
]

[project.optional-dependencies]
mcp = ["fastmcp>=0.5,<1.0"]
dev = ["pytest>=8.0,<9.0", "ruff>=0.5,<1.0"]
agentverse = ["uagents-adapter>=0.5,<1.0"]

[project.scripts]
acg = "acg.cli:app"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

```json
// graph_builder/package.json — copy-paste-ready
{
  "name": "acg-graph-builder",
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "scan": "tsx scan.ts"
  },
  "dependencies": {
    "ts-morph": "^22.0.0"
  },
  "devDependencies": {
    "tsx": "^4.15.0",
    "typescript": "^5.4.0",
    "@types/node": "^20.12.0"
  }
}
```

---

## Repo layout (final)

```
cognition/
├── README.md
├── HANDOFF.md
├── pyproject.toml
├── .gitignore
├── .env.example
├── Makefile
├── schema/
│   └── agent_lock.schema.json
├── examples/
│   ├── tasks.example.json
│   ├── lockfile.simple.example.json
│   └── lockfile.dag.example.json
├── acg/
│   ├── __init__.py
│   ├── cli.py
│   ├── schema.py
│   ├── llm.py
│   ├── compiler.py
│   ├── predictor.py
│   ├── solver.py
│   ├── explain.py
│   ├── enforce.py
│   └── report.py
├── graph_builder/
│   ├── package.json
│   ├── tsconfig.json
│   └── scan.ts
├── demo-app/                  (cloned starter, see Tier 4)
│   └── tasks.json
├── benchmark/
│   ├── runner.py
│   └── chart.py
├── mcp_server/
│   └── server.py
├── docs/
│   ├── CITATIONS.md
│   ├── ASUS_DEPLOYMENT.md
│   ├── COGNITION_INTEGRATION.md
│   └── ARCHITECTURE.md
├── tests/
│   ├── conftest.py
│   ├── test_schema.py
│   ├── test_solver.py
│   ├── test_predictor.py
│   └── fixtures/
│       ├── tiny_repo/
│       └── tasks_basic.json
└── scripts/
    └── (Cascade hook stretch — see cascade-hook-stretch-308cc2.md)
```

---

## File-by-file specifications

### Tier 1 — Schema and examples (gates everything; do these first, in order)

#### `schema/agent_lock.schema.json`

**Purpose:** the JSON Schema for `agent_lock.json` v1. Every lockfile validates against this. This is the most important file in the repo — judges will read it.

**Schema (write this exactly, no creative renaming):**

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://acg.dev/schema/agent_lock/v1",
  "title": "Agent Context Graph Lockfile",
  "type": "object",
  "required": ["version", "generated_at", "repo", "tasks", "execution_plan"],
  "properties": {
    "version": { "const": "1.0" },
    "generated_at": { "type": "string", "format": "date-time" },
    "generator": {
      "type": "object",
      "properties": {
        "tool": { "type": "string" },
        "version": { "type": "string" },
        "model": { "type": "string" }
      }
    },
    "repo": {
      "type": "object",
      "required": ["root", "languages"],
      "properties": {
        "root": { "type": "string" },
        "git_url": { "type": "string" },
        "commit": { "type": "string" },
        "languages": {
          "type": "array",
          "items": { "type": "string" }
        }
      }
    },
    "tasks": {
      "type": "array",
      "items": { "$ref": "#/$defs/Task" }
    },
    "execution_plan": {
      "type": "object",
      "required": ["groups"],
      "properties": {
        "groups": {
          "type": "array",
          "items": { "$ref": "#/$defs/Group" }
        }
      }
    },
    "conflicts_detected": {
      "type": "array",
      "items": { "$ref": "#/$defs/Conflict" }
    }
  },
  "$defs": {
    "Task": {
      "type": "object",
      "required": ["id", "prompt", "predicted_writes", "allowed_paths", "depends_on"],
      "properties": {
        "id": { "type": "string", "pattern": "^[a-z0-9_-]+$" },
        "prompt": { "type": "string" },
        "predicted_writes": {
          "type": "array",
          "items": { "$ref": "#/$defs/PredictedWrite" }
        },
        "allowed_paths": {
          "type": "array",
          "items": { "type": "string" },
          "description": "Glob patterns the task is allowed to modify"
        },
        "depends_on": {
          "type": "array",
          "items": { "type": "string" }
        },
        "parallel_group": { "type": "integer", "minimum": 1 },
        "rationale": { "type": "string" }
      }
    },
    "PredictedWrite": {
      "type": "object",
      "required": ["path", "confidence"],
      "properties": {
        "path": { "type": "string" },
        "confidence": { "type": "number", "minimum": 0, "maximum": 1 },
        "reason": { "type": "string" }
      }
    },
    "Group": {
      "type": "object",
      "required": ["id", "tasks", "type"],
      "properties": {
        "id": { "type": "integer", "minimum": 1 },
        "tasks": {
          "type": "array",
          "items": { "type": "string" }
        },
        "type": { "enum": ["parallel", "serial"] },
        "waits_for": {
          "type": "array",
          "items": { "type": "integer" }
        }
      }
    },
    "Conflict": {
      "type": "object",
      "required": ["files", "between_tasks", "resolution"],
      "properties": {
        "files": {
          "type": "array",
          "items": { "type": "string" }
        },
        "between_tasks": {
          "type": "array",
          "items": { "type": "string" }
        },
        "resolution": { "type": "string" }
      }
    }
  }
}
```

**Acceptance:** `jsonschema -i examples/lockfile.dag.example.json schema/agent_lock.schema.json` exits 0.

---

#### `examples/tasks.example.json`

**Purpose:** concrete input format the user feeds the compiler. Drives the Tier 4 demo.

**Contents:**

```json
{
  "version": "1.0",
  "tasks": [
    {
      "id": "oauth",
      "prompt": "Add Google OAuth login. Use NextAuth. Update Prisma schema with required fields.",
      "hints": {
        "touches": ["auth", "prisma"]
      }
    },
    {
      "id": "billing",
      "prompt": "Add a billing dashboard tab at /dashboard/billing with Stripe integration. Add a sidebar entry. Update Prisma with subscription model.",
      "hints": {
        "touches": ["billing", "prisma", "navigation"]
      }
    },
    {
      "id": "settings",
      "prompt": "Redesign the user settings page at /settings. Reorganize sections. Update sidebar entry styling.",
      "hints": {
        "touches": ["settings", "navigation"]
      }
    },
    {
      "id": "tests",
      "prompt": "Write end-to-end Playwright tests for the checkout flow.",
      "hints": {
        "touches": ["tests"]
      }
    }
  ]
}
```

**Acceptance:** valid JSON; 4 tasks; the natural overlap between them (auth, prisma, navigation) is visible to a human reader.

---

#### `examples/lockfile.simple.example.json`

**Purpose:** the trivially-disjoint case. Two tasks, completely separate file scopes, both run in parallel.

Build a 2-task example: `add_readme` (touches `README.md`, `docs/`) and `bump_deps` (touches `package.json`, `package-lock.json`). Disjoint. One parallel group.

**Acceptance:** validates against schema; `execution_plan.groups` has length 1, type "parallel".

---

#### `examples/lockfile.dag.example.json`

**Purpose:** the realistic DAG case for the demo's 4-task scenario. This is what the video shows on screen.

Build the lockfile for the `tasks.example.json` above. Required structure:

- 4 tasks: `oauth`, `billing`, `settings`, `tests`
- `oauth` predicted_writes: `lib/auth.ts`, `prisma/schema.prisma`, `app/api/auth/**`
- `settings` predicted_writes: `app/settings/page.tsx`, `components/sidebar.tsx`
- `billing` predicted_writes: `app/dashboard/billing/page.tsx`, `lib/stripe.ts`, `prisma/schema.prisma`, `components/sidebar.tsx`
- `tests` predicted_writes: `tests/e2e/checkout.spec.ts`
- `oauth` and `settings` have disjoint writes → parallel group 1
- `billing` overlaps with both (`prisma/schema.prisma` with oauth, `components/sidebar.tsx` with settings) → group 2 serial, waits_for [1]
- `tests` waits last → group 3 serial, waits_for [2]
- `conflicts_detected` array has 2 entries documenting the overlaps

**Acceptance:** validates against schema; topological order is `[oauth+settings] → billing → tests`; one human can read it and understand the demo without explanation.

### Tier 1 acceptance gate

Run:
```bash
python -c "import json, jsonschema; \
  schema = json.load(open('schema/agent_lock.schema.json')); \
  for f in ['examples/lockfile.simple.example.json', 'examples/lockfile.dag.example.json']: \
    jsonschema.validate(json.load(open(f)), schema); print(f, 'OK')"
```

Both must print "OK". Do not proceed to Tier 2 until this passes.

---

### Tier 2 — Python core

Build in this order: `schema.py` → `llm.py` → `solver.py` → `predictor.py` → `compiler.py` → `explain.py` → `enforce.py` → `report.py` → `cli.py`.

#### `acg/__init__.py`

```python
"""Agent Context Graph — pre-flight write contract compiler for multi-agent code."""
__version__ = "0.1.0"
```

---

#### `acg/schema.py`

**Purpose:** Pydantic v2 models that mirror the JSON Schema. Used by every other module.

**Public API:**
- `class TaskInput(BaseModel)` — input task from `tasks.json`
- `class TasksInput(BaseModel)` — root of `tasks.json`
- `class PredictedWrite(BaseModel)` — `path: str`, `confidence: float`, `reason: str`
- `class Task(BaseModel)` — full lockfile task
- `class Group(BaseModel)` — execution group
- `class Conflict(BaseModel)` — detected conflict
- `class Repo(BaseModel)` — repo metadata
- `class Generator(BaseModel)` — generator metadata
- `class ExecutionPlan(BaseModel)` — groups list
- `class AgentLock(BaseModel)` — root model with `model_validate_json` and `model_dump_json(indent=2)`

**Acceptance:** `AgentLock.model_validate_json(open('examples/lockfile.dag.example.json').read())` returns a valid model instance.

---

#### `acg/llm.py`

**Purpose:** provider-agnostic OpenAI-compatible client. Reads `ACG_LLM_URL`, `ACG_LLM_MODEL`, `ACG_LLM_API_KEY` from env.

**Public API:**
- `class LLMClient` with `__init__(base_url, model, api_key, timeout)`
- `LLMClient.complete(messages: list[dict], response_format: dict | None) -> str`
- `LLMClient.from_env() -> LLMClient` (factory)

**Implementation notes:**
- Use `httpx.Client` with `timeout=120.0`
- POST to `{base_url}/chat/completions`
- Default base_url: `https://api.groq.com/openai/v1`
- Default model: `llama-3.3-70b-versatile`
- If `response_format` is dict, pass through (works on OpenAI; Groq/vLLM may ignore)
- Raise `LLMError` with body on non-2xx
- Retry once on connection error

**Acceptance:** `LLMClient.from_env().complete([{"role":"user","content":"reply with the word HELLO"}])` returns a string containing "HELLO" (case-insensitive). Test against Groq free tier with `GROQ_API_KEY` env var.

---

#### `acg/solver.py`

**Purpose:** given a list of tasks with predicted_writes, build the DAG and emit execution groups. Pure function, no LLM, no IO. Tested rigorously.

**Public API:**
- `def detect_conflicts(tasks: list[Task]) -> list[Conflict]` — returns pairs of tasks with overlapping predicted_writes
- `def build_dag(tasks: list[Task]) -> nx.DiGraph` — nodes = task IDs, edges = serial dependencies
- `def topological_groups(dag: nx.DiGraph) -> list[Group]` — group IDs, ordered, with waits_for

**Algorithm:**
1. For each pair of tasks (i, j), compute write-set intersection on `path` field
2. If non-empty, mark as conflict; add directed edge i → j (alphabetical first) to enforce order
3. Honor explicit `depends_on` declared by user
4. Topo-sort the DAG; nodes at the same topological level with no edges between them go in the same parallel group
5. Each subsequent level becomes a serial group with `waits_for` pointing back to its prerequisite group(s)

**Acceptance:** `tests/test_solver.py` covers:
- 2 disjoint tasks → 1 parallel group
- 4 tasks per the dag.example → 3 groups in known order
- A cycle in declared depends_on → raises `ValueError("cycle detected")`
- Empty task list → empty groups

---

#### `acg/predictor.py`

**Purpose:** given a repo graph (from `graph_builder/scan.ts` JSON output) + a task prompt, predict which files the task will write.

**Public API:**
- `def predict_writes(task: TaskInput, repo_graph: dict, llm: LLMClient) -> list[PredictedWrite]`

**Implementation:**
1. **Static seed:** scan task prompt for explicit file mentions (regex `[\w/]+\.(ts|tsx|js|jsx|py|prisma|sql|md|json)`). Add as confidence 0.95.
2. **Symbol seed:** scan task prompt for symbol names (camelCase tokens >5 chars). Look up in `repo_graph.symbols` → file. Add as confidence 0.85.
3. **Topical seed:** for each task hint (`auth`, `billing`, etc.), find files with matching path components or content keywords. Add as confidence 0.7.
4. **LLM re-rank:** call LLM with system prompt explaining ACG, user prompt containing task + filtered graph (top 50 most relevant files), get back JSON list of `{path, confidence, reason}`. Merge with seeds (LLM can boost or demote).
5. Deduplicate, sort by confidence desc, return top 8.

**Prompt template (commit verbatim):**

```
SYSTEM: You are ACG, a static analyzer that predicts which files an agent task will modify.
You are given a task description and a code graph (files, imports, exports, hotspots).
Output a JSON object with key "writes" containing a list of {path, confidence, reason}.
Confidence is 0.0-1.0. Reason is one short sentence.
Be conservative: only include files where the task description clearly implies a modification.
Do not include files based on speculation.

USER: Task: {task.prompt}
Hints: {task.hints}

Code graph (top 50 relevant files):
{filtered_graph_json}

Existing static-seed predictions (you may keep, demote, or remove these):
{seed_predictions_json}

Output JSON only, no prose.
```

**Acceptance:** `tests/test_predictor.py` runs with a mocked `LLMClient` (returns canned JSON) and produces correct merged output. Real-LLM smoke test in a separate marker (`-m smoke`).

---

#### `acg/compiler.py`

**Purpose:** orchestrate predictor + solver into a full lockfile.

**Public API:**
- `def compile_lockfile(repo_path: Path, tasks_input: TasksInput, repo_graph: dict, llm: LLMClient) -> AgentLock`

**Steps:**
1. For each task, call `predict_writes(task, repo_graph, llm)`
2. Convert `predicted_writes` into `allowed_paths` glob list (path → glob via parent dir wildcards if confidence ≥ 0.7; exact path otherwise)
3. Call `solver.detect_conflicts(tasks)`
4. Call `solver.build_dag(tasks)` then `solver.topological_groups(dag)`
5. Assemble `AgentLock` with all metadata (timestamp, repo info, generator info)
6. Return

**Acceptance:** `acg compile --repo demo-app --tasks demo-app/tasks.json` produces a valid `agent_lock.json` that validates against the schema.

---

#### `acg/explain.py`

**Purpose:** terminal-friendly DAG visualization. ASCII art, no graphviz dependency.

**Public API:**
- `def render_dag(lock: AgentLock) -> str` — ASCII tree
- `def render_summary(lock: AgentLock) -> str` — bullet list of groups, conflicts, key tasks

**Output sample (target):**
```
Execution plan:
  Group 1 (parallel): oauth, settings
  Group 2 (serial, waits for 1): billing
  Group 3 (serial, waits for 2): tests

Conflicts detected:
  - lib/auth.ts overlap: oauth ⨯ billing → serialize billing after oauth
  - components/sidebar.tsx overlap: billing ⨯ settings → serialize billing after settings

ASCII DAG:
  oauth ───┐
           ├──► billing ───► tests
  settings ┘
```

**Acceptance:** `acg explain --lock examples/lockfile.dag.example.json` produces output matching the structure above.

---

#### `acg/enforce.py`

**Purpose:** the demo's enforcement layer. Wraps a write attempt, validates against the lockfile.

**Public API:**
- `def validate_write(lock: AgentLock, task_id: str, write_path: str) -> tuple[bool, str | None]` — returns (allowed, reason_if_blocked)
- `def cli_validate(lockfile_path, task_id, write_path) -> int` — CLI exit-code wrapper (0 = allowed, 2 = blocked)

**Algorithm:**
1. Find task by id; raise if not found
2. Match `write_path` against any glob in `task.allowed_paths` (use `pathlib.PurePath.match` or `fnmatch`)
3. If matched: return (True, None)
4. If not matched: return (False, f"path {write_path} is outside task {task_id}'s allowed_paths")

**Acceptance:** `acg validate-write --lock examples/lockfile.dag.example.json --task settings --path lib/auth.ts` exits 2 with message. Same with `--task settings --path app/settings/page.tsx` exits 0.

---

#### `acg/report.py`

**Purpose:** the benchmark chart. Reads two JSON result files, writes a PNG.

**Public API:**
- `def build_chart(naive_path, planned_path, out_path) -> None`

**Chart contents (5 grouped bar pairs):**
- Overlapping writes
- Blocked bad writes
- Manual merge steps
- Tests passing first run (yes=1, no=0)
- Wall time (minutes)

**Style:**
- matplotlib, no seaborn
- Title: "Agent coordination tax — naive vs ACG-planned"
- X-axis: metrics
- Two bars per metric: gray (naive) and a single accent color (planned)
- Numbers on top of each bar
- Save 1200×600 PNG

**Acceptance:** `acg report --naive .acg/run_naive.json --planned .acg/run_acg.json --out .acg/benchmark.png` produces a readable PNG.

---

#### `acg/cli.py`

**Purpose:** the Typer CLI. All commands route to functions in other modules.

**Commands:**
- `acg compile --repo PATH --tasks FILE --out FILE` → calls compiler
- `acg explain --lock FILE` → calls explain
- `acg validate-write --lock FILE --task ID --path PATH [--quiet]` → calls enforce
- `acg report --naive FILE --planned FILE --out FILE` → calls report
- `acg run-benchmark --mode {naive,planned} --repo PATH --tasks FILE --out FILE` → calls benchmark.runner

**Style:**
- Typer with type hints
- Rich for stylized output (color, but auto-disable when `--quiet` or non-tty)
- Each command has a 1-line `help=` string
- Exit codes: 0 = success, 1 = user error, 2 = validation block

**Acceptance:** `acg --help` lists all 5 commands with descriptions; `acg compile --help` shows correct flags.

### Tier 2 acceptance gate

```bash
acg compile --repo examples --tasks examples/tasks.example.json --out /tmp/test_lock.json
acg explain --lock /tmp/test_lock.json
acg validate-write --lock /tmp/test_lock.json --task settings --path lib/auth.ts  # exits 2
acg validate-write --lock /tmp/test_lock.json --task settings --path app/settings/page.tsx  # exits 0
pytest tests/ -v
```

All commands succeed; all tests pass. Do not proceed to Tier 3 until this passes.

---

### Tier 3 — Parser (Node/TS)

#### `graph_builder/tsconfig.json`

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "esModuleInterop": true,
    "skipLibCheck": true
  }
}
```

---

#### `graph_builder/scan.ts`

**Purpose:** scan a TS/JS repo with ts-morph, output a JSON graph that the predictor consumes.

**Behavior:**
- Reads `--repo PATH --out PATH.json` from argv
- Uses `Project` from ts-morph to load all `.ts`, `.tsx`, `.js`, `.jsx` files (skip `node_modules`, `.next`, `dist`)
- For each source file: collect `imports[]`, `exports[]`, `default_export` symbol, top-level declarations
- Compute hotspots: files imported by 5+ other files
- Output JSON shape:

```json
{
  "version": "1.0",
  "scanned_at": "2026-04-25T12:00:00Z",
  "root": "/path/to/repo",
  "language": "typescript",
  "files": [
    {
      "path": "lib/auth.ts",
      "imports": ["next-auth", "./db", "@/lib/prisma"],
      "exports": ["authOptions", "getCurrentUser"],
      "symbols": ["authOptions", "getCurrentUser", "AuthProvider"],
      "is_hotspot": true,
      "imported_by_count": 12
    }
  ],
  "symbols_index": {
    "authOptions": "lib/auth.ts",
    "getCurrentUser": "lib/auth.ts"
  },
  "hotspots": ["lib/auth.ts", "lib/prisma.ts", "components/sidebar.tsx"]
}
```

**Library:** `ts-morph` 22.x. Use `Project.addSourceFilesAtPaths('**/*.{ts,tsx,js,jsx}')`.

**Acceptance:** `cd graph_builder && npm install && npm run scan -- --repo ../demo-app --out ../.acg/context_graph.json` produces a non-empty graph. Graph contains hotspots. `lib/auth.ts` (or whatever the demo-app has) is listed.

---

### Tier 4 — Demo target

Pick a Next.js + Prisma starter. Recommended: clone `https://github.com/vercel/next.js/tree/canary/examples/with-prisma` to `demo-app/`, then expand it minimally so it has the files our `tasks.example.json` references:

- `lib/auth.ts` (stub, contains `export const authOptions = {}`)
- `lib/prisma.ts` (stub)
- `prisma/schema.prisma` (basic User model)
- `components/sidebar.tsx` (stub list)
- `app/settings/page.tsx` (stub)
- `app/dashboard/billing/page.tsx` (does not exist yet — billing creates it)
- `tests/` (empty)

The demo-app does NOT need to actually run. It only needs to look like a real codebase to ts-morph and to a viewer.

**Copy `examples/tasks.example.json` to `demo-app/tasks.json`** so the demo path is `acg compile --repo demo-app --tasks demo-app/tasks.json`.

**Acceptance:** `cd graph_builder && npm run scan -- --repo ../demo-app --out ../.acg/context_graph.json` produces a graph with at least 6 files and 3 hotspots.

---

### Tier 5 — Enforcement (script emulator only)

Already covered by `acg/enforce.py` and the `acg validate-write` CLI command. No new files in this tier — just confirm the BLOCKED/allowed scenario works on the demo-app:

```bash
acg compile --repo demo-app --tasks demo-app/tasks.json --out agent_lock.json
acg validate-write --lock agent_lock.json --task settings --path lib/auth.ts  # MUST exit 2
acg validate-write --lock agent_lock.json --task settings --path components/sidebar.tsx  # MUST exit 0
```

**Acceptance:** both commands behave as expected.

---

### Tier 6 — Benchmark harness

#### `benchmark/runner.py`

**Purpose:** simulate (or actually run, if Devin/Aider available) the two execution modes and emit a JSON metrics file.

**Public API (called via `acg run-benchmark`):**
- `def run_naive(repo_path, tasks_input) -> dict` — simulates parallel agents writing without coordination; counts overlaps, blocked writes, etc.
- `def run_planned(repo_path, lockfile_path) -> dict` — simulates serialized execution per the lockfile; counts the same metrics

**Simulation strategy (v1, no real agents):**
- For naive: assume each task writes its predicted_writes; count overlap pairs, count "manual merge steps" as overlap-pairs-times-2, "tests passing first run" = false (because last task likely sees broken state), wall time = max(individual durations) + overlap penalty
- For planned: same files written, but in DAG order; "manual merge steps" = 0; "tests passing first run" = true; wall time = sum of group max durations

**Metric output JSON:**
```json
{
  "mode": "naive",
  "tasks": 4,
  "overlapping_writes": 5,
  "blocked_bad_writes": 0,
  "manual_merge_steps": 6,
  "tests_passing_first_run": false,
  "wall_time_minutes": 18,
  "acu_consumed": null
}
```

**Acceptance:** running both modes produces JSON files with different (and plausible) numbers; running `acg report` against them produces a chart.

---

#### `benchmark/chart.py`

Already covered by `acg/report.py`. This file is a thin re-export if needed for direct import, otherwise omit.

### Tier 6 acceptance gate

```bash
acg run-benchmark --mode naive --repo demo-app --tasks demo-app/tasks.json --out .acg/run_naive.json
acg run-benchmark --mode planned --repo demo-app --tasks demo-app/tasks.json --out .acg/run_acg.json
acg report --naive .acg/run_naive.json --planned .acg/run_acg.json --out .acg/benchmark.png
```

PNG exists, has 5 metric pairs, planned-mode shows clearly fewer conflicts. Numbers must be plausible — do not fabricate to look better than they are. If real numbers are flat, document that and reframe the demo per the strategic plan's pivot triggers.

---

### Tier 7 — MCP server (stretch)

#### `mcp_server/server.py`

**Purpose:** wrap the CLI commands as MCP tools so Devin/Claude Code/Cursor can call ACG natively.

**Tools exposed:**
- `analyze_repo(path: str) -> dict` — calls graph_builder, returns graph summary + hotspots
- `predict_writes(task: dict, repo_graph: dict) -> list[dict]` — calls predictor
- `compile_lockfile(repo: str, tasks: dict) -> dict` — calls compiler, returns AgentLock as dict
- `validate_writes(lockfile: dict, task_id: str, attempted_path: str) -> dict` — calls enforce, returns {allowed: bool, reason: str | None}

**Implementation:** FastMCP. Register tools via `@mcp.tool()` decorators. Run with `mcp.run(transport="stdio")` for default MCP client compatibility.

**Acceptance:** `python -m mcp_server.server` runs; an MCP client (e.g. `mcp-inspector`) sees 4 tools and can call `analyze_repo` with the demo-app path.

---

### Tier 8 — Documentation

#### `README.md`

**Purpose:** Devpost-grade entry point. Read by judges within 60 seconds. Must convey thesis, demo proof, sponsor fit before scrolling.

**Structure:**

1. **Title:** Agent Context Graph (ACG)
2. **Hero one-liner:** "It's package-lock.json for parallel coding agents."
3. **Pitch paragraph:** the verbatim pitch from the execution kickoff plan.
4. **Demo (the chart):** embed `.acg/benchmark.png`
5. **Quickstart (60 seconds):**
   ```bash
   git clone ...
   pip install -e .
   cd graph_builder && npm install && cd ..
   export GROQ_API_KEY=...    # or use GX10 vLLM
   acg compile --repo demo-app --tasks demo-app/tasks.json --out agent_lock.json
   acg explain --lock agent_lock.json
   ```
6. **What's inside:** the lockfile schema link + 1 example.
7. **Architecture:** ASCII diagram from `docs/ARCHITECTURE.md`.
8. **Sponsor narratives:** two short paragraphs — Cognition (link to `docs/COGNITION_INTEGRATION.md`), ASUS (link to `docs/ASUS_DEPLOYMENT.md`).
9. **Honesty box:** the 7 non-negotiable commitments verbatim from the strategic plan.
10. **Citations:** link to `docs/CITATIONS.md`.
11. **License:** MIT.

---

#### `HANDOFF.md`

**Purpose:** Prajit (teammate) reads this when he wakes up. Tells him exactly what to do, in order, no ambiguity.

**Structure:**

1. **Status as of last commit:** what's done, what's not
2. **Your first 3 hours (in order):**
   - 30 min: pick the Next.js + Prisma starter, copy into `demo-app/`, ensure `tasks.example.json` paths resolve
   - 60 min: hand-label ground-truth `predicted_writes` for the 4 tasks in `demo-app/ground_truth.json` (used to score predictor)
   - 30 min: open every URL in `docs/CITATIONS.md`; verify each quote verbatim; mark verified or paraphrased
   - 60 min: write the demo video script (text only) following the 6-segment crash-test from `acg-execution-kickoff-308cc2.md`
3. **What Shashank is working on:** Tier 2 (Python core) and Tier 3 (parser)
4. **Daily milestones:** the H+N gates from the execution plan
5. **Decision authority:** what you can decide alone (file naming, README phrasing) vs what to ask Shashank (scope, sponsor strategy)

---

#### `docs/CITATIONS.md`

**Purpose:** Prajit fills this in by hand verifying every external citation. If any quote is paraphrased, the Devpost language softens.

**Structure:** a table of citations with columns: source URL, claimed quote, verified verbatim (yes/paraphrased/missing), retrieved date.

Include all 10 citations from the strategic plan + execution plan: CodeCRDT future-work, OpenCode #4278, Walden Yan, Cognition Manage Devins, DeepWiki MCP three-tool list, LA Hacks Cognition prizes, Windsurf hooks docs (if applicable), FastMCP package, uagents-adapter, ASUS GX10 LA Hacks sponsorship.

---

#### `docs/ASUS_DEPLOYMENT.md`

**Purpose:** the ASUS submission narrative + reproducibility manual. This is what the ASUS judges read.

**Structure:**

1. **Why ASUS GX10:** local-first AI agent infrastructure for compliance-heavy enterprises that cannot ship code to cloud LLM APIs. 128GB unified memory enables running Llama 3.3-70B + parsing a real codebase + simulating multiple agents simultaneously.
2. **Performance:** measured throughput of Llama 3.3-70B Q4 on GB10 (record actual numbers if you have access; otherwise cite expected ~25-40 tok/s).
3. **Deployment steps:**
   - vLLM install: `pip install vllm`
   - Model: `meta-llama/Llama-3.3-70B-Instruct` (Q4 GGUF or AWQ depending on tooling)
   - Server: `vllm serve <model> --host 0.0.0.0 --port 8000 --max-model-len 8192`
   - Client config: `export ACG_LLM_URL=http://localhost:8000/v1; export ACG_LLM_MODEL=<model-name>`
4. **Cost story:** zero per-call cost vs cloud API; one-time hardware cost amortizes at ~10K predictor calls.
5. **Privacy story:** code never leaves the machine; required for finance, healthcare, defense codebases.

---

#### `docs/COGNITION_INTEGRATION.md`

**Purpose:** the Cognition submission narrative. What the Cognition judges read after the README.

**Structure:**

1. **The gap ACG fills:** Devin Manage Devins coordinator "resolves conflicts" with no documented mechanism. ACG is the pre-flight artifact the coordinator could consume.
2. **Three smoking guns:** CodeCRDT future-work quote, OpenCode #4278, Walden Yan quote (verbatim from CITATIONS.md).
3. **MCP integration story:** how Devin would call ACG via MCP — show the 4 tool signatures and example flow.
4. **What we did not build:** explicit deferral on Cascade hook (cite the stretch plan), CRDT runtime (cite CodeCRDT), live Devin sessions (cite Devin platform availability).

---

#### `docs/ARCHITECTURE.md`

**Purpose:** one-page deep-dive that an engineering judge reads if they're impressed by README and want to verify depth.

**Contents:** the architecture box diagram from the strategic plan §7, plus 1-paragraph descriptions of each component, plus a sequence diagram showing the compile flow end-to-end.

### Tier 8 acceptance gate

All four docs exist, all internal links resolve, README displays correctly on github.com (preview before pushing).

---

### Tier 9 — Misc

#### `.gitignore`

```
# Python
__pycache__/
*.py[cod]
*.egg-info/
.venv/
.env
.pytest_cache/
.ruff_cache/

# Node
node_modules/
dist/

# ACG outputs
.acg/
agent_lock.json
demo-app/.next/
demo-app/node_modules/
demo-app/agent_lock.json

# OS
.DS_Store
```

---

#### `.env.example`

```
# LLM endpoint — pick one
ACG_LLM_URL=https://api.groq.com/openai/v1
ACG_LLM_MODEL=llama-3.3-70b-versatile
ACG_LLM_API_KEY=your_groq_key_here

# Or for GX10 local:
# ACG_LLM_URL=http://localhost:8000/v1
# ACG_LLM_MODEL=meta-llama/Llama-3.3-70B-Instruct
# ACG_LLM_API_KEY=anything
```

---

#### `Makefile`

```makefile
.PHONY: install scan compile demo test lint clean

install:
	pip install -e ".[dev]"
	cd graph_builder && npm install

scan:
	cd graph_builder && npm run scan -- --repo ../demo-app --out ../.acg/context_graph.json

compile: scan
	acg compile --repo demo-app --tasks demo-app/tasks.json --out agent_lock.json

demo: compile
	acg explain --lock agent_lock.json
	acg run-benchmark --mode naive --repo demo-app --tasks demo-app/tasks.json --out .acg/run_naive.json
	acg run-benchmark --mode planned --repo demo-app --tasks demo-app/tasks.json --out .acg/run_acg.json
	acg report --naive .acg/run_naive.json --planned .acg/run_acg.json --out .acg/benchmark.png

test:
	pytest tests/ -v

lint:
	ruff check .
	ruff format --check .

clean:
	rm -rf .acg agent_lock.json __pycache__ .pytest_cache .ruff_cache
```

---

## Build order (checkboxes)

The fresh Cascade should work down this list, marking each as it completes:

```
[ ] T1.1  schema/agent_lock.schema.json
[ ] T1.2  examples/tasks.example.json
[ ] T1.3  examples/lockfile.simple.example.json
[ ] T1.4  examples/lockfile.dag.example.json
[ ] T1-G  Tier 1 acceptance gate (validate examples)
[ ] T2.1  pyproject.toml
[ ] T2.2  acg/__init__.py
[ ] T2.3  acg/schema.py
[ ] T2.4  acg/llm.py
[ ] T2.5  acg/solver.py + tests
[ ] T2.6  acg/predictor.py + tests
[ ] T2.7  acg/compiler.py
[ ] T2.8  acg/explain.py
[ ] T2.9  acg/enforce.py + tests
[ ] T2.10 acg/report.py
[ ] T2.11 acg/cli.py
[ ] T2-G  Tier 2 acceptance gate (CLI roundtrip + tests)
[ ] T3.1  graph_builder/package.json + tsconfig.json
[ ] T3.2  graph_builder/scan.ts
[ ] T3-G  Tier 3 acceptance gate (graph emission)
[ ] T4.1  demo-app/ (cloned + minimal expansion)
[ ] T4.2  demo-app/tasks.json
[ ] T4-G  Tier 4 acceptance gate (graph + 6 files + 3 hotspots)
[ ] T5-G  Tier 5 acceptance gate (BLOCKED + allowed)
[ ] T6.1  benchmark/runner.py
[ ] T6-G  Tier 6 acceptance gate (chart PNG)
[ ] T8.1  README.md
[ ] T8.2  HANDOFF.md
[ ] T8.3  docs/CITATIONS.md (skeleton; teammate fills)
[ ] T8.4  docs/ASUS_DEPLOYMENT.md
[ ] T8.5  docs/COGNITION_INTEGRATION.md
[ ] T8.6  docs/ARCHITECTURE.md
[ ] T8-G  README renders cleanly on github.com
[ ] T9.1  .gitignore, .env.example, Makefile
[ ] T7.1  mcp_server/server.py            (STRETCH — only if T1-T8 done with buffer)
[ ] T7-G  MCP inspector connects          (STRETCH)
```

---

## Code quality requirements (judges will read this)

1. **Type hints on every public function.** Use Python 3.11+ syntax (`list[str]`, `dict[str, int]`, `X | None`). No `Optional` or `List`.
2. **Docstrings on every module and every public function.** One-line summary + Args + Returns. Google or NumPy style, pick one and stick to it.
3. **Pydantic v2 models** for all structured data crossing module boundaries.
4. **No silent except.** Every `except` clause names a specific exception type or re-raises.
5. **Tests:** at minimum, `test_solver.py` (DAG correctness), `test_schema.py` (lockfile validation), `test_predictor.py` (with mocked LLMClient). Aim for ≥70% coverage of `acg/solver.py` and `acg/enforce.py`.
6. **No prints in library code.** Use `rich.console.Console` from CLI, return values from libraries.
7. **Lint clean:** `ruff check .` exits 0. `ruff format --check .` exits 0.
8. **No magic numbers** in solver. All thresholds (confidence cutoff, hotspot threshold) are module-level constants with comments.
9. **Module size cap:** no Python file over 300 lines. If a module grows past this, split it.
10. **Commit hygiene:** one logical change per commit. Commit messages follow `tier.X: <verb> <subject>` (e.g., `T2.5: implement solver with topological grouping`).

---

## Demo asset checklist (block submission until all present)

```
[ ] agent_lock.json (real, generated against demo-app)
[ ] .acg/context_graph.json
[ ] .acg/run_naive.json
[ ] .acg/run_acg.json
[ ] .acg/benchmark.png
[ ] README.md (Devpost-grade)
[ ] docs/CITATIONS.md (every quote verified by Prajit)
[ ] docs/ASUS_DEPLOYMENT.md
[ ] docs/COGNITION_INTEGRATION.md
[ ] docs/ARCHITECTURE.md
[ ] demo-video.mp4 (2:20-2:40)
[ ] Devpost page (Cognition track)
[ ] Devpost page (ASUS track, separate submission)
```

---

## Forbidden actions (do not violate)

- Do not write a tree-sitter parser from scratch. Use ts-morph + tree-sitter-python bindings.
- Do not add a frontend dashboard beyond the single PNG chart.
- Do not implement the Cascade pre_write_code hook in v1 (separate stretch plan).
- Do not say "2× token reduction" anywhere. Say "conflict surface dropped from X to Y."
- Do not register on Agentverse before MCP server (Tier 7) is stable.
- Do not use Devin Manage Devins recursively to build ACG (clean control).
- Do not let any single Python file exceed 300 lines.
- Do not paraphrase a citation; if you cannot verify it verbatim, soften the README language until you can.
- Do not commit secrets to git (`.env` is gitignored).
- Do not add dependencies not pinned in `pyproject.toml`.
- Do not skip Tier acceptance gates.

---

## Open decisions (flag for human if reached)

1. **Demo-app starter choice:** if the recommended `vercel/next.js with-prisma` example is too thin, ask Shashank/Prajit which starter to use. Acceptable substitutes: t3-stack new-app, a hand-built 8-file Next.js + Prisma scaffold, or a small open-source repo (e.g. cal.com clone fragment).
2. **GX10 access:** if ASUS hardware is not yet accessible, document it in `docs/ASUS_DEPLOYMENT.md` as "tested on equivalent NVIDIA GB10 spec; live demo deployed at venue" and proceed with Groq for development. If GX10 access is denied entirely, the ASUS submission narrative becomes "designed for ASUS GX10; Groq used as a free-tier proxy showing the same provider-agnostic client architecture."
3. **Devin availability:** if Devin platform comes back online with at least 4 hours of build buffer, run the `tasks.example.json` task set through 1-2 real Devin sessions, capture screenshots, embed in README. If not, do not fake it.
4. **Real benchmark numbers:** if simulated benchmark (Tier 6 v1) produces numbers too flat to differentiate, ask Shashank whether to (a) tune the simulation to be more realistic, (b) run real Aider/Claude Code sessions on the demo-app, or (c) reframe the chart as "predicted vs achieved" rather than "naive vs planned."
5. **Submission timing:** if hour 30 hits and any of `Devpost ASUS`, `MCP server`, or `agentverse adapter` is incomplete, drop it and ship Cognition submission cleanly.

---

## When you finish

Final pre-submission checklist (Shashank or Prajit, not the fresh Cascade):

```
[ ] All Tier acceptance gates pass
[ ] All citations verified or softened in CITATIONS.md
[ ] README renders cleanly on github.com
[ ] Demo video uploaded (Drive or YouTube)
[ ] Devpost (Cognition) submitted with: video link, GitHub link, README excerpts, sponsor track checkbox
[ ] Devpost (ASUS) submitted with: same video, same GitHub, ASUS_DEPLOYMENT.md highlights, ASUS track checkbox
[ ] GitHub repo set to public, license MIT, releases tagged v0.1.0
[ ] CITATIONS.md is the very last thing to edit (in case quotes need softening at the wire)
```

If everything in this document was followed, what you ship is a credible top-3 contender on the Cognition track and a plausible top-5 on the ASUS track.
