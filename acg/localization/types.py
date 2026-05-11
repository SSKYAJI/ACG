"""Shared types for optional SCIP-backed localization."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ScipStatus:
    status: str
    reason: str = ""
    index_path: str = ""
    cache_dir: str = ""
    command: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScipEntity:
    path: str
    symbol: str
    name: str
    kind: str = ""
    line_start: int | None = None
    line_end: int | None = None
    signature: str = ""
    role: str = "definition"
    references: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScipReference:
    path: str
    symbol: str
    line: int | None = None
    role: str = "reference"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScipIndexSummary:
    file_count: int = 0
    symbol_count: int = 0
    reference_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
