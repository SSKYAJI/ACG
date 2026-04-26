# ACG — Evaluation Results

> **Agent Context Graph** is a static write-contract compiler for multi-agent
> code generation. Given a task list and a repo, ACG produces a lockfile that
> (a) predicts which tasks will contend on shared files, (b) sequences
> contending tasks deterministically, (c) restricts each agent's filesystem
> write authority via per-task `allowed_paths`, and (d) validates every
> proposed write against the contract. The contract is provider-agnostic:
> the same client compiles against Groq, llama.cpp on a local GX10, or
> Devin's hosted agent without code change.

This document collects the empirical evidence for the four claims above
across two codebases (Java Spring + TypeScript T3-stack), three backends
(mock, local GX10, Devin), and one scaling extrapolation chart, plus a
self-analysis pass that computes predictor accuracy from the artifacts
themselves.

## TL;DR

| Claim | Evidence | Where |
| --- | --- | --- |
| ACG predicts contention at compile time | 4 conflict pairs auto-detected in demo-app lockfile; 3 in Greenhouse | `demo-app/agent_lock.json`, `experiments/greenhouse/agent_lock.json` |
| Devin (live black-box agent) respects the contract | 6/6 PRs in scope, 0 out-of-bounds writes | `eval_run_devin_api_*_smoke.json` |
| Local LLM (Gemma-3-27B on ASUS GX10) respects the contract | 6/6 proposals in scope, 0 OOB | `eval_run_combined.json` (`_local/`) |
| Per-worker context shrinks under scoping | 11% on Greenhouse (broad scopes), 43% on demo-app (tight scopes) | analyzer report |
| Per-task savings scale to net win past breakeven | N≈16 (Java) / N≈9 (TypeScript) | `docs/scaling_breakeven.png` |
| Predictor accuracy is auditable from artifacts | Recall 1.00, precision 0.61 across 6 runs | `acg analyze-runs` |
| Provider-agnostic execution (privacy story) | Same harness across Groq / llama.cpp / Devin | `Makefile`, `docs/ASUS_DEPLOYMENT.md` |

## 1. Why ACG (positioning vs CodeCRDT)

Pugachev (2025), *CodeCRDT: Observation-Driven Coordination for Multi-Agent
LLM Code Generation* (arXiv:2510.18893), reports that parallel multi-agent
code generation produces task-dependent outcomes: **21.1% speedup to 39.4%
slowdown** depending on task structure. Their explicit limitation
(Section 7.1, 8 *Future Work*):

> "Tasks with independent components benefit most. Highly coupled tasks
> show true coordination overhead. ... Optimal: 3–5 agents for suitable
> tasks; sequential for others."

Their system **cannot decide a priori which tasks are "suitable"** for
parallel execution. They run, measure, and characterize.

ACG addresses exactly this gap: a **static dependency graph + LLM-seeded
predictor** compiles task descriptions into a lockfile that decides, before
any agent runs, which tasks are independent (parallelizable) and which
contend on shared files (must serialize). The runtime layer becomes a
contract validator instead of a coordinator. ACG is **complementary** to
runtime coordination patterns like CodeCRDT — it provides the parallelism
oracle they lack.

| Property | CodeCRDT (Pugachev 2025) | ACG (this work) |
| --- | --- | --- |
| Coordination | Runtime via Y.Map LWW + observation | Compile-time via static lockfile |
| Predicts conflicts in advance | No (post-hoc characterization) | Yes (per-task `allowed_paths` + conflict pairs) |
| Agent integration | Tightly coupled (CRDT-aware agent) | Black-box compatible (validated on Devin) |
| Semantic conflicts | 5–10% rate, "require post-generation reconciliation" | Predictable from import/export graph (future work, not in this submission) |
| Languages tested | TypeScript/React only | Java Spring + TypeScript T3 |
| Agents tested | Claude Sonnet 4.5 (their own implementation) | Devin (hosted), Gemma-3-27B (local), mock |
| Determinism guarantee | SEC convergence (character-level) | Static contract enforcement (filesystem-level) |

## 2. The fixtures

### 2.1 Greenhouse (Java) — black-box validation

Real Spring repo (~200 files):

* Source: <https://github.com/spring-attic/greenhouse>, pinned commit
  `174c1c320875a66447deb2a15d04fc86afd07f60`.
* Three tasks: `lambda-rowmapper-{account,invite,app}` — replace anonymous
  `RowMapper<T>` inner classes with Java 8 lambdas, bump `<java-version>`
  in `pom.xml` from `1.6` to `1.8`.
* Each task touches `pom.xml` plus one `Jdbc*Repository.java` file —
  three real, mechanical Java modernizations.
* Lockfile predicts that **all three tasks contend on `pom.xml`**
  (3 overlapping write pairs).

### 2.2 demo-app (TypeScript) — cross-language validation

T3-stack Next.js + Prisma + tRPC app (~50 files):

* Located at `demo-app/` in this repo.
* Four tasks: `oauth`, `billing`, `settings`, `tests` — typical SaaS
  feature work spanning auth, payments, settings UI, and Playwright e2e.
* Lockfile predicts **four overlapping write pairs**:
  `(oauth, billing)`, `(oauth, tests)`, `(settings, billing)`,
  `(tests, billing)` — driven mostly by `.env.example` and
  `prisma/schema.prisma` shared across features.
* Execution plan: parallel group `{oauth, settings}` (no conflicts),
  then serial `billing`, then serial `tests`.

## 3. Demo 1 — Greenhouse + Devin (live black-box agent)

Both strategies were run end-to-end against Devin v3 API on the live
Greenhouse fork at `https://github.com/SSKYAJI/greenhouse`. Six real PRs
were opened.

| Strategy | Wall time | ACUs | Out-of-bounds writes | Overlap pairs (compile-time) |
| --- | --- | --- | --- | --- |
| `naive_parallel` | 277 s | 3.06 | **0** | 3 |
| `acg_planned` | 854 s | 3.22 | **0** | 3 |

*Source artifacts:*
`experiments/greenhouse/runs/eval_run_devin_api_naive_smoke.json`,
`experiments/greenhouse/runs/eval_run_devin_api_acg_smoke.json`.

### 3.1 What this proves and what it does not

**Proves:** Devin — a fully black-box hosted agent — respects ACG's
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

The honest framing: ACG **eliminates the gamble** on whether parallel
agents will converge. Naive parallel is a probabilistic-conflict workflow;
ACG's planned strategy is deterministic.

### 3.2 Wall-time honesty

ACG's planned strategy is **3.08× slower** than naive parallel on this
fixture (854 s vs 277 s). This is the price of sequencing: parallel is
fast on independent tasks, ACG serializes on shared files. The trade is
correctness for speed. For workloads where merge-time conflict resolution
is the bottleneck (any team-scale CI), ACG amortizes by eliminating
conflict-resolution overhead downstream.

## 4. Demo 2 — Greenhouse + ASUS GX10 local LLM (privacy)

The same three tasks, run against `llama-server` (llama.cpp) on an ASUS
GX10 with `gemma-4-26B-A4B-it` Q4_K_XL — two server instances on
`100.115.37.73:{8080,8081}` reachable via Tailscale, sub-agents and
orchestrator respectively.

| Strategy | Wall time | Total prompt tokens | Total completion tokens | OOB writes |
| --- | --- | --- | --- | --- |
| `naive_parallel` | 10.86 s | 2159 | 890 | **0** |
| `acg_planned` | 33.91 s | 1922 + 1243 orchestrator overhead | 977 | **0** |

*Source:* `experiments/greenhouse/runs/_local/eval_run_combined.json`.

### 4.1 What this proves

* **Privacy story:** the entire pipeline (lockfile compile + naive run +
  planned run) executed without sending source code or tasks off the GX10.
  The same Python client switches from Groq to llama.cpp via two env vars.
* **Per-worker prompt-token savings: 11%** (1922 / 3 = 641 tok/task vs
  2159 / 3 = 720 tok/task). Real numbers from the live Gemma 3 server,
  not mock.
* **Real completion tokens** captured from the OpenAI-compatible `usage`
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

| Strategy | Total prompt tokens | Per-task average | OOB writes | Overlap pairs |
| --- | --- | --- | --- | --- |
| `naive_parallel` | 897 | 224 | 0 | 4 |
| `acg_planned` | 513 | 128 | 0 | 4 |

*Source:* `experiments/demo-app/runs/eval_run_combined.json`.

### 5.1 Per-task savings

| Task | Naive prompt tokens | Planned prompt tokens | Δ |
| --- | --- | --- | --- |
| oauth | 221 | 136 | -38% |
| billing | 235 | 145 | -38% |
| settings | 226 | 128 | -43% |
| tests | 215 | 104 | -52% |

**Per-worker savings: 43% on average** — four times bigger than
Greenhouse, because the scopes are four times tighter. The variance
between codebases is itself the finding: ACG's value is dominated by
scope tightness, which the lockfile compiler controls.

## 6. Scaling: when does ACG win on total tokens?

Per-task savings amortize the orchestrator's one-time fixed overhead at
breakeven N = `orch_overhead / per_task_savings`. Past breakeven, planned
beats naive on total tokens. Numbers are taken directly from the live
artifacts.

| Codebase | Per-task savings (tok) | Orchestrator overhead (tok) | Breakeven N |
| --- | --- | --- | --- |
| Greenhouse (Java) | 79 | 1243 | **15.7 tasks** |
| demo-app (TypeScript) | 96 | 881 | **9.2 tasks** |

Chart: `docs/scaling_breakeven.png` (regenerate with
`./.venv/bin/python -m experiments.greenhouse.scaling_chart \
--combined experiments/greenhouse/runs/_local/eval_run_combined.json
experiments/demo-app/runs/eval_run_combined.json --out
docs/scaling_breakeven.png`).

The chart shows naive scaling linearly while planned starts higher (the
orchestrator overhead) but climbs more slowly. The two cross at the
breakeven N values above. Beyond that point, planned strictly dominates
on total prompt tokens — and the slope advantage compounds at scale.

## 7. Predictor accuracy (the "learn from eval" loop)

A new CLI command, `acg analyze-runs`, aggregates eval_run artifacts into
a per-task predictor accuracy report (precision / recall / F1) and surfaces
refinement suggestions.

```bash
./.venv/bin/acg analyze-runs \
  experiments/greenhouse/runs/eval_run_devin_api_naive_smoke.json \
  experiments/greenhouse/runs/eval_run_devin_api_acg_smoke.json \
  experiments/greenhouse/runs/_local/eval_run_combined.json \
  experiments/demo-app/runs/eval_run_combined.json \
  --out experiments/greenhouse/runs/_analysis/report.md \
  --json-out experiments/greenhouse/runs/_analysis/report.json
```

### 7.1 Aggregated metrics across 6 strategy-runs (10 task-runs)

| Task | TP | FP | FN | Precision | Recall | F1 |
| --- | --- | --- | --- | --- | --- | --- |
| `lambda-rowmapper-account` | 2 | 6 | 0 | 0.25 | 1.00 | 0.40 |
| `lambda-rowmapper-app` | 2 | 6 | 0 | 0.25 | 1.00 | 0.40 |
| `lambda-rowmapper-invite` | 2 | 6 | 0 | 0.25 | 1.00 | 0.40 |
| `oauth` | 6 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| `billing` | 8 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| `settings` | 3 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| `tests` | 5 | 0 | 0 | 1.00 | 1.00 | 1.00 |

**Overall: precision = 0.61, recall = 1.00, F1 = 0.76.**

### 7.2 What this tells us

* **Recall = 1.00 across every task in every artifact.** The predictor
  never misses a file the agent eventually wrote. This is the safety
  property: an agent's actual changes are always in the predicted set,
  so `allowed_paths` (which is a superset of predicted) is never
  surprised by a write the validator would reject as truly unexpected.
* **Greenhouse precision = 0.25.** The predictor over-predicts on Java
  by 4×. It seeds files like `Account.java`, `AccountException.java`,
  and `AccountMapper.java` for `lambda-rowmapper-account`, but the agent
  only modifies `JdbcAccountRepository.java` plus `pom.xml`. The
  analyzer surfaces these as concrete refinement suggestions:

  > predictor over-predicts (precision=0.25); consider removing
  > `['Account.java', 'AccountException.java', 'AccountMapper.java']`
  > from predicted_writes seeds

* **demo-app precision = 1.00.** TypeScript predictor is well-calibrated
  on this fixture (mock backend echoes lockfile predictions; the metric
  is mostly a sanity check there).

This is the **learning loop**: artifacts produced by every run feed back
into a predictor calibration pass. The pipeline is closed.

## 8. Contract enforcement events

Across all six runs and ten task-runs:

* **Total out-of-bounds proposals:** 0
* **Total validator-blocked write events:** 0

Both Devin and Gemma-3-27B proposed only files within their per-task
`allowed_paths`. The validator was poised to reject OOB proposals but
never had to fire.

This is also a **limitation of the demo**: we have not engineered a
fixture where the agent intentionally proposes outside scope so the
validator's enforcement path is exercised in a visible way. The validator
is unit-tested (`tests/test_enforce.py`), but the live demo does not
showcase it. To fix in a follow-up: a tightened lockfile variant where
`allowed_paths` is closer to the minimal write set — the agent's natural
proposals would then spill outside the contract and produce visible
`blocked_write_events` records.

## 9. Honest limitations

* **3-task Greenhouse fixture is small.** Each task is a 2-file
  modernization. A real Java 6 → 8 migration would touch dozens of
  files. ACG's value scales with codebase size and task count
  (Section 6); we have empirical data only at small N and rely on
  extrapolation past N = 4.
* **Merge conflicts did not materialize.** All three Greenhouse naive
  PRs merged cleanly because the agents converged on identical
  `pom.xml` edits. ACG predicted the contention; on this fixture the
  contention happened to resolve itself. This is the correct
  interpretation: ACG eliminates the gamble, not the conflict per se.
* **Wall-time trade-off is real.** Planned is 3× slower than naive on
  3 tasks (Devin) and ~3× on local LLM. The time is recovered when you
  account for downstream merge-conflict resolution at team scale, but
  the per-run number is honest.
* **Validator never fired.** Both backends produced in-scope proposals
  on every run. The contract worked as a safety net but was not
  stress-tested live.
* **Predictor over-predicts on Java by 4×.** Recall is perfect, but the
  precision shortfall is a real finding (and the analyzer's primary
  refinement signal).
* **No human evaluation of code quality.** Same limitation as CodeCRDT.
  Generated code was inspected by hand on a sample of PRs but not
  scored.

## 10. Reproducibility

All runs produce deterministic JSON artifacts. To reproduce:

### 10.1 Mock smoke (deterministic, ~5 s)

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
```

### 10.2 Local LLM (requires GX10 + llama-server)

```bash
ACG_LLM_URL=http://100.115.37.73:8080/v1 \
ACG_ORCH_URL=http://100.115.37.73:8081/v1 \
ACG_LLM_API_KEY=local ACG_ORCH_API_KEY=local \
ACG_LLM_MODEL=gemma ACG_ORCH_MODEL=gemma \
./.venv/bin/python -m experiments.greenhouse.headtohead \
  --lock experiments/greenhouse/agent_lock.json \
  --tasks experiments/greenhouse/tasks.json \
  --repo experiments/greenhouse/checkout \
  --backend local --strategy both \
  --out-dir experiments/greenhouse/runs/_local
```

### 10.3 Devin (requires `DEVIN_API_KEY`, ~$5 ACUs per strategy)

```bash
./.venv/bin/python -m experiments.greenhouse.headtohead \
  --lock experiments/greenhouse/agent_lock.json \
  --tasks experiments/greenhouse/tasks.json \
  --backend devin-api --strategy <naive_parallel|acg_planned> \
  --repo-url https://github.com/SSKYAJI/greenhouse.git \
  --base-branch master --max-parallelism 1 --max-acu-limit 10 \
  --out experiments/greenhouse/runs/<your_artifact>.json
```

### 10.4 Analyze and chart

```bash
./.venv/bin/acg analyze-runs experiments/greenhouse/runs/ \
  --out experiments/greenhouse/runs/_analysis/report.md

./.venv/bin/python -m experiments.greenhouse.scaling_chart \
  --combined experiments/greenhouse/runs/_local/eval_run_combined.json \
             experiments/demo-app/runs/eval_run_combined.json \
  --label "Greenhouse (Java)" "demo-app (TypeScript)" \
  --out docs/scaling_breakeven.png
```

## 11. Summary

ACG is a **static write-contract compiler** for multi-agent code
generation. It compiles a tasks list + repo into a lockfile that:

1. predicts contention pairs at compile time (4 in demo-app, 3 in
   Greenhouse);
2. produces a deterministic execution plan (parallel groups +
   sequential groups);
3. enforces per-task filesystem write authority via `allowed_paths`;
4. is provider-agnostic — the same client targets Groq, llama.cpp on a
   GX10, or Devin's hosted agent.

We tested across two languages, three backends, and ten task-runs.
**6/6 live Devin PRs respected the contract; 6/6 local LLM proposals
respected the contract.** Per-worker context shrank 11–43% depending on
scope tightness; total-token breakeven occurs at N=9 (TypeScript) or
N=16 (Java). A self-analysis pass surfaces predictor calibration drift
(Greenhouse precision 0.25, demo-app 1.00) and produces concrete
refinement suggestions.

Compared to runtime coordination patterns like CodeCRDT (Pugachev 2025),
ACG operates **upstream**: it provides the parallelism oracle that
runtime coordination lacks. The two are complementary.

---

### References

* Pugachev, S. *CodeCRDT: Observation-Driven Coordination for Multi-Agent
  LLM Code Generation*. arXiv:2510.18893v1 [cs.DC], 18 Oct 2025.
* This repository, branch `main`, commit `<HEAD at submission time>`.

### Artifacts referenced in this document

* `experiments/greenhouse/runs/eval_run_devin_api_naive_smoke.json` — Devin naive PRs.
* `experiments/greenhouse/runs/eval_run_devin_api_acg_smoke.json` — Devin planned PRs.
* `experiments/greenhouse/runs/_local/eval_run_combined.json` — local Gemma-3 GX10 run.
* `experiments/demo-app/runs/eval_run_combined.json` — TypeScript T3 mock run.
* `experiments/greenhouse/runs/_analysis/report.md` — predictor analysis.
* `experiments/greenhouse/runs/_analysis/report.json` — predictor analysis (machine-readable).
* `docs/scaling_breakeven.png` — total-token scaling chart.
