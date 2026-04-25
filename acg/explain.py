"""Terminal-friendly visualisations of an :class:`AgentLock`.

The output of :func:`render_dag` is intentionally ASCII-only so it copies
cleanly into the demo video, the Devpost description, and the README.
"""

from __future__ import annotations

from .schema import AgentLock, Group


def render_summary(lock: AgentLock) -> str:
    """Bullet-list summary of execution plan, conflicts, and key tasks."""
    lines: list[str] = ["Execution plan:"]
    for grp in lock.execution_plan.groups:
        wait = (
            f", waits for {', '.join(str(w) for w in grp.waits_for)}"
            if grp.waits_for
            else ""
        )
        lines.append(
            f"  Group {grp.id} ({grp.type}{wait}): {', '.join(grp.tasks)}"
        )

    if lock.conflicts_detected:
        lines.append("")
        lines.append("Conflicts detected:")
        for conflict in lock.conflicts_detected:
            files = ", ".join(conflict.files)
            between = " ⨯ ".join(conflict.between_tasks)
            lines.append(f"  - {files} overlap: {between} → {conflict.resolution}")
    else:
        lines.append("")
        lines.append("Conflicts detected: none")
    return "\n".join(lines)


def _build_predecessors(lock: AgentLock) -> dict[str, list[str]]:
    preds: dict[str, list[str]] = {t.id: list(t.depends_on) for t in lock.tasks}
    return preds


def _ordered_groups(lock: AgentLock) -> list[Group]:
    return sorted(lock.execution_plan.groups, key=lambda g: g.id)


def render_dag(lock: AgentLock) -> str:
    """Render a compact ASCII DAG of the lockfile.

    The first column lists tasks in the first parallel group, the right side
    chains successive serial groups separated by ``──►`` arrows. Multi-task
    serial groups are rendered as ``[a + b]``.
    """
    groups = _ordered_groups(lock)
    if not groups:
        return "ASCII DAG: (empty)"

    columns: list[str] = []
    for grp in groups:
        if len(grp.tasks) == 1:
            columns.append(grp.tasks[0])
        else:
            columns.append("[" + " + ".join(grp.tasks) + "]")

    lines: list[str] = ["ASCII DAG:"]
    if len(columns) == 1:
        lines.append("  " + columns[0])
        return "\n".join(lines)

    # First column may have multiple tasks (parallel root).
    first_group = groups[0]
    rest = " ───► ".join(columns[1:])
    if len(first_group.tasks) > 1:
        # Multi-line root that fans into a single follow-up.
        first_col = first_group.tasks
        max_width = max(len(name) for name in first_col)
        for idx, name in enumerate(first_col):
            connector = "───┐" if idx == 0 else (
                "───┤" if idx < len(first_col) - 1 else "───┘"
            )
            spacer = " " * (max_width - len(name))
            if idx == len(first_col) // 2:
                lines.append(f"  {name}{spacer} {connector}──► {rest}")
            else:
                lines.append(f"  {name}{spacer} {connector}")
    else:
        lines.append("  " + columns[0] + " ───► " + rest)

    return "\n".join(lines)


def render(lock: AgentLock) -> str:
    """Convenience wrapper combining summary and DAG."""
    return f"{render_summary(lock)}\n\n{render_dag(lock)}"
