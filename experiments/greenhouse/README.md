# Greenhouse experiment

A head-to-head playground for ACG against parallel coding agents on a real
**legacy Java** codebase: Spring's own
[`spring-attic/greenhouse`](https://github.com/spring-attic/greenhouse)
conference app, pinned to commit
[`174c1c320875a66447deb2a15d04fc86afd07f60`](https://github.com/spring-attic/greenhouse/tree/174c1c320875a66447deb2a15d04fc86afd07f60).

The experiment exists to demonstrate that ACG's pre-flight write-set
planning generalizes beyond TypeScript: the same predictor and solver that
power the demo-app benchmark also work against a Java repo, given a
language-appropriate context graph.

## Why this repo

- ~130 Java files with Spring-style domain services
  (`EventService`, `InviteService`, `FriendService`, `AccountRepository`)
  that share configuration in `DatabaseConfig.java`.
- Apache 2.0 licensed; the human author has confirmed `mvn clean test`
  builds at the pinned commit.
- The shared `JdbcTemplate` config is exactly the kind of cross-cutting
  surface where naive parallel agents collide.

## Run it

```bash
make setup-greenhouse compile-greenhouse
```

`setup-greenhouse` clones (or updates) the upstream repo into
`experiments/greenhouse/checkout/` and pins it to the commit above.
`compile-greenhouse` runs `acg compile --language java`, which:

1. Walks the checkout with the tree-sitter Java grammar
   (`graph_builder/scan_java.py`) and emits
   `experiments/greenhouse/checkout/.acg/context_graph.json`.
2. Feeds that graph + `tasks.json` into the standard ACG compile pipeline
   (predictor → solver → enforce).
3. Writes `experiments/greenhouse/agent_lock.json`.

## What to expect in the lockfile

Three refactor tasks, each replacing an anonymous-inner-class with a
Java 8 lambda:

- `lambda-rowmapper-account` — `RowMapper<PasswordProtectedAccount>` → lambda
- `lambda-rowmapper-invite` — `RowMapper<Invite>` → lambda
- `lambda-rowmapper-app` — four `RowMapper` inner classes in `JdbcAppRepository.java` → lambdas

Each task carries `"pom.xml"` in `hints.touches` so the topical seed picks
up the shared build file alongside the per-task service file. The solver
detects the `pom.xml` overlap across all three tasks and serializes them,
producing three serial groups in the lockfile.

The lockfile's `predicted_writes` should contain at least the per-service
file plus `DatabaseConfig.java` for each task, matching the predictor's
overlap signal.

## Head-to-head eval harness (megaplan v0.1)

The harness that drives parallel coding agents against this lockfile —
ACG-planned vs. naive — lives at `experiments/greenhouse/headtohead.py`.
Its single output artifact is `eval_run.json` (see
`experiments/greenhouse/eval_schema.py` for the v0.1 dataclasses):

```bash
# Mock backend — deterministic, runs in <2s, CI-friendly.
make eval-greenhouse-mock
# → experiments/greenhouse/runs/eval_run_naive.json
# → experiments/greenhouse/runs/eval_run_acg.json
# → experiments/greenhouse/runs/eval_run_combined.json

# Live local LLM (GX10) — same harness, real worker calls.
make eval-greenhouse-local

# Manual Devin sidecar — point DEVIN_RESULTS_NAIVE / DEVIN_RESULTS_ACG at
# JSON files exported from Devin sessions (see devin_adapter.py docstring
# for the sidecar shape).
make eval-greenhouse-devin-manual \
  DEVIN_RESULTS_NAIVE=experiments/greenhouse/runs/devin_naive_raw.json \
  DEVIN_RESULTS_ACG=experiments/greenhouse/runs/devin_acg_raw.json

# Markdown table + PNG chart from any eval_run files on disk.
make eval-greenhouse-report
```

### Backends

| Backend        | When to use                     | Wires up                                                                                                                                                                             |
| -------------- | ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `mock`         | CI, offline, schema validation  | `LockfileEchoMockLLM` echoes `predicted_writes`                                                                                                                                      |
| `local`        | Live GX10 numbers               | `acg.runtime.RuntimeLLM` against `ACG_*` env vars                                                                                                                                    |
| `devin-manual` | Devin sessions exported by hand | reads a sidecar JSON                                                                                                                                                                 |
| `devin-api`    | **Live Devin v3 API**           | `DevinClient` against `DEVIN_API_KEY` / `DEVIN_ORG_ID`; submits real sessions, polls until terminal, extracts changed files via `pull_requests` + `structured_output` + message scan |

#### Live Devin API setup (one-time)

1. **Fork** `spring-attic/greenhouse` to a GitHub org you control.
2. **Connect that org to Devin** via the Devin admin UI so Devin's GitHub
   integration can clone, push, and open PRs.
3. **Export credentials** in `.env`:
   ```env
   DEVIN_API_KEY=cog_xxxx
   DEVIN_ORG_ID=org_xxxx
   ```
4. **Run** `make eval-greenhouse-devin-api DEVIN_GITHUB_REPO_URL=https://github.com/<your-org>/greenhouse.git`.

The harness submits one Devin session per task. For `naive_parallel`,
all 3 sessions launch concurrently. For `acg_planned`, sessions are
submitted in the order dictated by `execution_plan.groups` — within a
group they run in parallel; groups serialize behind their predecessors.

Each session's prompt:

- (Both strategies) tells Devin the GitHub URL + base branch and asks
  it to open a PR titled `[ACG-<strategy>] <task_id>`.
- (`acg_planned` only) embeds the lockfile's `allowed_paths` as a soft
  write boundary plus any cross-task conflicts the planner identified.

Each session is tagged `strategy=...`, `task_id=...`, `run_id=...`,
`harness=acg-greenhouse` so you can filter and audit them in the Devin
UI later. Devin's reported `acus_consumed` is captured per task and
aggregated into `summary_metrics.acus_consumed_total`.

### What "completed" means

Conservative scoring per the megaplan: a task is `completed` only if the
backend reports success **and** any tests that ran passed **and** no
proposed/actual write fell outside the task's `allowed_paths`. A task
that wrote outside its boundary is `completed_unsafe` and does **not**
count toward `summary_metrics.tasks_completed`.

### Limitations

- The mock backend's wall time is symbolic (`MockRuntimeLLM` returns in
  microseconds). Run `make eval-greenhouse-local` for honest tasks/hour.
- All three Greenhouse tasks touch `pom.xml`, so naive parallel always
  surfaces 3 overlap pairs and ACG always serializes them. Add tasks
  that modernize independent surfaces (see the megaplan candidate list)
  only after the core artifact pipeline is stable.
- `devin-api` is **live**. Implementation in
  `experiments/greenhouse/devin_api.py` + `devin_adapter.py` against the
  v3 organization-scoped surface, exercised by `tests/test_devin_api.py`
  (17 cases). 6 live Devin PRs from `make eval-greenhouse-devin-api` are
  cited in `RESULTS.md` and `docs/COGNITION_INTEGRATION.md`. The manual
  sidecar path remains available as a fallback when API quota is
  exhausted.
