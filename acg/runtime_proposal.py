"""Worker reply classification for run traces and eval artifacts.

OpenAI-compatible ``finish_reason`` values and empty / refusal replies are
collapsed into a small ``proposal_status`` enum consumed by
:class:`acg.runtime.WorkerResult` and Greenhouse ``eval_run.json`` summaries.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final

PROPOSAL_OK: Final = "ok"
PROPOSAL_TRUNCATED: Final = "truncated"
PROPOSAL_UNPARSEABLE: Final = "unparseable"
PROPOSAL_DECLINED: Final = "declined"
PROPOSAL_TRANSPORT_ERROR: Final = "transport_error"

PROPOSAL_STATUSES: tuple[str, ...] = (
    PROPOSAL_OK,
    PROPOSAL_TRUNCATED,
    PROPOSAL_UNPARSEABLE,
    PROPOSAL_DECLINED,
    PROPOSAL_TRANSPORT_ERROR,
)

_REFUSAL_SNIPPETS: tuple[str, ...] = (
    "i can't help",
    "i cannot help",
    "i'm not able to",
    "i am not able to",
    "cannot assist",
    "can't assist",
    "unable to assist",
    "unable to help",
    "i'm sorry, but",
    "i am sorry, but",
    "as an ai",
    "i cannot comply",
    "can't comply",
)


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    if not t.startswith("```"):
        return t
    t = re.sub(r"^```(?:json)?\s*", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s*```\s*$", "", t)
    return t.strip()


def _json_payload_maybe_writes(text: str) -> Any | None:
    """Return parsed JSON when ``text`` is or contains a single JSON object."""
    t = _strip_code_fence(text)
    if not t:
        return None
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start = t.find("{")
        end = t.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(t[start : end + 1])
        except json.JSONDecodeError:
            return None


def explicit_empty_writes_payload(content: str) -> bool:
    """True when JSON parses to an object with an empty ``writes``/``proposals`` list."""
    payload = _json_payload_maybe_writes(content)
    if not isinstance(payload, dict):
        return False
    for key in ("writes", "proposals"):
        if key in payload:
            val = payload[key]
            return isinstance(val, list) and len(val) == 0
    return False


def classify_zero_proposal_reply(*, raw_content: str, finish_reason: str) -> str:
    """Classify a worker reply that yielded zero parsed file proposals.

    Precondition: the caller already ruled out ``transport_error`` and
    ``truncated`` (``finish_reason == length``).
    """
    del finish_reason  # reserved for future provider-specific rules
    text = (raw_content or "").strip()
    if not text:
        return PROPOSAL_DECLINED
    lowered = text.lower()
    if any(s in lowered for s in _REFUSAL_SNIPPETS):
        return PROPOSAL_DECLINED
    if explicit_empty_writes_payload(text):
        return PROPOSAL_OK
    return PROPOSAL_UNPARSEABLE


def proposal_status_counts_dict() -> dict[str, int]:
    return {k: 0 for k in PROPOSAL_STATUSES}
