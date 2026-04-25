# HANDOFF — 2026-04-25 ~10:30 PT

> **Read this first if you are a fresh agent picking up the project.**
> The previous agent's context window was filling up; this file captures
> everything important from the session so you don't miss anything.
> An older `HANDOFF.md` exists and is still partially relevant (acceptance
> gates, Tier 1/2/3 framing) — read this file first, then skim that one
> if needed.

---

## TL;DR — Where We Are

**ACG planner is fully wired and producing correct lockfiles against real
Gemma 4 over Tailscale.** Visualizer renders the lockfile. All 22 tests
pass. The next demo-critical piece is the **orchestrator + sub-agent
runtime** — that hasn't been built yet, and that's your job.

---

## What's Live Right Now

### Hardware / Network

- **Mac (dev machine):** the user works here. Repo at `/Users/prajit/Desktop/projects/cognition`.
- **ASUS GX10:** 128 GB unified memory, llama.cpp with Vulkan backend, hostname `gx10-f2c9` over Tailscale.
- **Tailscale tunnel:** Mac ↔ GX10 confirmed working both directions.

### Two llama-server instances on the GX10

| port | role | flags | typical latency |
|---|---|---|---|
| **8080** | sub-agents / predictor | `--reasoning-budget 0 --parallel 4 --jinja` | ~2 s |
| **8081** | orchestrator | `--reasoning-budget 4096 --parallel 1 --jinja` | ~30–40 s |

Both serve the same model: `~/models/gemma-4-26B-A4B-it-GGUF/gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf`.

VRAM footprint: ~16 GB per instance, ~32 GB total — comfortable on 128 GB.

### Server startup command (run on ASUS, in `~/llama.cpp`)

```bash
TS_IP=$(tailscale ip -4 | head -1)
MODEL=~/models/gemma-4-26B-A4B-it-GGUF/gemma-4-26B-A4B-it-UD-Q4_K_XL.gguf

pkill -f llama-server 2>/dev/null
sleep 2

# Sub-agent server :8080 — no thinking
nohup ./build/bin/llama-server \
  -m "$MODEL" -c 8192 --parallel 4 --cont-batching \
  --host "$TS_IP" --port 8080 \
  --jinja --reasoning-budget 0 \
  > ~/llama-subagents.log 2>&1 &

# Orchestrator server :8081 — thinking allowed
nohup ./build/bin/llama-server \
  -m "$MODEL" -c 8192 --parallel 1 --cont-batching \
  --host "$TS_IP" --port 8081 \
  --jinja --reasoning-budget 4096 \
  > ~/llama-orchestrator.log 2>&1 &

sleep 5
ss -tlnp | grep -E ":(8080|8081)"
```

To stop both: `pkill -f llama-server`.

### Verify from Mac

```bash
make gemma-ping             # both ports return 200
./.venv/bin/python probe_thinking.py    # confirms reasoning behaviour per port
```

---

## Hard Facts About llama-server (Discovered The Hard Way)

1. **Per-request thinking overrides DO NOT WORK on this build.**
   `chat_template_kwargs.enable_thinking`, `chat_template_kwargs.thinking`,
   `reasoning_effort`, `reasoning_budget`, root-level `thinking` — all
   silently ignored. Server-wide `--reasoning-budget` is the ONLY knob.
   That's why we run two instances. Don't waste time trying to make
   per-request work; verified via `probe_thinking.py` against both ports.

2. **`--jinja` is required** for the Gemma chat template to parse correctly.
   Without it, BOS tokens leak into output as literal text.

3. **`--reasoning-budget 0`** completely removes the `reasoning_content`
   field from the response — not just "empty" but absent. Useful as a
   detection signal (`"reasoning_content" in msg.keys()` ⇒ thinking is on).

4. **Thinking is slow but content-quality-equivalent on our prompts.**
   For the orchestrator dispatch decisions we have, no-think outputs are
   structurally identical to thinking outputs. Thinking is buying us
   *demo narrative*, not *correctness*. Use it sparingly — once per run.

---

## What Was Done This Session

### Code changes shipped

| file | change |
|---|---|
| `graph_builder/scan.ts` | Added `collectAssetFiles` to enumerate non-TS assets (`prisma/**/*.prisma`, root configs). `.prisma` files auto-flagged as hotspots. Recovered the `prisma/schema.prisma` conflict that was being missed. |
| `Makefile` | Added `GEMMA_HOST`, `GEMMA_PORT`, `GEMMA_ORCH_PORT`, `GEMMA_ENV`, `GEMMA_ORCH_ENV` vars. Added targets `gemma-ping`, `compile-gemma`, `demo-gemma`. |
| `viz/src/components/Toolbar.tsx` | Removed header title `"ACG · agent_lock.json"` and metadata strings. Toolbar now just hosts Play / Reset buttons. Sidebar still shows lockfile metadata. |
| `viz/src/App.tsx` | Dropped now-unused Toolbar props. |
| `probe_thinking.py` (new, root) | Diagnostic script that probes per-request reasoning override knobs against any port. Keep around for future server-build sanity checks. |

### Architectural decisions

1. **Two-server split** for thinking vs no-think. Confirmed Option A (per-request override) is dead.
2. **Asset enumeration in graph_builder** rather than predictor speculation. Topical seed for `"prisma"` now finds `prisma/schema.prisma` deterministically.
3. **Conservative predictor system prompt is the right default** — speculation belongs in the seed step, not the LLM. Don't loosen this without thought.

### Verified outcomes

- `make compile-gemma` produces a 2-conflict, 3-group lockfile that matches the canonical mock structurally.
- All 22 pytest tests pass.
- Visualizer renders the new lockfile correctly with HMR.

---

## Architecture Crash Course

### Two graphs, don't confuse them

1. **Repo graph** (`demo-app/.acg/context_graph.json`) — file-level. Built by `graph_builder/scan.ts`. `{files, symbols_index, hotspots, language}`. Now includes asset files (prisma schemas, root configs) in addition to TS sources.

2. **Execution DAG** (inside `agent_lock.json`'s `execution_plan.groups`) — task-level. Nodes = tasks, edges = (a) conflict-derived serializations + (b) explicit `depends_on`. Built by `acg/solver.py`.

### Predictor pipeline (`acg/predictor.py`)

For each task: 3 deterministic seeds + 1 LLM rerank.

- **`_static_seed`** (conf 0.95): regex matches verbatim file paths in prompt (e.g. `prisma/schema.prisma`).
- **`_symbol_seed`** (conf 0.85): camelCase tokens >5 chars looked up in `symbols_index`.
- **`_topical_seed`** (conf 0.7): hint keywords substring-matched against file paths in graph.
- **LLM rerank**: send Gemma the task + 50-file graph slice + current seeds → JSON `{writes: [...]}`. Merged with seeds, LLM confidence wins on overlapping paths.

### Compiler (`acg/compiler.py`)

- Calls predictor per task → `predicted_writes`.
- `_build_allowed_paths`: high-confidence multi-segment writes get broadened to `dir/**` globs; low-confidence stays exact.
- `_resolve_dependencies`: explicit `depends_on` from tasks.json + heuristic "test-flagged tasks depend on every non-test task" (this is why `tests` shows ↑3 deps even when not declared).

### Solver (`acg/solver.py`)

```python
1. Add a node per task.
2. For every conflict pair (i, j) with overlapping predicted_writes, add edge
   from FEWER-conflicts task → MORE-conflicts task. Tie-broken by index.
3. Add explicit dependency edges from depends_on.
4. Assert acyclic.
5. Topological-group: layer of nodes with no remaining predecessors.
   Layer type = "parallel" if multi-task else "serial".
```

For the demo lockfile this produces:
```
oauth ──┐
settings ──┴──► billing ──► tests
```
Groups: `G1[oauth, settings] → G2[billing] → G3[tests]`.

### Enforcement primitive (`acg/enforce.py`)

```python
validate_write(lock: AgentLock, task_id: str, write_path: str) -> tuple[bool, str | None]
```

Returns `(True, None)` if `write_path` matches any of the task's `allowed_paths` globs (with `**` support), else `(False, reason)`. **This is your enforcement primitive for the runtime — use it on every proposed file write.**

---

## Commands You Should Know

```bash
# From repo root, on the Mac:

make gemma-ping              # check both servers
make scan                    # rebuild context_graph.json
make compile-gemma           # rebuild agent_lock.json using port 8080
make explain                 # human-readable lockfile summary
make test                    # run full pytest suite (22 tests)
make viz                     # start visualizer dev server
make compile-gemma GEMMA_HOST=100.x.y.z   # override hostname

./.venv/bin/python probe_thinking.py     # diagnostic for the no-think port
LLM_URL=http://gx10-f2c9:8081/v1/chat/completions \
  ./.venv/bin/python probe_thinking.py   # diagnostic for the thinking port
```

---

## Remaining Work — Prioritized

### Demo-critical (must ship)

1. **Orchestrator agent** — calls port 8081 once for a thinking dispatch decision. ⬅ **YOUR JOB**.
2. **Sub-agent runtime** — calls port 8080 in parallel per group. ⬅ **YOUR JOB**.
3. **Mid-flight write enforcement** — every proposed write goes through `validate_write`. ⬅ **YOUR JOB**.
4. **Real benchmark numbers** — replace the synthetic timings in `benchmark/runner.py` with real wall-clock from the runtime.
5. **Design doc** (1 page, diagram + invariants).
6. **Citations honesty pass** — `docs/CITATIONS.md` flags everything as unverified.

### Demo-strong (should ship)

7. Visualizer "Files view" — predicted-write files as nodes, hotspots highlighted.
8. Visualizer live mode — stream sub-agent activity from runtime trace.
9. ~~Per-request thinking control~~ — **CONFIRMED IMPOSSIBLE on this server build**. Skip.
10. FastMCP wrapper exposing `acg.compile`, `acg.validate_write`, `acg.explain` as MCP tools.
11. Predictor prompt tightening for speculative paths (`src/app/dashboard/<X>/page.tsx`-class misses).

### Stretch

12. Real Devin sessions as sub-agent runtime.
13. Cascade `pre_write_code` hook (Windsurf integration).
14. `ground_truth.json` for predictor precision/recall.
15. Stress test on a bigger repo than `demo-app`.
16. Confidence calibration analysis (Gemma is handing out 1.00 a lot — verify).

---

## Your Immediate Task: Build `acg/runtime.py`

### Goal

End-to-end runtime that reads `agent_lock.json`, calls the orchestrator once, fans out sub-agents per group, validates every proposed write against `allowed_paths`, and emits a structured run trace.

**Important:** sub-agents should *propose* writes (JSON), not actually mutate files. v1 is a proposal-and-validation loop. Real file writes can come later. This makes the demo controllable and the BLOCKED moments reproducible.

### Architecture

```
1. Load agent_lock.json via AgentLock.model_validate_json
2. RuntimeConfig.from_env() — orchestrator URL/model + sub-agent URL/model
3. Orchestrator pass (port 8081, thinking, ~30 s):
   - Prompt: "Here's the plan; reason about its soundness; emit dispatch JSON."
   - Capture {content, reasoning_content, completion_tokens, wall_s}.
4. For each group in execution_plan.groups (in order, respecting waits_for):
   - asyncio.gather over the group's tasks:
     - Sub-agent call (port 8080, no think, ~2 s):
       - Prompt: "Task: <prompt>; here are relevant files: <graph slice>;
                  output JSON {writes: [{file, description}, ...]}"
       - Parse reply (handle ```json fences and prose) into proposals.
     - For each proposal:
       - allowed, reason = validate_write(lock, task.id, proposal.file)
       - Record allowed/blocked with reason.
   - Move to next group only after all parallel tasks finish.
5. Emit run trace to demo-app/.acg/run_trace.json with:
   - orchestrator: {content, reasoning, wall_s, tokens, finish_reason}
   - workers: [{task_id, proposals, allowed, blocked, wall_s, ...}]
   - groups_executed: [1, 2, 3, ...]
   - started_at, finished_at, total_wall_s
```

### File: `acg/runtime.py`

Aim for ~300–400 lines, single file, well-commented.

Key components:

- **`RuntimeConfig`** dataclass + `from_env()` reading `ACG_ORCH_URL`, `ACG_ORCH_MODEL`, `ACG_ORCH_API_KEY`, `ACG_LLM_URL`, `ACG_LLM_MODEL`, `ACG_LLM_API_KEY`. Sensible defaults pointing at `gx10-f2c9:8080` / `:8081`.

- **`RuntimeLLM`** async httpx client. Returns `LLMReply{content, reasoning, completion_tokens, finish_reason, wall_s}`. Don't fold this into the existing `LLMClient` (sync, used by the predictor) — make a separate async cousin.

- **Dataclasses:** `WriteProposal`, `WriteValidation` (allowed bool + reason), `WorkerResult`, `OrchestratorResult`, `RunResult`. All `asdict`-friendly so the JSON trace is one `json.dumps(asdict(run_result), indent=2)` away.

- **`run_orchestrator(lock, llm) -> OrchestratorResult`** — one async call.

- **`run_worker(task, lock, repo_graph, llm) -> WorkerResult`** — one async call + post-validation.

- **`run_group(group, lock, repo_graph, sub_llm) -> list[WorkerResult]`** — `asyncio.gather` over tasks.

- **`run_lockfile(lock, repo_graph, orch_llm, sub_llm) -> RunResult`** — top-level entrypoint.

- **Event-style stdout output.** Print one line per event so the user can watch progress live: `[orchestrator] thinking...`, `[worker oauth] proposed 3 writes`, `[validator] ALLOWED oauth → src/server/auth/config.ts`, `[validator] BLOCKED billing → src/server/random.ts: outside allowed_paths`. Use `rich` or plain prints, your choice.

### Worker prompt (suggested)

```text
SYSTEM: You are a coding agent assigned a single task. Output ONLY a JSON
        object with key "writes": an array of objects with keys "file"
        (repository-relative path) and "description" (one sentence).
        Do not include prose, code fences, or any other text.

USER:   Task: {task.prompt}
        Hints: {task.hints or "none"}
        Available files in this repo (top 30 by importance):
        {short list of file paths from context_graph}
```

**Do NOT tell the worker its `allowed_paths`.** Let it propose freely; the validator catches violations. This is more honest and produces occasional BLOCKED moments for the demo.

### Orchestrator prompt (suggested)

```text
SYSTEM: You are an orchestrator analyzing a multi-agent execution plan for
        coding tasks. Reason carefully about whether the plan respects all
        write conflicts. Output ONLY a JSON object with keys:
        - "approved" (boolean)
        - "concerns" (list of short strings)
        - "dispatch_order" (list of group ids in execution order)

USER:   Lockfile summary:
        - Tasks (N): id, prompt, predicted_writes summary
        - Conflicts (M): files, between_tasks, current resolution
        - Execution plan: groups with tasks and waits_for
```

The orchestrator's reasoning_content is the demo's "watch the model think"
moment. Save it verbatim in the run trace.

### CLI integration (`acg/cli.py`)

```python
@app.command("run")
def cmd_run(
    lock: Annotated[Path, typer.Option(...)],
    repo: Annotated[Path, typer.Option(...)],
    out: Annotated[Path, typer.Option(...)],
) -> None:
    """Execute the lockfile under runtime enforcement."""
    import asyncio
    from .runtime import RuntimeConfig, RuntimeLLM, run_lockfile

    lockfile = AgentLock.model_validate_json(lock.read_text())
    repo_graph = _load_repo_graph(repo)
    cfg = RuntimeConfig.from_env()
    orch = RuntimeLLM(cfg.orch_url, cfg.orch_model, cfg.orch_api_key)
    sub = RuntimeLLM(cfg.sub_url, cfg.sub_model, cfg.sub_api_key)
    result = asyncio.run(run_lockfile(lockfile, repo_graph, orch, sub))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(result), indent=2, default=str) + "\n")
    _console.print(f"[green]wrote[/] {out}")
```

### Makefile target

```makefile
run-gemma:
	$(GEMMA_ENV) $(GEMMA_ORCH_ENV) ./.venv/bin/acg run \
	  --lock demo-app/agent_lock.json \
	  --repo demo-app \
	  --out demo-app/.acg/run_trace.json
```

### Tests (`tests/test_runtime.py`)

Mirror the pattern in `tests/test_predictor.py`. Stub LLM that returns canned JSON.

Minimum coverage:

- `test_runtime_executes_groups_in_order` — group N+1 doesn't start until group N finishes.
- `test_runtime_blocks_writes_outside_allowed_paths` — proposing a path outside `allowed_paths` produces a BLOCKED entry, not an ALLOWED one.
- `test_runtime_allows_writes_within_allowed_paths` — happy path.
- `test_runtime_handles_malformed_worker_reply` — non-JSON reply doesn't crash the run; produces zero proposals.

### Verification after build

```bash
make compile-gemma          # ensure lockfile is fresh
make run-gemma              # NEW — should take ~40 s (orchestrator) + a few s (sub-agents)
cat demo-app/.acg/run_trace.json | jq .orchestrator.wall_s
cat demo-app/.acg/run_trace.json | jq '.workers[] | {task_id, allowed: .allowed_writes | length, blocked: .blocked_writes | length}'
make test                   # 22 → 26-ish passing
```

### Things to AVOID

- **Don't change the existing predictor or compiler** — they work, tests pin them.
- **Don't actually mutate files in v1** — propose-and-validate only.
- **Don't try to make per-request thinking overrides work** — confirmed broken.
- **Don't combine `RuntimeLLM` with the existing sync `LLMClient`** — they have different access patterns and the sync client is used by the predictor at compile time. Keep them separate.
- **Don't add `reasoning_content` to `LLMClient`** for the same reason.
- **Don't rebuild the visualizer** — viz polish is a separate task.
- **Don't write a `PLAN.md`** — the user previously declined that option; this file plus the existing HANDOFF.md are enough.

---

## Files To Read First (In Order)

1. `acg/llm.py` — sync httpx client used by predictor; your `RuntimeLLM` is its async cousin.
2. `acg/enforce.py` — `validate_write()` is your enforcement primitive.
3. `acg/cli.py` — add `acg run` here following the existing pattern.
4. `acg/schema.py` — Pydantic models. `AgentLock`, `Task`, `Group`.
5. `demo-app/agent_lock.json` — example lockfile to test against.
6. `demo-app/.acg/context_graph.json` — example repo graph slice for worker prompts.
7. `probe_thinking.py` — example of calling the live servers and parsing replies (including the `keys: [...]` reasoning detection).
8. `Makefile` — pattern for env-var-bracketed targets.
9. `tests/test_predictor.py` — pattern for stubbed-LLM tests; copy this style.

---

## Bootstrap Prompt For The Next Agent

Copy-paste this into a fresh agent session:

```
Read /Users/prajit/Desktop/projects/cognition/HANDOFF_NEXT.md in full
before doing anything else. That file is the canonical context for the
project — architecture, what's been done, hard facts about the llama-server
build, and the spec for your immediate task.

Your job: build acg/runtime.py per the spec in section "Your Immediate
Task". The user has two llama-server instances running over Tailscale
(gx10-f2c9:8080 no-think, gx10-f2c9:8081 thinking). Build an end-to-end
async runtime that:

  1. reads agent_lock.json
  2. calls the orchestrator (port 8081) once for a thinking dispatch plan
  3. fans out sub-agents per execution group via asyncio.gather (port 8080)
  4. each sub-agent proposes file writes as JSON (do NOT tell it the
     allowed_paths — let it propose freely)
  5. every proposed write goes through validate_write() against the lockfile
  6. writes a run trace to demo-app/.acg/run_trace.json

Do NOT mutate files in v1 — workers propose, validator approves or blocks.
Keep the existing predictor and compiler unchanged. After implementing,
add an `acg run` CLI command, a `make run-gemma` Makefile target, and at
least 4 tests in tests/test_runtime.py mirroring the test_predictor style.
Run the full pytest suite to confirm 22 → 26-ish passing. Then do a real
end-to-end run against the live servers and paste the trace summary back
to the user (orchestrator wall_s, per-task allowed/blocked counts).

Constraints:
- Do not waste tool calls running my-side commands; ask the user to run
  them and paste output. Especially server-side commands.
- Do not try per-request reasoning overrides — confirmed broken on this
  server build via probe_thinking.py.
- Do not invent new directories. Single file acg/runtime.py is fine.
- Keep all comments minimal unless the user asks for more.
```

---

## Open Questions / Known Issues

- **Worker reply parsing** is fragile. Gemma sometimes wraps in ```json fences, sometimes in prose, sometimes returns inline arrays without the wrapper object. Use the same forgiving parser pattern that `acg/predictor.py:_parse_llm_writes` uses (regex strip fences → `json.loads` → fall back to `{...}` substring extraction).
- **Worker hallucinations.** Without the graph slice in the prompt, workers will invent plausible paths (`src/utils/helpers.ts`) that don't exist. This is fine — the validator blocks them, which is good demo material — but if you want grounded output, include the graph slice.
- **Orchestrator output format.** Gemma sometimes prepends prose to the JSON (`Here is the analysis: {...}`). Use the same forgiving parser.
- **Sub-agent fairness.** Port 8080 has `--parallel 4`. If a group has more than 4 tasks, asyncio.gather will queue. That's fine; just note it in logs.
- **Demo-app `tests` task** has `predicted_writes: [{path: "tests"}]` (a directory, not a file). The worker may need extra hand-holding for this case. Consider: if `predicted_writes` is a directory-only path, ask the worker to propose a specific file under that directory.

---

## Useful Pointers

- **Sidebar.tsx** is in BOTH `billing` and `settings` predicted_writes — that's the demo's single inter-group conflict. After your runtime runs, you should see two ALLOWED writes to `Sidebar.tsx` (one per task) but in different groups thanks to the solver having serialized them.
- **`prisma/schema.prisma`** is in BOTH `oauth` and `billing` predicted_writes — but they're already separated into different groups (G1 vs G2) by the solver. ALLOWED for both.
- **A "BLOCKED" demo moment** can be manufactured by adding a task to `tasks.json` with a misleading prompt that the worker would naturally answer with a path outside the predicted writes (e.g. a "rewrite the auth flow" task that tempts the worker to touch `src/components/Sidebar.tsx`).

---

End of handoff. Good luck.
