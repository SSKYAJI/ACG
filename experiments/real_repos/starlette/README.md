# Starlette real-repo benchmark (`experiments/real_repos/starlette`)

Python [Starlette](https://github.com/encode/starlette) checkout pinned for three PR-style tasks (combined lockfile + task list).

## Layout

| Path | Purpose |
| --- | --- |
| `runs_sonnet_v2_n5/` | **Canonical baseline** — Claude Sonnet 4.6 (Anthropic direct), `comparison_full`, 5 seeds × 3 tasks. |
| `runs_RETRACTED_kimi_n5_applied_8K_truncated/` | **Retracted** v1 sweep: `ACG_WORKER_MAX_TOKENS` defaulted to 8192/4096, biasing metrics via truncation (`RETRACTED.md`). Do not use for comparisons. |
| `runs_RETRACTED_kimi_n5_applied_8K_truncated_v1_audited/` | **Retracted** archived audit copy of the same; do not use. |
| `runs_smoke/` | Output from `smoke.sh` (short one-task harness). |
| `multi_seed_sonnet.sh` | Sonnet v2 re-run script (`comparison_full`, writes `runs_sonnet_v2_n5/`). |
| `multi_seed_deepseek.sh` | DeepSeek V4 Flash run script (OpenRouter sweeps, writes `runs_deepseek_n5/`). |
| `runs_deepseek_n5/` | Active: cheap-model benchmark outputs (currently being populated). |
| `multi_seed_kimi.sh.audited_v1` | Frozen historical script snapshot (not used for new runs). |
| `agent_lock_combined.json` / `tasks_combined.json` | Harness inputs. |
| `merge_combined.py` | Rebuild `eval_run_combined.json` per seed from per-strategy `eval_run_*.json`. |
| `aggregate.py` | Bootstrap aggregate over seeds → `aggregate.json` / `aggregate.md`. |
| `setup.sh` | Pin checkout, refresh graph, verify pytest collection. |
| `smoke.sh` | Environment-backed one-task smoke harness (short wall cap). |

## After a sweep

```bash
./.venv/bin/python experiments/real_repos/starlette/merge_combined.py \
  --base-dir experiments/real_repos/starlette/runs_sonnet_v2_n5
./.venv/bin/python experiments/real_repos/starlette/aggregate.py
```

Point `--base-dir` at `runs_deepseek_n5/` (or another run root) for non-Sonnet sweeps once those directories exist.
