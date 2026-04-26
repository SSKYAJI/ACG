"""Prompt templates for Devin sessions in the head-to-head harness.

The whole point of the eval is to compare two orchestrations against the
**same agent**:

- ``build_naive_prompt`` — tasks submitted with no contract, no
  dependency context, no write boundary. Sessions race in parallel.
- ``build_planned_prompt`` — tasks submitted per ``execution_plan``
  group, each with the lockfile's ``allowed_paths`` injected as a soft
  constraint and any merged-prior dependency context.

Both prompt builders close with a strict instruction asking Devin to
emit a JSON object matching :data:`devin_api.CHANGED_FILES_SCHEMA` so
``extract_changed_files`` can recover the changed-file list even if the
session does not open a PR.
"""

from __future__ import annotations

from acg.schema import AgentLock, Task

# Common closing block — appended to every prompt so Devin's final
# message is a parseable JSON envelope.
_STRUCTURED_OUTPUT_INSTRUCTION = """\

When you finish, OPEN A PULL REQUEST against the base branch and then \
reply with a single JSON object (no surrounding prose) matching this shape:

```json
{
  "changed_files": ["repo/relative/path1", "repo/relative/path2"],
  "pr_url": "https://github.com/<org>/<repo>/pull/<n>",
  "branch": "<branch-name-you-pushed>",
  "summary": "one-paragraph human summary"
}
```

`changed_files` must list every path you modified, created, or deleted.
"""


def build_naive_prompt(
    task: Task,
    *,
    repo_url: str,
    base_branch: str,
) -> str:
    """Build the bare-prompt variant for the ``naive_parallel`` strategy.

    No write boundary, no dependency context. Mirrors what a developer
    would do if they handed three Devin sessions independent task
    descriptions and let them race.
    """
    return (
        f"You are modifying the {repo_url} repository (base branch: {base_branch}). "
        f"Clone it, create a working branch off `{base_branch}`, and complete the task below. "
        f"Push your branch and open a PR titled `[ACG-naive] {task.id}` against `{base_branch}`.\n\n"
        f"## Task `{task.id}`\n{task.prompt}\n" + _STRUCTURED_OUTPUT_INSTRUCTION
    )


def build_planned_prompt(
    task: Task,
    *,
    repo_url: str,
    base_branch: str,
    lock: AgentLock,
) -> str:
    """Build the ACG-contract variant for the ``acg_planned`` strategy.

    Injects the lockfile's ``allowed_paths`` as a hard-sounding
    constraint and surfaces ``depends_on`` so Devin understands that
    prior tasks have already merged.
    """
    allowed_block = "\n".join(f"  - `{glob}`" for glob in task.allowed_paths) or "  - (none)"
    deps = ", ".join(f"`{d}`" for d in task.depends_on) if task.depends_on else None
    deps_note = (
        f"Prior tasks already merged into `{base_branch}`: {deps}. "
        f"Rebase if you encounter merge conflicts.\n\n"
        if deps
        else ""
    )

    # Surface conflict context the planner identified — so Devin knows
    # *why* its boundary is narrow.
    conflict_lines: list[str] = []
    for conflict in lock.conflicts_detected:
        if task.id in conflict.between_tasks and conflict.files:
            others = [t for t in conflict.between_tasks if t != task.id]
            files = ", ".join(f"`{f}`" for f in conflict.files)
            conflict_lines.append(
                f"  - {files} also touched by {', '.join(f'`{o}`' for o in others)}: "
                f"{conflict.resolution or 'serialized via execution plan'}"
            )
    conflict_block = ""
    if conflict_lines:
        conflict_block = (
            "## Known cross-task conflicts (already resolved by the schedule)\n"
            + "\n".join(conflict_lines)
            + "\n\n"
        )

    return (
        f"You are modifying the {repo_url} repository (base branch: {base_branch}). "
        f"Clone it, create a working branch off `{base_branch}`, and complete the task below. "
        f"Push your branch and open a PR titled `[ACG-planned] {task.id}` against `{base_branch}`.\n\n"
        f"## Task `{task.id}`\n{task.prompt}\n\n"
        f"{deps_note}"
        f"## Write boundary (ACG contract)\n"
        f"You may modify ONLY files matching these glob patterns:\n"
        f"{allowed_block}\n\n"
        f"If you believe a file outside this boundary must change, STOP and explain in your "
        f"reply rather than editing it. The boundary was computed by the Agent Context Graph "
        f"compiler to prevent collisions with other tasks running in this batch.\n\n"
        f"{conflict_block}" + _STRUCTURED_OUTPUT_INSTRUCTION
    )
