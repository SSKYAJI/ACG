from __future__ import annotations

from pathlib import Path
from typing import Any

from acg.index.aggregate import aggregate
from acg.schema import PredictedWrite, TaskInput


class StubIndexer:
    def __init__(self, name: str, writes: list[PredictedWrite]) -> None:
        self.name = name
        self._writes = writes

    def predict(self, task: TaskInput, repo_root: Path | None, repo_graph: dict[str, Any]) -> list[PredictedWrite]:
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
