"""Tests for optional SCIP localization primitives."""

from __future__ import annotations

from pathlib import Path

from acg.localization import build_scip_metadata, parse_scip_json, select_scip_index_command


def test_parse_scip_json_handles_documents_symbols_and_dedupes(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    payload = {
        "documents": [
            {
                "relative_path": "src\\app.ts",
                "occurrences": [
                    {"symbol": "local 0 App#", "range": [1, 0, 3, 1], "symbol_roles": 1},
                    {"symbol": "local 0 helper#", "range": [6, 0, 6, 6], "symbol_roles": 0},
                    {"symbol": "local 0 App#", "range": [1, 0, 3, 1], "symbol_roles": 1},
                ],
                "symbols": [
                    {
                        "symbol": "local 0 App#",
                        "display_name": "App",
                        "kind": "Function",
                        "signature_documentation": {"text": "function App(): JSX.Element"},
                    },
                    {"symbol": "local 0 App#", "display_name": "Duplicate"},
                ],
            },
            {
                "relative_path": "../outside.ts",
                "occurrences": [
                    {"symbol": "local 0 Outside#", "range": [0, 0, 0, 1], "symbol_roles": 1}
                ],
            },
        ]
    }

    entities, references, summary = parse_scip_json(payload, repo_root=repo_root)

    assert len(entities) == 1
    assert entities[0].path == "src/app.ts"
    assert entities[0].symbol == "local 0 App#"
    assert entities[0].name == "App"
    assert entities[0].kind == "Function"
    assert entities[0].line_start == 2
    assert entities[0].line_end == 4
    assert entities[0].signature == "function App(): JSX.Element"
    assert len(references) == 1
    assert references[0].path == "src/app.ts"
    assert references[0].symbol == "local 0 helper#"
    assert references[0].line == 7
    assert summary.file_count == 1
    assert summary.symbol_count == 1
    assert summary.reference_count == 1


def test_parse_scip_json_handles_top_level_occurrences_and_symbol_dict(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    source = repo_root / "pkg" / "module.py"
    source.parent.mkdir()
    source.write_text("def target():\n    return target()\n")
    payload = {
        "occurrences": [
            {
                "path": str(source),
                "symbol": "python pkg/module.py Target().",
                "range": [[0, 4], [0, 10]],
                "symbolRoles": ["Definition"],
            },
            {
                "relativePath": "pkg/module.py",
                "symbol": "python pkg/module.py Target().",
                "range": [1, 11, 1, 17],
            },
            {
                "path": "C:\\outside\\module.py",
                "symbol": "python outside/module.py Outside().",
                "range": [0, 0, 0, 1],
                "symbolRoles": ["Definition"],
            },
        ],
        "symbols": {
            "target": {
                "symbol": "python pkg/module.py Target().",
                "displayName": "Target",
                "syntaxKind": "Function",
            }
        },
    }

    entities, references, summary = parse_scip_json(payload, repo_root=repo_root)

    assert [(entity.path, entity.name, entity.kind, entity.references) for entity in entities] == [
        ("pkg/module.py", "Target", "Function", ["pkg/module.py"])
    ]
    assert [(reference.path, reference.line) for reference in references] == [("pkg/module.py", 2)]
    assert summary.file_count == 1
    assert summary.symbol_count == 1
    assert summary.reference_count == 1


def test_build_scip_metadata_returns_unavailable_when_tools_are_missing(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("acg.localization.scip_backend.which", lambda name: None)

    metadata = build_scip_metadata(tmp_path, language="typescript")

    assert metadata["localization_backend"] == "scip"
    assert metadata["scip_status"]["status"] == "unavailable"
    assert "no SCIP indexer found" in metadata["scip_status"]["reason"]
    assert metadata["scip_entities"] == []
    assert metadata["scip_references"] == []
    assert str(Path.home() / ".cache" / "acg" / "scip") in metadata["scip_status"]["cache_dir"]


def test_js_command_uses_infer_tsconfig(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "acg.localization.scip_backend.which",
        lambda name: f"/bin/{name}" if name == "scip-typescript" else None,
    )

    assert select_scip_index_command(tmp_path, "typescript") == [
        "/bin/scip-typescript",
        "index",
        "--infer-tsconfig",
    ]


def test_js_command_skips_infer_tsconfig_when_tsconfig_exists(
    monkeypatch, tmp_path: Path
) -> None:
    (tmp_path / "tsconfig.json").write_text("{}")
    monkeypatch.setattr(
        "acg.localization.scip_backend.which",
        lambda name: f"/bin/{name}" if name == "scip-typescript" else None,
    )

    assert select_scip_index_command(tmp_path, "javascript") == [
        "/bin/scip-typescript",
        "index",
    ]


def test_python_command_prefers_scip_python_plus_then_fallback(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "acg.localization.scip_backend.which",
        lambda name: f"/bin/{name}" if name in {"scip-python", "scip-python-plus"} else None,
    )
    assert select_scip_index_command(tmp_path, "python") == [
        "/bin/scip-python-plus",
        "index",
        "--project-name",
        tmp_path.name,
        "--project-version",
        "workspace",
    ]

    monkeypatch.setattr(
        "acg.localization.scip_backend.which",
        lambda name: f"/bin/{name}" if name == "scip-python" else None,
    )
    assert select_scip_index_command(tmp_path, "python") == [
        "/bin/scip-python",
        "index",
        "--project-name",
        tmp_path.name,
        "--project-version",
        "workspace",
    ]
