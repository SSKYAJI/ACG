> **Goal:** add a 5th deterministic indexer to the `acg.index`
> aggregator — local sentence-transformer embeddings — to lift recall
> on greenfield prompts where no symbol/path/identifier match exists
> ("checkout" → "billing" / "subscription" / "payment").

---

## Project context

You are working on **`cognition`** — a Python+TypeScript repo whose product
is **ACG (Agent Context Graph)**. PR 1 shipped `acg/index/` with four
indexers — `framework`, `pagerank`, `bm25`, `cochange` — fused by
`acg.index.aggregate` into a top-N seed set fed to the predictor.
Today's mean recall@5 across `demo-app`, `t3-app`, and `express`
fixtures is **0.65** (see `benchmark/results.json`).

Per the roadmap in `docs/plans/acg-index-rewrite.md` §5, the highest-
expected-gain follow-up is a **local embeddings indexer** — purely
on-machine sentence-transformer encoding of file documents, with
cosine similarity to the prompt. The plan explicitly rules out remote
APIs (no `openai`, no `voyageai`, no `lancedb`). Your job is to ship
that indexer.

## What "local embeddings" means here

- Use `sentence-transformers` with a small model
  (`all-MiniLM-L6-v2`, 22 MB on disk, ~80 MB RAM, 384 dim).
- Encode **the same document corpus the BM25 indexer uses** — file path
  tokens, exported symbols, imports, first docstring line. Reusing the
  corpus keeps fairness across indexers.
- Cache encodings to `<repo>/.acg/cache/embeddings-<signature>.pickle`
  with the same mtime+size signature scheme as `pagerank`'s cache.
- At query time encode the task prompt + entity tokens and rank by
  cosine similarity.

## Repo state to assume

- `main` contains PR 1-4 + `init-graph` + (likely) PR 6's MCP wrapper.
  Your branch only touches `acg/index/`, `tests/index/`,
  `pyproject.toml`, `benchmark/`, and `docs/plans/`.
- `acg/index/util.py::clamp_confidence` is the canonical clamp helper
  for indexer outputs.
- `acg/index/types.py::Indexer` is the Protocol every indexer
  implements. Your class must conform.
- `acg/index/aggregate.py` orchestrates first-pass indexers and a
  second-pass cochange. You add embeddings to the **first pass** but
  behind a **default-off feature flag** so existing CI numbers don't
  shift unexpectedly.

## Deliverables — file by file

### 1. `pyproject.toml` — add an optional dependency group

Add **only this** group; do not modify existing entries:

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0,<9.0", "ruff>=0.5,<1.0"]
mcp = ["fastmcp>=2.0,<3.0"]
index-vector = [
    "sentence-transformers>=2.7,<3.0",
    "numpy>=1.26,<3.0",
]
```

The `index-vector` extra is opt-in for two reasons:

- `sentence-transformers` brings in `torch` (~700 MB). Hackathon CI
  cannot afford that on every run.
- The aggregator transparently degrades when the extra is absent (see
  the lazy-import pattern in `acg/index/pagerank.py` for prior art).

Update the comment that documents extras: "MCP server (FastMCP) is
shipped under the `mcp` extra; the local-embeddings indexer ships under
the `index-vector` extra. See `docs/MCP_SERVER.md` and
`docs/plans/acg-index-rewrite.md`."

### 2. `acg/index/embeddings.py`

A new ~250-LOC module implementing `EmbeddingsIndexer`.

```python
"""Local sentence-transformer embeddings indexer.

Default-off. Activated by passing the indexer instance into
`aggregate(..., indexers=[..., EmbeddingsIndexer()])` or by setting
`ACG_INDEX_EMBEDDINGS=1` in the environment (see aggregate.py).
"""

from __future__ import annotations

import os
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acg.schema import PredictedWrite, TaskInput
from .types import Indexer
from .util import clamp_confidence

EMBEDDINGS_MODEL_NAME = os.environ.get(
    "ACG_INDEX_EMBEDDINGS_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
DEFAULT_TOP_N = 8
COSINE_FLOOR = 0.18      # below this we treat as noise
CACHE_DIRNAME = "embeddings"


@dataclass
class _Document:
    path: str
    text: str


class EmbeddingsIndexer:
    name = "embeddings"

    def __init__(
        self,
        *,
        top_n: int = DEFAULT_TOP_N,
        model_name: str = EMBEDDINGS_MODEL_NAME,
        cosine_floor: float = COSINE_FLOOR,
    ) -> None:
        self._top_n = top_n
        self._model_name = model_name
        self._cosine_floor = cosine_floor

    def predict(
        self,
        task: TaskInput,
        repo_root: Path | None,
        repo_graph: dict[str, Any],
    ) -> list[PredictedWrite]:
        # Lazy imports — extra is optional.
        try:
            import numpy as np
            from sentence_transformers import SentenceTransformer
        except ImportError:
            return []

        if repo_root is None:
            return []

        documents = self._build_documents(repo_graph)
        if not documents:
            return []

        model = self._load_model(SentenceTransformer)
        doc_vectors = self._encode_corpus(model, documents, np, repo_root)
        query_vector = self._encode_query(model, task, np)
        scores = self._cosine_similarity(query_vector, doc_vectors, np)

        ranked = sorted(
            zip(documents, scores, strict=True),
            key=lambda pair: (-float(pair[1]), pair[0].path),
        )
        out: list[PredictedWrite] = []
        for doc, score in ranked[: self._top_n * 2]:
            score_f = float(score)
            if score_f < self._cosine_floor:
                continue
            out.append(
                PredictedWrite(
                    path=doc.path,
                    confidence=clamp_confidence((score_f + 1.0) / 2.0 * 0.85),
                    reason=(
                        f"Local embedding cosine={score_f:.2f} between task prompt "
                        f"and {doc.path} document tokens."
                    ),
                )
            )
            if len(out) >= self._top_n:
                break
        return out

    # ----- internals (see implementation notes below) ---------------------
    def _build_documents(self, repo_graph: dict[str, Any]) -> list[_Document]: ...
    def _load_model(self, factory): ...
    def _encode_corpus(self, model, documents, np, repo_root): ...
    def _encode_query(self, model, task, np): ...
    def _cosine_similarity(self, query, matrix, np): ...
```

#### Implementation notes (you write the bodies)

**`_build_documents`**: read `repo_graph["files"]` for path/imports/
exports/symbols and concatenate into one string per file. Mirror the
tokenization used by `acg/index/bm25.py` so the corpora match. **Skip
files that produce empty documents**.

**`_load_model`**: `factory(self._model_name)`. Cache the instance on
`self._model` after the first call so repeated `predict()` invocations
on the same indexer don't re-download. The hackathon constraint is
"first call may take 30s on cold disk; subsequent calls < 1s."

**`_encode_corpus`**: Build a cache key from the sorted list of
(path, len(text)) tuples — this matches the determinism semantics of
`acg/index/pagerank.py`'s cache. Store the encoded `np.ndarray` and
the document order under `<repo_root>/.acg/cache/embeddings/<sig>.pkl`.
On cache hit, return the array. On cache miss, call
`model.encode(texts, normalize_embeddings=True, show_progress_bar=False)`
and write the cache atomically (write to a temp file, rename).

**`_encode_query`**: encode `task.prompt` (and, if `task.hints.touches`
is set, append `" " + " ".join(task.hints.touches)`) as a single string.
`normalize_embeddings=True` so cosine = dot product.

**`_cosine_similarity`**: with both sides L2-normalized,
`matrix @ query_vector` gives the cosine vector. Return as a Python
list[float] for downstream sorting (you can keep np arrays; just be
explicit).

**Cache invalidation**: caches keyed on `(model_name, sig)` so changing
the model invalidates automatically. Add a 7-day TTL: ignore cache
files older than `time.time() - 7 * 24 * 3600`.

#### Confidence formula

The conventional cosine-to-confidence map is `(cos + 1) / 2`, which
maps [-1, 1] → [0, 1]. We additionally cap at 0.85 because embeddings
are a softer signal than framework conventions (0.85 ceiling) or
explicit symbol matches (0.85 in symbol_seed). This keeps embeddings
from out-ranking high-precision deterministic seeds.

### 3. `acg/index/__init__.py` — export

Append `embeddings` to the imports and `__all__`:

```python
from . import aggregate as aggregate_module
from . import bm25, cochange, embeddings, framework, pagerank
from .aggregate import aggregate
from .embeddings import EmbeddingsIndexer
from .types import Indexer

__all__ = [
    "EmbeddingsIndexer",
    "Indexer",
    "PredictedWrite",
    "aggregate",
    "aggregate_module",
    "bm25",
    "cochange",
    "embeddings",
    "framework",
    "pagerank",
]
```

### 4. `acg/index/aggregate.py` — wire the env-flag opt-in

Modify **only** `_default_indexers()`:

```python
def _default_indexers() -> list[Indexer]:
    indexers: list[Indexer] = [FrameworkIndexer(), PageRankIndexer(), BM25Indexer()]
    if os.environ.get("ACG_INDEX_EMBEDDINGS") == "1":
        try:
            from .embeddings import EmbeddingsIndexer
            indexers.append(EmbeddingsIndexer())
        except ImportError:
            # sentence-transformers not installed — silently skip.
            pass
    return indexers
```

Add the necessary `import os` at the top of the file. Do not change
the function signature, the cochange second-pass logic, or the fusion
math.

### 5. `tests/index/test_embeddings.py`

A new test module with at least 5 tests. Critically, **none of them
may import `sentence_transformers` or `torch`**; everything must be
patched. Use the `monkeypatch.setitem(sys.modules, ...)` pattern from
`tests/test_mcp.py` (PR 6) as the template.

```python
"""Embeddings indexer tests.

Patches the sentence-transformers import so the optional dep is not
required in CI. Verifies cache behaviour, cosine ranking, and graceful
degradation.
"""

import sys
from types import ModuleType
from unittest.mock import MagicMock
import pytest

# Tests:
# 1. predict() returns [] when sentence_transformers is absent.
# 2. predict() returns [] when repo_root is None.
# 3. predict() honours the cosine_floor (mock model returns mostly
#    near-zero scores; assert filtered).
# 4. encoding is cached by signature: second call hits the pickle on
#    disk and does NOT call model.encode again on the corpus.
# 5. confidence is clamped to [0, 0.85].
```

For the patched model, return deterministic numpy arrays:

```python
def fake_encode(texts, normalize_embeddings, show_progress_bar):
    # Map text → fixed vector using a hash so tests are deterministic.
    import numpy as np
    rng = np.random.default_rng(0)
    return rng.standard_normal((len(texts), 16)).astype("float32")
```

Then assert the indexer rejects scores below `cosine_floor` and ranks
the rest descending.

### 6. `benchmark/predictor_eval.py` — add an embeddings ablation

Modify `main()` to run **two** evaluations per dataset and print a
combined markdown table:

```python
def main() -> None:
    base_results: dict[str, dict[str, float]] = {}
    embed_results: dict[str, dict[str, float]] = {}
    for name in ("demo-app", "t3-app", "express"):
        base_results[name] = evaluate_dataset(name, indexers=None)
        embed_results[name] = evaluate_dataset(
            name,
            indexers=_indexers_with_embeddings(),
        )
    payload = {"base": base_results, "with_embeddings": embed_results}
    RESULTS_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(_markdown(payload))
```

`_indexers_with_embeddings()` constructs the default list plus
`EmbeddingsIndexer()`. If `sentence_transformers` is not importable,
fall back to the base list and print a `# embeddings extra not installed`
note instead of crashing.

`evaluate_dataset` gains an optional `indexers` keyword forwarded to
`aggregate(...)`. **Do not** change the wall-time budget guard (90s per
task) — embeddings should easily fit within it after the first warm-up
encode.

`_markdown` should print **two** tables (Base / With embeddings) and a
`Δrecall@5` row computed per-dataset. Acceptable shape:

```
## Base (framework + pagerank + bm25 + cochange)

| dataset | recall@5 | precision@5 | wall_s |
| --- | ---: | ---: | ---: |
| demo-app | 0.73 | 0.32 | 0.26 |
| ... |

## With embeddings (+ EmbeddingsIndexer, ACG_INDEX_EMBEDDINGS=1)

| dataset | recall@5 | precision@5 | wall_s | Δrecall@5 |
| --- | ---: | ---: | ---: | ---: |
| demo-app | 0.81 | 0.30 | 1.42 | +0.08 |
| ... |
```

Update `benchmark/results.json` schema to the wrapped form
`{"base": {...}, "with_embeddings": {...}}` (re-running the eval will
overwrite it). Add a one-paragraph note at the top of `benchmark/README.md`
(create the file if it does not exist) explaining the schema change.

### 7. `docs/plans/acg-index-rewrite.md` — update §5 roadmap

Mark item 3 (local embeddings indexer) as **shipped** with a small
inline note:

```markdown
3. ~~Add a local embeddings indexer.~~ **Shipped in PR 7
   (`acg/index/embeddings.py`)** behind the optional `index-vector`
   extra and the `ACG_INDEX_EMBEDDINGS=1` env flag.
```

Do NOT rewrite the rest of the plan or change §1-§4. Only this single
roadmap bullet.

## Performance budget

The benchmark must still complete in ≤ 90 seconds wall-clock per
dataset. Concretely:

- First run on a cold disk: encoding the demo-app corpus (16 files)
  should take < 5s; t3-app and express are larger and may take 30-45s.
- Second run on a warm cache: < 2s per dataset.

If your sandbox cannot install `sentence-transformers` (download
timeout), document that in the PR description and rely on the
patched-model unit tests + the base benchmark numbers. The human
author re-runs the live benchmark on their workstation post-merge.

## Branch / commit / PR conventions

- Branch from `main`: `git checkout -b index-embeddings-layer`
- Commits:
  ```
  pyproject: add optional [index-vector] dep group
  index: add EmbeddingsIndexer with on-disk cache and graceful degradation
  index: opt-in embeddings via ACG_INDEX_EMBEDDINGS env flag in aggregate()
  benchmark: add base-vs-embeddings ablation to predictor_eval.py
  tests: cover EmbeddingsIndexer without requiring the index-vector extra (5 cases)
  docs: mark local-embeddings indexer as shipped in acg-index-rewrite plan
  ```
- PR title: `index: add local-embedding indexer (default-off, +Δrecall@5)`
- PR description: include both benchmark tables (Base vs. With
  embeddings) and the Δrecall@5 column. Quote a single line summary
  like "+0.07 mean recall@5 across the three fixture repos at +1.1s mean
  wall-clock per dataset (warm cache)."

## Acceptance gates

```bash
# Mandatory (no extra installed):
./.venv/bin/python -m pytest tests/ -q          # all existing + 5 new tests pass
./.venv/bin/ruff check acg/ tests/ benchmark/
./.venv/bin/python benchmark/predictor_eval.py   # base table populates;
                                                  # with-embeddings table empty / skipped
                                                  # with a printed note
ACG_INDEX_EMBEDDINGS=1 ./.venv/bin/python -c "from acg.index import aggregate; print('ok')"
# ↑ should print 'ok' (graceful degradation when the extra is absent).

# Optional (with extra installed):
pip install -e '.[index-vector]'
./.venv/bin/python benchmark/predictor_eval.py
# ↑ both tables populate; Δrecall@5 must be ≥ 0 on at least 2 of the 3 fixtures.
```

The "Δrecall@5 ≥ 0 on ≥ 2/3 fixtures" gate is the correctness floor —
embeddings should never _worsen_ recall on the existing fixtures. If
your run shows a regression on demo-app or t3-app, iterate on the
cosine_floor / confidence-cap / corpus-tokenization until the gate
passes.

## DO NOT

- Modify any other indexer (`framework.py`, `pagerank.py`, `bm25.py`,
  `cochange.py`, `aggregate.py` beyond `_default_indexers()`).
- Modify `acg/predictor.py`. The predictor calls
  `acg.index.aggregate(...)` with the default indexer list; the env
  flag is the entire user-facing toggle.
- Add `sentence-transformers` to top-level `dependencies`. It MUST
  remain in `[project.optional-dependencies] index-vector`.
- Re-encode the corpus on every `predict()` call. Cache or fail (return
  []) — never block the predictor pipeline on a 30-second encode.
- Use any remote API (`openai`, `voyageai`, etc.). Local only.
- Touch `acg/runtime.py`, `acg/cli.py`, `viz/`, `demo-app/`, or
  `experiments/greenhouse/`. Other in-flight PRs own those surfaces.

## When in doubt

- `acg/index/bm25.py` is your reference for the corpus tokenization.
  Copy its `_tokenize_path` / `_tokenize_identifiers` helpers if they
  exist; if they're inline, factor a tiny `_document_for_file(entry)`
  helper into `acg/index/util.py` so both BM25 and embeddings share it
  (be careful not to break BM25's tests).
- `acg/index/pagerank.py` is your reference for cache-key construction
  and atomic pickle writes.
- `tests/test_mcp.py` (from PR 6) is the template for "patch an
  optional dep so CI doesn't need it installed." Mirror that style.
- `sentence-transformers` model load is **lazy**: do not call
  `SentenceTransformer(...)` at module import time, only inside
  `_load_model`.

Good luck. The recall lift here is what turns ACG from "deterministic
baseline" into "competitive with semantic-retrieval coding agents" on
greenfield prompts.
