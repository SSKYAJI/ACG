"""Unit tests for :mod:`graph_builder.scan_java`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from graph_builder import scan_java

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "tiny_java_repo"


def _scan_fixture() -> dict:
    return scan_java.scan(FIXTURE_DIR)


def _files_by_path(graph: dict) -> dict[str, dict]:
    return {entry["path"]: entry for entry in graph["files"]}


def test_class_extraction_emits_top_level_types_only_as_exports() -> None:
    graph = _scan_fixture()
    files = _files_by_path(graph)

    event_path = "src/main/java/com/example/events/EventService.java"
    account_path = "src/main/java/com/example/accounts/AccountRepository.java"
    config_path = "src/main/java/com/example/config/DatabaseConfig.java"

    assert files[event_path]["exports"] == ["EventService"]
    assert files[account_path]["exports"] == ["AccountRepository"]
    assert files[config_path]["exports"] == ["DatabaseConfig"]

    # Symbols include the type plus public methods (and nested types when
    # declared inline). Nested ``NestedHelper`` interface inside
    # AccountRepository should be present in symbols but NOT in exports.
    assert "AccountRepository" in files[account_path]["symbols"]
    assert "NestedHelper" in files[account_path]["symbols"]
    assert "NestedHelper" not in files[account_path]["exports"]


def test_method_extraction_keeps_public_skips_private_and_constructors() -> None:
    graph = _scan_fixture()
    files = _files_by_path(graph)
    event_path = "src/main/java/com/example/events/EventService.java"
    symbols = files[event_path]["symbols"]

    assert "findUpcoming" in symbols
    assert "ordering" in symbols
    # Constructors share the class name and are intentionally not re-emitted as
    # method symbols. The helper() method is private and must be filtered out.
    assert "helper" not in symbols
    # The Comparator anonymous-class compare() lives inside a method body; we
    # only walk top-level type bodies, so it should not leak into the index.
    assert "compare" not in symbols


def test_symbols_index_merges_across_files_and_first_wins() -> None:
    graph = _scan_fixture()
    index = graph["symbols_index"]

    assert index["EventService"].endswith("EventService.java")
    assert index["AccountRepository"].endswith("AccountRepository.java")
    assert index["DatabaseConfig"].endswith("DatabaseConfig.java")
    # ``jdbcTemplate`` (lowercase, the @Bean method) must point at the
    # DatabaseConfig file because that is the only file that *defines* it.
    assert index["jdbcTemplate"].endswith("DatabaseConfig.java")


def test_imports_and_hotspot_count_track_internal_references() -> None:
    graph = _scan_fixture()
    files = _files_by_path(graph)
    event_path = "src/main/java/com/example/events/EventService.java"
    config_path = "src/main/java/com/example/config/DatabaseConfig.java"

    # The Spring import must survive the import collector verbatim.
    assert "org.springframework.jdbc.core.JdbcTemplate" in files[event_path]["imports"]
    assert "com.example.config.DatabaseConfig" in files[event_path]["imports"]

    # DatabaseConfig is imported by both EventService and AccountRepository,
    # so its imported_by_count should be 2. Threshold is 3, so it is not yet
    # a hotspot in the tiny fixture — that is the correct behaviour.
    assert files[config_path]["imported_by_count"] == 2
    assert files[config_path]["is_hotspot"] is False


def test_empty_repo_returns_well_formed_graph(tmp_path: Path) -> None:
    graph = scan_java.scan(tmp_path)
    assert graph["language"] == "java"
    assert graph["version"] == "1.0"
    assert graph["files"] == []
    assert graph["symbols_index"] == {}
    assert graph["hotspots"] == []


def test_import_only_file_has_no_symbols(tmp_path: Path) -> None:
    """A file with only a package + imports should emit no exports/symbols."""
    src = tmp_path / "src/main/java/com/example/empty"
    src.mkdir(parents=True)
    (src / "package-info.java").write_text("package com.example.empty;\nimport java.util.List;\n")

    graph = scan_java.scan(tmp_path)
    assert len(graph["files"]) == 1
    entry = graph["files"][0]
    assert entry["exports"] == []
    assert entry["symbols"] == []
    assert entry["imports"] == ["java.util.List"]


def test_write_graph_persists_json_to_disk(tmp_path: Path) -> None:
    out_path = tmp_path / ".acg" / "context_graph.json"
    graph = scan_java.write_graph(FIXTURE_DIR, out_path)

    assert out_path.exists()
    on_disk = json.loads(out_path.read_text())

    # The disk payload must contain everything the predictor reads.
    for key in ("version", "language", "files", "symbols_index", "hotspots"):
        assert key in on_disk
    assert on_disk["language"] == "java"
    assert on_disk["files"] == graph["files"]


def test_main_cli_writes_to_out_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_path = tmp_path / "graph.json"
    rc = scan_java.main(["--repo", str(FIXTURE_DIR), "--out", str(out_path)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "language=java" in captured.out
    assert out_path.exists()
