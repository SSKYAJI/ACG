# Graph Quality Report v2

Fresh OpenRouter evals were run under consistent proposal-only conditions: `backend=local`, `strategy=acg_planned`, `model=qwen/qwen3-coder-30b-a3b-instruct`, and the existing head-to-head harness. Graph metrics are reused from `experiments/graph_quality/report.json`.

| Codebase | Language | Files | Symbols | Imports | Exports | Hotspots | Density | Precision | Recall | F1 | Eval artifact |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| demo-app TS | TypeScript | 19 | 24 | 28 | 23 | 4 | 3.95 | 0.4545 | 1.0000 | 0.6250 | `experiments/graph_quality/eval_runs/demo_app/eval_run_acg.json` |
| Brocoders TS | TypeScript | 163 | 163 | 713 | 161 | 44 | 6.36 | 0.5536 | 0.8857 | 0.6813 | `experiments/graph_quality/eval_runs/brocoders/eval_run_acg.json` |
| RealWorld TS | TypeScript | 39 | 39 | 139 | 40 | 12 | 5.59 | 0.4889 | 1.0000 | 0.6567 | `experiments/graph_quality/eval_runs/realworld/eval_run_acg.json` |
| Greenhouse Java | Java | 208 | 534 | 1097 | 180 | 23 | 8.71 | 0.2500 | 1.0000 | 0.4000 | `experiments/graph_quality/eval_runs/greenhouse/eval_run_acg.json` |

## Analyzer Outputs

| Codebase | Analyzer artifact | TP | FP | FN | Blocked proposals |
|---|---|---:|---:|---:|---:|
| demo-app TS | `experiments/graph_quality/eval_runs/demo_app/analysis_report.json` | 10 | 12 | 0 | 1 |
| Brocoders TS | `experiments/graph_quality/eval_runs/brocoders/analysis_report.json` | 31 | 25 | 4 | 6 |
| RealWorld TS | `experiments/graph_quality/eval_runs/realworld/analysis_report.json` | 22 | 23 | 0 | 0 |
| Greenhouse Java | `experiments/graph_quality/eval_runs/greenhouse/analysis_report.json` | 6 | 18 | 0 | 0 |

## Result

The density/F1 story does not survive v2. Density vs F1 is negative under the fresh consistent evals: Pearson `-0.7571`, Spearman `-0.2000`. Density vs recall is effectively flat/slightly negative: Pearson `-0.0709`, Spearman `-0.2582`.

Recommendation: do not keep a graph-density-versus-F1 claim. If Lane C stays in the paper, reframe it around high recall and write-boundary safety under planned proposal-only evals. Otherwise retract Lane C as graph-quality evidence.

## Failed / Inconclusive

No codebase failed. RealWorld used `experiments/realworld/agent_lock.json`; the blind-lock fallback was not used.

Note: these are local proposal-only artifacts. `actual_changed_files` means accepted/proposed write set, not applied git diffs or tested implementation correctness.
