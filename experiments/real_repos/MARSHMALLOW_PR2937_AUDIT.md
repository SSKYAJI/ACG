# Marshmallow pr2937 5-Seed Audit
Generated: 2026-05-13 00:57:23 PDT

## Verdict
FLAG (real seed1/seed2 successes, but not clean enough to cite as a 5-seed `cupp=1.00` result).

Seed1 ACG is not the earlier empty-patch/test-command artifact: it has a non-empty applied patch, touches the expected files, imports `marshmallow` from the checkout source, runs pytest, and reports `fail_to_pass=10/10` plus `pass_to_pass=197/197`. However, there are three caveats that need disclosure:

- The venv still uses an Anaconda base interpreter (`.venv/bin/python -> python3 -> /opt/homebrew/anaconda3/bin/python3`), although package isolation and source import are correct.
- Seed1's eval metadata records repo commit `fea5428567960f15be0c9a3a4b99c0d9bb63848c`, while the lock/manifest PR parent is `4acb783c73130f762aa5b0df6b65ff7685d5ff2c`; seed2 records the expected parent and also passes.
- By the time of audit, seeds 3-5 had also landed and ACG was `cupp=0`, `fail_to_pass=7/10` for each. This is a real variance/caveat, not a full 5-seed sweep at `cupp=1.00`.

## Evidence per step

### Step 1 — actual_changed_files non-empty: YES

Seed1 ACG reports `actual_changed_files=["src/marshmallow/validate.py", "tests/test_validate.py"]` in `experiments/real_repos/marshmallow/runs_sonnet_test_gate_pr2937_n5/seed1/eval_run_acg.json:97`, with `actual_changed_files_kind="applied_diff"` at line 101. It has `failure_reason=null` at line 119 and `patch_applies=true` at line 129. This is not the earlier `EMPTY_PATCH` case.

The emitted raw patch contains a real Email validator change in `experiments/real_repos/marshmallow/runs_sonnet_test_gate_pr2937_n5/seed1/acg_planned_raw/pr2937-email-idn.txt:154`: it adds an `original_labels = value.rsplit("@", 1)[1].split(".")` check at lines 164-169 and raises `ValidationError` when any label starts or ends with `-`. It also adds regression tests for invalid IDN/hyphen emails at lines 188-200.

The manifest's ground truth files for PR #2937 are `src/marshmallow/validate.py` and `tests/test_validate.py` in `experiments/real_repos/manifest.json:1075`. The historical merge is `f07eadc87dfac25ed505d5cd9d186920f2682733` with parent `4acb783c73130f762aa5b0df6b65ff7685d5ff2c` at lines 1072-1073. The historical PR fix is broader than the ACG seed1 patch: it changes the Email domain regex to accept Unicode labels directly and adds valid/invalid IDN tests. ACG's patch is narrower, but it addresses the same failing behavior by rejecting original Unicode labels with leading/trailing hyphens before returning from the IDNA fallback path.

### Step 2 — tests ran cleanly: YES

Seed1 ACG reports `cupp_rate=1.0` in `eval_run_acg.json:30`, `tests_ran_count=1` and `tests_total_run=217` at lines 71-72, `fail_to_pass_passed=10` and `fail_to_pass_total=10` at lines 117-118, and `pass_to_pass_passed=197` plus `pass_to_pass_total=197` at lines 142-143.

The top-level task test fields show `tests_collection_error=false`, `tests_exit_code=0`, `tests_failed_count=0`, `tests_passed_count=217`, `tests_ran=true`, `tests_skip_reason=""`, and `tests_total_count=217` in `eval_run_acg.json:163` through `:169`. This is not `test_command_not_found`.

The manifest lists 10 FTP cases for this PR in `experiments/real_repos/manifest.json:1079` through `:1090`, including three invalid IDN cases and seven valid IDN cases. The 197 PTP total matches the eval JSON. The legacy nested `test` object in `eval_run_acg.json:155` through `:162` is null/false, but the run-level counters and task-level `tests_*` fields show the actual pytest result.

### Step 3 — venv isolated: NO (strict), YES for source/package isolation

Strict binary criterion: NO. `experiments/real_repos/marshmallow/checkout/.venv/pyvenv.cfg:1` records `home = /opt/homebrew/anaconda3/bin`, line 4 records `executable = /opt/homebrew/anaconda3/bin/python3.12`, and line 5 shows the venv was created with `/opt/homebrew/anaconda3/bin/python3 -m venv ...`. Shell inspection also showed `.venv/bin/python -> python3 -> /opt/homebrew/anaconda3/bin/python3`.

Package/source isolation: YES. `pyvenv.cfg:2` has `include-system-site-packages = false`. Runtime inspection showed:

```text
sys.executable = /Users/prajit/Desktop/projects/cognition/experiments/real_repos/marshmallow/checkout/.venv/bin/python
sitepackages = /Users/prajit/Desktop/projects/cognition/experiments/real_repos/marshmallow/checkout/.venv/lib/python3.12/site-packages
marshmallow.__file__ = /Users/prajit/Desktop/projects/cognition/experiments/real_repos/marshmallow/checkout/src/marshmallow/__init__.py
pytest = /Users/prajit/Desktop/projects/cognition/experiments/real_repos/marshmallow/checkout/.venv/lib/python3.12/site-packages/pytest/__init__.py
```

The bootstrap script explicitly installs editable `marshmallow[tests]` using the venv at `experiments/real_repos/marshmallow/multi_seed_sonnet_pr2937.sh:49` through `:50`, then checks that `marshmallow.__file__` is under `experiments/real_repos/marshmallow/checkout/src/marshmallow` at lines 51-58. So the old artifact (importing marshmallow from global Anaconda site-packages) is not present, but the interpreter symlink still violates the stricter audit requirement.

### Step 4 — ACG vs naive diff diff: ACG applied a targeted patch; naive emitted a non-applicable patch

ACG seed1 used the lock context and changed the expected files. The eval JSON records 10,470 worker prompt tokens in `eval_run_acg.json:131`, actual changed files at lines 97-100, and no blocked or out-of-bounds writes at lines 116 and 138. Its raw patch targets the real `Email.__call__` / `DOMAIN_REGEX` code path and adds the original-label hyphen check in `acg_planned_raw/pr2937-email-idn.txt:164` through `:169`.

The naive seed1 result is materially different. `experiments/real_repos/marshmallow/runs_sonnet_test_gate_pr2937_n5/seed1/eval_run_naive.json:97` reports `actual_changed_files=[]`, line 116 reports `failure_reason="EMPTY_PATCH"`, and lines 114-115 report only `fail_to_pass=7/10`. It still ran tests (`tests_ran=true`, `tests_exit_code=1`, `tests_failed_count=2`) at lines 160-166 and kept `pass_to_pass=197/197` at lines 139-140.

The naive raw output in `experiments/real_repos/marshmallow/runs_sonnet_test_gate_pr2937_n5/seed1/naive_parallel_raw/pr2937-email-idn.txt:16` refers to `_validate_email`, lower-case `domain_regex`, and `self.error`, which do not match this marshmallow checkout's `Email.__call__` / `DOMAIN_REGEX` structure. Its first patch also imports `unicodedata` at line 12 and later adds speculative class-style tests at lines 84-108. The large gap is therefore explained by patch applicability/localization and context, not by identical patches getting different test treatment.

### Step 5 — blind OOB=0 explanation

The seed1 blind `out_of_bounds_write_count=0` is technically true but misleading if read as "no out-of-scope attempts." `experiments/real_repos/marshmallow/runs_sonnet_test_gate_pr2937_n5/seed1/eval_run_naive_parallel_blind.json:23` reports `blocked_invalid_write_count=6`, lines 33-45 report six blocked events, and line 51 reports `out_of_bounds_write_count=0`. The task-level blocked events at lines 113-143 show three attempted writes to `validator.go` and three to `validator_test.go`, all rejected because allowed paths are only `src/marshmallow/validate.py` and `tests/test_validate.py`.

`seed1/run_attempt1.log:3` through `:21` confirms those six blocked attempts before the blind eval JSON was written. The blind raw output also shows it guessed Go files: `naive_parallel_blind_raw/pr2937-email-idn.txt:8` targets `validator.go`, and line 25 targets `validator_test.go`.

The broken canary's blind OOB=3 was a different phenomenon. In `experiments/real_repos/marshmallow/runs_sonnet_test_gate_canary/seed1/eval_run_naive_parallel_blind.json:41` and `:51`, the OOB count is 3, and the files are `.acg/cache/cochange.pickle`, `.acg/cache/pagerank-e511371ab32174978e2376f5.pickle`, and `.acg/context_graph.json` at lines 145-149. It also had one blocked source/test attempt at lines 117-122. Conclusion: the new blind run did have out-of-scope model attempts, but they are counted under `blocked_invalid_write_count`, not `out_of_bounds_write_count`; the previous OOB count was polluted by infrastructure artifact files.

### Step 6 — seed2 cross-check (if available)

Seed2 was available and also passed ACG. `experiments/real_repos/marshmallow/runs_sonnet_test_gate_pr2937_n5/seed2/eval_run_acg.json:13` records the expected PR parent commit `4acb783c73130f762aa5b0df6b65ff7685d5ff2c`, line 30 reports `cupp_rate=1.0`, lines 97-100 report a non-empty applied diff touching `src/marshmallow/validate.py`, lines 116-117 report `fail_to_pass=10/10`, lines 141-142 report `pass_to_pass=197/197`, and lines 162-168 report clean pytest execution with 217 passed tests.

Additional landed seeds were checked because they were already present; no waiting was done for them. Current ACG seed summary:

```text
seed1 commit=fea5428567960f15be0c9a3a4b99c0d9bb63848c cupp=1 ftp=10/10 ptp=197/197 tests_exit=0
seed2 commit=4acb783c73130f762aa5b0df6b65ff7685d5ff2c cupp=1 ftp=10/10 ptp=197/197 tests_exit=0
seed3 commit=4acb783c73130f762aa5b0df6b65ff7685d5ff2c cupp=0 ftp=7/10 ptp=197/197 tests_exit=1
seed4 commit=4acb783c73130f762aa5b0df6b65ff7685d5ff2c cupp=0 ftp=7/10 ptp=197/197 tests_exit=1
seed5 commit=4acb783c73130f762aa5b0df6b65ff7685d5ff2c cupp=0 ftp=7/10 ptp=197/197 tests_exit=1
```

This makes seed1 real but not representative of a stable 5-seed `cupp=1.00`.

## What the paper can claim if PASS

Do not claim that the marshmallow PR #2937 5-seed result has `cupp=1.00`. A defensible FLAG wording is:

"On marshmallow PR #2937, ACG produced pytest-validated patches in seeds 1 and 2 that passed all 10 fail-to-pass tests and all 197 pass-to-pass tests, while seed1 baselines remained at 7/10 FTP. The full five-seed run showed substantial variance, with ACG seeds 3-5 at 7/10 FTP, so results should be reported per seed or as an aggregate with variance."

## Caveats / threats to validity (for the paper's limitations section)

- The seed1 result is not an empty-patch or skipped-test artifact, but its eval metadata records commit `fea5428...` rather than the PR parent `4acb783...`. Seed2 validates the same outcome at the expected parent, so this is a metadata/source-state caveat rather than a retraction by itself.
- The venv imports checkout-local marshmallow and uses venv-local packages, but the interpreter is based on Anaconda. If the paper requires hermetic interpreter isolation, rerun with a non-Anaconda Python and record `pyvenv.cfg`.
- Baseline `naive_parallel` did not apply a competing patch in seed1; it failed as `EMPTY_PATCH`. The seed1 ACG-vs-naive gap is therefore a context/localization and patch-applicability result, not a comparison between two successfully applied semantic fixes.
