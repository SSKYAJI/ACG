# ACG Execution Kickoff

Lean, single-scan execution plan that supersedes the demo-flow and build-order sections of the strategic plan; everything else (evidence, sponsor strategy, honesty commitments, risk register) inherits from `agent-context-graph-decision-plan-308cc2.md`.

---

## Final pitch (verbatim — Devpost, video, booth)

> Parallel coding agents are powerful, but they collide on shared files. Devin now manages teams of Devins, but public docs only say the coordinator resolves conflicts after the fact. **ACG moves that work before execution.** It scans the repo, predicts each task's write-set, emits a committable `agent_lock.json`, and enforces it with a Windsurf pre-write hook. In our demo, naive parallel agents collide on auth, Prisma, and navigation files. With ACG, safe tasks run in parallel, risky tasks serialize, and illegal writes are blocked before they corrupt the diff.

**Booth one-liner:** *"It's `package-lock.json` for parallel coding agents."*

---

## Build order (strict — do not reorder)

```
1. CLI: `acg compile`         ← core; lockfile generation
2. agent_lock.json schema     ← the artifact judges read
3. Visual DAG renderer        ← terminal-friendly ASCII + simple SVG
4. Windsurf hook (or emulator) ← the "BLOCKED" moment in demo
5. Benchmark harness          ← naive vs planned, on ASUS GX10
6. FastMCP wrapper            ← only after #1-5 work
7. Devin click-through        ← only if Devin available + time remains
8. Agentverse adapter         ← only after FastMCP works
```

**Rule:** any item below the line we're currently working on is a stretch goal. Do not jump ahead.

---

## Files we will produce

```
demo-app/                       (Next.js + Prisma starter, hand-cloned)
tasks.json                      (4 hand-written tasks: oauth, billing, settings, tests)
agent_lock.json                 (the artifact)
.acg/context_graph.json         (output of ts-morph/tree-sitter scan)
.acg/run_naive.json             (naive parallel result)
.acg/run_acg.json               (ACG-planned result)
.acg/benchmark.png              (the chart)
.windsurf/hooks.json            (Windsurf pre_write_code config)
mcp_server/server.py            (FastMCP, 4 tools)
README.md                       (Devpost-grade)
docs/CITATIONS.md               (verbatim quotes + URLs, verified by hand)
demo-video.mp4                  (2:20-2:40, recorded)
```

---

## Commands we will expose

```bash
acg compile --repo demo-app --tasks tasks.json --out agent_lock.json
acg explain --lock agent_lock.json
acg run-benchmark --mode naive
acg run-benchmark --mode planned
acg report
```

If they all work as commands, wrapping them as MCP tools is mechanical.

---

## The crash-test demo (2:40 total, recorded)

| Time | What's on screen | What we say |
|---|---|---|
| **0:00–0:20** | Naive run output: 4 agents, 3 of them wrote to overlapping files (lib/auth.ts, prisma/schema.prisma, components/sidebar.tsx). Red "Overlapping writes" box. | "I asked four agents to work in parallel: OAuth, billing, settings, checkout tests. Sounds safe. Three of them touched auth, Prisma, or navigation." |
| **0:20–0:50** | `acg compile` running. Output: predicted 4 tasks, found 3 risky overlaps, generated 3 execution groups. | "Before any agent writes code, ACG predicts the write-set and produces a lockfile." |
| **0:50–1:20** | Compact DAG visual + one task entry from agent_lock.json (the `billing` entry with predicted_writes, depends_on, allowed_paths). | "OAuth and Settings can run in parallel. Billing waits because it overlaps. Checkout tests wait last because tests should target final behavior." |
| **1:20–1:50** | Live BLOCKED message: agent attempts to write `lib/auth.ts` but the Windsurf hook rejects it with reason "belongs to oauth/billing dependency chain." | "The lockfile isn't documentation. The Windsurf hook blocks writes outside the task's allowed paths." |
| **1:50–2:20** | One chart: 5 metrics (overlapping writes 5→1, blocked bad writes 0→4, manual merge steps 6→2, tests passing first run no→yes, wall time 18m→14m). | "We measure conflict surface directly: overlapping files, blocked illegal writes, retries, test result, wall time. ACU when available." |
| **2:20–2:40** | Two-line plug: ACG = pre-flight artifact for Devin Manage Devins. | "Devin already knows how to manage Devins. ACG is the missing pre-flight layer: before the coordinator launches child Devins, it gets a lockfile saying which workstreams are safe to parallelize and which need to serialize." |

**Money shot:** the BLOCKED moment at 1:20–1:50. That's where judges go from "interesting" to "oh, that's clever."

---

## Honest metrics (the chart contents)

```
                       Naive parallel    ACG-planned
Overlapping writes          5                1
Blocked bad writes          0                4
Manual merge steps          6                2
Tests passing first run     no               yes
Wall time                  18m              14m
ACU                  (only if Devin sessions ran)
```

**Forbidden phrases:** "2× token reduction," "we save Devin tokens," "saves $X." We did not measure tokens unless Devin sessions ran. Say "conflict surface dropped from X to Y."

---

## Devin / Hardware fallback decision tree

```
Devin platform working AND repo connects?
├── Yes → 5-task benchmark on Next.js starter via Devin sessions, log ACUs from session metadata
└── No  → ASUS GX10 fallback: same tasks, same repo, run agents locally via Aider or Claude Code, measure same metrics minus ACU
```

Either path produces the chart. The Cognition narrative ("we built the pre-flight artifact Devin's coordinator would consume") is unchanged.

---

## Two-person task split (per GPT's recommendation)

**Person A — Compiler core (Shashank):**
- ts-morph + tree-sitter-python wrapper → `.acg/context_graph.json`
- Write-set predictor (static seed + LLM re-rank with structured output)
- DAG solver (parallel/serial edge labeling, topo sort)
- agent_lock.json schema + 3 example lockfiles
- CLI commands (compile, explain, report)

**Person B — Demo, harness, submission:**
- Next.js + Prisma demo-app fork (clone a starter, name it)
- Hand-write tasks.json (OAuth, billing, settings, tests)
- Hand-label ground-truth write-sets (precision/recall input)
- Windsurf hook config + emulator script
- Benchmark harness (run_naive, run_planned, report → png)
- README, Devpost copy, video script
- Verify all citations against primary sources by hand → docs/CITATIONS.md

**Shared:**
- The agent_lock.json schema (lock at hour 1, do not change after)
- Demo video recording (both run through it together)

---

## Hard milestones (kill-criteria from strategic plan §11)

| Hour | Gate | If pass | If fail |
|---|---|---|---|
| **H+1** | ts-morph + tree-sitter installed, hello-world graph dumped for demo-app | Continue | Switch to LLM-only graph (no static), document as limitation |
| **H+3** | First lockfile generated for tasks.json | Continue | Reduce to 2-task scenario |
| **H+5** | Hand-labeled write-set ground truth complete | Score predictor, continue | Skip precision/recall claim |
| **H+8** | Naive vs planned run on demo-app produces different overlap counts | Build chart | Reframe demo to "deterministic write-boundary enforcement" without comparison |
| **H+12** | Windsurf hook blocks one bad write live | Continue | Ship audit-only validator, drop "block" from demo |
| **H+18** | Benchmark chart populated with real numbers | Continue | Use synthetic numbers, label "preliminary" |
| **H+24** | FastMCP wrapper exposes 4 tools | Continue | Ship as CLI only, mention MCP as roadmap |
| **H+28** | Demo video recorded | Submit prep | Re-record only failing segment |
| **H+32** | Devpost + GitHub + video uploaded | Done | Submit Cognition only, skip Agentverse |

---

## What we DO NOT do

- Do not build tree-sitter parsers from scratch — use ts-morph + tree-sitter-python
- Do not add CRDT runtime layer (cite CodeCRDT, do not build)
- Do not add a frontend dashboard beyond a single static benchmark page
- Do not say "2×" or "saves tokens" without Devin data
- Do not pitch tree-sitter, MCP, or DAGs in the first minute of the video
- Do not register Agentverse before FastMCP wrapper is stable
- Do not use Devin Manage Devins recursively to build ACG (clean control)
- Do not let the Windsurf hook implementation block CLI progress (build emulator first)

---

## First 60 minutes (right now)

| Min | Action | Owner |
|---|---|---|
| 0–10 | Create GitHub repo, push initial README skeleton, agree on package layout | Both |
| 0–10 | Pick the Next.js + Prisma starter (recommend: `t3-oss/create-t3-app` baseline or similar) | Person B |
| 10–25 | `npm install ts-morph` + minimal script that dumps imports for demo-app | Person A |
| 10–25 | Write tasks.json with the 4 tasks + hand-labeled ground-truth allowed_paths | Person B |
| 25–45 | First-pass agent_lock.json schema + 1 example lockfile (handwritten, validates) | Person A |
| 25–45 | Windsurf hook config skeleton (`.windsurf/hooks.json`) + bash emulator script | Person B |
| 45–60 | Sync: schema locked, demo-app forked, hook emulator runs, ground truth ready | Both |

After H+1, we hit the first kill-criteria gate.

---

## Citation verification checklist (Person B, before recording video)

Open each URL, copy the quote into `docs/CITATIONS.md`, mark verified or paraphrased:

- [ ] CodeCRDT future-work quote (arXiv:2510.18893)
- [ ] OpenCode Issue #4278 body quote (github.com/anomalyco/opencode/issues/4278)
- [ ] Walden Yan quote (jxnl.co Sept 11 2025 post)
- [ ] Cognition Manage Devins coordinator language (cognition.ai/blog/devin-can-now-manage-devins)
- [ ] DeepWiki MCP three-tool list (cognition.ai/blog/deepwiki-mcp-server)
- [ ] LA Hacks Cognition prizes (la-hacks-2026.devpost.com)
- [ ] Windsurf pre_write_code hook docs (docs.windsurf.com)
- [ ] FastMCP package + version (PyPI)
- [ ] uagents-adapter MCPServerAdapter (innovationlab.fetch.ai docs)
- [ ] ASUS GX10 LA Hacks sponsorship (asus.com/us/business/...)

If any quote is paraphrased, soften the language in Devpost and video before recording.

---

## Win condition (one paragraph)

If by Sunday submission we can show: (1) naive parallel agents collide on at least 2 shared files in our demo-app; (2) ACG predicts every collision before any write happens; (3) `agent_lock.json` serializes the conflicting tasks and parallelizes the safe ones; (4) Windsurf hook live-blocks at least 1 illegal write on camera; (5) benchmark chart with at least 4 honest metrics — then this is a credible top-3 contender on the Cognition track. Anything less, top-3 odds drop below 15%.
