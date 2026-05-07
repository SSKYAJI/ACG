# Graph Quality v2 Diff

## What Changed

`report.md` mixed sources and conditions: some rows came from prior combined eval artifacts, one from a filtered Greenhouse analysis report, and RealWorld selected the lower-F1 harder case. `report_v2` reruns all four codebases through the same harness path with `backend=local`, `strategy=acg_planned`, OpenRouter env, and `qwen/qwen3-coder-30b-a3b-instruct`.

| Codebase | Old precision | New precision | Old recall | New recall | Old F1 | New F1 | Delta F1 |
|---|---:|---:|---:|---:|---:|---:|---:|
| demo-app TS | 0.5909 | 0.4545 | 0.8667 | 1.0000 | 0.7027 | 0.6250 | -0.0777 |
| Brocoders TS | 0.0714 | 0.5536 | 1.0000 | 0.8857 | 0.1333 | 0.6813 | +0.5480 |
| RealWorld TS | 0.5128 | 0.4889 | 0.8696 | 1.0000 | 0.6452 | 0.6567 | +0.0115 |
| Greenhouse Java | 0.2500 | 0.2500 | 1.0000 | 1.0000 | 0.4000 | 0.4000 | +0.0000 |

The biggest correction is Brocoders. Under the fresh `acg_planned` local run, Brocoders is no longer the low-F1 outlier from the old mixed-condition report. Demo-app moves down because the live model proposed fewer accepted test files than the lockfile predicted.

## Correlation Check

The graph-density-to-F1 claim is not supported.

| Metric pair | Old Pearson | New Pearson | Old Spearman | New Spearman |
|---|---:|---:|---:|---:|
| Density vs F1 | -0.5575 | -0.7571 | -0.8000 | -0.2000 |
| Density vs recall | 0.8122 | -0.0709 | 0.9487 | -0.2582 |

F1 correlation does not survive as a positive graph-quality story. It remains negative, and the new Pearson value is more negative than before. Recall also no longer gives the previous positive density signal; three rows are at `1.0000`, while Brocoders is `0.8857`.

## Paper Recommendation

Do not keep the graph-quality story as currently framed. The honest version is:

- Drop any claim that denser graphs predict better F1 in this sample.
- Keep Lane C only if it is reframed as a recall and boundary-safety observation: planned scoped runs kept recall high (`0.8857-1.0000`) and accepted zero out-of-bounds writes, while blocking seven out-of-scope proposals across demo-app and Brocoders.
- If the paper needs a density/F1 evidence lane rather than a recall/safety lane, retract Lane C until there is a larger, consistently scored sample with a non-negative relationship.

## Eval Commands

```bash
set -a && . ./.env && set +a && ./.venv/bin/python -m experiments.greenhouse.headtohead --lock demo-app/agent_lock.json --tasks demo-app/tasks.json --repo demo-app --backend local --strategy acg_planned --out-dir experiments/graph_quality/eval_runs/demo_app --suite-name demo-app-openrouter-graph-quality-v2
set -a && . ./.env && set +a && ./.venv/bin/python -m experiments.greenhouse.headtohead --lock experiments/microservice/agent_lock_brocoders.json --tasks experiments/microservice/tasks_brocoders.json --repo experiments/microservice/nestjs-boilerplate --backend local --strategy acg_planned --out-dir experiments/graph_quality/eval_runs/brocoders --suite-name brocoders-openrouter-graph-quality-v2
set -a && . ./.env && set +a && ./.venv/bin/python -m experiments.greenhouse.headtohead --lock experiments/realworld/agent_lock.json --tasks experiments/realworld/tasks_explicit.json --repo experiments/realworld/checkout --backend local --strategy acg_planned --out-dir experiments/graph_quality/eval_runs/realworld --suite-name realworld-openrouter-graph-quality-v2
set -a && . ./.env && set +a && ./.venv/bin/python -m experiments.greenhouse.headtohead --lock experiments/greenhouse/agent_lock.json --tasks experiments/greenhouse/tasks.json --repo experiments/greenhouse/checkout --backend local --strategy acg_planned --out-dir experiments/graph_quality/eval_runs/greenhouse --suite-name greenhouse-openrouter-graph-quality-v2
```

## Analyzer Commands

```bash
./.venv/bin/acg analyze-runs experiments/graph_quality/eval_runs/demo_app --out experiments/graph_quality/eval_runs/demo_app/analysis_report.md --json-out experiments/graph_quality/eval_runs/demo_app/analysis_report.json
./.venv/bin/acg analyze-runs experiments/graph_quality/eval_runs/brocoders --out experiments/graph_quality/eval_runs/brocoders/analysis_report.md --json-out experiments/graph_quality/eval_runs/brocoders/analysis_report.json
./.venv/bin/acg analyze-runs experiments/graph_quality/eval_runs/realworld --out experiments/graph_quality/eval_runs/realworld/analysis_report.md --json-out experiments/graph_quality/eval_runs/realworld/analysis_report.json
./.venv/bin/acg analyze-runs experiments/graph_quality/eval_runs/greenhouse --out experiments/graph_quality/eval_runs/greenhouse/analysis_report.md --json-out experiments/graph_quality/eval_runs/greenhouse/analysis_report.json
```
