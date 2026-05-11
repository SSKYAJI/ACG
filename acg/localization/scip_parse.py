"""Parser for ``scip print --json`` output."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .types import ScipEntity, ScipIndexSummary, ScipReference

SCIP_DEFINITION_ROLE = 1


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return list(value.values())
    return []


def _symbol_name(symbol: str) -> str:
    stripped = symbol.rstrip(" .#/")
    if not stripped:
        return symbol
    for separator in ("#", ".", "/", " "):
        if separator in stripped:
            stripped = stripped.rsplit(separator, 1)[-1]
    return stripped or symbol


def _symbol_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("symbol", "value", "name"):
            item = value.get(key)
            if isinstance(item, str):
                return item
    return ""


def _normalize_path(path_value: Any, repo_root: Path | None) -> str:
    if not isinstance(path_value, str) or not path_value:
        return ""
    raw = path_value.replace("\\", "/")
    if raw.startswith("file://"):
        raw = raw.removeprefix("file://")
    path = Path(raw)
    is_windows_absolute = re.match(r"^[A-Za-z]:/", raw) is not None
    if path.is_absolute() or is_windows_absolute:
        if repo_root is None:
            return raw if is_windows_absolute else path.as_posix()
        try:
            return path.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            return ""
    if repo_root is not None:
        normalized = (repo_root / raw).resolve()
        try:
            return normalized.relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            return ""
    return Path(raw).as_posix()


def _document_path(document: dict[str, Any], repo_root: Path | None) -> str:
    candidates = [
        document.get("relative_path"),
        document.get("relativePath"),
        document.get("path"),
        document.get("uri"),
    ]
    text_document = document.get("text_document") or document.get("textDocument")
    if isinstance(text_document, dict):
        candidates.extend(
            [
                text_document.get("relative_path"),
                text_document.get("relativePath"),
                text_document.get("path"),
                text_document.get("uri"),
            ]
        )
    for candidate in candidates:
        path = _normalize_path(candidate, repo_root)
        if path:
            return path
    return ""


def _line_range(occurrence: dict[str, Any]) -> tuple[int | None, int | None]:
    range_value = occurrence.get("range") or occurrence.get("range_")
    if not isinstance(range_value, list) or not range_value:
        return None, None
    if all(isinstance(item, int) for item in range_value):
        start = range_value[0] + 1
        end = range_value[2] + 1 if len(range_value) >= 3 else start
        return start, end
    if len(range_value) >= 2 and all(isinstance(item, list) for item in range_value[:2]):
        start_item = range_value[0]
        end_item = range_value[1]
        if (
            start_item
            and end_item
            and isinstance(start_item[0], int)
            and isinstance(end_item[0], int)
        ):
            return start_item[0] + 1, end_item[0] + 1
    return None, None


def _role_name(occurrence: dict[str, Any]) -> str:
    raw_role = occurrence.get("symbol_roles", occurrence.get("symbolRoles", occurrence.get("role")))
    if isinstance(raw_role, int):
        return "definition" if raw_role & SCIP_DEFINITION_ROLE else "reference"
    if isinstance(raw_role, str):
        return "definition" if raw_role.lower() == "definition" else raw_role.lower()
    if isinstance(raw_role, list):
        lowered = {str(item).lower() for item in raw_role}
        return "definition" if "definition" in lowered else "reference"
    return "reference"


def _signature(symbol_info: dict[str, Any]) -> str:
    candidates = [
        symbol_info.get("signature"),
        symbol_info.get("signature_documentation"),
        symbol_info.get("signatureDocumentation"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str):
            return candidate
        if isinstance(candidate, dict):
            text = candidate.get("text") or candidate.get("value")
            if isinstance(text, str):
                return text
    documentation = symbol_info.get("documentation")
    if isinstance(documentation, list):
        for item in documentation:
            if isinstance(item, str):
                return item
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                return item["text"]
    return ""


def _collect_symbol_info(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    by_symbol: dict[str, dict[str, Any]] = {}
    containers: list[Any] = [payload.get("symbols")]
    containers.extend(document.get("symbols") for document in _as_list(payload.get("documents")))
    for container in containers:
        for item in _as_list(container):
            if not isinstance(item, dict):
                continue
            symbol = _symbol_text(item.get("symbol") or item.get("name"))
            if symbol and symbol not in by_symbol:
                by_symbol[symbol] = item
    return by_symbol


def _iter_occurrences(
    payload: dict[str, Any],
    repo_root: Path | None,
) -> list[tuple[str, dict[str, Any]]]:
    pairs: list[tuple[str, dict[str, Any]]] = []
    for document in _as_list(payload.get("documents")):
        if not isinstance(document, dict):
            continue
        path = _document_path(document, repo_root)
        if not path:
            continue
        for occurrence in _as_list(document.get("occurrences")):
            if isinstance(occurrence, dict):
                pairs.append((path, occurrence))
    for occurrence in _as_list(payload.get("occurrences")):
        if not isinstance(occurrence, dict):
            continue
        path = _normalize_path(
            occurrence.get("path")
            or occurrence.get("relative_path")
            or occurrence.get("relativePath")
            or occurrence.get("document"),
            repo_root,
        )
        if path:
            pairs.append((path, occurrence))
    return pairs


def parse_scip_json(
    payload: dict,
    repo_root: Path | None = None,
) -> tuple[list[ScipEntity], list[ScipReference], ScipIndexSummary]:
    """Parse SCIP JSON into compact definition entities and references."""

    if not isinstance(payload, dict):
        return [], [], ScipIndexSummary()

    symbol_info = _collect_symbol_info(payload)
    entity_by_key: dict[tuple[str, str, str, int | None], ScipEntity] = {}
    reference_by_key: dict[tuple[str, str, int | None, str], ScipReference] = {}
    files: set[str] = set()

    for path, occurrence in _iter_occurrences(payload, repo_root):
        files.add(path)
        symbol = _symbol_text(occurrence.get("symbol"))
        if not symbol:
            continue
        line_start, line_end = _line_range(occurrence)
        role = _role_name(occurrence)
        if role == "definition":
            info = symbol_info.get(symbol, {})
            name = (
                info.get("display_name")
                or info.get("displayName")
                or info.get("name")
                or _symbol_name(symbol)
            )
            kind = str(info.get("kind") or info.get("syntax_kind") or info.get("syntaxKind") or "")
            key = (path, symbol, role, line_start)
            if key not in entity_by_key:
                entity_by_key[key] = ScipEntity(
                    path=path,
                    symbol=symbol,
                    name=str(name),
                    kind=kind,
                    line_start=line_start,
                    line_end=line_end,
                    signature=_signature(info),
                    role=role,
                )
        else:
            key = (path, symbol, line_start, role)
            reference_by_key.setdefault(
                key,
                ScipReference(path=path, symbol=symbol, line=line_start, role=role or "reference"),
            )

    references_by_symbol: dict[str, set[str]] = {}
    for reference in reference_by_key.values():
        references_by_symbol.setdefault(reference.symbol, set()).add(reference.path)

    entities = sorted(
        entity_by_key.values(),
        key=lambda item: (
            item.path,
            item.line_start if item.line_start is not None else -1,
            item.symbol,
        ),
    )
    for entity in entities:
        entity.references = sorted(references_by_symbol.get(entity.symbol, set()))

    references = sorted(
        reference_by_key.values(),
        key=lambda item: (
            item.path,
            item.line if item.line is not None else -1,
            item.symbol,
            item.role,
        ),
    )
    summary = ScipIndexSummary(
        file_count=len(files),
        symbol_count=len({entity.symbol for entity in entities}),
        reference_count=len(references),
    )
    return entities, references, summary
