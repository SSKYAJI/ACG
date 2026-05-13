"""Tests for ACG_LLM_EXTRA_PARAMS_JSON plumbing on :class:`acg.runtime.RuntimeConfig` / :class:`RuntimeLLM`."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest


def test_runtime_config_extra_params_from_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    from acg.runtime import RuntimeConfig

    monkeypatch.setenv(
        "ACG_LLM_EXTRA_PARAMS_JSON",
        json.dumps({"reasoning": {"effort": "none"}}),
    )
    cfg = RuntimeConfig.from_env()
    assert cfg.extra_params == {"reasoning": {"effort": "none"}}


def test_runtime_config_extra_params_malformed_json_falls_back_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from acg.runtime import RuntimeConfig

    monkeypatch.setenv("ACG_LLM_EXTRA_PARAMS_JSON", "{not json")
    cfg = RuntimeConfig.from_env()
    assert cfg.extra_params == {}


def test_runtime_llm_complete_merges_extra_params_into_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from acg.runtime import RuntimeLLM

    class FakeResponse:
        status_code = 200
        headers: dict[str, str] = {}
        text = "{}"

        def json(self) -> dict[str, Any]:
            return {
                "choices": [
                    {
                        "message": {"content": "ok", "reasoning_content": ""},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            }

    class FakeAsyncClient:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout
            self.calls: list[dict[str, Any]] = []

        async def post(
            self,
            endpoint: str,
            json: dict[str, Any],
            headers: dict[str, str],
        ) -> FakeResponse:
            self.calls.append({"json": json})
            return FakeResponse()

        async def aclose(self) -> None:
            return None

    fake_client = FakeAsyncClient(timeout=1.0)
    monkeypatch.setattr("acg.runtime.httpx.AsyncClient", lambda timeout: fake_client)
    llm = RuntimeLLM(
        base_url="http://example.invalid/v1",
        model="demo",
        api_key="key",
        extra_params={"reasoning": {"effort": "none", "exclude": True}},
    )
    asyncio.run(llm.complete([{"role": "user", "content": "hello"}]))
    sent = fake_client.calls[-1]["json"]
    assert sent.get("reasoning") == {"effort": "none", "exclude": True}
