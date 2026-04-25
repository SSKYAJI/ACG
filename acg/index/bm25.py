"""BM25 lexical indexer over paths, identifiers, imports, and docstrings."""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from rank_bm25 import BM25Okapi

from acg.schema import PredictedWrite, TaskInput

from .util import graph_file_entries, read_rel, task_text, tokenize

DOCSTRING_RE = re.compile(r'^\s*(?:"""([^"\n]*)|\'\'\'([^\'\n]*)|/\*\*?\s*([^*\n]*))', re.MULTILINE)
IMPORT_RE = re.compile(r"\b(?:import|from|require|use|package)\b[^\n;]*")
EXPORT_RE = re.compile(
    r"\b(?:export\s+(?:default\s+)?(?:function|class|const|let|var|interface|type)|def|class|func|public\s+class)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
SYNONYMS = {
    "navigation": ["nav", "sidebar", "menu"],
    "menu": ["navigation", "nav", "sidebar"],
    "database": ["db", "prisma", "sql"],
    "auth": ["authentication", "login", "oauth"],
}


def _docstring_first_line(text: str) -> str:
    match = DOCSTRING_RE.search(text)
    if not match:
        return ""
    return next((group or "" for group in match.groups() if group), "").strip()


def _source_tokens(repo_root: Path | None, path: str) -> tuple[list[str], list[str], str]:
    text = read_rel(repo_root, path, 60_000)
    if not text:
        return [], [], ""
    imports = IMPORT_RE.findall(text)
    exports = [match.group(1) for match in EXPORT_RE.finditer(text)]
    return exports, imports, _docstring_first_line(text)


class BM25Indexer:
    """Rank files by lexical overlap with the task prompt."""

    name = "bm25"

    def __init__(self, top_n: int = 8) -> None:
        self.top_n = top_n

    def predict(
        self,
        task: TaskInput,
        repo_root: Path | None,
        repo_graph: dict[str, Any],
    ) -> list[PredictedWrite]:
        entries = graph_file_entries(repo_root, repo_graph)
        if not entries:
            return []

        paths: list[str] = []
        corpus: list[list[str]] = []
        for entry in entries:
            path = entry["path"]
            source_exports, source_imports, docline = _source_tokens(repo_root, path)
            fields: list[str] = [
                path,
                " ".join(entry.get("exports") or []),
                " ".join(entry.get("symbols") or []),
                " ".join(entry.get("imports") or []),
                " ".join(source_exports),
                " ".join(source_imports),
                docline,
            ]
            tokens = tokenize(" ".join(fields))
            if not tokens:
                tokens = tokenize(path)
            paths.append(path)
            corpus.append(tokens)

        query = tokenize(task_text(task))
        query.extend(
            synonym
            for token in list(query)
            for synonym in SYNONYMS.get(token, [])
        )
        if not query:
            return []
        bm25 = BM25Okapi(corpus)
        raw_scores = bm25.get_scores(query)
        scores = []
        for tokens, raw_score in zip(corpus, raw_scores, strict=True):
            overlap = len(set(tokens) & set(query))
            scores.append(max(float(raw_score), float(overlap)))
        ranked = sorted(
            ((score, path) for score, path in zip(scores, paths, strict=True) if score > 0),
            key=lambda item: (-item[0], item[1]),
        )

        out: list[PredictedWrite] = []
        for score, path in ranked[: self.top_n]:
            out.append(
                PredictedWrite(
                    path=path,
                    confidence=math.tanh(score / 5.0),
                    reason=f"BM25 lexical match over path, identifiers, imports, docstrings (score {score:.2f}).",
                )
            )
        return out


def predict(
    task: TaskInput,
    repo_root: Path | None,
    repo_graph: dict[str, Any],
) -> list[PredictedWrite]:
    return BM25Indexer().predict(task, repo_root, repo_graph)
