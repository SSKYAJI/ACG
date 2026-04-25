"""ACG graph builder for Java repositories.

Walks a Maven/Gradle Java project with tree-sitter and emits a
``context_graph.json`` whose top-level shape mirrors what
:mod:`graph_builder.scan` (the TypeScript ts-morph scanner) produces, so the
Python predictor can consume either source interchangeably.

Output schema (mirrors ``graph_builder/scan.ts``)::

    {
      "version": "1.0",
      "scanned_at": "...",
      "root": "<absolute repo root>",
      "language": "java",
      "files": [
        {
          "path": "src/main/java/.../EventService.java",
          "imports": ["org.springframework.jdbc.core.JdbcTemplate", ...],
          "exports": ["EventService"],
          "symbols": ["EventService", "findUpcoming", ...],
          "default_export": null,
          "is_hotspot": false,
          "imported_by_count": 0
        },
        ...
      ],
      "symbols_index": {"EventService": "src/main/java/.../EventService.java"},
      "hotspots": ["src/main/java/.../config/DatabaseConfig.java", ...]
    }

The scanner is intentionally name-only: no type resolution, no method-body
analysis. It exists to feed :func:`acg.predictor._symbol_seed` and the
hotspot heuristic, both of which only need symbol → file mappings and
per-file import counts.

CLI::

    python -m graph_builder.scan_java --repo <repo> --out <context_graph.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# tree-sitter-languages ships pre-compiled grammars; no native build required.
from tree_sitter_languages import get_parser

# Hotspot threshold matches scan.ts: a file imported by >= N other files in
# the same repo is considered a shared-infrastructure hotspot.
HOTSPOT_THRESHOLD = 3

# Directories we never descend into.
_IGNORE_DIRS = {
    ".git",
    "target",
    "build",
    ".gradle",
    ".idea",
    "node_modules",
    ".acg",
    "out",
    "bin",
}

# Maven/Gradle config files we surface as asset nodes (no symbols, but the
# predictor's path-mention seed can still match them).
_ASSET_ROOT_FILES = ["pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle"]


def _walk_java_files(repo_root: Path) -> list[Path]:
    """Return every ``*.java`` file under ``repo_root`` (deterministic order)."""

    out: list[Path] = []
    stack: list[Path] = [repo_root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for entry in entries:
            if entry.is_dir():
                if entry.name in _IGNORE_DIRS or entry.name.startswith("."):
                    continue
                stack.append(entry)
            elif entry.is_file() and entry.suffix == ".java":
                out.append(entry)
    return sorted(out)


def _rel_posix(repo_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _scoped_identifier_text(node, source: bytes) -> str:
    """Concatenate identifiers in a tree-sitter ``scoped_identifier`` node."""

    return _node_text(node, source).strip()


def _extract_package(root, source: bytes) -> str:
    for child in root.children:
        if child.type == "package_declaration":
            for sub in child.children:
                if sub.type in ("scoped_identifier", "identifier"):
                    return _scoped_identifier_text(sub, source)
    return ""


def _extract_imports(root, source: bytes) -> list[str]:
    """Return the FQN of every ``import`` statement (no ``static`` keyword)."""

    imports: list[str] = []
    seen: set[str] = set()
    for child in root.children:
        if child.type != "import_declaration":
            continue
        # Skip ``import``, ``static``, ``;``, ``*`` tokens; the scoped_identifier
        # (or identifier) child holds the dotted FQN.
        target = ""
        is_wildcard = False
        for sub in child.children:
            if sub.type == "asterisk" or _node_text(sub, source) == "*":
                is_wildcard = True
            elif sub.type in ("scoped_identifier", "identifier"):
                target = _scoped_identifier_text(sub, source)
        if not target:
            continue
        if is_wildcard:
            target = f"{target}.*"
        if target not in seen:
            seen.add(target)
            imports.append(target)
    return imports


def _is_public(modifiers_node, source: bytes) -> bool:
    if modifiers_node is None:
        return False
    text = _node_text(modifiers_node, source)
    return "public" in text.split()


def _collect_type_decls(node, source: bytes) -> list[tuple[str, str, list[str]]]:
    """Walk ``node`` collecting ``(kind, name, public_methods)`` tuples.

    ``kind`` is one of ``class``, ``interface``, ``enum``. Nested types are
    included so that the symbols index can answer "where is FooHelper?" even
    if it lives inside another file's class body. Method extraction is
    name-only and skips constructors and non-public methods.
    """

    out: list[tuple[str, str, list[str]]] = []

    def visit(n) -> None:
        kind = None
        if n.type == "class_declaration":
            kind = "class"
        elif n.type == "interface_declaration":
            kind = "interface"
        elif n.type == "enum_declaration":
            kind = "enum"

        if kind is not None:
            name = ""
            body = None
            for child in n.children:
                if child.type == "identifier" and not name:
                    name = _node_text(child, source)
                elif child.type in ("class_body", "interface_body", "enum_body"):
                    body = child
            methods = _collect_public_methods(body, source) if body is not None else []
            out.append((kind, name, methods))

        for child in n.children:
            visit(child)

    visit(node)
    return out


def _collect_public_methods(body_node, source: bytes) -> list[str]:
    """Return public method names declared *directly* in ``body_node``.

    Interface methods are implicitly public (Java 8+) so we keep them all.
    Inside classes/enums we only keep methods whose modifier list contains
    ``public``. Constructor declarations are skipped (the surrounding type
    name is already in the symbols index).
    """

    if body_node is None:
        return []
    methods: list[str] = []
    seen: set[str] = set()
    is_interface = body_node.type == "interface_body"
    for child in body_node.children:
        if child.type != "method_declaration":
            continue
        modifiers = None
        name = ""
        for sub in child.children:
            if sub.type == "modifiers":
                modifiers = sub
            elif sub.type == "identifier" and not name:
                name = _node_text(sub, source)
        if not name:
            continue
        if not is_interface and not _is_public(modifiers, source):
            continue
        if name not in seen:
            seen.add(name)
            methods.append(name)
    return methods


def _scan_file(rel_path: str, source: bytes, parser) -> dict[str, Any]:
    tree = parser.parse(source)
    root = tree.root_node
    package = _extract_package(root, source)
    imports = _extract_imports(root, source)

    # Top-level types live directly under the program node; everything else is
    # nested. ``exports`` only includes top-level public types (the closest
    # Java analogue to a TS ``export``). ``symbols`` is the full set of named
    # things in the file: every type plus every public method.
    top_level_types: list[tuple[str, str, list[str]]] = []
    for child in root.children:
        if child.type in ("class_declaration", "interface_declaration", "enum_declaration"):
            type_decls = _collect_type_decls(child, source)
            if type_decls:
                top_level_types.append(type_decls[0])
    all_types = _collect_type_decls(root, source)

    exports: list[str] = []
    seen_exports: set[str] = set()
    for _kind, name, _methods in top_level_types:
        if name and name not in seen_exports:
            seen_exports.add(name)
            exports.append(name)

    symbols: list[str] = []
    seen_symbols: set[str] = set()
    for _kind, name, methods in all_types:
        if name and name not in seen_symbols:
            seen_symbols.add(name)
            symbols.append(name)
        for method in methods:
            if method not in seen_symbols:
                seen_symbols.add(method)
                symbols.append(method)

    return {
        "path": rel_path,
        "package": package,
        "imports": imports,
        "exports": exports,
        "symbols": symbols,
        "default_export": None,
        "is_hotspot": False,
        "imported_by_count": 0,
    }


def _build_fqn_index(file_nodes: list[dict[str, Any]]) -> dict[str, str]:
    """Map every fully-qualified ``package.ClassName`` to its file path."""

    fqn_to_path: dict[str, str] = {}
    for entry in file_nodes:
        package = entry.get("package", "")
        for export in entry.get("exports", []):
            fqn = f"{package}.{export}" if package else export
            fqn_to_path.setdefault(fqn, entry["path"])
    return fqn_to_path


def _count_imports(file_nodes: list[dict[str, Any]]) -> dict[str, int]:
    """Count, for each file, how many other files import a symbol it defines."""

    fqn_to_path = _build_fqn_index(file_nodes)
    # Group files by package so wildcard imports can fan out cheaply.
    by_package: dict[str, list[str]] = {}
    for entry in file_nodes:
        by_package.setdefault(entry.get("package", ""), []).append(entry["path"])

    counts: dict[str, int] = {}
    for entry in file_nodes:
        importer_path = entry["path"]
        for raw in entry.get("imports", []):
            target = raw
            if target.endswith(".*"):
                pkg = target[:-2]
                for path in by_package.get(pkg, []):
                    if path != importer_path:
                        counts[path] = counts.get(path, 0) + 1
                continue
            path = fqn_to_path.get(target)
            if path is None and "." in target:
                # Nested-class import: ``foo.bar.Outer.Inner`` resolves to the
                # outer class file when we don't track inner types.
                parent = target.rsplit(".", 1)[0]
                path = fqn_to_path.get(parent)
            if path and path != importer_path:
                counts[path] = counts.get(path, 0) + 1
    return counts


def _make_asset_node(rel: str) -> dict[str, Any]:
    return {
        "path": rel,
        "package": "",
        "imports": [],
        "exports": [],
        "symbols": [],
        "default_export": None,
        "is_hotspot": False,
        "imported_by_count": 0,
    }


def _collect_asset_files(repo_root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for name in _ASSET_ROOT_FILES:
        candidate = repo_root / name
        if candidate.exists() and candidate.is_file():
            out.append(_make_asset_node(name))
    return out


def scan(repo_root: Path) -> dict[str, Any]:
    """Scan ``repo_root`` and return the in-memory context graph dict."""

    repo_root = Path(repo_root).resolve()
    parser = get_parser("java")

    file_nodes: list[dict[str, Any]] = []
    for java_file in _walk_java_files(repo_root):
        try:
            source = java_file.read_bytes()
        except OSError:
            continue
        rel = _rel_posix(repo_root, java_file)
        file_nodes.append(_scan_file(rel, source, parser))

    counts = _count_imports(file_nodes)
    for entry in file_nodes:
        n = counts.get(entry["path"], 0)
        entry["imported_by_count"] = n
        entry["is_hotspot"] = n >= HOTSPOT_THRESHOLD

    file_nodes.extend(_collect_asset_files(repo_root))
    file_nodes.sort(key=lambda e: e["path"])

    # Build symbols_index: top-level type names first (so EventService points
    # at its own file even if some other file mentions it), then public methods
    # so the predictor can map renamed-method tasks back to a file.
    symbols_index: dict[str, str] = {}
    for entry in file_nodes:
        for sym in entry.get("exports", []):
            symbols_index.setdefault(sym, entry["path"])
    for entry in file_nodes:
        for sym in entry.get("symbols", []):
            symbols_index.setdefault(sym, entry["path"])

    hotspots = sorted(e["path"] for e in file_nodes if e.get("is_hotspot"))

    # Drop the per-file ``package`` helper before serializing — the schema
    # mirrors scan.ts exactly, and downstream consumers don't need it.
    public_files = [{k: v for k, v in entry.items() if k != "package"} for entry in file_nodes]

    return {
        "version": "1.0",
        "scanned_at": datetime.now(UTC).isoformat(),
        "root": str(repo_root),
        "language": "java",
        "files": public_files,
        "symbols_index": symbols_index,
        "hotspots": hotspots,
    }


def write_graph(repo_root: Path, out_path: Path) -> dict[str, Any]:
    """Scan ``repo_root`` and write the graph to ``out_path``."""

    graph = scan(repo_root)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(graph, indent=2, sort_keys=False) + "\n")
    return graph


def _parse_argv(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit an ACG context_graph.json for a Java repository."
    )
    parser.add_argument("--repo", required=True, help="Repository root.")
    parser.add_argument("--out", required=True, help="Path to context_graph.json.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_argv(list(sys.argv[1:] if argv is None else argv))
    graph = write_graph(Path(args.repo), Path(args.out))
    sys.stdout.write(
        f"wrote {args.out} ({len(graph['files'])} files, "
        f"{len(graph['hotspots'])} hotspots, language=java)\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
