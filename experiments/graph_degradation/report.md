# Graph Degradation Report

| Variant | precision | recall | F1 | delta F1 vs control |
| --- | ---: | ---: | ---: | ---: |
| control | 1.000 | 1.000 | 1.000 | 0.000 |
| degraded_no_symbols | 0.957 | 1.000 | 0.978 | -0.022 |
| degraded_no_imports | 0.957 | 1.000 | 0.978 | -0.022 |
| degraded_no_structure | 0.957 | 1.000 | 0.978 | -0.022 |

This ablation treats the full `demo-app` graph as the control and recompiles the lockfile from structurally stripped graph copies. The headline result is a small, repeatable degradation across all stripped variants, but it should be read as limited evidence rather than strong graph-causality evidence.

## Verification

The requested strip checks pass at the per-file graph fields:

- `degraded_no_symbols`: `symbols_index == {}` and every file has `symbols == []`.
- `degraded_no_imports`: every file has `imports == []` and `exports == []`.
- `degraded_no_structure`: both symbol and import/export file-field checks pass.

The cached `repo/.acg/context_graph.json` files reused by compile are byte-identical to the run-level graph copies checked above.

Each degraded lockfile is byte-different from `demo-app/agent_lock.json`, and each degraded predicted write set differs from control by the same semantic delta: the `settings` task adds `src/app/profile/page.tsx`; no control predictions are removed. That single extra prediction explains the shared precision/F1 drop, with recall unchanged.

The lockfiles do not contain a formal seed/evidence object. The available evidence is limited to `predicted_writes[].reason` strings and compile logs. Those reasons are dominated by prompt/static filename and framework-convention cues on `demo-app` (`Settings page route`, `Billing dashboard route`, env-var, Playwright, Next.js/API-route conventions), with fewer PageRank/BM25 graph or lexical reasons. A heuristic classification of the control lockfile counts 16 of 22 predicted writes as static/prompt/convention driven and 6 of 22 as index/graph/lexical driven.

Interpretation: the stripped graph copies were read, and the degraded variants are not byte-identical or prediction-identical to control. However, all stripped variants converge to the same modest F1 drop (-0.022), so this is best cited as a defensible modest-degradation finding. It should not be cited as strong evidence that `demo-app` prediction quality is causally driven by graph structure. One additional limitation is that raw graph-level `imports`/`exports` maps remain populated in the JSON artifacts, while the requested checks cover the per-file fields; the compile loader normalizes those top-level maps from per-file fields in memory.
