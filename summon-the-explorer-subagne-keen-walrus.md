# Plan: V8 raw-persistence + Sonnet re-run prep (cheap-model deferred to final paper run)

## Context

Round 6 of the Starlette benchmark used Sonnet 4.6 via Anthropic-direct. The audit caught two fixed bugs (B1: silent envelope-parser failure; E3: 8K truncation) plus one open gap (V8: raw-replies persisted only for `single_agent`, not the four worker strategies).

**Pivot from previous draft:** User has Anthropic API budget. For now we re-run on Sonnet 4.6. The cheaper non-Anthropic models (DeepSeek V4 Flash, Qwen3-Coder-Next, MiniMax M2.5) are held for the final paper run. This simplifies things:

- **Drop:** reasoning-disable extras mechanism (`ACG_LLM_EXTRA_PARAMS_JSON`). Sonnet 4.6 via Anthropic-direct doesn't expose chain-of-thought leakage in its OpenAI-compatible response — no extras needed. Ship this when we switch to OpenRouter for the paper run.
- **Drop:** DeepSeek canary protocol. Sonnet was already validated by round-7 envelope tests (68 passing).
- **Keep:** V8 raw-persistence — still critical. Even with Sonnet, if any of the 4 worker strategies hits a format edge case, we'll be blind without raw-reply files.
- **Keep:** uncap `ACG_*_MAX_TOKENS` (let Sonnet's 64K native ceiling apply).
- **Add:** explicit documented Anthropic-direct stanza in `.env.example` so the API key is easy to set.

## Why V8 (raw-persistence) still matters even on Sonnet

`_persist_single_agent_raw_reply_files` (strategies.py:1478-1496) writes the suite-level raw reply plus per-task chunks. After it shipped, any future single_agent parse miss is diagnosable by reading `<seed>/single_agent_raw/<task_id>.txt`.

The four worker strategies have no equivalent. `WorkerResult.raw_content` is already populated (`acg/runtime.py:597`) but the strategy-level callers (`_run_naive_parallel`, `_run_naive_parallel_blind`, `_run_acg_planned`, `_run_acg_planned_applied`) drop it on the floor. If a worker call ever produces unparseable output — even on Sonnet, even rarely — we'd repeat the original B1 debugging nightmare: `proposal_status: "unparseable"` with zero record of what the model actually said.

Cost: ~50 LOC in one file. Payback: every future "why did this task fail" question becomes a `cat` instead of a re-run.

---

## Workstream B — V8 raw-persistence extension

**Goal:** Persist `WorkerResult.raw_content` for all 4 non-single_agent strategies, mirroring the single_agent pattern.

**File to modify:** `experiments/greenhouse/strategies.py` (only file touched; `headtohead.py` plumbing at lines 517-521 → 548 → run_strategy:396 is already correct).

**Changes:**

1. Add new helper near `_persist_single_agent_raw_reply_files` (line 1478):

   ```python
   _WORKER_RAW_FILE_CAP = _SINGLE_AGENT_RAW_FILE_CAP  # reuse existing cap

   def _persist_worker_raw_replies(
       eval_dump_dir: Path | None,
       strategy_folder: str,
       worker_results: list[WorkerResult],
   ) -> dict[str, str]:
       """Write <eval_dump_dir>/<strategy_folder>/<task_id>.txt per worker result.

       No-op when eval_dump_dir is None or strategy_folder is empty.
       Returns task_id -> rel path.
       """
       if eval_dump_dir is None or not strategy_folder:
           return {}
       rd = eval_dump_dir / strategy_folder
       rd.mkdir(parents=True, exist_ok=True)
       rel: dict[str, str] = {}
       for wr in worker_results:
           body = (wr.raw_content or "")[:_WORKER_RAW_FILE_CAP]
           (rd / f"{wr.task_id}.txt").write_text(body, encoding="utf-8")
           rel[wr.task_id] = f"{strategy_folder}/{wr.task_id}.txt"
       return rel
   ```

2. Add `eval_dump_dir: Path | None = None` and `strategy_folder: str = ""` kwargs to each signature:
   - `_run_naive_parallel` (strategies.py:980)
   - `_run_naive_parallel_blind` (strategies.py:1044)
   - `_run_acg_planned` (strategies.py:1932)
   - `_run_acg_planned_applied` (strategies.py:2023)
   - Plus applied variants if present (`_run_naive_parallel_applied`, `_run_naive_parallel_blind_applied`) — verify via `rg "^async def _run_" experiments/greenhouse/strategies.py`.

3. Insert `_persist_worker_raw_replies(eval_dump_dir, strategy_folder, worker_results)` in each function right after `finished = now_iso()` and before eval-task aggregation. Specific insertion lines (per explore audit):
   - `_run_naive_parallel`: after line 1025
   - `_run_naive_parallel_blind`: after line 1085
   - `_run_acg_planned`: after line 2004
   - `_run_acg_planned_applied`: after line 2125

4. Wire `eval_dump_dir` + `strategy_folder` through `run_strategy` (strategies.py:2213). For each of the 7 non-single_agent dispatch branches:
   - Compute `folder = _short_name(strategy) + "_raw"`.
   - Pass `eval_dump_dir=eval_dump_dir, strategy_folder=folder` to the underlying `_run_*` call.
   - Branches to touch: `_run_single_agent_applied` (line 2279), `_run_naive_parallel` (2320), `_run_naive_parallel_applied` (2306), `_run_naive_parallel_blind` (2348), `_run_naive_parallel_blind_applied` (2334), `_run_acg_planned_applied` (2361), `_run_acg_planned` (2378).

5. Test: extend `tests/test_greenhouse_eval.py` with `test_naive_parallel_persists_raw_replies`:
   - Build a `WorkerResult` fixture with non-empty `raw_content`.
   - Invoke `_run_naive_parallel` with `eval_dump_dir=tmp, strategy_folder="naive_parallel_raw"`.
   - Assert `(tmp / "naive_parallel_raw" / task_id + ".txt").exists()` and body matches.
   - Mirror the pattern from `tests/test_single_agent_envelope.py:157-181`.

**Expected LOC:** ~55 lines, one file.

**Acceptance:** After any strategy runs with `--out-dir`, the seed dir has `<strategy_short>_raw/<task_id>.txt` files alongside `eval_run_<strategy_short>.json`.

---

## Workstream C — Sonnet re-run script + `.env.example`

**Goal:** Patch the run script to use Sonnet via Anthropic-direct cleanly, uncap max_tokens, write to a new output dir, and document the Anthropic key in `.env.example`.

**Files:** `experiments/real_repos/starlette/multi_seed_sonnet.sh` (renamed from `multi_seed_kimi.sh`; frozen snapshot `multi_seed_kimi.sh.audited_v1`), `.env.example`.

**Changes:**

1. `multi_seed_sonnet.sh`:
   - Change `BASE_OUT` from `experiments/real_repos/starlette/runs_kimi_n5_applied` to `experiments/real_repos/starlette/runs_sonnet_v2_n5` (preserves the audited v1 under `runs_RETRACTED_*`).
   - **Remove** `export ACG_WORKER_MAX_TOKENS="${ACG_WORKER_MAX_TOKENS:-16384}"` entirely. Let Sonnet's 64K native ceiling apply. Add a one-line comment: `# ACG_WORKER_MAX_TOKENS deliberately unset; provider native max (Sonnet=64K) applies.`
   - Change default model: `export ACG_LLM_MODEL="${ACG_LLM_MODEL:-claude-sonnet-4-6}"`.
   - Change default URL: ensure `ACG_LLM_URL` defaults to `https://api.anthropic.com/v1` if unset (current logic reads from `.env`; add a defensive `export ACG_LLM_URL="${ACG_LLM_URL:-https://api.anthropic.com/v1}"`).
   - Bump `--strategy` default to `comparison_full` (was `comparison`): `--strategy "${ACG_STRATEGY:-comparison_full}"`.
   - Change `--suite-name` to `starlette-sonnet-v2-n5`.
   - Update the header comment block to reflect Sonnet, not Kimi.

2. `.env.example` — add a clearly delimited Anthropic-direct stanza near the top of the LLM section:

   ```bash
   # ============================================
   # Anthropic-direct (Claude Sonnet 4.6 / Opus 4.7)
   # ============================================
   # The harness uses an OpenAI-compatible client; Anthropic's /v1 endpoint
   # accepts the same request shape. The API key goes in ACG_LLM_API_KEY.
   #
   # ACG_LLM_URL=https://api.anthropic.com/v1
   # ACG_LLM_API_KEY=sk-ant-api03-...
   # ACG_LLM_MODEL=claude-sonnet-4-6
   #
   # Note: Anthropic-direct does NOT return USD cost in the usage block —
   # cost_usd_total, cost_usd_per_completed_task, acus_consumed_total will
   # be NULL in eval_run_*.json. Compute cost externally:
   #   $ = (tokens_prompt * 3 + tokens_completion * 15) / 1_000_000  # Sonnet 4.6
   ```

   Also add a comment near the max_tokens vars: `# Leave unset for provider native max; setting above the model's hard ceiling returns 400.`

3. `aggregate.py` — verify `STRATEGIES_DEFAULT` includes all 5 (per round-7 audit it should). If not, add `naive_parallel_blind`.

**Expected LOC:** ~10 lines edited in `multi_seed_sonnet.sh` + ~15 lines added to `.env.example`.

**Acceptance:** `bash experiments/real_repos/starlette/multi_seed_sonnet.sh --dry-run` echoes Anthropic URL + `claude-sonnet-4-6` model + the new `runs_sonnet_v2_n5` output path; no `ACG_WORKER_MAX_TOKENS` export shows.

---

## Cursor multitask hand-off

When dispatching to Cursor:

- **Workstreams B and C are independent** — spawn 2 parallel subagents, one per workstream. They touch disjoint files (`strategies.py` vs `multi_seed_sonnet.sh` + `.env.example`).
- Each subagent should report back to the user with: file:line of every change, test output, lint status. The user forwards these to me for a quick verify pass before the actual re-run.
- **Do NOT have a subagent run the Sonnet re-run.** The user pastes the `env -i ...` command into their own terminal — the previous round's failures came from auto-runs.

## Critical files (paths only)

- `experiments/greenhouse/strategies.py` (Workstream B) — V8 raw-persistence: new helper + 4 signature edits + 4 call insertions + 7 dispatcher wirings.
- `experiments/real_repos/starlette/multi_seed_sonnet.sh` (Workstream C) — output dir, model default, uncap max_tokens, comparison_full.
- `.env.example` (Workstream C) — Anthropic-direct stanza + max_tokens caveat.
- `tests/test_greenhouse_eval.py` (Workstream B) — add `test_naive_parallel_persists_raw_replies`.

## Existing functions to reuse

- `_persist_single_agent_raw_reply_files` (strategies.py:1478) — template for the new helper.
- `_short_name` (headtohead.py:345-354) — strategy → folder-prefix mapping.
- `WorkerResult.raw_content` (runtime.py:597) — already populated, just needs to be written.
- Existing single-agent test pattern in `tests/test_single_agent_envelope.py:157-181` — template for the V8 persistence test.

## Verification (end-to-end)

After B + C land and before the Sonnet re-run:

1. Lint: `./.venv/bin/ruff check acg/ experiments/ tests/` → 0 errors.
2. Format: `./.venv/bin/ruff format --check acg/ experiments/ tests/` → 0 diffs.
3. Tests: `./.venv/bin/python -m pytest tests/ -q -m 'not smoke'` → all pass.
4. Dry-run: `bash experiments/real_repos/starlette/multi_seed_sonnet.sh --dry-run` shows Anthropic URL + Sonnet model + `runs_sonnet_v2_n5/` output path + no max_tokens export.

User-side (paste into terminal, not delegated to any subagent):

5. Update `.env` with the real Anthropic key.
6. Run the benchmark:
   ```bash
   env -i HOME="$HOME" PATH="$PATH" \
       ACG_AUTO_CONFIRM=1 \
       ACG_STRATEGY=comparison_full \
       ACG_SINGLE_AGENT_APPLY_PATCH=1 \
       ACG_LLM_URL=https://api.anthropic.com/v1 \
       ACG_LLM_API_KEY=<sk-ant-...> \
       ACG_LLM_MODEL=claude-sonnet-4-6 \
   bash experiments/real_repos/starlette/multi_seed_sonnet.sh \
   2>&1 | tee /tmp/starlette-sonnet-v2.log
   ```

7. Spot-check raw replies after seed 1 finishes:
   ```bash
   for d in single_agent_raw naive_parallel_raw naive_parallel_blind_raw acg_planned_raw acg_full_context_raw; do
     ls experiments/real_repos/starlette/runs_sonnet_v2_n5/seed1/$d/ 2>/dev/null
   done
   ```
   Confirm each strategy has 3 `<task_id>.txt` files with non-empty content.

8. Aggregate after all 5 seeds: `merge_combined.py` + `aggregate.py` against the new `runs_sonnet_v2_n5/` dir.

9. Compare `runs_sonnet_v2_n5/` vs `runs_RETRACTED_kimi_n5_applied_8K_truncated_v1_audited/` (archived pre-reorg v1) for stability — same lockfile, same commit, same model, so headline numbers should reproduce ±noise. If pr3166 stops truncating, that's the E3 fix confirmed end-to-end.

## Deferred to final paper run

When ready to swap in cheap models for the final paper sweep:
- Add the `ACG_LLM_EXTRA_PARAMS_JSON` reasoning-disable mechanism to `acg/runtime.py` (~30 LOC, deferred from this round).
- Run the canary protocol on DeepSeek V4 Flash, fall back to Qwen3-Coder-Next if format compliance fails.
- Write results to `runs_deepseek_n5/` to keep paper comparisons clean.
