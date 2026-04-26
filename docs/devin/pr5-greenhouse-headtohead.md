## Project context

You are working on **`cognition`** — a Python+TypeScript repo whose product
is **ACG (Agent Context Graph)**, a pre-flight write-set planner for
parallel coding agents. PR 4 (`java-scanner-greenhouse-seed`) shipped the
Java tree-sitter scanner, the `experiments/greenhouse/` setup script, the
3-task `tasks.json`, and a working `make compile-greenhouse` that produces
`experiments/greenhouse/agent_lock.json`.

What's still missing is the **head-to-head runtime harness**:
`experiments/greenhouse/headtohead.py`. This script is what we point our
demo chart at to claim "ACG cuts merge collisions on a real legacy Java
codebase." Today the only runtime is `acg/runtime.py` (orchestrator + 4
sub-agent worker fan-out, propose-and-validate). It targets `demo-app`'s
TypeScript lockfile via `make run-mock` / `make run-gemma`.

Your job is to build the equivalent driver for Greenhouse, comparing two
strategies on the same `experiments/greenhouse/agent_lock.json`:

1. **Naive parallel** — every task fires its workers without coordination;
   the harness records every proposed write and counts cross-task
   overlaps. No `validate_write` enforcement.
2. **ACG-planned** — execute the lockfile group-by-group; workers'
   proposals are validated against `allowed_paths` exactly the way
   `run_lockfile()` does today; blocked writes are counted as wins.

The output is a deterministic JSON metrics file that
`acg/report.py` (existing) already knows how to chart.

## Repo state to assume

- `main` contains PR 4. Branch from `origin/main`.
- `make setup-greenhouse compile-greenhouse` works end-to-end and writes
  `experiments/greenhouse/agent_lock.json` (3 tasks, 2 parallel groups,
  1 serial group; `pom.xml` is the shared-file collision lever).
- `acg/runtime.py` exposes `run_lockfile()`, `MockRuntimeLLM`, and the
  `RunResult` / `WorkerResult` / `Proposal` dataclasses you should reuse.
- `acg/runtime.py` is **frozen** for this PR — do NOT modify it. If you
  need behaviour it doesn't expose, add a thin adapter in
  `experiments/greenhouse/headtohead.py` instead.
- `experiments/greenhouse/checkout/` is `.gitignore`d; the harness must
  refuse to run if it's missing.

## Deliverables — file by file

### 1. `experiments/greenhouse/headtohead.py`

A standalone CLI script (NOT a Typer app — keep it simple) with this
signature:

```bash
python experiments/greenhouse/headtohead.py \
  --lock experiments/greenhouse/agent_lock.json \
  --repo experiments/greenhouse/checkout \
  --out experiments/greenhouse/headtohead.json \
  [--mode {both,naive,planned}] \
  [--mock]
```

`--mock` (default) runs against `acg.runtime.MockRuntimeLLM` so the
harness is deterministic, CI-runnable, and does not require the GX10. The
non-mock path uses `acg.runtime.RuntimeLLM` with `RuntimeConfig.from_env()`
exactly the way `acg run` does, but you do NOT need to test the live
path — the human author runs it on the GX10.

#### Naive simulator

The naive path does not use `run_lockfile()` at all. It:

1. Loads the lockfile via `AgentLock.model_validate_json`.
2. Loads the repo graph via `acg.repo_graph.load_context_graph(repo)`
   (this falls back to `{}` gracefully if the graph is missing).
3. Spawns one `run_worker(task, lock, repo_graph, sub_llm)` call per
   lockfile task **concurrently** via `asyncio.gather` with no
   group-ordering, no `waits_for`, no orchestrator pass.
4. Records every `Proposal` exactly as `run_worker` returns them, but
   instead of re-using each task's `allowed_paths`, also computes:
   - `overlapping_writes` — the number of (task_id, file) pairs where
     `file` is touched by ≥ 2 tasks in the proposal set.
   - `overlap_pairs` — the number of distinct task-pairs that collide on
     at least one file.
   - `manual_merge_steps` — `2 * overlap_pairs` (mirror
     `benchmark/runner.py::run_naive`'s coefficient).
   - `blocked_bad_writes` — always `0` for naive (no enforcement).
   - `tests_passing_first_run` — `False` if any overlap exists, else
     `True`.

#### Planned simulator

The planned path **calls `acg.runtime.run_lockfile`** with the lockfile
and the same `MockRuntimeLLM` instances:

```python
from acg.runtime import MockRuntimeLLM, run_lockfile

orch = MockRuntimeLLM(role="orchestrator")
sub = MockRuntimeLLM(role="worker")
result = await run_lockfile(
    lock=lock,
    repo_graph=repo_graph,
    orch=orch,
    sub=sub,
    lockfile_path=str(lock_path),
)
```

From the returned `RunResult` (see `acg/runtime.py` for the dataclass)
derive these metrics:

- `overlapping_writes` — count of (task_id, file) pairs where `file`
  appears in ≥ 2 different workers' allowed proposal sets. Should be
  `0` or `1` if the solver is doing its job.
- `blocked_bad_writes` — `sum(w.blocked_count for w in result.workers)`.
- `manual_merge_steps` — `0` (planned mode by definition has no manual
  merges).
- `tests_passing_first_run` — `True` if `blocked_bad_writes == 0 and
overlapping_writes <= 1`, else `False`.
- `wall_time_minutes` — derive from `result.total_wall_s / 60.0`,
  rounded to 1 decimal. (Mock LLMs run in milliseconds, so this number
  is symbolic; the human author re-runs `--mock=False` on GX10 for the
  real number.)
- `acu_consumed` — `None` (we don't burn ACUs in the harness).

#### Output JSON shape

```json
{
  "version": "1.0",
  "generated_at": "2026-04-25T...Z",
  "lockfile": "experiments/greenhouse/agent_lock.json",
  "repo": "experiments/greenhouse/checkout",
  "mode": "both",
  "naive": {
    "tasks": 3,
    "overlapping_writes": 6,
    "overlap_pairs": 3,
    "blocked_bad_writes": 0,
    "manual_merge_steps": 6,
    "tests_passing_first_run": false,
    "wall_time_minutes": 18,
    "acu_consumed": null,
    "proposals": [
      {"task_id": "lambda-rowmapper-account", "file": "pom.xml", "allowed": false, "reason": "naive overlap with lambda-rowmapper-invite, lambda-rowmapper-app"},
      ...
    ]
  },
  "planned": {
    "tasks": 3,
    "overlapping_writes": 1,
    "overlap_pairs": 1,
    "blocked_bad_writes": 2,
    "manual_merge_steps": 0,
    "tests_passing_first_run": true,
    "wall_time_minutes": 13,
    "acu_consumed": null,
    "groups_executed": [
      {"id": 1, "type": "parallel", "wall_s": 0.5, "tasks": ["lambda-rowmapper-account", "lambda-rowmapper-invite"]},
      {"id": 2, "type": "serial",   "wall_s": 0.3, "tasks": ["lambda-rowmapper-app"]}
    ]
  }
}
```

The numeric values above are **illustrative**; emit whatever the
simulators actually produce. Use a stable JSON serializer
(`json.dumps(..., indent=2, sort_keys=True)`) so the file diffs cleanly
between runs. If `--mode naive`, omit the `planned` key (and vice
versa).

### 2. `Makefile` additions

Append to the existing `# ----- Greenhouse (legacy-Java demo) -----`
section at the bottom of `Makefile`. **Do not** modify any existing
target.

```makefile
headtohead-greenhouse: compile-greenhouse
	./.venv/bin/python experiments/greenhouse/headtohead.py \
	  --lock experiments/greenhouse/agent_lock.json \
	  --repo experiments/greenhouse/checkout \
	  --out experiments/greenhouse/headtohead.json \
	  --mock

# Live GX10 variant (orchestrator+sub-agents on the asus box).
headtohead-greenhouse-gemma: compile-greenhouse
	$(GEMMA_ENV) $(GEMMA_ORCH_ENV) ./.venv/bin/python experiments/greenhouse/headtohead.py \
	  --lock experiments/greenhouse/agent_lock.json \
	  --repo experiments/greenhouse/checkout \
	  --out experiments/greenhouse/headtohead.json
```

Add `headtohead-greenhouse headtohead-greenhouse-gemma` to the `.PHONY`
declaration on line 1.

### 3. `experiments/greenhouse/README.md` — append a "Head-to-head"

section

Replace **only** the existing "Head-to-head harness" section at the
bottom (currently a placeholder pointing to a not-yet-written file) with
the actual usage:

````markdown
## Head-to-head harness

```bash
make headtohead-greenhouse        # mock LLMs, runs in <2s, deterministic
make headtohead-greenhouse-gemma  # live GX10 — ~1m wall, real worker LLM output
```
````

The harness writes `experiments/greenhouse/headtohead.json` containing
two metric blocks (`naive`, `planned`) shaped the same as
`.acg/run_naive.json` / `.acg/run_acg.json` so the existing
`acg report` chart renderer can consume them.

To produce a chart comparing the two strategies:

```bash
./.venv/bin/python -c "
import json; d = json.load(open('experiments/greenhouse/headtohead.json'))
json.dump(d['naive'],   open('/tmp/g_naive.json',   'w'))
json.dump(d['planned'], open('/tmp/g_planned.json', 'w'))
"
./.venv/bin/acg report --naive /tmp/g_naive.json --planned /tmp/g_planned.json --out docs/greenhouse_benchmark.png
```

```

Do not touch the rest of the README.

### 4. `tests/test_headtohead.py`

A new test module with at least 5 tests, all using the deterministic
mock path:

1. **`test_naive_records_overlap_on_pom_xml`** — build a minimal
   3-task `AgentLock` in-memory where all three tasks predict
   `pom.xml`; run the naive simulator; assert `overlapping_writes >= 3`
   and `overlap_pairs == 3`.
2. **`test_planned_zero_manual_merges`** — same lockfile, planned
   simulator; assert `manual_merge_steps == 0` and
   `tests_passing_first_run` is `True`.
3. **`test_naive_does_not_call_validate_write`** — patch
   `acg.enforce.validate_write` to `Mock(side_effect=AssertionError)`,
   run naive, assert no AssertionError. Naive must NOT enforce.
4. **`test_planned_calls_run_lockfile_once`** — patch
   `acg.runtime.run_lockfile` and assert it's awaited exactly once with
   the expected lockfile + repo_graph dict.
5. **`test_cli_writes_combined_json`** — invoke the harness's `main()`
   via `subprocess.run([sys.executable, "experiments/greenhouse/headtohead.py", ...])`
   in a `tmp_path` populated with a fixture lockfile; assert the
   resulting JSON has both `naive` and `planned` top-level keys.

Use existing fixtures where possible (`tests/conftest.py`,
`tests/fixtures/`). Build the in-memory `AgentLock` with the helpers in
`acg.schema` (you can mirror the construction style in
`tests/test_compiler.py` or `tests/test_solver.py`).

### 5. (Optional, time-permitting) `docs/plans/greenhouse-headtohead.md`

A 1-page operator-facing rationale for the harness:
- Why Greenhouse is the right legacy-Java target (overlap on `pom.xml`,
  3 RowMapper-to-lambda refactors, ~130 files).
- How the metrics map to the Cognition rubric.
- Pointers to the runtime + lockfile so reviewers can audit the chain.

Skip this if you're tight on time — the README addition is sufficient.

## Branch / commit / PR conventions

- Branch from `main`: `git checkout -b greenhouse-headtohead-harness`
- Commit style:
```

experiments: add greenhouse head-to-head harness
experiments: wire headtohead-greenhouse make targets
experiments: document head-to-head usage in greenhouse README
tests: cover greenhouse head-to-head simulators (5 cases)

````
- PR title: `experiments: greenhouse head-to-head harness (Java demo completer)`
- PR description: paste the JSON output of `make headtohead-greenhouse`
(mock mode is fine — that's deterministic).

## Acceptance gates

```bash
./.venv/bin/python -m pytest tests/ -q          # all 111+5 = 116+ tests pass
./.venv/bin/ruff check acg/ tests/ benchmark/ experiments/
make headtohead-greenhouse                       # writes the json file
test -f experiments/greenhouse/headtohead.json
./.venv/bin/python -c "import json; d=json.load(open('experiments/greenhouse/headtohead.json')); assert d['planned']['blocked_bad_writes'] >= d['naive']['blocked_bad_writes']"
````

The lockfile-derived assertion above (planned must catch ≥ as many bad
writes as naive) is the single hard correctness gate. Everything else is
shape and lint.

## DO NOT

- Modify `acg/runtime.py`, `acg/cli.py`, `acg/compiler.py`,
  `acg/predictor.py`, `acg/solver.py`, anything in `viz/`, anything in
  `demo-app/`, or any of the other `acg/index/*` modules.
- Regenerate `experiments/greenhouse/agent_lock.json` from inside the
  harness — load and read it. The `compile-greenhouse` make target is
  the only thing allowed to write it, and only via `acg compile`.
- Add new top-level dependencies to `pyproject.toml`. The harness uses
  stdlib + `acg.runtime` + `acg.repo_graph` + `acg.schema` only.
- Run `mvn` or actually mutate any `.java` file in
  `experiments/greenhouse/checkout/`. The harness is propose-and-record
  only, exactly like `acg/runtime.py`.
- Pin a different Greenhouse commit than PR 4 used. The build spike was
  done at `174c1c320875a66447deb2a15d04fc86afd07f60`.

## When in doubt

- `acg/runtime.py::run_lockfile` is your reference for the planned
  simulator. The `MockRuntimeLLM` it ships with is what makes the
  harness deterministic.
- `benchmark/runner.py::run_naive` shows the cost-coefficient math for
  the naive simulator. Reuse the constants
  (`NAIVE_BASE_MIN_PER_TASK`, `MANUAL_MERGE_STEPS_PER_OVERLAP`, etc.)
  by importing them from `benchmark.runner` so the demo-app and
  Greenhouse charts use the same scale.
- `acg/report.py::render_chart` (or however it's named) is what
  consumes the JSON; matching its expected keys means you don't have
  to ship a custom chart renderer.
- The lockfile's `predicted_writes` already include `pom.xml` for all 3
  tasks — that's the collision lever the demo leans on. Don't try to
  add or remove it.

Good luck. Ship deterministic numbers; the human author re-runs the
GX10 path post-merge for the real demo recording.
