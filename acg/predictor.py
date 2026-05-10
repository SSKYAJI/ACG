"""Task → write-set predictor.

For each :class:`~acg.schema.TaskInput`, the predictor fuses **seven
deterministic seed strategies** with one LLM re-rank pass to produce a
list of :class:`~acg.schema.PredictedWrite`.

The seed layer:

1. ``_static_seed`` — verbatim file mentions in the prompt
2. ``_symbol_seed`` — camelCase tokens resolved via the repo graph
3. ``_topical_seed`` — ``hints.touches`` substring match against paths
4. ``_test_scaffold_seed`` — framework convention + entity extraction
5. ``_env_seed`` — credential/provider triggers → ``.env.*`` files
6. ``_sibling_pattern_seed`` — analogical reasoning over existing API trees
7. ``_index_seed`` — :func:`acg.index.aggregate` (framework / PageRank /
   BM25 / git co-change), available whenever ``repo_root`` is set

The seeds give us a defensible baseline even when the LLM is offline; the
re-rank lets the model add task-implied files that the seeds cannot infer
(e.g. a ``components/sidebar.tsx`` for an "add a sidebar entry" task).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .llm import LLMProtocol
from .schema import PredictedWrite, TaskInput

# Tunable thresholds (no magic numbers in module bodies).
SEED_FILE_CONFIDENCE = 0.95
SEED_SYMBOL_CONFIDENCE = 0.85
SEED_TOPICAL_CONFIDENCE = 0.7
SEED_TEST_SCAFFOLD_CONFIDENCE = 0.85
SEED_ENV_CONFIDENCE = 0.8
SEED_ENV_LOCAL_CONFIDENCE = 0.65
SEED_SIBLING_PATTERN_PRIMARY_CONFIDENCE = 0.75
SEED_SIBLING_PATTERN_SECONDARY_CONFIDENCE = 0.65
SEED_INDEX_CONFIDENCE_FLOOR = 0.5
# Cap how many index-aggregator predictions feed into the seed pipeline per
# task. The aggregator's PageRank component is biased toward repo-wide
# hotspots and will happily propose the same auth/db files for every task,
# which over-serializes the lockfile. Three signals per task keeps the
# strongest hits while leaving room for task-specific seeds to dominate.
SEED_INDEX_TOP_N = 8
SEED_GRAPH_EXPANSION_CONFIDENCE = 0.92
SEED_GRAPH_EXPANSION_STRUCTURAL_CONFIDENCE = 0.92
SEED_GRAPH_EXPANSION_MIN_SEED_CONFIDENCE = 0.85
TOP_GRAPH_FILES_FOR_LLM = 50
MAX_PREDICTIONS = 10

# Regex for explicit file mentions like ``lib/auth.ts`` or ``prisma/schema.prisma``.
_FILE_MENTION_RE = re.compile(
    r"(?<![\w/.-])([\w./-]+\.(?:ts|tsx|js|jsx|mjs|cjs|py|prisma|sql|md|json|yaml|yml|html|css))"
)
# Symbol candidates: camelCase tokens length > 5 (e.g. ``getCurrentUser``).
_SYMBOL_RE = re.compile(r"\b([a-z][a-zA-Z0-9]{5,})\b")

# --- Test-scaffold seed ------------------------------------------------------
#
# Greenfield-aware prediction for "write tests" tasks.  Two-stage detection:
#   1. Existing config file in the repo (e.g. ``playwright.config.ts``) wins —
#      we parse its ``testDir`` and use the matching framework extension.
#   2. If no config exists, the prompt's framework keyword (playwright /
#      vitest / jest / pytest / cypress) selects a canonical default layout.
#
# Driven by §7-§8 of the agent file-set prediction survey: the project's
# declared conventions are the highest-precision signal we can lift before
# spending any LLM compute.

_TEST_TASK_KEYWORDS_RE = re.compile(
    r"\b(tests?|testing|specs?|coverage|regression|e2e|playwright|vitest|jest|cypress|pytest)\b",
    re.IGNORECASE,
)
# "tests for [the] checkout" / "specs covering signup" / "tests of [the] api"
_ENTITY_AFTER_TESTS_RE = re.compile(
    r"\btests?\s+(?:for|covering|of)\s+(?:the\s+|a\s+|an\s+)?([a-z][\w-]+)",
    re.IGNORECASE,
)
# "checkout flow" / "auth feature" / "billing endpoint" — domain-noun + role
_ENTITY_BEFORE_ROLE_RE = re.compile(
    r"\b([a-z][\w-]+)\s+(?:flow|feature|page|component|endpoint|api|route|module|service)\b",
    re.IGNORECASE,
)
_ENTITY_CONJUNCTION_RE = re.compile(
    r"\b([a-z][\w-]+)\s+(?:and|or)\s+([a-z][\w-]+)\b",
    re.IGNORECASE,
)
_ENTITY_STOPWORDS = {
    "the", "a", "an", "this", "that", "all", "any", "src", "lib", "test",
    "tests", "testing", "spec", "specs", "playwright", "vitest", "jest",
    "pytest", "cypress", "end", "to", "unit", "integration", "e2e",
}
_ENV_TRIGGER_RE = re.compile(
    r"\b(oauth|stripe|auth0|clerk|nextauth|api[\s-]?key|secret|"
    r"credentials?|provider[s]?|env(?:ironment)?\s+vars?)\b",
    re.IGNORECASE,
)
_SIBLING_TASK_RE = re.compile(
    r"\b(add|create|implement)\b.*\b(api|endpoint|route|webhook|checkout|integration)\b",
    re.IGNORECASE,
)
_ACTION_ENTITY_PATTERNS = (
    re.compile(r"\badd\s+(?:the\s+|a\s+|an\s+)?([a-z][\w-]+)\b", re.IGNORECASE),
    re.compile(r"\bimplement\s+(?:the\s+|a\s+|an\s+)?([a-z][\w-]+)\b", re.IGNORECASE),
    re.compile(r"\bcreate\s+(?:the\s+|a\s+|an\s+)?([a-z][\w-]+)\b", re.IGNORECASE),
)
_RESOURCE_ENTITY_RE = re.compile(
    r"\b([a-z][\w-]+)\s+(?:api|endpoint|route|webhook|checkout|integration)\b",
    re.IGNORECASE,
)
_SIBLING_ENTITY_STOPWORDS = _ENTITY_STOPWORDS | {
    "dashboard",
    "entry",
    "handler",
    "hook",
    "implement",
    "new",
    "tab",
    "update",
    "wire",
}
_ROUTE_FILENAME_RE = re.compile(r"^route\.(?:ts|tsx|js|jsx)$", re.IGNORECASE)

# (config_filename, default_testdir, default_extension)
_FRAMEWORK_DEFAULTS: dict[str, tuple[str | None, str, str]] = {
    "playwright": ("playwright.config.ts", "tests", ".spec.ts"),
    "vitest": ("vitest.config.ts", "tests", ".test.ts"),
    "jest": ("jest.config.js", "__tests__", ".test.ts"),
    "cypress": ("cypress.config.ts", "cypress/e2e", ".cy.ts"),
    "pytest": (None, "tests", ".py"),
}
# Order matters: keywords closer to the start are more specific, so "playwright"
# beats the generic "tests" in a prompt that names both.
_FRAMEWORK_KEYWORD_PRIORITY = ("playwright", "cypress", "vitest", "jest", "pytest")
_TESTDIR_RE = re.compile(r"testDir\s*:\s*['\"]([^'\"]+)['\"]")
# Both forms — "e2e" and "end-to-end" / "end to end" — are common in the wild.
_E2E_RE = re.compile(r"\b(?:e2e|end[-\s]to[-\s]end)\b", re.IGNORECASE)


def _looks_like_test_task(prompt: str) -> bool:
    return bool(_TEST_TASK_KEYWORDS_RE.search(prompt))


def _append_entity(entities: list[str], entity: str, *, stopwords: set[str]) -> None:
    candidate = entity.lower()
    if candidate in stopwords or candidate in entities:
        return
    if "/" in candidate or "." in candidate:
        return
    entities.append(candidate)


def _extract_entity_nouns(prompt: str) -> list[str]:
    """Pull up to four one-word domain nouns out of a test-task prompt.

    >>> _extract_entity_nouns("Write end-to-end Playwright tests for the checkout flow.")
    ['checkout']
    >>> _extract_entity_nouns("Add unit tests for the auth helper.")
    ['auth']
    """
    entities: list[str] = []
    for pattern in (_ENTITY_AFTER_TESTS_RE, _ENTITY_BEFORE_ROLE_RE):
        for match in pattern.finditer(prompt):
            _append_entity(entities, match.group(1), stopwords=_ENTITY_STOPWORDS)
            if len(entities) >= 4:
                return entities
    for match in _ENTITY_CONJUNCTION_RE.finditer(prompt):
        _append_entity(entities, match.group(1), stopwords=_ENTITY_STOPWORDS)
        if len(entities) >= 4:
            return entities
        _append_entity(entities, match.group(2), stopwords=_ENTITY_STOPWORDS)
        if len(entities) >= 4:
            return entities
    return entities


def _extract_entity_noun(prompt: str) -> str | None:
    entities = _extract_entity_nouns(prompt)
    return entities[0] if entities else None


def _extract_sibling_entities(prompt: str) -> list[str]:
    entities: list[str] = []
    for pattern in _ACTION_ENTITY_PATTERNS:
        for match in pattern.finditer(prompt):
            _append_entity(entities, match.group(1), stopwords=_SIBLING_ENTITY_STOPWORDS)
            if len(entities) >= 4:
                return entities
    for match in _RESOURCE_ENTITY_RE.finditer(prompt):
        _append_entity(entities, match.group(1), stopwords=_SIBLING_ENTITY_STOPWORDS)
        if len(entities) >= 4:
            return entities
    entity = _extract_entity_noun(prompt)
    if entity:
        _append_entity(entities, entity, stopwords=_SIBLING_ENTITY_STOPWORDS)
    return entities


def _sibling_pattern_seed(task: TaskInput, repo_graph: dict[str, Any]) -> list[PredictedWrite]:
    if not _SIBLING_TASK_RE.search(task.prompt):
        return []

    entities = _extract_sibling_entities(task.prompt)
    if not entities:
        return []

    files = repo_graph.get("files") or []
    existing_paths = {
        entry.get("path", "")
        for entry in files
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    }
    pattern_members: dict[tuple[str, ...], set[str]] = {}

    for entry in files:
        path = entry.get("path", "") if isinstance(entry, dict) else ""
        parts = path.split("/")
        if len(parts) < 2 or "api" not in parts or not _ROUTE_FILENAME_RE.match(parts[-1]):
            continue
        dir_parts = parts[:-1]
        for start in range(len(dir_parts)):
            for end in range(start, len(dir_parts)):
                pattern = tuple(dir_parts[:start] + ["*"] + dir_parts[end + 1 :] + [parts[-1]])
                pattern_members.setdefault(pattern, set()).add(path)

    ranked_patterns: list[tuple[int, int, tuple[str, ...]]] = []
    for pattern, members in pattern_members.items():
        if len(members) < 2 or pattern.count("*") != 1:
            continue
        wildcard_index = pattern.index("*")
        api_indexes = [idx for idx, part in enumerate(pattern) if part == "api"]
        if not api_indexes or wildcard_index <= api_indexes[-1]:
            continue
        ranked_patterns.append((len(pattern) - 1, wildcard_index, pattern))

    if not ranked_patterns:
        return []

    best_pattern = sorted(ranked_patterns, key=lambda item: (-item[0], -item[1], item[2]))[0][2]
    wildcard_index = best_pattern.index("*")
    seeds: list[PredictedWrite] = []

    for entity in entities:
        candidate_parts = list(best_pattern)
        candidate_parts[wildcard_index] = entity
        candidate_path = "/".join(candidate_parts)
        if candidate_path in existing_paths or any(seed.path == candidate_path for seed in seeds):
            continue
        confidence = (
            SEED_SIBLING_PATTERN_PRIMARY_CONFIDENCE
            if not seeds
            else SEED_SIBLING_PATTERN_SECONDARY_CONFIDENCE
        )
        seeds.append(
            PredictedWrite(
                path=candidate_path,
                confidence=confidence,
                reason=(
                    "Sibling-pattern seed: existing API routes follow "
                    f"{'/'.join(best_pattern)}; substitute task entity '{entity}'."
                ),
            )
        )
        if len(seeds) >= 2:
            break
    return seeds


def _index_seed(
    task: TaskInput, repo_root: Path | None, repo_graph: dict[str, Any]
) -> list[PredictedWrite]:
    """Run the deterministic indexer aggregator (framework + PageRank + BM25 + co-change).

    Wraps :func:`acg.index.aggregate` with three layers of safety so the
    existing seed pipeline never regresses:

    * ``repo_root=None`` short-circuits — pagerank/cochange need a real
      filesystem and git history.
    * Any exception inside the aggregator (missing tree-sitter binding,
      unreadable git log, malformed cache pickle) is swallowed and ``[]``
      is returned.
    * Outputs below :data:`SEED_INDEX_CONFIDENCE_FLOOR` are dropped to
      keep noisy long-tail entries out of the LLM rerank context.
    """
    if repo_root is None:
        return []
    try:
        from acg.index import aggregate as index_aggregate
    except Exception:
        return []
    try:
        candidates = index_aggregate(
            task, repo_root, repo_graph, top_n=SEED_INDEX_TOP_N
        )
    except Exception:
        return []
    is_test_task = _looks_like_test_task(task.prompt) or bool(
        task.hints and {"test", "tests", "e2e"} & {hint.lower() for hint in task.hints.touches}
    )
    is_docs_task = _looks_like_docs_task(task.prompt) or bool(
        task.hints
        and {"doc", "docs", "documentation", "readme"}
        & {hint.lower() for hint in task.hints.touches}
    )
    return [
        pw
        for pw in candidates
        if pw.confidence >= SEED_INDEX_CONFIDENCE_FLOOR
        and (is_test_task or not _is_test_prediction(pw.path))
        and (is_docs_task or not _is_docs_prediction(pw.path))
    ]


def _is_test_prediction(path: str) -> bool:
    parts = path.split("/")
    return (
        path.startswith(("test/", "tests/", "__tests__/", "cypress/", "e2e/"))
        or any(part in {"test", "tests", "__tests__"} for part in parts)
        or bool(re.search(r"\.(?:test|spec|test-d)\.", path))
    )


def _is_docs_prediction(path: str) -> bool:
    lower = path.lower()
    parts = lower.split("/")
    return (
        lower in {"readme.md", "changelog.md", "history.md"}
        or lower.startswith(("docs/", "doc/"))
        or any(part in {"docs", "doc", "documentation"} for part in parts)
    )


def _looks_like_docs_task(prompt: str) -> bool:
    return bool(re.search(r"\b(docs?|documentation|readme|changelog|release notes?)\b", prompt, re.IGNORECASE))


def _read_testdir_from_js_config(path: Path) -> str | None:
    """Best-effort regex extract of ``testDir`` from a Playwright/Vitest config."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _TESTDIR_RE.search(text)
    if not match:
        return None
    return match.group(1).strip("./").rstrip("/") or None


def _detect_test_layout(
    repo_root: Path | None, prompt: str
) -> tuple[str, str, str, str | None] | None:
    """Pick a framework + (test_dir, extension, config_path) tuple for the task.

    Returns ``None`` if neither a known config file nor a known framework
    keyword in the prompt can be matched.
    """
    # 1. Existing config files in the repo win (highest precision).
    if repo_root and repo_root.is_dir():
        for ext_variant in ("ts", "js", "mjs"):
            cfg = repo_root / f"playwright.config.{ext_variant}"
            if cfg.exists():
                td = _read_testdir_from_js_config(cfg) or "tests"
                return ("playwright", td, ".spec.ts", cfg.name)
        for ext_variant in ("ts", "js", "mjs"):
            cfg = repo_root / f"vitest.config.{ext_variant}"
            if cfg.exists():
                td = _read_testdir_from_js_config(cfg) or "tests"
                return ("vitest", td, ".test.ts", cfg.name)
        for fname in ("jest.config.ts", "jest.config.js", "jest.config.mjs"):
            cfg = repo_root / fname
            if cfg.exists():
                return ("jest", "__tests__", ".test.ts", cfg.name)
        for fname in ("cypress.config.ts", "cypress.config.js"):
            cfg = repo_root / fname
            if cfg.exists():
                return ("cypress", "cypress/e2e", ".cy.ts", cfg.name)
        # pytest: configured via pyproject.toml or pytest.ini — we use the
        # default 'tests/' layout if either exists.
        for fname in ("pytest.ini", "pyproject.toml", "tox.ini", "setup.cfg"):
            if (repo_root / fname).exists():
                return ("pytest", "tests", ".py", None)

    # 2. Greenfield: framework keyword in the prompt picks the convention.
    lower_prompt = prompt.lower()
    for kw in _FRAMEWORK_KEYWORD_PRIORITY:
        if kw in lower_prompt:
            cfg, td, ext = _FRAMEWORK_DEFAULTS[kw]
            return (kw, td, ext, cfg)
    return None


def _test_scaffold_seed(
    task: TaskInput, repo_root: Path | None
) -> list[PredictedWrite]:
    """Seed test/spec paths driven by project conventions or framework defaults.

    For tasks that ask to *create* tests, this is by far the highest-precision
    signal — the project's declared test layout (or, when missing, the named
    framework's canonical default) tells us exactly where the new file goes.
    Always emits the config file itself when greenfield, since the worker will
    need to create it.
    """
    if not _looks_like_test_task(task.prompt):
        return []
    layout = _detect_test_layout(repo_root, task.prompt)
    if layout is None:
        return []
    framework, test_dir, ext, config_path = layout

    entities = _extract_entity_nouns(task.prompt) or [task.id]
    is_e2e = bool(_E2E_RE.search(task.prompt))

    seeds: list[PredictedWrite] = []

    # Config file itself, only if it doesn't exist yet (greenfield).
    if config_path:
        config_exists = bool(
            repo_root and (repo_root / config_path).exists()
        )
        if not config_exists:
            seeds.append(
                PredictedWrite(
                    path=config_path,
                    confidence=SEED_TEST_SCAFFOLD_CONFIDENCE,
                    reason=(
                        f"{framework} config file inferred from task prompt"
                        f" (project does not declare one yet)."
                    ),
                )
            )

    # The actual spec file(s).
    for entity in entities:
        if framework == "playwright" and is_e2e:
            spec_path = f"{test_dir}/e2e/{entity}{ext}"
        elif framework == "pytest":
            spec_path = f"{test_dir}/test_{entity}{ext}"
        else:
            spec_path = f"{test_dir}/{entity}{ext}"

        seeds.append(
            PredictedWrite(
                path=spec_path,
                confidence=SEED_TEST_SCAFFOLD_CONFIDENCE,
                reason=(
                    f"{framework} convention: {test_dir}/ with {ext}"
                    f" extension, entity '{entity}' from task prompt."
                ),
            )
        )
    return seeds


def _env_seed(task: TaskInput, repo_root: Path | None) -> list[PredictedWrite]:
    if not _ENV_TRIGGER_RE.search(task.prompt):
        return []
    seeds = [
        PredictedWrite(
            path=".env.example",
            confidence=SEED_ENV_CONFIDENCE,
            reason=(
                "Env-var seed: prompt mentions credentials/providers; agents typically"
                " extend `.env.example`."
            ),
        )
    ]
    has_next_config = bool(
        repo_root
        and (
            (repo_root / "next.config.js").exists()
            or (repo_root / "next.config.ts").exists()
        )
    )
    if has_next_config:
        seeds.append(
            PredictedWrite(
                path=".env.local",
                confidence=SEED_ENV_LOCAL_CONFIDENCE,
                reason="Next.js project: `.env.local` is the conventional secrets file.",
            )
        )
    return seeds


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


def _path_entries(repo_graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        entry.get("path"): entry
        for entry in repo_graph.get("files", [])
        if isinstance(entry, dict) and isinstance(entry.get("path"), str)
    }


def _graph_neighbors(
    path: str, repo_graph: dict[str, Any], entries: dict[str, dict[str, Any]]
) -> list[str]:
    entry = entries.get(path, {})
    out: list[str] = []
    for key in ("resolved_imports", "importers", "type_links"):
        value = entry.get(key)
        if isinstance(value, list):
            out.extend(item for item in value if isinstance(item, str))
        mapping = repo_graph.get(key)
        if isinstance(mapping, dict):
            mapped = mapping.get(path)
            if isinstance(mapped, list):
                out.extend(item for item in mapped if isinstance(item, str))
    return sorted(dict.fromkeys(out))


def _graph_neighbor_edges(
    path: str, repo_graph: dict[str, Any], entries: dict[str, dict[str, Any]]
) -> list[tuple[str, str]]:
    entry = entries.get(path, {})
    out: list[tuple[str, str]] = []
    for key, kind in (
        ("resolved_imports", "import"),
        ("importers", "importer"),
        ("type_links", "type"),
    ):
        value = entry.get(key)
        if isinstance(value, list):
            out.extend((item, kind) for item in value if isinstance(item, str))
        mapping = repo_graph.get(key)
        if isinstance(mapping, dict):
            mapped = mapping.get(path)
            if isinstance(mapped, list):
                out.extend((item, kind) for item in mapped if isinstance(item, str))
    return sorted(dict.fromkeys(out))


def _token_set(text: str) -> set[str]:
    from acg.index.util import tokenize

    return set(tokenize(text))


def _task_matches_path(task_tokens: set[str], path: str, entry: dict[str, Any]) -> bool:
    del entry
    return bool(_token_set(path) & task_tokens)


def _graph_expansion_seed(
    task: TaskInput,
    repo_graph: dict[str, Any],
    seeds: list[PredictedWrite],
) -> list[PredictedWrite]:
    entries = _path_entries(repo_graph)
    if not entries:
        return []
    existing = {seed.path for seed in seeds}
    high_confidence_seeds = [
        seed for seed in seeds if seed.confidence >= SEED_GRAPH_EXPANSION_MIN_SEED_CONFIDENCE
    ]
    if not high_confidence_seeds:
        return []
    task_tokens = _token_set(task.prompt)
    is_test_task = _looks_like_test_task(task.prompt) or bool(
        task.hints and {"test", "tests", "e2e"} & {hint.lower() for hint in task.hints.touches}
    )
    evidence: dict[str, set[str]] = {}
    edge_kinds: dict[str, set[str]] = {}
    for seed in high_confidence_seeds:
        for neighbor, kind in _graph_neighbor_edges(seed.path, repo_graph, entries):
            if neighbor in existing or neighbor not in entries:
                continue
            if not is_test_task and _is_test_prediction(neighbor):
                continue
            evidence.setdefault(neighbor, set()).add(seed.path)
            edge_kinds.setdefault(neighbor, set()).add(kind)
    expansions: list[PredictedWrite] = []
    for path, sources in sorted(evidence.items()):
        entry = entries[path]
        kinds = edge_kinds.get(path, set())
        if _task_matches_path(task_tokens, path, entry):
            confidence = SEED_GRAPH_EXPANSION_CONFIDENCE
        elif "type" in kinds:
            confidence = SEED_GRAPH_EXPANSION_STRUCTURAL_CONFIDENCE
        elif "import" in kinds and len(sources) >= 2:
            confidence = SEED_GRAPH_EXPANSION_STRUCTURAL_CONFIDENCE
        elif len(sources) >= 2:
            continue
        else:
            continue
        expansions.append(
            PredictedWrite(
                path=path,
                confidence=confidence,
                reason=(
                    "Graph expansion: local import/importer/type edge from "
                    f"high-confidence seed(s) {', '.join(sorted(sources)[:3])}."
                ),
            )
        )
    return expansions


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
    repo_root: Path | None = None,
) -> list[PredictedWrite]:
    """Predict the file write-set for a single task.

    Args:
        task: Input task as supplied via ``tasks.json``.
        repo_graph: Output of :mod:`graph_builder.scan` (TS) or an empty dict.
        llm: LLM client implementing :class:`~acg.llm.LLMProtocol`.
        repo_root: Optional path to the target repository.  When provided,
            enables the test-scaffold seed (which inspects on-disk config
            files like ``playwright.config.ts``).  Safe to omit; the seed
            falls back to prompt-keyword inference and emits ``[]`` if
            neither a config nor a framework keyword is present.

    Returns:
        Up to :data:`MAX_PREDICTIONS` :class:`PredictedWrite` items, sorted by
        descending confidence.
    """
    seeds = _static_seed(task.prompt)
    seeds += _symbol_seed(task.prompt, repo_graph)
    if task.hints and task.hints.touches:
        seeds += _topical_seed(list(task.hints.touches), repo_graph)
    seeds += _test_scaffold_seed(task, repo_root)
    seeds += _env_seed(task, repo_root)
    seeds += _sibling_pattern_seed(task, repo_graph)
    seeds += _index_seed(task, repo_root, repo_graph)
    seeds += _graph_expansion_seed(task, repo_graph, seeds)
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
