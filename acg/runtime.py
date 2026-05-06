"""End-to-end async runtime for an ACG lockfile.

The runtime wires three things together:

1. A single **orchestrator** call (typically against a thinking-enabled
   ``llama-server`` on port 8081) that reasons about the lockfile's soundness
   and emits a JSON dispatch decision.
2. A **per-group sub-agent fan-out** (typically against a no-think
   ``llama-server`` on port 8080 with ``--parallel 4``). Each worker proposes
   a list of write paths as JSON. Workers are *not* told their
   ``allowed_paths`` — the validator below catches violations.
3. A **mid-flight enforcement** pass via :func:`acg.enforce.validate_write`.
   Every proposal lands in the run trace tagged ``allowed`` or with a
   non-empty ``reason``.

v1 is propose-and-validate only — no real file mutations. The structured run
trace is written to ``demo-app/.acg/run_trace.json`` and consumed by the
visualizer's live-replay mode.

Environment variables (read by :meth:`RuntimeConfig.from_env`):

============================  ==================================================
``ACG_ORCH_URL``              Orchestrator base URL (defaults to GX10:8081)
``ACG_ORCH_MODEL``            Orchestrator model id
``ACG_ORCH_API_KEY``          Orchestrator bearer token
``ACG_LLM_URL``               Sub-agent base URL (defaults to GX10:8080)
``ACG_LLM_MODEL``             Sub-agent model id
``ACG_LLM_API_KEY``           Sub-agent bearer token
``ACG_MOCK_LLM``              ``1`` ⇒ short-circuit to :class:`MockRuntimeLLM`
``ACG_PERF_TRACE``            Optional path for a GX10 perf trace JSON
============================  ==================================================
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx
from rich.console import Console
from rich.markup import escape as _rich_escape

from . import enforce
from .perf import PerfRecorder
from .repo_graph import scan_context_graph
from .schema import AgentLock, Group, Task

# ---------------------------------------------------------------------------
# Tunable constants. No magic numbers in module bodies.
# ---------------------------------------------------------------------------

DEFAULT_ORCH_URL = "http://gx10-f2c9:8081/v1"
DEFAULT_SUB_URL = "http://gx10-f2c9:8080/v1"
DEFAULT_MODEL = "gemma"
DEFAULT_TIMEOUT_S = 180.0

ORCH_MAX_TOKENS = 2048
SUB_MAX_TOKENS = 700
TEMPERATURE = 0.2

# Top N files (sorted by import-fan-in) embedded in worker prompts so workers
# propose grounded paths instead of inventing plausible ones.
WORKER_TOP_K_FILES = 30


# ---------------------------------------------------------------------------
# Configuration.
# ---------------------------------------------------------------------------


@dataclass
class RuntimeConfig:
    """Runtime configuration captured from environment variables.

    Two LLM endpoints are tracked because the live llama-server build does not
    honor per-request reasoning overrides; we run two server instances with
    different ``--reasoning-budget`` flags. See ``HANDOFF_NEXT.md`` for the
    full background.
    """

    orch_url: str = DEFAULT_ORCH_URL
    orch_model: str = DEFAULT_MODEL
    orch_api_key: str = ""
    sub_url: str = DEFAULT_SUB_URL
    sub_model: str = DEFAULT_MODEL
    sub_api_key: str = ""
    orch_max_tokens: int = ORCH_MAX_TOKENS
    sub_max_tokens: int = SUB_MAX_TOKENS
    request_timeout_s: float = DEFAULT_TIMEOUT_S
    perf_trace_path: Path | None = None
    engine: str = "unknown"
    dtype: str = "unknown"
    parallel: int = 0
    kv_cache_quant: str = "unknown"
    flash_attn: bool = False
    worker_concurrency: int = 0
    grace_overlap: bool = False
    model_sha: str = ""
    sequential: bool = False

    @classmethod
    def from_env(cls) -> RuntimeConfig:
        """Build a config from environment variables with sensible defaults."""
        return cls(
            orch_url=os.environ.get("ACG_ORCH_URL", DEFAULT_ORCH_URL),
            orch_model=os.environ.get("ACG_ORCH_MODEL", DEFAULT_MODEL),
            orch_api_key=os.environ.get("ACG_ORCH_API_KEY", ""),
            sub_url=os.environ.get("ACG_LLM_URL", DEFAULT_SUB_URL),
            sub_model=os.environ.get("ACG_LLM_MODEL", DEFAULT_MODEL),
            sub_api_key=os.environ.get("ACG_LLM_API_KEY", ""),
            perf_trace_path=_env_path("ACG_PERF_TRACE"),
            engine=os.environ.get("ACG_LLM_ENGINE", "unknown"),
            dtype=os.environ.get("ACG_LLM_DTYPE", "unknown"),
            parallel=_env_int("ACG_LLM_PARALLEL", 0),
            kv_cache_quant=os.environ.get("ACG_LLM_KV_QUANT", "unknown"),
            flash_attn=_env_bool("ACG_LLM_FLASH_ATTN", False),
            worker_concurrency=_env_int("ACG_WORKER_CONCURRENCY", 0),
            grace_overlap=_env_bool("ACG_GRACE_OVERLAP", False),
            model_sha=os.environ.get("ACG_LLM_MODEL_SHA", ""),
            sequential=_env_bool("ACG_SEQUENTIAL", False),
        )

    def public(self) -> dict[str, str]:
        """Return a secret-free dict suitable for embedding in the run trace."""
        return {
            "orch_url": self.orch_url,
            "orch_model": self.orch_model,
            "sub_url": self.sub_url,
            "sub_model": self.sub_model,
        }

    def perf_public(self) -> dict[str, Any]:
        """Return the config subset required by perf_trace.schema.json."""
        return {
            "engine": self.engine,
            "dtype": self.dtype,
            "parallel": self.parallel,
            "kv_cache_quant": self.kv_cache_quant,
            "flash_attn": self.flash_attn,
            "worker_concurrency": self.worker_concurrency,
            "grace_overlap": self.grace_overlap,
            "model_id": self.sub_model,
            "model_sha": self.model_sha,
        }


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return Path(raw)


# ---------------------------------------------------------------------------
# LLM client: async cousin of :class:`acg.llm.LLMClient`.
# ---------------------------------------------------------------------------


@dataclass
class LLMReply:
    """A single chat-completion result with timing + reasoning trace."""

    content: str
    reasoning: str
    completion_tokens: int
    finish_reason: str
    wall_s: float
    cost_usd: float | None = None
    cost_source: str | None = None


class RuntimeLLMProtocol(Protocol):
    """Duck-typed protocol implemented by both real and mock runtime clients."""

    model: str
    url: str

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = ...,
        temperature: float = ...,
    ) -> LLMReply: ...

    async def aclose(self) -> None: ...


class RuntimeLLM:
    """Async OpenAI-compatible client using a single shared :class:`httpx.AsyncClient`.

    Kept deliberately separate from the synchronous :class:`acg.llm.LLMClient`
    used by the predictor: that client is invoked at compile time over many
    short calls, while this one is invoked at runtime over fewer, longer calls
    (the orchestrator's thinking pass is ~30 s).
    """

    def __init__(
        self,
        base_url: str,
        model: str = DEFAULT_MODEL,
        api_key: str = "",
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self._client = httpx.AsyncClient(timeout=timeout)

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = SUB_MAX_TOKENS,
        temperature: float = TEMPERATURE,
    ) -> LLMReply:
        """POST to ``/chat/completions`` and return a populated :class:`LLMReply`.

        Retries once on transport error; raises :class:`RuntimeLLMError` on
        non-2xx responses or after the second transport failure.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        endpoint = f"{self.url}/chat/completions"

        last_exc: Exception | None = None
        start = time.perf_counter()
        for attempt in range(2):
            try:
                response = await self._client.post(endpoint, json=payload, headers=headers)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt == 0:
                    continue
                raise RuntimeLLMError(f"transport error contacting {endpoint}: {exc}") from exc

            if response.status_code >= 400:
                raise RuntimeLLMError(
                    f"{endpoint} returned {response.status_code}: {response.text[:500]}"
                )
            data = response.json()
            try:
                choice = data["choices"][0]
                msg = choice["message"]
                content = msg.get("content") or ""
                reasoning = msg.get("reasoning_content") or ""
                finish = choice.get("finish_reason") or ""
                usage = data.get("usage") or {}
                tokens = int(usage.get("completion_tokens") or 0)
                cost_usd, cost_source = _extract_cost_usd(data, response.headers)
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeLLMError(
                    f"unexpected response shape from {endpoint}: {data!r}"
                ) from exc
            return LLMReply(
                content=content,
                reasoning=reasoning,
                completion_tokens=tokens,
                finish_reason=finish,
                wall_s=time.perf_counter() - start,
                cost_usd=cost_usd,
                cost_source=cost_source,
            )
        raise RuntimeLLMError(f"unreachable runtime LLM retry loop, last_exc={last_exc}")

    async def aclose(self) -> None:
        """Close the underlying ``httpx.AsyncClient``."""
        await self._client.aclose()


class RuntimeLLMError(RuntimeError):
    """Raised when the runtime LLM endpoint misbehaves after retries."""


def _optional_cost_float(value: Any) -> float | None:
    """Parse provider cost values without inventing units."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip().lstrip("$"))
        except ValueError:
            return None
    return None


def _extract_cost_usd(
    data: dict[str, Any], headers: Any
) -> tuple[float | None, str | None]:
    """Best-effort OpenRouter/OpenAI-compatible cost extraction.

    Providers are inconsistent: OpenRouter-compatible proxies sometimes put
    spend in ``usage.cost``-style fields, while gateways may expose an
    ``x-openrouter-cost`` header. If no explicit cost is present, return
    ``(None, None)`` so reports can say "not recorded".
    """
    usage = data.get("usage") if isinstance(data, dict) else None
    candidates: list[tuple[str, Any]] = []
    if isinstance(usage, dict):
        for key in ("cost", "cost_usd", "total_cost", "total_cost_usd"):
            candidates.append((f"body.usage.{key}", usage.get(key)))
    for key in ("cost", "cost_usd", "total_cost", "total_cost_usd"):
        candidates.append((f"body.{key}", data.get(key)))
    for source, value in candidates:
        parsed = _optional_cost_float(value)
        if parsed is not None:
            return parsed, source

    for key in (
        "x-openrouter-cost",
        "x-openrouter-cost-usd",
        "openrouter-cost",
        "openrouter-cost-usd",
    ):
        parsed = _optional_cost_float(headers.get(key))
        if parsed is not None:
            return parsed, f"header.{key}"
    return None, None


# ---------------------------------------------------------------------------
# MockRuntimeLLM — deterministic offline stand-in.
# ---------------------------------------------------------------------------


_MOCK_ORCH_REASONING = (
    "Looking at the lockfile, four tasks are declared: oauth, billing, "
    "settings, tests. Two real conflicts exist on prisma/schema.prisma "
    "(oauth ↔ billing) and src/components/Sidebar.tsx (settings ↔ billing). "
    "The solver has already serialised billing after both oauth and settings, "
    "so group 1 (oauth, settings) can run in parallel safely. Group 2 (billing) "
    "consumes the merged Prisma schema. Group 3 (tests) runs last because "
    "Playwright e2e specs depend on the new routes existing. Plan looks sound."
)

_MOCK_ORCH_CONTENT = json.dumps(
    {
        "approved": True,
        "concerns": [
            "tests task lacks a concrete file path; worker may need extra hand-holding",
            "billing touches two hot files (Prisma + Sidebar); merge order matters",
        ],
        "dispatch_order": [1, 2, 3],
    },
    indent=2,
)


# Per-task canned proposals. Each task's set is intentionally crafted to mix
# in-bounds writes (ALLOWED) with one out-of-bounds write (BLOCKED) so that
# the run trace exercises both validator outcomes.
_MOCK_WORKER_PROPOSALS: dict[str, list[dict[str, str]]] = {
    "oauth": [
        {"file": "src/server/auth/config.ts", "description": "Add Google OAuth provider"},
        {"file": "prisma/schema.prisma", "description": "Add Account/Session/User auth tables"},
        {"file": "src/app/api/auth/[...nextauth]/route.ts", "description": "Wire NextAuth route handler"},
        {"file": "src/utils/random.ts", "description": "Helper for state token generation"},
    ],
    "billing": [
        {"file": "src/components/Sidebar.tsx", "description": "Add Billing nav entry"},
        {"file": "prisma/schema.prisma", "description": "Add Subscription model"},
        {"file": "src/app/dashboard/billing/page.tsx", "description": "Billing dashboard route"},
    ],
    "settings": [
        {"file": "src/app/settings/page.tsx", "description": "Redesign settings page"},
        {"file": "src/components/Sidebar.tsx", "description": "Update Settings entry styling"},
    ],
    "tests": [
        {"file": "tests/e2e/checkout.spec.ts", "description": "Playwright spec for checkout flow"},
        {"file": "tests/e2e/auth.spec.ts", "description": "Playwright spec for OAuth login"},
    ],
}


class MockRuntimeLLM:
    """Deterministic stand-in mirroring :class:`acg.llm.MockLLMClient`.

    The mock pattern-matches the user prompt for known task ids (workers) or a
    well-known "Lockfile summary" header (orchestrator) and returns canned
    JSON. Used by tests, ``--mock`` mode in the CLI, and to seed the committed
    fixture trace in ``demo-app/.acg/run_trace.json``.
    """

    def __init__(self, role: str = "worker", model: str = "mock-runtime") -> None:
        self.role = role
        self.model = model
        self.url = f"mock://{role}"

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        max_tokens: int = SUB_MAX_TOKENS,
        temperature: float = TEMPERATURE,
    ) -> LLMReply:
        del max_tokens, temperature  # unused
        # Cheap async marker so the mock behaves like a real awaitable.
        await asyncio.sleep(0)
        user_blob = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")

        if self.role == "orchestrator" or "Lockfile summary" in user_blob:
            return LLMReply(
                content=_MOCK_ORCH_CONTENT,
                reasoning=_MOCK_ORCH_REASONING,
                completion_tokens=128,
                finish_reason="stop",
                wall_s=0.01,
            )

        for task_id, proposals in _MOCK_WORKER_PROPOSALS.items():
            if f"Task id: {task_id}" in user_blob:
                return LLMReply(
                    content=json.dumps({"writes": proposals}),
                    reasoning="",
                    completion_tokens=64,
                    finish_reason="stop",
                    wall_s=0.005,
                )

        return LLMReply(
            content=json.dumps({"writes": []}),
            reasoning="",
            completion_tokens=8,
            finish_reason="stop",
            wall_s=0.001,
        )

    async def aclose(self) -> None:
        return None


# ---------------------------------------------------------------------------
# Result dataclasses (asdict-friendly so the run trace is one json.dumps away).
# ---------------------------------------------------------------------------


@dataclass
class Proposal:
    """A worker's proposed write, post-validation."""

    file: str
    description: str
    allowed: bool
    reason: str | None


@dataclass
class WorkerResult:
    """One worker's contribution to the run trace."""

    task_id: str
    group_id: int
    url: str
    model: str
    wall_s: float
    completion_tokens: int
    finish_reason: str
    raw_content: str
    proposals: list[Proposal]
    allowed_count: int
    blocked_count: int
    error: str | None = None
    cost_usd: float | None = None
    cost_source: str | None = None


@dataclass
class GroupResult:
    """One execution-group entry in the run trace."""

    id: int
    type: str
    started_at: str
    wall_s: float
    worker_ids: list[str]


@dataclass
class OrchestratorResult:
    """Captured orchestrator output + parsed dispatch decision."""

    url: str
    model: str
    wall_s: float
    completion_tokens: int
    finish_reason: str
    content: str
    reasoning_content: str
    parsed: dict[str, Any] | None


@dataclass
class RunResult:
    """Top-level run trace; serialised to ``demo-app/.acg/run_trace.json``."""

    version: str
    generated_at: str
    lockfile: str
    config: dict[str, str]
    orchestrator: OrchestratorResult
    workers: list[WorkerResult]
    groups_executed: list[GroupResult]
    started_at: str
    finished_at: str
    total_wall_s: float


# ---------------------------------------------------------------------------
# Forgiving JSON parser (mirrors acg.predictor._parse_llm_writes).
# ---------------------------------------------------------------------------


def _parse_writes(raw: str) -> list[dict[str, str]]:
    """Best-effort parse of an LLM reply into a list of ``{file, description}``.

    Tolerates ``json``-fenced blocks, leading/trailing prose, and replies that
    are bare arrays instead of ``{"writes": [...]}`` objects. Returns ``[]`` if
    no recognisable structure is found.
    """
    if not raw or not raw.strip():
        return []
    text = raw.strip()
    # Strip ```json ... ``` fences.
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    payload: Any = None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # Try to find the first balanced JSON object/array substring.
        for opener, closer in (("{", "}"), ("[", "]")):
            start = text.find(opener)
            end = text.rfind(closer)
            if start == -1 or end == -1 or end <= start:
                continue
            try:
                payload = json.loads(text[start : end + 1])
                break
            except json.JSONDecodeError:
                continue
    if payload is None:
        return []

    items: list[Any]
    if isinstance(payload, dict):
        candidate = payload.get("writes") or payload.get("proposals") or []
        items = candidate if isinstance(candidate, list) else []
    elif isinstance(payload, list):
        items = payload
    else:
        items = []

    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # Accept ``file`` or ``path`` (some workers paraphrase the field name).
        file_path = item.get("file") or item.get("path")
        if not isinstance(file_path, str) or not file_path.strip():
            continue
        description = item.get("description") or item.get("reason") or ""
        if not isinstance(description, str):
            description = str(description)
        out.append({"file": file_path.strip(), "description": description.strip()})
    return out


def _parse_orchestrator_dispatch(raw: str) -> dict[str, Any] | None:
    """Parse the orchestrator's reply into ``{approved, concerns, dispatch_order}``.

    Returns ``None`` when no JSON object can be recovered. The runtime never
    blocks on a parse failure here — the lockfile is the source of truth, the
    orchestrator's dispatch decision is narrative-only.
    """
    text = (raw or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None


# ---------------------------------------------------------------------------
# Prompt builders.
# ---------------------------------------------------------------------------


def _build_orchestrator_prompt(lock: AgentLock) -> list[dict[str, str]]:
    """Construct the orchestrator messages from a compact lockfile summary."""
    summary = {
        "tasks": [
            {
                "id": t.id,
                "prompt": t.prompt,
                "predicted_writes": [pw.path for pw in t.predicted_writes],
                "depends_on": list(t.depends_on),
            }
            for t in lock.tasks
        ],
        "conflicts": [
            {
                "files": list(c.files),
                "between_tasks": list(c.between_tasks),
                "resolution": c.resolution,
            }
            for c in lock.conflicts_detected
        ],
        "execution_plan": [
            {
                "id": g.id,
                "type": g.type,
                "tasks": list(g.tasks),
                "waits_for": list(g.waits_for),
            }
            for g in sorted(lock.execution_plan.groups, key=lambda g: g.id)
        ],
    }
    system = (
        "You are an orchestrator analyzing a multi-agent execution plan for "
        "coding tasks. Reason carefully about whether the plan respects all "
        "write conflicts. Output ONLY a JSON object with keys:\n"
        "  - \"approved\" (boolean)\n"
        "  - \"concerns\" (list of short strings)\n"
        "  - \"dispatch_order\" (list of group ids in execution order)\n"
        "Do not include any prose outside the JSON object."
    )
    user = (
        "Lockfile summary:\n"
        f"{json.dumps(summary, sort_keys=True, indent=2)}\n\n"
        "Reason about the plan, then emit the JSON dispatch decision."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _top_files(repo_graph: dict[str, Any], k: int = WORKER_TOP_K_FILES) -> list[str]:
    """Return the top-K most-imported repo files (descending fan-in)."""
    files = repo_graph.get("files") or []
    if not files:
        return []
    scored = sorted(
        files,
        key=lambda f: (-(f.get("imported_by_count") or 0), f.get("path", "")),
    )
    return [f.get("path") for f in scored[:k] if f.get("path")]


def _is_directoryish(path: str) -> bool:
    """Return True when a predicted-write path is a bare directory.

    The lockfile's ``tests`` task has ``predicted_writes = [{path: "tests"}]``;
    workers given that directly tend to invent random files outside the
    allowed glob. We append a hint instead.
    """
    if not path:
        return False
    if path.endswith("/"):
        return True
    last = path.rsplit("/", 1)[-1]
    return "." not in last


def _build_worker_prompt(
    task: Task, repo_graph: dict[str, Any]
) -> list[dict[str, str]]:
    """Construct the worker messages.

    Workers are intentionally NOT told their ``allowed_paths`` — see
    ``HANDOFF_NEXT.md`` ("Do NOT tell the worker its allowed_paths"). This
    keeps the validator honest and produces occasional BLOCKED moments.
    """
    files = _top_files(repo_graph)
    file_block = "\n".join(f"  - {p}" for p in files) or "  (graph empty)"

    extra_hint = ""
    for pw in task.predicted_writes:
        if _is_directoryish(pw.path):
            extra_hint = (
                f"\nNote: the lockfile predicts writes under '{pw.path}'. "
                "Propose specific file paths under that directory."
            )
            break

    system = (
        "You are a coding agent assigned a single task. Output ONLY a JSON "
        'object with key "writes": an array of objects with keys "file" '
        "(repository-relative path) and \"description\" (one short sentence). "
        "Do not include prose, code fences, or any other text."
    )
    user = (
        f"Task id: {task.id}\n"
        f"Task: {task.prompt}\n"
        f"Available files in this repo (top {len(files)} by importance):\n"
        f"{file_block}{extra_hint}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


# ---------------------------------------------------------------------------
# Async execution primitives.
# ---------------------------------------------------------------------------


_console = Console()
_console_err = Console(stderr=True, no_color=False)


def _now_iso() -> str:
    """Return a UTC ISO-8601 timestamp."""
    return datetime.now(UTC).isoformat()


def _task_input_tokens(task: Task, repo_graph: dict[str, Any]) -> int:
    prompt = _build_worker_prompt(task, repo_graph)
    return sum(len(message.get("content", "")) // 4 for message in prompt)


async def run_orchestrator(
    lock: AgentLock,
    llm: RuntimeLLMProtocol,
    *,
    max_tokens: int = ORCH_MAX_TOKENS,
) -> OrchestratorResult:
    """Run the single thinking-pass orchestrator call."""
    _console.print("[bold cyan][orchestrator][/] thinking…")
    messages = _build_orchestrator_prompt(lock)
    reply = await llm.complete(messages, max_tokens=max_tokens)
    parsed = _parse_orchestrator_dispatch(reply.content)
    _console.print(
        f"[bold cyan][orchestrator][/] done in {reply.wall_s:.2f}s "
        f"({reply.completion_tokens} tokens, finish={reply.finish_reason})"
    )
    return OrchestratorResult(
        url=llm.url,
        model=llm.model,
        wall_s=reply.wall_s,
        completion_tokens=reply.completion_tokens,
        finish_reason=reply.finish_reason,
        content=reply.content,
        reasoning_content=reply.reasoning,
        parsed=parsed,
    )


async def run_worker(
    task: Task,
    lock: AgentLock,
    repo_graph: dict[str, Any],
    llm: RuntimeLLMProtocol,
    group_id: int,
    *,
    max_tokens: int = SUB_MAX_TOKENS,
    config: RuntimeConfig | None = None,
    perf: PerfRecorder | None = None,
) -> WorkerResult:
    """Run a single sub-agent and validate every proposed write."""
    cfg = config or RuntimeConfig.from_env()
    _console.print(f"[blue][worker {task.id}][/] starting (group {group_id})")
    messages = _build_worker_prompt(task, repo_graph)
    error: str | None = None
    if perf:
        perf.mark_task_start(task.id, group_id)
    try:
        reply = await llm.complete(messages, max_tokens=max_tokens)
        if perf:
            perf.mark_first_token(task.id)
    except RuntimeLLMError as exc:
        # Failing closed: a worker that errored out contributes zero proposals.
        error = str(exc)
        _console.print(f"[red][worker {task.id}][/] LLM error: {exc}")
        if perf:
            perf.mark_task_end(
                task.id, input_tokens=_task_input_tokens(task, repo_graph), output_tokens=0
            )
        return WorkerResult(
            task_id=task.id,
            group_id=group_id,
            url=llm.url,
            model=llm.model,
            wall_s=0.0,
            completion_tokens=0,
            finish_reason="error",
            raw_content="",
            proposals=[],
            allowed_count=0,
            blocked_count=0,
            error=error,
        )

    raw_proposals = _parse_writes(reply.content)
    if perf:
        perf.mark_task_end(
            task.id,
            input_tokens=_task_input_tokens(task, repo_graph),
            output_tokens=reply.completion_tokens,
        )
    _console.print(
        f"[blue][worker {task.id}][/] proposed {len(raw_proposals)} writes "
        f"({reply.wall_s:.2f}s, {reply.completion_tokens} tokens)"
    )

    proposals: list[Proposal] = []
    allowed_count = 0
    blocked_count = 0
    if cfg.grace_overlap and raw_proposals:
        validation_results = await asyncio.gather(
            *(
                asyncio.to_thread(enforce.validate_write, lock, task.id, raw["file"])
                for raw in raw_proposals
            )
        )
    else:
        validation_results = [
            enforce.validate_write(lock, task.id, raw["file"]) for raw in raw_proposals
        ]
    for raw, (allowed, reason) in zip(raw_proposals, validation_results, strict=True):
        proposals.append(
            Proposal(
                file=raw["file"],
                description=raw.get("description", ""),
                allowed=allowed,
                reason=reason,
            )
        )
        safe_file = _rich_escape(raw["file"])
        if allowed:
            allowed_count += 1
            _console.print(
                f"  [green][validator][/] ALLOWED {task.id} → {safe_file}"
            )
        else:
            blocked_count += 1
            safe_reason = _rich_escape(reason or "outside allowed_paths")
            _console.print(
                f"  [red][validator][/] BLOCKED {task.id} → {safe_file}: {safe_reason}"
            )

    return WorkerResult(
        task_id=task.id,
        group_id=group_id,
        url=llm.url,
        model=llm.model,
        wall_s=reply.wall_s,
        completion_tokens=reply.completion_tokens,
        finish_reason=reply.finish_reason,
        raw_content=reply.content,
        proposals=proposals,
        allowed_count=allowed_count,
        blocked_count=blocked_count,
        error=error,
        cost_usd=reply.cost_usd,
        cost_source=reply.cost_source,
    )


async def run_group(
    group: Group,
    lock: AgentLock,
    repo_graph: dict[str, Any],
    sub_llm: RuntimeLLMProtocol,
    *,
    config: RuntimeConfig | None = None,
    perf: PerfRecorder | None = None,
) -> tuple[GroupResult, list[WorkerResult]]:
    """Run all workers in a group respecting the configured concurrency lane.

    Three modes are supported, controlled by ``config``:

    * ``sequential=True`` — workers run strictly one after another via
      ``await``. This is the *baseline* lane used to characterize end-to-end
      latency without any parallel speed-up.
    * ``worker_concurrency > 0`` — workers run inside ``asyncio.gather`` but
      are gated by an :class:`asyncio.Semaphore` so at most N execute
      concurrently. This is the *optimized* lane.
    * Otherwise (``worker_concurrency == 0``) — preserve the historical
      unbounded ``asyncio.gather`` behavior.
    """
    cfg = config or RuntimeConfig.from_env()
    _console.print(
        f"[bold magenta][group {group.id}][/] starting "
        f"({group.type}: {', '.join(group.tasks)})"
    )
    started_at = _now_iso()
    t0 = time.perf_counter()

    tasks_by_id = {t.id: t for t in lock.tasks}
    eligible = [tid for tid in group.tasks if tid in tasks_by_id]

    def _run_worker_coro(task_id: str) -> Any:
        return run_worker(
            tasks_by_id[task_id],
            lock,
            repo_graph,
            sub_llm,
            group.id,
            config=cfg,
            perf=perf,
        )

    workers: list[WorkerResult]
    if cfg.sequential:
        workers = []
        for task_id in eligible:
            workers.append(await _run_worker_coro(task_id))
    elif cfg.worker_concurrency > 0:
        semaphore = asyncio.Semaphore(cfg.worker_concurrency)

        async def _bounded(task_id: str) -> WorkerResult:
            async with semaphore:
                return await _run_worker_coro(task_id)

        workers = list(await asyncio.gather(*(_bounded(tid) for tid in eligible)))
    else:
        workers = list(
            await asyncio.gather(*(_run_worker_coro(tid) for tid in eligible))
        )

    wall_s = time.perf_counter() - t0
    _console.print(
        f"[bold magenta][group {group.id}][/] done in {wall_s:.2f}s"
    )
    return (
        GroupResult(
            id=group.id,
            type=group.type,
            started_at=started_at,
            wall_s=wall_s,
            worker_ids=list(group.tasks),
        ),
        list(workers),
    )


async def run_lockfile(
    lock: AgentLock,
    repo_graph: dict[str, Any],
    orch: RuntimeLLMProtocol,
    sub: RuntimeLLMProtocol,
    *,
    lockfile_path: str = "demo-app/agent_lock.json",
    repo_root: str | Path = "demo-app",
    language: str = "ts",
    config: RuntimeConfig | None = None,
    perf: PerfRecorder | None = None,
) -> RunResult:
    """Top-level entrypoint: orchestrator pass then sequential group execution."""
    cfg = config or RuntimeConfig.from_env()

    # -- engine receipts banner ------------------------------------------
    _p = cfg.perf_public()
    _task_count = len(lock.tasks)
    if cfg.sequential:
        _mode = "sequential"
    elif cfg.worker_concurrency == 1:
        _mode = "sequential"
    elif cfg.worker_concurrency > 1:
        _mode = f"concurrent x{cfg.worker_concurrency}"
    else:
        _mode = "concurrent unbounded"
    _console_err.print(
        f"[acg] engine={_p['engine']} dtype={_p['dtype']} "
        f"parallel={_p['parallel']} kv-quant={_p['kv_cache_quant']} "
        f"flash-attn={_p['flash_attn']}",
        markup=False,
        highlight=False,
    )
    _console_err.print(
        f"[acg] backend {_p['model_id']} ctx={cfg.sub_max_tokens} "
        f"worker-concurrency={_p['worker_concurrency']} "
        f"grace-overlap={_p['grace_overlap']}",
        markup=False,
        highlight=False,
    )
    _console_err.print(
        f"[acg] starting {_task_count} tasks ({_mode})",
        markup=False,
        highlight=False,
    )
    # --------------------------------------------------------------------

    started_at = _now_iso()
    t0 = time.perf_counter()
    if perf:
        perf.start()

    orch_result = await run_orchestrator(lock, orch)

    workers: list[WorkerResult] = []
    groups_executed: list[GroupResult] = []
    groups = sorted(lock.execution_plan.groups, key=lambda g: g.id)
    try:
        for idx, group in enumerate(groups):
            group_result, group_workers = await run_group(
                group, lock, repo_graph, sub, config=cfg, perf=perf
            )
            groups_executed.append(group_result)
            workers.extend(group_workers)
            if cfg.grace_overlap and idx < len(groups) - 1:

                async def _rescan() -> None:
                    try:
                        await asyncio.to_thread(
                            scan_context_graph, Path(repo_root), language=language
                        )
                    except Exception as exc:
                        _console.print(f"[yellow]grace-overlap rescan failed: {exc}[/]")

                asyncio.create_task(_rescan())
    finally:
        if perf:
            perf.stop()

    finished_at = _now_iso()
    total = time.perf_counter() - t0
    cfg_public = cfg.public()
    # Override URL/model with the actual values the LLMs reported (covers mock case).
    cfg_public.update(
        orch_url=orch.url,
        orch_model=orch.model,
        sub_url=sub.url,
        sub_model=sub.model,
    )

    _console.print(
        f"[bold green][run][/] complete: {len(workers)} workers, "
        f"{sum(w.allowed_count for w in workers)} allowed, "
        f"{sum(w.blocked_count for w in workers)} blocked, "
        f"{total:.2f}s wall"
    )

    if perf and cfg.perf_trace_path:
        perf.dump(cfg.perf_trace_path)

    return RunResult(
        version="1.0",
        generated_at=_now_iso(),
        lockfile=lockfile_path,
        config=cfg_public,
        orchestrator=orch_result,
        workers=workers,
        groups_executed=groups_executed,
        started_at=started_at,
        finished_at=finished_at,
        total_wall_s=total,
    )


__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_ORCH_URL",
    "DEFAULT_SUB_URL",
    "GroupResult",
    "LLMReply",
    "MockRuntimeLLM",
    "OrchestratorResult",
    "Proposal",
    "RunResult",
    "RuntimeConfig",
    "RuntimeLLM",
    "RuntimeLLMError",
    "RuntimeLLMProtocol",
    "WorkerResult",
    "run_group",
    "run_lockfile",
    "run_orchestrator",
    "run_worker",
]
