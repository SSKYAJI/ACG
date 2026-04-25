from __future__ import annotations

from pathlib import Path

from acg.index.bm25 import BM25Indexer
from acg.schema import TaskInput, TaskInputHints


def touch(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def predict(root: Path, prompt: str, graph: dict | None = None):
    return BM25Indexer().predict(TaskInput(id="task", prompt=prompt), root, graph or {})


def test_identifier_matching_from_source_exports(tmp_path: Path) -> None:
    touch(tmp_path, "lib/auth.ts", "export function getCurrentUser() { return null }\n")
    touch(tmp_path, "lib/billing.ts", "export function calculateInvoiceTotal() { return 0 }\n")

    assert predict(tmp_path, "Refactor getCurrentUser auth helper")[0].path == "lib/auth.ts"


def test_path_tokens_match_greenfield_area(tmp_path: Path) -> None:
    touch(tmp_path, "src/app/settings/page.tsx", "export default function Page() { return null }\n")
    touch(tmp_path, "src/app/dashboard/page.tsx", "export default function Dashboard() { return null }\n")

    assert predict(tmp_path, "Redesign settings page")[0].path == "src/app/settings/page.tsx"


def test_imports_are_indexed(tmp_path: Path) -> None:
    touch(tmp_path, "src/server/db.ts", "import { PrismaClient } from '@prisma/client'; export const db = new PrismaClient();\n")
    touch(tmp_path, "src/components/card.tsx", "export function Card() { return null }\n")

    assert predict(tmp_path, "Update Prisma database client")[0].path == "src/server/db.ts"


def test_graph_docstring_and_tie_breaking(tmp_path: Path) -> None:
    graph = {
        "files": [
            {"path": "b/billing.ts", "exports": ["BillingService"], "imports": [], "symbols": []},
            {"path": "a/billing.ts", "exports": ["BillingService"], "imports": [], "symbols": []},
        ]
    }

    writes = predict(tmp_path, "billing service", graph)
    assert [write.path for write in writes[:2]] == ["a/billing.ts", "b/billing.ts"]


def test_hints_participate_in_query(tmp_path: Path) -> None:
    graph = {"files": [{"path": "src/components/Sidebar.tsx", "exports": ["Sidebar"], "imports": []}]}
    task = TaskInput(id="task", prompt="Add a menu entry", hints=TaskInputHints(touches=["navigation"]))

    writes = BM25Indexer().predict(task, tmp_path, graph)
    assert writes and writes[0].path == "src/components/Sidebar.tsx"
