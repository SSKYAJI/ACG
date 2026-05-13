# Codex Task — Audit marshmallow pr2937 5-seed result (READ-ONLY)

**For: GPT-5.5 (or equivalent extended-thinking model). Use GPT-5.4-mini (or any cheap model) for sub-agent file traversal and JSON extraction; reserve the full reasoning model for synthesis.**

**Working dir: `/Users/prajit/Desktop/projects/cognition`** (git repo on `main`, remote `https://github.com/SSKYAJI/ACG.git`).

**Hard constraints:**
- **READ-ONLY.** Do not modify any existing file. Do not run `git add`, `git commit`, `git push`, `git reset`, `git checkout` (anything that changes branch state), `mv`, `rm`, or any in-place edit.
- **You MAY write exactly one new file:** `experiments/real_repos/MARSHMALLOW_PR2937_AUDIT.md`. Nothing else.
- Do NOT kill the running `multi_seed_sonnet_pr2937.sh` process or its child `headtohead.py` workers. They're producing seeds 2-5 right now.
- Do NOT touch the `checkout/` directories (gitignored upstream repos).
- Do NOT modify `acg/correctness.py`, `acg/runtime.py`, `acg/orchestrator.py`, `acg/compiler.py`, `acg/schema.py`, `experiments/greenhouse/strategies.py`, `experiments/greenhouse/headtohead.py`, `experiments/real_repos/_parsers.py`, `experiments/real_repos/compute_fail_to_pass.py`. Even read-only inspection is the goal — no edits.
- DO NOT call any GitHub write API (`gh pr create`, `gh issue ...`).

---

## 1. Why we're auditing

We just got the seed1 result of `experiments/real_repos/marshmallow/multi_seed_sonnet_pr2937.sh` (a per-PR 5-seed run with checkout-local venv bootstrap):

```
acg_planned             cupp=1.00 OOB=0 ftp=10/10
acg_planned_full_context cupp=1.00 OOB=0 ftp=10/10
naive_parallel          cupp=0.00 OOB=0 ftp=7/10
naive_parallel_blind    cupp=0.00 OOB=0 ftp=7/10
single_agent            cupp=0.00 OOB=0 ftp=7/10
```

**cupp = 1.00 on a real OSS bug-fix PR is exceptional.** Sonnet 4.6's solve rate on similar PRs (Starlette pr3148-jinja2-autoescape) is around 0.40. Hitting 1.00 on the very first seed of a single PR while every baseline gets stuck at ftp=7/10 is the kind of result that gets a paper rejected if reviewers find a defect.

**We need to verify this is real, not a measurement artifact.** Specifically we are worried about:

1. **Did ACG actually emit a non-empty patch that modified the right files?** The earlier (broken-venv) canary scored `cupp=1.0` for ACG with `actual_changed_files=[]` and `failure_reason=EMPTY_PATCH`. We need to confirm the seed1 ACG patch actually contains the validate.Email IDN fix.

2. **Did tests actually run?** The earlier canary's "passed" was `test_command_not_found`. The new script bootstraps a venv via `ensure_test_env()` in `multi_seed_sonnet_pr2937.sh`; verify the bootstrap worked AND that pytest actually executed.

3. **Is the venv really isolated?** The earlier marshmallow canary had `.venv/bin/python -> /opt/homebrew/anaconda3/bin/python3` and imported marshmallow from anaconda site-packages instead of the checkout source. Verify the new bootstrap produces a checkout-local `.venv` that imports marshmallow from `experiments/real_repos/marshmallow/checkout/src/marshmallow/`.

4. **Why is the gap so large?** ACG gets 10/10 FTP and naive gets 7/10 FTP. What did ACG do that naive didn't? Is there a legitimate localization difference, or is one of them being tested against a different test set / different source state?

5. **Did `naive_parallel_blind` really emit 0 OOB writes on seed1?** Every other repo we tested has blind producing positive OOB attempts. Zero on marshmallow seed1 is suspicious — either the blind agent uniquely behaved well, or the OOB-counting plumbing missed events. Compare to the marshmallow Round 2 (combined-lock) blind OOB attempt counts (~7/seed).

6. **Pass-to-pass status:** the cupp=1.00 also requires zero PTP regressions. The agent's patch may have broken something that wasn't in the FTP set. Verify the eval_run JSON's PTP pass count.

---

## 2. Where to look (key file paths)

### Result files for seed1 (the one we're auditing)
- `experiments/real_repos/marshmallow/runs_sonnet_test_gate_pr2937_n5/seed1/eval_run_acg.json`
- `experiments/real_repos/marshmallow/runs_sonnet_test_gate_pr2937_n5/seed1/eval_run_acg_full_context.json`
- `experiments/real_repos/marshmallow/runs_sonnet_test_gate_pr2937_n5/seed1/eval_run_naive.json`
- `experiments/real_repos/marshmallow/runs_sonnet_test_gate_pr2937_n5/seed1/eval_run_naive_parallel_blind.json`
- `experiments/real_repos/marshmallow/runs_sonnet_test_gate_pr2937_n5/seed1/eval_run_single_agent.json`
- `experiments/real_repos/marshmallow/runs_sonnet_test_gate_pr2937_n5/seed1/eval_run_combined.json`
- `experiments/real_repos/marshmallow/runs_sonnet_test_gate_pr2937_n5/seed1/{acg_planned,acg_planned_full_context,naive_parallel,naive_parallel_blind,single_agent}_raw/` (raw worker outputs, prompts, diffs)
- `experiments/real_repos/marshmallow/runs_sonnet_test_gate_pr2937_n5/seed1/run_attempt1.log`
- `experiments/real_repos/marshmallow/runs_sonnet_test_gate_pr2937_n5/launch.log`

### Reference: what the broken canary looked like
- `experiments/real_repos/marshmallow/runs_sonnet_test_gate_canary/seed1/eval_run_acg.json` (had cupp=1.0 with EMPTY_PATCH — the artifact we want to NOT see in seed1 of the new run)

### Reference: the per-PR lock and task
- `experiments/real_repos/marshmallow/agent_lock_pr-2937.json` (allowed_paths, predicted_writes, repo.commit)
- `experiments/real_repos/marshmallow/tasks_canary.json` (the pr2937 prompt the agent sees)

### Reference: the test-bootstrap script
- `experiments/real_repos/marshmallow/multi_seed_sonnet_pr2937.sh` (read the `ensure_test_env` function)

### Reference: the historical PR's actual fix
- The marshmallow checkout's git history: `git -C experiments/real_repos/marshmallow/checkout log --oneline | head` (gitignored upstream)
- The PR #2937 merge commit and parent: parent is `4acb783c73130f762aa5b0df6b65ff7685d5ff2c`, merge SHA from manifest
- `experiments/real_repos/manifest.json` — find the marshmallow entry, look at the pr2937 task: `merge_commit_sha`, `ground_truth_files`, `fail_to_pass`, `pass_to_pass`
- The actual diff: `git -C experiments/real_repos/marshmallow/checkout show <merge_sha> -- src/marshmallow/validate.py tests/test_validate.py`

### Reference: where the venv lives and what it imports
- `experiments/real_repos/marshmallow/checkout/.venv/` (should be a checkout-local venv after bootstrap)
- Verify: `ls -la experiments/real_repos/marshmallow/checkout/.venv/bin/python` and `experiments/real_repos/marshmallow/checkout/.venv/bin/python -c "import marshmallow; print(marshmallow.__file__)"` — the import path MUST resolve to `experiments/real_repos/marshmallow/checkout/src/marshmallow/__init__.py`, NOT to anaconda or any global site-packages.

### Reference: handoff with all known bugs
- `HANDOFF_GPT55.md` — read §4 ("Critical Bugs Found and Fixed") to understand the bug surface
- `experiments/real_repos/CLICK_MARSHMALLOW_DIAGNOSIS.md` — the previous diagnosis you (Codex) wrote

---

## 3. Audit checklist (work through these in order)

### Step 1 — Confirm ACG actually wrote real code

Sub-task for a lightweight sub-agent: extract from `eval_run_acg.json`'s task entry:
- `actual_changed_files` (must be non-empty; should include `src/marshmallow/validate.py`)
- `failure_reason` (must NOT be `EMPTY_PATCH` or `BLOCKED_BY_SCOPE`)
- The agent's emitted diff (from the `acg_planned_raw/` directory's worker output JSON)

Spot-check: does the diff contain a real change to the `Email` validator that handles IDN domains? Find the relevant function in `src/marshmallow/validate.py` and compare against the historical PR #2937 fix. If the diff is empty or unrelated to IDN handling, the result is bogus.

### Step 2 — Confirm tests actually ran

From `eval_run_acg.json` task entry:
- `tests_ran` must be `true`
- `tests_skip_reason` must be absent or `null`
- `tests_exit_code` must be `0` (or whatever pytest reports for "all pass")
- `tests_collection_error` must be `false`
- `fail_to_pass_passed` should be `10` (or whatever the manifest's FTP list length is for pr2937)
- `fail_to_pass_total` should match `len(manifest.repos[marshmallow].tasks[pr2937].fail_to_pass)`
- `pass_to_pass_passed` and `pass_to_pass_total` should show full PTP pass with no regression (PTP total ≈ 197 per the manifest)

If `tests_ran=false` or `tests_skip_reason=test_command_not_found` or `tests_collection_error=true`, the cupp=1.00 is fake.

### Step 3 — Confirm the venv is isolated

Use a lightweight sub-agent to:
```bash
file experiments/real_repos/marshmallow/checkout/.venv/bin/python
ls -la experiments/real_repos/marshmallow/checkout/.venv/bin/python
experiments/real_repos/marshmallow/checkout/.venv/bin/python -c "import sys; print(sys.executable); print(sys.path)"
experiments/real_repos/marshmallow/checkout/.venv/bin/python -c "import marshmallow; print(marshmallow.__file__); print(marshmallow.__version__)"
```

Required:
- `python` is a real interpreter binary in the checkout-local `.venv`, NOT a symlink to anaconda
- `marshmallow.__file__` resolves to `experiments/real_repos/marshmallow/checkout/src/marshmallow/__init__.py`, NOT `/opt/homebrew/anaconda3/...`
- `sys.path` starts with the checkout's `src/`, not a global site-packages

If any of these fail, the result is bogus.

### Step 4 — Compare ACG's diff vs naive's diff

Both ACG and naive emit diffs that get applied. ACG passes FTP 10/10 and naive only 7/10. What's different?

Extract from `*_raw/` directories the worker outputs for ACG and naive on pr2937. Compare:
- File set touched
- Specific lines changed
- Whether ACG modified additional helper functions naive missed
- Whether ACG's diff is materially different or just better-shaped

A legitimate result: ACG's localized prompt (lock-aware) pointed it at the right area of `validate.py`; naive went broader and missed a subtle detail. A red flag: ACG's diff is identical to naive's but tests behave differently between runs.

### Step 5 — Investigate the blind=0 OOB anomaly

In the broken-venv canary (`runs_sonnet_test_gate_canary/seed1/eval_run_naive_parallel_blind.json`), blind had OOB=3. Why does the bootstrapped-venv seed1 show blind OOB=0?

Hypotheses to check:
- The bootstrapped venv changed something about the runtime environment that affects what blind chooses to write (unlikely — venv is for test execution, not prompt construction)
- The two runs used different prompts (check `tasks_canary.json` hasn't changed; check the lock's `allowed_paths`)
- The OOB-counting plumbing is buggy and missed events in the new run (check `_apply_writes_git_sync` log for "OOB" events in `run_attempt1.log` even though the eval_run JSON reports 0)
- The blind agent on this specific seed legitimately stayed in scope (look at `naive_parallel_blind_raw/` worker output — what files did it actually propose?)

Most likely: legitimate seed variance + bootstrapped venv didn't sweep OOB attempts that the broken-venv run was inflating. But verify.

### Step 6 — Cross-check seed2 if available

While you work, seeds 2-5 are running in background. If seed2 has landed by the time you finish steps 1-5, also check `runs_sonnet_test_gate_pr2937_n5/seed2/eval_run_acg.json`. Does it also show ACG cupp=1.00 / ftp=10/10?

- If yes: the seed1 result is real and reproducible. Audit verdict: PASS.
- If seed2 shows cupp=0 or different ftp counts: there's seed variance and the cupp=1.00 on seed1 may be a lucky draw. Don't retract, but flag the variance for the paper.

Do NOT wait for seeds 3-5 to finish. Just check whatever's there by the time steps 1-5 are done.

---

## 4. Deliverable

Write your findings to **exactly one new file**: `experiments/real_repos/MARSHMALLOW_PR2937_AUDIT.md`.

Structure:

```markdown
# Marshmallow pr2937 5-Seed Audit
Generated: <date>

## Verdict
ONE OF: PASS (result is real, paper can cite cupp=1.00) / FAIL (artifact, retract) / FLAG (real but variance/caveat to disclose)

## Evidence per step
### Step 1 — actual_changed_files non-empty: YES/NO
[evidence]
### Step 2 — tests ran cleanly: YES/NO
[evidence]
### Step 3 — venv isolated: YES/NO
[evidence: `marshmallow.__file__` path, sys.path snippet]
### Step 4 — ACG vs naive diff diff: <short summary>
[evidence]
### Step 5 — blind OOB=0 explanation
[hypothesis chosen + evidence]
### Step 6 — seed2 cross-check (if available)
[seed2 cupp + ftp or "not yet landed"]

## What the paper can claim if PASS
[explicit wording, 1-2 sentences, ready to drop into PAPER_NUMBERS_CLEAN.md §3]

## Caveats / threats to validity (for the paper's limitations section)
[2-3 bullets]
```

Keep the audit report under 500 lines. Be specific with file paths and line numbers.

---

## 5. Workflow

1. Use a lightweight sub-agent (GPT-5.4-mini or any cheap model) to extract structured data from eval_run JSONs and run the venv-isolation shell commands. These are mechanical tasks that don't need full reasoning capacity.

2. Use full reasoning capacity for:
   - Comparing ACG's diff against naive's diff (Step 4)
   - Deciding the verdict and caveats
   - Writing the audit report

3. Do NOT read raw eval_run JSON files yourself if they're large — delegate to a sub-agent and ask for the specific fields you need.

4. Do NOT delegate the venv check or diff comparison to sub-agents — those are critical and benefit from your stronger judgment.

5. When you're done, end with a one-line summary on stdout: `AUDIT VERDICT: <PASS|FAIL|FLAG> — see MARSHMALLOW_PR2937_AUDIT.md`.

---

## 6. What this audit isn't

You are NOT:
- Modifying any code or running new experiments beyond the shell commands in Step 3
- Validating the ACG productivity claim on other repos (this audit is scoped to marshmallow pr2937 only)
- Producing a fix for any bug you find (if you find a bug, just document it in the audit; do not patch)

If the audit verdict is FAIL, the next step (taken by a human, not you) will be to retract the marshmallow cupp claim and fall back to the 2-repo (starlette + zod) cupp story.

---

## END

The result we're auditing is the most consequential single data point in the paper if it holds. Be thorough but bounded. The marshmallow pr2937 5-seed run is still in flight (seeds 2-5 finishing over the next ~15 min); your audit doesn't need to wait for those, but check seed2 if it lands while you work.
