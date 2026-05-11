"""Small text and repo helpers shared by deterministic indexers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9]*")
CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
PATH_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".go",
    ".java",
    ".rb",
    ".prisma",
    ".json",
    ".md",
    ".css",
    ".html",
    ".yml",
    ".yaml",
}
SKIP_DIRS = {
    ".acg",
    ".git",
    ".hg",
    ".mypy_cache",
    ".next",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
}


def tokenize(text: str) -> list[str]:
    """Tokenize identifiers, paths, snake_case, and camelCase into lowercase pieces."""

    tokens: list[str] = []
    for raw in TOKEN_RE.findall(text.replace("_", " ")):
        for piece in CAMEL_RE.sub(" ", raw).split():
            token = piece.lower()
            if token:
                tokens.append(token)
    return tokens


def unique(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def repo_files(repo_root: Path | None, repo_graph: dict[str, Any]) -> list[str]:
    graph_files = repo_graph.get("files") or []
    paths = [
        entry.get("path", "")
        for entry in graph_files
        if isinstance(entry, dict) and entry.get("path")
    ]
    if paths:
        return sorted(unique(paths))
    if repo_root is None or not repo_root.exists():
        return []
    out: list[str] = []
    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(repo_root).as_posix()
        if any(part in SKIP_DIRS for part in path.relative_to(repo_root).parts):
            continue
        if path.suffix in PATH_EXTENSIONS:
            out.append(rel)
    return sorted(out)


def graph_file_entries(repo_root: Path | None, repo_graph: dict[str, Any]) -> list[dict[str, Any]]:
    files = repo_graph.get("files") or []
    if files:
        return [entry for entry in files if isinstance(entry, dict) and entry.get("path")]
    return [
        {"path": path, "imports": [], "exports": [], "symbols": []}
        for path in repo_files(repo_root, repo_graph)
    ]


def read_rel(repo_root: Path | None, rel_path: str, max_chars: int = 200_000) -> str:
    if repo_root is None:
        return ""
    path = repo_root / rel_path
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except OSError:
        return ""


def cache_dir(repo_root: Path | None) -> Path | None:
    if repo_root is None:
        return None
    path = repo_root / ".acg" / "cache"
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return path


def clamp_confidence(value: float) -> float:
    return max(0.0, min(1.0, value))


def task_text(task: Any) -> str:
    hints = getattr(task, "hints", None)
    touches = " ".join(getattr(hints, "touches", []) or []) if hints else ""
    return f"{task.id} {task.prompt} {touches}"
