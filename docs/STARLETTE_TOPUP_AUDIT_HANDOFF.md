# Starlette 5-strategy top-up — final audit handoff

This is the gating checklist before we scale up to more repos / models. Audit the data, the code path, and the claim chain. If anything is shaky, surface it before we burn money on bigger runs.

You are picking up after a partial Anthropic-credit-exhaustion incident and an envelope-format change to `single_agent`. Be skeptical.

## How to read this repo cheaply

- The runtime + strategy code lives in `acg/runtime.py` and `experiments/greenhouse/strategies.py` — both are >1000 LOC. **Do not read either end-to-end.** Use the Task tool with `subagent_type=explore` and `model=claude-sonnet-4-6` for any file-traversal-heavy work. Spawn parallel explore subagents whenever an audit step touches more than one file.
- The eval_run JSONs are 100-3000 lines each and contain large `tasks[].proposals[]` arrays. Have a sonnet explore subagent extract only the fields the audit step needs; don't read full JSONs yourself.
- Use `Grep` (ripgrep) for exact-symbol lookups; `SemanticSearch` for "where is X handled" questions; `Read` only for short, targeted spans you already know the location of.
- The `CLAUDE.md` at repo root has the project layout, invariants, and venv path (`./.venv/bin/python`).

## Where the data lives

- **Canonical baseline (current):** `experiments/real_repos/starlette/runs_sonnet_v2_n5/` — Sonnet 4.6 re-run; use this for comparisons.
- **Retracted v1 (historical audit target; do not use for headline metrics):** `experiments/real_repos/starlette/runs_RETRACTED_kimi_n5_applied_8K_truncated/` (see `RETRACTED.md`). Same on-disk tree as the former `runs_kimi_n5_applied/` path referenced in earlier drafts of this checklist.
  - `seed{1..5}/eval_run_<strategy>.json` — five strategies per seed.
  - `seed{1..5}/eval_run_combined.json` — rebuilt from the five per-strategy files via `merge_combined.py`.
  - `seed{1..5}/run_attempt1.log` — captures all `[ALLOWED]`/`[BLOCKED]` events streamed during the run.
  - `aggregate.json`, `aggregate.md` — bootstrap aggregation across 5 seeds.
  - `RESULTS.md` — narrative summary with per-seed cells and headline table.
- **Headline cheatsheet entry:** `experiments/PAPER_NUMBERS.md` "Round 6" section.
- **Inputs:** `experiments/real_repos/starlette/agent_lock_combined.json` (lockfile, 3 tasks), `experiments/real_repos/starlette/tasks_combined.json`.
- **Repo checkout:** `experiments/real_repos/starlette/checkout/` at commit `2b73aecd8377e0c189943a5f30d3dbab134f6104`.

## Experiment setup

- Model: `claude-sonnet-4-6` over Anthropic direct (`https://api.anthropic.com/v1`), OpenAI-compatible client. Two API keys were used; first ran dry after ~$2.55 last night, second key powered today's 14-minute top-up.
- 5 strategies × 5 seeds × 3 tasks = 75 task-attempts per strategy.
- Tasks: `pr3148-jinja2-autoescape`, `pr3137-cors-credentials-origin`, `pr3166-session-middleware` (real upstream Starlette PRs).
- Mode: **propose-validate** (`evidence_kind=proposed_write_set` and `suite_proposed_write_set`), not applied-diff. No tests are run; we only score what the agent proposed to write and whether the validator would accept it.

## Code changes shipped in this round (uncommitted)

1. `experiments/greenhouse/headtohead.py` — added `STRATEGY_GROUPS["top_up"]` = `[SINGLE_AGENT_STRATEGY, NAIVE_PARALLEL_BLIND_STRATEGY]` and `STRATEGY_GROUPS["comparison_full"]` = all 5. Check this is wired to the dispatcher.
2. `experiments/greenhouse/strategies.py` — added `apply_patch_suites` kwarg to `_build_single_agent_prompt`; `_run_single_agent` honors `ACG_SINGLE_AGENT_APPLY_PATCH=1` to swap the legacy JSON-paths system prompt for an apply_patch envelope system prompt. The parser path also forks: envelope mode uses `_parse_single_agent_applied_envelopes` and a regex over `*** Update/Add/Delete File:` headers to populate `writes`. The legacy JSON path is preserved as the fallback. **Verify the envelope mode parser correctly derives `actual_changed_files` and that the file is present in the output JSON.**
3. `experiments/real_repos/starlette/multi_seed_sonnet.sh` (formerly `multi_seed_kimi.sh`) — added `ACG_STRATEGY` env override (defaults to `comparison`); fixed a nested-quote bug in the DRY-RUN echo.
4. `experiments/real_repos/starlette/aggregate.py` (formerly `aggregate_kimi.py`) — added `"naive_parallel_blind"` to `STRATEGIES_DEFAULT`.
5. `experiments/real_repos/starlette/merge_combined.py` — NEW utility. Walks a base dir, reads every `eval_run_<short>.json`, writes a unified `eval_run_combined.json`. Short-name → strategy mapping mirrors `headtohead.py._short_name`.

## Headline numbers to verify (paper-grade)

| strategy | completion mean | tok_compl mean | tok_compl stdev | tok_prompt | wall_s mean | OOB writes (total across 5 seeds) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `naive_parallel_blind` | 0.333 | 13,759 | 1,662 | 842 | 81.2 | 12 |
| `naive_parallel` | 0.933 | 12,513 | 2,387 | 1,194 | 64.3 | 0 |
| `acg_planned_full_context` | 1.000 | 10,076 | 3,386 | 1,194 | 75.6 | 0 |
| `acg_planned` | 0.800 | 9,769 | 1,682 | 1,194 | 75.2 | 0 |
| `single_agent` (apply_patch) | 1.000 | 12,386 | 3,246 | 400 | 133.9 | 0 |

Per-seed cells are in `runs_RETRACTED_kimi_n5_applied_8K_truncated/RESULTS.md` (retracted; do not cite for new comparisons). The deterministic per-seed completion pattern is striking: `naive_parallel_blind` is 0.333 in **every** seed (5/15 tasks, perfect 1/3); `acg_planned_full_context` is 1.000 in **every** seed.

## Mandatory audit checklist

Work top-down. Stop and report any failure immediately; don't keep auditing past a real defect. For each item, show me the file/line that proves your claim, and the actual numbers you computed.

### A. Data integrity

A1. **All 5 strategies present in every seed.** Confirm each `seed{1..5}/eval_run_combined.json` has `strategies` keys exactly = `{acg_planned, acg_planned_full_context, naive_parallel, naive_parallel_blind, single_agent}` and that each per-strategy file (`eval_run_<short>.json`) is present and parseable. Delegate the json-scan to an explore subagent so you don't read 5×6 files yourself.

A2. **Combined file ↔ per-strategy file consistency.** For seed1 only, verify that `eval_run_combined.json.strategies[X].summary_metrics` equals the contents of `seed1/eval_run_{short(X)}.json.summary_metrics` for at least 2 strategies. If they don't match, `merge_combined.py` has a bug.

A3. **Aggregate ↔ raw seeds.** Pull `tokens_completion_total` and `task_completion_rate` from each of the 5 seeds for each of the 5 strategies. Re-compute the means and confirm they match the headline table above to within 0.5 tokens / 0.001 completion. Aggregate code: `experiments/real_repos/starlette/aggregate.py`.

A4. **OOB count derivation.** For `naive_parallel_blind`, manually sum `summary_metrics.out_of_bounds_write_count` across the 5 seeds and confirm = 12. Then cross-check by counting `proposals[].scope_status == "out_of_bounds"` events in the `tasks[].proposals[]` arrays in each `seed*/eval_run_naive_parallel_blind.json`. If those don't match, the summary metric is mis-counting.

A5. **The 1/3 mystery.** For `naive_parallel_blind`, identify which exact task (`pr3148`, `pr3137`, or `pr3166`) succeeds in every seed and which two fail. Then explain why — most likely the failing tasks require touching multiple files whose paths the blind agent can't infer from the bare task prompt. Sample seed1's `eval_run_naive_parallel_blind.json` `tasks[].status` field and `failure_reason`.

A6. **NULL fields.** Identify every `summary_metrics` field that is NULL across all 5 seeds in any strategy. The expected NULLs are `cost_usd_total`, `cost_usd_per_completed_task`, `acus_consumed_total` (Anthropic direct doesn't return cost in the OpenAI-compatible usage block). Anything else NULL is a bug — flag it.

### B. Methodological validity

B1. **Single_agent IS producing apply_patch envelopes, not JSON.** In `seed1/eval_run_single_agent.json`, locate the system prompt that was sent (look for `apply_patch_suites=True`-derived language) OR locate the raw model reply in `tasks[].raw_reply` or `actual_changed_files`. Verify by inspection that single_agent's `actual_changed_files_kind` is `"suite_proposed_write_set"` AND that the per-task `actual_changed_files` arrays contain real Starlette paths (e.g., `starlette/templating.py`, `starlette/middleware/cors.py`), not generic placeholders. If you see `actual_changed_files == []` for any task that single_agent claims to have completed, the parser missed envelopes — fatal bug.

B2. **Naive_parallel_blind IS blind.** The whole baseline depends on this. Inspect `_run_naive_parallel_blind` in `experiments/greenhouse/strategies.py` and confirm it calls `run_worker(..., include_lockfile_hints=False)`. Then have an explore subagent confirm that when `include_lockfile_hints=False`, the worker prompt in `acg/runtime.py` (or wherever `run_worker` builds prompts) does NOT include `predicted_writes` or `candidate_context_paths` strings. There's a test at `tests/test_greenhouse_eval.py::test_naive_parallel_blind_dispatch_uses_blind_prompt` — confirm it passes and that it actually asserts the right thing.

B3. **Validator IS running for `naive_parallel_blind`.** The OOB count is 12 only if the validator runs against blind proposals. Confirm `_run_naive_parallel_blind` still calls into the same `validate_write` path inside `run_worker` (no `include_lockfile_hints=False` short-circuit on validation). If the validator is disabled for the blind path, the OOB number is the agent's *own claim* about what it tried to write, not a validator observation — that weakens the safety claim. Cite the line in `acg/runtime.py` that proves validation ran.

B4. **Identical inputs across strategies.** All 5 strategies should be running against the same `agent_lock_combined.json`, `tasks_combined.json`, and `repo_graph` (`experiments/real_repos/starlette/checkout/.acg/context_graph.json`). Confirm `seed1/eval_run_*.json` all show identical `lockfile`, `repo.commit`, and `tasks_total=3`. If any strategy was run against a different lockfile or commit, the comparison is invalid.

B5. **No leakage from last night's run into today's data.** Inspect `mtime` on each `seed*/eval_run_*.json` (use `ls -la` via Shell). `naive_parallel`, `acg_planned`, `acg_planned_full_context` should be ~`May 11 23:56-00:14`; `naive_parallel_blind` and `single_agent` should be ~`May 12 10:30-10:41`. Anything else is wrong.

### C. Code change correctness

C1. **`top_up` strategy group is reachable.** Run `./.venv/bin/python -c "from experiments.greenhouse.headtohead import STRATEGY_GROUPS; print(STRATEGY_GROUPS['top_up'])"` and confirm it returns `['single_agent', 'naive_parallel_blind']`.

C2. **`ACG_SINGLE_AGENT_APPLY_PATCH=1` actually flips the prompt.** Have an explore subagent diff the two system-prompt branches in `_build_single_agent_prompt` (`apply_patch_suites=True` vs `False`). Confirm the env-var read in `_run_single_agent` is `os.environ.get("ACG_SINGLE_AGENT_APPLY_PATCH", "0") == "1"` — case-sensitive, no whitespace, and that env-var=`"0"` falls through to the legacy JSON path (so we haven't accidentally permanently switched the default).

C3. **`comparison_full` strategy group works for fresh runs.** Confirm `STRATEGY_GROUPS["comparison_full"]` exists and contains all 5 strategies (this is what we'll use for new repos). Verify the dispatcher in `experiments/greenhouse/strategies.py::run_strategy` handles all 5 entries (the dispatcher uses `if strategy == X: ... elif strategy == Y: ...` chains — confirm there's no missing branch).

C4. **`merge_combined.py` handles the no-old-file case.** Run `./.venv/bin/python experiments/real_repos/starlette/merge_combined.py --seeds 1 --base-dir /tmp/nonexistent` and confirm it exits non-zero with a clear error message, not a stack trace. This matters because future top-up users will run it before the run finishes.

C5. **Pytest still green for the touched paths.** Run `./.venv/bin/python -m pytest tests/test_greenhouse_eval.py -q -m 'not smoke'` and confirm all tests pass. Specifically watch for `test_naive_parallel_blind_dispatch_uses_blind_prompt` and any test mentioning `_build_single_agent_prompt`, `_parse_single_agent_applied_envelopes`, or `apply_patch_suites`. If any fail, surface the failure.

C6. **Lint clean on touched files.** Run `./.venv/bin/ruff check experiments/greenhouse/headtohead.py experiments/greenhouse/strategies.py experiments/real_repos/starlette/aggregate.py experiments/real_repos/starlette/merge_combined.py` — should be 0 errors. Same for `ruff format --check`.

### D. Paper-claim verification

D1. **"19.5 % completion-token reduction (ACG-full vs naive)" — arithmetic.** Compute `(12513 - 10076) / 12513` and confirm `≈ 0.1948`. Then confirm by re-computing means from the raw seed data (do not trust the headline table — recompute).

D2. **"22 % reduction (ACG-scoped vs naive)" — arithmetic.** Same exercise: `(12513 - 9769) / 12513` should be `≈ 0.2193`.

D3. **"12 OOB writes is a 60+ pp completion gap" claim.** Verify completion(`naive_parallel_blind`) = 0.333 and completion(`naive_parallel`) = 0.933, so the gap = 60.0 pp. The claim "lockfile-aware multi-agent ≥ blind by 60+ pp" should hold.

D4. **Cost computation (manual).** Compute `(tokens_prompt × $3 + tokens_completion × $15) / 1,000,000` for each strategy and produce a $/seed-mean number. Cross-check that ACG full-context cost ≈ $0.155 and naive ≈ $0.191. Cite the per-seed cells in your math.

D5. **Wall-time variance for `single_agent`.** stdev = 35.7s on a mean of 133.9s is high (27 %). Look at the raw seed values (108, 132, 107, 128, 195s) — seed5 is the outlier. Skim seed5's `eval_run_single_agent.json` and explain why (likely a longer apply_patch envelope; check `tokens_completion_total` and `proposal_write_count`). Decide whether this matters for the paper claim or if it's just one slow run.

D6. **What happens if cost columns are NULL.** Confirm that no part of the paper-numbers narrative depends on the `cost_usd_total` field being non-null. If it does, flag it — we need to compute cost externally for Anthropic-direct runs.

### E. Pre-flight before scaling

E1. **Will `comparison_full` work cleanly on a fresh repo?** Identify any bookkeeping that assumes the run output dir is empty (or has only specific files). Check `_outputs_for_strategies` in `headtohead.py` — does it overwrite existing files silently, or warn? For future runs on brocoders / fastify / a fresh starlette branch, this is the entry point.

E2. **Cost ceiling for next round.** Project cost for a `comparison_full` run on a 6-task repo × 5 seeds × Sonnet 4.6 using mean tokens from this run. Don't undershoot — use the upper-90th-percentile of the token observations.

E3. **Failure-mode coverage.** Did anything in this round get a `failure_reason = TRUNCATED_BY_MAX_TOKENS`? Grep the run logs and eval_run JSONs. If 0 truncations, we're fine. If any, identify which strategy/task/seed and whether `ACG_WORKER_MAX_TOKENS` (default 8192) needs to be raised before next round.

E4. **Single-agent vs `single_agent_applied`.** There's a sibling function `_run_single_agent_applied` for applied-diff-live mode. Confirm it also uses `apply_patch_suites=True` by default (so the apply-time path doesn't regress to the JSON format). One grep on `_build_single_agent_prompt` should answer this.

E5. **Run-trace events for the blind safety story.** For seed1's `naive_parallel_blind`, list every `ALLOWED` and `BLOCKED` event from `seed1/run_attempt1.log` and confirm the count of blocked + out-of-bounds events matches `summary_metrics.out_of_bounds_write_count` for that strategy.

## What I want back from you

A single response with three sections:

1. **VERDICT** — one of `SHIP IT`, `SHIP WITH CAVEATS`, `DO NOT SHIP` — with one sentence justifying.

2. **CHECKLIST RESULTS** — one line per audit item (A1…E5), with `PASS` / `FAIL` / `SKIPPED-WHY`. For every FAIL, cite the file:line that proves the failure and propose the fix.

3. **OPEN QUESTIONS** — anything you couldn't decide from the data alone. Prefer these be 1-2 sentences each so I can resolve them quickly.

Don't write a long narrative. The data is in the files; you're verifying, not summarizing.

Be ruthless. Better to find a defect now than after we burn $50 on a 5-repo round.
