"""Backend helpers for optional SCIP-backed localization."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from shutil import which
from typing import Any

from .scip_parse import parse_scip_json
from .types import ScipIndexSummary, ScipStatus


def scip_cache_dir(repo_root: Path) -> Path:
    digest = hashlib.sha256(str(repo_root.resolve()).encode("utf-8")).hexdigest()[:16]
    return Path.home() / ".cache" / "acg" / "scip" / digest


def discover_command(name: str) -> str | None:
    return which(name)


def select_scip_index_command(repo_root: Path, language: str) -> list[str]:
    commands = _candidate_scip_index_commands(repo_root, language)
    return commands[0] if commands else []


def _candidate_scip_index_commands(repo_root: Path, language: str) -> list[list[str]]:
    language_key = language.lower()
    if language_key in {"javascript", "typescript", "js", "ts", "tsx"}:
        command = discover_command("scip-typescript")
        if command is None:
            return []
        out = [command, "index"]
        if not (repo_root / "tsconfig.json").exists():
            out.append("--infer-tsconfig")
        return [out]
    if language_key in {"python", "py"}:
        commands: list[list[str]] = []
        for candidate in ("scip-python-plus", "scip-python"):
            command = discover_command(candidate)
            if command is not None:
                commands.append(
                    [
                        command,
                        "index",
                        "--project-name",
                        repo_root.name,
                        "--project-version",
                        "workspace",
                    ]
                )
        return commands
    del repo_root
    return []


def select_scip_print_command() -> list[str]:
    command = discover_command("scip")
    if command is None:
        return []
    return [command, "print"]


def _status_dict(status: ScipStatus, summary: ScipIndexSummary | None = None) -> dict[str, Any]:
    summary = summary or ScipIndexSummary()
    return {
        "localization_backend": "scip",
        "scip_status": status.to_dict(),
        "scip_summary": summary.to_dict(),
        "scip_entities": [],
        "scip_references": [],
    }


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("NODE_OPTIONS", "--max-old-space-size=8192")
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
        env=env,
        timeout=300,
    )


def _file_metadata(entities: list[Any], references: list[Any]) -> list[dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    for entity in entities:
        path = getattr(entity, "path", "")
        if not path:
            continue
        item = by_path.setdefault(
            path,
            {
                "path": path,
                "scip_symbols": [],
                "scip_definition_count": 0,
                "scip_reference_count": 0,
            },
        )
        item["scip_definition_count"] += 1
        name = getattr(entity, "name", "") or getattr(entity, "symbol", "")
        if name:
            item["scip_symbols"].append(name)
    for reference in references:
        path = getattr(reference, "path", "")
        if not path:
            continue
        item = by_path.setdefault(
            path,
            {
                "path": path,
                "scip_symbols": [],
                "scip_definition_count": 0,
                "scip_reference_count": 0,
            },
        )
        item["scip_reference_count"] += 1
    out: list[dict[str, Any]] = []
    for path in sorted(by_path):
        item = by_path[path]
        item["scip_symbols"] = sorted(set(item["scip_symbols"]))
        out.append(item)
    return out


def build_scip_metadata(repo_root: Path, language: str, mode: str = "scip") -> dict[str, Any]:
    """Build optional SCIP metadata, returning an unavailable status on failure."""

    if mode != "scip":
        return _status_dict(ScipStatus(status="disabled", reason=f"mode={mode}"))

    cache_dir = scip_cache_dir(repo_root)
    index_path = cache_dir / "index.scip"
    index_commands = _candidate_scip_index_commands(repo_root, language)
    if not index_commands:
        return _status_dict(
            ScipStatus(
                status="unavailable",
                reason=f"no SCIP indexer found for language {language}",
                index_path=str(index_path),
                cache_dir=str(cache_dir),
            )
        )
    print_command = select_scip_print_command()
    if not print_command:
        return _status_dict(
            ScipStatus(
                status="unavailable",
                reason="scip CLI not found",
                index_path=str(index_path),
                cache_dir=str(cache_dir),
                command=index_commands[0] if index_commands else [],
            )
        )

    cache_dir.mkdir(parents=True, exist_ok=True)
    repo_index_path = repo_root / "index.scip"
    had_repo_index = repo_index_path.exists()
    original_repo_index = repo_index_path.read_bytes() if had_repo_index else b""
    attempted_errors: list[str] = []
    successful_command: list[str] = []
    try:
        for index_command in index_commands:
            full_index_command = [*index_command, "--output", str(index_path)]
            try:
                result = _run(full_index_command, repo_root)
            except (OSError, subprocess.SubprocessError) as exc:
                attempted_errors.append(f"{full_index_command}: {type(exc).__name__}: {exc}")
                continue
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "SCIP indexing failed").strip()
                attempted_errors.append(f"{full_index_command}: {detail}")
                continue
            if not index_path.exists():
                attempted_errors.append(
                    f"{full_index_command}: SCIP indexing did not produce {index_path}"
                )
                continue
            successful_command = full_index_command
            break
        if not successful_command:
            return _status_dict(
                ScipStatus(
                    status="unavailable",
                    reason="; ".join(attempted_errors) or "SCIP indexing failed",
                    index_path=str(index_path),
                    cache_dir=str(cache_dir),
                    command=index_commands[0],
                )
            )
    finally:
        if had_repo_index:
            repo_index_path.write_bytes(original_repo_index)
        else:
            repo_index_path.unlink(missing_ok=True)

    full_print_command = [*print_command, "--json", str(index_path)]
    try:
        result = _run(full_print_command, repo_root)
    except (OSError, subprocess.SubprocessError) as exc:
        return _status_dict(
            ScipStatus(
                status="unavailable",
                reason=f"{type(exc).__name__}: {exc}",
                index_path=str(index_path),
                cache_dir=str(cache_dir),
                command=full_print_command,
            )
        )
    if result.returncode != 0:
        return _status_dict(
            ScipStatus(
                status="unavailable",
                reason=(result.stderr or result.stdout or "SCIP print failed").strip(),
                index_path=str(index_path),
                cache_dir=str(cache_dir),
                command=full_print_command,
            )
        )
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        return _status_dict(
            ScipStatus(
                status="unavailable",
                reason=f"invalid SCIP JSON: {exc}",
                index_path=str(index_path),
                cache_dir=str(cache_dir),
                command=full_print_command,
            )
        )

    entities, references, summary = parse_scip_json(payload, repo_root=repo_root)
    return {
        "localization_backend": "scip",
        "scip_status": ScipStatus(
            status="ok",
            index_path=str(index_path),
            cache_dir=str(cache_dir),
            command=successful_command,
        ).to_dict(),
        "scip_summary": summary.to_dict(),
        "scip_entities": [entity.to_dict() for entity in entities],
        "scip_references": [reference.to_dict() for reference in references],
        "files": _file_metadata(entities, references),
    }
