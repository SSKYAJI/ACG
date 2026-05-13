# Codex Task — Diagnose & Fix Click + Marshmallow Round 2 Discrepancy

**For: GPT-5.5 (with extended thinking)**
**Working dir: `/Users/prajit/Desktop/projects/cognition`** — a git repo on `main`, remote `https://github.com/SSKYAJI/ACG.git`.
**Branch policy: stay on `main`, do NOT push, do NOT amend existing commits.**

Read this whole document before doing anything. Then think carefully before editing.

---

## 1. Project context (one paragraph)

**ACG (Agent Context Graph)** compiles a per-task write contract (`agent_lock.json`) constraining where an LLM coding agent (Sonnet 4.6) is allowed to write while fixing a real-OSS bug. The headline experiment runs **5 strategies** against each PR — `acg_planned`, `acg_planned_full_context`, `naive_parallel`, `naive_parallel_blind` (no lock awareness in prompt, primary safety adversary), `single_agent` — and scores each via **CuPP** (Correct-under-Patch-Property): diff must pass `FAIL_TO_PASS` tests AND `PASS_TO_PASS` tests AND have zero out-of-bounds (OOB) writes outside the lock's `allowed_paths`.

Working setup details are in `HANDOFF_GPT55.md` (read §4 "Critical Bugs Found and Fixed" before assuming anything).

---

## 2. The bug

Both `click` and `marshmallow` show a **canary-vs-Round-2 discrepancy** that I can't explain from configuration alone:

### Click

- **Canary** (`experiments/real_repos/click/runs_sonnet_test_gate_canary/seed1/`):
  - lock: `agent_lock_pr-2933.json` (single-PR lock, 1 task: `pr2933-clirunner-stderr-flush`)
  - tasks: `tasks_canary.json` (single task)
  - Result: `acg=1.00`, `acg_full_context=1.00`, `naive=1.00`, `naive_parallel_blind=0.00 (OOB=6, unresolved_unsafe=1.00)`, `single_agent=1.00`
- **Round 2** (`experiments/real_repos/click/runs_sonnet_test_gate_n5/seed{1,2}/`):
  - lock: `agent_lock_combined.json` (combined lock, 3 tasks: `pr3126-fish-completion-multiline`, `pr2933-clirunner-stderr-flush`, `pr3363-unprocessed-flag-value`)
  - tasks: `tasks_combined.json` (3 tasks)
  - Result: **`acg=0.00` (chg=5 seed1, chg=3 seed2)**, `acg_full_context=0.00`, `naive=0.00`, `naive_parallel_blind=0.00 (OOB=7-8/seed)`, `single_agent=0.00`

The canary PR (`pr2933`) is ALSO in the combined run, but its row contributes 0/1 resolved in Round 2.

### Marshmallow

- **Canary** (`experiments/real_repos/marshmallow/runs_sonnet_test_gate_canary/seed1/`):
  - lock: `agent_lock_pr-2937.json` (single task: `pr2937-email-idn`)
  - tasks: `tasks_canary.json` (single task)
  - Result: `acg=1.00`, `acg_full_context=0.00`, `naive=1.00`, `naive_parallel_blind=0.00 (OOB=3, **resolved_unsafe=1.00**)`, `single_agent=1.00`
- **Round 2** (`experiments/real_repos/marshmallow/runs_sonnet_test_gate_n5/seed{1,2}/`):
  - lock: `agent_lock_combined.json` (3 tasks: `pr2937-email-idn`, `pr2901-constant-required`, `pr2902-enum-by-name`)
  - tasks: `tasks_combined.json`
  - Result: **`acg=0.00`**, `acg_full_context=0.00`, `naive=0.00`, `naive_parallel_blind=0.00 (OOB=7/seed, unresolved_unsafe=0.67)`, `single_agent=0.00`

### Zod control (works correctly)

For comparison, zod's Round 2 (same combined-lock pattern, 3 PRs) **does** produce `acg=0.33` reliably across 4 seeds — matching its canary signal. So the combined-run mechanism is not categorically broken — something specific to click and marshmallow is wrong.

### Round 2 is still running in background

Three background processes:
- `bash experiments/real_repos/{zod,click,marshmallow}/multi_seed_sonnet.sh -y`
- Do NOT kill them. Do NOT touch the `runs_sonnet_test_gate_n5/` output directories while they're running.
- Verify they're still going: `ps aux | grep multi_seed_sonnet | grep -v grep` (expect 3 procs).

---

## 3. The hypotheses to investigate (in order of likelihood)

### H1: State pollution between PRs within a seed run

In combined runs, the runtime fans 3 tasks out (per `lock.execution_plan.groups`). Each task applies a diff to the same `checkout/` directory. If the runtime doesn't reset the checkout between tasks (or runs them in `parallel_group` and they race), pr2933 may see pr3126's or pr3363's diff still applied when its tests run — corrupting its results.

**To investigate**:
1. Read `experiments/real_repos/click/agent_lock_combined.json` — look at `execution_plan.groups`. Is it one group with all 3 tasks (parallel), or 3 separate groups (sequential)?
2. Compare with `experiments/real_repos/zod/agent_lock_combined.json` — what does zod's execution_plan look like? (zod works correctly.)
3. Read `experiments/greenhouse/strategies.py` — find where the checkout is reset between tasks/strategies. Specifically look at `_apply_writes_git_sync` (around lines 879–900 per the handoff — **DO NOT MODIFY THIS FUNCTION** per handoff §9 rule 5).
4. Read `experiments/greenhouse/headtohead.py` — understand how it iterates tasks and what state is preserved.

### H2: Wrong `repo.commit` parent_sha — combined lock at oldest PR's parent

The combined lock is pinned to the OLDEST PR's parent_sha (per handoff Bug 8). For click, that's `b7cf06970e40` = pr2933's parent. So when pr3126 and pr3363 run, the source isn't at THEIR parent_sha — meaning *those PRs' bugs may not be present* (the source is at an earlier state), and the agent's "fix" produces no measurable effect or breaks tests written against the later state.

**Verify**:
```bash
git -C experiments/real_repos/click/checkout log --oneline b7cf06970e40..c8da1fcc2cb4 | head -20
# c8da1fcc2cb4 = pr3363's parent. Any of those commits include the fix files?
```

If pr3363's tests reference functions added between b7cf06970e40 and c8da1fcc2cb4, those tests will error at module-import time → cupp=0.

### H3: Combined lock has stale or missing `predicted_writes`

Some per-PR locks (`agent_lock_pr-3126.json`, `agent_lock_pr-3363.json`, `agent_lock_pr-2901.json`, `agent_lock_pr-2902.json`) contain `None` entries in `predicted_writes`:

```bash
./.venv/bin/python - <<'PY'
import json
from pathlib import Path
for lf in sorted(Path('experiments/real_repos/click').glob('agent_lock_pr-*.json')) + \
          sorted(Path('experiments/real_repos/marshmallow').glob('agent_lock_pr-*.json')):
    try:
        d = json.loads(lf.read_text())
        for t in d.get('tasks', []):
            pw = t.get('predicted_writes') or []
            nones = [i for i,p in enumerate(pw) if p is None]
            if nones:
                print(f"{lf}: task {t.get('id')} has None entries at indices {nones}")
    except Exception as e:
        print(f"{lf}: ERR {e}")
PY
```

The **combined** locks have these fields filled in correctly (no None) — Round 2 uses the combined lock — but verify this is still true. If the runtime falls back to per-PR locks for any reason, that path is broken.

### H4: Task-prompt drift between canary and Round 2

Compare:
- `experiments/real_repos/click/tasks_canary.json` (1 task — pr2933 prompt)
- `experiments/real_repos/click/tasks_combined.json` task with `id="pr2933-clirunner-stderr-flush"` (same task, but other tasks alongside it)

If the prompts diverge (e.g., the canary's pr2933 prompt has a leak that the combined version corrected, or vice versa), that's the difference.

### H5: Test-runner contamination (most boring, most likely)

For Python repos, pytest may cache module state between PR test runs in one seed. The combined run executes 3 PRs in sequence, each calling `pytest` against the same checkout's `tests/` dir. Module-level state, `conftest.py` fixtures, or `__pycache__` could carry state from PR1's apply into PR2's test run.

**Check**: Read `acg/correctness.py` `_run_pr_tests_py` (and the JS variant `_run_pr_tests_js`). Does each invocation pass `--cache-clear` or set `PYTHONDONTWRITEBYTECODE=1`? Are temp dirs unique per task?

(Reminder per handoff §9 rule 7: do NOT modify `acg/correctness.py`. If the root cause is in there, surface the diagnosis and patch the test harness elsewhere or recommend a follow-up PR.)

---

## 4. What to do

**Step 1 — Diagnose without editing.** Run the investigation commands above. Note your findings in a file `experiments/real_repos/CLICK_MARSHMALLOW_DIAGNOSIS.md` (new file, untracked). Include:
- Which hypothesis (H1–H5, or new) explains the discrepancy
- Concrete evidence (file/line refs, log excerpts)
- Whether zod escapes the bug by accident (different test runner? different PR count? different parent_sha relationship?)

**Step 2 — Propose a minimal fix.** Constraints (from `HANDOFF_GPT55.md` §9):
- DO NOT modify: `acg/runtime.py`, `acg/orchestrator.py`, `acg/compiler.py`, `acg/schema.py`, `schema/agent_lock.schema.json`, `tests/test_runtime.py`, `tests/test_compile.py`, `tests/test_orchestrator.py`, `acg/correctness.py`, `experiments/real_repos/_parsers.py`, `experiments/real_repos/compute_fail_to_pass.py`, and the parser tests.
- ALLOWED to modify: the per-repo `agent_lock_combined.json` / `tasks_combined.json` / `multi_seed_sonnet.sh` files for click and marshmallow; new helper scripts under `experiments/real_repos/<repo>/`; aggregation scripts under `experiments/greenhouse/` IF strictly necessary.
- If the right fix is to **run only the validated PR** (per-PR lock pinned to that PR's parent_sha, single-task tasks file), that's an acceptable pragmatic fix — write a new `multi_seed_sonnet_pr<N>.sh` per repo and document it.

**Step 3 — Validate the fix.** Re-run a 1-seed canary using your proposed config:
```bash
set -a && . ./.env && set +a
export ACG_SEED=99
./.venv/bin/python -m experiments.greenhouse.headtohead \
  --lock <YOUR_FIXED_LOCK> \
  --tasks <YOUR_FIXED_TASKS> \
  --repo experiments/real_repos/<repo>/checkout \
  --backend local --strategy comparison_full \
  --applied-diff-live \
  --out-dir experiments/real_repos/<repo>/runs_sonnet_test_gate_validation/seed99 \
  --suite-name <repo>-validation
```
Confirm `acg cupp ≥ canary acg cupp` for the validated PR. If yes, the fix is good.

**Step 4 — Write up.** Update `experiments/real_repos/CLICK_MARSHMALLOW_DIAGNOSIS.md` with: root cause, fix, validation cupp data, recommended path forward for the paper.

**Do NOT commit anything.** The user runs commits and pushes.

---

## 5. Hard safety rules (do not violate)

1. **NEVER push to remote.** `git push` is forbidden in this task.
2. **NEVER force-push / reset --hard / amend** existing commits on `main`.
3. **NEVER kill the running multi_seed_sonnet.sh processes.** They are producing useful safety-contract data even where cupp=0. Wait for them OR work in parallel with them.
4. **NEVER touch upstream `checkout/` git history.** Those are gitignored; even within them, never `git push` — though no script in the codebase invokes a write endpoint, keep it that way.
5. **NEVER commit `.env`** or any file containing `sk-ant-...`. The API key is in `.env`; treat it as untrusted (the user pasted it in chat earlier — it's compromised and should be rotated, but that's the user's job).
6. **Use `git add <specific paths>`**, never `git add .` or `git add -A`.
7. **Do not modify `acg/correctness.py`** even if you think it's the bug. Document findings instead.
8. **Do not trust subagent self-reports** — verify with `ls`, `git status`, `grep`, etc.
9. **Don't poll background tasks** with sleep loops. The harness notifies on completion. If you must wait, use `Monitor` (with `until <check>; do sleep 2; done`).

---

## 6. The end state I expect from you

Either:

**A. Diagnosis only** — you identified the root cause, documented it in `CLICK_MARSHMALLOW_DIAGNOSIS.md`, but the fix touches forbidden files. In that case, propose what the follow-up PR should change in `acg/correctness.py` or `acg/runtime.py` AND propose a workaround (e.g., per-PR-only runs) that achieves a clean result without touching those files. Validate the workaround.

**B. Fix + validation** — you found a fix that doesn't touch forbidden files, applied it (to lock/tasks/scripts only), and re-ran a 1-seed validation showing acg cupp ≥ canary cupp on the previously-failing PR.

In both cases, leave the running multi_seed processes alone. Your validation run goes into `runs_sonnet_test_gate_validation/` so it doesn't collide with Round 2's `runs_sonnet_test_gate_n5/`.

When you're done, the cross-repo paper's CuPP claim should have a clear path: either Round 2 data is salvageable (B), or per-PR validation runs are the correct comparison set (A) and the cross-repo aggregate documents the methodology shift.

---

## 7. Files to read first (in priority order)

1. `HANDOFF_GPT55.md` — full project context, all 10 known bugs, safety rules
2. `CLAUDE_NEXT_TASKS.md` — current sprint scope
3. `experiments/real_repos/aggregate_all.md` — what claims the paper currently makes (will need refresh after your fix)
4. `experiments/real_repos/click/agent_lock_combined.json` and `agent_lock_pr-2933.json` — diff them
5. `experiments/real_repos/marshmallow/agent_lock_combined.json` and `agent_lock_pr-2937.json` — diff them
6. `experiments/real_repos/zod/agent_lock_combined.json` — the working control
7. `experiments/real_repos/click/runs_sonnet_test_gate_canary/seed1/eval_run_acg.json` vs `experiments/real_repos/click/runs_sonnet_test_gate_n5/seed1/eval_run_acg.json` — the smoking gun for what changed
8. `experiments/greenhouse/strategies.py` (around `_apply_writes_git_sync`, read only)
9. `experiments/greenhouse/headtohead.py` (entry point)
10. `acg/correctness.py` lines 317 (`_run_pr_tests_js`), 428 (dispatch) — read only, no edits

---

## END

Good luck. The most likely root cause is H1 (state pollution between PRs in a combined run) or H5 (pytest cache). Zod escapes because vitest is invoked per-task with `--no-cache` style semantics by default, while pytest is sticky. If that's true, the fix is to run per-PR multi-seed jobs instead of combined ones for click and marshmallow — write a small wrapper script and that's the deliverable. Don't over-engineer.
