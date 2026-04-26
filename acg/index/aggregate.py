"""Fusion layer for deterministic indexers."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from acg.schema import PredictedWrite, TaskInput

from .bm25 import BM25Indexer
from .cochange import CochangeIndexer
from .framework import FrameworkIndexer
from .pagerank import PageRankIndexer
from .types import Indexer
from .util import clamp_confidence


def _merge_into(
    fused: dict[str, PredictedWrite],
    prediction: PredictedWrite,
) -> None:
    existing = fused.get(prediction.path)
    confidence = clamp_confidence(prediction.confidence)
    if existing is None:
        fused[prediction.path] = PredictedWrite(
            path=prediction.path,
            confidence=confidence,
            reason=prediction.reason,
        )
        return
    reasons = [reason for reason in [existing.reason, prediction.reason] if reason]
    fused[prediction.path] = PredictedWrite(
        path=prediction.path,
        confidence=max(existing.confidence, confidence),
        reason="; ".join(dict.fromkeys(reasons)),
    )


def _default_indexers() -> list[Indexer]:
    indexers: list[Indexer] = [FrameworkIndexer(), PageRankIndexer(), BM25Indexer()]
    if os.environ.get("ACG_INDEX_EMBEDDINGS") == "1":
        try:
            from .embeddings import EmbeddingsIndexer

            indexers.append(EmbeddingsIndexer())
        except ImportError:
            # sentence-transformers not installed -- silently skip.
            pass
    return indexers


def aggregate(
    task: TaskInput,
    repo_root: Path | None,
    repo_graph: dict[str, Any],
    indexers: Sequence[Indexer] | None = None,
    top_n: int = 8,
) -> list[PredictedWrite]:
    """Run every indexer, fuse their outputs, return top-N predictions."""

    fused: dict[str, PredictedWrite] = {}
    first_pass = list(indexers) if indexers is not None else _default_indexers()
    for indexer in first_pass:
        for prediction in indexer.predict(task, repo_root, repo_graph):
            _merge_into(fused, prediction)

    if indexers is None or any(indexer.name == "cochange" for indexer in first_pass):
        seed_paths = [item.path for item in sorted(fused.values(), key=lambda write: (-write.confidence, write.path))]
        cochange = CochangeIndexer(seed_paths=seed_paths)
        for prediction in cochange.predict(task, repo_root, repo_graph):
            _merge_into(fused, prediction)

    return sorted(fused.values(), key=lambda write: (-write.confidence, write.path))[:top_n]
