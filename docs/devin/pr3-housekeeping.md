# PR 3 — Housekeeping: CI + docs refresh + repo cleanup

> **Copy everything below this line into Devin verbatim.**

---

## Project context

You are working on **`cognition`**, a Python+TypeScript repo. Phases 1-2
plus Track A (test-scaffold predictor seed) just shipped. The repo's CI
story is non-existent, the root README and HANDOFF docs are stale (last
updated before the runtime + viz live mode landed), and there are two
diagnostic scripts cluttering the repo root.

## Deliverables

### 1. GitHub Actions CI (`.github/workflows/ci.yml`)

A workflow that runs on every push and pull-request to `main`. Two jobs.

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  python:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - name: Install
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
      - name: Pytest
        run: pytest tests/ -q
      - name: Ruff
        run: ruff check acg/ tests/ benchmark/

  viz:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: "20"
          cache: npm
          cache-dependency-path: viz/package-lock.json
      - name: Install
        working-directory: viz
        run: npm ci
      - name: Typecheck
        working-directory: viz
        run: npx tsc --noEmit
      - name: Build
        working-directory: viz
        run: npm run build
```

Confirm `pyproject.toml` declares an `[project.optional-dependencies] dev`
group. If not, add one with `pytest>=8`, `ruff>=0.6`. Don't touch any
production dep entries.

Add a CI status badge to the top of root `README.md` after you update it
in deliverable 3.

### 2. HANDOFF.md refresh

Replace the existing `HANDOFF.md` (and delete `HANDOFF_NEXT.md` after
folding any still-relevant items). New content structure:

```markdown
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
```

Tone: terse, factual, no marketing. ~300 words.

### 3. Root README.md refresh

The current root `README.md` predates the runtime and viz live mode. Add a
**"Live execution mode"** section after the existing intro:

````markdown
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
````

Also add the CI badge at the top: `![ci](https://github.com/<org>/cognition/actions/workflows/ci.yml/badge.svg)` — leave `<org>` as a literal placeholder; the human author replaces on merge.

### 4. Repo cleanup

Move two diagnostic scripts:

```
test_5.py            → scripts/diagnostics/llm_call_probe.py
probe_thinking.py    → scripts/diagnostics/reasoning_content_probe.py
```

Add `scripts/diagnostics/README.md`:

```markdown
# Diagnostic scripts

One-shot probes for hand-debugging the LLM servers. Not part of the test
suite or CI.

| Script | What it does |
|--------|--------------|
| `llm_call_probe.py` | Sends 4 parallel chat completions (1 orchestrator + 3 workers) and prints token/timing telemetry. Useful for verifying both ports are alive. |
| `reasoning_content_probe.py` | Probes the orchestrator-port server for `reasoning_content` field presence; useful when changing reasoning-budget config. |

Run with `python scripts/diagnostics/<script>.py`.
```

Don't change the script bodies — just move them.

### 5. Schema field-level docstrings

For both `schema/agent_lock.schema.json` and `schema/run_trace.schema.json`:
add a `description` field to every property in every object. Examples:

```json
"properties": {
  "id": {
    "type": "string",
    "description": "Stable, kebab-case task identifier; unique within a lockfile."
  },
  "predicted_writes": {
    "type": "array",
    "description": "Predictor's anticipated file edits, sorted by descending confidence.",
    "items": {"$ref": "#/$defs/PredictedWrite"}
  },
  ...
}
```

Aim for one line per property, ~10-15 words. Match the tone of the
Pydantic docstrings in `acg/schema.py`.

### 6. (Optional, time-permitting) `acg validate-lockfile` CLI

In `acg/cli.py`, add a `validate-lockfile` command that loads a lockfile
JSON, validates it against `schema/agent_lock.schema.json` via
`jsonschema`, and prints "OK" or the first validation error. Mirror the
existing `validate-write` style. Add 2 tests in `tests/test_cli.py` (create
if it doesn't exist).

If you don't have time, skip — this is genuinely optional.

## Branch / commit / PR conventions

- Branch: `housekeeping-ci-docs` from `main`
- Commits:
  ```
  ci: add GitHub Actions workflow (pytest + ruff + tsc + viz build)
  docs: refresh HANDOFF.md to reflect Phase 1+2+Track A
  docs: add live execution mode section to root README
  scripts: move diagnostic probes to scripts/diagnostics/
  schema: add field-level descriptions to agent_lock + run_trace schemas
  cli: add validate-lockfile command  # only if you got to deliverable 6
  ```
- PR title: `Housekeeping: CI + docs + cleanup`
- PR description: list which deliverables landed (1-5 mandatory, 6 optional).

## Acceptance gates

```bash
./.venv/bin/python -m pytest tests/ -q       # all tests still green
./.venv/bin/ruff check acg/ tests/ benchmark/
cd viz && npx tsc --noEmit && npm run build  # CI mirrors this
```

The CI workflow itself only verifies once you push — that's expected.

## DO NOT

- Modify `acg/predictor.py`, `acg/runtime.py`, or anything in `viz/src/` or
  `demo-app/`. PRs 1 and 2 own those surfaces.
- Add a `dependabot.yml` or any release workflow. CI is intentionally
  minimal for this PR.
- Rewrite the project intro paragraph in `README.md` — only ADD the "Live
  execution mode" section. The existing copy is the human author's voice.
- Delete `viz/README.md` or `docs/plans/`. They stay.
