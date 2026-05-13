"""Provider-agnostic LLM client over an OpenAI-compatible HTTP API.

The client reads configuration from environment variables so the same code path
works with Groq (dev), vLLM (local), and OpenAI (compat smoke):

==========================  =======================================================
Environment variable        Purpose
==========================  =======================================================
``ACG_LLM_URL``             Base URL, e.g. ``https://api.groq.com/openai/v1``
``ACG_LLM_MODEL``           Model id, e.g. ``llama-3.3-70b-versatile``
``ACG_LLM_API_KEY``         Bearer token; ``GROQ_API_KEY`` is used as a fallback
``ACG_MOCK_LLM``            If set to ``1`` a deterministic offline client is used
``ACG_LLM_EXTRA_PARAMS_JSON``  Optional JSON object merged into compile-time
                            ``/chat/completions`` requests (provider-specific keys).
``ACG_COMPILE_TASK_CONCURRENCY``  Max parallel :func:`acg.compiler.compile_lockfile`
                            predictor tasks (default ``1`` = serial). When ``>1``,
                            each worker uses its own :class:`LLMClient` (``from_env``);
                            keep low to avoid OpenRouter 429s.
==========================  =======================================================
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Protocol

import httpx

DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_MODEL = "llama-3.3-70b-versatile"
DEFAULT_TIMEOUT = 120.0

_log = logging.getLogger(__name__)


def parse_acg_llm_extra_params_json() -> dict[str, Any]:
    """Parse ``ACG_LLM_EXTRA_PARAMS_JSON`` and merge into chat-completions payloads."""
    raw = os.environ.get("ACG_LLM_EXTRA_PARAMS_JSON")
    if raw is None or not str(raw).strip():
        return {}
    try:
        parsed: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        _log.warning("ACG_LLM_EXTRA_PARAMS_JSON is not valid JSON: %s", exc)
        return {}
    if isinstance(parsed, dict):
        return parsed
    _log.warning(
        "ACG_LLM_EXTRA_PARAMS_JSON must be a JSON object, got %s",
        type(parsed).__name__,
    )
    return {}


class LLMError(RuntimeError):
    """Raised when an LLM endpoint returns a non-2xx response or transport fails."""


@dataclass
class LLMUsage:
    """Running totals of provider-reported usage across one or more ``complete()`` calls.

    Populated by :class:`LLMClient` after each successful chat-completion. The
    compiler reads ``usage_total`` from its client at the end of the predict
    loop to stamp honest token / cost / wall-time numbers into
    :class:`acg.schema.Generator`. ``MockLLMClient`` keeps a zero-valued
    instance so callers don't need to special-case the offline path.

    All fields are additive — caller is expected to snapshot before/after a
    bounded operation (e.g. compile) to get a per-operation delta.
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    wall_seconds: float = 0.0
    calls: int = 0
    # ``provider`` when at least one call returned a usage block; ``estimate``
    # when we had to fall back to chars//4. The compiler surfaces this so the
    # paper can disclose which source generated the headline number.
    source: str = "none"
    _lock: Lock = field(default_factory=Lock, repr=False, compare=False)

    def add(
        self,
        *,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        cost_usd: float | None,
        wall_seconds: float,
        source: str,
    ) -> None:
        """Increment the running totals from one ``complete()`` call."""
        with self._lock:
            self.prompt_tokens += int(prompt_tokens or 0)
            self.completion_tokens += int(completion_tokens or 0)
            self.cost_usd += float(cost_usd or 0.0)
            self.wall_seconds += float(wall_seconds)
            self.calls += 1
            # ``provider`` wins over ``estimate``; either wins over ``none``.
            if source == "provider":
                self.source = "provider"
            elif source == "estimate" and self.source != "provider":
                self.source = "estimate"

    def snapshot(self) -> LLMUsage:
        """Return a copy of the running totals (without the lock)."""
        with self._lock:
            return LLMUsage(
                prompt_tokens=self.prompt_tokens,
                completion_tokens=self.completion_tokens,
                cost_usd=self.cost_usd,
                wall_seconds=self.wall_seconds,
                calls=self.calls,
                source=self.source,
            )


class LLMProtocol(Protocol):
    """Duck-typed protocol both real and mock clients implement."""

    model: str
    usage_total: LLMUsage

    def complete(
        self, messages: list[dict[str, str]], response_format: dict[str, Any] | None = ...
    ) -> str: ...


class LLMClient:
    """Thin OpenAI-compatible client backed by ``httpx``.

    The client is intentionally minimal: it exposes a single
    :meth:`complete` method that returns the model's textual reply. Higher-level
    parsing (JSON extraction, schema validation) lives in callers.
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        api_key: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        extra_params: dict[str, Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self._client = httpx.Client(timeout=timeout)
        self.usage_total: LLMUsage = LLMUsage()
        self.extra_params: dict[str, Any] = dict(extra_params or {})

    @classmethod
    def from_env(cls) -> LLMProtocol:
        """Build a client from environment variables.

        Returns a :class:`MockLLMClient` when ``ACG_MOCK_LLM=1`` or no API key is
        available, so offline development and CI pass without secrets.
        """
        if os.environ.get("ACG_MOCK_LLM") == "1":
            return MockLLMClient()
        api_key = os.environ.get("ACG_LLM_API_KEY") or os.environ.get("GROQ_API_KEY")
        if not api_key:
            return MockLLMClient()
        base_url = os.environ.get("ACG_LLM_URL", DEFAULT_BASE_URL)
        model = os.environ.get("ACG_LLM_MODEL", DEFAULT_MODEL)
        extra = parse_acg_llm_extra_params_json()
        return cls(base_url=base_url, model=model, api_key=api_key, extra_params=extra)

    @classmethod
    def from_env_for_compile(cls) -> LLMProtocol:
        """Build a compile-time client, optionally overriding model/URL/key.

        Compile-time LLM calls (seed expansion, rerank, scope review) only
        need structured JSON output — they do not need frontier thinking
        models. To decouple compile spend from runtime model choice, the
        compiler prefers ``ACG_COMPILE_MODEL`` / ``ACG_COMPILE_URL`` /
        ``ACG_COMPILE_API_KEY`` when set, falling back to the ``ACG_LLM_*``
        values otherwise. Recommended: a fast cheap MoE coder model like
        ``qwen/qwen3-coder-30b-a3b-instruct`` (10x cheaper, 5x faster than
        Kimi K2.6 with comparable structured-output quality).
        """
        if os.environ.get("ACG_MOCK_LLM") == "1":
            return MockLLMClient()
        api_key = (
            os.environ.get("ACG_COMPILE_API_KEY")
            or os.environ.get("ACG_LLM_API_KEY")
            or os.environ.get("GROQ_API_KEY")
        )
        if not api_key:
            return MockLLMClient()
        base_url = (
            os.environ.get("ACG_COMPILE_URL")
            or os.environ.get("ACG_LLM_URL", DEFAULT_BASE_URL)
        )
        model = (
            os.environ.get("ACG_COMPILE_MODEL")
            or os.environ.get("ACG_LLM_MODEL", DEFAULT_MODEL)
        )
        extra = parse_acg_llm_extra_params_json()
        return cls(base_url=base_url, model=model, api_key=api_key, extra_params=extra)

    def complete(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None = None,
    ) -> str:
        """POST to ``/chat/completions`` and return the assistant message.

        Args:
            messages: OpenAI-style ``[{"role": ..., "content": ...}]`` list.
            response_format: Optional ``response_format`` payload (e.g. JSON
                mode). Passed through; some providers ignore unknown keys.

        Returns:
            Assistant message string.

        Raises:
            LLMError: on non-2xx HTTP responses or transport failures after retry.
        """
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if response_format is not None:
            payload["response_format"] = response_format
        payload.update(self.extra_params)
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        url = f"{self.base_url}/chat/completions"

        last_exc: Exception | None = None
        start = time.perf_counter()
        for attempt in range(2):
            try:
                response = self._client.post(url, json=payload, headers=headers)
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt == 0:
                    continue
                raise LLMError(f"transport error contacting {url}: {exc}") from exc
            if response.status_code >= 400:
                raise LLMError(f"{url} returned {response.status_code}: {response.text[:500]}")
            data = response.json()
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LLMError(f"unexpected response shape from {url}: {data!r}") from exc
            self._record_usage(data, response.headers, content, messages, start)
            return content
        # Defensive: loop exits via return or raise above; this is unreachable.
        raise LLMError(f"unreachable LLM retry loop, last_exc={last_exc}")

    def _record_usage(
        self,
        data: dict[str, Any],
        headers: httpx.Headers,
        content: str,
        messages: list[dict[str, str]],
        start: float,
    ) -> None:
        """Aggregate provider-reported tokens + cost into ``self.usage_total``.

        Falls back to a chars//4 estimate when the provider omits a ``usage``
        block (rare for OpenAI/OpenRouter, common for self-hosted vLLM). The
        ``source`` field on :class:`LLMUsage` records which path was used so
        downstream paper numbers can disclose it.
        """
        usage = data.get("usage") if isinstance(data, dict) else None
        prompt_tokens: int | None = None
        completion_tokens: int | None = None
        cost_usd: float | None = None
        source = "estimate"
        if isinstance(usage, dict):
            prompt_tokens = _coerce_token_int(
                usage.get("prompt_tokens") or usage.get("input_tokens")
            )
            completion_tokens = _coerce_token_int(
                usage.get("completion_tokens") or usage.get("output_tokens")
            )
            for key in ("cost", "cost_usd", "total_cost", "total_cost_usd"):
                if usage.get(key) is not None:
                    cost_usd = _coerce_cost_float(usage[key])
                    if cost_usd is not None:
                        break
            if prompt_tokens is not None or completion_tokens is not None:
                source = "provider"
        if cost_usd is None:
            for header_name in (
                "x-openrouter-cost",
                "x-openrouter-cost-usd",
                "openrouter-cost",
                "openrouter-cost-usd",
            ):
                header_value = headers.get(header_name)
                if header_value is not None:
                    cost_usd = _coerce_cost_float(header_value)
                    if cost_usd is not None:
                        break
        if prompt_tokens is None:
            prompt_blob = "".join(m.get("content", "") for m in messages)
            prompt_tokens = max(1, len(prompt_blob) // 4)
        if completion_tokens is None:
            completion_tokens = max(0, len(content) // 4)
        self.usage_total.add(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            wall_seconds=time.perf_counter() - start,
            source=source,
        )


def _coerce_token_int(value: Any) -> int | None:
    """Best-effort cast of a provider-reported token count to an ``int``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer():
        return int(value) if value >= 0 else None
    if isinstance(value, str) and value.strip():
        try:
            return max(0, int(float(value.strip())))
        except ValueError:
            return None
    return None


def _coerce_cost_float(value: Any) -> float | None:
    """Parse a provider cost value into a USD float; strip a leading ``$``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.strip().lstrip("$"))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# MockLLMClient — deterministic offline stand-in.
# ---------------------------------------------------------------------------

# Canned write-set predictions keyed by task id. Covers the demo task set so the
# Tier 2 acceptance gate passes without API credentials. Tasks not in this map
# fall through to an empty re-rank, leaving seed-only predictions intact.
_CANNED_PREDICTIONS: dict[str, list[dict[str, Any]]] = {
    "oauth": [
        {
            "path": "src/server/auth/config.ts",
            "confidence": 0.95,
            "reason": "NextAuth options home in the T3 layout",
        },
        {
            "path": "prisma/schema.prisma",
            "confidence": 0.9,
            "reason": "NextAuth + Prisma adapter requires schema additions",
        },
        {
            "path": "src/app/api/auth/[...nextauth]/route.ts",
            "confidence": 0.85,
            "reason": "NextAuth route handler for OAuth callbacks",
        },
    ],
    "billing": [
        {
            "path": "src/app/dashboard/billing/page.tsx",
            "confidence": 0.95,
            "reason": "Billing dashboard route",
        },
        {
            "path": "src/server/stripe.ts",
            "confidence": 0.85,
            "reason": "Stripe client module",
        },
        {
            "path": "prisma/schema.prisma",
            "confidence": 0.9,
            "reason": "Subscription model fields",
        },
        {
            "path": "src/components/Sidebar.tsx",
            "confidence": 0.85,
            "reason": "New sidebar entry for billing",
        },
    ],
    "settings": [
        {
            "path": "src/app/settings/page.tsx",
            "confidence": 0.95,
            "reason": "Settings page route",
        },
        {
            "path": "src/components/Sidebar.tsx",
            "confidence": 0.85,
            "reason": "Sidebar entry styling",
        },
    ],
    "tests": [
        {
            "path": "tests/e2e/checkout.spec.ts",
            "confidence": 0.95,
            "reason": "Playwright spec for checkout",
        },
    ],
}


class MockLLMClient:
    """Deterministic stand-in returning canned write-set predictions.

    The mock pattern-matches the user prompt for known task ids and returns the
    corresponding predicted writes. Used in tests, the Tier 2 gate, and any
    environment without an LLM API key.
    """

    model = "mock-llm-canned"

    def __init__(self) -> None:
        self.usage_total: LLMUsage = LLMUsage()

    def complete(
        self,
        messages: list[dict[str, str]],
        response_format: dict[str, Any] | None = None,
    ) -> str:
        del response_format
        user_blob = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
        system_blob = "\n".join(m.get("content", "") for m in messages if m.get("role") == "system")
        if "Decompose one high-level repository goal" in system_blob:
            return json.dumps(
                {
                    "tasks": [
                        {
                            "id": "implement-core-change",
                            "prompt": "Implement the core code changes requested by the high-level goal.",
                            "hints": {"touches": ["src", "app", "core"]},
                            "depends_on": [],
                        },
                        {
                            "id": "add-tests",
                            "prompt": "Add focused tests covering the implemented behavior.",
                            "hints": {"touches": ["tests"]},
                            "depends_on": ["implement-core-change"],
                        },
                    ]
                }
            )
        for task_id, writes in _CANNED_PREDICTIONS.items():
            # The predictor prompt embeds ``Task id: <id>`` so we match on that.
            if f"Task id: {task_id}" in user_blob:
                return json.dumps({"writes": writes})
        return json.dumps({"writes": []})
