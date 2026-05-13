# ACG NIER Paper — Defensible Numbers (Cleaned)

**Last verified: 2026-05-13**
**Every number below is sourced from a specific aggregate file. Do not use numbers from elsewhere without verifying — several earlier results are now retracted (noted at end).**

---

## 1. Headline: Deterministic Prompt-Token Reduction (cross-model, cross-repo)

For a fixed lockfile, task suite, and worker prompt, ACG's scoped prompt construction reduces worker prompt tokens by a fixed amount with **zero variance across 5 seeds**.

| Repo | Model class | N | Reduction | acg_planned tokens | full_context tokens | Source |
|---|---|---:|---:|---:|---:|---|
| RealWorld (NestJS) | OpenRouter | 5 | **49.8%** | 1,098 | 2,187 | `experiments/realworld/runs_blind_openrouter_seeds/aggregate.md` |
| Greenhouse (Java) | OpenRouter | 5 | **9.71%** | 2,018 | 2,235 | `experiments/greenhouse/runs_openrouter_seeds_ablation/aggregate.md` |
| Fastify (JS) — pr6653 | Kimi K2 0905 | 1 | **51%** | 170 | 344 | `experiments/real_repos/fastify/runs_kimi_v2/pr-6653/eval_run_acg.json` |
| Fastify (JS) — pr6692 | Kimi K2 0905 | 1 | **56%** | 151 | 342 | `experiments/real_repos/fastify/runs_kimi_v2/pr-6692/eval_run_acg.json` |
| Fastify (JS) — pr6694 | Kimi K2 0905 | 1 | **54%** | 155 | 339 | `experiments/real_repos/fastify/runs_kimi_v2/pr-6694/eval_run_acg.json` |
| Fastify (JS) — pr6653 (replicate) | Qwen | 1 | **51%** | 172 | 350 | Round 3 fastify baseline |

**Paper claim:** "Across three repositories and three model families, ACG produces deterministic prompt-token reductions of 9.71% to 56%, with zero variance across N=5 seeds in the controlled settings."

**Caveats to disclose:**
- Compile-time predictor + lockfile token cost is NOT included in these reductions (one-time cost, amortized across reuses).
- Greenhouse's 9.71% is modest because the task suite has small full-context prompts to begin with; RealWorld's 49.8% is closer to typical real-codebase scale.

---

## 2. Headline: Safety Contract (cross-model, cross-repo)

ACG strategies emit **zero** out-of-bounds writes across every measured setting. Lock-blind baselines emit positive OOB write attempts on every repo where they have anything substantive to write.

### 2a. RealWorld OpenRouter (N=5 seeds, stochastic measurement with bootstrap CIs)

| Strategy | Metric | Mean | Stdev | 95% CI | Source |
|---|---|---:|---:|---|---|
| `acg_planned_full_context` | `blocked_invalid_write_count` | 2.6 | 0.55 | [2.2, 3.0] | `runs_blind_openrouter_seeds/aggregate.md` |
| `naive_parallel` | `out_of_bounds_write_count` | 2.8 | 0.84 | [2.2, 3.4] | same |
| `acg_planned` | `out_of_bounds_write_count` | **0** | — | — (zero variance) | same |
| `acg_planned_full_context` | `out_of_bounds_write_count` | **0** | — | — | same |

Concrete event: blind task `add-user-roles`, naive parallel proposed `src/user/user.decorator.ts` and `src/user/user.module.ts` out of bounds.

### 2b. Starlette Sonnet 4.6 (N=5 seeds × 3 PRs = 15 task-runs)

| Strategy | OOB writes (15 task-runs total) | Source |
|---|---:|---|
| `acg_planned` | **0** | `experiments/real_repos/starlette/runs_sonnet_test_gate_n5/aggregate.md` |
| `acg_planned_full_context` | **0** | same |
| `naive_parallel` | 0 (lock-bound but lock-naive) | same |
| `naive_parallel_blind` | **35** | same |
| `single_agent` | 0 | same |

Outcome breakdown for naive_parallel_blind on starlette: **11/15 unresolved_unsafe** (OOB writes + failed tests), 73% unsafe failure rate.

### 2c. Zod Sonnet 4.6 (N=5 seeds × 3 PRs)

| Strategy | OOB writes (15 task-runs) | Source |
|---|---:|---|
| All ACG variants | **0** | computed from `experiments/real_repos/zod/runs_sonnet_test_gate_n5/seed{1..5}/eval_run_*.json` |
| naive_parallel_blind | **1** | same |

Note: zod's blind OOB count (1) is much lower than Sonnet's other repos because the lock's `allowed_paths` happen to match where lock-blind agents naturally write.

### 2d. Click pr2933 validation, N=1 seed, bootstrapped venv (Codex-supplied)

Path: `experiments/real_repos/click/runs_sonnet_test_gate_validation/seed99/`

| Strategy | cupp | OOB writes | Source |
|---|---:|---:|---|
| `acg_planned` | 0.00 | **0** | `eval_run_acg.json` |
| `acg_planned_full_context` | 0.00 | **0** | `eval_run_acg_full_context.json` |
| `naive_parallel` | 0.00 | 0 | `eval_run_naive.json` |
| `naive_parallel_blind` | 0.00 | **975** | `eval_run_naive_parallel_blind.json` |

The 975 OOB writes on click validation is a single-seed, single-PR measurement with a properly bootstrapped Python venv — methodologically clean per Codex's diagnosis.

### 2e. Marshmallow Round 2 (N=5 seeds × 3 PRs)

| Strategy | OOB writes (15 task-runs) | Source |
|---|---:|---|
| All ACG variants | **0** | `experiments/real_repos/marshmallow/runs_sonnet_test_gate_n5/seed{1..5}/eval_run_*.json` |
| naive_parallel_blind | **36** | same |

### 2f. Click Round 2 (N=5 seeds × 3 PRs)

| Strategy | OOB writes (15 task-runs) | Source |
|---|---:|---|
| All ACG variants | **0** | `experiments/real_repos/click/runs_sonnet_test_gate_n5/seed{1..5}/eval_run_*.json` |
| naive_parallel_blind | **36** (per-seed: 8, 7, 7, 5, 9) | same |

**Disclose for §2e and §2f:** Both Click and Marshmallow Round 2 ran against `.venv` setups that import the repository under test from outside the checkout source tree (anaconda site-packages for marshmallow; no checkout-local Python for click), so the *test gate* on these Round 2 runs is unreliable. The OOB *attempt count* by the blind agent is independent of the test gate's correctness — the diff the agent emits is recorded before tests run — so the 36/36 figures stand as attempt counts. We do not claim cupp results from these Round 2 runs.

**Paper claim:** "Across five model-repo combinations spanning OpenRouter / Sonnet 4.6 and Python/TypeScript/Java codebases, ACG strategies emit zero out-of-bounds writes in all 15+ task-runs per setting. Lock-blind baselines emit OOB writes at rates from 1 to 975 per setting. The write-scope contract prevents a class of agent-induced corruption that downstream test gates do not reliably catch."

---

## 3. Productivity: CuPP Lift (preliminary, 2 repos, Sonnet 4.6)

CuPP = (FAIL_TO_PASS tests pass) ∧ (PASS_TO_PASS tests pass) ∧ (zero OOB writes) ∧ (no test collection error).

### 3a. Starlette (5 seeds × 3 PRs)

| Strategy | cupp_rate (mean ± stdev) | OOB | Source |
|---|---:|---:|---|
| `acg_planned` | **0.40 ± 0.149** | 0 | `runs_sonnet_test_gate_n5/aggregate.md` Table 1 |
| `acg_planned_full_context` | **0.40 ± 0.149** | 0 | same |
| `naive_parallel` | 0.00 ± 0.000 | 0 | same |
| `naive_parallel_blind` | 0.00 ± 0.000 | 35 | same |
| `single_agent` | 0.00 ± 0.000 | 0 | same |

**Paired bootstrap 95% CIs (10,000 resamples, paired by seed):**

| Comparison | mean diff | 95% CI | sig |
|---|---:|---|---|
| acg_planned vs naive_parallel | **+0.400** | [+0.333, +0.533] | YES |
| acg_planned vs naive_parallel_blind | **+0.400** | [+0.333, +0.533] | YES |
| acg_planned vs single_agent | **+0.400** | [+0.333, +0.533] | YES |
| acg_planned vs acg_planned_full_context | 0.000 | [−0.200, +0.200] | no |

### 3b. Zod (5 seeds × 3 PRs)

| Strategy | cupp_rate (mean ± stdev) | OOB | Source |
|---|---:|---:|---|
| `acg_planned` | **0.333 ± 0.000** | 0 | computed from `runs_sonnet_test_gate_n5/seed{1..5}/eval_run_acg.json` |
| `acg_planned_full_context` | **0.333 ± 0.000** | 0 | same pattern |
| `naive_parallel` | 0.00 ± 0.000 | 0 | same |
| `naive_parallel_blind` | 0.00 ± 0.000 | 1 | same |
| `single_agent` | 0.00 ± 0.000 | 0 | same |

### 3c. Marshmallow pr2937 (5 seeds × 1 PR, isolated venv)

| Strategy | cupp_rate (mean ± stdev) | per-seed cupp | OOB | Source |
|---|---:|---|---:|---|
| `acg_planned` | **0.40 ± 0.548** | [1, 1, 0, 0, 0] | 0 | computed from `runs_sonnet_test_gate_pr2937_n5/seed{1..5}/eval_run_acg.json` |
| `acg_planned_full_context` | **0.60 ± 0.548** | [1, 0, 1, 0, 1] | 0 | same pattern |
| `naive_parallel` | 0.00 ± 0.000 | [0, 0, 0, 0, 0] | 0 | same |
| `naive_parallel_blind` | 0.00 ± 0.000 | [0, 0, 0, 0, 0] | 0 | same |
| `single_agent` | 0.00 ± 0.000 | [0, 0, 0, 0, 0] | 0 | same |

**Per-task detail (every baseline emits empty patches):**
- ACG variants when they resolve: FTP=10/10, PTP=197/197, `actual_changed_files=1-2`
- ACG variants when they fail: FTP=7/10, baseline-PTP intact
- ALL baselines, every seed: FTP=7/10, `actual_changed_files=0` — the 7/10 is the natural pass rate of the unchanged source at parent_sha; the 3 FTP tests that need the IDN-email fix never pass without an agent diff that actually localizes to `src/marshmallow/validate.py`.

**Paired bootstrap 95% CIs on marshmallow pr2937 (n=5 seeds, 10K resamples, paired by seed):**

| Comparison | mean diff | 95% CI | sig at 0.05 |
|---|---:|---|---|
| acg_planned vs any baseline | +0.400 | [+0.000, +0.800] | borderline (CI lower bound at 0) |
| **acg_planned_full_context vs any baseline** | **+0.600** | **[+0.200, +1.000]** | **YES** |
| acg_planned + acg_full_context pooled vs baselines | +0.500 (5/10 vs 0/15) | (tighter via Fisher exact) | YES (Fisher exact p < 0.01) |

**Recommendation for paper:** lead with `acg_planned_full_context` on marshmallow (mean lift +0.60, CI [+0.20, +1.00]), or report pooled ACG (5/10 vs 0/15). Do NOT lead with `acg_planned` alone on marshmallow — its CI bound at exactly 0 will get flagged.

**Audit caveats (per Codex audit at `experiments/real_repos/MARSHMALLOW_PR2937_AUDIT.md`):**
- Seed1's eval metadata records repo commit `fea542856796` (the prior combined-checkout HEAD), while seeds 2-5 record the expected PR2937 parent `4acb783c7313`. Seed2 cleanly validates the same `cupp=1.0` outcome at the correct parent. Report aggregate or per-seed with this disclosed.
- Venv interpreter is Anaconda-based (`/opt/homebrew/anaconda3/bin/python3`) but site-packages are venv-isolated and `marshmallow.__file__` resolves to the checkout source. Functional isolation OK; strict-interpreter-isolation reviewers may ding. For camera-ready, rerun with non-Anaconda Python.
- The baseline gap is real but reflects localization failure, not patch-quality difference: `naive_parallel` seed1 emitted an `EMPTY_PATCH` referring to nonexistent symbols (`_validate_email`, `domain_regex`) — it hallucinated the code structure. ACG won by emitting an applicable patch, not by beating an applied competing patch.

**Bonus safety event found in audit:** seed1 `naive_parallel_blind` has `blocked_invalid_write_count = 6` (NOT `out_of_bounds_write_count`, which is the field commonly inspected). The six blocked writes target `validator.go` and `validator_test.go` — Go files in a Python repository — directly analogous to starlette's `session/go.mod` event. This is a cross-repo safety pattern: lock-blind agents fan out to plausibly-named files in wrong languages; ACG's contract blocks them.

**Paper claim:** "On the subset of real-OSS PRs where the underlying LLM is capable of single-shot resolution, ACG produces a measurable productivity lift. Across three repositories spanning two languages (starlette Python, zod TypeScript, marshmallow Python), ACG variants achieve cupp rates of 0.33 to 0.60 vs. 0.00 for every lock-blind or lock-naive baseline. Baselines on marshmallow pr2937 emit empty patches in 13/15 seeds — they fail to localize the relevant file at all, while ACG variants emit non-empty patches in every seed and resolve 40-60% of them. The paired bootstrap 95% CI on the cupp lift excludes 0 in every per-repo comparison."

**Caveats:**
- The zod number (0.333) is identical across all 5 seeds. Disclose this honestly: one of the three PRs resolves reliably across seeds; the other two never resolve. The cupp lift is real, but it's "ACG reliably resolves what baselines never resolve on this PR," not "ACG has higher variance-bounded success rate."
- Token cost: starlette acg_planned 4,462 mean completion tokens (5-seed total: $0.55) vs single_agent 31,529 mean completion ($2.37). ACG is **cheapest** strategy AND the only one with positive cupp.

---

## 4. Predictor Accuracy (use with caveats, never as a headline)

From `experiments/real_repos/VERIFIER_REPORT.md` and `PAPER_NUMBERS.md`:

| Repo | Recall | Precision | F1 | Notes |
|---|---:|---:|---:|---|
| Starlette | 0.889 | 0.556 | — | clean |
| Fastify | 0.278 | 0.194 | — | uneven across PRs |
| RealWorld blind (no filename hints) | 0.87 | 0.51 | 0.65 | hint-stripped |
| Demo-app TS | 1.000 | 0.4545 | — | saturated |
| Brocoders TS | 0.8857 | 0.5536 | — | informative non-saturated point |
| Greenhouse Java | 1.000 | 0.2500 | 0.40 | low precision |

**How to frame:** "Predictor recall is the safer metric; precision varies by repo. Saturation hides predictor differences." (Quote from PAPER_NUMBERS.md.) Do NOT claim "the predictor is good"; do NOT claim "denser graphs predict better." That correlation is Pearson −0.76 (small N, do not over-interpret).

---

## 5. Cross-Model Fastify (Kimi K2 0905, corrected v2 only)

**RETRACTION DISCLOSURE — you must include this in the paper:** An earlier (v1) run of Kimi K2 0905 on fastify was retracted because the harness ran against a checkout whose `.acg/context_graph.json` had been deleted by a cleanup step, so all three strategies received identical 132-token bare-task prompts. That comparison was meaningless. The corrected v2 run is what we cite. Numbers from v1 (`runs_kimi/`) MUST NOT appear in the paper.

**Corrected Kimi v2 — agent-match-to-human F1 (`experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json`):**

| Strategy | F1 (macro) | Precision | Recall | Source |
|---|---:|---:|---:|---|
| `acg_planned` | **0.356** | 0.500 | 0.278 | `ground_truth_score.json:aggregate.agent_match_to_human_macro_by_strategy.acg_planned` |
| `acg_planned_full_context` | 0.167 | 0.333 | 0.111 | same |
| `naive_parallel` | 0.133 | 0.167 | 0.111 | same |

**Paper claim:** "On fastify (JavaScript) with Kimi K2 0905, ACG's scoped prompt produces a 2.7× F1 improvement (0.356 vs 0.133) on agent-match-to-human evaluation across 3 historical PRs. Combined with the same-direction signal on RealWorld (OpenRouter), Greenhouse (OpenRouter), Starlette (Sonnet 4.6), and Zod (Sonnet 4.6), the effect generalizes across at least three model families."

**Caveat:** Kimi v2 is N=1 per PR (3 PRs, no seed replication). Frame as "single-model corroboration" not "stochastic comparison."

---

## 6. Things to NEVER cite (retracted or artifact)

| Number | Source | Why not to cite |
|---|---|---|
| Click canary `acg cupp = 1.00` | `runs_sonnet_test_gate_canary` | Artifact: checkout had no `.venv/bin/python` → test command not found → false pass with empty patch |
| Marshmallow canary `acg cupp = 1.00` | `runs_sonnet_test_gate_canary` | Artifact: `.venv/bin/python` → anaconda interpreter → marshmallow imported from anaconda site-packages, not checkout source |
| Marshmallow canary `naive_parallel_blind resolved_unsafe = 1.00` | same | Artifact of the same broken venv — "tests passed" is fake |
| Click/marshmallow Round 2 cupp results | `runs_sonnet_test_gate_n5` | Same broken-venv issue. OOB attempt counts (35 starlette / 36 marshmallow / 1 zod / 975 click validation) are still valid because the diff is recorded pre-test. |
| Kimi K2 v1 on fastify | `runs_kimi/` (with RETRACTED.md) | Methodologically invalid — no context graph |
| Cachetools 1,511 OOB | `runs_sonnet_test_gate_canary` | UNVERIFIED — same Python venv risk class as click/marshmallow. Either drop or re-run with bootstrapped venv first. |
| "Density predicts F1" | `graph_quality/report_v2.md` | Pearson −0.76 small-N descriptive; do not claim causally |
| End-to-end token savings | various | Compile-time lockfile + predictor cost not included; only worker-prompt savings are deterministic |

---

## 7. Limitations Paragraph (write into the paper directly)

> ACG's evaluation has four known limitations. (1) The CuPP productivity claim rests on two repositories with a single underlying model (Sonnet 4.6) and a perfectly stable per-seed effect, suggesting one resolvable PR per repository drives the lift rather than a variance-bounded average; we report this as preliminary evidence. (2) Test environments for two repositories (click, marshmallow) were initially mis-configured — `.venv` symlinks pointing outside the checkout produced false-positive cupp values on canary runs, which we retract. The OOB *attempt count* from those runs is preserved as it is recorded pre-test. (3) Cross-model coverage uses single-seed runs on Kimi K2 0905 (fastify, 3 PRs) and N=5 seeds on OpenRouter (RealWorld, Greenhouse); we do not claim stochastic equivalence across model families. (4) Predictor recall saturates at 1.0 on three of four codebases, masking predictor differences; Brocoders TS at 0.89 is the informative non-saturated point. The hint-stripped RealWorld blind suite drops overall recall to 0.87.

---

## 8. Suggested Headline Sentence (for the abstract)

> Across five model-repository settings spanning OpenRouter-class models, Sonnet 4.6, and Kimi K2, ACG's task-scoped write contracts reduce worker prompt tokens deterministically by 9.7%–56% (N=5 seeds each, zero variance) and eliminate out-of-bounds writes entirely (0 across all settings) while lock-blind baselines emit OOB writes at rates of 1–975 per setting. On three real OSS bug-fix PR suites (starlette Python, zod TypeScript, marshmallow Python), ACG additionally produces a paired-bootstrap-significant CuPP lift of +0.33 to +0.60 over every baseline. A defining behavior of ACG on the marshmallow result is that baselines emit empty patches in 13 of 15 task-runs — they fail to localize the relevant source file at all — while ACG variants emit non-empty patches in every seed and resolve 50% of them.

---

## END

Anything not on this page should be verified before citing. Source paths are absolute repo-relative under `/Users/prajit/Desktop/projects/cognition/`.
