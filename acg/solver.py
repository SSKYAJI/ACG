"""DAG solver for ACG.

Given a list of :class:`~acg.schema.Task` objects with predicted write-sets,
the solver:

1. Detects conflicts (pairs of tasks whose predicted writes overlap).
2. Builds a directed acyclic graph encoding a safe execution order.
3. Topologically groups nodes into parallel-safe and serial groups.

The solver is a pure function: no IO, no LLM, no global state. It is the
component judges should be most confident about; everything here is unit
tested.

Edge orientation rule (deviation from earlier draft notes): when two tasks
have overlapping predicted writes, the predecessor is the task with **fewer
total conflicts**, with input-list index as the tie-break. The intuition is
that the more-coupled task should serialize against its lighter neighbours.
This rule reproduces the canonical demo lockfile in
``examples/lockfile.dag.example.json``.
"""

from __future__ import annotations

from collections import defaultdict
from itertools import combinations

import networkx as nx

from .schema import Conflict, Group, Task


def _path_set(task: Task) -> set[str]:
    """Return the set of paths a task plans to write."""
    return {pw.path for pw in task.predicted_writes}


def detect_conflicts(tasks: list[Task]) -> list[Conflict]:
    """Find every pair of tasks whose predicted writes share at least one path.

    The resolution narrative reflects the same predecessor rule
    :func:`build_dag` applies (lighter-conflict task first; input index as
    tie-break), so the conflict list and the execution plan stay consistent.

    Args:
        tasks: Tasks with populated ``predicted_writes``.

    Returns:
        One :class:`Conflict` per overlapping task pair, listing
        ``[predecessor, successor]`` in ``between_tasks``.
    """
    pair_idx = _conflict_pairs_by_index(tasks)
    counts: dict[int, int] = defaultdict(int)
    for i, j in pair_idx:
        counts[i] += 1
        counts[j] += 1

    conflicts: list[Conflict] = []
    for i, j in pair_idx:
        a, b = tasks[i], tasks[j]
        overlap = sorted(_path_set(a) & _path_set(b))
        if (counts[i], i) <= (counts[j], j):
            predecessor, successor = a.id, b.id
        else:
            predecessor, successor = b.id, a.id
        conflicts.append(
            Conflict(
                files=overlap,
                between_tasks=[predecessor, successor],
                resolution=(
                    f"Serialize {successor} after {predecessor}; both modify "
                    + ", ".join(overlap)
                ),
            )
        )
    return conflicts


def _conflict_pairs_by_index(tasks: list[Task]) -> list[tuple[int, int]]:
    """Index-based pair list of overlapping tasks."""
    out: list[tuple[int, int]] = []
    for i, j in combinations(range(len(tasks)), 2):
        if _path_set(tasks[i]) & _path_set(tasks[j]):
            out.append((i, j))
    return out


def build_dag(
    tasks: list[Task],
    heuristic_deps: dict[str, list[str]] | None = None,
) -> nx.DiGraph:
    """Build the directed dependency graph.

    Edges are added in three layers, ordered by *defeasibility*:

    1. **Conflict-derived** — lighter task (fewer conflicts) runs first;
       tie-break by input-list index. Defeasible by the SCC pass below.
    2. **Heuristic** — caller-supplied ``heuristic_deps`` (e.g. the
       compiler's "tests run last" rule). Also defeasible.
    3. **Explicit user-declared** — each ``task.depends_on`` entry. *Not*
       defeasible: a cycle here always raises ``ValueError``.

    Between steps 2 and 3 a strongly-connected-component pass collapses any
    cycle formed by defeasible edges into a deterministic input-order chain.
    This lets the index-aggregator surface real overlaps (test tasks
    legitimately touch shared utilities) without erroring.

    Args:
        tasks: Tasks with populated ``predicted_writes`` and optional
            ``depends_on`` (treated as user-explicit, non-defeasible).
        heuristic_deps: Optional ``{task_id: [predecessor_ids]}`` map of
            defeasible heuristic edges.

    Returns:
        A :class:`networkx.DiGraph` whose nodes are task ids and whose edges
        encode predecessor → successor relationships.

    Raises:
        ValueError: if user-declared ``depends_on`` chains form a cycle, or
            reference an unknown task id.
    """
    graph: nx.DiGraph = nx.DiGraph()
    for task in tasks:
        graph.add_node(task.id)

    known_ids = {task.id for task in tasks}

    pair_idx = _conflict_pairs_by_index(tasks)
    conflict_count: dict[int, int] = defaultdict(int)
    for i, j in pair_idx:
        conflict_count[i] += 1
        conflict_count[j] += 1

    # 1) Conflict-derived edges (defeasible).
    for i, j in pair_idx:
        key_i = (conflict_count[i], i)
        key_j = (conflict_count[j], j)
        if key_i <= key_j:
            graph.add_edge(tasks[i].id, tasks[j].id)
        else:
            graph.add_edge(tasks[j].id, tasks[i].id)

    # 2) Heuristic edges supplied by the caller (defeasible).
    if heuristic_deps:
        for tid, preds in heuristic_deps.items():
            if tid not in known_ids:
                continue
            for dep in preds:
                if dep in known_ids and dep != tid:
                    graph.add_edge(dep, tid)

    # SCC collapse: defeat any cycles formed by the two defeasible layers
    # above by replacing each non-trivial SCC's internal edges with a strict
    # input-order chain.
    if not nx.is_directed_acyclic_graph(graph):
        id_to_idx = {task.id: idx for idx, task in enumerate(tasks)}
        for component in nx.strongly_connected_components(graph):
            if len(component) <= 1:
                continue
            ordered = sorted(component, key=lambda tid: id_to_idx[tid])
            for u in component:
                for v in component:
                    if u != v and graph.has_edge(u, v):
                        graph.remove_edge(u, v)
            for u, v in zip(ordered, ordered[1:], strict=False):
                graph.add_edge(u, v)

    # 3) Explicit user-declared dependencies (non-defeasible).
    for task in tasks:
        for dep in task.depends_on:
            if dep not in known_ids:
                raise ValueError(
                    f"task {task.id!r} depends_on unknown task id {dep!r}"
                )
            graph.add_edge(dep, task.id)

    if not nx.is_directed_acyclic_graph(graph):
        cycle = nx.find_cycle(graph, orientation="original")
        raise ValueError(f"cycle detected: {cycle}")

    return graph


def topological_groups(dag: nx.DiGraph) -> list[Group]:
    """Partition the DAG into ordered execution groups.

    A node's level is the length of the longest path from any source to that
    node. Nodes sharing a level form a single group. Multi-node groups are
    ``parallel``; single-node groups beyond the first are ``serial``. Each
    group declares ``waits_for`` as the immediately preceding group ids
    derivable from its predecessors.

    Args:
        dag: Acyclic dependency graph.

    Returns:
        List of :class:`Group` objects in topological order. Empty when the
        graph has no nodes.
    """
    if dag.number_of_nodes() == 0:
        return []

    levels: dict[str, int] = {}
    for node in nx.topological_sort(dag):
        preds = list(dag.predecessors(node))
        levels[node] = 0 if not preds else max(levels[p] for p in preds) + 1

    by_level: dict[int, list[str]] = defaultdict(list)
    for node, lvl in levels.items():
        by_level[lvl].append(node)

    # Map level -> group id (group ids are 1-indexed and dense).
    sorted_levels = sorted(by_level.keys())
    level_to_group: dict[int, int] = {lvl: idx + 1 for idx, lvl in enumerate(sorted_levels)}

    groups: list[Group] = []
    for lvl in sorted_levels:
        nodes = sorted(by_level[lvl])
        gid = level_to_group[lvl]
        if len(nodes) > 1:
            grp_type = "parallel"
        else:
            grp_type = "parallel" if lvl == 0 else "serial"
        # ``waits_for`` is the immediate predecessor group only. Earlier groups
        # are transitively waited for via the group chain, so listing them here
        # would be redundant and clutter the lockfile.
        waits_for: list[int] = [gid - 1] if gid > 1 else []
        groups.append(
            Group(id=gid, tasks=nodes, type=grp_type, waits_for=waits_for)
        )

    return groups
