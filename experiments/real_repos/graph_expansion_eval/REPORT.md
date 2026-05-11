# Graph expansion live eval (3 seeds)

Run status: completed_3_seeds — post-hoc: the Step 2 early-abort rule for seed 2 **would have tripped** (union mean `candidate_recall` over seeds 1–2 was **0.632** < 0.78); the harness was not stopped before seed 3 because polling missed that window while the process was still running.

## Hard gates (Step 3, `after_live_*` CSVs)

All metrics **`mean ± σ`** at 3 decimals; row cites are **`experiments/real_repos/graph_expansion_eval/<file>`** `scope`/logical row.

| Gate | PASS / FAIL | Value | Citation |
|------|-------------|-------|----------|
| 1. `candidate_recall ≥ 0.85` | **FAIL** | 0.645 ± 0.039 | `after_live_predictor_summary.csv` · scope `macro` · `candidate_recall_mean` / `candidate_recall_std` |
| 2. `candidate_count_median ∈ [8, 14]` | **FAIL** | 0.000 ± 0.000 | `after_live_predictor_summary.csv` · scope `macro` · `candidate_count_median_mean` / `candidate_count_median_std` |
| 3. `hard_recall ≥ 0.60` (target ≥ 0.65) | **FAIL / FAIL** | 0.515 ± 0.017 | `after_live_predictor_summary.csv` · scope `macro` · `hard_recall_mean` / `hard_recall_std` (below both 0.60 and 0.65) |
| 4. `approved_replan_count` on ≥ 3 distinct tasks (3 seeds combined) | **FAIL** | **1** task (`pr-4974` only) | `after_live_seed{1,2,3}_strategy_scores.csv` · `strategy=acg_planned_replan`, `approved_replan_count` > 0 |
| 5. `macro_f1(acg_planned_replan) ≥ macro_f1(naive_parallel) − σ` | **FAIL** | 0.605 vs 0.607 bound | `after_live_strategy_summary_variance.csv` · `backend=local` · `macro_f1_mean`/`macro_f1_std` · naive lower bound `0.643 − 0.036 = 0.607` |
| 6. `σ(macro_f1) ≤ 0.05` (both strategies) | **PASS / PASS** | 0.019 and 0.036 | `after_live_strategy_summary_variance.csv` · `backend=local` · `acg_planned_replan` and `naive_parallel` · `macro_f1_std` |

## What changed this iteration

- **Fix A (candidate gate):** Tightened which retrieved writes count as `must_write` via context-only detection and confidence/signal gating in `acg/predictor.py:1091–1154` (`_is_context_only_path`, `_is_must_write`).

- **Fix B (post-LLM type/stub expansion):** After the planner LLM, anchors expand along `type_link` and stub import neighbors with a cap, boosting typings adjacency in `acg/predictor.py:830–895` (`_post_llm_must_write_neighbor_expansion`).

- **Fix C (scope review pruner):** Bounded drops/promotions with floor rules and `candidate_context` protection in `acg/predictor.py:1477–1565` (`_scope_review_can_drop`, `_apply_scope_review`, sorting).

- **Fix D (auto-replan):** `.env` sets `ACG_AUTO_REPLAN=1`; runtime gates structured auto-approval on `tier`, score ≥ 0.72, signal intersection, and no hard conflict in `acg/runtime.py:818–856` (`_candidate_context_replan_state`, `_can_auto_approve_replan`).

- **Fix E (`--seeds`):** Multi-seed live sweeps in `experiments/real_repos/graph_expansion_eval/evaluate.py:1774–1825` (`argparse` + seeded predictor/strategy loop + variance CSVs).

- **Fix F (predictor summary companions):** Aggregate predictor mean/std CSV including `candidate_count_*` in `experiments/real_repos/graph_expansion_eval/evaluate.py:1004–1068` (`_predictor_summary_rows`, `_write_predictor_aggregate_csvs`).

- **Harness fix 1:** `_run_naive_parallel` forces `auto_replan=False` for the naive baseline in `experiments/greenhouse/strategies.py:471–488`.

- **Harness fix 2:** `_write_strategy_artifacts_for_seed` deep-copies the lock per strategy so replan mutations do not leak across strategies in `experiments/real_repos/graph_expansion_eval/evaluate.py:652–676`.

## Variance table (live backend `local`, 3 seeds)

`mean ± σ` from `after_live_strategy_summary_variance.csv` (`statistics.pstdev`).

| Strategy | macro_f1 | macro_recall | macro_precision | approved_replan_count | total_out_of_bounds | total_blocked_invalid |
|----------|----------|--------------|-----------------|------------------------|---------------------|----------------------|
| acg_planned | 0.564 ± 0.015 | 0.515 ± 0.017 | 0.661 ± 0.020 | 0.000 ± 0.000 | 0.000 ± 0.000 | 1.000 ± 0.000 |
| acg_planned_replan | 0.605 ± 0.019 | 0.561 ± 0.017 | 0.699 ± 0.024 | 1.000 ± 0.000 | 0.000 ± 0.000 | 0.000 ± 0.000 |
| naive_parallel | 0.643 ± 0.036 | 0.597 ± 0.037 | 0.741 ± 0.037 | 0.000 ± 0.000 | 3.000 ± 1.414 | 0.000 ± 0.000 |

## Mock vs live gap (`acg_planned_replan` F1)

Per-task means over seeds 1–3. Column **`live_f1 − mock_f1`**.

| repo | task_id | mock_f1 | live_f1 | live_f1 − mock_f1 |
|------|---------|---------|---------|-------------------|
| urllib3 | pr-4974 | 0.000 | 0.311 | **+0.311** |
| fastify | pr-6653 | 0.000 | 0.136 | **+0.136** |
| axios | pr-10868 | 0.400 | 0.500 | +0.100 |
| flask | pr-5917 | 0.242 | 0.263 | +0.020 |
| (remaining tasks) | — | (same as live within rounding) | — | 0.000 |

**Largest gap tasks (live − mock):** `urllib3/pr-4974`, `fastify/pr-6653`, `axios/pr-10868`.

Diagnostics: **mean(`mock_f1 − live_f1`) = −0.052**; **max = 0.000** (no mock-over-live optimism in this slice).

## Typings recovery (`.d.ts` / `.pyi` in ground truth)

| repo | task_id | Ground truth typings | seed 1 | seed 2 | seed 3 |
|------|---------|----------------------|--------|--------|--------|
| fastify | pr-6653 | `types/request.d.ts` | yes (`candidate_context_paths` lists `types/request.d.ts` / `types/content-type-parser.d.ts`) | yes | yes |

**`blocked_truth_recoverable_fraction` (macro mean ± σ):** 0.142 ± 0.021 — `after_live_predictor_summary.csv` · scope `macro`.

## Remaining gaps vs viability thresholds (section 7)

| Threshold | PASS / FAIL | Observed |
|-----------|-------------|----------|
| `hard_precision ≥ 0.85` | **FAIL** | 0.641 ± 0.019 (`after_live_predictor_summary.csv` · macro) |
| `hard_f1 ≥ 0.70` | **FAIL** | 0.552 ± 0.014 (`after_live_predictor_summary.csv` · macro) |
| `hard_recall_after_replan ≥ 0.85` | **FAIL** | proxy: `macro_recall_mean` for `acg_planned_replan` = **0.561 ± 0.017** (`after_live_strategy_summary_variance.csv` · `backend=local`; no separate `hard_recall_after_replan` column in harness) |
| `candidate_recall ≥ 0.90` | **FAIL** | 0.645 ± 0.039 (`after_live_predictor_summary.csv` · macro) |
| `candidate_precision ≥ 0.25` | **PASS** | 0.641 ± 0.021 |
| `candidate_count_median ≤ 18` | **PASS** | 0.000 ± 0.000 (median-of-task `candidate_context_count` per seed, then averaged — **`after_live_predictor_summary.csv` · macro**; see gate 2 for band mismatch) |

**Section 7 score:** **2 / 6 PASS** (needs ≥ 4 of 6 for viability alongside Step 3).

---

**VERDICT: regression —** aggregate `candidate_recall` is **0.645 ± 0.039** (< 0.70), seed-2 abort would have fired on **0.632** union mean over seeds 1–2, and `macro_f1(acg_planned_replan)` is below **`macro_f1(naive_parallel) − σ`** per `after_live_strategy_summary_variance.csv`.
