# Lane S Done

Ran the Round 5 Fastify model-scaling check with Kimi K2 0905.

- Working model slug: `moonshotai/kimi-k2-0905`; the first requested slug succeeded, so no fallback was used.
- Harness: `./.venv/bin/python -m experiments.greenhouse.headtohead --backend local --strategy ablation`, with `ACG_LLM_MODEL=moonshotai/kimi-k2-0905` and `ACG_LLM_URL=https://openrouter.ai/api/v1`.
- Lockfiles reused: `experiments/real_repos/fastify/agent_lock_pr-6653.json`, `agent_lock_pr-6692.json`, and `agent_lock_pr-6694.json`. The requested `runs/pr-*/agent_lock.json` files were not present; prior artifacts also point at the root `agent_lock_pr-*.json` files.
- Artifacts written under `experiments/real_repos/fastify/runs_kimi/pr-{6653,6692,6694}/`.
- Score written to `experiments/real_repos/fastify/runs_kimi/ground_truth_score.json`.
- Provider accounting was clean: all runs used `tokens_prompt_method: provider_usage_prompt_tokens` and `cost_method: sum_provider_reported_task_costs`.
- Recorded provider cost across the nine harness calls: `$0.0022175`.
- Predictor write sets matched the Qwen Fastify run exactly.
- Macro `agent_match_to_human` F1: `acg_planned=0.000`, `acg_planned_full_context=0.000`, `naive_parallel=0.278`.
- Final pytest: `211 passed, 11 warnings`.
