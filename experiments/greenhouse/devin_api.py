"""HTTP client and parsers for the Devin v3 organization-scoped API.

Empirically discovered via ``scripts/diagnostics/devin_api_probe.py``
against a real enterprise org. The endpoints below all returned 200;
sibling endpoints we tried (``/files``, ``/diff``, ``/output``,
``/stop``, ``/pull-requests``) returned 404. Extraction data lives **on
the session detail JSON itself**, not under separate sub-resources.

Confirmed v3 surface:

- ``POST /v3/organizations/{org_id}/sessions`` — create a session and
  receive the full session detail back.
- ``GET  /v3/organizations/{org_id}/sessions/{session_id}`` — poll
  status and read ``pull_requests`` and ``structured_output``.
- ``GET  /v3/organizations/{org_id}/sessions/{session_id}/messages``
  — paginated ``{items, total, end_cursor, has_next_page}`` chat log.

Status semantics (also empirical):

- ``status`` enum seen so far: ``new``, ``claimed``, ``running``.
- ``status_detail`` carries fine-grained sub-state. ``status="running"``
  + ``status_detail="waiting_for_user"`` is the conversational
  "Devin has replied; awaiting next message" state — for our one-shot
  harness we treat that as a successful terminal state.

Devin gracefully ignores unknown create-body fields. We send
``structured_output_schema`` (a v1-documented field) and read back
``structured_output`` to extract the agent's final structured response.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.devin.ai"
DEFAULT_REQUEST_TIMEOUT_S = 60.0
DEFAULT_POLL_INTERVAL_S = 30.0
DEFAULT_MAX_WAIT_S = 2700.0  # 45 min — Devin codegen sessions often run 5–30 min.

# Status values that mean "stop polling, the session is finished".
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        "completed",
        "complete",
        "finished",
        "done",
        "success",
        "succeeded",
        "stopped",
        "blocked",
        "failed",
        "error",
        "errored",
        "cancelled",
        "canceled",
        "archived",
    }
)
# `running` + this status_detail means "Devin replied and is awaiting the
# next user message" — terminal for our one-shot harness.
CONVERSATIONAL_TERMINAL_DETAILS: frozenset[str] = frozenset({"waiting_for_user"})

# Statuses that count as successful in our scoring rubric. Anything else
# in TERMINAL_STATUSES (failed/blocked/cancelled/...) is a failure.
SUCCESS_STATUSES: frozenset[str] = frozenset(
    {"completed", "complete", "finished", "done", "success", "succeeded"}
)


class DevinAPIError(RuntimeError):
    """Raised when the Devin API returns a non-2xx HTTP response.

    The ``status_code`` and ``payload`` attributes carry the raw response
    so callers can branch on auth vs. quota vs. transient errors.
    """

    def __init__(self, status_code: int, message: str, payload: Any = None) -> None:
        super().__init__(f"Devin API {status_code}: {message}")
        self.status_code = status_code
        self.payload = payload


# ---------------------------------------------------------------------------
# Dataclasses for the v3 response shapes we actually care about.
# ---------------------------------------------------------------------------


@dataclass
class DevinPullRequest:
    """A pull request opened by a Devin session.

    Field names mirror what Devin returns. We only require ``url``;
    other fields default to ``None`` because the schema is sparsely
    documented and Devin sometimes omits values.
    """

    url: str
    title: str | None = None
    branch: str | None = None
    state: str | None = None
    repository: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> DevinPullRequest:
        url = payload.get("url") or payload.get("html_url") or payload.get("pull_request_url") or ""
        return cls(
            url=url,
            title=payload.get("title"),
            branch=payload.get("branch") or payload.get("head_branch") or payload.get("head"),
            state=payload.get("state") or payload.get("status"),
            repository=payload.get("repository") or payload.get("repo"),
            raw=dict(payload),
        )


@dataclass
class DevinSessionDetail:
    """Parsed view of ``GET /v3/organizations/{org}/sessions/{sid}``."""

    session_id: str
    status: str | None
    status_detail: str | None
    acus_consumed: float | None
    pull_requests: list[DevinPullRequest]
    structured_output: Any
    tags: list[str]
    title: str | None
    url: str | None
    created_at: int | None
    updated_at: int | None
    raw: dict[str, Any]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> DevinSessionDetail:
        prs_raw = payload.get("pull_requests") or []
        if not isinstance(prs_raw, list):
            prs_raw = []
        prs = [DevinPullRequest.from_payload(pr) for pr in prs_raw if isinstance(pr, dict)]
        sid = payload.get("session_id") or payload.get("id") or ""
        if not isinstance(sid, str) or not sid:
            raise DevinAPIError(
                200,
                f"missing session_id in response payload (keys={sorted(payload.keys())})",
                payload=payload,
            )
        return cls(
            session_id=sid,
            status=_optional_str(payload.get("status")),
            status_detail=_optional_str(payload.get("status_detail")),
            acus_consumed=_optional_float(payload.get("acus_consumed")),
            pull_requests=prs,
            structured_output=payload.get("structured_output"),
            tags=[t for t in (payload.get("tags") or []) if isinstance(t, str)],
            title=_optional_str(payload.get("title")),
            url=_optional_str(payload.get("url")),
            created_at=_optional_int(payload.get("created_at")),
            updated_at=_optional_int(payload.get("updated_at")),
            raw=dict(payload),
        )

    def is_terminal(self) -> bool:
        """Return True iff the session has reached a state we should stop polling on."""
        if self.status and self.status.lower() in TERMINAL_STATUSES:
            return True
        if (
            self.status
            and self.status.lower() == "running"
            and self.status_detail
            and self.status_detail.lower() in CONVERSATIONAL_TERMINAL_DETAILS
        ):
            return True
        return False

    def is_success(self) -> bool:
        """Heuristic: treat as success if status is success-ish OR Devin opened a PR."""
        if self.status and self.status.lower() in SUCCESS_STATUSES:
            return True
        if (
            self.status
            and self.status.lower() == "running"
            and self.status_detail
            and self.status_detail.lower() in CONVERSATIONAL_TERMINAL_DETAILS
        ):
            return True
        if self.pull_requests:
            return True
        return False


@dataclass
class DevinMessage:
    """One message from ``GET /sessions/{sid}/messages``."""

    event_id: str
    source: str  # "user" | "devin" | other
    message: str
    created_at: int | None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> DevinMessage:
        return cls(
            event_id=str(payload.get("event_id") or ""),
            source=str(payload.get("source") or ""),
            message=str(payload.get("message") or ""),
            created_at=_optional_int(payload.get("created_at")),
        )


def _optional_str(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _optional_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    return None


# ---------------------------------------------------------------------------
# Client.
# ---------------------------------------------------------------------------


class DevinClient:
    """Thin async wrapper around the v3 API.

    Use as an async context manager so the underlying ``httpx.AsyncClient``
    is closed deterministically::

        async with DevinClient.from_env() as client:
            session = await client.create_session(prompt="...")
            final = await client.wait_for_terminal(session.session_id)

    Tests should pass ``transport=httpx.MockTransport(handler)`` to avoid
    hitting the real API.
    """

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_BASE_URL,
        org_id: str,
        api_key: str,
        timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
        user_agent: str = "acg-greenhouse/0.1",
    ) -> None:
        if not org_id:
            raise ValueError("org_id is required")
        if not api_key:
            raise ValueError("api_key is required")
        self.base_url = base_url.rstrip("/")
        self.org_id = org_id
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": user_agent,
        }
        client_kwargs: dict[str, Any] = {
            "timeout": timeout_s,
            "headers": self._headers,
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**client_kwargs)

    # ----- env-var convenience ------------------------------------------------

    @classmethod
    def from_env(
        cls,
        *,
        env: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
    ) -> DevinClient:
        """Construct a client from ``DEVIN_API_KEY`` / ``DEVIN_ORG_ID`` env vars.

        Optionally honors ``DEVIN_API_BASE_URL`` for self-hosted enterprise
        tenants. ``env`` may be passed in tests; defaults to ``os.environ``.
        """
        import os

        source = env if env is not None else os.environ
        api_key = source.get("DEVIN_API_KEY", "")
        org_id = source.get("DEVIN_ORG_ID", "")
        base_url = source.get("DEVIN_API_BASE_URL", DEFAULT_BASE_URL)
        if not api_key:
            raise DevinAPIError(0, "DEVIN_API_KEY is not set")
        if not org_id:
            raise DevinAPIError(0, "DEVIN_ORG_ID is not set")
        return cls(
            base_url=base_url,
            org_id=org_id,
            api_key=api_key,
            transport=transport,
            timeout_s=timeout_s,
        )

    # ----- async lifecycle ---------------------------------------------------

    async def __aenter__(self) -> DevinClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ----- core endpoints ----------------------------------------------------

    async def create_session(
        self,
        prompt: str,
        *,
        tags: list[str] | None = None,
        structured_output_schema: dict[str, Any] | None = None,
        max_acu_limit: int | None = None,
        title: str | None = None,
        unlisted: bool = False,
        idempotent: bool = False,
        playbook_id: str | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> DevinSessionDetail:
        """``POST /v3/organizations/{org}/sessions`` — start a new session.

        Returns the parsed session detail. The Devin API responds with the
        full session JSON (not just a session_id), so we don't need a
        follow-up GET to learn the initial state.
        """
        body: dict[str, Any] = {"prompt": prompt}
        if tags:
            body["tags"] = list(tags)
        if structured_output_schema is not None:
            body["structured_output_schema"] = structured_output_schema
        if max_acu_limit is not None:
            body["max_acu_limit"] = int(max_acu_limit)
        if title:
            body["title"] = title
        if unlisted:
            body["unlisted"] = True
        if idempotent:
            body["idempotent"] = True
        if playbook_id:
            body["playbook_id"] = playbook_id
        if extra_body:
            body.update(extra_body)
        url = f"{self.base_url}/v3/organizations/{self.org_id}/sessions"
        payload = await self._post_json(url, body)
        return DevinSessionDetail.from_payload(payload)

    async def get_session(self, session_id: str) -> DevinSessionDetail:
        """``GET /v3/organizations/{org}/sessions/{session_id}``."""
        url = f"{self.base_url}/v3/organizations/{self.org_id}/sessions/{session_id}"
        payload = await self._get_json(url)
        return DevinSessionDetail.from_payload(payload)

    async def get_messages(
        self,
        session_id: str,
        *,
        max_pages: int = 20,
    ) -> list[DevinMessage]:
        """``GET /sessions/{session_id}/messages`` — fetch all chat messages.

        Paginates via ``end_cursor`` / ``has_next_page`` until exhausted or
        ``max_pages`` is reached (safety cap; Devin sessions are typically
        well under that).
        """
        url = f"{self.base_url}/v3/organizations/{self.org_id}/sessions/{session_id}/messages"
        out: list[DevinMessage] = []
        cursor: str | None = None
        for _ in range(max_pages):
            page_url = url + (f"?cursor={cursor}" if cursor else "")
            payload = await self._get_json(page_url)
            items = payload.get("items") or []
            for item in items:
                if isinstance(item, dict):
                    out.append(DevinMessage.from_payload(item))
            if not payload.get("has_next_page"):
                break
            next_cursor = payload.get("end_cursor")
            if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        return out

    async def wait_for_terminal(
        self,
        session_id: str,
        *,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        max_wait_s: float = DEFAULT_MAX_WAIT_S,
        on_poll: callable | None = None,
    ) -> DevinSessionDetail:
        """Poll ``get_session`` until the session reaches a terminal state.

        ``on_poll(detail)`` is called after every poll, useful for live
        progress reporting in the CLI. Raises :class:`DevinAPIError` with
        ``status_code=408`` if ``max_wait_s`` elapses without terminal.
        """
        deadline = time.monotonic() + max_wait_s
        last: DevinSessionDetail | None = None
        while True:
            detail = await self.get_session(session_id)
            last = detail
            if on_poll is not None:
                try:
                    on_poll(detail)
                except Exception:
                    # Progress hooks must never break the poll loop.
                    pass
            if detail.is_terminal():
                return detail
            if time.monotonic() >= deadline:
                raise DevinAPIError(
                    408,
                    f"session {session_id} did not reach terminal state within {max_wait_s:.0f}s "
                    f"(last status={detail.status!r}, status_detail={detail.status_detail!r})",
                    payload=detail.raw,
                )
            await asyncio.sleep(poll_interval_s)
        # unreachable but keeps type checkers happy
        assert last is not None  # pragma: no cover
        return last  # pragma: no cover

    # ----- private helpers ---------------------------------------------------

    async def _get_json(self, url: str) -> dict[str, Any]:
        try:
            resp = await self._client.get(url)
        except httpx.HTTPError as exc:
            raise DevinAPIError(0, f"network error on GET {url}: {exc}") from exc
        return self._unwrap(resp)

    async def _post_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.post(url, json=body)
        except httpx.HTTPError as exc:
            raise DevinAPIError(0, f"network error on POST {url}: {exc}") from exc
        return self._unwrap(resp)

    @staticmethod
    def _unwrap(resp: httpx.Response) -> dict[str, Any]:
        if 200 <= resp.status_code < 300:
            try:
                payload = resp.json()
            except (ValueError, json.JSONDecodeError) as exc:
                raise DevinAPIError(
                    resp.status_code,
                    f"non-JSON response: {resp.text[:200]}",
                ) from exc
            if not isinstance(payload, dict):
                raise DevinAPIError(
                    resp.status_code,
                    f"expected JSON object, got {type(payload).__name__}",
                    payload=payload,
                )
            return payload
        # Non-2xx: surface the structured error if any.
        try:
            payload = resp.json()
        except (ValueError, json.JSONDecodeError):
            payload = {"_text": resp.text[:500]}
        message = ""
        if isinstance(payload, dict):
            for key in ("detail", "message", "error"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    message = value
                    break
        raise DevinAPIError(resp.status_code, message or resp.reason_phrase, payload=payload)


# ---------------------------------------------------------------------------
# Pure parsers — extract the changed-file list from a finished session.
# ---------------------------------------------------------------------------


# Schema we send at create time so Devin returns a structured final reply.
CHANGED_FILES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "changed_files": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Repository-relative paths of every file you modified, created, or deleted.",
        },
        "pr_url": {
            "type": ["string", "null"],
            "description": "URL of the pull request you opened, if any.",
        },
        "branch": {
            "type": ["string", "null"],
            "description": "Name of the git branch you pushed.",
        },
        "summary": {
            "type": "string",
            "description": "One-paragraph human summary of what you changed and why.",
        },
    },
    "required": ["changed_files", "summary"],
}


# Markdown ```json blocks in a chat message.
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)
# Inline file paths Devin commonly mentions: "src/foo/Bar.java", "pom.xml", etc.
# Conservative — requires either a slash or a known top-level filename.
_INLINE_PATH_RE = re.compile(
    r"\b([A-Za-z0-9_./-]+\.(?:java|xml|kt|gradle|properties|yml|yaml|json|md|sql|sh|py|ts|tsx|js|jsx))\b"
)


@dataclass
class ChangedFilesExtraction:
    """Result of pulling the changed-file list from a finished session.

    ``files`` is the deduped, sorted union of whatever the most-trusted
    source produced. ``source`` records which path won so the eval
    artifact can audit which extraction route fired.
    """

    files: list[str]
    source: str  # "structured_output" | "fenced_json_message" | "inline_path_scan" | "empty"
    pr_url: str | None = None
    branch: str | None = None
    summary: str | None = None


def extract_changed_files(
    detail: DevinSessionDetail,
    messages: list[DevinMessage] | None,
) -> ChangedFilesExtraction:
    """Pull the changed-file list using a three-tier fallback.

    1. ``structured_output`` field on the session detail (authoritative
       when Devin honors the schema we sent at create time).
    2. The last ``json`` fenced block in any ``source="devin"`` message.
    3. Conservative regex scan for file-path-shaped tokens in Devin's
       messages (last resort; reported separately via ``source``).
    """
    # Tier 1: structured_output.
    so = detail.structured_output
    parsed_so: dict[str, Any] | None = None
    if isinstance(so, dict):
        parsed_so = so
    elif isinstance(so, str) and so.strip():
        try:
            candidate = json.loads(so)
            if isinstance(candidate, dict):
                parsed_so = candidate
        except json.JSONDecodeError:
            parsed_so = None
    if parsed_so is not None:
        files = _coerce_string_list(parsed_so.get("changed_files"))
        if files:
            return ChangedFilesExtraction(
                files=_dedupe_sorted(files),
                source="structured_output",
                pr_url=_optional_str(parsed_so.get("pr_url")),
                branch=_optional_str(parsed_so.get("branch")),
                summary=_optional_str(parsed_so.get("summary")),
            )

    # Tier 2: fenced JSON in the latest devin message.
    if messages:
        for msg in reversed(messages):
            if msg.source.lower() != "devin":
                continue
            for match in _FENCED_JSON_RE.finditer(msg.message):
                try:
                    candidate = json.loads(match.group(1))
                except json.JSONDecodeError:
                    continue
                if not isinstance(candidate, dict):
                    continue
                files = _coerce_string_list(candidate.get("changed_files"))
                if files:
                    return ChangedFilesExtraction(
                        files=_dedupe_sorted(files),
                        source="fenced_json_message",
                        pr_url=_optional_str(candidate.get("pr_url")),
                        branch=_optional_str(candidate.get("branch")),
                        summary=_optional_str(candidate.get("summary")),
                    )

    # Tier 3: regex scan over devin messages.
    if messages:
        scanned: set[str] = set()
        for msg in messages:
            if msg.source.lower() != "devin":
                continue
            for match in _INLINE_PATH_RE.finditer(msg.message):
                token = match.group(1)
                # Skip tokens that look like URLs or fully-qualified Java class names.
                if "://" in token or token.startswith("."):
                    continue
                scanned.add(token)
        if scanned:
            return ChangedFilesExtraction(
                files=sorted(scanned),
                source="inline_path_scan",
            )

    return ChangedFilesExtraction(files=[], source="empty")


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out = []
    for item in value:
        if isinstance(item, str) and item:
            out.append(item)
    return out


def _dedupe_sorted(items: list[str]) -> list[str]:
    return sorted(set(items))
