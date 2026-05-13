# Predictor vs agent breakdown (`runs_kimi_v2`)

This note aggregates **Fastify** Kimi K2 runs under `experiments/real_repos/*/runs_kimi_v2/`, using the same definitions as `scripts/analysis/predictor_vs_agent.py`:

- **Predictor miss** — ground-truth file not covered by `allowed_write_globs` (contract too narrow).
- **Agent miss within scope** — ground truth in the globs but missing from the agent’s proposed `actual_changed_files`.
- **Agent overshoot** — proposed file not in the human diff.

**Strategy:** `acg_planned` (local propose/validate artifacts). Ground truth lists come from each repo’s `runs_kimi_v2/ground_truth_score.json`.

## Repos with `runs_kimi_v2/`

| Repo | `runs_kimi_v2/` | Notes |
| --- | --- | --- |
| `fastify` | yes | Three PRs below. |
| `starlette` | _not in tree_ | No `runs_kimi_v2/` checkout yet; older scores live under `starlette/runs/` if needed later. |
| others | _none found_ | Re-run this table when new repos add `runs_kimi_v2/`. |

## Fastify (`moonshotai/kimi-k2-0905`)

Macro **acg_planned** recall **0.278** in `ground_truth_score.json` is dominated by **predictor misses**: entire human touched files sit outside `allowed_paths`, so the agent literally could not score a true positive on them. Per-PR, **recall within scope** counts only ground-truth files that intersect `allowed_write_globs`.

| PR | GT files | Predictor miss (GT ∉ allowed) | Agent miss in scope | Overshoot | Recall within scope |
| --- | ---: | ---: | ---: | ---: | --- |
| pr-6653 | 5 | 5 | 0 | 3 | _n/a_ (no GT file in allowed globs) |
| pr-6692 | 2 | 1 (`lib/content-type-parser.js`) | 0 | 0 | **1.00** (1/1 scoped GT) |
| pr-6694 | 3 | 2 (`lib/handle-request.js`, `lib/request.js`) | 0 | 1 (`test/content-parser.test.js`) | **1.00** (1/1 scoped GT) |

**Takeaway:** ACG agent **1.00** recall within scope on **2/3** PRs where the denominator is defined; the third PR’s human diff is entirely outside the compiled write contract.

### Commands

From the repo root (so `experiments/real_repos/...` resolves):

```bash
./.venv/bin/python scripts/analysis/predictor_vs_agent.py \
  --repo fastify --pr 6653 \
  --eval-run experiments/real_repos/fastify/runs_kimi_v2/pr-6653/eval_run_combined.json
```

Repeat with `--pr 6692` / `--pr 6694` and matching `--eval-run` paths.
