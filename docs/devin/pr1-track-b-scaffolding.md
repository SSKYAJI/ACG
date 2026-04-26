# PR 1 — Track B: `acg/index/` deterministic indexer scaffolding

> **Copy everything below this line into Devin verbatim. Edits welcome but
> please preserve the file/branch/acceptance specs.**

---

## Project context

You are working on **`cognition`**, a Python+TypeScript repo whose core
product is **ACG (Agent Context Graph)** — a static analyzer that compiles a
list of LLM coding tasks into an `agent_lock.json` lockfile. The lockfile
records, per task, the *predicted file write-set* and an `allowed_paths`
glob list. A separate runtime (`acg/runtime.py`) executes the lockfile by
fanning out worker LLMs, validating each proposed write against
`allowed_paths`, and recording a `run_trace.json` for replay in a React
visualizer.

The **predictor** (`acg/predictor.py::predict_writes`) is the heart of ACG.
Today it is a thin LLM-rerank glued on top of three deterministic seed
functions:

1. `_static_seed` — regex for explicit file mentions in the prompt
2. `_symbol_seed` — camelCase token → file via repo graph's `symbols_index`
3. `_topical_seed` — `hints.touches` substring match against existing files
4. `_test_scaffold_seed` — *(Track A, just shipped)* test framework
   convention detection

When seeds 1-3 collapse to `[]` (any greenfield task: "add a billing API",
"write tests", "add a Stripe webhook"), the LLM is told to be conservative
and refuses to speculate, so `predicted_writes` ends up empty.

## Your goal

Replace the weak deterministic seed layer with a **rich indexer package**
that produces 5-10 plausible predicted paths even for cold/greenfield tasks,
inspired by Aider's repomap, Sourcegraph SCIP, and the academic literature
on file-set prediction. **You do not modify `acg/predictor.py`** — you
create a new public function `acg.index.aggregate(task, repo_root,
repo_graph) -> list[PredictedWrite]` that the human author will wire in
post-demo.

## Reference research (from a Perplexity Sonar pass we ran)

Read this section in full before starting. It tells you what to lift from
where.

### Aider repomap (`acg/index/pagerank.py`)

Aider's `aider/repomap.py` (MIT, https://github.com/paul-gauthier/aider) uses
**tree-sitter via `py-tree-sitter-languages`** to extract symbol definitions
and references per file, builds a NetworkX directed graph (edge weight = #
cross-file references), then runs **personalized PageRank** with the
personalization vector seeded by tokens from the user's prompt that fuzzy-
match symbol names. Top-K files by rank are emitted. Algorithm walkthrough:
https://aider.chat/2023/10/22/repomap.html. Port the algorithm; do not
copy/paste GPL'd code — Aider is MIT but write your own implementation
directly from the algorithm description.

### Sourcegraph Cody (informational only)

Cody combines **BM25 over identifiers** + **SCIP symbol-graph traversal** +
deprecated embeddings. We're not running SCIP, but the BM25 channel
inspires `bm25.py` below.

### Sweep / Greptile (informational only)

Sweep's open source code (Apache-2.0,
https://github.com/sweepai/sweep) does lexical-first → vector-second →
agentic. We mirror only the lexical-first.

### Academic anchors

- **Zimmermann et al. 2005 — ROSE.** Association rule mining over git commit
  history: given a partial change set, predict additional files via support
  + confidence thresholds. PDF:
  https://thomas-zimmermann.com/publications/files/zimmermann-tse-2005.pdf
- **SWE-bench retrieval baseline.** BM25 over file paths + symbol names +
  docstrings; reproducible deterministic baseline.
- **RACG survey (arXiv:2510.04905, 2025).** Confirms identifier match → BM25
  → dense embedding → graph-augmented as the canonical retrieval ladder.

### Framework conventions

There is no published library of framework path conventions. Build a Python
dict mapping `(framework_fingerprint, task_verb, entity_name) → [path
templates]`. Cover Next.js App Router (T3), Django, Rails, Vite SPA,
FastAPI, Spring Boot. Each ~10 lines of mappings.

## Deliverables — file by file

All paths are absolute from repo root.

### `acg/index/__init__.py`

Public API surface only. Export `aggregate`, the `Indexer` Protocol, and the
return dataclass. Re-export submodules so `from acg.index import framework,
pagerank, bm25, cochange` works.

### `acg/index/types.py`

```python
from typing import Protocol
from acg.schema import PredictedWrite, TaskInput

class Indexer(Protocol):
    """A pure deterministic indexer: task + repo state -> predicted writes."""
    name: str  # "framework" | "pagerank" | "bm25" | "cochange"

    def predict(
        self,
        task: TaskInput,
        repo_root: Path | None,
        repo_graph: dict,
    ) -> list[PredictedWrite]: ...
```

### `acg/index/framework.py`

Detect the framework via fingerprint files (`next.config.{js,ts}`,
`manage.py`, `Gemfile`, `vite.config.{js,ts}`, `pom.xml`, `pyproject.toml`,
etc.). For each detected framework, apply a hard-coded path grammar:

- **Next.js App Router**:
  - "add `<x>` API endpoint" → `app/api/<x>/route.ts` (or `src/app/api/<x>/route.ts` if `src/` exists)
  - "add `<x>` page" → `app/<x>/page.tsx`
  - "add `<x>` layout" → `app/<x>/layout.tsx`
  - "add middleware" → `middleware.ts` (root)
- **T3 stack** (Next.js + tRPC + Prisma):
  - "add `<x>` router" → `server/api/routers/<x>.ts`
  - "add `<x>` model" → `prisma/schema.prisma`
- **Django**:
  - "add `<x>` view/endpoint" → `<app>/views.py`, `<app>/serializers.py`,
    `<app>/urls.py`
  - "add `<x>` model" → `<app>/models.py`
- **Rails**:
  - "add `<x>` controller" → `app/controllers/<x>_controller.rb`
  - "add `<x>` model" → `app/models/<x>.rb`
- **FastAPI**:
  - "add `<x>` route" → `app/api/<x>.py` or `app/routers/<x>.py`
- **Spring Boot**:
  - "add `<x>` controller" → `src/main/java/.../controllers/<X>Controller.java`

Confidence 0.85 when both fingerprint and verb match. Implementation should
be a dict-of-dicts plus a small dispatch function. ~250 lines total.

### `acg/index/pagerank.py`

Tree-sitter symbol graph + personalized PageRank.

- Use `tree-sitter-languages` for parser binaries (TypeScript, JavaScript,
  Python, Go, Java initially).
- Build a directed graph G: nodes = files (relative paths). For each
  reference of a symbol defined in another file, add an edge `(referrer →
  definer, weight += 1)`.
- Personalization vector: tokens from the task prompt fuzzy-match against
  symbol names (use `rapidfuzz` ≥ 0.85 ratio). Files defining matched
  symbols get `1.0`; everything else gets `1 / |files|`.
- Run `networkx.pagerank(G, alpha=0.85, personalization=p)`.
- Emit top-N (default 8) files with confidence = `min(0.9, rank * 1000)`
  clamped, reason = "personalized PageRank rank #k, top symbol matches:
  ...".
- Cache the graph as a `pickle` keyed on file mtimes under `.acg/cache/`.

Performance budget: <2s on a 50k-file repo. If NetworkX is too slow, fall
back to `igraph` or restrict to files within 2 hops of prompt-matched
symbols.

### `acg/index/bm25.py`

`rank_bm25.BM25Okapi` over the corpus of `path_tokens + exported_symbols +
imports + docstring_first_line`. Tokenize on `[a-zA-Z][a-zA-Z0-9]*`,
lowercase, split camelCase and snake_case into pieces. At query time,
tokenize the task prompt the same way, query, return top-N with confidence
= `tanh(score / 5.0)` so it stays in [0, 1).

### `acg/index/cochange.py`

ROSE-style association rule mining. Run `git log --name-only --pretty=format:
COMMIT --no-merges` once and parse into a list of commit → set-of-files.
Build a sparse co-occurrence count matrix. At query time, given a candidate
seed file (from any other indexer), look up its row, return files with
co-change-count ≥ 3 ranked by `count / commits_with_seed`. Cache the matrix
under `.acg/cache/cochange.pickle` keyed on `git rev-parse HEAD`.

This indexer is **second-pass**: it expands a seed set, it doesn't generate
seeds from a prompt. Aggregator should call it after the first three
indexers.

### `acg/index/aggregate.py`

```python
def aggregate(
    task: TaskInput,
    repo_root: Path | None,
    repo_graph: dict[str, Any],
    indexers: Sequence[Indexer] | None = None,
    top_n: int = 8,
) -> list[PredictedWrite]:
    """Run every indexer, fuse their outputs, return top-N predictions."""
```

Fusion strategy: per path, take `max(confidence)` across indexers and
concatenate reasons (`"; "`-joined). Sort by descending confidence. Cap at
`top_n`.

Default indexer order: `framework`, `pagerank`, `bm25`, `cochange`.

### `tests/index/__init__.py` and per-module test files

- `tests/index/test_framework.py` — at least 6 tests covering Next.js,
  Django, Rails, FastAPI; positive matches and a no-fingerprint negative.
- `tests/index/test_pagerank.py` — at least 3 tests using a fixture repo
  under `tests/fixtures/tiny_repo/` (already exists). Verify
  centrality picks up hotspots and personalization steers ranking.
- `tests/index/test_bm25.py` — at least 4 tests including identifier
  matching and tie-breaking.
- `tests/index/test_cochange.py` — at least 3 tests using a fixture git repo
  built in `tmp_path`.
- `tests/index/test_aggregate.py` — at least 3 tests verifying fusion
  semantics: max-confidence, reason concat, top-N cap.

### `benchmark/predictor_eval.py`

A standalone script measuring **recall@5** and **precision@5** of the
deterministic indexer set on a fixture dataset.

Fixture dataset under `benchmark/fixtures/`:

- `demo-app-tasks.json` — 8 tasks against the existing `demo-app/`. Ground
  truth is the **actual** files written by the LLM workers in the current
  `demo-app/.acg/run_trace.json` (i.e., the union of `proposals[].file`
  including BLOCKED ones, since blocked = predictor missed it).
- `t3-app-tasks.json` — 8 tasks against a clone of `create-t3-app`. Use
  GitHub at `t3-oss/create-t3-app` shallow-cloned to a tmp dir at runtime.
- `express-api-tasks.json` — 8 tasks against
  `expressjs/express` shallow-cloned. Tasks should target Express
  middleware, routes, and tests.

Each task fixture: `{id, prompt, hints, ground_truth_paths}`.

Output:

```
$ python benchmark/predictor_eval.py
                       recall@5   precision@5   wall_s
demo-app                  0.83          0.62      0.84
t3-app                    0.71          0.55      1.42
express                   0.66          0.50      1.18
                          ----          ----      ----
mean                      0.73          0.56      1.15
```

Print a markdown table to stdout; also write `benchmark/results.json` for
historical comparison.

### `docs/plans/acg-index-rewrite.md`

A 5-page architecture spec. Structure:

1. **Motivation** — current predictor's seed-collapse failure mode, with
   the `tests` task in `demo-app` as the canonical example.
2. **Architecture** — diagram of the indexer pipeline, the `Indexer`
   protocol, the aggregator's fusion strategy.
3. **Module-by-module** — what each indexer covers, what it does NOT cover,
   reference algorithm.
4. **Citations** — every algorithmic claim links to a primary source: Aider
   blog, Zimmermann TSE 2005 PDF, RACG survey arXiv, SWE-bench paper, etc.
5. **Roadmap** — what's NOT in this PR (embeddings indexer, vector store,
   HyDE retrieval) and the priority order for future work.

Use markdown tables, code blocks for the protocol definition, and inline
hyperlinks. ~3000 words.

## Dependencies to add to `pyproject.toml`

```toml
dependencies = [
    # ... existing ...
    "tree-sitter-languages>=1.10",
    "networkx>=3.2",
    "rank-bm25>=0.2.2",
    "rapidfuzz>=3.6",
]
```

(Already present: `pydantic`, `httpx`, `typer`, `rich`, `pytest`,
`ruff`. Don't add anything else.)

## Branch / commit / PR conventions

- Branch from `main`: `git checkout -b track-b-index-scaffolding`
- Commit style: imperative mood, scope prefix.
  ```
  index: add framework-convention indexer
  index: add personalized-PageRank indexer
  index: add BM25 over identifiers + paths
  index: add ROSE-style co-change mining
  index: add aggregator + Indexer Protocol
  benchmark: add predictor_eval.py with 3 fixture repos
  docs: add acg-index-rewrite plan with citations
  ```
- One PR titled `Track B: deterministic indexer scaffolding`. PR description
  should reproduce the benchmark output table and link the plan doc.

## Acceptance gates (must all pass before opening the PR)

```bash
./.venv/bin/python -m pytest tests/ -q       # 51 existing + new tests pass
./.venv/bin/ruff check acg/ tests/ benchmark/
./.venv/bin/python benchmark/predictor_eval.py  # benchmark runs to completion
```

`recall@5` mean across the three fixture repos should be ≥ **0.6** for the
PR to be considered acceptable. Below that, iterate on the indexer
implementations until you cross the bar.

## DO NOT

- Modify `acg/predictor.py` (the human author wires `acg.index.aggregate`
  in post-demo).
- Modify `acg/runtime.py`, `acg/cli.py`, anything in `viz/`, or anything
  under `demo-app/`.
- Regenerate `demo-app/.acg/run_trace.json` or `agent_lock.json` — they are
  frozen demo artefacts.
- Add LLM calls to any indexer. The whole point is that they're
  deterministic.
- Add embedding-model dependencies (`voyageai`, `openai`, `lancedb`,
  `transformers`). That's a follow-up PR.
- Spend more than 90 seconds wall-clock per benchmark task in
  `predictor_eval.py`.

## When in doubt

- Read `acg/predictor.py` end-to-end to understand the existing 4-seed
  pipeline. Your aggregator should match its output shape (`list[PredictedWrite]`).
- Read `acg/schema.py` for `TaskInput`, `PredictedWrite`, `TaskInputHints`.
- Read `tests/test_predictor.py` for the test style (parametrize, `tmp_path`
  fixtures, StubLLM pattern). Mirror that style.
- The existing `demo-app/.acg/run_trace.json` is an excellent ground-truth
  source — every `proposals[].file` is a real Gemma write attempt.

Good luck.
