"""Unit tests for the ACG DAG solver."""

from __future__ import annotations

import pytest

from acg.schema import PredictedWrite, Task
from acg.solver import build_dag, detect_conflicts, topological_groups


def _task(id_: str, paths: list[str], depends_on: list[str] | None = None) -> Task:
    return Task(
        id=id_,
        prompt="",
        predicted_writes=[PredictedWrite(path=p, confidence=0.9, reason="") for p in paths],
        allowed_paths=paths,
        depends_on=depends_on or [],
    )


def test_two_disjoint_tasks_form_one_parallel_group() -> None:
    tasks = [
        _task("readme", ["README.md", "docs/quickstart.md"]),
        _task("deps", ["package.json", "package-lock.json"]),
    ]
    dag = build_dag(tasks)
    groups = topological_groups(dag)
    assert len(groups) == 1
    assert groups[0].type == "parallel"
    assert sorted(groups[0].tasks) == ["deps", "readme"]
    assert groups[0].waits_for == []
    assert detect_conflicts(tasks) == []


def test_demo_dag_produces_three_groups_in_known_order() -> None:
    tasks = [
        _task(
            "oauth",
            [
                "lib/auth.ts",
                "prisma/schema.prisma",
                "app/api/auth/[...nextauth]/route.ts",
            ],
        ),
        _task(
            "billing",
            [
                "app/dashboard/billing/page.tsx",
                "lib/stripe.ts",
                "prisma/schema.prisma",
                "components/sidebar.tsx",
            ],
        ),
        _task(
            "settings",
            ["app/settings/page.tsx", "components/sidebar.tsx"],
        ),
        _task(
            "tests",
            ["tests/e2e/checkout.spec.ts"],
            depends_on=["billing"],
        ),
    ]
    dag = build_dag(tasks)
    groups = topological_groups(dag)
    assert [g.tasks for g in groups] == [
        ["oauth", "settings"],
        ["billing"],
        ["tests"],
    ]
    assert [g.type for g in groups] == ["parallel", "serial", "serial"]
    assert [g.waits_for for g in groups] == [[], [1], [2]]

    conflicts = detect_conflicts(tasks)
    assert len(conflicts) == 2
    pairs = {tuple(c.between_tasks) for c in conflicts}
    assert ("oauth", "billing") in pairs
    assert ("settings", "billing") in pairs


def test_cycle_raises_value_error() -> None:
    a = _task("a", ["shared.ts"], depends_on=["b"])
    b = _task("b", ["shared.ts"], depends_on=["a"])
    with pytest.raises(ValueError):
        build_dag([a, b])


def test_mutual_conflict_clique_collapses_to_serial_chain() -> None:
    """Three tasks all conflicting on different shared files form a cycle under
    the lighter-first heuristic; the solver must collapse the SCC into a
    deterministic input-order chain instead of erroring."""
    # a/b share x.ts, b/c share y.ts, a/c share z.ts.  Each task has 2 conflicts,
    # so the ``(count, idx)`` tie-break otherwise produces a 3-cycle.
    tasks = [
        _task("a", ["x.ts", "z.ts"]),
        _task("b", ["x.ts", "y.ts"]),
        _task("c", ["y.ts", "z.ts"]),
    ]
    dag = build_dag(tasks)
    groups = topological_groups(dag)
    # Three serial groups, one per task, in input-list order.
    assert [g.tasks for g in groups] == [["a"], ["b"], ["c"]]
    assert [g.type for g in groups] == ["parallel", "serial", "serial"]


def test_mutual_clique_keeps_disjoint_task_in_parallel_group() -> None:
    """A 3-clique alongside a fully disjoint task: clique serializes, the
    disjoint task joins the first parallel level."""
    tasks = [
        _task("a", ["x.ts", "z.ts"]),
        _task("b", ["x.ts", "y.ts"]),
        _task("c", ["y.ts", "z.ts"]),
        _task("solo", ["unrelated.md"]),
    ]
    dag = build_dag(tasks)
    groups = topological_groups(dag)
    # Level 0 holds {a, solo}; b and c follow at deeper levels.
    assert set(groups[0].tasks) == {"a", "solo"}
    assert groups[0].type == "parallel"
    assert groups[1].tasks == ["b"]
    assert groups[2].tasks == ["c"]


def test_unknown_dependency_raises_value_error() -> None:
    with pytest.raises(ValueError):
        build_dag([_task("a", ["x.ts"], depends_on=["ghost"])])


def test_empty_task_list_yields_empty_groups() -> None:
    dag = build_dag([])
    assert topological_groups(dag) == []
    assert detect_conflicts([]) == []


def test_resolution_describes_predecessor_then_successor() -> None:
    # Tie on conflict count (each touches only ``shared.ts``); input order wins.
    tied = [
        _task("alpha", ["shared.ts"]),
        _task("beta", ["shared.ts"]),
    ]
    conflicts = detect_conflicts(tied)
    assert conflicts[0].between_tasks == ["alpha", "beta"]
    assert "Serialize beta after alpha" in conflicts[0].resolution

    # When one task has more total conflicts, the lighter task runs first.
    heavy_first = [
        _task("heavy", ["a.ts", "b.ts", "c.ts"]),
        _task("light_a", ["a.ts"]),
        _task("light_b", ["b.ts"]),
    ]
    conflicts = detect_conflicts(heavy_first)
    pairs = {tuple(c.between_tasks) for c in conflicts}
    assert ("light_a", "heavy") in pairs
    assert ("light_b", "heavy") in pairs
