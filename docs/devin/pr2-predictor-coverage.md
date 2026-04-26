# PR 2 — Predictor coverage extensions

> **Copy everything below this line into Devin verbatim.**

---

## Project context

You are extending the **predictor** in the `cognition` repo's ACG project.
The predictor (`acg/predictor.py`) takes a `TaskInput` plus a `repo_graph`
plus an LLM client and returns a list of `PredictedWrite` records. Each
`PredictedWrite` has `path`, `confidence` (0-1), `reason`. The compiler
(`acg/compiler.py`) turns these into a lockfile's `allowed_paths` glob
list, and the runtime validates each worker's proposed write against those
globs.

Today there are four deterministic seed functions:

1. `_static_seed` — regex for explicit file mentions
2. `_symbol_seed` — camelCase tokens → file via `repo_graph["symbols_index"]`
3. `_topical_seed` — `hints.touches` substring match against existing files
4. `_test_scaffold_seed` — *(Track A, just shipped)* test framework
   convention detection

The **live demo trace** in `demo-app/.acg/run_trace.json` shows the
predictor missing four predictable proposals across the `oauth`, `billing`,
and `tests` tasks:

| Task | Worker proposed | Predictor missed because |
|------|------------------|--------------------------|
| oauth | `.env.example` | no seed handles env files |
| billing | `.env.example` | same |
| billing | `src/app/api/billing/checkout/route.ts` | predictor anticipated `src/server/stripe.ts`, sibling pattern was right |
| billing | `src/app/api/billing/webhook/route.ts` | same |

Your goal is to add three new deterministic seeds to close those gaps, plus
a multi-entity extension to `_test_scaffold_seed`, plus one targeted compiler
fix for shallow test path glob-broadening.

## Deliverables

### 1. `_env_seed` in `acg/predictor.py`

A new seed function that recognizes prompts implying environment-variable
edits and emits `.env.example` (and `.env.local` for Next.js projects) as
predictions.

```python
_ENV_TRIGGER_RE = re.compile(
    r"\b(oauth|stripe|auth0|clerk|nextauth|api[\s-]?key|secret|"
    r"credentials?|provider[s]?|env(?:ironment)?\s+vars?)\b",
    re.IGNORECASE,
)

def _env_seed(task: TaskInput, repo_root: Path | None) -> list[PredictedWrite]:
    if not _ENV_TRIGGER_RE.search(task.prompt):
        return []
    seeds = [PredictedWrite(
        path=".env.example",
        confidence=0.8,
        reason="Env-var seed: prompt mentions credentials/providers; agents typically extend `.env.example`.",
    )]
    if repo_root and (repo_root / "next.config.js").exists() or \
       repo_root and (repo_root / "next.config.ts").exists():
        seeds.append(PredictedWrite(
            path=".env.local",
            confidence=0.65,
            reason="Next.js project: `.env.local` is the conventional secrets file.",
        ))
    return seeds
```

Confidence 0.8 for `.env.example`, 0.65 for `.env.local`. Wire into
`predict_writes` after `_test_scaffold_seed`.

### 2. `_sibling_pattern_seed` in `acg/predictor.py`

Walk `repo_graph["files"]` looking for **structural siblings** — paths that
share their parent's pattern with at least 2 other files. When the prompt
asks to "add a `<resource>` API/endpoint/route", pick the deepest sibling
pattern and propose the new path with `<entity>` substituted.

Concretely:

```
repo_graph contains:
  src/app/api/auth/[...nextauth]/route.ts
  src/app/api/auth/config.ts

Task: "Add Stripe webhook endpoint."
Entity (extracted): "stripe" or "webhook" — pick the most distinctive token.

Output:
  src/app/api/stripe/route.ts  (confidence 0.75)
  src/app/api/webhook/route.ts (confidence 0.65 — secondary)
```

Use the existing `_extract_entity_noun` helper from Track A; if it returns
None, look for "add the X" / "implement Y" / "create Z" patterns. Filter
out paths that already exist in `repo_graph`. Cap at 2 sibling-pattern
seeds per task.

### 3. Multi-entity in `_test_scaffold_seed`

Today `_extract_entity_noun` returns the first match. Promote it to
`_extract_entity_nouns` (plural, returns `list[str]`) by collecting all
non-stopword matches up to 4. Update `_test_scaffold_seed` to emit one
spec path per entity:

- "Add Playwright tests covering login and signup" → 2 seeds:
  `tests/e2e/login.spec.ts` and `tests/e2e/signup.spec.ts`
- Existing single-entity behaviour preserved.

Keep a backward-compatible `_extract_entity_noun` alias returning the first
of the list, since tests import it directly.

### 4. Compiler fix: glob-broadening for shallow test paths

In `acg/compiler.py::_to_allowed_path`, today the broadening rule is:

```python
GLOB_BROADENING_MIN_SEGMENTS = 4
GLOB_BROADENING_MIN_CONFIDENCE = 0.7
```

So `tests/e2e/checkout.spec.ts` (3 segments) stays exact, blocking a
sibling proposal like `tests/e2e/billing.spec.ts`.

Lower the threshold for **test directories specifically**:

```python
TEST_DIR_PREFIXES = ("tests/", "__tests__/", "cypress/", "e2e/", "spec/")

def _to_allowed_path(write: PredictedWrite) -> str:
    parts = write.path.split("/")
    is_test_path = any(write.path.startswith(p) for p in TEST_DIR_PREFIXES)
    min_segments = 3 if is_test_path else GLOB_BROADENING_MIN_SEGMENTS
    if (
        write.confidence >= GLOB_BROADENING_MIN_CONFIDENCE
        and len(parts) >= min_segments
    ):
        return "/".join(parts[:-1]) + "/**"
    return write.path
```

So `tests/e2e/checkout.spec.ts` (3 segments) now broadens to `tests/e2e/**`,
covering sibling spec files.

## Tests to add

In `tests/test_predictor.py`, append a new section:

```python
# --------------------------------------------------------------------------- #
# Env-file seed.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "prompt,should_seed",
    [
        ("Add Google OAuth via NextAuth.", True),
        ("Wire up Stripe checkout.", True),
        ("Add an Auth0 provider.", True),
        ("Refactor the dashboard sidebar.", False),
    ],
)
def test_env_seed_triggers(prompt, should_seed, tmp_path): ...
```

Aim for at least:
- 4 parametrized cases for `_env_seed`'s trigger regex
- 1 test for the Next.js `.env.local` augmentation
- 3 tests for `_sibling_pattern_seed`: positive (existing API dir), negative (no siblings), entity fallback
- 2 tests for multi-entity test scaffolding
- 2 tests for compiler `_to_allowed_path` shallow-test-path broadening (one test path, one non-test shallow path control)
- 1 integration test through `predict_writes` confirming all seeds compose

In `tests/test_compiler.py` (create if it doesn't exist) — focus the
broadening tests there for clarity. Use the existing `tests/conftest.py`
fixtures.

## Demo trace regression check

After your changes, **the live trace `demo-app/.acg/run_trace.json` must
not regress**. The current floor is **10 ALLOWED / 4 BLOCKED**. Improvement
is welcome and expected (PR 2 should turn at least 2 of the 4 BLOCKED into
ALLOWED — the env-file ones — when `make compile-gemma` and `make run-gemma`
are re-run; see "Verifying" below).

You don't have GX10 access, so you cannot re-run `make compile-gemma`
yourself. Instead, **write a deterministic regression test that hand-builds
a minimal `repo_graph` mimicking demo-app, runs the predictor with the
canned LLM stub, and asserts that for the `oauth` and `billing` tasks the
predicted_writes now include `.env.example`**. The human author will do the
live re-run.

## Branch / commit / PR conventions

- Branch: `predictor-coverage-extensions` from `main`
- Commits:
  ```
  predictor: add env-file seed for credential/provider tasks
  predictor: add sibling-pattern seed for "add API endpoint" tasks
  predictor: support multi-entity test scaffolding
  compiler: lower glob-broadening threshold for test paths
  tests: cover env/sibling/multi-entity/test-broadening (15 cases)
  ```
- PR title: `predictor: extend coverage to env files, sibling patterns, multi-entity tests`
- PR description: include a markdown table comparing pre/post predicted_writes for the 4 demo-app tasks.

## Acceptance gates

```bash
./.venv/bin/python -m pytest tests/ -q       # 51 existing + new tests pass (~66+)
./.venv/bin/ruff check acg/ tests/ benchmark/
```

PLUS the regression test in your PR must assert the post-change predictor
output includes `.env.example` for `oauth` and `billing` tasks.

## DO NOT

- Modify `acg/runtime.py` or anything in `viz/`.
- Modify the **existing** `_test_scaffold_seed` function body other than
  swapping single-entity for multi-entity. Track A's 9 tests must continue
  to pass byte-identical.
- Modify `demo-app/agent_lock.json` or `demo-app/.acg/run_trace.json`
  manually. The human author re-runs `make compile-gemma` and `make
  run-gemma` post-merge.
- Touch the `framework`/`pagerank`/`bm25`/`cochange` modules (those are
  PR 1's territory).
- Add new dependencies. All four extensions can be done with stdlib + the
  existing `pydantic` and `re`.

## When in doubt

- The existing 4 seed functions in `acg/predictor.py` are the template for
  yours: pure functions taking `(task, repo_graph, repo_root?)`, returning
  `list[PredictedWrite]`.
- `tests/test_predictor.py` shows the parametrize style and `tmp_path`
  pattern.
- The Track A `_test_scaffold_seed` (lines ~160-220 of `acg/predictor.py`)
  is the most recent and idiomatic example.
