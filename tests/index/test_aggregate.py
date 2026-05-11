from __future__ import annotations

from pathlib import Path
from typing import Any

from acg.index.aggregate import aggregate
from acg.schema import PredictedWrite, TaskInput


class StubIndexer:
    def __init__(self, name: str, writes: list[PredictedWrite]) -> None:
        self.name = name
        self._writes = writes

    def predict(
        self, task: TaskInput, repo_root: Path | None, repo_graph: dict[str, Any]
    ) -> list[PredictedWrite]:
        del task, repo_root, repo_graph
        return self._writes


def test_fusion_takes_max_confidence() -> None:
    task = TaskInput(id="x", prompt="task")
    writes = aggregate(
        task,
        None,
        {},
        indexers=[
            StubIndexer("a", [PredictedWrite(path="a.py", confidence=0.2, reason="low")]),
            StubIndexer("b", [PredictedWrite(path="a.py", confidence=0.7, reason="high")]),
        ],
    )

    assert writes[0].confidence == 0.7


def test_fusion_concatenates_reasons_once() -> None:
    task = TaskInput(id="x", prompt="task")
    writes = aggregate(
        task,
        None,
        {},
        indexers=[
            StubIndexer("a", [PredictedWrite(path="a.py", confidence=0.2, reason="same")]),
            StubIndexer("b", [PredictedWrite(path="a.py", confidence=0.7, reason="same")]),
            StubIndexer("c", [PredictedWrite(path="a.py", confidence=0.6, reason="other")]),
        ],
    )

    assert writes[0].reason == "same; other"


def test_top_n_cap_sorts_by_confidence_then_path() -> None:
    task = TaskInput(id="x", prompt="task")
    writes = aggregate(
        task,
        None,
        {},
        indexers=[
            StubIndexer(
                "a",
                [
                    PredictedWrite(path="b.py", confidence=0.9, reason="b"),
                    PredictedWrite(path="a.py", confidence=0.9, reason="a"),
                    PredictedWrite(path="c.py", confidence=0.8, reason="c"),
                ],
            )
        ],
        top_n=2,
    )

    assert [write.path for write in writes] == ["a.py", "b.py"]


def test_graph_expansion_runs_before_final_top_n() -> None:
    task = TaskInput(id="request", prompt="Update request validation handling")
    repo_graph = {
        "files": [
            {
                "path": "lib/handle-request.js",
                "symbols": ["handleRequest"],
                "resolved_imports": ["lib/validation.js"],
                "importers": [],
                "type_links": [],
            },
            {
                "path": "lib/validation.js",
                "symbols": ["validate"],
                "resolved_imports": [],
                "importers": ["lib/handle-request.js"],
                "type_links": [],
            },
        ],
        "resolved_imports": {"lib/handle-request.js": ["lib/validation.js"]},
        "importers": {"lib/validation.js": ["lib/handle-request.js"]},
    }
    writes = aggregate(
        task,
        None,
        repo_graph,
        indexers=[
            StubIndexer(
                "seed",
                [
                    PredictedWrite(
                        path="lib/handle-request.js",
                        confidence=0.95,
                        reason="seed",
                    )
                ],
            )
        ],
        top_n=2,
    )

    assert [write.path for write in writes] == [
        "lib/handle-request.js",
        "lib/validation.js",
    ]
