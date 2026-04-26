# PR 4 — Java graph builder + legacy-Java demo seed

> **Copy everything below this line into Devin verbatim.**
> **Before launching:** the human author has spiked the target repo's
> `mvn clean test` and confirmed it builds. The repo + commit hash below
> are LOCKED — do NOT pick a different repo, even if you find issues.

---

## Project context

You are extending **`cognition`** — a Python+TypeScript repo whose product
is **ACG (Agent Context Graph)**, a pre-flight write-set planner for
parallel coding agents. ACG today:

- Compiles a `tasks.json` into a lockfile (`agent_lock.json`) with disjoint
  per-task `allowed_paths`
- Runs an async runtime that fans out worker LLMs and validates each
  proposed write against `allowed_paths`
- Visualizes the trace in a React SPA under `viz/`

Today this works on **TypeScript repos** because the graph builder
(`graph_builder/scan.ts`) uses `ts-morph` and only knows how to traverse
a TS AST. Symbol-graph data feeds the predictor's `_symbol_seed` step.

Your goal: add **Java support** so we can run the ACG demo head-to-head
against parallel coding agents on a real legacy Java codebase.

## Repo target (LOCKED — do not change)

- **Repo URL:** `https://github.com/spring-attic/greenhouse`
- **Pinned commit:** `174c1c320875a66447deb2a15d04fc86afd07f60`
- **License:** Apache 2.0
- **Build:** Maven, embedded H2 for tests
- **Why this repo:** Spring team's own conference app from 2011, ~130
  Java files, named domain services (`EventService`, `InviteService`,
  `FriendService`, `AccountRepository`) that overlap on shared config.

If for any reason the human-supplied alternative is `mybatis/jpetstore-6`
at tag `jpetstore-6.0.1`, the entire spec below applies with that swap.
Don't autonomously decide to switch repos.

## Deliverables — file by file

### 1. `graph_builder/scan_java.py`

A new Python module that produces a `context_graph.json` shaped
**identically** to what `graph_builder/scan.ts` already emits for
TypeScript. Read `graph_builder/scan.ts` first to understand the exact
output schema; your Java emitter must mirror its top-level shape:

```json
{
  "version": "1.0",
  "language": "java",
  "files": ["src/main/java/.../EventService.java", ...],
  "symbols_index": {
    "EventService": ["src/main/java/.../EventService.java"],
    "JdbcTemplate": ["src/main/java/.../config/DatabaseConfig.java", ...],
    ...
  },
  "imports": {
    "src/main/java/.../EventService.java": [
      "org.springframework.jdbc.core.JdbcTemplate",
      ...
    ]
  }
}
```

Implementation:
- Use `tree-sitter` + `tree-sitter-languages` for the Java grammar (already
  added in PR 1's `pyproject.toml` deps; if PR 1 hasn't merged yet, add it
  here).
- Walk every `.java` file under the target repo
- Extract: class declarations, interface declarations, public method
  signatures (name only), import statements
- Write to `<repo_root>/.acg/context_graph.json`

Keep the implementation single-file (~250 LOC). No multi-pass
type-resolution; we only need name-level symbols, not full type info.

### 2. `acg/cli.py` — extend `compile` command

Add a `--language` flag (default `typescript`) to `acg compile`. When
`--language java`, route to `scan_java.scan(repo_root)` instead of
shelling out to `graph_builder/scan.ts`. The rest of the compile pipeline
(predictor → solver → enforce) is language-agnostic and needs no changes.

### 3. `experiments/greenhouse/setup.sh`

A bash setup script (idempotent, safe to re-run):

```bash
#!/usr/bin/env bash
set -euo pipefail

WORKDIR="${WORKDIR:-experiments/greenhouse/checkout}"
REPO="https://github.com/spring-attic/greenhouse.git"
COMMIT="174c1c320875a66447deb2a15d04fc86afd07f60"

if [ ! -d "$WORKDIR" ]; then
  git clone --depth 1000 "$REPO" "$WORKDIR"
fi

cd "$WORKDIR"
git fetch --depth 1000 origin "$COMMIT" 2>/dev/null || true
git checkout "$COMMIT"
echo "Greenhouse pinned at $COMMIT"
```

Add a corresponding `make setup-greenhouse` target in the Makefile.

### 4. `experiments/greenhouse/tasks.json`

Three carefully-engineered refactor tasks designed to **force write-set
overlap on shared config files**. Use ACG's existing `tasks.json` schema
(see `examples/tasks.example.json`).

```json
{
  "tasks": [
    {
      "id": "lambda-event-comparator",
      "prompt": "In EventService, replace the anonymous Comparator<Event> inner class used for sorting upcoming events with a Java 8 lambda using Comparator.comparing(Event::getStartTime). Update any imports.",
      "hints": {
        "touches": ["EventService", "events"]
      }
    },
    {
      "id": "lambda-rowmapper-account",
      "prompt": "In AccountRepository, replace the anonymous RowMapper<Account> inner class with a lambda. The mapping logic should remain identical.",
      "hints": {
        "touches": ["AccountRepository", "accounts"]
      }
    },
    {
      "id": "lambda-rowmapper-invite",
      "prompt": "In InviteRepository (or InviteService if the repository class is absent), replace anonymous RowMapper<Invite> inner classes with lambdas.",
      "hints": {
        "touches": ["Invite", "invites"]
      }
    }
  ]
}
```

**Critical:** all three tasks legitimately touch shared `JdbcTemplate`
configuration in `DatabaseConfig.java` because anonymous-class →
lambda refactors often want to inline their query string near the bean
definition. Verify by inspection that ACG's predictor produces an
overlap signal on `DatabaseConfig.java` for at least 2 of the 3 tasks.
If not, add a 4th task that explicitly mentions
`DatabaseConfig` to seed the collision.

### 5. `tests/test_scan_java.py`

At least 5 tests covering:
- Class extraction (positive + import-only file negative)
- Method extraction (public methods named correctly, private methods skipped)
- Symbols-index merge across files
- Empty-input handling
- A small fixture under `tests/fixtures/tiny_java_repo/` with 3 hand-
  written `.java` files

### 6. `Makefile` additions

```makefile
.PHONY: setup-greenhouse compile-greenhouse

setup-greenhouse:
	bash experiments/greenhouse/setup.sh

compile-greenhouse: setup-greenhouse
	./.venv/bin/acg compile \
	  --tasks experiments/greenhouse/tasks.json \
	  --repo experiments/greenhouse/checkout \
	  --language java \
	  --out experiments/greenhouse/agent_lock.json
```

### 7. `experiments/greenhouse/README.md`

A 1-page operator's guide:
- One-line summary of the experiment
- How to run `make setup-greenhouse compile-greenhouse`
- What to expect in the lockfile (3-task disjoint write-sets)
- A pointer to the harness in `experiments/greenhouse/headtohead.py`
  (which the human author writes, not Devin)

## Dependencies to add to `pyproject.toml`

```toml
# only if PR 1 hasn't merged these yet:
"tree-sitter>=0.21",
"tree-sitter-languages>=1.10",
```

No other new deps. Do NOT add `pyspark`, `javalang`, `pyjavac`, or
similar — tree-sitter is sufficient.

## Branch / commit / PR conventions

- Branch from `main`: `git checkout -b java-scanner-greenhouse-seed`
- Commits:
  ```
  graph_builder: add tree-sitter-based Java scanner
  cli: route compile to scan_java when --language java
  experiments: add greenhouse setup script + tasks.json
  tests: cover scan_java with 5 unit tests
  docs: add greenhouse experiment README
  ```
- PR title: `Java graph builder + legacy-Java demo seed`
- PR description: include the output of `make compile-greenhouse` showing
  3 tasks with non-empty `predicted_writes` and a summary line of which
  shared file (likely `DatabaseConfig.java`) overlaps.

## Acceptance gates

```bash
./.venv/bin/python -m pytest tests/ -q     # all tests still pass
./.venv/bin/ruff check acg/ tests/ benchmark/ graph_builder/ experiments/
make setup-greenhouse compile-greenhouse   # produces non-empty lockfile
```

The lockfile must:
- Contain exactly 3 tasks
- Each task must have `predicted_writes` with at least 2 entries (your
  scanner has emitted enough symbols for the predictor to seed)
- The compiler's solver must place at least 2 tasks in the **same**
  group (parallel) **and** show at least one shared-file overlap that the
  solver routed to a serial group — proving the conflict detection is real

## DO NOT

- Modify `acg/runtime.py`, `acg/cli.py compile`'s existing TS path,
  `viz/`, or `demo-app/`
- Modify the merged Track A test-scaffold seed or any `acg/index/`
  modules from PR 1
- Commit the cloned Greenhouse source into the cognition repo (it's
  pulled at runtime via `setup.sh`; only `experiments/greenhouse/checkout/`
  is in `.gitignore` — add it if not already there)
- Pick a different repo than the one specified above. The human author
  ran a build spike to confirm this commit works.
- Run `mvn` from your build environment — Devin sandboxes don't
  necessarily have a JDK, and the experiment runs on the human author's
  machine. Just write the scanner and the setup script.

## When in doubt

- `graph_builder/scan.ts` is your reference for the output shape.
- `acg/predictor.py` consumes `repo_graph["symbols_index"]` and
  `repo_graph["files"]` — don't change the predictor; just make sure
  your scanner emits exactly those keys.
- `tree-sitter-languages` ships pre-compiled grammars; you do NOT need
  `npm install` or any C compilation.
