# Agent Context Graph — Project-Deciding Plan

A pre-flight, DAG-shaped disjointness compiler for multi-agent code agents, exposed as an MCP server, that prevents file-conflict merge-tax in fan-out workflows like Devin Manage Devins.

---

## 1. The decision

**We are building Agent Context Graph (ACG)** — an open-source, local-first MCP server that takes a repository and a list of agent tasks, statically predicts the write-set for each task, and emits a committable `agent_lock.json` describing a DAG of tasks (parallel-safe edges + serial-dependent edges). The lockfile is enforced at runtime via a Windsurf Cascade `pre_write_code` hook and consumed by Devin / Claude Code / OpenCode via the MCP protocol.

**Primary submission:** Cognition track at LA Hacks 2026.
**Secondary submission (decide Saturday night):** Fetch.ai Agentverse track via `uagents-adapter` MCPServerAdapter wrapping.

**We are not building:** a CRDT runtime layer; a multi-language graph (TS/JS + Python only); a live-demo product; a frontend dashboard with auth/users; semantic-conflict detection beyond file-level.

---

## 2. Thesis sentence

> **"Anthropic says agents should talk. Cognition says they shouldn't. We say: if the lockfile proves writes are disjoint, they don't need to."**

Sub-thesis (when more time): *ACG closes the static-analysis gap CodeCRDT (Oct 2025) named as future work, and ships the file-lock primitive OpenCode users requested in Issue #4278 (Nov 2025), as a Cognition-native MCP server.*

---

## 3. Evidence base — the three smoking guns

| Source | Quote / Fact | Why it matters |
|---|---|---|
| **CodeCRDT paper (arXiv:2510.18893, Oct 2025)** | *"Future work should use static analysis (data-flow graphs, shared variable access patterns) for objective coupling measurement."* Reports 5–10% semantic conflicts that CRDT cannot resolve. | We are explicitly building the future-work paragraph from a 6-month-old paper. Clean academic positioning. |
| **OpenCode Issue #4278 (Nov 2025, closed "completed" but unimplemented)** | *"Multiple OpenCode clients and/or agents don't stomp on each other's changes… Running multiple agents/tools in parallel that all use OpenCode can easily end up overwriting each other's changes."* | Real users asking for our exact thing in writing on a Cognition-affiliated repo. Demand is documented. |
| **Cognition docs + Walden Yan (Jason Liu blog, Sep 2025)** | Devin Manage Devins coordinator "resolves conflicts" with **zero documented mechanism**. Yan: *"With any agentic system, lots of actions carry these implicit decisions… you almost always have to make sure this decision is shared with everyone else, or else you might just get these conflicting decisions."* | The pain is acknowledged at the highest level; the fix is not. We ship the fix. |

**Supporting (not lead) evidence:**
- Cognition engineer's verbal pain: 9-agent Java upgrade migrations create cascading dependent PRs requiring manual approval. Public evidence is thin — treat as field validation, not headline.
- Agint (arXiv:2511.19635, Nov 2025) — runtime DAG compiler for SE agents. Adjacent but distinct: theirs runs DAGs, ours statically verifies disjointness before dispatch.

---

## 4. Sponsor strategy

### Primary: Cognition track

| | Value |
|---|---|
| 1st | $3,000 + 1,000 ACUs + Windsurf Pro 1yr + eng convo |
| 2nd | $2,000 + 1,000 ACUs + Windsurf Pro 1yr + eng convo |
| 3rd | $1,000 + 1,000 ACUs + Windsurf Pro 1yr + eng convo |
| Honorable Mention | Cognition Swag Pack |

**Submission requirements:** Devpost page, public GitHub repo, **2–3 minute demo video** (recorded, not live), project description naming the track. No mandatory Devin/Windsurf use clause. No license requirement.

### Secondary: Agentverse / Fetch.ai (decide Saturday night)

Build the MCP server with **`FastMCP` (Python) + `uagents-adapter` MCPServerAdapter** from day one. Adds zero marginal cost during build. If by Saturday 8pm we have working benchmarks and polish remaining, register on Agentverse with `Innovation Lab` badge in agent metadata, submit. If time is tight, skip the dual-submission with no architectural cost.

**Hard rule:** Cognition pitch is the primary story. Agentverse is opportunistic. Do not let Agentverse customization compromise the Cognition narrative.

### Why not other tracks

- ASUS / Aramco / Sustain the Spark / Catalyst for Care — wrong domain, no AI infra fit
- Figma "Flicker to Flow" — workflow-adjacent but consumer-leaning; weak fit
- Agentverse is the only credible secondary

---

## 5. Scope — in / out

### In scope (must-ship)

- **Tree-sitter graph builder** for TypeScript/JavaScript and Python
- **Task → write-set predictor** (static-first via import closure + symbol search; LLM re-rank for task-specific files)
- **Disjointness solver** that produces a DAG with parallel and serial edges
- **`agent_lock.json` schema** — committable artifact, version-controllable, human-reviewable
- **MCP server** in Python with `FastMCP`, exposing tools: `analyze_repo`, `predict_writes`, `compile_lockfile`, `validate_writes`
- **Windsurf Cascade `pre_write_code` hook** that reads the lockfile and rejects out-of-bounds writes
- **Comparison harness** that runs the same task list through three modes (single, naive parallel, planned) against real Devin sessions and records tokens/ACUs/conflicts
- **Static frontend** (Next.js + Tailwind + Tremor) showing the money chart, task explorer, lockfile viewer, honesty box
- **2–3 minute demo video** — recorded, not live
- **Devpost submission + public GitHub repo + README**
- **Agentverse-compatible from day one** (FastMCP + uagents-adapter), submission decided Saturday night

### Explicitly out of scope

- CRDT runtime layer (CodeCRDT is complementary; we cite, do not build)
- Multi-language graph (Go, Rust, Java, C++ — not in scope)
- Semantic-conflict detection beyond file-level overlap (we own this limitation)
- Live web demo / "try it on your repo" form
- Authentication, accounts, multi-user
- Real-time WebSocket streaming
- Pretty animations / dark-mode toggle / mobile-responsive design
- Multi-trial statistical benchmarks (n=5 single-trial is acknowledged as directional)

---

## 6. The DAG insight — why this is more than a flat lockfile

A flat parallel-disjointness lockfile would handle the easy case (5 unrelated features in one PR cycle) but miss the case the Cognition engineer actually complained about (Java 6→7→8→11→17 migrations).

The DAG-shaped lockfile expresses **two edge types**:

- **Parallel-safe edges:** task A and task B can run concurrently (write-sets disjoint)
- **Serial-dependent edges:** task A must complete before task B starts (B reads/writes files A modifies)

For a typical migration:

```
[Java 6→7 across 12 files]  ←  parallelize across files
        ↓ serial
[Java 7→8 across 12 files]  ←  parallelize across files
        ↓ serial
[Java 8→11 across 12 files]
        ↓ serial
[validation pass]
```

The lockfile becomes a **single committable plan for the entire migration**, not a sequence of human-approved PRs. Devin Manage Devins reads it, fans out within each stage, blocks between stages, and only requests human review at the explicit checkpoints we declare in the lockfile.

This is the differentiation against:
- **CodeCRDT** — runtime, character-level, no static planning
- **Agint** — runtime DAG generation; we statically verify a human-supplied or LLM-generated DAG before any agent runs
- **OpenCode locks** — per-file advisory; no task graph, no serial reasoning
- **LangGraph / Airflow / Temporal** — generic workflow engines; no code-graph awareness, no per-task write-set prediction

---

## 7. Architecture sketch (high-level only)

```
┌──────────────────────────────────────────────────────────────┐
│                       INPUT                                  │
│  • Repo path                                                 │
│  • Task list (natural language, ordered or unordered)        │
└────────────────────┬─────────────────────────────────────────┘
                     │
        ┌────────────▼────────────┐
        │  GRAPH BUILDER          │
        │  • tree-sitter (TS/JS,  │
        │    Python)              │
        │  • import closure       │
        │  • symbol references    │
        │  • Codemaps fallback    │
        └────────────┬────────────┘
                     │
        ┌────────────▼────────────┐
        │  WRITE-SET PREDICTOR    │
        │  • static seed: files   │
        │    explicitly named     │
        │  • LLM re-rank to add   │
        │    task-implied files   │
        │  • report precision/    │
        │    recall on labeled    │
        │    set                  │
        └────────────┬────────────┘
                     │
        ┌────────────▼────────────┐
        │  DAG SOLVER             │
        │  • detect overlapping   │
        │    write-sets           │
        │  • emit parallel edges  │
        │    (disjoint)           │
        │  • emit serial edges    │
        │    (overlap or          │
        │    dependency)          │
        │  • topological sort     │
        └────────────┬────────────┘
                     │
        ┌────────────▼────────────┐
        │  agent_lock.json        │
        │  (committable)          │
        └────────────┬────────────┘
                     │
        ┌────────────┴─────────────┬──────────────────┐
        │                          │                  │
┌───────▼────────┐    ┌────────────▼─────────┐   ┌────▼──────────────┐
│  MCP SERVER    │    │  CASCADE HOOK        │   │  DEVIN HARNESS    │
│  (FastMCP)     │    │  pre_write_code      │   │  spawns sessions  │
│  4 tools       │    │  rejects out-of-     │   │  with structured  │
│  Devin/Claude/ │    │  bounds writes       │   │  output + tags;   │
│  Cursor can    │    │                      │   │  measures tokens, │
│  call          │    │                      │   │  ACUs, conflicts  │
└────────────────┘    └──────────────────────┘   └───────────────────┘
                                                          │
                                              ┌───────────▼───────────┐
                                              │  COMPARISON CHART     │
                                              │  single | naive |     │
                                              │  planned (3 modes)    │
                                              └───────────────────────┘
```

**MCP tool surface (final):**
- `analyze_repo(path)` → graph summary + hotspots
- `predict_writes(task, graph)` → predicted file set + confidence
- `compile_lockfile(repo, tasks)` → `agent_lock.json` DAG
- `validate_writes(lockfile, attempted_write)` → boolean + reason

---

## 8. Demo target split

| Purpose | Repo | Tasks | Sessions |
|---|---|---|---|
| **Primary benchmark (the money chart)** | Medium Next.js + Prisma starter | 5 hand-picked tasks (2 parallel-safe, 2 densely coupled, 1 borderline) | 5 × 3 modes = 15 Devin sessions |
| **Showcase narrative (Issue #4278)** | OpenCode (anomalyco/opencode) | 1–2 tasks aligned with the file-locks issue (e.g., "add per-file lock subsystem") | 3–5 sessions |
| **Total Devin spend** | | | ~18–20 sessions |

Estimated ACU spend: ~100–250 ACUs depending on task size. Within sponsor credit budget.

**Cleanup rule:** if hour-3 pilot shows the merge-tax delta is <2× on the Next.js starter, switch to a denser repo (or denser tasks) before committing to the full harness. Don't burn 20 sessions on a flat result.

---

## 9. Risk register with kill-criteria

| Risk | Likelihood | Impact | Mitigation | Kill-criteria (when to pivot) |
|---|---|---|---|---|
| Merge-tax delta is small (<2×) on chosen repo | Medium | High | Run hour-3 pilot on 1 task before full harness | If pilot shows <1.5× delta, switch repo or reframe to "reviewable artifact" angle |
| Tree-sitter graph is too thin (misses dynamic imports, DI, config coupling) | High | Medium | Be honest in honesty box; static + LLM re-rank for prediction | Acknowledge in demo; do not overclaim "proves disjointness" |
| Devin session wall-clock exceeds budget (15 sessions × 30 min = 7.5 hr) | High | Medium | Start sessions hour 6–8 in parallel, not sequentially | If at hour 18 we have <6 completed sessions, cut to n=3 and label as preliminary |
| Cascade `pre_write_code` hook doesn't behave as docs suggest | Medium | Medium | Validate hook with dummy script in hour 1 | If hook is non-functional, ship audit-only enforcement (post-hoc validator) |
| Task→file prediction LLM is unreliable | Medium | High | Hand-label predicted file sets; report precision/recall openly | If precision < 0.7, gate on user confirmation rather than full automation |
| Agentverse adapter fails to wrap our MCP | Low | Low | Build with FastMCP from day one; only Agentverse-specific shim is `uagents-adapter` | Skip Agentverse submission; no project impact |
| OpenCode showcase task fails entirely | Medium | Low | Showcase is narrative-only; primary benchmark is on Next.js | Drop showcase if not working by hour 28; lead with Next.js numbers |
| Judges interpret as "yet another multi-agent" | Medium | High | Lead-pitch language is "we make multi-agent unnecessary by proving disjointness"; never say "team," "swarm," "coordinator" | Refine pitch script before demo recording |

---

## 10. Submission deliverables

### Devpost page (priority order)

1. **Hero one-liner:** the thesis sentence
2. **The chart:** screenshot of the comparison bar chart (single/naive/planned)
3. **Inspectable artifact:** screenshot or live-viewable `agent_lock.json` for one task
4. **Three smoking guns:** CodeCRDT future-work quote, Issue #4278 link, Walden quote
5. **Honesty box:** what we don't claim (n=5, file-level only, JS/TS+Py, Cascade-required for runtime)
6. **Architecture diagram:** the box diagram from §7
7. **Demo video:** 2–3 minute recorded walkthrough
8. **Devin session links:** clickable links to ~5 representative `app.devin.ai/sessions/...` runs (trust anchor)
9. **GitHub repo:** public, MIT-licensed, README with quickstart
10. **Track tags:** "Cognition Track" + (if dual-submitting) "Agentverse Track"

### GitHub README skeleton

- Title + thesis
- Install + 60-second quickstart
- The MCP tool reference
- The lockfile schema
- Architecture diagram
- Benchmark results (with raw data CSV link)
- Limitations (mirror the honesty box)
- Citations: CodeCRDT, OpenCode #4278, Walden's blog, Anthropic multi-agent paper
- License: MIT

### Demo video shot list (2–3 min)

- 0:00–0:15 — Hook: the thesis sentence over the merge-tax chart
- 0:15–0:45 — The problem: show OpenCode Issue #4278, CodeCRDT future-work paragraph, Walden quote (3 cuts)
- 0:45–1:15 — The solution: type a task list, ACG generates the DAG lockfile (screen-capture)
- 1:15–1:45 — The proof: show 5 tasks across 3 modes, the bar chart, click into one Devin session
- 1:45–2:15 — Honesty: what we catch vs what we don't (the honesty box)
- 2:15–2:45 — How to use it: MCP install snippet, Cascade hook one-liner
- 2:45–3:00 — Close: GitHub link, who we are, thank you

---

## 11. Decision gates

| Hour | Gate | Pass criteria | Fail action |
|---|---|---|---|
| **H+1** | Cascade `pre_write_code` hook validates | Hook fires on dummy script, blocks/allows correctly | Drop runtime enforcement; ship audit-only validator |
| **H+3** | Pilot merge-tax measurement | Single vs naive shows ≥2× delta on 1 task on Next.js starter | Switch repo or reframe pitch to "reviewable artifact" before building harness |
| **H+8** | Tree-sitter graph + write-set predictor working | Generates correct write-set for ≥3 of 5 hand-labeled tasks | Cut graph builder; use LLM-only prediction with caveats |
| **H+12** | First lockfile generated | `agent_lock.json` produces valid DAG with both parallel and serial edges | Drop DAG, ship flat lockfile only |
| **H+16** | First end-to-end Devin session run via harness | Token counts and ACU spend recorded | Drop comparison, ship lockfile-only with theoretical chart |
| **H+24** | All 15 primary benchmark sessions complete | Chart populated with real data | Cut to n=3, label "preliminary" |
| **H+28** | OpenCode showcase task complete | At least 1 Issue #4278-aligned task shows correct DAG | Drop showcase from video, mention only in Devpost |
| **H+30** | Frontend deployed, README complete | Public URLs all live | Cut frontend; submit just GitHub + Devpost |
| **H+32** | Demo video recorded | 2–3 min cut, all 6 segments present | Re-record only the failing segment |
| **H+35** | Final Devpost submission | All assets uploaded, dual-submission decided | Submit Cognition only, skip Agentverse |

---

## 12. Honesty commitments (non-negotiable)

These statements appear verbatim in Devpost, README, and demo video:

1. **n=5 single-trial.** Directional evidence only. Not a benchmark paper.
2. **File-level disjointness only.** Semantic drift across disjoint files is out of scope and CodeCRDT's domain.
3. **JavaScript/TypeScript and Python only.** Other languages would need their own tree-sitter parsers and write-set heuristics.
4. **Cascade hook enforcement is Windsurf-specific.** Devin sessions are validated post-hoc, not pre-empted at write time.
5. **Task→file prediction precision/recall are reported openly** on the hand-labeled set.
6. **The merge-tax metric is novel and self-defined.** We argue it matters; we don't claim industry consensus.
7. **CodeCRDT, Agint, LangGraph, OpenCode locks are cited as related work.** We do not claim to be the first to think about multi-agent coordination — only the first to ship pre-flight static disjointness as an MCP-exposed lockfile.

---

## 13. Why we win (rubric mapping)

Cognition rubric: **Product Value | Engineering Quality | Process | Bonus**

- **Product Value:** Closes a documented gap (Issue #4278), implements named future work (CodeCRDT), addresses a Walden-acknowledged problem. Real ACU savings demonstrated empirically. Real users asking for it in writing.
- **Engineering Quality:** Static graph + LLM re-rank with reported precision/recall; DAG solver with topological sort; MCP server with 4 clean tools; Cascade hook with documented exit codes; honest limitations documented.
- **Process:** Use ACG to plan our own build (dogfooding). Tag every Devin session as `acg-self-build`. Show the self-build lockfile in the demo. Devpost includes a "We used Devin and Windsurf to build this" section enumerating which Devin sessions did what.
- **Bonus (Devin/Windsurf value):** Direct answer. Devin Manage Devins is the consumer of our MCP. Windsurf Cascade is the runtime enforcer via hooks. Codemaps is a graph fallback input. We use the entire Cognition stack on its own terms.

---

## 14. Pivot triggers (when to abandon and reshape)

We abandon the lockfile-DAG architecture and reshape if any of the following hits before H+12:

- Tree-sitter graph + LLM re-rank cannot achieve precision ≥0.7 on hand-labeled set → reshape to "lockfile is human-authored, ACG validates and enforces"
- Cascade hook is fundamentally non-functional for our use case → reshape to audit-only validator with no runtime enforcement; lead pitch with "committable artifact for human review"
- Pilot benchmark shows merge-tax delta < 1.2× on multiple repos → reshape to "Cascade hook + lockfile committed alongside PR for human review" (drop the comparison-chart pitch entirely)

If two of three pivot triggers fire, the project is in trouble. Cut to MVP: just the lockfile schema + MCP server + a worked example. Submit honest "research prototype" with limitations.

---

## 15. Open items (carry into build)

- Verify Cascade `pre_write_code` hook behavior in hour 1 (validate, exit codes, file path resolution)
- Identify the exact Next.js + Prisma starter repo to benchmark against (medium-sized, has auth + db + UI)
- Identify 5 specific tasks for the benchmark (2 parallel-safe, 2 densely coupled, 1 borderline)
- Identify 1–2 OpenCode showcase tasks aligned with Issue #4278
- Decide on Devin session tag scheme (suggest: `acg-bench-{repo}-{taskN}-{mode}`)
- Decide on hand-labeled write-set ground truth (5 tasks × 3 humans? or 5 × 1?)
- Set up shared Devpost draft early so both teammates can edit
- Set up shared GitHub repo early; commit initial README skeleton in hour 1
