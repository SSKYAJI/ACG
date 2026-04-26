"""Tests for the Devin v3 API client and head-to-head adapter.

These tests use ``httpx.MockTransport`` to simulate the v3 API without
hitting the real ``api.devin.ai``. Response shapes mirror what the live
probe (``scripts/diagnostics/devin_api_probe.py``) recorded against a
real enterprise org, so any change in the schema we rely on shows up
here first.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from acg.schema import (
    AgentLock,
    Conflict,
    ExecutionPlan,
    Generator,
    Group,
    PredictedWrite,
    Repo,
    Task,
)
from experiments.greenhouse import devin_adapter
from experiments.greenhouse.devin_api import (
    CHANGED_FILES_SCHEMA,
    DevinAPIError,
    DevinClient,
    DevinMessage,
    DevinPullRequest,
    DevinSessionDetail,
    extract_changed_files,
)
from experiments.greenhouse.devin_prompts import (
    build_naive_prompt,
    build_planned_prompt,
)

# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


def _greenhouse_task(task_id: str, service_path: str) -> Task:
    return Task(
        id=task_id,
        prompt=f"Replace anonymous RowMapper in {service_path} with a Java 8 lambda.",
        predicted_writes=[
            PredictedWrite(path=service_path, confidence=0.95, reason="primary edit"),
            PredictedWrite(path="pom.xml", confidence=0.9, reason="java-version bump"),
        ],
        allowed_paths=[service_path, "pom.xml"],
        depends_on=[],
        parallel_group=None,
        rationale=None,
    )


def _build_lock(*, serialized: bool) -> AgentLock:
    tasks = [
        _greenhouse_task(
            "lambda-rowmapper-account",
            "src/main/java/com/springsource/greenhouse/account/JdbcAccountRepository.java",
        ),
        _greenhouse_task(
            "lambda-rowmapper-invite",
            "src/main/java/com/springsource/greenhouse/invite/JdbcInviteRepository.java",
        ),
        _greenhouse_task(
            "lambda-rowmapper-app",
            "src/main/java/com/springsource/greenhouse/develop/JdbcAppRepository.java",
        ),
    ]
    if serialized:
        groups = [
            Group(id=1, tasks=[tasks[0].id], type="serial", waits_for=[]),
            Group(id=2, tasks=[tasks[1].id], type="serial", waits_for=[1]),
            Group(id=3, tasks=[tasks[2].id], type="serial", waits_for=[2]),
        ]
        conflicts = [
            Conflict(
                files=["pom.xml"],
                between_tasks=[tasks[0].id, tasks[1].id],
                resolution="serialize on pom.xml",
            )
        ]
    else:
        groups = [Group(id=1, tasks=[t.id for t in tasks], type="parallel", waits_for=[])]
        conflicts = []
    return AgentLock(
        version="1.0",
        generated_at=AgentLock.utcnow(),
        generator=Generator(tool="acg", version="test", model="mock"),
        repo=Repo(
            root="experiments/greenhouse/checkout",
            git_url="https://github.com/SSKYAJI/greenhouse.git",
            commit="174c1c320875a66447deb2a15d04fc86afd07f60",
            languages=["java"],
        ),
        tasks=tasks,
        execution_plan=ExecutionPlan(groups=groups),
        conflicts_detected=conflicts,
    )


# ---------------------------------------------------------------------------
# Mock transport helpers — simulate the v3 API.
# ---------------------------------------------------------------------------


class FakeDevinAPI:
    """In-memory mock of the v3 endpoints we hit.

    Each ``create_session`` returns a deterministic session_id and stores
    a script for how subsequent ``GET /sessions/{sid}`` polls should
    transition. Used as ``httpx.MockTransport(fake.handler)``.
    """

    def __init__(self, *, org_id: str = "org_test") -> None:
        self.org_id = org_id
        self._next_id = 0
        self._sessions: dict[str, dict[str, Any]] = {}
        self._messages: dict[str, list[dict[str, Any]]] = {}
        self._poll_scripts: dict[str, list[dict[str, Any]]] = {}
        self.create_calls: list[dict[str, Any]] = []
        self.get_calls: list[str] = []
        self.message_calls: list[str] = []

    def script_session(
        self,
        *,
        prompt_substr: str | None = None,
        final_status: str = "running",
        final_status_detail: str = "waiting_for_user",
        pull_requests: list[dict[str, Any]] | None = None,
        structured_output: dict[str, Any] | None = None,
        messages: list[dict[str, Any]] | None = None,
        acus_consumed: float = 1.5,
        intermediate_polls: list[dict[str, Any]] | None = None,
    ) -> str:
        """Pre-seed a session that ``create_session`` will return when the
        prompt contains ``prompt_substr`` (or any prompt if None).

        Returns the synthetic session_id so tests can assert on it.
        """
        self._next_id += 1
        sid = f"session_test_{self._next_id:06d}"
        base_session = {
            "session_id": sid,
            "status": "new",
            "status_detail": None,
            "acus_consumed": 0,
            "pull_requests": [],
            "structured_output": None,
            "tags": [],
            "title": None,
            "url": f"https://devin.example/sessions/{sid}",
            "created_at": 1700000000 + self._next_id,
            "updated_at": 1700000000 + self._next_id,
            "_match_substr": prompt_substr,
        }
        self._sessions[sid] = base_session
        self._poll_scripts[sid] = list(intermediate_polls or []) + [
            {
                "status": final_status,
                "status_detail": final_status_detail,
                "acus_consumed": acus_consumed,
                "pull_requests": list(pull_requests or []),
                "structured_output": structured_output,
            }
        ]
        self._messages[sid] = list(messages or [])
        return sid

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/sessions"):
            return self._handle_create(request)
        # /messages must be checked before bare /sessions/{sid}.
        if request.method == "GET" and path.endswith("/messages"):
            return self._handle_messages(request)
        if request.method == "GET" and "/sessions/" in path:
            return self._handle_get_session(request)
        return httpx.Response(404, json={"detail": "Not Found"})

    def _handle_create(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        prompt = body.get("prompt") or ""
        self.create_calls.append({"body": body, "headers": dict(request.headers)})
        # Find the first scripted session whose match_substr is None or
        # appears in the prompt.
        for _sid, sess in self._sessions.items():
            if sess.get("_dispatched"):
                continue
            substr = sess.get("_match_substr")
            if substr is None or substr in prompt:
                sess["_dispatched"] = True
                response_payload = {k: v for k, v in sess.items() if not k.startswith("_")}
                response_payload["tags"] = body.get("tags") or []
                response_payload["title"] = body.get("title")
                return httpx.Response(200, json=response_payload)
        return httpx.Response(
            500,
            json={"detail": "no scripted session matched", "_prompt": prompt[:200]},
        )

    def _handle_get_session(self, request: httpx.Request) -> httpx.Response:
        sid = request.url.path.rsplit("/", 1)[-1]
        self.get_calls.append(sid)
        sess = self._sessions.get(sid)
        if sess is None:
            return httpx.Response(404, json={"detail": "Not Found"})
        script = self._poll_scripts.get(sid) or []
        if script:
            step = script.pop(0)
            sess.update(step)
        response_payload = {k: v for k, v in sess.items() if not k.startswith("_")}
        return httpx.Response(200, json=response_payload)

    def _handle_messages(self, request: httpx.Request) -> httpx.Response:
        # /v3/organizations/{org}/sessions/{sid}/messages
        sid = request.url.path.rsplit("/", 2)[-2]
        self.message_calls.append(sid)
        items = self._messages.get(sid) or []
        return httpx.Response(
            200,
            json={
                "items": items,
                "total": len(items),
                "has_next_page": False,
                "end_cursor": None,
            },
        )


def _build_client(fake: FakeDevinAPI) -> DevinClient:
    transport = httpx.MockTransport(fake.handler)
    return DevinClient(
        base_url="https://api.devin.example",
        org_id=fake.org_id,
        api_key="cog_test_token",
        transport=transport,
    )


# ---------------------------------------------------------------------------
# DevinClient unit tests.
# ---------------------------------------------------------------------------


def test_client_constructor_rejects_missing_credentials() -> None:
    with pytest.raises(ValueError, match="org_id"):
        DevinClient(base_url="x", org_id="", api_key="cog_x")
    with pytest.raises(ValueError, match="api_key"):
        DevinClient(base_url="x", org_id="o", api_key="")


def test_from_env_raises_devin_api_error_when_unset() -> None:
    with pytest.raises(DevinAPIError, match="DEVIN_API_KEY"):
        DevinClient.from_env(env={})
    with pytest.raises(DevinAPIError, match="DEVIN_ORG_ID"):
        DevinClient.from_env(env={"DEVIN_API_KEY": "cog_x"})


def test_create_session_sends_bearer_and_parses_response() -> None:
    fake = FakeDevinAPI()
    sid = fake.script_session()
    import asyncio

    async def _go() -> DevinSessionDetail:
        async with _build_client(fake) as client:
            return await client.create_session(
                prompt="please do thing",
                tags=["strategy=naive_parallel", "task_id=x"],
                structured_output_schema=CHANGED_FILES_SCHEMA,
                max_acu_limit=5,
                title="t",
            )

    detail = asyncio.run(_go())
    assert detail.session_id == sid
    assert detail.status == "new"
    assert fake.create_calls
    sent_headers = fake.create_calls[0]["headers"]
    assert sent_headers["authorization"] == "Bearer cog_test_token"
    assert sent_headers["content-type"] == "application/json"
    sent_body = fake.create_calls[0]["body"]
    assert sent_body["prompt"] == "please do thing"
    assert "structured_output_schema" in sent_body
    assert sent_body["max_acu_limit"] == 5
    assert sent_body["title"] == "t"


def test_get_session_raises_devin_api_error_on_404() -> None:
    fake = FakeDevinAPI()
    import asyncio

    async def _go() -> None:
        async with _build_client(fake) as client:
            await client.get_session("nope")

    with pytest.raises(DevinAPIError) as excinfo:
        asyncio.run(_go())
    assert excinfo.value.status_code == 404


def test_wait_for_terminal_polls_until_terminal_status_detail() -> None:
    fake = FakeDevinAPI()
    sid = fake.script_session(
        intermediate_polls=[
            {"status": "claimed", "status_detail": None},
            {"status": "running", "status_detail": "thinking"},
        ],
        final_status="running",
        final_status_detail="waiting_for_user",
        pull_requests=[
            {"url": "https://github.com/x/y/pull/1", "branch": "feat/x", "title": "t"},
        ],
    )
    import asyncio

    async def _go() -> DevinSessionDetail:
        async with _build_client(fake) as client:
            return await client.wait_for_terminal(
                sid,
                poll_interval_s=0.0,
                max_wait_s=5.0,
            )

    detail = asyncio.run(_go())
    assert detail.is_terminal()
    assert detail.is_success()
    # FakeDevinAPI returned 3 polls (claimed, running/thinking, running/waiting_for_user)
    assert len(fake.get_calls) >= 3
    assert detail.pull_requests[0].url == "https://github.com/x/y/pull/1"


def test_wait_for_terminal_times_out() -> None:
    fake = FakeDevinAPI()
    # Script that never reaches a terminal state — every poll says 'running/thinking'.
    sid = fake.script_session(
        final_status="running",
        final_status_detail="thinking",
    )
    # Replace the script with infinite non-terminal entries.
    fake._poll_scripts[sid] = [
        {"status": "running", "status_detail": "thinking"},
        {"status": "running", "status_detail": "thinking"},
        {"status": "running", "status_detail": "thinking"},
    ]
    import asyncio

    async def _go() -> None:
        async with _build_client(fake) as client:
            await client.wait_for_terminal(sid, poll_interval_s=0.0, max_wait_s=0.001)

    with pytest.raises(DevinAPIError) as excinfo:
        asyncio.run(_go())
    assert excinfo.value.status_code == 408


def test_get_messages_paginates_and_returns_typed_messages() -> None:
    fake = FakeDevinAPI()
    sid = fake.script_session(
        messages=[
            {"event_id": "e1", "source": "user", "message": "hi", "created_at": 1},
            {"event_id": "e2", "source": "devin", "message": "pong", "created_at": 2},
        ],
    )
    import asyncio

    async def _go() -> list[DevinMessage]:
        async with _build_client(fake) as client:
            return await client.get_messages(sid)

    messages = asyncio.run(_go())
    assert [m.source for m in messages] == ["user", "devin"]
    assert messages[1].message == "pong"


# ---------------------------------------------------------------------------
# extract_changed_files tiered fallback.
# ---------------------------------------------------------------------------


def _detail_with(
    structured_output: Any = None, prs: list[dict[str, Any]] | None = None
) -> DevinSessionDetail:
    return DevinSessionDetail(
        session_id="x",
        status="running",
        status_detail="waiting_for_user",
        acus_consumed=1.0,
        pull_requests=[DevinPullRequest.from_payload(pr) for pr in (prs or [])],
        structured_output=structured_output,
        tags=[],
        title=None,
        url=None,
        created_at=None,
        updated_at=None,
        raw={},
    )


def test_extract_uses_structured_output_when_present() -> None:
    detail = _detail_with(
        structured_output={
            "changed_files": ["pom.xml", "src/foo.java"],
            "pr_url": "https://github.com/x/y/pull/9",
            "branch": "feature/x",
            "summary": "did the thing",
        }
    )
    extraction = extract_changed_files(detail, [])
    assert extraction.source == "structured_output"
    assert extraction.files == ["pom.xml", "src/foo.java"]
    assert extraction.pr_url == "https://github.com/x/y/pull/9"
    assert extraction.branch == "feature/x"
    assert extraction.summary == "did the thing"


def test_extract_falls_back_to_fenced_json_in_messages() -> None:
    detail = _detail_with(structured_output=None)
    devin_msg = DevinMessage(
        event_id="e",
        source="devin",
        message=(
            "Here is what I changed:\n\n"
            "```json\n"
            '{"changed_files": ["pom.xml", "src/main/java/A.java"], '
            '"pr_url": "https://github.com/x/y/pull/2", "branch": "b", "summary": "s"}\n'
            "```\n"
        ),
        created_at=1,
    )
    extraction = extract_changed_files(detail, [devin_msg])
    assert extraction.source == "fenced_json_message"
    assert "pom.xml" in extraction.files
    assert extraction.pr_url == "https://github.com/x/y/pull/2"


def test_extract_falls_back_to_inline_path_scan() -> None:
    detail = _detail_with()
    msg = DevinMessage(
        event_id="e",
        source="devin",
        message="I edited pom.xml and src/main/java/com/Foo.java to fix the bug.",
        created_at=1,
    )
    extraction = extract_changed_files(detail, [msg])
    assert extraction.source == "inline_path_scan"
    assert "pom.xml" in extraction.files
    assert "src/main/java/com/Foo.java" in extraction.files


def test_extract_returns_empty_when_no_signal() -> None:
    detail = _detail_with()
    extraction = extract_changed_files(detail, [])
    assert extraction.source == "empty"
    assert extraction.files == []


def test_extract_skips_user_messages_in_path_scan() -> None:
    detail = _detail_with()
    user_msg = DevinMessage(
        source="user", event_id="u", message="please edit pom.xml", created_at=1
    )
    extraction = extract_changed_files(detail, [user_msg])
    assert extraction.files == []
    assert extraction.source == "empty"


# ---------------------------------------------------------------------------
# Prompt builders.
# ---------------------------------------------------------------------------


def test_naive_prompt_omits_allowed_paths_and_dependency_context() -> None:
    lock = _build_lock(serialized=False)
    text = build_naive_prompt(
        lock.tasks[0],
        repo_url="https://github.com/x/y.git",
        base_branch="master",
    )
    assert "ACG-naive" in text
    assert "Allowed" not in text and "allowed_paths" not in text
    assert "Write boundary" not in text
    assert "https://github.com/x/y.git" in text


def test_planned_prompt_embeds_allowed_paths_and_conflicts() -> None:
    lock = _build_lock(serialized=True)
    text = build_planned_prompt(
        lock.tasks[1],  # invite — has a pom.xml conflict with account
        repo_url="https://github.com/x/y.git",
        base_branch="master",
        lock=lock,
    )
    assert "ACG-planned" in text
    assert "Write boundary" in text
    # allowed_paths injected — task touches invite repo file + pom.xml
    assert "JdbcInviteRepository.java" in text
    assert "pom.xml" in text
    # Conflict block surfaced
    assert "lambda-rowmapper-account" in text


# ---------------------------------------------------------------------------
# devin_api_run end-to-end with mock transport.
# ---------------------------------------------------------------------------


def _script_three_happy_sessions(fake: FakeDevinAPI, *, with_oob: bool = False) -> dict[str, str]:
    """Pre-seed three sessions matching each Greenhouse task's prompt.

    Returns ``{task_id: session_id}`` so tests can assert on correlation.
    """
    accounts = [
        ("lambda-rowmapper-account", "JdbcAccountRepository.java", "account"),
        ("lambda-rowmapper-invite", "JdbcInviteRepository.java", "invite"),
        ("lambda-rowmapper-app", "JdbcAppRepository.java", "develop"),
    ]
    out: dict[str, str] = {}
    for task_id, file_marker, slug in accounts:
        files = [
            f"src/main/java/com/springsource/greenhouse/{slug}/{file_marker}",
            "pom.xml",
        ]
        if with_oob and task_id == "lambda-rowmapper-account":
            # Inject one out-of-bounds file so we can assert oob accounting.
            files.append("src/main/java/com/springsource/greenhouse/secrets/SecretLoader.java")
        sid = fake.script_session(
            prompt_substr=task_id,
            pull_requests=[
                {
                    "url": f"https://github.com/SSKYAJI/greenhouse/pull/{len(out) + 1}",
                    "branch": f"acg/{task_id}",
                    "title": f"[ACG] {task_id}",
                }
            ],
            structured_output={
                "changed_files": files,
                "pr_url": f"https://github.com/SSKYAJI/greenhouse/pull/{len(out) + 1}",
                "branch": f"acg/{task_id}",
                "summary": f"applied {task_id}",
            },
            messages=[
                {
                    "event_id": f"e-{task_id}",
                    "source": "devin",
                    "message": f"Done with {task_id}",
                    "created_at": 1700000000,
                }
            ],
            acus_consumed=12.5,
        )
        out[task_id] = sid
    return out


def test_devin_api_run_naive_parallel_with_mock_transport() -> None:
    fake = FakeDevinAPI()
    sids = _script_three_happy_sessions(fake)
    lock = _build_lock(serialized=False)
    client = _build_client(fake)
    run = devin_adapter.devin_api_run(
        strategy="naive_parallel",
        lock=lock,
        lockfile_path="agent_lock.json",
        repo_url="https://github.com/SSKYAJI/greenhouse.git",
        base_branch="master",
        poll_interval_s=0.0,
        max_wait_s=5.0,
        max_parallelism=5,
        client=client,
    )
    assert run.backend == "devin-api"
    assert run.strategy == "naive_parallel"
    assert run.summary_metrics.tasks_total == 3
    assert run.summary_metrics.tasks_completed == 3
    assert run.summary_metrics.acus_consumed_total == pytest.approx(37.5, rel=1e-6)
    assert run.summary_metrics.overlapping_write_pairs == 3  # all three touch pom.xml

    # Each task got the expected mock session id and PR url, and tags
    # were correctly set at create time.
    by_id = {t.task_id: t for t in run.tasks}
    for task_id, sid in sids.items():
        assert by_id[task_id].session_id == sid
        assert by_id[task_id].artifacts.pr_url is not None
        assert by_id[task_id].metrics.acus_consumed == pytest.approx(12.5)
        assert "pom.xml" in by_id[task_id].actual_changed_files
    # Naive prompts must NOT include the ACG write-boundary block.
    for task_id in sids:
        assert "Write boundary" not in by_id[task_id].prompt
    # Every create call carried the strategy/task_id/run_id tags so we can
    # correlate sessions in Devin's UI later.
    seen_strategies = {
        next((t for t in call["body"]["tags"] if t.startswith("strategy=")), None)
        for call in fake.create_calls
    }
    assert seen_strategies == {"strategy=naive_parallel"}


def test_devin_api_run_acg_planned_serializes_per_execution_plan() -> None:
    """Planned strategy submits sessions one group at a time — verifiable by
    observing that no two POST /sessions calls overlap when the schedule is
    fully serialized.
    """
    fake = FakeDevinAPI()
    _script_three_happy_sessions(fake)
    lock = _build_lock(serialized=True)  # 3 serial groups, one task each
    client = _build_client(fake)
    run = devin_adapter.devin_api_run(
        strategy="acg_planned",
        lock=lock,
        lockfile_path="agent_lock.json",
        repo_url="https://github.com/SSKYAJI/greenhouse.git",
        base_branch="master",
        poll_interval_s=0.0,
        max_wait_s=5.0,
        max_parallelism=5,
        client=client,
    )
    assert run.summary_metrics.tasks_total == 3
    assert run.summary_metrics.tasks_completed == 3
    assert run.summary_metrics.acus_consumed_total == pytest.approx(37.5, rel=1e-6)
    # Planned prompts MUST include the boundary block.
    for task in run.tasks:
        assert "Write boundary" in task.prompt
    # Tags include strategy=acg_planned and task_id correlation.
    strategy_tags = [
        next((t for t in call["body"]["tags"] if t.startswith("strategy=")), None)
        for call in fake.create_calls
    ]
    assert all(tag == "strategy=acg_planned" for tag in strategy_tags)
    # Order check: account (group 1) submitted before invite (group 2)
    # before app (group 3). Look at the bodies in submission order.
    submitted_order = [
        next((t for t in call["body"]["tags"] if t.startswith("task_id=")), "")
        for call in fake.create_calls
    ]
    assert submitted_order == [
        "task_id=lambda-rowmapper-account",
        "task_id=lambda-rowmapper-invite",
        "task_id=lambda-rowmapper-app",
    ]


def test_devin_api_run_flags_out_of_bounds_writes_as_completed_unsafe() -> None:
    fake = FakeDevinAPI()
    _script_three_happy_sessions(fake, with_oob=True)
    lock = _build_lock(serialized=False)
    client = _build_client(fake)
    run = devin_adapter.devin_api_run(
        strategy="acg_planned",
        lock=lock,
        lockfile_path="agent_lock.json",
        repo_url="https://github.com/SSKYAJI/greenhouse.git",
        base_branch="master",
        poll_interval_s=0.0,
        max_wait_s=5.0,
        client=client,
    )
    by_id = {t.task_id: t for t in run.tasks}
    # The account task wrote outside its boundary; status should flip.
    assert by_id["lambda-rowmapper-account"].status == "completed_unsafe"
    assert "secrets/SecretLoader.java" in " ".join(
        by_id["lambda-rowmapper-account"].out_of_bounds_files
    )
    # Conservative scoring: completed_unsafe does NOT count toward
    # tasks_completed.
    assert run.summary_metrics.tasks_completed == 2
    assert run.summary_metrics.out_of_bounds_write_count == 1
    # In planned mode, blocked_write_events captures the violation.
    assert by_id["lambda-rowmapper-account"].blocked_write_events
