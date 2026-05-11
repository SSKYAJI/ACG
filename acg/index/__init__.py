"""Deterministic file-set indexers for Agent Context Graph."""

from __future__ import annotations

from acg.schema import PredictedWrite

from . import aggregate as aggregate_module
from . import cochange, framework, pagerank, scip
from .aggregate import aggregate
from .scip import ScipIndexer
from .types import Indexer

try:
    from . import bm25
except ImportError:
    bm25 = None  # type: ignore[assignment]

try:
    from . import embeddings
    from .embeddings import EmbeddingsIndexer
except ImportError:
    embeddings = None  # type: ignore[assignment]
    EmbeddingsIndexer = None  # type: ignore[assignment]

__all__ = [
    "EmbeddingsIndexer",
    "Indexer",
    "PredictedWrite",
    "ScipIndexer",
    "aggregate",
    "aggregate_module",
    "bm25",
    "cochange",
    "embeddings",
    "framework",
    "pagerank",
    "scip",
]
