"""Pydantic v2 models that mirror ``schema/agent_lock.schema.json``.

These models are the single source of truth for structured data crossing module
boundaries. Every public function in :mod:`acg` accepts and returns instances of
these classes (or plain dicts when interfacing with JSON files).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class _StrictModel(BaseModel):
    """Base model that forbids unknown fields and trims whitespace on strings."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Input models — what the user feeds the compiler via ``tasks.json``.
# ---------------------------------------------------------------------------


class TaskInputHints(_StrictModel):
    """Optional task hints that bias the predictor toward feature areas."""

    touches: list[str] = Field(default_factory=list)
    suspected_files: list[str] = Field(default_factory=list)


class TaskInput(_StrictModel):
    """A single task as supplied by the human author of ``tasks.json``."""

    id: str
    prompt: str
    hints: TaskInputHints | None = None
    depends_on: list[str] = Field(default_factory=list)

    @field_validator("id")
    @classmethod
    def _id_pattern(cls, v: str) -> str:
        import re

        if not re.fullmatch(r"[a-z0-9_-]+", v):
            raise ValueError(
                f"task id {v!r} must match ^[a-z0-9_-]+$ (lowercase alnum, dash, underscore)"
            )
        return v


class TasksInput(_StrictModel):
    """Root document of ``tasks.json``."""

    version: Literal["1.0"] = "1.0"
    tasks: list[TaskInput]
    tokens_planner_total: int | None = None


# ---------------------------------------------------------------------------
# Output models — what the compiler writes to ``agent_lock.json``.
# ---------------------------------------------------------------------------


class PredictedWrite(_StrictModel):
    """A predicted file write with confidence and short rationale."""

    path: str
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = ""


class FileScope(_StrictModel):
    """A localized file with its lockfile tier and supporting signals."""

    path: str
    tier: Literal["must_write", "candidate_context", "needs_replan"]
    score: float = Field(ge=0.0, le=1.0)
    signals: list[str] = Field(default_factory=list)
    reason: str = ""


class Task(_StrictModel):
    """A task as serialized into the lockfile."""

    id: str
    prompt: str
    predicted_writes: list[PredictedWrite]
    allowed_paths: list[str]
    candidate_context_paths: list[str] = Field(default_factory=list)
    file_scopes: list[FileScope] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    parallel_group: int | None = Field(default=None, ge=1)
    rationale: str | None = None


class Group(_StrictModel):
    """A node in the execution DAG; one group runs after its ``waits_for``."""

    id: int = Field(ge=1)
    tasks: list[str]
    type: Literal["parallel", "serial"]
    waits_for: list[int] = Field(default_factory=list)


class ExecutionPlan(_StrictModel):
    """Topologically ordered execution plan."""

    groups: list[Group]


class Conflict(_StrictModel):
    """A detected conflict between two or more tasks on shared files."""

    files: list[str]
    between_tasks: list[str]
    resolution: str


class Repo(_StrictModel):
    """Repository metadata captured at compile time."""

    root: str
    git_url: str | None = None
    commit: str | None = None
    languages: list[str]


class Generator(_StrictModel):
    """Generator metadata for provenance.

    ``tokens_planner_total`` is the headline planner-prompt token count
    consumed during compile. When the LLM provider returns a ``usage`` block
    (OpenAI / OpenRouter), this value is the sum of
    ``usage.prompt_tokens`` across every planner call and
    ``tokens_planner_method`` is ``"provider_usage"``. When the provider
    omits usage (self-hosted vLLM), the compiler falls back to a
    ``chars // 4`` estimate and ``tokens_planner_method = "estimate_chars_div_4"``.

    ``compile_wall_seconds`` is wall-clock time spent inside
    :func:`acg.compiler.compile_lockfile`. ``compile_cost_usd`` is the
    sum of provider-reported per-call costs across the same span (``None``
    when no provider returned a cost).
    """

    tool: str
    version: str
    model: str | None = None
    tokens_planner_total: int | None = None
    tokens_scope_review_total: int | None = None
    tokens_planner_completion_total: int | None = None
    tokens_planner_method: str | None = None
    compile_wall_seconds: float | None = None
    compile_cost_usd: float | None = None


class AgentLock(_StrictModel):
    """Root document of ``agent_lock.json``."""

    version: Literal["1.0"] = "1.0"
    generated_at: datetime
    generator: Generator | None = None
    repo: Repo
    tasks: list[Task]
    execution_plan: ExecutionPlan
    conflicts_detected: list[Conflict] = Field(default_factory=list)

    @staticmethod
    def utcnow() -> datetime:
        """Return a timezone-aware UTC datetime suitable for ``generated_at``."""
        return datetime.now(UTC)
