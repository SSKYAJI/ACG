# Click + Marshmallow Canary/Round-2 Diagnosis

Generated: 2026-05-13

## Summary

The discrepancy is not explained by PR-to-PR state pollution in the apply loop. The combined locks do put all three tasks into one `parallel` execution group, but `experiments/greenhouse/strategies.py` serializes every git checkout/apply/test section behind an `asyncio.Lock` before calling `_apply_writes_git_sync`. `_apply_writes_git_sync` then checks out a fresh `acg-applied/<task>` branch from the pinned `base_sha`, applies the task diff, runs the overlay/test gate, and detaches back to `base_sha` in `finally`.

The stronger root cause is evaluation/configuration drift:

1. The canary "green" rows for the validated PRs are not reliable proof that the model fixed the checkout. The current recorded canary ACG rows have zero applied source changes (`EMPTY_PATCH` or `BLOCKED_BY_SCOPE`) while still receiving `cupp_rate=1.0`.
2. The Python repo test environments are not stable isolated editable installs. The current click checkout has no executable `.venv/bin/python`; the current marshmallow `.venv/bin/python` points at `/opt/homebrew/anaconda3` and does not import marshmallow from the checkout.
3. `.venv/` inside an upstream checkout must be ignored by that checkout's own git metadata. Otherwise `_apply_writes_git_sync` uses `git add -A`, can accidentally commit the venv into an `acg-applied/*` branch, and then remove it when detaching back to the base commit.
4. The combined Python runs also add a real parent-sha problem: the combined lock is pinned to the oldest PR parent, but later PRs are scored with canonical tests from later merge commits against an older source tree.

## Evidence

### H1: State Pollution Between PRs

Not the main explanation in the current runner.

- `experiments/greenhouse/strategies.py:2263` creates `git_lock` for ACG applied mode.
- `experiments/greenhouse/strategies.py:2271-2280` wraps `_apply_writes_git_sync(...)` in that lock.
- `experiments/greenhouse/strategies.py:2284-2312` may run workers in parallel, but applies/tests returned worker diffs one at a time.
- `experiments/greenhouse/strategies.py:879-1048` checks out a task branch from `base_sha`, applies, overlays tests, runs the task tests, and detaches back to `base_sha`.

The combined lock execution groups are parallel:

- click `agent_lock_combined.json`: one group with `pr3126`, `pr2933`, `pr3363`, `type=parallel`.
- marshmallow `agent_lock_combined.json`: one group with `pr2937`, `pr2901`, `pr2902`, `type=parallel`.
- zod has the same combined-lock pattern and still gets `acg=0.33`.

So the group type affects LLM worker concurrency, but not the git apply/test critical section.

### H2: Wrong Combined `repo.commit` / Parent SHA

Confirmed as a real combined-run flaw, especially for marshmallow.

- click combined lock is pinned to `b7cf06970e40a3144eb963ff34ed7c38934afb40`, PR #2933's parent.
- click later PR parents are `c69643b...` for PR #3126 and `c8da1f...` for PR #3363.
- `git log b7cf069..c8da1f -- src/click/core.py src/click/shell_completion.py src/click/testing.py tests/...` shows many intervening changes to all relevant click source/test files.
- marshmallow combined lock is pinned to `fea5428567960f15be0c9a3a4b99c0d9bb63848c`, PR #2901's parent.
- marshmallow PR #2937's parent is `4acb783c73130f762aa5b0df6b65ff7685d5ff2c`, after multiple intervening commits including PR #2902 and validate.URL IDN work.

An isolated no-agent check with a fresh editable env showed that base parent plus canonical PR tests fails as expected:

```text
click PR #2933 no-patch check:
tests/test_testing.py::test_isolation_flushes_unflushed_stderr FAILED
1 failed, 25 passed

marshmallow PR #2937 no-patch check:
tests/test_validate.py::test_email_idn_invalid[user@-münchen.de] FAILED
tests/test_validate.py::test_email_idn_invalid[user@münchen-.de] FAILED
2 failed, 215 passed
```

That means a green zero-patch canary is an environment/scoring artifact, not a real source fix.

### H3: Stale/Missing `predicted_writes`

Not the Round-2 explanation.

Current combined locks for click and marshmallow have no `None` entries in `predicted_writes`, and their combined `allowed_paths` are populated. Some non-canary per-PR locks still have `repo.commit=None`, but Round 2 uses the combined locks. That should still be fixed before any per-PR multi-seed expansion beyond the validated PRs.

### H4: Prompt Drift

Not observed for the validated PRs.

The prompt for click `pr2933-clirunner-stderr-flush` is identical in `tasks_canary.json` and `tasks_combined.json`. The prompt for marshmallow `pr2937-email-idn` is also identical in canary and combined task files.

### H5: Pytest Cache / Test-Runner Contamination

Possible secondary risk, but not needed to explain the discrepancy.

`acg/correctness.py:445-455` runs pytest without `--cache-clear` and without `PYTHONDONTWRITEBYTECODE=1`. However, the current discrepancy is already explained by the non-isolated Python environments and combined parent mismatch.

## Smoking-Gun Eval Rows

Current recorded canaries:

- click canary ACG row: `actual_changed_files=[]`, `failure_reason=BLOCKED_BY_SCOPE`, `tests_ran=true`, `fail_to_pass_passed=1/1`, `pass_to_pass_passed=23/23`, `cupp_rate=1.0`.
- marshmallow canary ACG row: `actual_changed_files=[]`, `failure_reason=EMPTY_PATCH`, `tests_ran=true`, `fail_to_pass_passed=10/10`, `pass_to_pass_passed=197/197`, `cupp_rate=1.0`.

Current recorded combined runs:

- click Round 2 seed1/seed2 ACG rows: every task has `tests_ran=false` and `tests_skip_reason=test_command_not_found`.
- marshmallow Round 2 seed1/seed2 ACG rows: every task has `tests_ran=true`, `tests_exit_code=4`, `tests_collection_error=true`.

Current environment checks:

```text
experiments/real_repos/click/checkout/.venv/bin/python: missing
experiments/real_repos/marshmallow/checkout/.venv/bin/python -> /opt/homebrew/anaconda3/bin/python3
marshmallow import from that interpreter: ModuleNotFoundError
click import from that interpreter: /opt/homebrew/anaconda3/lib/python3.12/site-packages/click/__init__.py
```

So the Python canaries and combined Round 2 are not comparable measurement units.

## Why Zod Escapes

Zod uses `npx vitest run` from the checkout, so it does not depend on a broken Python `.venv` symlink or an editable Python package install. Zod also has the same parallel combined execution-plan pattern but still runs tests for all tasks and produces `acg=0.33`, which further argues against H1 as the primary cause.

Zod may still be escaping the oldest-parent issue by accident: its later PR tests still collect and execute against the oldest parent, and one task resolves reliably. That does not make the combined-parent method generally valid for Python repos.

## Minimal Fix

Do not use combined three-PR runs for click and marshmallow in the paper comparison set. Use single-validated-PR runs with the per-PR lock pinned to that PR's own `parent_commit_sha`, and require a real editable test environment before scoring.

Added scripts:

- `experiments/real_repos/click/multi_seed_sonnet_pr2933.sh`
- `experiments/real_repos/marshmallow/multi_seed_sonnet_pr2937.sh`

Both scripts:

- run only the validated PR lock/task file;
- write to a new output directory, not `runs_sonnet_test_gate_n5`;
- add `.venv/`, `.pytest_cache/`, `__pycache__/`, and `*.pyc` to the upstream checkout's local `.git/info/exclude`;
- create/repair `.venv`;
- install the checkout in editable mode with test dependencies;
- assert imports come from the checkout source tree before invoking `headtohead`.

Do not run these scripts while the existing combined `multi_seed_sonnet.sh` jobs are still using the same checkout.

## Validation

Completed with isolated temporary checkouts so the active combined Round-2 jobs were not disturbed. The temporary checkouts copied `manifest.json`, added `.venv/` and cache paths to `.git/info/exclude`, installed the package in editable mode, and asserted imports came from the checkout source tree before `headtohead` ran.

Click validation output:

- directory: `experiments/real_repos/click/runs_sonnet_test_gate_validation/seed99`
- command shape: per-PR lock `agent_lock_pr-2933.json`, `tasks_canary.json`, `comparison_full`
- `acg`: `cupp=0.00`, `OOB=0`, `chg=1`, `tests_ran=true`, `exit=1`, `ftp=0/1`, `ptp=23/23`, changed only `tests/test_testing.py`
- `acg_full_context`: `cupp=0.00`, `OOB=0`, `chg=0`, `tests_ran=true`, `exit=1`, `ftp=0/1`, `ptp=23/23`
- `naive_parallel_blind`: `cupp=0.00`, `OOB=2`, collection error

Marshmallow validation output:

- directory: `experiments/real_repos/marshmallow/runs_sonnet_test_gate_validation/seed99`
- command shape: per-PR lock `agent_lock_pr-2937.json`, `tasks_canary.json`, `acg_planned` only
- `acg`: `cupp=0.00`, `OOB=0`, `chg=0`, `tests_ran=true`, `exit=1`, `ftp=7/10`, `ptp=197/197`

These clean validations intentionally do not match the old canary `cupp=1.0`. They show the old canaries were false positives caused by environment/scoring drift. The per-PR scripts are still the right way to rerun these repos if needed, but there is no validated click/marshmallow productivity win to salvage.

## Recommended Paper Path

Treat click and marshmallow Round 2 combined results as invalid for CuPP productivity claims. Keep the OOB/safety observations only if the write-attempt data is clearly labeled as coming from a broken Python test environment and not used for resolved-safe productivity.

For the paper's clean comparison set:

- starlette: keep full Round 2.
- zod: keep full Round 2, with a note that combined-parent validity was empirically checked by tests collecting/running.
- click/marshmallow: present only as diagnostic/safety evidence after rerun, not as productivity wins. The existing canary and combined CuPP values should be excluded from the paper aggregate.

## Follow-Up PR Recommendations

These are outside this task's allowed edit scope but should be fixed before more Python repo evaluations:

- The CuPP aggregation should not count `EMPTY_PATCH`, `BLOCKED_BY_SCOPE`, or otherwise failed/zero-source-change rows as resolved just because the overlaid tests pass. The current `EvalTask.outcome` logic only looks at test results and OOB status.
- `_apply_writes_git_sync` should not run unconstrained `git add -A` inside third-party checkouts. It should stage only the files touched by accepted envelopes and the canonical overlaid test files, or run test environments outside the git checkout.
- Python test invocation should use isolated editable installs and should add `--cache-clear` plus `PYTHONDONTWRITEBYTECODE=1` to reduce cross-run contamination.
- Combined multi-PR Python runs need either per-task checkouts pinned to each task's own parent SHA or a manifest-level proof that all canonical tests collect and fail/pass as expected against the shared oldest parent.
