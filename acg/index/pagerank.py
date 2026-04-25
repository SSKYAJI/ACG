"""Personalized PageRank indexer over a file-level symbol graph."""

from __future__ import annotations

import hashlib
import pickle
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import networkx as nx
from rapidfuzz import fuzz

from acg.schema import PredictedWrite, TaskInput

from .util import cache_dir, graph_file_entries, read_rel, task_text, tokenize

try:  # pragma: no cover - exercised when the native wheel supports this Python.
    from tree_sitter_languages import get_parser as _get_tree_sitter_parser
except Exception:  # pragma: no cover
    _get_tree_sitter_parser = None

DEFAULT_TOP_N = 8
FUZZY_THRESHOLD = 85
MAX_FILES = 50_000
EXTENSION_LANGUAGE = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "javascript",
    ".go": "go",
    ".java": "java",
}
DEFINITION_RE = re.compile(
    r"\b(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function|class|interface|type|const|let|var|def|func)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
JAVA_DEFINITION_RE = re.compile(
    r"\b(?:public|private|protected)?\s*(?:static\s+)?(?:class|interface|enum|record)\s+([A-Za-z_][A-Za-z0-9_]*)"
)
IMPORT_RE = re.compile(r"(?:from\s+['\"]([^'\"]+)['\"]|import\s+[^'\n]*?from\s+['\"]([^'\"]+)['\"]|import\s+['\"]([^'\"]+)['\"]|require\(['\"]([^'\"]+)['\"]\)|from\s+([\w.]+)\s+import|import\s+([\w.]+))")
IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")


@dataclass
class SymbolGraph:
    graph: nx.DiGraph
    file_symbols: dict[str, set[str]]
    symbol_files: dict[str, set[str]]


def _repo_signature(repo_root: Path | None, paths: list[str]) -> str:
    digest = hashlib.sha256()
    digest.update("|".join(paths).encode())
    if repo_root is not None:
        for rel in paths[:MAX_FILES]:
            path = repo_root / rel
            try:
                stat = path.stat()
            except OSError:
                continue
            digest.update(f"{rel}:{stat.st_mtime_ns}:{stat.st_size}".encode())
    return digest.hexdigest()[:24]


def _cache_path(repo_root: Path | None, signature: str) -> Path | None:
    directory = cache_dir(repo_root)
    if directory is None:
        return None
    return directory / f"pagerank-{signature}.pickle"


def _module_to_path(importer: str, specifier: str, files: set[str]) -> str | None:
    if specifier.startswith("."):
        base = Path(importer).parent / specifier
        candidates = [base.as_posix(), f"{base.as_posix()}.ts", f"{base.as_posix()}.tsx", f"{base.as_posix()}.js", f"{base.as_posix()}.py", f"{base.as_posix()}/index.ts", f"{base.as_posix()}/index.tsx"]
    elif specifier.startswith("~/"):
        raw = specifier[2:]
        candidates = [f"src/{raw}", raw, f"src/{raw}.ts", f"src/{raw}.tsx", f"{raw}.ts", f"{raw}.tsx", f"src/{raw}/index.ts", f"src/{raw}/index.tsx"]
    else:
        raw = specifier.replace(".", "/")
        candidates = [raw, f"{raw}.py", f"{raw}.ts", f"{raw}.tsx", f"{raw}.js"]
    for candidate in candidates:
        normalized = Path(candidate).as_posix()
        if normalized in files:
            return normalized
    return None


def _imports(text: str) -> list[str]:
    specs: list[str] = []
    for match in IMPORT_RE.finditer(text):
        spec = next((group for group in match.groups() if group), None)
        if spec:
            specs.append(spec)
    return specs


def _regex_definitions(text: str, path: str) -> set[str]:
    symbols = {match.group(1) for match in DEFINITION_RE.finditer(text)}
    if path.endswith(".java"):
        symbols.update(match.group(1) for match in JAVA_DEFINITION_RE.finditer(text))
    return symbols


def _tree_sitter_definitions(text: str, path: str) -> set[str]:
    if _get_tree_sitter_parser is None:
        return set()
    language = EXTENSION_LANGUAGE.get(Path(path).suffix)
    if language is None:
        return set()
    try:
        parser = _get_tree_sitter_parser(language)
        tree = parser.parse(text.encode("utf-8", errors="ignore"))
    except Exception:
        return set()
    symbols: set[str] = set()

    def visit(node: Any) -> None:
        node_type = getattr(node, "type", "")
        if node_type in {"function_declaration", "class_declaration", "interface_declaration", "type_alias_declaration", "lexical_declaration", "method_definition"}:
            child = node.child_by_field_name("name")
            if child is not None:
                symbols.add(text[child.start_byte : child.end_byte])
        for child in getattr(node, "children", []):
            visit(child)

    visit(tree.root_node)
    return {symbol for symbol in symbols if symbol}


def _build_symbol_graph(repo_root: Path | None, repo_graph: dict[str, Any]) -> SymbolGraph:
    entries = graph_file_entries(repo_root, repo_graph)[:MAX_FILES]
    files = {entry["path"] for entry in entries}
    graph = nx.DiGraph()
    graph.add_nodes_from(files)
    file_symbols: dict[str, set[str]] = defaultdict(set)
    symbol_files: dict[str, set[str]] = defaultdict(set)
    references: dict[str, Counter[str]] = {}

    for entry in entries:
        path = entry["path"]
        text = read_rel(repo_root, path)
        declared = set(entry.get("exports") or []) | set(entry.get("symbols") or [])
        declared.update(_regex_definitions(text, path))
        declared.update(_tree_sitter_definitions(text, path))
        declared = {symbol for symbol in declared if symbol and len(symbol) < 120}
        file_symbols[path] = declared
        for symbol in declared:
            symbol_files[symbol].add(path)
        references[path] = Counter(IDENT_RE.findall(text))

    for entry in entries:
        path = entry["path"]
        text = read_rel(repo_root, path)
        for specifier in list(entry.get("imports") or []) + _imports(text):
            target = _module_to_path(path, specifier, files)
            if target and target != path:
                graph.add_edge(path, target, weight=graph.get_edge_data(path, target, {}).get("weight", 0) + 2)

    for referrer, counts in references.items():
        for symbol, count in counts.items():
            definers = symbol_files.get(symbol, set()) - {referrer}
            for definer in definers:
                graph.add_edge(referrer, definer, weight=graph.get_edge_data(referrer, definer, {}).get("weight", 0) + count)

    return SymbolGraph(graph=graph, file_symbols=dict(file_symbols), symbol_files=dict(symbol_files))


def build_symbol_graph(repo_root: Path | None, repo_graph: dict[str, Any]) -> SymbolGraph:
    paths = [entry["path"] for entry in graph_file_entries(repo_root, repo_graph)]
    signature = _repo_signature(repo_root, paths)
    path = _cache_path(repo_root, signature)
    if path is not None and path.exists():
        try:
            with path.open("rb") as fh:
                cached = pickle.load(fh)
            if isinstance(cached, SymbolGraph):
                return cached
        except (OSError, pickle.PickleError, EOFError):
            pass
    graph = _build_symbol_graph(repo_root, repo_graph)
    if path is not None:
        try:
            with path.open("wb") as fh:
                pickle.dump(graph, fh)
        except OSError:
            pass
    return graph


def _symbol_matches(task: TaskInput, symbol_graph: SymbolGraph) -> dict[str, list[str]]:
    query_tokens = tokenize(task_text(task))
    matches: dict[str, list[str]] = defaultdict(list)
    for symbol, files in symbol_graph.symbol_files.items():
        symbol_tokens = tokenize(symbol)
        if not symbol_tokens:
            continue
        top_ratio = max(
            (fuzz.ratio(query, candidate) for query in query_tokens for candidate in [symbol.lower(), *symbol_tokens]),
            default=0,
        )
        if top_ratio >= FUZZY_THRESHOLD:
            for path in files:
                matches[path].append(symbol)
    return {path: sorted(symbols) for path, symbols in matches.items()}


def _weighted_pagerank(
    graph: nx.DiGraph,
    personalization: dict[str, float],
    alpha: float = 0.85,
    max_iter: int = 100,
    tol: float = 1e-8,
) -> dict[str, float]:
    nodes = list(graph.nodes)
    if not nodes:
        return {}
    total = sum(personalization.values()) or 1.0
    p = {node: personalization.get(node, 0.0) / total for node in nodes}
    rank = {node: 1.0 / len(nodes) for node in nodes}
    outgoing_weight = {
        node: sum(float(data.get("weight", 1.0)) for _, _, data in graph.out_edges(node, data=True))
        for node in nodes
    }
    for _ in range(max_iter):
        dangling = alpha * sum(rank[node] for node in nodes if outgoing_weight[node] == 0)
        next_rank = {node: (1.0 - alpha) * p[node] + dangling * p[node] for node in nodes}
        for source, target, data in graph.edges(data=True):
            weight = float(data.get("weight", 1.0))
            total_weight = outgoing_weight[source]
            if total_weight:
                next_rank[target] += alpha * rank[source] * weight / total_weight
        error = sum(abs(next_rank[node] - rank[node]) for node in nodes)
        rank = next_rank
        if error < len(nodes) * tol:
            break
    return rank


class PageRankIndexer:
    """Rank symbol-adjacent files with prompt-personalized PageRank."""

    name = "pagerank"

    def __init__(self, top_n: int = DEFAULT_TOP_N) -> None:
        self.top_n = top_n

    def predict(
        self,
        task: TaskInput,
        repo_root: Path | None,
        repo_graph: dict[str, Any],
    ) -> list[PredictedWrite]:
        symbol_graph = build_symbol_graph(repo_root, repo_graph)
        graph = symbol_graph.graph
        if not graph.nodes:
            return []
        matches = _symbol_matches(task, symbol_graph)
        if matches:
            base = 1.0 / len(graph)
            personalization = {
                node: (1.0 + len(matches.get(node, []))) if node in matches else base
                for node in graph.nodes
            }
        else:
            personalization = {node: 1.0 / len(graph) for node in graph.nodes}
        ranks = _weighted_pagerank(graph, personalization)
        ranked = sorted(ranks.items(), key=lambda item: (-item[1], item[0]))[: self.top_n]
        out: list[PredictedWrite] = []
        for idx, (path, rank) in enumerate(ranked, start=1):
            top_matches = ", ".join(matches.get(path, [])[:3]) or "none"
            confidence = min(0.9, rank * 1000)
            if matches and path not in matches:
                confidence = min(confidence, 0.45)
            out.append(
                PredictedWrite(
                    path=path,
                    confidence=max(0.05, confidence),
                    reason=f"personalized PageRank rank #{idx}, top symbol matches: {top_matches}.",
                )
            )
        return out


def predict(
    task: TaskInput,
    repo_root: Path | None,
    repo_graph: dict[str, Any],
) -> list[PredictedWrite]:
    return PageRankIndexer().predict(task, repo_root, repo_graph)
