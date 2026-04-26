"""Devin v3 API discovery probe.

Goal: empirically discover the undocumented v3 endpoints we need for the
Greenhouse head-to-head harness — specifically (a) how to poll session
status, (b) how to extract changed files / diffs / PR URLs, (c) what
status enum values exist, (d) whether `repo` / `branch` / `commit` are
accepted at create time even though they're undocumented.

Confirmed by Perplexity from the official Devin docs:
  - Base URL: https://api.devin.ai/v3
  - Auth:    Authorization: Bearer cog_<token>
  - Create:  POST /v3/organizations/{org_id}/sessions  body={"prompt":"..."}

Everything else is a guess. This probe sends carefully-shaped requests
against likely paths (mirroring v1 conventions where they exist),
records every status code + response body, and writes a JSON trace for
post-hoc inspection.

Safety:
  - The session prompt is "Reply with the single word 'pong'..." so the
    agent does no real work. Combined with `max_acu_limit=1` (v1 field;
    v3 may ignore but it's a belt-and-suspenders safety net) the probe
    should burn at most a fraction of one ACU.
  - The session is tagged `acg-probe` and `unlisted=True` so it does not
    pollute the org's session feed (legacy field — v3 may ignore).
  - Re-runnable: pass `--session-id <id>` and `--no-create` to discover
    endpoints against an existing session without spending more ACUs.

Usage:
  export DEVIN_API_KEY=cog_xxxx
  export DEVIN_ORG_ID=org_xxxx
  python scripts/diagnostics/devin_api_probe.py \
    --out scripts/diagnostics/devin_probe_output.json

Output: a single JSON file with the full request/response trace.
Paste the file back into chat for analysis.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError as exc:  # pragma: no cover
    print(
        "error: httpx is required. Run `pip install httpx` or use the project venv.",
        file=sys.stderr,
    )
    raise SystemExit(2) from exc

DEFAULT_BASE_URL = "https://api.devin.ai"
DEFAULT_PROBE_PROMPT = (
    "Reply with the single word 'pong' and nothing else. "
    "Do not write any code, do not browse, do not interact with any repository. "
    "This is an integration probe; finishing immediately is the desired behavior."
)
DEFAULT_TIMEOUT_S = 30.0
DEFAULT_POLL_INTERVAL_S = 5.0
DEFAULT_MAX_POLL_S = 120.0
PROBE_TAG = "acg-probe"

# Endpoint discovery matrix.
# Each entry: (method, path_template, purpose, body_or_none).
# {org} and {sid} get substituted at runtime.
DISCOVERY_ENDPOINTS: list[tuple[str, str, str, dict[str, Any] | None]] = [
    # Session-detail variants — the most likely path is the v3 org-scoped one.
    ("GET", "/v3/organizations/{org}/sessions/{sid}", "session detail (v3 org-scoped)", None),
    ("GET", "/v3/sessions/{sid}", "session detail (v3 flat)", None),
    ("GET", "/v1/sessions/{sid}", "session detail (v1 legacy)", None),
    ("GET", "/v1/session/{sid}", "session detail (v1 singular)", None),
    # Listing — sanity check that auth works against an org-listing endpoint.
    ("GET", "/v3/organizations/{org}/sessions?limit=3", "list sessions (v3)", None),
    ("GET", "/v1/sessions?limit=3", "list sessions (v1)", None),
    # Messages / output — any of these probably hold the agent's chat or final output.
    ("GET", "/v3/organizations/{org}/sessions/{sid}/messages", "session messages (v3)", None),
    ("GET", "/v3/sessions/{sid}/messages", "session messages (v3 flat)", None),
    ("GET", "/v1/sessions/{sid}/messages", "session messages (v1)", None),
    # Files / diff / PR / branch — the megaplan-critical extraction endpoints.
    ("GET", "/v3/organizations/{org}/sessions/{sid}/files", "session files (v3)", None),
    ("GET", "/v3/organizations/{org}/sessions/{sid}/changes", "session changes (v3)", None),
    ("GET", "/v3/organizations/{org}/sessions/{sid}/diff", "session diff (v3)", None),
    ("GET", "/v3/organizations/{org}/sessions/{sid}/output", "session output (v3)", None),
    ("GET", "/v3/organizations/{org}/sessions/{sid}/pull-requests", "session PRs (v3)", None),
    (
        "GET",
        "/v3/organizations/{org}/sessions/{sid}/structured-output",
        "structured output (v3)",
        None,
    ),
    # Stop / delete — useful for cleanup, also discovers their cancellation API.
    ("POST", "/v3/organizations/{org}/sessions/{sid}/stop", "stop session (v3)", {}),
]


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _redacted_headers(headers: dict[str, str]) -> dict[str, str]:
    """Mask the bearer token before writing the trace to disk."""
    out = dict(headers)
    if "Authorization" in out:
        token = out["Authorization"]
        if token.startswith("Bearer ") and len(token) > 16:
            out["Authorization"] = f"Bearer {token[7:13]}...REDACTED"
    return out


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except (ValueError, json.JSONDecodeError):
        return {"_non_json_text": resp.text[:4000]}


def _record_response(
    method: str,
    url: str,
    *,
    request_body: Any,
    request_headers: dict[str, str],
    resp: httpx.Response | None,
    error: str | None = None,
    elapsed_ms: float = 0.0,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "request": {
            "method": method,
            "url": url,
            "headers": _redacted_headers(request_headers),
            "body": request_body,
        },
        "elapsed_ms": round(elapsed_ms, 2),
    }
    if resp is not None:
        record["response"] = {
            "status": resp.status_code,
            "headers": dict(resp.headers),
            "json_or_text": _safe_json(resp),
        }
    if error is not None:
        record["error"] = error
    return record


async def _request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: Any = None,
) -> dict[str, Any]:
    """Send one request, capture the full request/response trace."""
    started = time.perf_counter()
    try:
        if method == "GET":
            resp = await client.get(url, headers=headers)
        elif method == "POST":
            resp = await client.post(url, headers=headers, json=body)
        elif method == "DELETE":
            resp = await client.delete(url, headers=headers)
        else:
            raise ValueError(f"unsupported method {method}")
        return _record_response(
            method,
            url,
            request_body=body,
            request_headers=headers,
            resp=resp,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )
    except httpx.HTTPError as exc:
        return _record_response(
            method,
            url,
            request_body=body,
            request_headers=headers,
            resp=None,
            error=f"{type(exc).__name__}: {exc}",
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )


def _build_create_body(prompt: str, *, repo_url: str | None, branch: str | None) -> dict[str, Any]:
    """Send {prompt, tags, max_acu_limit, unlisted} plus optional repo hints.

    repo_url/branch are speculative — official docs do not list them, but
    we send them so the response tells us whether the API ignores or
    rejects unknown fields. That signal is what we want.
    """
    body: dict[str, Any] = {
        "prompt": prompt,
        "tags": [PROBE_TAG, f"started_at={_now_iso()}"],
        "max_acu_limit": 1,
        "unlisted": True,
        "title": "ACG Greenhouse probe (auto-generated, safe to delete)",
    }
    if repo_url:
        # Try multiple plausible field names — Devin will accept one or
        # silently drop them; the response shape will tell us.
        body["repo_url"] = repo_url
        body["repository_url"] = repo_url
    if branch:
        body["branch"] = branch
    return body


async def _create_session(
    client: httpx.AsyncClient,
    base_url: str,
    org_id: str,
    headers: dict[str, str],
    *,
    prompt: str,
    repo_url: str | None,
    branch: str | None,
) -> tuple[dict[str, Any], str | None]:
    """POST a probe session. Returns (trace_record, session_id_or_none)."""
    url = f"{base_url}/v3/organizations/{org_id}/sessions"
    body = _build_create_body(prompt, repo_url=repo_url, branch=branch)
    record = await _request(client, "POST", url, headers=headers, body=body)
    sid: str | None = None
    resp = record.get("response") or {}
    payload = resp.get("json_or_text") or {}
    if isinstance(payload, dict):
        # Try several plausible session-id field names.
        for key in ("session_id", "id", "sessionId"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                sid = value
                break
    return record, sid


async def _discover(
    client: httpx.AsyncClient,
    base_url: str,
    org_id: str,
    sid: str,
    headers: dict[str, str],
) -> list[dict[str, Any]]:
    """Hit every guessed endpoint once, in parallel where safe."""
    tasks = []
    for method, path_tpl, purpose, body in DISCOVERY_ENDPOINTS:
        url = base_url + path_tpl.format(org=org_id, sid=sid)
        tasks.append(
            _annotated_request(client, method, url, headers=headers, body=body, purpose=purpose)
        )
    return list(await asyncio.gather(*tasks))


async def _annotated_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: Any,
    purpose: str,
) -> dict[str, Any]:
    record = await _request(client, method, url, headers=headers, body=body)
    record["purpose"] = purpose
    return record


async def _poll_until_terminal(
    client: httpx.AsyncClient,
    base_url: str,
    org_id: str,
    sid: str,
    headers: dict[str, str],
    *,
    poll_interval_s: float,
    max_poll_s: float,
) -> list[dict[str, Any]]:
    """Hit the v3 org-scoped session-detail endpoint on a fixed cadence.

    We extract whatever field smells like ``status`` / ``state`` and stop
    once it matches a known-terminal-ish value. Even if the field name
    is novel the trace records every poll.
    """
    url = f"{base_url}/v3/organizations/{org_id}/sessions/{sid}"
    log: list[dict[str, Any]] = []
    deadline = time.perf_counter() + max_poll_s
    while True:
        record = await _request(client, "GET", url, headers=headers)
        record["polled_at"] = _now_iso()
        log.append(record)
        status = _extract_status(record)
        if status:
            record["extracted_status"] = status
        terminal = status and status.lower() in {
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
        }
        if terminal:
            break
        if time.perf_counter() >= deadline:
            break
        await asyncio.sleep(poll_interval_s)
    return log


def _extract_status(record: dict[str, Any]) -> str | None:
    payload = ((record.get("response") or {}).get("json_or_text")) or {}
    if not isinstance(payload, dict):
        return None
    for key in ("status", "state", "session_status", "status_indicator"):
        value = payload.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _summary(
    create_record: dict[str, Any],
    discovery: list[dict[str, Any]],
    poll_log: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute a compact summary so the trace is skimmable."""

    def status_of(record: dict[str, Any]) -> int | None:
        return (record.get("response") or {}).get("status")

    twoxx, fourohfour, other = [], [], []
    for record in discovery:
        s = status_of(record)
        purpose = record.get("purpose", "?")
        url = record["request"]["url"]
        line = f"{s} {record['request']['method']:<6} {url}  ({purpose})"
        if s and 200 <= s < 300:
            twoxx.append(line)
        elif s == 404:
            fourohfour.append(line)
        else:
            other.append(line)

    statuses_seen = sorted({s for r in poll_log if (s := r.get("extracted_status"))})
    last_status = poll_log[-1].get("extracted_status") if poll_log else None

    create_response = (create_record.get("response") or {}).get("json_or_text") or {}
    create_keys = sorted(create_response.keys()) if isinstance(create_response, dict) else []
    return {
        "create_status_code": status_of(create_record),
        "create_response_keys": create_keys,
        "discovered_2xx_endpoints": twoxx,
        "discovered_404_endpoints": fourohfour,
        "discovered_other_endpoints": other,
        "poll_count": len(poll_log),
        "poll_statuses_observed": statuses_seen,
        "poll_terminal_status": last_status,
    }


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Probe the Devin v3 API and dump a discovery trace.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("DEVIN_API_BASE_URL", DEFAULT_BASE_URL),
        help=f"API root (default: {DEFAULT_BASE_URL} or $DEVIN_API_BASE_URL).",
    )
    parser.add_argument(
        "--org-id",
        default=os.environ.get("DEVIN_ORG_ID"),
        help="Devin organization id (default: $DEVIN_ORG_ID).",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("DEVIN_API_KEY"),
        help=(
            "Bearer token, e.g. cog_xxxx (default: $DEVIN_API_KEY). NEVER pass on "
            "the command line in shared shells; prefer env vars."
        ),
    )
    parser.add_argument(
        "--prompt",
        default=DEFAULT_PROBE_PROMPT,
        help="Session prompt. Default is a benign 'reply with pong' prompt.",
    )
    parser.add_argument(
        "--repo-url",
        default=None,
        help=(
            "Optional repo URL to send in the create body — sent under both "
            "`repo_url` and `repository_url` so the response tells us which (if any) "
            "the API accepts. SAFE DEFAULT IS NONE."
        ),
    )
    parser.add_argument(
        "--branch",
        default=None,
        help="Optional branch hint to send. Same caveat as --repo-url.",
    )
    parser.add_argument(
        "--no-create",
        action="store_true",
        help="Skip session creation; require --session-id and only run discovery.",
    )
    parser.add_argument(
        "--session-id",
        default=None,
        help="Existing session id (use with --no-create or to skip the create step).",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("scripts/diagnostics/devin_probe_output.json"),
        help="Where to write the trace JSON.",
    )
    parser.add_argument(
        "--poll-interval-s",
        type=float,
        default=DEFAULT_POLL_INTERVAL_S,
    )
    parser.add_argument(
        "--max-poll-s",
        type=float,
        default=DEFAULT_MAX_POLL_S,
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=DEFAULT_TIMEOUT_S,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned requests and exit without sending anything.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive 'this will spend ACUs' confirmation prompt.",
    )
    return parser.parse_args(argv)


def _print_dry_run(args: argparse.Namespace) -> None:
    print("== DRY RUN — no requests will be sent ==\n")
    print(f"base_url:    {args.base_url}")
    print(f"org_id:      {args.org_id}")
    print(f"api_key:     {'<set>' if args.api_key else '<MISSING>'}")
    print()
    print("[1] Create:")
    body = _build_create_body(args.prompt, repo_url=args.repo_url, branch=args.branch)
    print(f"  POST {args.base_url}/v3/organizations/{args.org_id}/sessions")
    print(f"  body: {json.dumps(body, indent=2)}")
    print()
    print("[2] Discovery probes (one per endpoint, after create):")
    for method, path_tpl, purpose, _body in DISCOVERY_ENDPOINTS:
        print(f"  {method:<6} {args.base_url}{path_tpl}  ({purpose})")
    print()
    print(f"[3] Poll every {args.poll_interval_s}s for up to {args.max_poll_s}s.")
    print(f"[4] Write trace to {args.out}.")


async def _amain(args: argparse.Namespace) -> int:
    if args.dry_run:
        _print_dry_run(args)
        return 0
    if not args.api_key:
        print("error: --api-key (or $DEVIN_API_KEY) is required.", file=sys.stderr)
        return 2
    if not args.org_id:
        print("error: --org-id (or $DEVIN_ORG_ID) is required.", file=sys.stderr)
        return 2
    if args.no_create and not args.session_id:
        print("error: --no-create requires --session-id.", file=sys.stderr)
        return 2

    if not args.yes and not args.no_create:
        print("⚠  This probe will create a real Devin session against your org.")
        print(
            "   Prompt is benign ('reply with pong') and max_acu_limit=1 is set, "
            "but some ACU consumption is still possible."
        )
        print(f"   Org: {args.org_id}")
        print(f"   Base URL: {args.base_url}")
        try:
            answer = input("   Proceed? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in {"y", "yes"}:
            print("aborted.", file=sys.stderr)
            return 1

    headers = {
        "Authorization": f"Bearer {args.api_key}",
        "Content-Type": "application/json",
        "User-Agent": "acg-greenhouse-probe/0.1",
    }
    trace: dict[str, Any] = {
        "started_at": _now_iso(),
        "config": {
            "base_url": args.base_url,
            "org_id": args.org_id,
        },
    }

    async with httpx.AsyncClient(timeout=args.timeout_s) as client:
        sid: str | None = args.session_id
        if not args.no_create:
            print(
                f"creating session against {args.base_url}/v3/organizations/{args.org_id}/sessions ..."
            )
            create_record, created_sid = await _create_session(
                client,
                args.base_url,
                args.org_id,
                headers,
                prompt=args.prompt,
                repo_url=args.repo_url,
                branch=args.branch,
            )
            trace["create"] = create_record
            create_status = (create_record.get("response") or {}).get("status")
            print(f"  create status: {create_status}")
            if not created_sid:
                print(
                    "warning: could not parse a session_id from the create response. "
                    "Discovery will be skipped.",
                    file=sys.stderr,
                )
            sid = created_sid or sid

        if sid:
            print(f"discovering endpoints against session_id={sid} ...")
            trace["session_id"] = sid
            trace["discovery"] = await _discover(client, args.base_url, args.org_id, sid, headers)
            print("polling for terminal status ...")
            trace["poll_log"] = await _poll_until_terminal(
                client,
                args.base_url,
                args.org_id,
                sid,
                headers,
                poll_interval_s=args.poll_interval_s,
                max_poll_s=args.max_poll_s,
            )
            print(
                "re-running discovery after poll (so we can compare pre/post-completion shapes) ..."
            )
            trace["discovery_post_poll"] = await _discover(
                client, args.base_url, args.org_id, sid, headers
            )
        else:
            trace["discovery"] = []
            trace["poll_log"] = []
            trace["discovery_post_poll"] = []

    trace["finished_at"] = _now_iso()
    trace["summary"] = _summary(
        trace.get("create") or {},
        list(trace.get("discovery") or []) + list(trace.get("discovery_post_poll") or []),
        trace.get("poll_log") or [],
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(trace, sort_keys=True, indent=2) + "\n")
    print("\n=== summary ===")
    for line in trace["summary"]["discovered_2xx_endpoints"]:
        print(f"  ✓ {line}")
    for line in trace["summary"]["discovered_other_endpoints"]:
        print(f"  ? {line}")
    print(f"\n  poll_count: {trace['summary']['poll_count']}")
    print(f"  statuses observed: {trace['summary']['poll_statuses_observed']}")
    print(f"  terminal status: {trace['summary']['poll_terminal_status']}")
    print(f"\n  full trace: {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        print("\ninterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
