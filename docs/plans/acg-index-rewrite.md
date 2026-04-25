# ACG deterministic index rewrite plan

## 1. Motivation

ACG exists to move multi-agent coordination work before execution. The compiler turns task prompts into an `agent_lock.json` whose `predicted_writes` and `allowed_paths` let the runtime fan out workers safely and block writes outside each task's contract. That contract is only as good as the static write-set predictor. Before this rewrite, `acg/predictor.py` had four seed channels: literal path mentions, symbol lookup through `symbols_index`, hint-to-path substring matches, and the Track A test-scaffold convention seed. Those signals are high precision, but they are sparse. They work when the prompt says `src/server/auth/config.ts` or names `authOptions`; they collapse when the task is greenfield.

The canonical failure is the `tests` task in `demo-app`: "Write end-to-end Playwright tests for the checkout flow." A conservative predictor can see the word "tests", but there is no existing `tests/e2e/checkout.spec.ts`, no checkout symbol, and no path mention. Track A improved this exact case by reading Playwright/Vitest/Jest/Pytest conventions, yet the same failure mode appears for "add a billing API", "add a Stripe webhook", "add a posts router", or "add a Rails invoice model". Humans infer the target path from framework conventions, identifier vocabulary, import structure, and change history; ACG needs deterministic approximations of those signals before asking any LLM to rank or expand candidates.

This PR introduces `acg.index`, a deterministic retrieval package that emits plausible paths even when the old seed set is empty. It deliberately does **not** modify `acg/predictor.py`; the public seam is `acg.index.aggregate(task, repo_root, repo_graph) -> list[PredictedWrite]`. The demo author can wire it into the existing LLM rerank pipeline after the hackathon without destabilizing the frozen runtime or demo artifacts.

The goal is not perfect semantic program understanding. The goal is a robust first retrieval ladder: framework grammar for greenfield files, symbol graph centrality for existing hotspots, BM25 over identifiers and paths for lexical matches, and ROSE-style co-change for second-pass expansion. This follows the same progression used by modern coding-agent retrieval systems: cheap lexical and structural signals first, graph expansion next, embeddings later.

## 2. Architecture

```text
TaskInput + repo_root + repo_graph
        │
        ├── framework indexer ── greenfield route/model/page/test-adjacent paths
        ├── pagerank indexer  ── file graph from definitions/references/imports
        ├── BM25 indexer      ── path + symbols + imports + first docstring line
        │
        └── first-pass fused seed set
                    │
                    ▼
            cochange indexer ── git association-rule expansion
                    │
                    ▼
          aggregate(): max confidence, concat reasons, sort, top-N
```

The public protocol is intentionally tiny:

```python
from pathlib import Path
from typing import Protocol

from acg.schema import PredictedWrite, TaskInput

class Indexer(Protocol):
    """A pure deterministic indexer: task + repo state -> predicted writes."""

    name: str

    def predict(
        self,
        task: TaskInput,
        repo_root: Path | None,
        repo_graph: dict,
    ) -> list[PredictedWrite]: ...
```

Every indexer is pure from the compiler's perspective: task plus local repository state in, `PredictedWrite` list out. Implementations may cache expensive indexes under `.acg/cache/`, but the cache key is derived from repository mtimes or git `HEAD`, so repeated runs remain deterministic for a fixed checkout.

`aggregate()` runs the default order `framework`, `pagerank`, `bm25`, then `cochange`. The first three indexers generate seeds independently from the prompt and repository. Co-change is a second-pass indexer; it expands whatever the first pass already found. Fusion is per-path:

| Field | Fusion rule |
| --- | --- |
| `path` | exact relative path key |
| `confidence` | maximum confidence produced by any indexer |
| `reason` | unique semicolon-joined reason strings, preserving first-seen order |
| order | descending confidence, then path for deterministic ties |
| cap | `top_n`, default 8 |

This keeps the return shape identical to the existing predictor's output (`list[PredictedWrite]`) while making provenance visible in the lockfile. A future LLM reranker can still keep, demote, or discard deterministic candidates, but it no longer starts from an empty list on greenfield prompts.

## 3. Module-by-module design

### `framework.py`

The framework indexer handles files that do not exist yet. It detects fingerprints by config files and graph paths:

| Framework | Fingerprints | Example predictions |
| --- | --- | --- |
| Next.js App Router | `next.config.{js,ts,mjs}`, `package.json` dependency | `src/app/api/billing/route.ts`, `app/settings/page.tsx`, `middleware.ts` |
| T3 stack | Next.js plus tRPC/Prisma paths or dependencies | `server/api/routers/billing.ts`, `prisma/schema.prisma` |
| Django | `manage.py`, Django in `pyproject.toml` | `<app>/views.py`, `<app>/serializers.py`, `<app>/urls.py`, `<app>/models.py` |
| Rails | `Gemfile` with Rails or `config/routes.rb` | `app/controllers/billing_controller.rb`, `app/models/invoice.rb` |
| Vite SPA | `vite.config.{js,ts,mjs}`, Vite dependency | `src/pages/Settings.tsx` |
| FastAPI | FastAPI dependency or imports in `main.py` | `app/routers/payments.py`, `app/api/payments.py` |
| Spring Boot | `pom.xml` containing Spring Boot | `src/main/java/.../controllers/BillingController.java` |

Prompts are parsed with small regular expressions for task roles (`api`, `page`, `layout`, `middleware`, `router`, `model`, `view`, `controller`, `route`) and an entity extractor. Confidence is `0.85` when both a framework fingerprint and verb/role match. This module intentionally does not try to inspect routing declarations deeply or generate every companion file. It covers high-frequency conventions that unblock cold tasks; deeper framework-specific reasoning belongs in later indexers or explicit planner rules.

### `pagerank.py`

The PageRank indexer ports the algorithmic idea from Aider's repository map rather than copying code. It extracts definitions and references, builds a directed file graph, and runs personalized PageRank:

1. Nodes are relative file paths.
2. Definitions come from repo graph `exports`/`symbols`, lightweight regexes, and best-effort `tree-sitter-languages` parsing for TypeScript, JavaScript, Python, Go, and Java.
3. Import edges connect referrers to imported local files with extra weight.
4. Identifier references connect a file to another file that defines the referenced symbol.
5. Prompt tokens fuzzy-match symbol names via `rapidfuzz`. Matched definer files get boosted in the personalization vector.
6. A small pure-Python weighted PageRank loop ranks files without requiring SciPy.

The emitted reason includes the PageRank rank and top symbol matches. Confidence follows the requested `min(0.9, rank * 1000)` shape, with a cap for unmatched files when there are explicit prompt symbol matches so generic graph centrality does not outrank the personalized target.

The index is cached as a pickle under `.acg/cache/pagerank-<signature>.pickle`, where the signature includes file paths, mtimes, and sizes. The implementation caps indexing at 50,000 files and skips heavy directories through shared repo-file traversal. If a native tree-sitter wheel is unavailable, regex and graph metadata still produce useful results.

This module covers existing-file hotspots and symbol-adjacent edits. It does not understand dynamic dispatch, generated source, or full SCIP-level symbol resolution.

### `bm25.py`

The BM25 indexer implements the deterministic lexical baseline described in SWE-bench retrieval work and echoed by coding-assistant systems. Each file's document is:

```text
path_tokens + exported_symbols + imports + docstring_first_line
```

Tokens are split on paths, snake_case, and camelCase, lowercased, and lightly expanded with domain synonyms such as `navigation -> sidebar/menu` and `database -> prisma/db`. Query tokens are built from task id, prompt, and `hints.touches`.

`rank_bm25.BM25Okapi` scores the corpus. Very small corpora can produce negative IDF scores, so the implementation floors the retrieval signal at exact token-overlap count before ranking. Confidence is `tanh(score / 5.0)`, keeping it in `[0, 1)`.

BM25 covers lexical matches for existing files, especially paths like `src/app/settings/page.tsx`, symbols like `getCurrentUser`, imports like `PrismaClient`, and docstrings in Python/Ruby/Java. It does not infer brand-new files unless the path already appears in the repository or another indexer supplies the candidate.

### `cochange.py`

The co-change indexer implements a ROSE-style association rule pass over git history. It runs:

```bash
git log --name-only --pretty=format:COMMIT --no-merges
```

and parses commits into sets of changed files. For every pair in a commit it increments a sparse co-occurrence counter and tracks how many commits touched each seed file. At query time it receives the first-pass seed set from `aggregate()`, looks up each seed row, and emits files with co-change count at least 3, ranked by `count / commits_with_seed`. Confidence is capped at `0.8` because change history is a strong but repo-local signal.

The cache lives at `.acg/cache/cochange.pickle` and is keyed by `git rev-parse HEAD`. This module covers companion edits such as implementation plus tests or route plus registration file. It does not generate initial seeds; if the first pass finds nothing, co-change returns nothing.

### `aggregate.py`

The aggregator is deliberately boring. It is the compatibility layer that makes all indexers usable as a single predictor seed source. It accepts a custom indexer sequence for tests and future experimentation; otherwise it instantiates framework, PageRank, BM25, and then co-change. The co-change pass uses the fused first-pass paths as seeds rather than calling co-change with an empty prompt.

## 4. Citations

| Claim | Source |
| --- | --- |
| Repository maps can be built from definitions/references, weighted file graphs, and personalized PageRank seeded by prompt-symbol matches. | Aider repomap walkthrough, ["Aider is AI pair programming in your terminal"](https://aider.chat/2023/10/22/repomap.html), and Aider's MIT-licensed [`repomap.py`](https://github.com/paul-gauthier/aider). |
| Association-rule mining over version history can predict related files from a partial change set using support/confidence. | Thomas Zimmermann et al., ["Mining Version Histories to Guide Software Changes"](https://thomas-zimmermann.com/publications/files/zimmermann-tse-2005.pdf), IEEE TSE 2005 (ROSE). |
| BM25 over file paths, symbols, and natural-language text is a reproducible deterministic retrieval baseline for software-engineering tasks. | SWE-bench paper and benchmark materials: [SWE-bench](https://www.swebench.com/) and ["SWE-bench: Can Language Models Resolve Real-World GitHub Issues?"](https://arxiv.org/abs/2310.06770). |
| Modern code-agent retrieval ladders start with identifier/lexical match, then sparse retrieval, then graph or learned retrieval. | RACG survey reference in the project brief, arXiv:2510.04905, and Sourcegraph Cody architecture discussions such as [Cody context filters and search](https://sourcegraph.com/docs/cody). |
| Framework conventions are high-precision priors for greenfield path prediction even without a formal universal library. | Framework docs: [Next.js App Router route handlers](https://nextjs.org/docs/app/building-your-application/routing/route-handlers), [Django views](https://docs.djangoproject.com/en/stable/topics/http/views/), [Rails routing/controllers](https://guides.rubyonrails.org/routing.html), [FastAPI bigger applications](https://fastapi.tiangolo.com/tutorial/bigger-applications/), [Spring Boot REST controllers](https://spring.io/guides/gs/rest-service/). |

## 5. Roadmap

This PR is scaffolding, not the final predictor. The next steps should be prioritized by expected recall gain per implementation risk:

1. **Wire `aggregate()` into `predictor.py` behind a flag.** Keep existing seeds and LLM rerank intact, but pass deterministic aggregate candidates as the seed list. This is intentionally outside this PR because the demo artifacts are frozen and the human author requested no predictor changes.
2. **Improve framework grammars with repo-specific registration files.** T3 routers often need `root.ts`; Rails controllers usually imply routes and tests; Django REST Framework implies serializers. These should be encoded as small companion-file maps once the first public API settles.
3. **Add a local embeddings indexer.** A vector store can capture semantic matches like "checkout" to "billing" or "subscription", but this PR intentionally avoids `openai`, `voyageai`, `lancedb`, `transformers`, or any remote model dependencies.
4. **Add HyDE-style deterministic pseudo-documents only after embeddings exist.** HyDE without embeddings is mostly prompt templating; with embeddings it could improve greenfield recall by generating canonical implementation vocabulary.
5. **Adopt SCIP or language-server indexes for stronger symbol resolution.** Tree-sitter plus regex is fast and dependency-light, but SCIP would give cross-language definitions, references, and package metadata comparable to production code search.
6. **Mine test naming conventions.** Track A covers test scaffold defaults. A broader test indexer should learn repo-local pairs like `lib/request.js -> test/req.*.js` and `src/foo.py -> tests/test_foo.py`.
7. **Persist benchmark history.** `benchmark/results.json` is written for local comparison. CI can later upload or diff it to prevent regressions in recall@5 while keeping the wall-clock budget below 90 seconds per task.

The acceptance benchmark in this PR is intentionally modest: fixture datasets for `demo-app`, `create-t3-app`, and Express, evaluated with recall@5 and precision@5. The mean recall target is 0.6. That threshold ensures the indexers produce meaningful seeds across three very different repository shapes without overfitting the compiler or runtime.
