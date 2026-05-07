# Retracted

This Round 5 run is methodologically invalid. The harness was invoked against a Fastify checkout whose `.acg/context_graph.json` had been removed by Lane O3's cleanup step, so the harness's `_load_repo_graph(repo_path)` returned an empty `{}`. Without a populated repo graph, the `acg_planned` (scoped graph), `acg_planned_full_context` (full graph), and `naive_parallel` (full graph) strategies all collapsed to the same minimal prompt construction.

## Symptom

All 9 eval JSONs in this directory record `tokens_prompt_total` of approximately 130 tokens regardless of strategy:

- pr-6653: acg=132, naive=132, full=132
- pr-6692: acg=130, naive=130, full=130
- pr-6694: acg=127, naive=127, full=127

Round 3 Qwen baseline (with intact context graph) showed ~50% prompt-token reduction for `acg_planned` vs `naive_parallel`:

- pr-6653: acg=172, naive=350, full=350
- pr-6692: acg=155, naive=348, full=348
- pr-6694: acg=157, naive=344, full=344

The corrected Kimi rerun (after regenerating the context graph) reproduces the Round 3 token-reduction profile (170/344/344 on pr-6653, etc.) and produces meaningful F1 scores.

## Consequence

- `acg_planned` and `acg_planned_full_context` strategies: the agent received bare task descriptions only, proposed OOB writes, the validator blocked all of them, `actual_changed_files` was empty, scored as F1 0.000.
- `naive_parallel` strategy: same bare prompt but no scope to enforce, so OOB proposals went through; macro F1 0.278 by chance.

The 0.000 vs 0.278 numbers do NOT test the model-scaling claim, despite earlier PAPER_NUMBERS.md text presenting them as such.

## Where to look instead

Corrected run: `experiments/real_repos/fastify/runs_kimi_v2/`. See the "Round 5 Kimi K2 Fastify Scaling Check (Retracted + Corrected)" section in `experiments/PAPER_NUMBERS.md`.

## Why this directory is preserved

Kept as historical record (matching the Round 3 -> Round 4 retraction pattern), not deleted. Do not cite any artifact under this path as evidence.
