"""Tests for :class:`acg.llm.LLMClient` provider-usage tracking.

The compile-cost paper accounting depends on the client recording real
provider-reported usage (OpenAI / OpenRouter ``usage`` block) into
``self.usage_total`` after every successful ``complete()``. These tests
stub the underlying ``httpx.Client`` so we don't hit the network.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from acg.llm import LLMClient, LLMUsage, MockLLMClient


def _make_response(payload: dict[str, Any], headers: dict[str, str] | None = None) -> MagicMock:
    """Build a stub ``httpx.Response`` that ``LLMClient.complete`` can consume."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.text = ""
    resp.headers = headers or {}
    return resp


def test_usage_total_records_provider_block_with_cost() -> None:
    """OpenRouter-style response with ``usage`` block sets source=provider + cost."""
    client = LLMClient(api_key="x")
    client._client = MagicMock()
    client._client.post.return_value = _make_response(
        {
            "choices": [{"message": {"content": '{"writes": []}'}}],
            "usage": {
                "prompt_tokens": 552,
                "completion_tokens": 6859,
                "cost": 0.028,
            },
        }
    )

    out = client.complete([{"role": "user", "content": "hi"}])
    assert out == '{"writes": []}'
    assert client.usage_total.source == "provider"
    assert client.usage_total.prompt_tokens == 552
    assert client.usage_total.completion_tokens == 6859
    assert abs(client.usage_total.cost_usd - 0.028) < 1e-9
    assert client.usage_total.calls == 1


def test_usage_total_falls_back_to_estimate_when_no_usage_block() -> None:
    """Self-hosted vLLM-style response without ``usage`` falls back to char-based estimate."""
    client = LLMClient(api_key="x")
    client._client = MagicMock()
    client._client.post.return_value = _make_response(
        {"choices": [{"message": {"content": "hello world"}}]}
    )

    client.complete([{"role": "user", "content": "abcd" * 100}])
    assert client.usage_total.source == "estimate"
    assert client.usage_total.prompt_tokens > 0
    assert client.usage_total.completion_tokens > 0
    assert client.usage_total.cost_usd == 0.0
    assert client.usage_total.calls == 1


def test_usage_total_accumulates_across_calls() -> None:
    """Two successive ``complete()`` calls sum into ``usage_total``."""
    client = LLMClient(api_key="x")
    client._client = MagicMock()
    client._client.post.side_effect = [
        _make_response(
            {
                "choices": [{"message": {"content": "a"}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001},
            }
        ),
        _make_response(
            {
                "choices": [{"message": {"content": "b"}}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 7, "cost": 0.002},
            }
        ),
    ]

    client.complete([{"role": "user", "content": "1"}])
    client.complete([{"role": "user", "content": "2"}])
    assert client.usage_total.prompt_tokens == 40
    assert client.usage_total.completion_tokens == 12
    assert abs(client.usage_total.cost_usd - 0.003) < 1e-9
    assert client.usage_total.calls == 2
    assert client.usage_total.source == "provider"


def test_mock_client_exposes_zero_usage_total() -> None:
    """``MockLLMClient`` must satisfy ``LLMProtocol.usage_total`` for the compiler."""
    mock = MockLLMClient()
    assert isinstance(mock.usage_total, LLMUsage)
    assert mock.usage_total.calls == 0
    assert mock.usage_total.source == "none"


def test_provider_cost_extracted_from_openrouter_header_when_body_missing() -> None:
    """OpenRouter sometimes returns usage tokens in body and cost in headers."""
    client = LLMClient(api_key="x")
    client._client = MagicMock()
    client._client.post.return_value = _make_response(
        payload={
            "choices": [{"message": {"content": "x"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50},
        },
        headers={"x-openrouter-cost": "0.0044"},
    )

    client.complete([{"role": "user", "content": "hi"}])
    assert client.usage_total.source == "provider"
    assert client.usage_total.prompt_tokens == 100
    assert client.usage_total.completion_tokens == 50
    assert abs(client.usage_total.cost_usd - 0.0044) < 1e-9
