"""ACG graph builder for Python repositories.

Walks a Python project with LibCST and emits a ``context_graph.json`` whose
top-level shape mirrors :mod:`graph_builder.scan_java` and
:mod:`graph_builder.scan` (the TypeScript ts-morph scanner) so the Python
predictor can consume any of them interchangeably.

Output schema (matches ``graph_builder/scan.ts`` and ``scan_java.py``)::

    {
      "version": "1.0",
      "scanned_at": "...",
      "root": "<absolute repo root>",
      "language": "python",
      "files": [
        {
          "path": "app/main.py",
          "imports": ["fastapi.FastAPI", "app.routers.items.router", ...],
          "exports": ["app", "create_app"],
          "symbols": ["app", "create_app", "Settings.from_env", ...],
          "default_export": null,
          "is_hotspot": false,
          "imported_by_count": 0
        },
        ...
      ],
      "symbols_index": {"create_app": "app/main.py", ...},
      "hotspots": ["app/main.py", ...],
      "routes": [{"path": "app/main.py", "kind": "api", "route": "/health"}, ...]
    }

The scanner is name-and-decorator only: no type resolution, no call-graph
construction. It exists to feed :func:`acg.predictor._symbol_seed`, the
hotspot heuristic, and the FastAPI/Flask route templates in
:mod:`acg.index.framework`.

Files that fail to parse with LibCST are skipped (with a warning recorded in
``parse_failures``) rather than crashing the whole scan -- LibCST is stricter
than the stdlib ``ast`` module on syntax errors, so this is a defensive guard
for real-world repos that may contain experimental or partial files.

CLI::

    python -m graph_builder.scan_python --repo <repo> --out <context_graph.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import libcst as cst
import libcst.matchers as m

# Hotspot threshold matches scan.ts and scan_java.py: a file imported by >= N
# other files in the same repo is considered a shared-infrastructure hotspot.
HOTSPOT_THRESHOLD = 3

# Directories we never descend into.
_IGNORE_DIRS = {
    ".acg",
    ".eggs",
    ".git",
    ".gradle",
    ".idea",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "bin",
    "build",
    "dist",
    "node_modules",
    "out",
    "site-packages",
    "target",
    "venv",
}

# Asset / config files we surface as zero-symbol nodes (no imports, no
# symbols, but the predictor's path-mention seed can still match them).
_ASSET_ROOT_FILES = (
    "Pipfile",
    "Pipfile.lock",
    "manage.py",
    "poetry.lock",
    "pyproject.toml",
    "pytest.ini",
    "requirements.txt",
    "setup.cfg",
    "setup.py",
    "tox.ini",
)

# HTTP method names recognised on FastAPI / APIRouter / Flask Blueprint
# decorators. ``route`` is Flask/Blueprint-specific (and also matches Django
# class-based usage that we ignore here).
_HTTP_METHODS = {
    "get",
    "post",
    "put",
    "patch",
    "delete",
    "head",
    "options",
    "trace",
    "route",
}


# ---------------------------------------------------------------------------
# File walking
# ---------------------------------------------------------------------------


def _walk_py_files(repo_root: Path) -> list[Path]:
    """Return every ``*.py`` file under ``repo_root`` (deterministic order)."""

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
                if entry.name in _IGNORE_DIRS or entry.name.endswith(".egg-info"):
                    continue
                stack.append(entry)
            elif entry.is_file() and entry.suffix == ".py":
                out.append(entry)
    return sorted(out)


def _rel_posix(repo_root: Path, path: Path) -> str:
    return path.resolve().relative_to(repo_root.resolve()).as_posix()


# ---------------------------------------------------------------------------
# Module index
# ---------------------------------------------------------------------------


def _detect_source_root(repo_root: Path) -> str:
    """Return ``"src"`` if ``<repo>/src/<pkg>/__init__.py`` exists, else ``""``.

    A ``src/`` layout means modules should be addressed without the leading
    ``src/`` component (e.g. ``src/foo/bar.py`` is module ``foo.bar``).
    """

    src = repo_root / "src"
    if not src.is_dir():
        return ""
    for child in src.iterdir():
        if child.is_dir() and (child / "__init__.py").exists():
            return "src"
        if child.is_file() and child.suffix == ".py":
            # ``src/foo.py`` (no package, but src layout) still counts.
            return "src"
    return ""


def _module_name(rel_path: str, source_root: str) -> str:
    """Convert a repo-relative POSIX path to its dotted module name."""

    rel = rel_path
    if source_root and rel.startswith(source_root + "/"):
        rel = rel[len(source_root) + 1 :]
    if rel.endswith("/__init__.py"):
        rel = rel[: -len("/__init__.py")]
    elif rel == "__init__.py":
        rel = ""
    elif rel.endswith(".py"):
        rel = rel[:-3]
    return rel.replace("/", ".")


# ---------------------------------------------------------------------------
# Per-file extraction
# ---------------------------------------------------------------------------


def _attr_chain(node: cst.BaseExpression) -> str | None:
    """Render an attribute chain like ``a.b.c`` to its dotted string.

    Returns ``None`` if any component is not a plain ``Name`` / ``Attribute``
    (e.g. a function call or subscript), so we never silently swallow
    dynamic imports.
    """

    parts: list[str] = []
    current: cst.BaseExpression | None = node
    while isinstance(current, cst.Attribute):
        parts.append(current.attr.value)
        current = current.value
    if isinstance(current, cst.Name):
        parts.append(current.value)
        return ".".join(reversed(parts))
    return None


def _resolve_relative(package: str, level: int, target: str) -> str:
    """Resolve ``from <level dots><target> import ...`` against ``package``.

    ``package`` is the dotted package name *containing* the importing file
    (e.g. ``app.routers`` when the importer is ``app/routers/items.py``,
    or ``app.routers`` when the importer is ``app/routers/__init__.py``).
    ``level`` is the number of leading dots (``1`` for ``from .x``,
    ``2`` for ``from ..x``). ``target`` is the portion after the dots
    (may be empty for ``from . import x``).

    Resolution rules mirror CPython:

    * ``level=1`` keeps ``package`` as-is (``from .`` means *this package*).
    * ``level=2`` ascends one parent (``from ..`` means *parent package*).
    * ``level=N`` ascends ``N - 1`` parents.
    """

    if level <= 0:
        return target
    parts = package.split(".") if package else []
    parts = parts[: max(0, len(parts) - (level - 1))]
    if target:
        parts.append(target)
    return ".".join(parts)


def _module_package(module: str, is_init: bool) -> str:
    """Return the dotted package name that *contains* ``module``.

    For an ``__init__.py``, that's the module itself (the file *is* the
    package). For a regular ``.py`` file, it's the parent dotted name.
    """

    if is_init:
        return module
    if "." in module:
        return module.rsplit(".", 1)[0]
    return ""


def _string_value(node: cst.BaseExpression) -> str | None:
    """Return the value of a literal string node, else ``None``."""

    if isinstance(node, cst.SimpleString):
        try:
            value = node.evaluated_value
        except Exception:  # noqa: BLE001 - libcst raises on f-strings/bytes
            return None
        if isinstance(value, str):
            return value
    if isinstance(node, cst.ConcatenatedString):
        try:
            value = node.evaluated_value
        except Exception:  # noqa: BLE001
            return None
        if isinstance(value, str):
            return value
    return None


def _extract_all_literal(module_body: cst.BaseSuite | list) -> list[str] | None:
    """Return the literal contents of ``__all__`` if statically recoverable."""

    body_iter = module_body.body if hasattr(module_body, "body") else module_body
    for stmt in body_iter:
        if not isinstance(stmt, cst.SimpleStatementLine):
            continue
        for sub in stmt.body:
            if not isinstance(sub, cst.Assign):
                continue
            if not (
                len(sub.targets) == 1
                and isinstance(sub.targets[0].target, cst.Name)
                and sub.targets[0].target.value == "__all__"
            ):
                continue
            value = sub.value
            if isinstance(value, (cst.List, cst.Tuple)):
                names: list[str] = []
                ok = True
                for element in value.elements:
                    if not isinstance(element, cst.Element):
                        ok = False
                        break
                    text = _string_value(element.value)
                    if text is None:
                        ok = False
                        break
                    names.append(text)
                if ok:
                    return names
            return None
    return None


def _decorator_route(decorator: cst.Decorator) -> tuple[str, str] | None:
    """Return ``(receiver, route)`` for a literal route decorator, else None.

    Recognises the FastAPI / APIRouter form ``@<recv>.<method>("/path", ...)``
    and the Flask / Blueprint form ``@<recv>.route("/path", ...)``. The
    receiver name is purely informational; only the method name matters
    for classification (``api`` for HTTP verbs, ``api`` for ``route``).
    """

    deco = decorator.decorator
    if not isinstance(deco, cst.Call):
        return None
    func = deco.func
    if not isinstance(func, cst.Attribute):
        return None
    method = func.attr.value
    if method not in _HTTP_METHODS:
        return None
    receiver = _attr_chain(func.value)
    if receiver is None:
        return None
    if not deco.args:
        return None
    first = deco.args[0]
    route = _string_value(first.value)
    if route is None:
        return None
    return receiver, route


def _is_public(name: str) -> bool:
    return bool(name) and not name.startswith("_")


def _is_dunder(name: str) -> bool:
    return len(name) >= 4 and name.startswith("__") and name.endswith("__")


class _FileVisitor(cst.CSTVisitor):
    """Collect imports, exports, symbols, and routes from a module."""

    def __init__(self, module: str, rel_path: str, is_init: bool) -> None:
        self.module = module
        self.package = _module_package(module, is_init)
        self.rel_path = rel_path
        self.imports: list[str] = []
        self._seen_imports: set[str] = set()
        self.top_level_names: list[str] = []
        self._seen_top: set[str] = set()
        self.symbols: list[str] = []
        self._seen_symbols: set[str] = set()
        self.routes: list[dict[str, str]] = []
        self._depth = 0  # zero at module level

    # -- helpers ------------------------------------------------------------

    def _add_import(self, name: str) -> None:
        if name and name not in self._seen_imports:
            self._seen_imports.add(name)
            self.imports.append(name)

    def _add_top_level(self, name: str) -> None:
        if not name or _is_dunder(name):
            return
        if name not in self._seen_top:
            self._seen_top.add(name)
            self.top_level_names.append(name)
        self._add_symbol(name)

    def _add_symbol(self, name: str) -> None:
        if not name or _is_dunder(name):
            return
        if name not in self._seen_symbols:
            self._seen_symbols.add(name)
            self.symbols.append(name)

    # -- imports ------------------------------------------------------------

    def visit_Import(self, node: cst.Import) -> None:
        for alias in node.names:
            chain = _attr_chain(alias.name)
            if chain:
                self._add_import(chain)

    def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
        level = len(node.relative)
        module_name = _attr_chain(node.module) if node.module is not None else ""
        if module_name is None:
            return
        base = (
            _resolve_relative(self.package, level, module_name)
            if level > 0
            else (module_name or "")
        )
        if isinstance(node.names, cst.ImportStar):
            target = base if base else "*"
            self._add_import(f"{target}.*" if base else "*")
            return
        for alias in node.names:
            if not isinstance(alias.name, cst.Name):
                continue
            leaf = alias.name.value
            full = f"{base}.{leaf}" if base else leaf
            self._add_import(full)

    # -- depth tracking -----------------------------------------------------

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        if self._depth == 0:
            self._add_top_level(node.name.value)
            self._record_decorator_routes(node.decorators)
        self._depth += 1

    def leave_FunctionDef(self, original_node: cst.FunctionDef) -> None:
        self._depth -= 1

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        if self._depth == 0:
            self._add_top_level(node.name.value)
            class_name = node.name.value
            for stmt in node.body.body:
                method = self._method_name(stmt)
                if method is not None:
                    self._add_symbol(f"{class_name}.{method}")
                    if isinstance(stmt, (cst.FunctionDef,)):
                        self._record_decorator_routes(stmt.decorators)
        self._depth += 1

    def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
        self._depth -= 1

    @staticmethod
    def _method_name(stmt: cst.BaseStatement) -> str | None:
        if isinstance(stmt, cst.FunctionDef):
            return stmt.name.value
        return None

    def visit_Assign(self, node: cst.Assign) -> None:
        if self._depth != 0:
            return
        for target in node.targets:
            t = target.target
            if isinstance(t, cst.Name):
                self._add_top_level(t.value)
            elif isinstance(t, (cst.Tuple, cst.List)):
                for element in t.elements:
                    if isinstance(element, cst.Element) and isinstance(element.value, cst.Name):
                        self._add_top_level(element.value.value)

    def visit_AnnAssign(self, node: cst.AnnAssign) -> None:
        if self._depth != 0:
            return
        if isinstance(node.target, cst.Name):
            self._add_top_level(node.target.value)

    # -- routes -------------------------------------------------------------

    def _record_decorator_routes(self, decorators) -> None:
        for deco in decorators:
            extracted = _decorator_route(deco)
            if extracted is None:
                continue
            _receiver, route = extracted
            self.routes.append(
                {"path": self.rel_path, "kind": "api", "route": route}
            )


def _scan_file(
    rel_path: str, source: str, module: str, is_init: bool
) -> dict[str, Any] | None:
    """Parse ``source`` and return a file node, or ``None`` on parse failure."""

    try:
        tree = cst.parse_module(source)
    except cst.ParserSyntaxError:
        return None

    visitor = _FileVisitor(module=module, rel_path=rel_path, is_init=is_init)
    tree.visit(visitor)

    all_literal = _extract_all_literal(tree)
    if all_literal is not None:
        exports = list(dict.fromkeys(all_literal))
    else:
        exports = [name for name in visitor.top_level_names if _is_public(name)]

    # Symbols include private top-level names so symbols_index can still
    # resolve them, plus class methods recorded by the visitor. Keep a
    # deterministic order: top-level first, then class methods.
    symbols: list[str] = []
    seen: set[str] = set()
    for name in visitor.symbols:
        if name not in seen:
            seen.add(name)
            symbols.append(name)

    return {
        "path": rel_path,
        "module": module,
        "imports": visitor.imports,
        "exports": exports,
        "symbols": symbols,
        "default_export": None,
        "is_hotspot": False,
        "imported_by_count": 0,
        "_routes": visitor.routes,
    }


# ---------------------------------------------------------------------------
# Cross-file resolution
# ---------------------------------------------------------------------------


def _build_module_index(file_nodes: list[dict[str, Any]]) -> dict[str, str]:
    """Map every dotted module name to its repo-relative file path."""

    index: dict[str, str] = {}
    for entry in file_nodes:
        module = entry.get("module", "")
        if module:
            index.setdefault(module, entry["path"])
    return index


def _count_imports(file_nodes: list[dict[str, Any]]) -> dict[str, int]:
    """Count, for each file, how many other files import a module/name from it.

    Resolution rules:

    * Wildcard import ``a.b.*`` adds 1 to ``a.b`` (the package ``__init__``
      or the module file) when present in the index.
    * ``a.b.c`` is tried first as a module path; if not found, fall back to
      ``a.b`` (treating ``c`` as a name re-exported from module ``a.b``).
    """

    index = _build_module_index(file_nodes)

    counts: dict[str, int] = {}
    for entry in file_nodes:
        importer_path = entry["path"]
        for raw in entry.get("imports", []):
            target = raw
            if target.endswith(".*"):
                base = target[:-2]
                path = index.get(base)
                if path and path != importer_path:
                    counts[path] = counts.get(path, 0) + 1
                continue
            path = index.get(target)
            if path is None and "." in target:
                parent = target.rsplit(".", 1)[0]
                path = index.get(parent)
            if path and path != importer_path:
                counts[path] = counts.get(path, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Asset surfacing
# ---------------------------------------------------------------------------


def _make_asset_node(rel: str) -> dict[str, Any]:
    return {
        "path": rel,
        "module": "",
        "imports": [],
        "exports": [],
        "symbols": [],
        "default_export": None,
        "is_hotspot": False,
        "imported_by_count": 0,
        "_routes": [],
    }


def _collect_asset_files(
    repo_root: Path, already_scanned: set[str]
) -> list[dict[str, Any]]:
    """Surface root-level config files that aren't picked up as Python source.

    ``setup.py`` / ``manage.py`` are valid ``.py`` files and are already
    covered by the LibCST walk; we only add the rest as asset nodes here.
    """

    out: list[dict[str, Any]] = []
    for name in _ASSET_ROOT_FILES:
        if name in already_scanned:
            continue
        candidate = repo_root / name
        if candidate.exists() and candidate.is_file():
            out.append(_make_asset_node(name))
    return out


# ---------------------------------------------------------------------------
# Top-level scan
# ---------------------------------------------------------------------------


def scan(repo_root: Path) -> dict[str, Any]:
    """Scan ``repo_root`` and return the in-memory context graph dict."""

    repo_root = Path(repo_root).resolve()
    source_root = _detect_source_root(repo_root)

    file_nodes: list[dict[str, Any]] = []
    parse_failures: list[str] = []
    for py_file in _walk_py_files(repo_root):
        rel = _rel_posix(repo_root, py_file)
        try:
            source = py_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        module = _module_name(rel, source_root)
        is_init = rel.endswith("__init__.py")
        node = _scan_file(rel, source, module, is_init)
        if node is None:
            parse_failures.append(rel)
            continue
        file_nodes.append(node)

    counts = _count_imports(file_nodes)
    for entry in file_nodes:
        n = counts.get(entry["path"], 0)
        entry["imported_by_count"] = n
        entry["is_hotspot"] = n >= HOTSPOT_THRESHOLD

    scanned_paths = {entry["path"] for entry in file_nodes}
    file_nodes.extend(_collect_asset_files(repo_root, scanned_paths))
    file_nodes.sort(key=lambda e: e["path"])

    # symbols_index follows the same first-win rule as scan_java.py:
    # exports first (so the public name binds to its declaring file), then
    # the rest of the symbols.
    symbols_index: dict[str, str] = {}
    for entry in file_nodes:
        for sym in entry.get("exports", []):
            symbols_index.setdefault(sym, entry["path"])
    for entry in file_nodes:
        for sym in entry.get("symbols", []):
            symbols_index.setdefault(sym, entry["path"])

    hotspots = sorted(e["path"] for e in file_nodes if e.get("is_hotspot"))

    routes: list[dict[str, str]] = []
    for entry in file_nodes:
        routes.extend(entry.get("_routes", []))

    # Drop helper-only fields before serialisation -- the schema mirrors
    # scan.ts / scan_java.py exactly.
    public_files = [
        {k: v for k, v in entry.items() if k not in {"module", "_routes"}}
        for entry in file_nodes
    ]

    payload: dict[str, Any] = {
        "version": "1.0",
        "scanned_at": datetime.now(UTC).isoformat(),
        "root": str(repo_root),
        "language": "python",
        "files": public_files,
        "symbols_index": symbols_index,
        "hotspots": hotspots,
        "routes": routes,
    }
    if parse_failures:
        payload["parse_failures"] = sorted(parse_failures)
    return payload


def write_graph(repo_root: Path, out_path: Path) -> dict[str, Any]:
    """Scan ``repo_root`` and write the graph to ``out_path``."""

    graph = scan(repo_root)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(graph, indent=2, sort_keys=False) + "\n")
    return graph


def _parse_argv(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Emit an ACG context_graph.json for a Python repository."
    )
    parser.add_argument("--repo", required=True, help="Repository root.")
    parser.add_argument("--out", required=True, help="Path to context_graph.json.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_argv(list(sys.argv[1:] if argv is None else argv))
    graph = write_graph(Path(args.repo), Path(args.out))
    extra = ""
    if graph.get("parse_failures"):
        extra = f", {len(graph['parse_failures'])} parse failures"
    sys.stdout.write(
        f"wrote {args.out} ({len(graph['files'])} files, "
        f"{len(graph['hotspots'])} hotspots, "
        f"{len(graph.get('routes') or [])} routes, "
        f"language=python{extra})\n"
    )
    return 0


# Suppress the "imported but unused" lint for the matchers re-export; we
# expose it for downstream tooling tests but don't reference it directly here.
_ = m


if __name__ == "__main__":
    raise SystemExit(main())
