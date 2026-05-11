"""SCIP-backed entity indexer."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from rank_bm25 import BM25Okapi
except ImportError:

    class BM25Okapi:  # type: ignore[no-redef]
        def __init__(self, corpus: list[list[str]]) -> None:
            self._corpus = corpus

        def get_scores(self, query: list[str]) -> list[float]:
            query_terms = set(query)
            return [float(len(set(tokens) & query_terms)) for tokens in self._corpus]


from acg.schema import PredictedWrite, TaskInput

from .util import task_text, tokenize

DEFINITION_CONFIDENCE_CAP = 0.82
REFERENCE_CONFIDENCE_CAP = 0.74
CONTEXT_CONFIDENCE_CAP = 0.68


@dataclass(frozen=True)
class _EntityDoc:
    entity: Any
    symbol: str
    name: str
    definition_path: str
    reference_paths: tuple[str, ...]
    context_paths: tuple[str, ...]
    tokens: list[str]


def _get(obj: Any, *keys: str) -> Any:
    for key in keys:
        if isinstance(obj, dict):
            value = obj.get(key)
        else:
            value = getattr(obj, key, None)
        if value not in (None, ""):
            return value
    return None


def _path_from(value: Any) -> str:
    if isinstance(value, str):
        return value.strip("./")
    if isinstance(value, dict):
        return str(value.get("path") or value.get("file_path") or value.get("file") or "").strip(
            "./"
        )
    return str(
        getattr(value, "path", None)
        or getattr(value, "file_path", None)
        or getattr(value, "file", "")
    ).strip("./")


def _paths_from(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (str, dict)):
        path = _path_from(value)
        return [path] if path else []
    if not isinstance(value, list | tuple | set):
        return []
    paths = [_path_from(item) for item in value]
    return [path for path in paths if path]


def _local_name(symbol: str) -> str:
    if not symbol:
        return ""
    for marker in ("#", ".", "/", " "):
        if marker in symbol:
            symbol = symbol.rsplit(marker, 1)[-1]
    return symbol.strip("`'\"()[]{}")


def _entity_name(entity: Any, symbol: str) -> str:
    name = _get(entity, "name", "display_name", "identifier")
    return str(name or _local_name(symbol)).strip()


def _entity_definition_path(entity: Any) -> str:
    direct = _get(
        entity,
        "path",
        "file_path",
        "file",
        "definition_path",
        "relative_path",
    )
    path = _path_from(direct)
    if path:
        return path
    definition = _get(entity, "definition", "definition_location", "location")
    return _path_from(definition)


def _entity_reference_paths(entity: Any) -> tuple[str, ...]:
    paths: list[str] = []
    for key in (
        "references",
        "reference_paths",
        "ref_paths",
        "reference_locations",
    ):
        paths.extend(_paths_from(_get(entity, key)))
    return tuple(dict.fromkeys(path for path in paths if path))


def _entity_context_paths(entity: Any) -> tuple[str, ...]:
    paths: list[str] = []
    for key in ("referenced_by", "context_paths", "related_paths"):
        paths.extend(_paths_from(_get(entity, key)))
    return tuple(dict.fromkeys(path for path in paths if path))


def _text_fields(
    entity: Any, symbol: str, name: str, definition_path: str, refs: tuple[str, ...]
) -> str:
    values: list[str] = [symbol, name, definition_path, " ".join(refs)]
    for key in ("kind", "language", "signature", "doc", "documentation", "detail"):
        value = _get(entity, key)
        if value:
            values.append(str(value))
    return " ".join(values)


def _entity_docs(entities: Any) -> list[_EntityDoc]:
    if not isinstance(entities, list):
        return []
    docs: list[_EntityDoc] = []
    for entity in entities:
        symbol = str(_get(entity, "symbol", "scip_symbol", "descriptor") or "").strip()
        name = _entity_name(entity, symbol)
        definition_path = _entity_definition_path(entity)
        reference_paths = _entity_reference_paths(entity)
        context_paths = _entity_context_paths(entity)
        if not symbol and not name:
            continue
        if not definition_path and not reference_paths and not context_paths:
            continue
        text = _text_fields(
            entity, symbol, name, definition_path, (*reference_paths, *context_paths)
        )
        tokens = tokenize(text)
        if not tokens:
            continue
        docs.append(
            _EntityDoc(
                entity=entity,
                symbol=symbol,
                name=name,
                definition_path=definition_path,
                reference_paths=reference_paths,
                context_paths=context_paths,
                tokens=tokens,
            )
        )
    return docs


def _confidence(score: float, cap: float) -> float:
    if score <= 0:
        return 0.0
    return max(0.5, min(cap, cap - (1.0 / (score + 3.0))))


class ScipIndexer:
    """Rank SCIP entities against the task and return file-level candidates."""

    name = "scip"

    def __init__(self, top_n: int = 8) -> None:
        self.top_n = top_n

    def predict(
        self,
        task: TaskInput,
        repo_root: Path | None,
        repo_graph: dict[str, Any],
    ) -> list[PredictedWrite]:
        del repo_root
        docs = _entity_docs(repo_graph.get("scip_entities"))
        if not docs:
            return []
        query = tokenize(task_text(task))
        if not query:
            return []

        bm25 = BM25Okapi([doc.tokens for doc in docs])
        raw_scores = bm25.get_scores(query)
        query_tokens = set(query)
        ranked: list[tuple[float, _EntityDoc]] = []
        for doc, raw_score in zip(docs, raw_scores, strict=True):
            overlap = len(set(doc.tokens) & query_tokens)
            score = max(float(raw_score), float(overlap))
            if score > 0:
                ranked.append((score, doc))
        ranked.sort(key=lambda item: (-item[0], item[1].definition_path, item[1].symbol))

        best: dict[str, PredictedWrite] = {}
        evidence: dict[str, list[str]] = defaultdict(list)
        for score, doc in ranked:
            label = doc.name or doc.symbol or "unknown"
            if doc.definition_path:
                confidence = _confidence(score, DEFINITION_CONFIDENCE_CAP)
                reason = (
                    f"SCIP entity {label!r} ({doc.symbol or label}) BM25 matched task; "
                    f"definition in {doc.definition_path}."
                )
                self._merge(best, evidence, doc.definition_path, confidence, reason)
            for ref_path in doc.reference_paths:
                if ref_path == doc.definition_path:
                    continue
                confidence = _confidence(score, REFERENCE_CONFIDENCE_CAP)
                reason = (
                    f"SCIP reference to entity {label!r} ({doc.symbol or label}) "
                    f"BM25 matched task in {ref_path}."
                )
                self._merge(best, evidence, ref_path, confidence, reason)
            for context_path in doc.context_paths:
                if context_path == doc.definition_path or context_path in doc.reference_paths:
                    continue
                confidence = _confidence(score, CONTEXT_CONFIDENCE_CAP)
                reason = (
                    f"SCIP reference context for entity {label!r} ({doc.symbol or label}) "
                    f"BM25 matched task in {context_path}."
                )
                self._merge(best, evidence, context_path, confidence, reason)

        for path, reasons in evidence.items():
            existing = best[path]
            best[path] = PredictedWrite(
                path=path,
                confidence=existing.confidence,
                reason="; ".join(dict.fromkeys(reasons[:3])),
            )
        return sorted(best.values(), key=lambda write: (-write.confidence, write.path))[
            : self.top_n
        ]

    @staticmethod
    def _merge(
        best: dict[str, PredictedWrite],
        evidence: dict[str, list[str]],
        path: str,
        confidence: float,
        reason: str,
    ) -> None:
        existing = best.get(path)
        if existing is None or confidence > existing.confidence:
            best[path] = PredictedWrite(path=path, confidence=confidence, reason=reason)
        evidence[path].append(reason)


def predict(
    task: TaskInput,
    repo_root: Path | None,
    repo_graph: dict[str, Any],
) -> list[PredictedWrite]:
    return ScipIndexer().predict(task, repo_root, repo_graph)
