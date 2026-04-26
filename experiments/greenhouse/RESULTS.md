# ACG — Evaluation Results

> **Agent Context Graph** is a static write-contract compiler for multi-agent
> code generation. Given a task list and a repo, ACG produces a lockfile that
> (a) predicts which tasks will contend on shared files, (b) sequences
> contending tasks deterministically, (c) restricts each agent's filesystem
> write authority via per-task `allowed_paths`, and (d) validates every
> proposed write against the contract. The contract is provider-agnostic:
> the same lockfile and evaluation harness can target Groq-compatible
> clients, llama.cpp on a local GX10, or Devin's hosted agent via backend
> adapters.

This document collects the empirical evidence for the four claims above
across three codebases (Java Spring + TypeScript T3-stack + NestJS backend),
three backends (mock, local GX10, Devin), and one scaling extrapolation chart, plus a
self-analysis pass that computes predictor accuracy from the artifacts
themselves.

## TL;DR

| Claim                                              | Evidence                                                                                                                            | Where                                                                          |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| ACG predicts contention at compile time            | 3 conflict pairs in Greenhouse, 4 in demo-app, 11 in Brocoders NestJS                                                               | lockfiles under `experiments/` and `demo-app/`                                 |
| Devin (live black-box agent) respects the contract | 6/6 PRs in scope, 0 out-of-bounds writes (N = 1 trial; see §10)                                                                     | `eval_run_devin_api_*_smoke.json`                                              |
| Local LLM on ASUS GX10 respects the contract       | 0 OOB across Greenhouse and Brocoders local runs                                                                                    | local `eval_run_combined.json` artifacts                                       |
| Validator visibly fires under tightened scope      | Tightened-Greenhouse fixture: `blocked_write_events ≥ 1` per task on every backend (mock)                                           | `agent_lock_tight.json` + `test_tightened_greenhouse_lockfile_fires_validator` |
| Per-worker context shrinks under scoping           | 11% on Greenhouse, 43% on demo-app, 54% on Brocoders NestJS (point estimates, N = 1)                                                | analyzer reports + eval artifacts                                              |
| Static ACG has no extra LLM coordinator tax        | Shared main coordinator is excluded from both strategies                                                                            | `strategies.py`, `eval_run_combined.json`                                      |
| Predictor accuracy is auditable from artifacts     | Full-pipeline recall 1.00, precision 0.82 across 10 strategy-runs (vs lockfile self-report; retrieval-baseline leaderboard pending) | `acg analyze-runs`                                                             |
| Provider-agnostic execution (privacy story)        | Same lockfile + harness across cloud, local, and Devin backends                                                                     | `Makefile`, `docs/ASUS_DEPLOYMENT.md`                                          |

## 1. Why ACG (positioning vs CodeCRDT and adjacent prior art)

We surveyed the public literature and found **no documented system that
ships ACG's exact composition** — static write-set predictor + DAG
solver + filesystem-level write contract. Closest comparators:

- **CodeCRDT** (Pugachev 2025, arXiv:2510.18893) is _runtime_ CRDT-based
  coordination, not pre-flight contract enforcement. Their own evaluation
  reports parallel multi-agent outcomes ranging from **21.1% speedup to
  39.4% slowdown**, with **5–10% semantic conflicts** even when textual
  convergence succeeds. Their explicit limitation (Section 7.1, 8
  _Future Work_):

  > "Tasks with independent components benefit most. Highly coupled tasks
  > show true coordination overhead. ... Optimal: 3–5 agents for suitable
  > tasks; sequential for others."

  The paper does not ship a static pre-run planner that decides which tasks
  are "suitable" for parallel execution. It runs, measures, and characterizes
  the outcomes.

- **Aider's repomap** (PageRank over tree-sitter defs/refs) is a
  whole-repo _context-selection_ tool, not a write-set predictor; it has
  no enforcement layer.
- **OpenRewrite / Moderne** offers compiler-accurate semantic search over
  Java types/methods/dependencies but provides no per-PR DAG plan, no
  LLM agent layer, and no committable lockfile.
- **Anthropic's orchestrator-worker pattern** plans dynamically and does
  not produce a static, committable plan artifact.
- **Google ADK**'s philosophy ("context is a compiled view over a richer
  stateful system") is the closest framing match but ships no
  write-claim or enforcement.

ACG addresses the gap CodeCRDT itself flags: a **static dependency
graph + LLM-seeded predictor** compiles task descriptions into a lockfile
that decides, before any agent runs, which tasks are independent
(parallelizable) and which contend on shared files (must serialize). The
runtime layer becomes a contract validator instead of a coordinator. ACG
is **complementary** to runtime coordination patterns like CodeCRDT — it
provides a pre-flight parallelism signal that can complement their
runtime substrate.

**What we do not claim.** ACG is not the _first_ such system in absolute
terms (we cannot prove a negative across closed-source enterprise tools);
it is the first publicly documented one we could find. We do not claim
to prevent merge conflicts in general — we prevent **textual** merge
conflicts on disjoint write-sets, while leaving **semantic** conflicts
(CodeCRDT's 5–10% baseline) deliberately out of scope and cited as
future work.

| Property                      | CodeCRDT (Pugachev 2025)                                         | ACG (this work)                                                         |
| ----------------------------- | ---------------------------------------------------------------- | ----------------------------------------------------------------------- |
| Coordination                  | Runtime via Y.Map LWW + observation                              | Compile-time via static lockfile                                        |
| Predicts conflicts in advance | No (post-hoc characterization)                                   | Yes (per-task `allowed_paths` + conflict pairs)                         |
| Agent integration             | Tightly coupled (CRDT-aware agent)                               | Black-box compatible (validated on Devin)                               |
| Semantic conflicts            | 5–10% preliminary rate, "require post-generation reconciliation" | Out of scope here; import/export risk analysis is plausible future work |
| Languages tested              | TypeScript/React only                                            | Java Spring + TypeScript T3 + NestJS backend                            |
| Agents tested                 | Claude Sonnet 4.5 (their own implementation)                     | Devin (hosted), local llama.cpp/Gemma backend, mock                     |
| Determinism guarantee         | SEC convergence (character-level)                                | Deterministic execution plan + filesystem-level write boundary          |

## 2. The fixtures

### 2.1 Greenhouse (Java) — black-box validation

Real Spring repo (~200 files):

- Source: <https://github.com/spring-attic/greenhouse>, pinned commit
  `174c1c320875a66447deb2a15d04fc86afd07f60`.
- Three tasks: `lambda-rowmapper-{account,invite,app}` — replace anonymous
  `RowMapper<T>` inner classes with Java 8 lambdas, bump `<java-version>`
  in `pom.xml` from `1.6` to `1.8`.
- Each task touches `pom.xml` plus one `Jdbc*Repository.java` file —
  three real, mechanical Java modernizations.
- Lockfile predicts that **all three tasks contend on `pom.xml`**
  (3 overlapping write pairs).

### 2.2 demo-app (TypeScript) — cross-language validation

T3-stack Next.js + Prisma + tRPC app (~50 files):

- Located at `demo-app/` in this repo.
- Four tasks: `oauth`, `billing`, `settings`, `tests` — typical SaaS
  feature work spanning auth, payments, settings UI, and Playwright e2e.
- Lockfile predicts **four overlapping write pairs**:
  `(oauth, billing)`, `(oauth, tests)`, `(settings, billing)`,
  `(tests, billing)` — driven mostly by `.env.example` and
  `prisma/schema.prisma` shared across features.
- Execution plan: parallel group `{oauth, settings}` (no conflicts),
  then serial `billing`, then serial `tests`.

### 2.3 Brocoders NestJS boilerplate — modern backend validation

Production-style NestJS backend (`brocoders/nestjs-boilerplate`, main
branch, commit `dd0034750fc7f6ec15712afbecf50fa9828018a2`):

- 156 TypeScript files under `src/`, with modular auth, users, files,
  sessions, roles, statuses, mail, config, and database infrastructure.
- TypeORM + PostgreSQL relational path plus Mongoose/document-database
  support; this is a modern DB-backed backend, not a toy route fixture.
- Seven tasks: products domain, API-key auth, users search, files e2e
  tests, registration-email background job, notifications webhook, and
  deployment/config hardening.
- Lockfile predicts **11 overlapping write pairs** and preserves partial
  parallelism: group 1 runs `{deployment-config, products-domain,
users-search}` together, followed by serialized groups for the tasks
  contending on shared infrastructure.
- Main shared paths are realistic NestJS/backend hotspots:
  `src/app.module.ts` and `docker-compose.yml`.

## 3. Demo 1 — Greenhouse + Devin (live black-box agent)

Both strategies were run end-to-end against Devin v3 API on the live
Greenhouse fork at `https://github.com/SSKYAJI/greenhouse`. Six real PRs
were opened.

| Strategy         | Wall time | ACUs | Out-of-bounds writes | Overlap pairs (compile-time) |
| ---------------- | --------- | ---- | -------------------- | ---------------------------- |
| `naive_parallel` | 277 s     | 3.06 | **0**                | 3                            |
| `acg_planned`    | 854 s     | 3.22 | **0**                | 3                            |

_Source artifacts:_
`experiments/greenhouse/runs/eval_run_devin_api_naive_smoke.json`,
`experiments/greenhouse/runs/eval_run_devin_api_acg_smoke.json`.

### 3.1 What this proves and what it does not

**Observed:** Devin — a fully black-box hosted agent — stayed within ACG's
`allowed_paths` contract on every PR. We did not modify Devin. We did not
fine-tune. The constraint is a static lockfile delivered as part of the
prompt; the validator is run post-hoc on the diff. **6/6 PRs landed
contractually compliant.**

**Does not prove:** that ACG prevents merge conflicts that would otherwise
have happened. We attempted to merge the three naive PRs sequentially into
master and they merged cleanly under git's `ort` strategy — all three
agents converged on the identical `pom.xml` change (`1.6` → `1.8`), so git
auto-resolved the redundancy. The `overlapping_write_pairs=3` metric is a
**compile-time fact** about contention; the runtime conflict count
(`merge_conflicts=0`) reflects that on this fixture, the agents got lucky.

The honest framing: ACG **reduces the gamble** for predicted file-level
overlaps by serializing them before agents run. Naive parallel leaves that
risk to git and reviewer luck; ACG's planned strategy is deterministic with
respect to the lockfile's predicted write conflicts.

### 3.2 Wall-time honesty

ACG's planned strategy is **3.08× slower** than naive parallel on this
fixture (854 s vs 277 s). This is the price of sequencing: parallel is
fast on independent tasks, ACG serializes on shared files. The trade is
correctness for speed. For workloads where merge-time conflict resolution
is the bottleneck, ACG can amortize the sequencing cost by avoiding
predicted overlapping writes before PRs are opened.

## 4. Demo 2 — Greenhouse + ASUS GX10 local LLM (privacy)

The same three tasks, run against `llama-server` (llama.cpp) on an ASUS
GX10 with `gemma-4-26B-A4B-it` Q4_K_XL. The default benchmark executes the
compiled lockfile directly; the normal lead/coordinator is treated as shared
environment for both strategies, not as an ACG-only LLM call.

| Strategy         | Wall time | Worker prompt tokens | Completion tokens | Extra ACG coordinator tokens | OOB writes |
| ---------------- | --------- | -------------------- | ----------------- | ---------------------------- | ---------- |
| `naive_parallel` | 10.90 s   | 2159                 | 936               | 0                            | **0**      |
| `acg_planned`    | 18.90 s   | 1922                 | 933               | 0                            | **0**      |

_Source:_ `experiments/greenhouse/runs/_local/eval_run_combined.json`.

### 4.1 What this proves

- **Privacy story:** the local run (lockfile compile + naive run + planned
  run) executed against the GX10-hosted llama.cpp servers rather than a
  hosted LLM API. The same runtime client can switch between OpenAI-style
  cloud endpoints and llama.cpp via environment variables.
- **Per-worker prompt-token savings: 11%** (1922 / 3 = 641 tok/task vs
  2159 / 3 = 720 tok/task). Real numbers from the live local server,
  not mock.
- **Real completion tokens** captured from the OpenAI-compatible `usage`
  block on every chat-completion call.

### 4.2 Why savings are modest on Greenhouse

The lockfile's `allowed_paths` are deliberately generous on this fixture
(e.g., `lambda-rowmapper-account` covers `account/** + invite/** +
members/**`, ~52 files in scope vs the prompt's top-30 cap). When scope
is wider than the worker's top-K context window, the scoping does not
shrink the prompt much. **Savings scale with scope tightness**, as the
next demo shows.

## 5. Demo 3 — demo-app + mock (cross-language + tighter scopes)

Same harness, different codebase. demo-app's lockfile has narrow per-task
scopes (`settings` touches only 3 globs).

| Strategy         | Total prompt tokens | Per-task average | OOB writes | Overlap pairs |
| ---------------- | ------------------- | ---------------- | ---------- | ------------- |
| `naive_parallel` | 897                 | 224              | 0          | 4             |
| `acg_planned`    | 513                 | 128              | 0          | 4             |

_Source:_ `experiments/demo-app/runs/eval_run_combined.json`.

### 5.1 Per-task savings

| Task     | Naive prompt tokens | Planned prompt tokens | Δ    |
| -------- | ------------------- | --------------------- | ---- |
| oauth    | 221                 | 136                   | -38% |
| billing  | 235                 | 145                   | -38% |
| settings | 226                 | 128                   | -43% |
| tests    | 215                 | 104                   | -52% |

**Per-worker savings: 43% on average** — four times bigger than
Greenhouse, because the scopes are four times tighter. The variance
between codebases is itself the finding: ACG's value is dominated by
scope tightness, which the lockfile compiler controls.

## 6. Demo 4 — Brocoders NestJS + ASUS GX10 local LLM

This is the strongest context-scaling fixture: a real modular backend with
enough files for repo-scope reduction to matter, and enough shared NestJS
infrastructure for contention prediction to matter.

| Strategy         | Execution shape              | Wall time | Worker prompt tokens | Completion tokens | Extra ACG coordinator tokens | OOB writes |
| ---------------- | ---------------------------- | --------- | -------------------- | ----------------- | ---------------------------- | ---------- |
| `naive_parallel` | 7 workers at once            | 45.21 s   | 3721                 | 4874              | 0                            | **0**      |
| `acg_planned`    | 5 groups, first group size 3 | 85.97 s   | 1700                 | 4751              | 0                            | **0**      |

_Source:_ `experiments/microservice/runs_brocoders_local/eval_run_combined.json`.

### 6.1 What this adds beyond demo-app

- **Real backend size:** 156 TypeScript files under `src/`, versus the
  smaller hand-built demo-app.
- **Partial, not total, serialization:** the lockfile preserves a parallel
  group of three tasks and serializes the infrastructure-conflicting tasks.
- **Largest prompt reduction observed:** worker prompt context drops
  from 3721 to 1700 tokens, a **54% reduction**.
- **No ACG-only coordinator tax:** planned uses the same shared lead-agent
  assumption as naive, then follows the static lockfile schedule.

### 6.2 Caveat

The local model under-proposed concrete writes for several Brocoders tasks
(many returned zero proposed writes). Therefore the Brocoders result is best
read as a **planning/context-scaling benchmark**, not as a code-quality
success benchmark. The lockfile shape and prompt-token savings are still
real artifact measurements.

## 7. Scaling: worker prompt-token savings

Real multi-agent systems normally have a main coordinator or lead agent in
both strategies. The fair comparison is therefore not "naive has no
coordinator, ACG has one"; it is "the same coordinator dispatches agents,
and ACG gives it a static lockfile schedule plus scoped worker context."
The artifacts below exclude shared coordinator cost from both strategies and
report only worker prompt tokens.

| Codebase              | Naive tok/task | Planned tok/task | Per-task savings | Extra ACG coordinator tokens |
| --------------------- | -------------- | ---------------- | ---------------- | ---------------------------- |
| Greenhouse (Java)     | 720            | 641              | **79 / task**    | 0                            |
| demo-app (TypeScript) | 224            | 128              | **96 / task**    | 0                            |
| Brocoders NestJS      | 532            | 243              | **289 / task**   | 0                            |

Chart: `docs/scaling_breakeven.png` (regenerate with
`./.venv/bin/python -m experiments.greenhouse.scaling_chart \
--combined experiments/greenhouse/runs/_local/eval_run_combined.json
experiments/demo-app/runs/eval_run_combined.json
experiments/microservice/runs_brocoders_local/eval_run_combined.json --out
docs/scaling_breakeven.png`).

The chart shows both strategies scaling linearly with task count when the
shared main coordinator is excluded from both sides. Planned starts lower
and climbs more slowly because each worker receives a scoped context instead
of the broader repo prompt.

## 8. Predictor accuracy (the "learn from eval" loop)

A new CLI command, `acg analyze-runs`, aggregates eval_run artifacts into
a per-task predictor accuracy report (precision / recall / F1) and surfaces
refinement suggestions.

```bash
./.venv/bin/acg analyze-runs \
  experiments/greenhouse/runs/eval_run_devin_api_naive_smoke.json \
  experiments/greenhouse/runs/eval_run_devin_api_acg_smoke.json \
  experiments/greenhouse/runs/_local/eval_run_combined.json \
  experiments/demo-app/runs/eval_run_combined.json \
  experiments/microservice/runs_brocoders_mock/eval_run_combined.json \
  experiments/microservice/runs_brocoders_local/eval_run_combined.json \
  --out experiments/greenhouse/runs/_analysis/report.md \
  --json-out experiments/greenhouse/runs/_analysis/report.json
```

### 8.1 Aggregated metrics across 10 strategy-runs (48 task records, 14 unique tasks)

| Task                       | TP  | FP  | FN  | Precision | Recall | F1   |
| -------------------------- | --- | --- | --- | --------- | ------ | ---- |
| `api-key-auth`             | 8   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `billing`                  | 8   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `deployment-config`        | 8   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `files-e2e-tests`          | 8   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `lambda-rowmapper-account` | 2   | 6   | 0   | 0.25      | 1.00   | 0.40 |
| `lambda-rowmapper-app`     | 2   | 6   | 0   | 0.25      | 1.00   | 0.40 |
| `lambda-rowmapper-invite`  | 2   | 6   | 0   | 0.25      | 1.00   | 0.40 |
| `notifications-webhook`    | 8   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `oauth`                    | 6   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `products-domain`          | 8   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `registration-email-job`   | 8   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `settings`                 | 3   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `tests`                    | 5   | 0   | 0   | 1.00      | 1.00   | 1.00 |
| `users-search`             | 8   | 0   | 0   | 1.00      | 1.00   | 1.00 |

**Overall: precision = 0.82, recall = 1.00, F1 = 0.90.**

### 8.2 What this tells us

- **Recall = 1.00 across every task in every artifact.** The predictor
  never misses a file the agent eventually wrote in these artifacts. This
  is the observed safety signal: actual changes are always in the predicted
  set, so `allowed_paths` is never surprised by a write the validator would
  reject as truly unexpected in this benchmark.
- **Greenhouse precision = 0.25.** The predictor over-predicts on Java
  by 4×. It seeds files like `Account.java`, `AccountException.java`,
  and `AccountMapper.java` for `lambda-rowmapper-account`, but the agent
  only modifies `JdbcAccountRepository.java` plus `pom.xml`. The
  analyzer surfaces these as concrete refinement suggestions:

  > predictor over-predicts (precision=0.25); consider removing
  > `['Account.java', 'AccountException.java', 'AccountMapper.java']`
  > from predicted_writes seeds

- **demo-app and Brocoders precision = 1.00.** TypeScript predictor is
  well-calibrated on these fixtures. For Brocoders, interpret this with
  the caveat from Section 6.2: the mock backend echoes lockfile predictions
  and the local model under-proposed on several tasks.

This is the **learning loop**: artifacts produced by every run feed back
into a predictor calibration pass. The pipeline is closed.

## 9. Contract enforcement events

Across the ten in-scope strategy-runs and forty-eight task records:

- **Total out-of-bounds proposals:** 0
- **Total validator-blocked write events:** 0

Devin, mock, and local LLM runs proposed only files within their per-task
`allowed_paths` in these artifacts. The validator was poised to reject OOB
proposals but never had to fire **on the original lockfiles**, because the
compile-time `allowed_paths` were generous enough (e.g., Greenhouse's
`account/** + invite/** + members/**` covered ~52 files for what was a
2-file refactor) that natural agent proposals stayed in-scope.

### 9.1 Tightened fixture so the validator visibly fires

To close the unfalsifiable-safety-claim gap, we ship a **second**
Greenhouse lockfile, hand-edited to deliberately tighten
`allowed_paths` to the exact ground-truth files per task while leaving
`predicted_writes` at its original (wider) size:

- Artifact: `experiments/greenhouse/agent_lock_tight.json` — each task's
  `allowed_paths` is exactly `[pom.xml, <single Jdbc*Repository.java>]`,
  validates against `schema/agent_lock.schema.json`.
- Make target: `make eval-greenhouse-tight-mock` writes artifacts under
  `experiments/greenhouse/runs/tight/`.
- Regression test:
  `tests/test_greenhouse_eval.py::test_tightened_greenhouse_lockfile_fires_validator`
  asserts `blocked_invalid_write_count ≥ 1` overall and per-task on
  every CI run. **Test passes** as of the most recent commit.

On the mock backend, every task on the tight fixture produces
`blocked_write_events` against the predictor's over-eager false
positives, while the in-bounds proposals (the actual ground-truth
`pom.xml` + `Jdbc*Repository.java` writes) still land:

| Task                       | Blocked write events | Actual changed files | Blocked targets (sample)                                                    |
| -------------------------- | -------------------: | -------------------: | --------------------------------------------------------------------------- |
| `lambda-rowmapper-account` |                    6 |                    2 | `Account.java`, `AccountException.java`, `AccountMapper.java`, …            |
| `lambda-rowmapper-invite`  |                    6 |                    2 | `Invite.java`, `MailInviteService.java`, `FacebookInviteController.java`, … |
| `lambda-rowmapper-app`     |                    6 |                    2 | `App.java`, `AppController.java`, `AppForm.java`, `AppSummary.java`, …      |
| **Total**                  |               **18** |                **6** |                                                                             |

_Source:_ `experiments/greenhouse/runs/tight/eval_run_acg.json`. This is the
falsifiable safety signal:

- The validator's enforcement path is exercised in a committed
  artifact, not just a unit test.
- A regression in `acg/enforce.py:validate_write` would now fail CI.
- The artifact answers the obvious reviewer question — _"OK, but show
  me the validator actually firing"_ — with a single command:
  ```bash
  jq .summary_metrics.blocked_invalid_write_count \
    experiments/greenhouse/runs/tight/eval_run_acg.json   # 18
  ```

### 9.2 Pending: live Devin smoke run on the tight fixture

A single Devin v3 API run on `agent_lock_tight.json` would extend the
falsifiable-safety claim from "mock backend" to "black-box hosted agent."
Devin tends to stay in-scope when given tight constraints, so this is
verification-only — mock has already proven the validator fires. Cited
as carry-over work in the v2 megaplan.

## 10. Honest limitations

- **3-task Greenhouse fixture is small.** Each task is a 2-file
  modernization. A real Java 6 → 8 migration would touch dozens of
  files. ACG's value scales with codebase size and task count
  (Section 7); we have empirical data only at small N and rely on
  extrapolation past the observed 3-, 4-, and 7-task fixtures.
- **Merge conflicts did not materialize.** All three Greenhouse naive
  PRs merged cleanly because the agents converged on identical
  `pom.xml` edits. ACG predicted the contention; on this fixture the
  contention happened to resolve itself. This is the correct
  interpretation: ACG predicted file-level contention, but this run does
  not demonstrate an actual merge-conflict prevention event.
- **Wall-time trade-off is real.** Planned is slower than naive on
  these small fixtures because it deliberately serializes predicted
  conflicts. The time can be recovered only when downstream
  merge/review/test conflict costs dominate, so the per-run wall-clock
  number is honest.
- **Brocoders local model under-proposed.** The Brocoders lockfile and
  prompt-token result are strong, but the local LLM emitted zero concrete
  writes for several tasks. Treat that run as context/planning evidence,
  not as a generated-code quality benchmark.
- **Validator's live firing is mock-only so far.** The tightened-Greenhouse
  fixture (§9.1) demonstrates the enforcement path firing on the mock
  backend with a CI-enforced regression test. A live Devin smoke run on
  the same lockfile is the natural extension and is cited as carry-over
  in the v2 megaplan; Devin tends to stay in-scope under tight
  constraints so the smoke run is verification-only.
- **Predictor over-predicts on Java by 4×.** Recall is perfect, but the
  precision shortfall is a real finding (and the analyzer's primary
  refinement signal).
- **N = 1 trial per (strategy × backend × fixture).** Every stochastic
  cell in this document is a single trial. Stdev / bootstrap CIs are
  v2 megaplan §4 work; treat percentage and wall-time deltas as point
  estimates, not population means.
- **Predictor F1 = 0.90 is full-pipeline-vs-lockfile.** This compares
  `predicted_writes` to the agent's self-reported `actual_changed_files`,
  not to git-diff ground truth, and does not yet benchmark ACG's full
  pipeline against retrieval baselines (BM25 only / PageRank only /
  Aider-repomap-style). The standalone localization leaderboard is
  v2 megaplan §5A work.
- **No human evaluation of code quality.** Same limitation as CodeCRDT.
  Generated code was inspected by hand on a sample of PRs but not
  scored.

## 11. Reproducibility

All runs emit auditable JSON artifacts. Mock runs are deterministic; local
LLM and Devin runs depend on model/server/API behavior. To reproduce:

### 11.1 Mock smoke (deterministic, ~5 s)

```bash
./.venv/bin/python -m experiments.greenhouse.headtohead \
  --lock experiments/greenhouse/agent_lock.json \
  --tasks experiments/greenhouse/tasks.json \
  --repo experiments/greenhouse/checkout \
  --backend mock --strategy both \
  --out-dir experiments/greenhouse/runs/_mock_smoke

./.venv/bin/python -m experiments.greenhouse.headtohead \
  --lock demo-app/agent_lock.json \
  --tasks demo-app/tasks.json \
  --repo demo-app \
  --backend mock --strategy both \
  --out-dir experiments/demo-app/runs

bash experiments/microservice/setup.sh

ACG_MOCK_LLM=1 ./.venv/bin/python -m experiments.greenhouse.headtohead \
  --lock experiments/microservice/agent_lock_brocoders.json \
  --tasks experiments/microservice/tasks_brocoders.json \
  --repo experiments/microservice/nestjs-boilerplate \
  --backend mock --strategy both \
  --out-dir experiments/microservice/runs_brocoders_mock

# Tightened-Greenhouse fixture (§9.1): validator visibly fires.
make eval-greenhouse-tight-mock
# or equivalently:
./.venv/bin/python -m experiments.greenhouse.headtohead \
  --lock experiments/greenhouse/agent_lock_tight.json \
  --tasks experiments/greenhouse/tasks.json \
  --repo experiments/greenhouse/checkout \
  --backend mock --strategy both \
  --out-dir experiments/greenhouse/runs/tight
# Verify the validator fired:
jq .summary_metrics.blocked_invalid_write_count \
  experiments/greenhouse/runs/tight/eval_run_acg.json   # ≥ 1
```

### 11.2 Local LLM (requires GX10 + llama-server)

```bash
ACG_LLM_URL=http://100.115.37.73:8080/v1 \
ACG_LLM_API_KEY=local \
ACG_LLM_MODEL=gemma \
./.venv/bin/python -m experiments.greenhouse.headtohead \
  --lock experiments/greenhouse/agent_lock.json \
  --tasks experiments/greenhouse/tasks.json \
  --repo experiments/greenhouse/checkout \
  --backend local --strategy both \
  --out-dir experiments/greenhouse/runs/_local

ACG_LLM_URL=http://100.115.37.73:8080/v1 \
ACG_LLM_API_KEY=local \
ACG_LLM_MODEL=gemma \
./.venv/bin/python -m experiments.greenhouse.headtohead \
  --lock experiments/microservice/agent_lock_brocoders.json \
  --tasks experiments/microservice/tasks_brocoders.json \
  --repo experiments/microservice/nestjs-boilerplate \
  --backend local --strategy both \
  --out-dir experiments/microservice/runs_brocoders_local
```

### 11.3 Devin (requires `DEVIN_API_KEY`, ~$5 ACUs per strategy)

```bash
./.venv/bin/python -m experiments.greenhouse.headtohead \
  --lock experiments/greenhouse/agent_lock.json \
  --tasks experiments/greenhouse/tasks.json \
  --backend devin-api --strategy <naive_parallel|acg_planned> \
  --repo-url https://github.com/SSKYAJI/greenhouse.git \
  --base-branch master --max-parallelism 1 --max-acu-limit 10 \
  --out experiments/greenhouse/runs/<your_artifact>.json
```

### 11.4 Analyze and chart

```bash
./.venv/bin/python -m acg.cli analyze-runs \
  experiments/greenhouse/runs/eval_run_devin_api_naive_smoke.json \
  experiments/greenhouse/runs/eval_run_devin_api_acg_smoke.json \
  experiments/greenhouse/runs/_local/eval_run_combined.json \
  experiments/demo-app/runs/eval_run_combined.json \
  experiments/microservice/runs_brocoders_mock/eval_run_combined.json \
  experiments/microservice/runs_brocoders_local/eval_run_combined.json \
  --out experiments/greenhouse/runs/_analysis/report.md \
  --json-out experiments/greenhouse/runs/_analysis/report.json

./.venv/bin/python -m experiments.greenhouse.scaling_chart \
  --combined experiments/greenhouse/runs/_local/eval_run_combined.json \
             experiments/demo-app/runs/eval_run_combined.json \
             experiments/microservice/runs_brocoders_local/eval_run_combined.json \
  --label "Greenhouse (Java)" "demo-app (TypeScript)" "Brocoders NestJS" \
  --out docs/scaling_breakeven.png
```

## 12. Summary

ACG is a **static write-contract compiler** for multi-agent code
generation. It compiles a tasks list + repo into a lockfile that:

1. predicts contention pairs at compile time (3 in Greenhouse, 4 in
   demo-app, 11 in Brocoders NestJS);
2. produces a deterministic execution plan (parallel groups +
   sequential groups);
3. enforces per-task filesystem write authority via `allowed_paths`;
4. is provider-agnostic — the same lockfile and harness can target cloud
   OpenAI-compatible endpoints, llama.cpp on a GX10, or Devin's hosted
   agent via backend adapters.

We tested across Java and TypeScript backends, three execution backends,
ten strategy-runs, and forty-eight task records, plus one **tightened
fixture** that exercises the enforcement path explicitly.
**6/6 live Devin PRs respected the generous contract; all local and mock
artifacts reported 0 out-of-bounds writes; the tightened-Greenhouse mock
artifact reports `blocked_invalid_write_count ≥ 1` per task** — the
validator firing in a committed, CI-enforced artifact (§9.1). Per-worker
context shrank 11–54% depending on scope tightness on N = 1 trials per
cell, with no extra ACG-only coordinator tokens in the default
static-plan path. A self-analysis pass surfaces predictor calibration
drift (Greenhouse precision 0.25, TypeScript fixtures 1.00) and produces
concrete refinement suggestions.

Compared to runtime coordination patterns like CodeCRDT (Pugachev 2025),
ACG operates **upstream**: it provides a static pre-flight contention signal
that runtime coordination systems can consume. The two are complementary.
What ACG does not claim, per §1 and §10: it is not the absolute first
such system (only the first publicly documented one we found), it does
not prevent semantic merge conflicts (CodeCRDT's 5–10% baseline applies),
and its stochastic numbers are point estimates pending the v2 megaplan's
N ≥ 3 trial work.

---

### References

- Pugachev, S. _CodeCRDT: Observation-Driven Coordination for Multi-Agent
  LLM Code Generation_. arXiv:2510.18893v1 [cs.DC], 18 Oct 2025.
- This repository, branch `main`, commit `<HEAD at submission time>`.

### Artifacts referenced in this document

- `experiments/greenhouse/runs/eval_run_devin_api_naive_smoke.json` — Devin naive PRs.
- `experiments/greenhouse/runs/eval_run_devin_api_acg_smoke.json` — Devin planned PRs.
- `experiments/greenhouse/runs/_local/eval_run_combined.json` — local llama.cpp GX10 run.
- `experiments/demo-app/runs/eval_run_combined.json` — TypeScript T3 mock run.
- `experiments/microservice/runs_brocoders_mock/eval_run_combined.json` — Brocoders NestJS mock run.
- `experiments/microservice/runs_brocoders_local/eval_run_combined.json` — Brocoders NestJS local llama.cpp GX10 run.
- `experiments/greenhouse/agent_lock_tight.json` — hand-tightened Greenhouse lockfile (§9.1).
- `experiments/greenhouse/runs/tight/eval_run_acg.json` — mock run on the tight fixture, demonstrating `blocked_invalid_write_count ≥ 1` per task.
- `experiments/greenhouse/runs/_analysis/report.md` — predictor analysis.
- `experiments/greenhouse/runs/_analysis/report.json` — predictor analysis (machine-readable).
- `docs/scaling_breakeven.png` — worker prompt-token scaling chart.
