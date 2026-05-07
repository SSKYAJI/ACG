# Fastify Predictor vs Agent Decomposition

This note decomposes the low absolute `acg_planned` macro `agent_match_to_human` F1 on the three Fastify historical PRs into predictor scope and agent selection effects. The score being decomposed is F1 between each local proposal artifact's `actual_changed_files` and the manifest `ground_truth_files`; those paths are repo-relative proposal files, not applied writes. @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:57 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:58 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:59

## Method

- `predictor_recall_pr = |allowed_paths ∩ ground_truth| / |ground_truth|`.
- `predictor_precision_pr = |allowed_paths ∩ ground_truth| / |allowed_paths|`.
- `within_scope_agent_recall = |agent_proposed ∩ within_scope_gt| / max(|within_scope_gt|, 1)`.
- `within_scope_agent_precision = |agent_proposed ∩ within_scope_gt| / max(|agent_proposed|, 1)`.
- `f1` is recomputed from `agent_proposed` vs `ground_truth` and checked against `ground_truth_score.json`.

## Per-PR Decomposition

### pr-6653

Ground truth is `lib/handle-request.js`, `lib/request.js`, `lib/symbols.js`, `lib/validation.js`, and `types/request.d.ts`. The lockfile allowed only `lib/content-type-parser.js`, `test/content-parser.test.js`, `test/internals/validation.test.js`, `test/logger/logger-test-utils.js`, and `types/content-type-parser.d.ts`. @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/manifest.json:56 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/manifest.json:63 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/manifest.json:64 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/manifest.json:65 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/manifest.json:66 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/manifest.json:67 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/manifest.json:68 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/agent_lock_pr-6653.json:48

| Metric | Qwen R3 acg_planned | Kimi R5 acg_planned |
| --- | --- | --- |
| allowed_paths | `lib/content-type-parser.js`; `test/content-parser.test.js`; `test/internals/validation.test.js`; `test/logger/logger-test-utils.js`; `types/content-type-parser.d.ts` | same |
| predictor_recall_pr | `0.000` (`0/5`) | `0.000` (`0/5`) |
| predictor_precision_pr | `0.000` (`0/5`) | `0.000` (`0/5`) |
| within_scope_gt | empty | empty |
| out_of_scope_gt | `lib/handle-request.js`; `lib/request.js`; `lib/symbols.js`; `lib/validation.js`; `types/request.d.ts` | same |
| agent_proposed | `lib/content-type-parser.js`; `test/content-parser.test.js`; `test/internals/validation.test.js` | `lib/content-type-parser.js`; `test/content-parser.test.js`; `types/content-type-parser.d.ts` |
| tp_within_scope | empty | empty |
| fp_total | `lib/content-type-parser.js`; `test/content-parser.test.js`; `test/internals/validation.test.js` | `lib/content-type-parser.js`; `test/content-parser.test.js`; `types/content-type-parser.d.ts` |
| within_scope_agent_recall | `0.000` (`0/max(0,1)`) | `0.000` (`0/max(0,1)`) |
| within_scope_agent_precision | `0.000` (`0/3`) | `0.000` (`0/3`) |
| f1 | `0.000` | `0.000` |
| decomposition | lost-to-predictor `5`; lost-to-agent `0`; captured-by-agent `0` | lost-to-predictor `5`; lost-to-agent `0`; captured-by-agent `0` |

Sources for proposals and checked F1: Qwen proposed three content/validation-test files and scored F1 `0.000`; Kimi proposed three content-type files and also scored F1 `0.000`. Kimi additionally recorded blocked attempted writes to `lib/validation.js` and `lib/request.js`, both outside `allowed_paths` and both ground-truth request-side files. @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/pr-6653/eval_run_acg.json:49 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/pr-6653/eval_run_acg.json:50 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/pr-6653/eval_run_acg.json:51 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/pr-6653/eval_run_acg.json:52 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:78 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:88 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/pr-6653/eval_run_acg.json:64 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/pr-6653/eval_run_acg.json:65 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/pr-6653/eval_run_acg.json:66 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/pr-6653/eval_run_acg.json:67 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:64 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:67 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/pr-6653/eval_run_acg.json:85 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/pr-6653/eval_run_acg.json:88 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/pr-6653/eval_run_acg.json:93

### pr-6692

Ground truth is `lib/content-type-parser.js` and `lib/schemas.js`. The lockfile allowed `lib/schemas.js`, `test/internals/reply-serialize.test.js`, and `types/content-type-parser.d.ts`. @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/manifest.json:43 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/manifest.json:50 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/manifest.json:51 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/manifest.json:52 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/agent_lock_pr-6692.json:38

| Metric | Qwen R3 acg_planned | Kimi R5 acg_planned |
| --- | --- | --- |
| allowed_paths | `lib/schemas.js`; `test/internals/reply-serialize.test.js`; `types/content-type-parser.d.ts` | same |
| predictor_recall_pr | `0.500` (`1/2`) | `0.500` (`1/2`) |
| predictor_precision_pr | `0.333` (`1/3`) | `0.333` (`1/3`) |
| within_scope_gt | `lib/schemas.js` | same |
| out_of_scope_gt | `lib/content-type-parser.js` | same |
| agent_proposed | `lib/schemas.js` | `lib/schemas.js` |
| tp_within_scope | `lib/schemas.js` | `lib/schemas.js` |
| fp_total | empty | empty |
| within_scope_agent_recall | `1.000` (`1/1`) | `1.000` (`1/1`) |
| within_scope_agent_precision | `1.000` (`1/1`) | `1.000` (`1/1`) |
| f1 | `0.667` | `0.667` |
| decomposition | lost-to-predictor `1`; lost-to-agent `0`; captured-by-agent `1` | lost-to-predictor `1`; lost-to-agent `0`; captured-by-agent `1` |

Sources for proposals and checked F1: both Qwen and Kimi proposed exactly `lib/schemas.js`, matching the only ground-truth file that was in scope, and both scored F1 `0.667`. @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/pr-6692/eval_run_acg.json:49 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/pr-6692/eval_run_acg.json:50 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:219 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:220 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:228 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:229 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/pr-6692/eval_run_acg.json:64 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/pr-6692/eval_run_acg.json:65 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:119 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:120 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:126 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:127

### pr-6694

Ground truth is `lib/content-type-parser.js`, `lib/handle-request.js`, and `lib/request.js`. The lockfile allowed `lib/content-type-parser.js`, `test/content-parser.test.js`, `test/logger/logger-test-utils.js`, and `types/content-type-parser.d.ts`. @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/manifest.json:29 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/manifest.json:36 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/manifest.json:37 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/manifest.json:38 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/manifest.json:39 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/agent_lock_pr-6694.json:43

| Metric | Qwen R3 acg_planned | Kimi R5 acg_planned |
| --- | --- | --- |
| allowed_paths | `lib/content-type-parser.js`; `test/content-parser.test.js`; `test/logger/logger-test-utils.js`; `types/content-type-parser.d.ts` | same |
| predictor_recall_pr | `0.333` (`1/3`) | `0.333` (`1/3`) |
| predictor_precision_pr | `0.250` (`1/4`) | `0.250` (`1/4`) |
| within_scope_gt | `lib/content-type-parser.js` | same |
| out_of_scope_gt | `lib/handle-request.js`; `lib/request.js` | same |
| agent_proposed | `lib/content-type-parser.js`; `test/content-parser.test.js`; `types/content-type-parser.d.ts` | `lib/content-type-parser.js`; `test/content-parser.test.js` |
| tp_within_scope | `lib/content-type-parser.js` | `lib/content-type-parser.js` |
| fp_total | `test/content-parser.test.js`; `types/content-type-parser.d.ts` | `test/content-parser.test.js` |
| within_scope_agent_recall | `1.000` (`1/1`) | `1.000` (`1/1`) |
| within_scope_agent_precision | `0.333` (`1/3`) | `0.500` (`1/2`) |
| f1 | `0.333` | `0.400` |
| decomposition | lost-to-predictor `2`; lost-to-agent `0`; captured-by-agent `1` | lost-to-predictor `2`; lost-to-agent `0`; captured-by-agent `1` |

Sources for proposals and checked F1: both agents selected the only in-scope ground-truth file, `lib/content-type-parser.js`. Kimi's higher F1 is from one fewer false positive, not from recovering the out-of-scope request-side files. @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/pr-6694/eval_run_acg.json:49 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/pr-6694/eval_run_acg.json:50 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/pr-6694/eval_run_acg.json:51 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/pr-6694/eval_run_acg.json:52 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:326 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:327 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:338 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:339 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/pr-6694/eval_run_acg.json:64 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/pr-6694/eval_run_acg.json:65 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/pr-6694/eval_run_acg.json:66 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:164 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:165 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:171 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:172

## Macro Summary

| Model / strategy | Predictor recall macro | Predictor precision macro | Within-scope agent recall macro | Within-scope agent precision macro | F1 macro | Lost-to-predictor | Lost-to-agent | Captured-by-agent |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen R3 `acg_planned` | `0.278` | `0.194` | `0.667` | `0.444` | `0.333` | `8 total` (`2.667/PR`) | `0 total` (`0.000/PR`) | `2 total` (`0.667/PR`) |
| Kimi R5 `acg_planned` | `0.278` | `0.194` | `0.667` | `0.500` | `0.356` | `8 total` (`2.667/PR`) | `0 total` (`0.000/PR`) | `2 total` (`0.667/PR`) |

The requested macro within-scope recall includes pr-6653 as `0/max(0,1)`, so it is `0.667`. If computed over the actually in-scope ground-truth files, both models are `2/2 = 1.000` recall: `lib/schemas.js` on pr-6692 and `lib/content-type-parser.js` on pr-6694. The aggregate F1 values match the stored scores: Qwen `0.333` and Kimi `0.356`, while the deterministic predictor macro is recall `0.278` and precision `0.194`. @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:3 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:4 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:5 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:53 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:55 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:56 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:3 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:4 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:5

## Verdict

Per PR, the dominant ceiling is predictor scope. pr-6653 is fully predictor-bound: the lockfile had zero overlap with five ground-truth files, so F1 `0.000` was forced by scope before model capability mattered. pr-6692 is partially predictor-bound: the predictor surfaced one of two ground-truth files, and both agents captured that one. pr-6694 is also predictor-bound on recall: the predictor surfaced only `lib/content-type-parser.js` out of three ground-truth files, and both agents captured that in-scope file; Kimi's absolute F1 improves only because it emits one fewer false positive than Qwen. @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/agent_lock_pr-6653.json:48 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/agent_lock_pr-6692.json:38 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/agent_lock_pr-6694.json:43

Macro judgment: Kimi's `0.356` F1 is not credible evidence of a frontier-model capability ceiling. Across the 10 ground-truth files, 8 were outside the predictor's allowed scope, 2 were captured by the agent, and 0 in-scope ground-truth files were lost to the agent. The main residual agent-side issue in this slice is precision, not recall: Qwen had 5 total false positives and Kimi had 4, producing Kimi's small F1 advantage. @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:23 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:24 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:25 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:27 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:28 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:23 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:24 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:25 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:27 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs/ground_truth_score.json:28

## Recommended Paper Framing

Frame Kimi's Fastify `acg_planned` F1 of `0.356` as the realized performance under a narrow predictor, not as Kimi's file-selection ceiling. The more defensible claim is: on this small Fastify slice, ACG's absolute F1 is bounded by predictor recall (`0.278` macro; 8/10 ground-truth files out of scope), while the agent captures all in-scope ground-truth files and mostly differs by false-positive behavior. This supports a paper claim about predictor-scope bottlenecks and scoped-contract usefulness, with the usual caveat that this is `N=3` PRs in one JavaScript repo rather than a general model-capability benchmark. @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:3 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:4 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:5 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:6 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:7 @/Users/prajit/Desktop/projects/cognition/experiments/real_repos/fastify/runs_kimi_v2/ground_truth_score.json:8
