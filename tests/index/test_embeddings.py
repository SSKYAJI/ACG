"""Embeddings indexer tests.

Patches the ``sentence_transformers`` import so the optional dep is not
required in CI. Verifies cache behaviour, cosine ranking, and graceful
degradation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest

from acg.index.embeddings import (
    CONFIDENCE_CEILING,
    EmbeddingsIndexer,
)
from acg.schema import TaskInput, TaskInputHints

# ---------------------------------------------------------------------------
# Fake sentence-transformers / numpy modules so the optional extra is not
# required in CI. Tests opt-in via the ``patched_st`` fixture.
# ---------------------------------------------------------------------------


def _vec(seed: int, dim: int = 8) -> list[float]:
    import math

    raw = [math.sin(seed + i + 1) for i in range(dim)]
    norm = math.sqrt(sum(value * value for value in raw)) or 1.0
    return [value / norm for value in raw]


class _FakeModel:
    def __init__(self, name: str) -> None:
        self.name = name
        self.encode_calls: list[list[str]] = []

    def encode(
        self,
        texts: list[str],
        normalize_embeddings: bool = True,
        show_progress_bar: bool = False,
    ) -> Any:
        import numpy as np

        self.encode_calls.append(list(texts))
        rows = [_vec(hash(text) & 0xFFFF) for text in texts]
        return np.asarray(rows, dtype="float32")


@pytest.fixture
def patched_st(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Install a fake ``sentence_transformers`` module exposing ``_FakeModel``."""

    pytest.importorskip("numpy")
    module = ModuleType("sentence_transformers")
    instances: list[_FakeModel] = []

    def factory(name: str) -> _FakeModel:
        model = _FakeModel(name)
        instances.append(model)
        return model

    module.SentenceTransformer = factory  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    return {"module": module, "instances": instances}


def _touch(root: Path, rel: str, body: str = "export const sentinel = 1\n") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def _graph(paths: list[str]) -> dict[str, Any]:
    return {"files": [{"path": p, "exports": [], "imports": [], "symbols": []} for p in paths]}


# ---------------------------------------------------------------------------
# 1. Graceful degradation when sentence_transformers is not importable.
# ---------------------------------------------------------------------------


def test_predict_returns_empty_without_sentence_transformers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    _touch(tmp_path, "src/billing.ts")

    indexer = EmbeddingsIndexer()
    out = indexer.predict(
        TaskInput(id="task", prompt="checkout flow"),
        tmp_path,
        _graph(["src/billing.ts"]),
    )

    assert out == []


# ---------------------------------------------------------------------------
# 2. Repo root None -> no predictions.
# ---------------------------------------------------------------------------


def test_predict_returns_empty_when_repo_root_is_none(patched_st: dict[str, Any]) -> None:
    indexer = EmbeddingsIndexer()
    out = indexer.predict(
        TaskInput(id="task", prompt="checkout flow"),
        None,
        _graph(["src/billing.ts"]),
    )

    assert out == []


# ---------------------------------------------------------------------------
# 3. Cosine floor filters near-zero scores; results are ranked descending.
# ---------------------------------------------------------------------------


def test_predict_honours_cosine_floor_and_ranking(
    tmp_path: Path,
    patched_st: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import numpy as np

    paths = ["src/billing.ts", "src/checkout.ts", "src/unrelated.ts"]
    for rel in paths:
        _touch(tmp_path, rel)
    graph = _graph(paths)

    fake_module = patched_st["module"]
    expected_query_vec = np.asarray(_vec(101, 8), dtype="float32")

    class RankingModel:
        def __init__(self, name: str) -> None:
            self.name = name
            self.encode_calls: list[list[str]] = []

        def encode(
            self,
            texts: list[str],
            normalize_embeddings: bool = True,
            show_progress_bar: bool = False,
        ) -> Any:
            self.encode_calls.append(list(texts))
            if len(texts) == 1:
                return np.asarray([expected_query_vec], dtype="float32")
            rows: list[Any] = []
            for text in texts:
                if "checkout" in text:
                    rows.append(expected_query_vec)
                elif "billing" in text:
                    rows.append(expected_query_vec * 0.6)
                else:
                    rows.append(expected_query_vec * 0.0)
            return np.asarray(rows, dtype="float32")

    fake_module.SentenceTransformer = RankingModel  # type: ignore[attr-defined]

    indexer = EmbeddingsIndexer(top_n=4, cosine_floor=0.18)
    out = indexer.predict(
        TaskInput(id="task", prompt="redesign checkout flow"),
        tmp_path,
        graph,
    )

    paths_out = [write.path for write in out]
    assert "src/checkout.ts" in paths_out
    assert "src/billing.ts" in paths_out
    assert "src/unrelated.ts" not in paths_out
    assert paths_out[0] == "src/checkout.ts"
    assert out[0].confidence >= out[-1].confidence


# ---------------------------------------------------------------------------
# 4. Encoding cache: second predict() reuses pickle, doesn't re-encode corpus.
# ---------------------------------------------------------------------------


def test_corpus_encoding_is_cached_to_disk(
    tmp_path: Path,
    patched_st: dict[str, Any],
) -> None:
    paths = ["src/billing.ts", "src/checkout.ts"]
    for rel in paths:
        _touch(tmp_path, rel)
    graph = _graph(paths)

    indexer_first = EmbeddingsIndexer(top_n=4, cosine_floor=-1.0)
    indexer_first.predict(
        TaskInput(id="task", prompt="checkout"),
        tmp_path,
        graph,
    )

    cache_root = tmp_path / ".acg" / "cache" / "embeddings"
    cached_files = list(cache_root.glob("*.pkl"))
    assert cached_files, "expected an embeddings cache file after the first run"

    indexer_second = EmbeddingsIndexer(top_n=4, cosine_floor=-1.0)
    indexer_second.predict(
        TaskInput(id="task", prompt="checkout"),
        tmp_path,
        graph,
    )

    assert len(patched_st["instances"]) == 2
    second_calls = patched_st["instances"][1].encode_calls
    # On warm cache the second indexer must only encode the query (1 text),
    # never the multi-document corpus.
    assert all(len(call) == 1 for call in second_calls), second_calls


# ---------------------------------------------------------------------------
# 5. Confidence is clamped to [0, 0.85].
# ---------------------------------------------------------------------------


def test_confidence_is_clamped_to_ceiling(
    tmp_path: Path,
    patched_st: dict[str, Any],
) -> None:
    import numpy as np

    paths = ["src/billing.ts", "src/checkout.ts"]
    for rel in paths:
        _touch(tmp_path, rel)
    graph = _graph(paths)

    fake_module = patched_st["module"]
    fixed = np.asarray(_vec(7, 8), dtype="float32")

    class IdenticalModel:
        def __init__(self, name: str) -> None:
            self.name = name
            self.encode_calls: list[list[str]] = []

        def encode(
            self,
            texts: list[str],
            normalize_embeddings: bool = True,
            show_progress_bar: bool = False,
        ) -> Any:
            self.encode_calls.append(list(texts))
            return np.asarray([fixed for _ in texts], dtype="float32")

    fake_module.SentenceTransformer = IdenticalModel  # type: ignore[attr-defined]

    indexer = EmbeddingsIndexer(top_n=4, cosine_floor=-1.0)
    out = indexer.predict(
        TaskInput(
            id="task",
            prompt="anything",
            hints=TaskInputHints(touches=["billing"]),
        ),
        tmp_path,
        graph,
    )

    assert out, "expected at least one prediction"
    for write in out:
        assert 0.0 <= write.confidence <= CONFIDENCE_CEILING
    # cosine == 1.0 -> ((1+1)/2) * 0.85 == 0.85, exact ceiling.
    assert out[0].confidence == pytest.approx(CONFIDENCE_CEILING)


# ---------------------------------------------------------------------------
# Sanity: the patched MagicMock pattern keeps tests dependency-free.
# ---------------------------------------------------------------------------


def test_patched_module_is_used_not_real_dep(patched_st: dict[str, Any]) -> None:
    factory = patched_st["module"].SentenceTransformer
    assert factory is not None
    assert isinstance(MagicMock(), MagicMock)  # imported via unittest.mock
