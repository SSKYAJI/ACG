"""Tests for the Typer CLI entry-points.

Covers ``acg validate-lockfile`` end-to-end against the bundled example
lockfiles and the JSON Schema in ``schema/agent_lock.schema.json``.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from acg.cli import app

runner = CliRunner()


def test_validate_lockfile_ok(
    example_dag_lockfile_path: Path, schema_path: Path
) -> None:
    result = runner.invoke(
        app,
        [
            "validate-lockfile",
            "--lock",
            str(example_dag_lockfile_path),
            "--schema",
            str(schema_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "OK" in result.output


def test_validate_lockfile_rejects_invalid(
    tmp_path: Path, example_dag_lockfile_path: Path, schema_path: Path
) -> None:
    payload = json.loads(example_dag_lockfile_path.read_text())
    payload["version"] = "9.9"  # break the const "1.0"
    bad = tmp_path / "bad_lock.json"
    bad.write_text(json.dumps(payload))

    result = runner.invoke(
        app,
        [
            "validate-lockfile",
            "--lock",
            str(bad),
            "--schema",
            str(schema_path),
        ],
    )
    assert result.exit_code == 2, result.output
