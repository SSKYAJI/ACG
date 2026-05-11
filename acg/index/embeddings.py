"""Local sentence-transformer embeddings indexer.

Default-off. Activated by passing the indexer instance into
``aggregate(..., indexers=[..., EmbeddingsIndexer()])`` or by setting
``ACG_INDEX_EMBEDDINGS=1`` in the environment (see ``aggregate.py``).

The indexer encodes the same document corpus that ``BM25Indexer`` uses --
file path tokens, declared exports/symbols, imports, and the first
docstring line -- using a small ``sentence-transformers`` model
(``all-MiniLM-L6-v2`` by default). Cosine similarity between the encoded
prompt and each document is mapped to a confidence in ``[0.0, 0.85]``.

Encodings are cached on disk under ``<repo>/.acg/cache/embeddings/`` keyed
by ``(model_name, repo_signature)`` so subsequent calls reuse the matrix.
The optional ``sentence-transformers`` and ``numpy`` dependencies live in
the ``index-vector`` extra; if either import fails, ``predict()`` returns
an empty list and the rest of the aggregator continues unaffected.
"""

from __future__ import annotations

import hashlib
import os
import pickle  # noqa: S403  TODO(security): replace with numpy.savez(allow_pickle=False)
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acg.schema import PredictedWrite, TaskInput

from .bm25 import EXPORT_RE, IMPORT_RE, _docstring_first_line
from .util import cache_dir, graph_file_entries, read_rel, task_text

EMBEDDINGS_MODEL_NAME = os.environ.get(
    "ACG_INDEX_EMBEDDINGS_MODEL", "sentence-transformers/all-MiniLM-L6-v2"
)
DEFAULT_TOP_N = 8
COSINE_FLOOR = 0.18  # below this we treat as noise
CONFIDENCE_CEILING = 0.85
CACHE_DIRNAME = "embeddings"
CACHE_TTL_SECONDS = 7 * 24 * 3600
MAX_FILES = 50_000
MAX_DOC_CHARS = 4_000

_PATH_SPLIT_RE = re.compile(r"[\\/_\-\.]+")


def _path_tokens(path: str) -> list[str]:
    pieces = [p for p in _PATH_SPLIT_RE.split(path) if p]
    return pieces


def _document_text(repo_root: Path | None, entry: dict[str, Any]) -> str:
    path = entry.get("path") or ""
    if not path:
        return ""
    fields: list[str] = []
    fields.append(path)
    fields.extend(_path_tokens(path))
    fields.extend(entry.get("exports") or [])
    fields.extend(entry.get("symbols") or [])
    fields.extend(entry.get("imports") or [])
    text = read_rel(repo_root, path, 60_000)
    if text:
        fields.extend(match.group(1) for match in EXPORT_RE.finditer(text))
        fields.extend(IMPORT_RE.findall(text))
        docline = _docstring_first_line(text)
        if docline:
            fields.append(docline)
    document = " ".join(field.strip() for field in fields if field).strip()
    return document[:MAX_DOC_CHARS]


@dataclass
class _Document:
    path: str
    text: str


class EmbeddingsIndexer:
    """Rank files by cosine similarity in a local sentence-transformer space."""

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
        self._model: Any | None = None

    def predict(
        self,
        task: TaskInput,
        repo_root: Path | None,
        repo_graph: dict[str, Any],
    ) -> list[PredictedWrite]:
        try:
            import numpy as np
            from sentence_transformers import SentenceTransformer
        except ImportError:
            return []

        if repo_root is None:
            return []

        documents = self._build_documents(repo_root, repo_graph)
        if not documents:
            return []

        model = self._load_model(SentenceTransformer)
        doc_vectors = self._encode_corpus(model, documents, np, repo_root)
        if doc_vectors is None:
            return []
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
            confidence = max(
                0.0, min(CONFIDENCE_CEILING, (score_f + 1.0) / 2.0 * CONFIDENCE_CEILING)
            )
            out.append(
                PredictedWrite(
                    path=doc.path,
                    confidence=confidence,
                    reason=(
                        f"Local embedding cosine={score_f:.2f} between task prompt "
                        f"and {doc.path} document tokens."
                    ),
                )
            )
            if len(out) >= self._top_n:
                break
        return out

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _build_documents(
        self,
        repo_root: Path | None,
        repo_graph: dict[str, Any],
    ) -> list[_Document]:
        entries = graph_file_entries(repo_root, repo_graph)[:MAX_FILES]
        documents: list[_Document] = []
        for entry in entries:
            text = _document_text(repo_root, entry)
            if not text:
                continue
            documents.append(_Document(path=entry["path"], text=text))
        documents.sort(key=lambda doc: doc.path)
        return documents

    def _load_model(self, factory: Any) -> Any:
        if self._model is None:
            self._model = factory(self._model_name)
        return self._model

    def _signature(self, documents: list[_Document]) -> str:
        # Hash path + full document text (already in memory from
        # ``_build_documents``) so that equal-length content edits — variable
        # renames, swapped lines, equal-length string-literal swaps — invalidate
        # the cache. Previously the signature only included ``len(doc.text)``,
        # which silently reused stale vectors after refactor-only edits and
        # could violate the PR7 acceptance gate (Δrecall@5 ≥ 0).
        digest = hashlib.sha256()
        digest.update(self._model_name.encode())
        for doc in documents:
            digest.update(doc.path.encode())
            digest.update(b"\x00")
            digest.update(doc.text.encode())
            digest.update(b"\n")
        return digest.hexdigest()[:24]

    def _cache_path(self, repo_root: Path | None, signature: str) -> Path | None:
        directory = cache_dir(repo_root)
        if directory is None:
            return None
        sub = directory / CACHE_DIRNAME
        try:
            sub.mkdir(parents=True, exist_ok=True)
        except OSError:
            return None
        return sub / f"{signature}.pkl"

    def _encode_corpus(
        self,
        model: Any,
        documents: list[_Document],
        np: Any,
        repo_root: Path | None,
    ) -> Any:
        signature = self._signature(documents)
        cache_path = self._cache_path(repo_root, signature)
        if cache_path is not None and cache_path.exists():
            try:
                age = time.time() - cache_path.stat().st_mtime
            except OSError:
                age = CACHE_TTL_SECONDS + 1
            if age <= CACHE_TTL_SECONDS:
                try:
                    with cache_path.open("rb") as fh:
                        cached = pickle.load(fh)
                    if (
                        isinstance(cached, dict)
                        and cached.get("paths") == [doc.path for doc in documents]
                        and cached.get("model") == self._model_name
                    ):
                        return cached["vectors"]
                except (OSError, pickle.PickleError, EOFError):
                    pass

        texts = [doc.text for doc in documents]
        vectors = model.encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        vectors = np.asarray(vectors, dtype="float32")

        if cache_path is not None:
            payload = {
                "model": self._model_name,
                "paths": [doc.path for doc in documents],
                "vectors": vectors,
            }
            tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
            try:
                with tmp_path.open("wb") as fh:
                    pickle.dump(payload, fh)
                tmp_path.replace(cache_path)
            except OSError:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
        return vectors

    def _encode_query(self, model: Any, task: TaskInput, np: Any) -> Any:
        prompt = task.prompt
        hints = getattr(task, "hints", None)
        touches = list(getattr(hints, "touches", []) or []) if hints else []
        if touches:
            prompt = f"{prompt} {' '.join(touches)}"
        elif not prompt:
            prompt = task_text(task)
        vectors = model.encode(
            [prompt],
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        vectors = np.asarray(vectors, dtype="float32")
        return vectors[0]

    def _cosine_similarity(self, query: Any, matrix: Any, np: Any) -> list[float]:
        scores = np.asarray(matrix, dtype="float32") @ np.asarray(query, dtype="float32")
        return [float(value) for value in scores.tolist()]
