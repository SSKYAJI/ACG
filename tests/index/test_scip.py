from __future__ import annotations

from acg.index.aggregate import aggregate
from acg.index.scip import DEFINITION_CONFIDENCE_CAP, REFERENCE_CONFIDENCE_CAP, ScipIndexer
from acg.schema import TaskInput


def test_scip_indexer_maps_starlette_templates_autoescape_to_templating() -> None:
    graph = {
        "scip_entities": [
            {
                "symbol": "python starlette/templating.py Jinja2Templates#",
                "name": "Jinja2Templates",
                "path": "starlette/templating.py",
                "signature": "Jinja2Templates Environment select_autoescape autoescape",
                "references": ["tests/test_templates.py"],
            },
            {
                "symbol": "python starlette/responses.py Response#",
                "name": "Response",
                "path": "starlette/responses.py",
            },
        ]
    }

    writes = ScipIndexer().predict(
        TaskInput(id="templates", prompt="Enable Jinja2Templates autoescape."),
        None,
        graph,
    )
    by_path = {write.path: write for write in writes}

    assert "starlette/templating.py" in by_path
    assert by_path["starlette/templating.py"].confidence <= DEFINITION_CONFIDENCE_CAP
    assert "SCIP entity" in by_path["starlette/templating.py"].reason
    assert "Jinja2Templates" in by_path["starlette/templating.py"].reason


def test_scip_indexer_maps_fastify_content_type_request_without_hub_promotion() -> None:
    graph = {
        "scip_entities": [
            {
                "symbol": "javascript lib/content-type-parser.js ContentTypeParser#",
                "name": "ContentTypeParser",
                "path": "lib/content-type-parser.js",
                "signature": "content type parser request",
            },
            {
                "symbol": "typescript types/request.d.ts FastifyRequest#",
                "name": "FastifyRequest",
                "path": "types/request.d.ts",
                "signature": "request content type",
                "references": ["lib/request.js"],
            },
            {
                "symbol": "javascript lib/symbols.js kRequestCacheValidateFns#",
                "name": "kRequestCacheValidateFns",
                "path": "lib/symbols.js",
                "signature": "request cache",
            },
        ]
    }

    writes = ScipIndexer().predict(
        TaskInput(id="request", prompt="Update Fastify request content-type handling."),
        None,
        graph,
    )
    by_path = {write.path: write for write in writes}

    assert "lib/content-type-parser.js" in by_path
    assert "types/request.d.ts" in by_path
    assert by_path["types/request.d.ts"].confidence <= DEFINITION_CONFIDENCE_CAP
    assert by_path["lib/request.js"].confidence <= REFERENCE_CONFIDENCE_CAP
    assert all(
        "SCIP entity" in write.reason or "SCIP reference" in write.reason for write in writes
    )


def test_aggregate_adds_scip_default_only_when_enabled_or_metadata(
    monkeypatch,
) -> None:
    task = TaskInput(id="templates", prompt="Enable Jinja2Templates autoescape.")
    graph = {
        "scip_entities": [
            {
                "symbol": "python starlette/templating.py Jinja2Templates#",
                "name": "Jinja2Templates",
                "path": "starlette/templating.py",
            }
        ]
    }

    monkeypatch.delenv("ACG_INDEX_SCIP", raising=False)
    writes = aggregate(task, None, graph, top_n=8)
    assert any(write.path == "starlette/templating.py" for write in writes)

    monkeypatch.setenv("ACG_INDEX_SCIP", "0")
    writes = aggregate(task, None, graph, top_n=8)
    assert all("SCIP" not in write.reason for write in writes)
