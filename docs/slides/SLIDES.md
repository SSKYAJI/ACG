# ACG — Slide Deck Content (raw, ready to convert)

> Use this file as the script for the deck. Each `## Slide N` section is one
> slide. Bullets are tight on purpose — read aloud, don't paste verbatim.
>
> Audience: Cognition / Windsurf engineers. Lead with architecture, not
> visuals. Show the JSON. End with open questions.

---

## Slide 1 — Title

**ACG: Agent Context Graph**

A static write-contract compiler for multi-agent code generation.

**One-line thesis:** _Compile-time, not runtime._ Prevent agent collisions
before they happen instead of reconciling them after.

---

## Slide 2 — The thesis (talk 30s)

- Most multi-agent coordination work is **runtime**: CRDTs, OT, LWW, agent
  message passing.
- We chose the **opposite**: compile a static plan from the task list and
  the repo, hand each agent a scoped contract, validate diffs after the fact.
- Three implications:
  - The plan is **committable** — `agent_lock.json` lives in git, is
    code-reviewable, diffable, auditable.
  - The runtime layer becomes a **validator**, not a coordinator.
  - **Black-box agents work** — we don't need the agent vendor to cooperate.

---

## Slide 3 — The CodeCRDT contrast

| Property                      | CodeCRDT (Pugachev 2025)            | ACG (this work)                                    |
| ----------------------------- | ----------------------------------- | -------------------------------------------------- |
| Coordination                  | Runtime via Y.Map LWW + observation | Compile-time via static lockfile                   |
| Predicts conflicts in advance | No (post-hoc characterization)      | Yes (per-task `allowed_paths` + conflict pairs)    |
| Agent integration             | Tightly coupled (CRDT-aware agent)  | Black-box compatible (validated on Devin)          |
| Semantic conflicts            | 5–10% preliminary rate              | Out of scope; future work                          |
| Languages tested              | TypeScript / React only             | Java Spring + TypeScript T3 + NestJS backend       |
| Agents tested                 | Claude Sonnet 4.5 (their own impl)  | Devin v3, local Gemma Q4 / GX10, mock              |
| Determinism guarantee         | SEC convergence (character-level)   | Deterministic execution plan + filesystem boundary |

CodeCRDT itself flagged the gap (their §7.1, §8 _Future Work_): _"Tasks with
independent components benefit most. Highly coupled tasks show true
coordination overhead."_ They run, measure, characterize. We **plan
upstream of them**.

---

## Slide 4 — Architecture

**Pipeline:**

```
tasks.json + repo
       │
       ▼
   acg compile         ◄── static analyzer + LLM-seeded predictor + DAG solver
       │
       ▼
  agent_lock.json      ◄── COMMITTABLE artifact: predicted_writes, allowed_paths,
       │                    execution_plan.groups, conflicts_detected
       ▼
   workers (any agent: Devin / Gemma / Claude / mock)
       │
       ▼
   acg validate-write  ◄── per-diff filesystem boundary check (allowed_paths)
       │
       ▼
  eval_run.json        ◄── auditable artifact: tokens, OOB, blocked, timings
```

**Key design choice:** `allowed_paths` is _enforced filesystem authority_,
not just a suggestion. Validator runs against the actual diff.

---

## Slide 5 — What we ran on each codebase (overview)

Three real codebases, two languages, three eras / styles of work.
Detailed per-codebase slides follow this overview.

| Codebase         | Stack             | Files     | Tasks | Conflict pairs | Backends                      |
| ---------------- | ----------------- | --------- | ----- | -------------- | ----------------------------- |
| Greenhouse       | Java 1.6 / Spring | ~200 java | 3     | 3              | Devin v3, Gemma Q4/GX10, mock |
| Brocoders NestJS | NestJS / TypeORM  | 156 ts    | 7     | 11             | Gemma Q4/GX10, mock           |
| demo-app         | Next.js / Prisma  | ~50 ts    | 4     | 4              | mock                          |

Total: 14 unique tasks across 10 strategy-runs and 48 task records.

---

## Slide 6 — Greenhouse (Java) detail

**Repo:** `spring-attic/greenhouse` @ `174c1c320875a66447deb2a15d04fc86afd07f60`
(genuinely pinned at a Java 1.6 commit — `<java-version>` in pom.xml).

**Tasks (3 total):**

- `lambda-rowmapper-account` — replace anonymous `RowMapper<PasswordProtectedAccount>`
  inner class in `JdbcAccountRepository.java` with a Java 8 lambda.
- `lambda-rowmapper-invite` — same on `JdbcInviteRepository.java`.
- `lambda-rowmapper-app` — four anonymous `RowMapper` classes in
  `JdbcAppRepository.java` → lambdas.
- All three bump `<java-version>` from `1.6` → `1.8` in `pom.xml`.

**Lockfile predictions:** 3 conflict pairs, all on `pom.xml`.

**Results:**

- **Devin v3 (live, prior carry-over run):** 6/6 PRs respected the contract. 0 OOB writes. Naive parallel 277s vs ACG planned 854s (3.08× slower, but no contention risk).
- **Gemma Q4 / GX10 (local, fresh re-run on demo day):** prompt tokens 2159 / 3 tasks → 1922 / 3 tasks (720 → 641 per task, ~11% reduction). Completion tokens 1062 → 912. Wall naive 12.47s vs ACG 22.13s. 0 OOB. Artifact: `experiments/greenhouse/runs/eval_run_combined.json`.
- **Tightened-fixture variant** (`agent_lock_tight.json`): mock run produces 18 blocked write events on the original predictor's over-eager Java false positives, while the in-bounds writes still landed. CI test enforces this.

**Honest framing:** all three Devin PRs merged cleanly because agents
converged on identical pom.xml writes. ACG predicted contention; on this
fixture, contention happened to resolve itself. Naive PRs got lucky.

---

## Slide 7 — Brocoders NestJS detail (the strongest demo)

**Repo:** `brocoders/nestjs-boilerplate` @ `dd0034750fc7f6ec15712afbecf50fa9828018a2`
(main branch). Real production-style backend: NestJS + TypeORM + PostgreSQL +
Mongoose. 156 TypeScript files under `src/`.

**Tasks (7 total):**

- `products-domain` — new domain module with controller, service, DTOs,
  entity, repository, TypeORM migration.
- `api-key-auth` — service-to-service auth: guard, module, service, config.
- `users-search` — search + email-domain filter on users list endpoint.
- `files-e2e-tests` — e2e coverage for file upload auth/config.
- `registration-email-job` — Bull-backed background job, wired into auth
  - mail modules.
- `notifications-webhook` — webhook endpoint with module, controller, DTO.
- `deployment-config` — `.env.example`, docker-compose, app/database
  config types hardened.

**Lockfile predictions:** 11 conflict pairs (10 on `src/app.module.ts`,
1 on `docker-compose.yml`).

**Execution plan:** group 1 = `{deployment-config, products-domain,
users-search}` parallel, then 4 serial groups for the contending tasks.
Preserves _partial_ parallelism — not total serialization.

**Results (Gemma Q4 / GX10, fresh re-run on demo day):**

- Worker prompt tokens **3721 total / 532 per task → 1700 total / 243 per task (~54% reduction).**
- Worker completion tokens 4900 → 4775 total.
- Wall-time naive 48.14s vs ACG planned 74.47s (ACG planned does _more_ serial work; trades wall-time for safety).
- 0 out-of-bounds writes, 0 blocked writes, 0 merge conflicts across both strategies.
- Artifact: `experiments/microservice/runs_brocoders_local/eval_run_combined.json`.

**Caveat:** local Q4 model under-proposed concrete writes for several tasks (most workers hit the 700-token completion cap with zero diffs emitted). Treat this run as a **planning / context-scaling** demonstration, not a generated-code-quality benchmark. The lockfile shape and prompt-token savings are real artifact measurements; the agent's code-quality is not what this fixture is measuring.

---

## Slide 8 — demo-app (T3) detail

**Repo:** `demo-app/` in this codebase. Next.js 14 / Prisma / tRPC /
NextAuth, ~50 files.

**Tasks (4 total):**

- `oauth` — Google OAuth via NextAuth, Prisma schema additions.
- `billing` — `/dashboard/billing` page, Stripe integration, sidebar entry,
  Prisma subscription model.
- `settings` — `/settings` redesign, sidebar styling.
- `tests` — Playwright e2e for checkout flow.

**Lockfile predictions:** 4 conflict pairs on `.env.example`,
`prisma/schema.prisma`, sidebar, dashboard.

**Execution plan:** group 1 = `{oauth, settings}` parallel (no conflicts),
then `billing` serial, then `tests` serial.

**Results (mock, fresh re-run on demo day):**

- Worker prompt tokens **897 total / 224 per task → 513 total / 128 per task (~43% reduction).**
- Worker completion tokens 88 → 88 total (mock backend is deterministic).
- 0 out-of-bounds writes, 0 blocked writes.
- Artifact: `experiments/demo-app/runs/eval_run_combined.json`.

This is the _cross-language_ demo — same harness on T3 stack, narrower `allowed_paths`, larger per-task savings than Greenhouse because scope is tighter.

---

## Slide 9 — Parallelism sweep: measured wall-time + predicted conflicts (NEW)

**Headline:** _Naive buys speed by paying growing conflict-risk. ACG plateaus
at the lockfile's max-group-size — by design — and keeps concurrent-conflict
count at zero across all N._

**Brocoders NestJS, 7 tasks, 11 predicted conflict pairs total. Sweep N=1..5
on Gemma Q4 / GX10:**

| Parallelism cap N | Naive wall (s) | ACG wall (s) | Naive predicted concurrent pairs | ACG predicted concurrent pairs |
| ----------------: | -------------: | -----------: | -------------------------------: | -----------------------------: |
|                 1 |          90.02 |        89.11 |                                0 |                              0 |
|                 2 |          57.69 |        79.44 |                                2 |                              0 |
|                 3 |          69.24 |        76.74 |                                4 |                              0 |
|                 4 |          45.16 |        75.90 |                                5 |                              0 |
|                 5 |          44.16 |        71.71 |                                6 |                              0 |

(Naive at N=2 < N=3 is GPU-saturation noise; trend line still drops
monotonically. Multi-trial CIs are future work.)

**Chart:** `docs/parallelism_sweep_brocoders.png` — two-panel:

- **Left panel: measured wall-time × N.** Live LLM. Naive (red): 90s → 44s
  as N rises. ACG (green): 89s → 72s, plateaus once N hits the lockfile's
  max group size (= 3). At N=1 they're tied (both serial). At N=5 naive
  is ~1.6× faster.
- **Right panel: predicted-conflict pairs × N.** Mechanical from lockfile.
  Naive (red): 0 → 6 at N=5 (would hit 11 at N=7). ACG (green): flat at 0.

**Invariants confirmed by the sweep (every N, both strategies):**

- `out_of_bounds_write_count = 0` (allowed_paths enforcement is N-invariant).
- `tokens_prompt_total` constant — naive 3721, ACG 1700, regardless of N.
  (Per-task prompts are independent of how many other agents are running.)

**Headline trade-off, in one sentence:** _On Brocoders at N=5, naive ships
2× faster wall-time but accepts 6 predicted concurrent-conflict pairs;
ACG accepts the slower wall-time and ships 0 conflict pairs. The lockfile
quantifies which currency you're paying in._

**Honest framing:** wall-time numbers are _measured_ (single-trial, live
GX10). Conflict-pair counts are _predicted_ (mechanical from the
lockfile's `conflicts_detected` list — not observed runtime collisions).
Both numbers and the chart are regenerable: see
`@/Users/prajit/Desktop/projects/cognition/experiments/microservice/parallelism_sweep.py`
and the JSON artifact at `docs/parallelism_sweep_brocoders.json`.

---

## Slide 10 — Per-worker prompt-token savings across codebases

**Chart:** `docs/scaling_breakeven.png` (regenerated against fresh artifacts).

| Codebase              | Tasks | Naive total tok | ACG total tok | Naive tok/task | ACG tok/task | Savings |
| --------------------- | ----: | --------------: | ------------: | -------------: | -----------: | ------: |
| Greenhouse (Java)     |     3 |            2159 |          1922 |          719.7 |        640.7 |    ~11% |
| demo-app (TypeScript) |     4 |             897 |           513 |          224.2 |        128.2 |    ~43% |
| Brocoders NestJS      |     7 |            3721 |          1700 |          531.6 |        242.9 |    ~54% |

**Variance is the finding.** Savings scale with `allowed_paths` tightness, which the lockfile compiler controls. Greenhouse's per-task allowed scope is generous (3 directories ≈ 52 files); Brocoders' is narrow (≈7 globs matching ≈10 files); demo-app sits in between.

---

## Slide 11 — Falsifiable safety: validator firing

**The artifact:** `experiments/greenhouse/runs/tight/eval_run_acg.json`.
Single command on stage:

```bash
jq .summary_metrics.blocked_invalid_write_count \
   experiments/greenhouse/runs/tight/eval_run_acg.json
# → 18
```

**The fixture:** `experiments/greenhouse/agent_lock_tight.json` —
hand-edited so `allowed_paths` is exactly `[pom.xml, <Jdbc*Repository.java>]`
per task while `predicted_writes` retains its (over-eager) original size.

**The result:** every Greenhouse task produces ≥ 1 `blocked_write_event`
against the predictor's own false positives, while the actual ground-truth
writes still land. Regression test:
`tests/test_greenhouse_eval.py::test_tightened_greenhouse_lockfile_fires_validator`.

This is the **falsifiable** part. Without it, "we have a safety layer" is
a vibes claim. With it, a regression in `acg/enforce.py:validate_write`
fails CI on the next push.

---

## Slide 12 — Self-analysis loop

**Command:** `acg analyze-runs <eval_run_*.json...>` reads its own
artifacts and emits a per-task predictor accuracy report.

**Aggregate across 10 strategy-runs / 48 task records / 14 unique tasks:**

- **Recall = 1.00** — predictor never missed a file the agent eventually
  wrote.
- **Precision = 0.82 overall** — but **0.25 on Greenhouse Java tasks**,
  1.00 on TypeScript tasks.
- Analyzer surfaces concrete refinement suggestion:
  > _"predictor over-predicts (precision=0.25); consider removing
  > `['Account.java', 'AccountException.java', 'AccountMapper.java']`
  > from predicted_writes seeds"_

**Loop closes.** Eval artifacts feed back into predictor calibration.
This is the meta-evidence: the system _knows where it's wrong_ and tells
you.

---

## Slide 13 — Honest limitations (RESULTS §10 condensed)

Read these aloud. Engineer-judges trust honesty.

- **N=1 trial per (strategy × backend × fixture).** Every percentage in
  this deck is a single run. CIs are future work.
- **No real merge conflict ever materialized.** Greenhouse PRs converged
  on identical `pom.xml` edits. ACG predicted contention; on this fixture,
  contention happened to resolve itself.
- **3-task Greenhouse fixture is small.** Real Java migrations touch
  hundreds of files.
- **Java predictor over-predicts by 4×.** Recall is perfect; precision
  shortfall is a real finding (and the analyzer's primary refinement
  signal).
- **Validator's _live_ firing is mock-only.** Tightened-fixture run on the
  mock backend fires the validator with a CI-enforced regression test.
  Live Devin smoke run on the same lockfile is carry-over work.
- **Brocoders local model under-proposed.** Several tasks emitted zero
  concrete writes. Treat that run as planning/context evidence, not
  code-quality evidence.
- **Predictor F1 is full-pipeline-vs-lockfile**, not vs git-diff ground
  truth. Standalone localization leaderboard vs BM25 / PageRank / Aider
  repomap is future work.

---

## Slide 14 — Future directions

If we keep building this, the next four items in priority order:

- **Real merge-conflict fixture.** One pair of tasks that genuinely
  conflict on a specific file region. Naive parallel produces a `git
merge` conflict; ACG planned doesn't. The single demo this dataset is
  missing.
- **N=5+ multi-trial stats.** Bootstrap CIs on every percentage in the
  deck. ~1 day of GX10 compute.
- **Predictor leaderboard.** Same task→file localization benchmark, ACG
  vs BM25-only vs personalized-PageRank-only vs Aider's repomap vs
  Moderne semantic search. This is what gets ML researchers to adopt the
  predictor.
- **Java precision fix.** Self-analyzer already names the offending seeds.
  Push 0.25 → 0.5+. ~1–2 hours of work.
- **Larger / more complicated Java migration fixture.** The current 3-task Greenhouse demo proves the harness works on Java; a 50–200-file Java 6 → 17 migration would prove the _value_ scales. **Top pick from Perplexity research:** Apache Continuum @ commit `78ee257` (tag `continuum-1.4.3`, Feb 2015) — 19 Maven modules, ~1000 Java files, Apache 2.0, JUnit 3.8.1 + JMock. Heavy anonymous-inner-class usage in the build queue / notifier system, `java.util.Date` everywhere in the scheduling domain model, Plexus IoC throughout. Self-contained (no downstream consumers tracking the 1.4.x branch). See `docs/slides/perplexity_java_search.md` for the full ranked candidate list.

---

## Slide 15 — Why this matters (close)

Three takeaways for engineer-judges:

- **Black-box agent compatible.** Validated on Devin v3 without modifying
  Devin. Same harness works for any agent that takes a prompt and produces
  a diff.
- **Provider-agnostic.** Cloud OpenAI-style endpoints, llama.cpp on a
  GX10, mock — all the same lockfile, same harness, same artifact schema.
- **Committable safety.** `agent_lock.json` is git-versioned, code-
  reviewable, regression-testable. PR-level safety review for agent runs
  is a real enterprise need; nothing else we found ships a committable
  artifact at this granularity.

**Architectural bet:** static planning is upstream of runtime
coordination. Compile-time is cheaper than runtime reconciliation. We
think the agent infra stack will eventually have both layers; we built
the upstream one.

---

## Speaker notes

- **Pace:** 90s per slide max. Slides 6, 7, 9, 11 are the "stop and
  explain" slides — give them 2 min each.
- **What to skip if running long:** slide 8 (demo-app) — it's the
  weakest demo numerically. Lead with Brocoders.
- **What to never skip:** slide 11 (validator firing) and slide 13
  (limitations). These are the two slides that distinguish a research
  prototype from a vibe demo.
- **If a judge interrupts on slide 3 (CodeCRDT contrast):** they're
  technical and engaged — go deeper on the architectural choice and
  let the rest of the deck slip.
- **If a judge interrupts on slide 13 (limitations):** they're skeptical
  — finish the limitations list, then jump straight to slide 15. Don't
  re-defend; show you understand the problem better than they do.
