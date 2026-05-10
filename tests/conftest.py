"""Shared pytest fixtures.

The repo root is added to ``sys.path`` so test modules can import the
``acg`` package without requiring the project to be ``pip install -e``-ed
first. The Tier 2 acceptance gate documents both invocation styles.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Fixture directories may contain real ``test_*.py`` files (e.g. the Python
# scanner fixtures under ``tests/fixtures/tiny_py_*/``). Those are *fixtures*,
# not tests for the ACG package, so pytest should not try to collect them.
collect_ignore_glob = ["fixtures/*/tests/*", "fixtures/*/*/tests/*"]


@pytest.fixture
def schema_path() -> Path:
    return ROOT / "schema" / "agent_lock.schema.json"


@pytest.fixture
def schema_dict(schema_path: Path) -> dict:
    return json.loads(schema_path.read_text())


@pytest.fixture
def example_tasks_path() -> Path:
    return ROOT / "examples" / "tasks.example.json"


@pytest.fixture
def example_dag_lockfile_path() -> Path:
    return ROOT / "examples" / "lockfile.dag.example.json"


@pytest.fixture
def example_simple_lockfile_path() -> Path:
    return ROOT / "examples" / "lockfile.simple.example.json"
