# Devin task bundle — ACG roadmap PRs

Independent prompts, scoped so they will not merge-conflict if launched in
parallel. Each PR has its own prompt file in this directory; copy that file's
body into Devin's UI verbatim.

## Status of the repo as of this handoff

- **Phase 1 (runtime, `acg/runtime.py`)** — shipped, `acg run` CLI command,
  `make run-gemma` and `make run-mock` targets.
- **Phase 2 (viz live mode)** — shipped, RAF replay of
  `demo-app/.acg/run_trace.json`, ALLOWED/BLOCKED badges, orchestrator panel.
- **Track A + B (predictor seeds + indexer scaffolding)** — shipped, 7-seed
  pipeline including `acg.index.aggregate()` (framework / pagerank / bm25 /
  cochange).
- **PR 1-4** — all merged. Track B indexer scaffolding, predictor coverage
  extensions, housekeeping/CI/docs, Java tree-sitter scanner + Greenhouse
  experiment seed.
- **Repo graph normalization** — local working copy (`acg/repo_graph.py`,
  `acg init-graph` CLI, Makefile/CLI changes). Commit and push before
  launching new Devin work so all PRs branch off the same `main`.
- **Tests/lint/build:** `pytest -q` → 111 passed; `ruff check acg/ tests/
benchmark/` → clean; `cd viz && npx tsc --noEmit && npm run build` → clean.

**Before launching Devin:** ensure `main` is pushed and clean:

```bash
git status                         # should be clean (or commit acg/repo_graph.py first)
git push origin main               # all four new PRs branch from origin/main
```

## The PRs

PRs 1-4 are MERGED into local `main` as of 2026-04-25. PRs 5-8 are the
next wave; they share no file footprints (apart from append-only
`Makefile` and additive `pyproject.toml` extras), so they can launch in
parallel.

| PR  | Prompt file                                                    | Branch                          | Touches                                                                                          | Status             |
| --- | -------------------------------------------------------------- | ------------------------------- | ------------------------------------------------------------------------------------------------ | ------------------ |
| 1   | [`pr1-track-b-scaffolding.md`](pr1-track-b-scaffolding.md)     | `track-b-index-scaffolding`     | `acg/index/`, `benchmark/predictor_eval.py`                                                      | ✅ merged          |
| 2   | [`pr2-predictor-coverage.md`](pr2-predictor-coverage.md)       | `predictor-coverage-extensions` | `acg/predictor.py`, `acg/compiler.py`, `tests/`                                                  | ✅ merged          |
| 3   | [`pr3-housekeeping.md`](pr3-housekeeping.md)                   | `housekeeping-ci-docs`          | `.github/workflows/`, `HANDOFF.md`, `README.md`, `scripts/diagnostics/`, `schema/*.schema.json`  | ✅ merged          |
| 4   | [`pr4-java-scanner.md`](pr4-java-scanner.md)                   | `java-scanner-greenhouse-seed`  | `graph_builder/scan_java.py`, `experiments/greenhouse/`, `tests/test_scan_java.py`, `Makefile`   | ✅ merged          |
| 5   | [`pr5-greenhouse-headtohead.md`](pr5-greenhouse-headtohead.md) | `greenhouse-headtohead-harness` | `experiments/greenhouse/headtohead.py`, `experiments/greenhouse/README.md`, `Makefile`, `tests/` | 🚀 ready to launch |
| 6   | [`pr6-mcp-server.md`](pr6-mcp-server.md)                       | `mcp-server-wrapper`            | `acg/mcp/`, `acg/cli.py` (one new command), `pyproject.toml`, `docs/MCP_SERVER.md`, `tests/`     | 🚀 ready to launch |
| 7   | [`pr7-embeddings-indexer.md`](pr7-embeddings-indexer.md)       | `index-embeddings-layer`        | `acg/index/embeddings.py`, `acg/index/aggregate.py`, `pyproject.toml`, `benchmark/`, `tests/`    | 🚀 ready to launch |
| 8   | [`pr8-cascade-hook.md`](pr8-cascade-hook.md)                   | `cascade-pre-write-hook`        | `scripts/precheck_write.sh`, `.windsurf/hooks.json`, `docs/CASCADE_INTEGRATION.md`, `tests/`     | 🚀 ready to launch |

### Why these four next

- **PR 5 — Greenhouse head-to-head harness.** Completes the Java
  legacy-demo arc (PR 4 shipped the scanner + lockfile; PR 5 ships the
  runtime metrics that go on the demo chart).
- **PR 6 — MCP server wrapper.** Closes the "MCP wrapper is on the
  roadmap" footnote in `docs/COGNITION_INTEGRATION.md`. Sponsor
  narrative completer for the Cognition track.
- **PR 7 — Local-embeddings indexer.** Roadmap item #3 in
  `docs/plans/acg-index-rewrite.md`. Mean-recall@5 lift behind an
  opt-in extra; default-off so existing benchmarks don't regress
  silently.
- **PR 8 — Cascade `pre_write_code` hook.** The stretch plan from
  `docs/plans/cascade-hook-stretch-308cc2.md`. Upgrades the demo's
  "blocked write" beat from a Python wrapper to Cascade itself.

## File footprint check (no merge conflicts at parallel launch)

| File / dir                                      | PR 5 | PR 6 | PR 7 | PR 8 |
| ----------------------------------------------- | :--: | :--: | :--: | :--: |
| `Makefile` (append-only)                        |  ✏   |  ✏   |      |  ✏   |
| `pyproject.toml` (extras only)                  |      |  ✏   |  ✏   |      |
| `acg/cli.py`                                    |      |  ✏   |      |      |
| `acg/mcp/` (new)                                |      |  ✏   |      |      |
| `acg/index/embeddings.py` (new)                 |      |      |  ✏   |      |
| `acg/index/aggregate.py`                        |      |      |  ✏   |      |
| `acg/index/__init__.py`                         |      |      |  ✏   |      |
| `experiments/greenhouse/headtohead.py` (new)    |  ✏   |      |      |      |
| `experiments/greenhouse/README.md`              |  ✏   |      |      |      |
| `scripts/precheck_write.sh` (new)               |      |      |      |  ✏   |
| `.windsurf/hooks.json` (new)                    |      |      |      |  ✏   |
| `benchmark/predictor_eval.py`                   |      |      |  ✏   |      |
| `docs/MCP_SERVER.md` (new)                      |      |  ✏   |      |      |
| `docs/CASCADE_INTEGRATION.md` (new)             |      |      |      |  ✏   |
| `README.md` (additive only)                     |      |  ✏   |      |  ✏   |
| `tests/index/test_embeddings.py` (new)          |      |      |  ✏   |      |
| `tests/test_mcp.py` (new)                       |      |  ✏   |      |      |
| `tests/test_headtohead.py` (new)                |  ✏   |      |      |      |
| `tests/test_precheck_write_script.py` (new)     |      |      |      |  ✏   |
| `docs/COGNITION_INTEGRATION.md` (one paragraph) |      |  ✏   |      |      |
| `HANDOFF.md` (one bullet)                       |      |  ✏   |      |      |
| `docs/plans/acg-index-rewrite.md` (one bullet)  |      |      |  ✏   |      |

The only multi-PR-touched files are `Makefile` (3 PRs, append-only —
each adds new targets at the bottom) and `pyproject.toml` (2 PRs,
each adds a new entry under `[project.optional-dependencies]`). Both
merge automatically with `git merge` ordered as below.

## What Devin must NOT touch (across all four PRs)

- `acg/runtime.py` — frozen; produces the live demo trace.
- `acg/predictor.py` — frozen; the 7-seed pipeline is locked in.
- `acg/compiler.py`, `acg/solver.py`, `acg/enforce.py` — frozen.
- `acg/repo_graph.py` and `acg/cli.py::cmd_compile` / `cmd_init_graph` —
  shipping in the human's working copy; do not modify.
- `viz/` — frozen.
- `demo-app/agent_lock.json`, `demo-app/.acg/run_trace.json` — frozen.
- `acg/index/{framework,pagerank,bm25,cochange}.py` — frozen.

PR-specific frozen surfaces are listed in each prompt's "DO NOT" block.

## Acceptance gates (every PR must pass these)

```bash
# Python
./.venv/bin/python -m pytest tests/ -q
./.venv/bin/ruff check acg/ tests/ benchmark/

# TypeScript / viz
cd viz && npx tsc --noEmit && npm run build && cd ..
```

Plus PR-specific tests/benchmarks listed in each prompt.

## Launching strategy

**Parallel (recommended given the napping operator):** launch all four
at once. They share no source files. Merge in this order:

1. **PR 6 (MCP)** — purely additive `acg/mcp/` + one CLI command.
   Smallest review surface.
2. **PR 5 (Greenhouse harness)** — only touches `experiments/`,
   `tests/`, and the `Makefile`. Demo-track win.
3. **PR 8 (Cascade hook)** — touches `.windsurf/`, `scripts/`,
   `docs/`, `tests/`. Independent of the Python source tree.
4. **PR 7 (Embeddings)** — biggest blast radius (`acg/index/`,
   `benchmark/`, `pyproject.toml`). Land last so any Δrecall@5 numbers
   include the other PRs' merged state.

**Sequential safer:** launch PR 6 → PR 5 → PR 8 → PR 7 in that order;
each gates the next.

When launching in Devin, set `git fetch origin && git checkout main &&
git pull` as the first command in every session so all four branches
start from the same SHA.
