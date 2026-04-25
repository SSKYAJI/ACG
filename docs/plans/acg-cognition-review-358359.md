# ACG Cognition Track Assessment

This plan reviews whether Agent Context Graph is solving a real problem, what must be validated, and how to position it to compete strongly in the LA Hacks Cognition track.

## Current understanding

- **Project:** Agent Context Graph (`ACG`), a pre-flight planner for multi-agent coding workflows.
- **Core mechanism:** take a repo plus task list, predict each task's likely file write-set, compile a DAG-shaped `agent_lock.json`, and use MCP/Cascade validation to block or flag out-of-bounds writes.
- **Primary value claim:** reduce merge-conflict/review/ACU waste when multiple coding agents work in parallel.
- **Best pitch framing:** not “another multi-agent swarm,” but “a lockfile and graph compiler that makes agent parallelism reviewable and safer.”
- **Current repo state:** no implementation artifacts yet beyond a short README; the existing decision plan is the source of truth.

## Is this an actual problem?

**Verdict: yes, but only if framed narrowly and proven with a demo.**

The real problem is not just Git merge conflicts. The stronger problem is **agent coordination tax**:

- **Stomped writes:** multiple agents edit the same file or nearby files with no shared plan.
- **Hidden coupling:** even when agents do not conflict syntactically, reviewers must determine whether their changes are compatible.
- **Wasted compute:** naive parallel agent runs can burn credits/tokens/ACUs before discovering conflicts.
- **Poor reviewability:** current multi-agent workflows often lack a committable artifact that says who was allowed to touch what.

The plan should avoid overclaiming “proves correctness.” File-level disjointness is useful, but it does not prove semantic independence. The safest claim is: **ACG provides a conservative pre-flight and runtime/audit guardrail for file-level write collisions, plus a reviewable DAG for dependent work.**

## Can this win the Cognition track?

**Verdict: plausible top-tier / winner candidate if the demo shows a clear delta; risky if it remains mostly conceptual.**

### Why it is strong

- **Sponsor-native:** directly uses and improves workflows around Devin, Windsurf/Cascade, MCP, and agentic coding.
- **Technical depth:** tree-sitter/static analysis + write-set prediction + DAG solver + MCP server is more substantial than a wrapper app.
- **Measurable:** a single compelling chart comparing naive parallel vs planned execution could make the value obvious.
- **Timely:** everyone in the track will have strong AI tools, so a project about orchestrating AI coding tools is strategically aligned.
- **Inspectable artifact:** `agent_lock.json` is concrete, reviewable, and easy for judges to understand.

### Why it may lose

- **Too ambitious:** full tree-sitter graph, LLM re-rank, Cascade hook, Devin harness, frontend, benchmarks, and video is a lot for 2 people.
- **Evidence risk:** the plan cites several very specific external claims; if any are wrong or unverifiable, the narrative weakens.
- **Demo risk:** if the benchmark is artificial or the chart is weak, judges may see it as a research prototype.
- **Novelty risk:** judges may classify it as “just locks for agents” unless the DAG + MCP + measured savings story is crystal clear.

## Recommended MVP scope

Build the smallest version that proves the thesis:

1. **Lockfile schema:** `agent_lock.json` with tasks, allowed files, dependencies, confidence, and rationale.
2. **Write-set predictor:** start with explicit file mentions, import closure, simple symbol/path search, and optional LLM ranking.
3. **DAG compiler:** mark tasks as parallel-safe when write-sets are disjoint; add serial edges for overlaps/dependencies.
4. **Validator:** CLI or MCP tool that accepts attempted writes and returns allow/deny/audit reasons.
5. **Demo benchmark:** one repo, 3–5 tasks, compare naive parallel vs ACG-planned on conflicts/rework/review burden.
6. **Thin UI or README-first demo:** chart, lockfile viewer, and honesty box are more important than a polished app.

If Cascade `pre_write_code` hooks are hard or undocumented, ship **audit-only validation** instead of blocking enforcement. That is still demoable and honest.

## Pitch refinement

Use this core line:

> “Agent Context Graph is a lockfile compiler for AI coding agents: before agents write code, it predicts their write boundaries, compiles a dependency DAG, and gives Devin/Windsurf a reviewable contract for safe parallelism.”

Avoid these phrases:

- **“Proves agents are independent”** — too strong.
- **“Prevents all conflicts”** — false because semantic conflicts remain.
- **“Multi-agent coordinator”** — sounds generic.
- **“CRDT replacement”** — unnecessary comparison; ACG is pre-flight planning, not runtime merging.

## Must-verify before committing fully

- **Official track rubric:** exact Cognition judging criteria, submission requirements, and any bonus for Devin/Windsurf usage.
- **External sources:** verify every cited claim and quote with URLs/screenshots.
- **Cascade hooks:** confirm whether `pre_write_code` exists and can block writes the way the plan assumes.
- **Devin access:** confirm practical session limits, API/export access, and whether session metrics can be gathered in time.
- **Benchmark repo/tasks:** choose tasks that naturally create both parallel-safe and coupled cases.

## Best use of the two-person team

- **Person A:** lockfile schema, DAG compiler, validator/MCP surface.
- **Person B:** benchmark harness, demo assets, README/Devpost, source verification.
- **Shared:** task design and demo video script.

Do not let frontend polish consume the project. The winning artifact is the **measured workflow improvement**, not the dashboard.

## Perplexity prompt to verify claims and track fit

```text
I am evaluating a LA Hacks Cognition track hackathon project called Agent Context Graph (ACG). ACG is a local-first MCP server / CLI that takes a code repository and a list of coding-agent tasks, predicts each task's likely file write-set, compiles a DAG-shaped lockfile (`agent_lock.json`) with parallel-safe and serial-dependent tasks, and validates whether agents such as Devin/Windsurf/Cascade stay within their assigned write boundaries. The goal is to reduce merge-conflict/review/compute waste in multi-agent coding workflows.

Please research and return a source-cited assessment with URLs for every claim. I need:

1. The official LA Hacks 2026 Cognition track rules, rubric, prizes, judging criteria, submission requirements, and whether Devin/Windsurf usage is required or rewarded.
2. Whether Cognition/Devin/Windsurf currently document multi-agent coding, “Manage Devins,” conflict resolution, MCP support, Cascade hooks, or pre-write/write-validation hooks. Include exact docs links and quotes.
3. Whether OpenCode Issue #4278 exists and whether it is about multiple agents/clients overwriting each other or file locks. Include issue URL, status, and exact relevant quotes.
4. Whether an arXiv paper called CodeCRDT with ID arXiv:2510.18893 exists, what it claims, and whether it says future work should use static analysis/data-flow/shared variable access patterns for coupling measurement. Include exact quotes.
5. Whether a paper/project called Agint arXiv:2511.19635 exists and how it differs from a static pre-flight DAG/write-set lockfile.
6. Any Anthropic, Cognition, or engineering blog posts about multi-agent coding systems, agent coordination, merge conflicts, or agents needing shared context. Include exact quotes.
7. Competitive landscape: tools or papers that already do static write-set prediction, file-level locks for AI agents, MCP-based coding-agent coordination, or DAG planning for software-engineering agents.
8. Hackathon-winning potential: based on the Cognition rubric, would ACG be a strong submission? What are the biggest risks judges may object to?
9. Suggest a 2–3 minute demo structure and the most credible benchmark metrics for a weekend build.

Be skeptical. Separate verified facts from assumptions. Flag any claim that cannot be verified. Return a concise executive summary first, then detailed citations.
```

## Final recommendation

Proceed, but cut scope aggressively. This can be a serious Cognition-track contender if the team ships a credible lockfile/DAG/MCP prototype plus one convincing measured demo. The project should be judged on whether it makes parallel agentic coding safer and more reviewable, not whether it solves all merge or semantic conflicts.
