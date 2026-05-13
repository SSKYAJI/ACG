# Response to audit (round 1)

## Defects addressed

### B1 — single_agent parser (FATAL → fixed)

- **Diagnosis:** The on-disk probe at `/tmp/sa-probe/eval_run_single_agent.json` **does not** exhibit the “empty `actual_changed_files` but `task_completion_rate: 1.0`” failure mode: all three tasks have non-empty `actual_changed_files` (e.g. `tests/test_templating.py`), `proposal_status` is `ok`, and `tasks[0].artifacts.raw_reply` (truncated to 8192 chars in JSON) clearly contains natural-language lead-in plus `Task id:` lines and `*** Begin Patch` / `*** End Patch` blocks. The embedded `model.url` is `https://api.anthropic.com/v1` with `claude-sonnet-4-6` in that file—so this artifact is **not** evidence of the misrouted Kimi hypothesis for that path on disk. For the **historical** audited failure on `runs_RETRACTED_kimi_n5_applied_8K_truncated_v1_audited`, the best-effort root cause (without a stored raw body in those JSONs) is twofold: (1) **format ambiguity** — `_build_single_agent_prompt` (`apply_patch_suites=True`, `strategies.py` ~1353–1370) explicitly offers **format A** (apply_patch + `Task id:`) *or* **format B** (legacy JSON `{"tasks":[...]}`), while the old `apply_patch_mode` branch only ran the envelope parser first and **did not** fall back to `_parse_single_agent_task_writes` when envelopes were empty, so a JSON-only reply produced `{}` parses; (2) **strict section headers** — the prior `^Task id:\s*…$` style matcher missed common variants (markdown bold, leading fences) so sections could be dropped even when patches existed. **Uncertainty:** without `raw_reply` in the v1_audited artifacts, the relative weight of (1) vs (2) for that specific Sonnet run cannot be proven from JSON alone.

- **Fix:**
  - `experiments/greenhouse/strategies.py`: `_normalize_single_agent_apply_patch_text`, relaxed `_parse_single_agent_applied_sections` / mega path (~1415–1476); `_parse_single_agent_applied_envelopes` (~1468–1475); JSON fallback after empty envelope-derived `parsed` (~1650–1665); per-task `failed` + `UNPARSEABLE_APPLY_PATCH_ENVELOPE` + `PROPOSAL_UNPARSEABLE` when `apply_patch_mode` and zero writes (~1688–1727); `artifacts.raw_reply` snippet on index 0 (~1715–1719); `_persist_single_agent_raw_reply_files` writes `single_agent_raw/<task_id>.txt` + `suite_reply.txt` (~1478–1495); `run_strategy` passes `eval_dump_dir` into `_run_single_agent` (~2258–2266, `run_strategy` ~2200).
  - `experiments/greenhouse/headtohead.py`: `eval_dump_dir` = `--out-dir` or parent of `--out` for single-strategy runs (~515–518, ~383–396).
  - `experiments/greenhouse/strategies.py` `_build_single_agent_prompt`: stricter ASCII instructions for format A (~1353–1370).

- **Test:** `tests/test_single_agent_envelope.py` — `test_run_single_agent_apply_patch_empty_reply_fails` (empty `LLMReply` + `eval_dump_dir` asserts `status=="failed"`, `failure_reason==UNPARSEABLE_APPLY_PATCH_ENVELOPE`, on-disk `single_agent_raw/` files); existing tests cover malformed prose, well-formed envelopes, and parser edge cases.

- **Outstanding:** Models can still answer with prose before patches; recommend keeping `single_agent_raw/` + `artifacts.raw_reply` for any future triage. Re-run Starlette on Sonnet with `env -i` (no `.env`) before updating paper tables.

### E3 — max_tokens truncation (FATAL → fixed)

- **Fix:** `experiments/real_repos/starlette/multi_seed_sonnet.sh` (then still named `multi_seed_kimi.sh`; see `multi_seed_kimi.sh.audited_v1`) — default `ACG_WORKER_MAX_TOKENS` raised from **8192** to **16384** with an inline comment (lines ~78–80).

- **Why this is enough:** Audit noted every truncated worker run had `tokens_completion == 8192` exactly on `pr3166-session-middleware`; doubling the cap gives headroom for the largest envelopes without changing the global `acg/runtime.py` default.

### A6, C6, E1 — minor fixes

- **A6 (NULL fields):** Not re-scanned across all `runs_RETRACTED_kimi_n5_applied_8K_truncated_v1_audited` JSONs in this code-only session. Per the handoff, **expected** persistent NULLs for Anthropic-direct runs include cost totals where the OpenAI-compatible usage block lacks USD. Flag any *additional* NULL `summary_metrics` fields only after re-loading aggregate artifacts in a future audit pass.

- **C6 (lint):** `ruff check` and `ruff format --check` clean on `headtohead.py`, `strategies.py`, `aggregate.py`, `merge_combined.py`, and `tests/test_single_agent_envelope.py` (see Lint status). `merge_combined.py` also ships an optional `--strategies` allowlist for safer partial merges (see module docstring).

- **E1 (`comparison_full` bookkeeping):** `headtohead` resolves per-strategy output paths and calls `write_eval_run` — **existing files are overwritten silently** with no warning. Fresh repos should use an empty or dedicated `--out-dir` per run.

## NOT addressed in this round (deferred to next session, user instruction)

- **Phase 5** (re-run with corrected code on Sonnet) — **DEFERRED**, no LLM calls this session.
- **Phase 6** (regenerate `RESULTS.md` / `PAPER_NUMBERS.md` from corrected data) — **DEFERRED** until a successful re-run exists.

## To replay this audit cycle

1. `git diff` to inspect the code changes.
2. When ready to re-run, paste this command (**DO NOT** `source` / `. ./.env` — it overrides inline Anthropic vars):

   ```bash
   env -i HOME="$HOME" PATH="$PATH" \
       ACG_AUTO_CONFIRM=1 \
       ACG_STRATEGY=comparison_full \
       ACG_SINGLE_AGENT_APPLY_PATCH=1 \
       ACG_WORKER_MAX_TOKENS=16384 \
       ACG_LLM_URL=https://api.anthropic.com/v1 \
       ACG_LLM_API_KEY=<sk-ant-...> \
       ACG_LLM_MODEL=claude-sonnet-4-6 \
   bash experiments/real_repos/starlette/multi_seed_sonnet.sh \
   2>&1 | tee /tmp/starlette-rerun.log
   ```

3. After the re-run completes: `./.venv/bin/python experiments/real_repos/starlette/merge_combined.py` + `./.venv/bin/python experiments/real_repos/starlette/aggregate.py` + manually update `runs_sonnet_v2_n5/RESULTS.md` and `experiments/PAPER_NUMBERS.md` (archive superseded numbers under `runs_RETRACTED_kimi_n5_applied_8K_truncated_v1_audited/RESULTS.md` as retracted-but-archived).

## Pytest status

```
....................................................................     [100%]
68 passed in 2.53s
```

Command: `./.venv/bin/python -m pytest tests/test_single_agent_envelope.py tests/test_greenhouse_eval.py -q -m 'not smoke'`

## Lint status

```
1 file reformatted, 4 files left unchanged
All checks passed!
5 files already formatted
```

Commands: `./.venv/bin/ruff format experiments/greenhouse/headtohead.py experiments/greenhouse/strategies.py experiments/real_repos/starlette/aggregate.py experiments/real_repos/starlette/merge_combined.py tests/test_single_agent_envelope.py` then `./.venv/bin/ruff check` on the same paths, then `./.venv/bin/ruff format --check` on the same paths.
