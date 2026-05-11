"""Fusion layer for deterministic indexers."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from acg.schema import PredictedWrite, TaskInput

from .cochange import CochangeIndexer
from .framework import FrameworkIndexer
from .pagerank import PageRankIndexer
from .scip import ScipIndexer
from .types import Indexer
from .util import clamp_confidence, task_text, tokenize

try:
    from .bm25 import BM25Indexer
except ImportError:
    BM25Indexer = None  # type: ignore[assignment]

GRAPH_EXPANSION_MIN_CONFIDENCE = 0.72
GRAPH_EXPANSION_CONFIDENCE = 0.72
GRAPH_EXPANSION_MATCH_CONFIDENCE = 0.92
GRAPH_EXPANSION_MULTI_SOURCE_CONFIDENCE = 0.92


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


def _is_graph_only(prediction: PredictedWrite) -> bool:
    reason = prediction.reason.strip().lower()
    return reason.startswith("graph expansion")


def _sort_key(prediction: PredictedWrite) -> tuple[int, float, str]:
    return (1 if _is_graph_only(prediction) else 0, -prediction.confidence, prediction.path)


def _has_scip_metadata(repo_graph: dict[str, Any]) -> bool:
    if repo_graph.get("scip_entities") or repo_graph.get("scip_summary"):
        return True
    return any(
        isinstance(entry, dict)
        and (
            entry.get("scip_symbols")
            or entry.get("scip_definition_count")
            or entry.get("scip_reference_count")
        )
        for entry in repo_graph.get("files", [])
    )


def _default_indexers(repo_graph: dict[str, Any] | None = None) -> list[Indexer]:
    indexers: list[Indexer] = [FrameworkIndexer(), PageRankIndexer()]
    if BM25Indexer is not None:
        indexers.append(BM25Indexer())
    scip_flag = os.environ.get("ACG_INDEX_SCIP")
    graph = repo_graph or {}
    if scip_flag != "0" and (scip_flag == "1" or _has_scip_metadata(graph)):
        indexers.append(ScipIndexer())
    if os.environ.get("ACG_INDEX_EMBEDDINGS") == "1":
        try:
            from .embeddings import EmbeddingsIndexer

            indexers.append(EmbeddingsIndexer())
        except ImportError:
            # sentence-transformers not installed -- silently skip.
            pass
    return indexers


def _file_entries_by_path(repo_graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        entry.get("path"): entry
        for entry in repo_graph.get("files", [])
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    }


def _neighbors(
    path: str, repo_graph: dict[str, Any], entries: dict[str, dict[str, Any]]
) -> list[str]:
    entry = entries.get(path, {})
    candidates: list[str] = []
    for key in ("resolved_imports", "importers", "type_links"):
        values = entry.get(key)
        if isinstance(values, list):
            candidates.extend(item for item in values if isinstance(item, str))
    for mapping_name in ("resolved_imports", "importers", "type_links"):
        mapping = repo_graph.get(mapping_name)
        if isinstance(mapping, dict):
            values = mapping.get(path)
            if isinstance(values, list):
                candidates.extend(item for item in values if isinstance(item, str))
    return sorted(dict.fromkeys(candidates))


def _neighbor_edges(
    path: str, repo_graph: dict[str, Any], entries: dict[str, dict[str, Any]]
) -> list[tuple[str, str]]:
    entry = entries.get(path, {})
    candidates: list[tuple[str, str]] = []
    for key, kind in (
        ("resolved_imports", "import"),
        ("importers", "importer"),
        ("type_links", "type"),
    ):
        values = entry.get(key)
        if isinstance(values, list):
            candidates.extend((item, kind) for item in values if isinstance(item, str))
        mapping = repo_graph.get(key)
        if isinstance(mapping, dict):
            values = mapping.get(path)
            if isinstance(values, list):
                candidates.extend((item, kind) for item in values if isinstance(item, str))
    return sorted(dict.fromkeys(candidates))


def _matches_task(path: str, entry: dict[str, Any], task_tokens: set[str]) -> bool:
    del entry
    haystack = path
    return bool(set(tokenize(haystack)) & task_tokens)


def _is_test_path(path: str) -> bool:
    parts = path.split("/")
    return (
        path.startswith(("test/", "tests/", "__tests__/", "cypress/", "e2e/"))
        or any(part in {"test", "tests", "__tests__"} for part in parts)
        or ".test." in path
        or ".spec." in path
        or ".test-d." in path
    )


def _is_test_task(task: TaskInput) -> bool:
    text = task_text(task).lower()
    return any(keyword in text for keyword in ("test", "tests", "spec", "coverage", "regression"))


def _graph_expand(
    task: TaskInput,
    repo_graph: dict[str, Any],
    fused: dict[str, PredictedWrite],
) -> None:
    entries = _file_entries_by_path(repo_graph)
    if not entries:
        return
    task_tokens = set(tokenize(task_text(task)))
    is_test_task = _is_test_task(task)
    seeds = [
        path
        for path, write in sorted(fused.items(), key=lambda item: (-item[1].confidence, item[0]))
        if write.confidence >= GRAPH_EXPANSION_MIN_CONFIDENCE
    ]
    evidence: dict[str, list[str]] = {}
    edge_kinds: dict[str, set[str]] = {}
    for seed in seeds:
        for neighbor, kind in _neighbor_edges(seed, repo_graph, entries):
            if neighbor == seed or neighbor in fused or neighbor not in entries:
                continue
            if not is_test_task and _is_test_path(neighbor):
                continue
            evidence.setdefault(neighbor, []).append(seed)
            edge_kinds.setdefault(neighbor, set()).add(kind)
    for path, source_paths in sorted(evidence.items()):
        entry = entries[path]
        kinds = edge_kinds.get(path, set())
        if _matches_task(path, entry, task_tokens):
            confidence = GRAPH_EXPANSION_MATCH_CONFIDENCE
        elif "type" in kinds:
            confidence = GRAPH_EXPANSION_MULTI_SOURCE_CONFIDENCE
        elif "import" in kinds and len(set(source_paths)) >= 2:
            confidence = GRAPH_EXPANSION_MULTI_SOURCE_CONFIDENCE
        else:
            confidence = GRAPH_EXPANSION_CONFIDENCE
        if confidence < GRAPH_EXPANSION_MATCH_CONFIDENCE:
            continue
        _merge_into(
            fused,
            PredictedWrite(
                path=path,
                confidence=confidence,
                reason=(
                    "Graph expansion: local import/type edge from high-confidence seed(s) "
                    f"{', '.join(sorted(set(source_paths))[:3])}."
                ),
            ),
        )


def aggregate(
    task: TaskInput,
    repo_root: Path | None,
    repo_graph: dict[str, Any],
    indexers: Sequence[Indexer] | None = None,
    top_n: int = 8,
) -> list[PredictedWrite]:
    """Run every indexer, fuse their outputs, return top-N predictions."""

    fused: dict[str, PredictedWrite] = {}
    first_pass = list(indexers) if indexers is not None else _default_indexers(repo_graph)
    for indexer in first_pass:
        for prediction in indexer.predict(task, repo_root, repo_graph):
            _merge_into(fused, prediction)

    _graph_expand(task, repo_graph, fused)

    if indexers is None or any(indexer.name == "cochange" for indexer in first_pass):
        seed_paths = [
            item.path
            for item in sorted(fused.values(), key=lambda write: (-write.confidence, write.path))
        ]
        cochange = CochangeIndexer(seed_paths=seed_paths)
        for prediction in cochange.predict(task, repo_root, repo_graph):
            _merge_into(fused, prediction)

    _graph_expand(task, repo_graph, fused)

    return sorted(fused.values(), key=_sort_key)[:top_n]
