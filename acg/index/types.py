"""Shared types for deterministic indexers."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from acg.schema import PredictedWrite, TaskInput


class Indexer(Protocol):
    """A pure deterministic indexer: task + repo state -> predicted writes."""

    name: str

    def predict(
        self,
        task: TaskInput,
        repo_root: Path | None,
        repo_graph: dict,
    ) -> list[PredictedWrite]: ...
