from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

GRAPH_VERSION = "1.0"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LANGUAGE_ALIASES = {
    "auto": "auto",
    "ts": "typescript",
    "typescript": "typescript",
    "js": "javascript",
    "javascript": "javascript",
    "java": "java",
    "python": "python",
}
CONFIG_FILENAMES = {
    ".env.example",
    "build.gradle",
    "build.gradle.kts",
    "cypress.config.js",
    "cypress.config.ts",
    "drizzle.config.js",
    "drizzle.config.ts",
    "jest.config.js",
    "jest.config.mjs",
    "jest.config.ts",
    "manage.py",
    "next.config.js",
    "next.config.mjs",
    "next.config.ts",
    "package.json",
    "Pipfile",
    "Pipfile.lock",
    "playwright.config.js",
    "playwright.config.mjs",
    "playwright.config.ts",
    "poetry.lock",
    "pom.xml",
    "postcss.config.cjs",
    "postcss.config.js",
    "pyproject.toml",
    "pytest.ini",
    "requirements.txt",
    "settings.gradle",
    "setup.cfg",
    "setup.py",
    "tailwind.config.js",
    "tailwind.config.ts",
    "tox.ini",
    "tsconfig.json",
    "vite.config.js",
    "vite.config.mjs",
    "vite.config.ts",
    "vitest.config.js",
    "vitest.config.mjs",
    "vitest.config.ts",
}
CONFIG_SUFFIXES = (
    ".config.cjs",
    ".config.js",
    ".config.mjs",
    ".config.ts",
    ".schema.json",
    ".prisma",
)
TEST_DIRS = {"__tests__", "cypress", "e2e", "spec", "test", "tests"}
TEST_SUFFIXES = (
    ".cy.js",
    ".cy.jsx",
    ".cy.ts",
    ".cy.tsx",
    ".spec.js",
    ".spec.jsx",
    ".spec.ts",
    ".spec.tsx",
    ".test.js",
    ".test.jsx",
    ".test.ts",
    ".test.tsx",
)
PYTHON_TEST_FILENAMES = {"conftest.py"}
CODE_EXTENSIONS = {".java", ".js", ".jsx", ".py", ".ts", ".tsx"}


class GraphScanError(RuntimeError):
    pass


def context_graph_path(repo_root: Path) -> Path:
    return Path(repo_root) / ".acg" / "context_graph.json"


def normalize_language(language: str | None, *, allow_auto: bool = True) -> str:
    key = (language or "auto").strip().lower()
    normalized = LANGUAGE_ALIASES.get(key)
    if normalized is None:
        expected = ", ".join(sorted(LANGUAGE_ALIASES))
        raise ValueError(f"unsupported language {language!r}; expected one of: {expected}")
    if normalized == "auto" and not allow_auto:
        raise ValueError("language must resolve to typescript, javascript, or java")
    return normalized


def detect_language(repo_root: Path) -> str:
    root = Path(repo_root)
    if any((root / name).exists() for name in ("pom.xml", "build.gradle", "build.gradle.kts")):
        return "java"
    if any(
        (root / name).exists() for name in ("tsconfig.json", "next.config.ts", "vite.config.ts")
    ):
        return "typescript"
    if any(
        (root / name).exists()
        for name in ("pyproject.toml", "setup.py", "setup.cfg", "Pipfile", "manage.py")
    ):
        return "python"

    counts = {"java": 0, "typescript": 0, "javascript": 0, "python": 0}
    for path in _walk_code_files(root):
        if path.suffix == ".java":
            counts["java"] += 1
        elif path.suffix in {".ts", ".tsx"}:
            counts["typescript"] += 1
        elif path.suffix in {".js", ".jsx"}:
            counts["javascript"] += 1
        elif path.suffix == ".py":
            counts["python"] += 1

    if counts["typescript"]:
        return "typescript"
    if counts["javascript"] or (root / "package.json").exists():
        return "javascript"
    if counts["java"]:
        return "java"
    if counts["python"]:
        return "python"
    return "typescript"


def load_context_graph(repo_root: Path) -> dict[str, Any]:
    path = context_graph_path(repo_root)
    if not path.exists():
        return {}
    try:
        graph = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return normalize_context_graph(graph, repo_root=repo_root)


def scan_context_graph(
    repo_root: Path,
    language: str = "auto",
    out_path: Path | None = None,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    normalized_language = normalize_language(language)
    if normalized_language == "auto":
        normalized_language = detect_language(root)
    out = Path(out_path).resolve() if out_path is not None else context_graph_path(root)

    if normalized_language == "java":
        graph = _scan_java(root, out)
    elif normalized_language == "python":
        graph = _scan_python(root, out)
    elif normalized_language in {"typescript", "javascript"}:
        graph = _scan_typescript(root, out)
    else:
        raise ValueError(f"unsupported language {language!r}")

    normalized = normalize_context_graph(graph, repo_root=root, language=normalized_language)
    _write_context_graph(out, normalized)
    return normalized


def normalize_context_graph(
    graph: dict[str, Any],
    *,
    repo_root: Path | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    payload = dict(graph) if isinstance(graph, dict) else {}
    root = Path(repo_root).resolve() if repo_root is not None else None
    files = _normalize_files(payload.get("files"))
    resolved_language = _resolve_graph_language(payload, root, language)
    symbols_index = _symbols_index(payload.get("symbols_index"), files)
    imports = {entry["path"]: _string_list(entry.get("imports")) for entry in files}
    exports = {entry["path"]: _string_list(entry.get("exports")) for entry in files}
    hotspots = _hotspots(payload.get("hotspots"), files)

    payload["version"] = str(payload.get("version") or GRAPH_VERSION)
    if root is not None:
        payload["root"] = str(root)
    elif not isinstance(payload.get("root"), str):
        payload["root"] = ""
    payload["language"] = resolved_language
    payload["languages"] = _languages(payload.get("languages"), resolved_language)
    payload["files"] = files
    payload["symbols_index"] = symbols_index
    payload["imports"] = imports
    payload["exports"] = exports
    payload["hotspots"] = hotspots
    payload["routes"] = _routes(payload.get("routes"), files)
    payload["configs"] = _path_union(
        payload.get("configs"), (path for path in imports if _is_config_path(path))
    )
    payload["tests"] = _path_union(
        payload.get("tests"), (path for path in imports if _is_test_path(path))
    )
    return payload


def _scan_java(repo_root: Path, out_path: Path) -> dict[str, Any]:
    from graph_builder import scan_java

    return scan_java.write_graph(repo_root, out_path)


def _scan_python(repo_root: Path, out_path: Path) -> dict[str, Any]:
    from graph_builder import scan_python

    return scan_python.write_graph(repo_root, out_path)


def _scan_typescript(repo_root: Path, out_path: Path) -> dict[str, Any]:
    builder = PROJECT_ROOT / "graph_builder"
    if not builder.exists():
        raise GraphScanError("graph_builder/ is missing; cannot build TypeScript repo graph")
    try:
        subprocess.run(
            ["npm", "run", "scan", "--", "--repo", str(repo_root), "--out", str(out_path)],
            cwd=builder,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError as exc:
        raise GraphScanError(
            "npm is required to scan TypeScript/JavaScript repos; run make install"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise GraphScanError("TypeScript/JavaScript repo graph scan timed out") from exc
    except subprocess.CalledProcessError as exc:
        detail = "\n".join(part for part in (exc.stdout, exc.stderr) if part).strip()
        message = "TypeScript/JavaScript repo graph scan failed"
        if detail:
            message = f"{message}:\n{detail}"
        raise GraphScanError(message) from exc

    try:
        return json.loads(out_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise GraphScanError(f"could not read scanner output at {out_path}: {exc}") from exc


def _write_context_graph(path: Path, graph: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(graph, indent=2, sort_keys=False) + "\n")


_PYTHON_IGNORE_DIRS = {
    ".acg",
    ".eggs",
    ".git",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "site-packages",
    "target",
    "venv",
}


def _walk_code_files(repo_root: Path) -> list[Path]:
    if not repo_root.exists():
        return []
    out: list[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or path.suffix not in CODE_EXTENSIONS:
            continue
        rel_parts = path.relative_to(repo_root).parts
        if any(part in _PYTHON_IGNORE_DIRS for part in rel_parts):
            continue
        if any(part.endswith(".egg-info") for part in rel_parts):
            continue
        out.append(path)
    return sorted(out)


def _normalize_files(value: Any) -> list[dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(value, list):
        return []
    for entry in value:
        if isinstance(entry, str):
            node = {"path": entry}
        elif isinstance(entry, dict) and isinstance(entry.get("path"), str):
            node = dict(entry)
        else:
            continue
        path = node["path"].strip("/")
        if not path:
            continue
        node["path"] = path
        node["imports"] = _string_list(node.get("imports"))
        node["exports"] = _string_list(node.get("exports"))
        node["symbols"] = _string_list(node.get("symbols"))
        node["default_export"] = (
            node.get("default_export") if isinstance(node.get("default_export"), str) else None
        )
        node["is_hotspot"] = bool(node.get("is_hotspot"))
        node["imported_by_count"] = _int_value(node.get("imported_by_count"))
        out[path] = node
    return [out[path] for path in sorted(out)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({item for item in value if isinstance(item, str) and item})


def _int_value(value: Any) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(0, value)
    return 0


def _resolve_graph_language(
    payload: dict[str, Any], repo_root: Path | None, language: str | None
) -> str:
    candidate = language
    if candidate is None or candidate == "auto":
        existing = payload.get("language")
        candidate = existing if isinstance(existing, str) and existing else None
    if candidate is None and repo_root is not None:
        candidate = detect_language(repo_root)
    if candidate is None:
        return "unknown"
    try:
        return normalize_language(candidate, allow_auto=False)
    except ValueError:
        return candidate.strip().lower() or "unknown"


def _languages(value: Any, primary: str) -> list[str]:
    items: list[str] = []
    if primary and primary != "unknown":
        items.append(primary)
    if isinstance(value, list):
        items.extend(item for item in value if isinstance(item, str) and item)
    return _unique_sorted(items)


def _symbols_index(value: Any, files: list[dict[str, Any]]) -> dict[str, str]:
    index: dict[str, str] = {}
    if isinstance(value, dict):
        for symbol, path_value in value.items():
            path = _first_path(path_value)
            if isinstance(symbol, str) and symbol and path:
                index.setdefault(symbol, path)
    for entry in files:
        path = entry["path"]
        for symbol in [*entry.get("exports", []), *entry.get("symbols", [])]:
            if isinstance(symbol, str) and symbol:
                index.setdefault(symbol, path)
    return {symbol: index[symbol] for symbol in sorted(index)}


def _first_path(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item:
                return item
    return None


def _hotspots(value: Any, files: list[dict[str, Any]]) -> list[str]:
    paths = [entry["path"] for entry in files if entry.get("is_hotspot")]
    if isinstance(value, list):
        paths.extend(item for item in value if isinstance(item, str) and item)
    return _unique_sorted(paths)


def _path_union(value: Any, derived: Iterable[str]) -> list[str]:
    paths = list(derived)
    if isinstance(value, list):
        paths.extend(item for item in value if isinstance(item, str) and item)
    return _unique_sorted(paths)


def _routes(value: Any, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    routes: list[dict[str, Any]] = []
    if isinstance(value, list):
        routes.extend(
            item for item in value if isinstance(item, dict) and isinstance(item.get("path"), str)
        )
    for entry in files:
        route = _route_for_path(entry["path"])
        if route is not None:
            routes.append(route)
    by_key = {(item.get("path"), item.get("kind"), item.get("route")): item for item in routes}
    return [
        by_key[key]
        for key in sorted(
            by_key, key=lambda item: tuple("" if part is None else str(part) for part in item)
        )
    ]


def _route_for_path(path: str) -> dict[str, Any] | None:
    parts = path.split("/")
    if not parts:
        return None
    filename = parts[-1]
    stem = filename.split(".", 1)[0]
    if stem in {"layout", "page", "route"}:
        app_index = _app_index(parts)
        if app_index is None:
            return None
        route_parts = parts[app_index + 1 : -1]
        route = "/" + "/".join(route_parts) if route_parts else "/"
        kind = "api" if stem == "route" and route_parts[:1] == ["api"] else stem
        return {"path": path, "kind": kind, "route": route}
    if filename.endswith("Controller.java"):
        return {"path": path, "kind": "spring_controller", "route": None}
    return None


def _app_index(parts: list[str]) -> int | None:
    if parts[:1] == ["app"]:
        return 0
    if parts[:2] == ["src", "app"]:
        return 1
    return None


def _is_config_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if name in CONFIG_FILENAMES or name.startswith(".env"):
        return True
    if path in {"prisma/schema.prisma", "drizzle/schema.ts", "schema.prisma"}:
        return True
    return name.endswith(CONFIG_SUFFIXES)


def _is_test_path(path: str) -> bool:
    parts = path.split("/")
    if any(part in TEST_DIRS for part in parts):
        return True
    if path.startswith("src/test/java/"):
        return True
    name = parts[-1]
    if name in PYTHON_TEST_FILENAMES:
        return True
    if name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py")):
        return True
    return path.endswith(TEST_SUFFIXES)


def _unique_sorted(values: Iterable[str]) -> list[str]:
    return sorted({value for value in values if value})
