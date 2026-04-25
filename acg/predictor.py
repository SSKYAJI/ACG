"""Task → write-set predictor.

For each :class:`~acg.schema.TaskInput`, the predictor combines three static
seed strategies with one LLM re-rank pass to produce a list of
:class:`~acg.schema.PredictedWrite`. The seeds give us a defensible baseline
even when the LLM is offline; the re-rank lets the model add task-implied
files that the seeds cannot infer (e.g. a ``components/sidebar.tsx`` for an
"add a sidebar entry" task).
"""

from __future__ import annotations

import json
import re
from typing import Any

from .llm import LLMProtocol
from .schema import PredictedWrite, TaskInput

# Tunable thresholds (no magic numbers in module bodies).
SEED_FILE_CONFIDENCE = 0.95
SEED_SYMBOL_CONFIDENCE = 0.85
SEED_TOPICAL_CONFIDENCE = 0.7
TOP_GRAPH_FILES_FOR_LLM = 50
MAX_PREDICTIONS = 8

# Regex for explicit file mentions like ``lib/auth.ts`` or ``prisma/schema.prisma``.
_FILE_MENTION_RE = re.compile(
    r"(?<![\w/.-])([\w./-]+\.(?:ts|tsx|js|jsx|mjs|cjs|py|prisma|sql|md|json|yaml|yml|html|css))"
)
# Symbol candidates: camelCase tokens length > 5 (e.g. ``getCurrentUser``).
_SYMBOL_RE = re.compile(r"\b([a-z][a-zA-Z0-9]{5,})\b")


def _static_seed(prompt: str) -> list[PredictedWrite]:
    """Predict files explicitly named in the prompt."""
    seen: dict[str, PredictedWrite] = {}
    for match in _FILE_MENTION_RE.findall(prompt):
        path = match.strip("./")
        if path and path not in seen:
            seen[path] = PredictedWrite(
                path=path,
                confidence=SEED_FILE_CONFIDENCE,
                reason="Path mentioned verbatim in task prompt.",
            )
    return list(seen.values())


def _symbol_seed(prompt: str, repo_graph: dict[str, Any]) -> list[PredictedWrite]:
    """Predict files via symbol → file lookup against the repo graph."""
    index = repo_graph.get("symbols_index") or {}
    if not index:
        return []
    out: dict[str, PredictedWrite] = {}
    for token in _SYMBOL_RE.findall(prompt):
        path = index.get(token)
        if path and path not in out:
            out[path] = PredictedWrite(
                path=path,
                confidence=SEED_SYMBOL_CONFIDENCE,
                reason=f"Symbol {token!r} referenced in prompt; defined in {path}.",
            )
    return list(out.values())


def _topical_seed(
    hints: list[str], repo_graph: dict[str, Any]
) -> list[PredictedWrite]:
    """Match hint keywords against path components in the repo graph."""
    files = repo_graph.get("files") or []
    if not hints or not files:
        return []
    needles = [h.lower() for h in hints if h]
    out: dict[str, PredictedWrite] = {}
    for entry in files:
        path = entry.get("path", "")
        path_lower = path.lower()
        for needle in needles:
            if needle and needle in path_lower and path not in out:
                out[path] = PredictedWrite(
                    path=path,
                    confidence=SEED_TOPICAL_CONFIDENCE,
                    reason=f"Hint {needle!r} matches path component.",
                )
                break
    return list(out.values())


def _filter_graph_for_llm(repo_graph: dict[str, Any]) -> dict[str, Any]:
    """Return a compact graph slice safe to embed in an LLM prompt."""
    files = repo_graph.get("files") or []
    hotspots = set(repo_graph.get("hotspots") or [])
    # Score: hotspots first, then files with rich exports, then everything else.
    scored = sorted(
        files,
        key=lambda f: (
            0 if f.get("path") in hotspots else 1,
            -len(f.get("exports") or []),
            f.get("path", ""),
        ),
    )
    trimmed = []
    for f in scored[:TOP_GRAPH_FILES_FOR_LLM]:
        trimmed.append(
            {
                "path": f.get("path"),
                "exports": (f.get("exports") or [])[:8],
                "imports": (f.get("imports") or [])[:8],
                "is_hotspot": bool(f.get("is_hotspot")),
            }
        )
    return {
        "language": repo_graph.get("language"),
        "hotspots": list(hotspots),
        "files": trimmed,
    }


def _build_prompt(
    task: TaskInput,
    repo_graph: dict[str, Any],
    seeds: list[PredictedWrite],
) -> list[dict[str, str]]:
    system = (
        "You are ACG, a static analyzer that predicts which files an agent task will modify.\n"
        "You are given a task description and a code graph (files, imports, exports, hotspots).\n"
        'Output a JSON object with key "writes" containing a list of {path, confidence, reason}.\n'
        "Confidence is 0.0-1.0. Reason is one short sentence.\n"
        "Be conservative: only include files where the task description clearly implies a modification.\n"
        "Do not include files based on speculation."
    )
    hints_blob = (
        json.dumps(task.hints.model_dump() if task.hints else {}, sort_keys=True)
    )
    user = (
        f"Task id: {task.id}\n"
        f"Task: {task.prompt}\n"
        f"Hints: {hints_blob}\n\n"
        f"Code graph (top {TOP_GRAPH_FILES_FOR_LLM} relevant files):\n"
        f"{json.dumps(_filter_graph_for_llm(repo_graph), sort_keys=True)}\n\n"
        "Existing static-seed predictions (you may keep, demote, or remove these):\n"
        f"{json.dumps([s.model_dump() for s in seeds], sort_keys=True)}\n\n"
        "Output JSON only, no prose."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_llm_writes(raw: str) -> list[PredictedWrite]:
    """Best-effort parse of the LLM reply into PredictedWrite list."""
    raw = raw.strip()
    if not raw:
        return []
    # Strip code fences if the model added them.
    if raw.startswith("```"):
        raw = raw.strip("`")
        # remove leading ``json`` language tag
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Try to locate the first ``{`` ... ``}`` substring.
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return []
        try:
            payload = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return []
    items = payload.get("writes") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        return []
    out: list[PredictedWrite] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            out.append(PredictedWrite(**item))
        except (TypeError, ValueError):
            continue
    return out


def _merge(
    seeds: list[PredictedWrite], rerank: list[PredictedWrite]
) -> list[PredictedWrite]:
    """Merge seed and re-ranked predictions.

    For paths present in both, the LLM's confidence wins (it can demote or
    boost the seed) but the seed's reason is preserved when the LLM omits one.
    """
    merged: dict[str, PredictedWrite] = {p.path: p for p in seeds}
    for pw in rerank:
        existing = merged.get(pw.path)
        if existing is None:
            merged[pw.path] = pw
            continue
        new_reason = pw.reason or existing.reason
        merged[pw.path] = PredictedWrite(
            path=pw.path, confidence=pw.confidence, reason=new_reason
        )
    return sorted(merged.values(), key=lambda p: (-p.confidence, p.path))


def predict_writes(
    task: TaskInput,
    repo_graph: dict[str, Any],
    llm: LLMProtocol,
) -> list[PredictedWrite]:
    """Predict the file write-set for a single task.

    Args:
        task: Input task as supplied via ``tasks.json``.
        repo_graph: Output of :mod:`graph_builder.scan` (TS) or an empty dict.
        llm: LLM client implementing :class:`~acg.llm.LLMProtocol`.

    Returns:
        Up to :data:`MAX_PREDICTIONS` :class:`PredictedWrite` items, sorted by
        descending confidence.
    """
    seeds = _static_seed(task.prompt)
    seeds += _symbol_seed(task.prompt, repo_graph)
    if task.hints and task.hints.touches:
        seeds += _topical_seed(list(task.hints.touches), repo_graph)
    # Deduplicate seeds, keeping the highest-confidence variant per path.
    by_path: dict[str, PredictedWrite] = {}
    for pw in seeds:
        cur = by_path.get(pw.path)
        if cur is None or pw.confidence > cur.confidence:
            by_path[pw.path] = pw
    seeds = list(by_path.values())

    rerank: list[PredictedWrite] = []
    try:
        reply = llm.complete(_build_prompt(task, repo_graph, seeds))
        rerank = _parse_llm_writes(reply)
    except Exception:
        # Failing closed: keep seeds. Logging is the CLI layer's responsibility.
        rerank = []

    merged = _merge(seeds, rerank)
    return merged[:MAX_PREDICTIONS]
