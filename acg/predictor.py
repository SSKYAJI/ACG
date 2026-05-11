"""Task → write-set predictor.

For each :class:`~acg.schema.TaskInput`, the predictor fuses **eight
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
8. ``_module_name_seed`` — task-token alignment to ``src/<module>/<module>.*`` clusters

The seeds give us a defensible baseline even when the LLM is offline; the
re-rank lets the model add task-implied files that the seeds cannot infer
(e.g. a ``components/sidebar.tsx`` for an "add a sidebar entry" task).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .compiler import _is_test_task as _compiler_is_test_task
from .llm import LLMProtocol
from .schema import FileScope, PredictedWrite, TaskInput

# Tunable thresholds (no magic numbers in module bodies).
SEED_FILE_CONFIDENCE = 0.95
SEED_SYMBOL_CONFIDENCE = 0.85
SEED_TOPICAL_CONFIDENCE = 0.7
SEED_TEST_SCAFFOLD_CONFIDENCE = 0.85
SEED_ENV_CONFIDENCE = 0.8
SEED_ENV_LOCAL_CONFIDENCE = 0.65
SEED_SIBLING_PATTERN_PRIMARY_CONFIDENCE = 0.75
SEED_SIBLING_PATTERN_SECONDARY_CONFIDENCE = 0.65
SEED_PLANNER_HINT_CONFIDENCE = 0.82
SEED_TEST_SOURCE_LINK_CONFIDENCE = 0.82
SEED_INDEX_CONFIDENCE_FLOOR = 0.5
# Cap how many index-aggregator predictions feed into the seed pipeline per
# task. The aggregator's PageRank component is biased toward repo-wide
# hotspots and will happily propose the same auth/db files for every task,
# which over-serializes the lockfile. Three signals per task keeps the
# strongest hits while leaving room for task-specific seeds to dominate.
SEED_INDEX_TOP_N = 24
SEED_GRAPH_EXPANSION_CONFIDENCE = 0.72
SEED_GRAPH_EXPANSION_STRUCTURAL_CONFIDENCE = 0.78
SEED_GRAPH_EXPANSION_MIN_SEED_CONFIDENCE = 0.72
LLM_SEED_EXPANSION_REASON = (
    "LLM seed expansion: planner path from repository manifest; not in indexer seed pool."
)
TOP_GRAPH_FILES_FOR_LLM = 50
MAX_PREDICTIONS = 10
MAX_CONTEXT_PREDICTIONS = 25
HUB_IMPORTER_THRESHOLD = 20
HUB_IMPORT_THRESHOLD = 15
HUB_TOTAL_DEGREE_THRESHOLD = 30

HIGH_PRECISION_SIGNALS = {
    "auth_role",
    "cluster",
    "env",
    "explicit",
    "framework",
    "llm",
    "module_name",
    "package",
    "planner",
    "sibling",
    "symbol",
    "testlink",
}
_CANDIDATE_HIGH_PRECISION_SIGNALS = HIGH_PRECISION_SIGNALS | {"scope_review"}
CONTEXT_ONLY_SIGNALS = {"bm25", "cochange", "entity", "graph", "hint", "pagerank", "scip"}
AUTO_REPLAN_SIGNALS = {
    "auth_role",
    "explicit",
    "framework",
    "llm",
    "package",
    "planner",
    "symbol",
    "testlink",
}
TEST_LINK_STOPWORDS = {
    "middleware",
    "test",
    "tests",
    "testing",
    "unit",
}


@dataclass(frozen=True)
class ScopePrediction:
    scopes: list[FileScope]
    scope_review_tokens: int = 0


# Regex for explicit file mentions like ``lib/auth.ts`` or ``prisma/schema.prisma``.
_FILE_MENTION_RE = re.compile(
    r"(?<![\w/.-])([\w./-]+\.(?:ts|tsx|js|jsx|mjs|cjs|py|prisma|sql|md|json|yaml|yml|html|css))"
)
# Symbol candidates: camelCase tokens length > 5 (e.g. ``getCurrentUser``).
_SYMBOL_RE = re.compile(r"\b([A-Za-z][a-zA-Z0-9]{5,})\b")

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
    "the",
    "a",
    "an",
    "this",
    "that",
    "all",
    "any",
    "src",
    "lib",
    "test",
    "tests",
    "testing",
    "spec",
    "specs",
    "playwright",
    "vitest",
    "jest",
    "pytest",
    "cypress",
    "end",
    "to",
    "unit",
    "integration",
    "e2e",
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
_AUTH_ROLE_TRIGGER_RE = re.compile(
    r"\b(role|roles|guard|middleware|decorator|auth|permission|access.?control|rbac|acl)\b",
    re.IGNORECASE,
)
_PACKAGE_TRIGGER_RE = re.compile(
    r"\b("
    r"rate[-\s]?limit(?:ing)?"
    r"|\brate\s+limiting\b"
    r"|throttl(?:ing)?"
    r"|\bthrottling\b"
    r"|library|package|dependency|install\s+(?:the\s+|a\s+|an\s+)?([a-z][\w-]+)"
    r")\b",
    re.IGNORECASE,
)
_CLUSTER_SUFFIXES = (
    ".controller.ts",
    ".service.ts",
    ".module.ts",
    ".entity.ts",
    ".interface.ts",
    ".middleware.ts",
    ".dto.ts",
    ".guard.ts",
)
SEED_AUTH_ROLE_CONFIDENCE = 0.75
SEED_MODULE_NAME_CONFIDENCE = 0.75
SEED_PACKAGE_JSON_CONFIDENCE = 0.75
SEED_CLUSTER_CONFIDENCE = 0.65
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
        candidates = index_aggregate(task, repo_root, repo_graph, top_n=SEED_INDEX_TOP_N)
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
    return bool(
        re.search(
            r"\b(docs?|documentation|readme|changelog|release notes?)\b", prompt, re.IGNORECASE
        )
    )


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


def _test_scaffold_seed(task: TaskInput, repo_root: Path | None) -> list[PredictedWrite]:
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
        config_exists = bool(repo_root and (repo_root / config_path).exists())
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
        spec_exists = bool(repo_root and (repo_root / spec_path).exists())
        confidence = SEED_TEST_SCAFFOLD_CONFIDENCE if framework != "pytest" or spec_exists else 0.55
        reason_prefix = (
            f"{framework} convention"
            if confidence >= SEED_TEST_SCAFFOLD_CONFIDENCE
            else f"{framework} convention candidate"
        )

        seeds.append(
            PredictedWrite(
                path=spec_path,
                confidence=confidence,
                reason=(
                    f"{reason_prefix}: {test_dir}/ with {ext}"
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
        and ((repo_root / "next.config.js").exists() or (repo_root / "next.config.ts").exists())
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


def _topical_seed(hints: list[str], repo_graph: dict[str, Any]) -> list[PredictedWrite]:
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


def _planner_suspected_file_seed(task: TaskInput) -> list[PredictedWrite]:
    if not task.hints or not task.hints.suspected_files:
        return []
    seen: dict[str, PredictedWrite] = {}
    for raw_path in task.hints.suspected_files:
        path = raw_path.strip("./")
        if not path or path in seen:
            continue
        seen[path] = PredictedWrite(
            path=path,
            confidence=SEED_PLANNER_HINT_CONFIDENCE,
            reason="Planner suspected file from task decomposition hints.",
        )
    return list(seen.values())


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


def _read_repo_file(repo_root: Path | None, path: str, max_chars: int = 80_000) -> str:
    if repo_root is None:
        return ""
    try:
        return (repo_root / path).read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except OSError:
        return ""


def _test_source_link_seed(
    task: TaskInput,
    repo_root: Path | None,
    repo_graph: dict[str, Any],
    seeds: list[PredictedWrite],
) -> list[PredictedWrite]:
    """Find existing regression tests tied to source candidates.

    This is deliberately existing-file only. It improves Python projects like
    Starlette without fabricating pytest filenames from task nouns.
    """
    if not _looks_like_test_task(task.prompt):
        return []
    entries = _path_entries(repo_graph)
    if not entries:
        return []
    source_seeds = [
        seed for seed in seeds if seed.path in entries and not _is_test_prediction(seed.path)
    ]
    if not source_seeds:
        return []

    prompt_tokens = _token_set(task.prompt) - TEST_LINK_STOPWORDS
    out: dict[str, PredictedWrite] = {}
    for test_path, entry in entries.items():
        if not _is_test_prediction(test_path):
            continue
        text = _read_repo_file(repo_root, test_path)
        imported_sources = set(entry.get("resolved_imports") or [])
        path_tokens = _token_set(test_path) - TEST_LINK_STOPWORDS
        for source in source_seeds:
            source_entry = entries[source.path]
            source_symbols = {
                item
                for item in [
                    *source_entry.get("exports", []),
                    *source_entry.get("symbols", []),
                ]
                if isinstance(item, str)
            }
            mentions_symbol = any(symbol and symbol in text for symbol in source_symbols)
            imports_source = source.path in imported_sources
            topical_match = bool(prompt_tokens & path_tokens)
            if not (imports_source or mentions_symbol or topical_match):
                continue
            out[test_path] = PredictedWrite(
                path=test_path,
                confidence=SEED_TEST_SOURCE_LINK_CONFIDENCE,
                reason=(
                    "Test-source mapping: existing test imports or mentions "
                    f"source candidate {source.path}."
                ),
            )
            break
    return list(out.values())


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


def _stub_sibling_path(path: str) -> str | None:
    if path.endswith(".py"):
        return path[:-3] + ".pyi"
    if path.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
        return str(Path(path).with_suffix(".d.ts"))
    return None


def _type_link_neighbors(
    path: str, repo_graph: dict[str, Any], entries: dict[str, dict[str, Any]]
) -> list[str]:
    entry = entries.get(path, {})
    out: list[str] = []
    value = entry.get("type_links")
    if isinstance(value, list):
        out.extend(item for item in value if isinstance(item, str))
    mapping = repo_graph.get("type_links")
    if isinstance(mapping, dict):
        mapped = mapping.get(path)
        if isinstance(mapped, list):
            out.extend(item for item in mapped if isinstance(item, str))
    return sorted(dict.fromkeys(out))


def _resolved_import_stub_neighbors(
    path: str, repo_graph: dict[str, Any], entries: dict[str, dict[str, Any]]
) -> list[str]:
    entry = entries.get(path, {})
    imports: list[str] = []
    value = entry.get("resolved_imports")
    if isinstance(value, list):
        imports.extend(item for item in value if isinstance(item, str))
    mapping = repo_graph.get("resolved_imports")
    if isinstance(mapping, dict):
        mapped = mapping.get(path)
        if isinstance(mapped, list):
            imports.extend(item for item in mapped if isinstance(item, str))
    out: list[str] = []
    for target in dict.fromkeys(imports):
        stub = _stub_sibling_path(target)
        if stub and stub in entries:
            out.append(stub)
    return sorted(dict.fromkeys(out))


def _post_llm_must_write_neighbor_expansion(
    task: TaskInput,
    repo_graph: dict[str, Any],
    writes: list[PredictedWrite],
    writes_by_path: dict[str, PredictedWrite],
    signal_map: dict[str, set[str]],
    llm_paths: set[str],
) -> list[PredictedWrite]:
    entries = _path_entries(repo_graph)
    if not entries:
        return []

    anchor_writes: list[PredictedWrite] = []
    for write in writes:
        signals = signal_map.get(write.path, set()) or _signals_for_reason(write.reason)
        if write.path in llm_paths or _is_must_write(task, write, signals, entries):
            anchor_writes.append(write)

    expansions: list[PredictedWrite] = []

    for anchor in anchor_writes:
        neighbors: list[tuple[str, str]] = []
        for path in _type_link_neighbors(anchor.path, repo_graph, entries):
            neighbors.append((path, "type_link"))
        for path in _resolved_import_stub_neighbors(anchor.path, repo_graph, entries):
            neighbors.append((path, "stub"))

        seen_for_anchor: set[str] = set()
        picked = 0
        for neighbor_path, _kind in neighbors:
            if neighbor_path == anchor.path or neighbor_path in seen_for_anchor:
                continue
            seen_for_anchor.add(neighbor_path)
            picked += 1

            existing = writes_by_path.get(neighbor_path)
            if existing is not None:
                if existing.confidence < SEED_GRAPH_EXPANSION_STRUCTURAL_CONFIDENCE:
                    writes_by_path[neighbor_path] = PredictedWrite(
                        path=neighbor_path,
                        confidence=SEED_GRAPH_EXPANSION_STRUCTURAL_CONFIDENCE,
                        reason=existing.reason,
                    )
                signal_map.setdefault(neighbor_path, set()).update({"graph", "must_write_neighbor"})
            else:
                expanded = PredictedWrite(
                    path=neighbor_path,
                    confidence=SEED_GRAPH_EXPANSION_STRUCTURAL_CONFIDENCE,
                    reason=(
                        "Post-LLM graph expansion: type/stub neighbor of "
                        f"{'must_write' if _is_must_write(task, anchor, signal_map.get(anchor.path, set()) or _signals_for_reason(anchor.reason), entries) else 'LLM'} path {anchor.path}."
                    ),
                )
                expansions.append(expanded)
                writes_by_path[neighbor_path] = expanded
                signal_map.setdefault(neighbor_path, set()).update({"graph", "must_write_neighbor"})
            if picked >= 3:
                break

    if not expansions:
        return []
    return expansions


def _auth_role_seed(task: TaskInput, repo_graph: dict[str, Any]) -> list[PredictedWrite]:
    """Predict auth middleware / guard files when the prompt mentions roles or access control."""
    if not _AUTH_ROLE_TRIGGER_RE.search(task.prompt):
        return []
    files = repo_graph.get("files") or []
    out: dict[str, PredictedWrite] = {}
    for entry in files:
        path = entry.get("path", "") if isinstance(entry, dict) else ""
        lower = path.lower()
        if any(kw in lower for kw in ("auth", "guard", "role", "permission", "middleware")):
            if path not in out:
                out[path] = PredictedWrite(
                    path=path,
                    confidence=SEED_AUTH_ROLE_CONFIDENCE,
                    reason="Auth/role seed: prompt mentions access control; file matches auth/guard/middleware naming.",
                )
    return list(out.values())


def _package_json_seed(task: TaskInput, repo_root: Path | None) -> list[PredictedWrite]:
    """Predict package.json when the prompt implies adding a library or dependency."""
    if not _PACKAGE_TRIGGER_RE.search(task.prompt):
        return []
    if repo_root is None or not (repo_root / "package.json").exists():
        return []
    return [
        PredictedWrite(
            path="package.json",
            confidence=SEED_PACKAGE_JSON_CONFIDENCE,
            reason="Package seed: prompt mentions adding a library, package, or rate-limiting dependency.",
        )
    ]


def _cluster_base_relevant(base_name: str, task_tokens: set[str]) -> bool:
    """Return True when ``base_name`` is plausibly the entity the task is about.

    The implied-cluster seed amplifies one anchor into all of a NestJS module's
    sibling files. Without a task-grounding gate, an indexer hit on an
    unrelated module (e.g. ``profile.service.ts`` ranked high by PageRank)
    becomes a full ``must_write`` cluster for a task that only cares about
    articles. This helper is the gate: the cluster anchor's base name
    (``profile`` / ``user`` / ``article``) must overlap, with simple
    singular/plural tolerance, with the tokens drawn from the task id, prompt,
    and ``hints.touches``.

    When one string is a prefix of the other, the extra tail must look like a
    simple English plural (``s`` / ``es`` only, length at most 2) so
    ``article`` matches ``articles`` and ``class`` matches ``classes``, but
    ``auth`` does not match ``author`` (tail ``or``, not a plural suffix).

    >>> _cluster_base_relevant("article", {"add", "articles", "search"})
    True
    >>> _cluster_base_relevant("user", {"add", "users", "roles"})
    True
    >>> _cluster_base_relevant("profile", {"add", "article", "search"})
    False
    >>> _cluster_base_relevant("auth", {"author", "name"})
    False
    >>> _cluster_base_relevant("ab", {"ab", "abc"})  # too short to gate on
    False
    """
    base = base_name.lower()
    if len(base) < 3:
        return False
    _plural_tails = frozenset(("", "s", "es"))

    def _prefix_plural_match(longer: str, shorter: str) -> bool:
        if not longer.startswith(shorter):
            return False
        if len(longer) - len(shorter) > 2:
            return False
        return longer[len(shorter) :] in _plural_tails

    for token in task_tokens:
        if token == base:
            return True
        if _prefix_plural_match(token, base):
            return True
        if _prefix_plural_match(base, token):
            return True
    return False


def _implied_cluster_seed(
    task: TaskInput, repo_graph: dict[str, Any], seeds: list[PredictedWrite]
) -> list[PredictedWrite]:
    """Predict co-located module files (controller/service/entity/module/etc.) from existing seeds.

    When a seed points to one file in a NestJS/TS module cluster, the other files
    in the same directory with related suffixes are likely to need coordinated
    edits. This improves recall for entity/controller/service tasks.

    Gated on task relevance: only expand a cluster when the anchor's ``base_name``
    (e.g. ``article`` in ``src/article/article.service.ts``) is plausibly the
    entity the task is about. Without this gate a broad indexer signal on an
    unrelated module would amplify into a full ``must_write`` cluster for the
    wrong task — see :func:`_cluster_base_relevant`.
    """
    entries = _path_entries(repo_graph)
    if not entries:
        return []
    task_tokens = _task_evidence_tokens(task)
    existing_paths = {seed.path for seed in seeds}
    cluster_predictions: dict[str, PredictedWrite] = {}
    for seed in seeds:
        seed_path = seed.path
        if "/" not in seed_path:
            continue
        dir_part = seed_path.rsplit("/", 1)[0]
        seed_suffix = seed_path.rsplit("/", 1)[-1]
        # Find the base name (e.g., "user.controller.ts" -> "user")
        base_name: str | None = None
        for suffix in _CLUSTER_SUFFIXES:
            if seed_suffix.endswith(suffix):
                base_name = seed_suffix[: -len(suffix)]
                break
        if base_name is None:
            continue
        if not _cluster_base_relevant(base_name, task_tokens):
            continue
        for suffix in _CLUSTER_SUFFIXES:
            candidate = f"{dir_part}/{base_name}{suffix}"
            if candidate == seed_path or candidate in existing_paths:
                continue
            if candidate not in entries:
                continue
            if candidate in cluster_predictions:
                continue
            cluster_predictions[candidate] = PredictedWrite(
                path=candidate,
                confidence=SEED_CLUSTER_CONFIDENCE,
                reason=(
                    f"Implied cluster seed: {seed_path} suggests coordinated edits"
                    f" to sibling {candidate} in the same module."
                ),
            )
    return list(cluster_predictions.values())


def _module_name_seed(task: TaskInput, repo_graph: dict[str, Any]) -> list[PredictedWrite]:
    """Seed ``src/<name>/<name>.*`` NestJS siblings when task tokens match ``<name>`` (plural-aware).

    Uses :func:`_task_evidence_tokens` and :func:`_cluster_base_relevant` so
    plural/prefix rules stay aligned with :func:`_implied_cluster_seed`.
    """
    entries = _path_entries(repo_graph)
    if not entries:
        return []
    task_tokens = _task_evidence_tokens(task)
    module_dirs: set[str] = set()
    for entry in repo_graph.get("files") or []:
        path = entry.get("path", "") if isinstance(entry, dict) else ""
        parts = path.split("/")
        if len(parts) < 3 or parts[0] != "src":
            continue
        if "." in parts[1]:
            continue
        module_dirs.add(f"{parts[0]}/{parts[1]}")
    predictions: dict[str, PredictedWrite] = {}
    for module_dir in module_dirs:
        base_name = module_dir.rsplit("/", 1)[-1]
        if not _cluster_base_relevant(base_name, task_tokens):
            continue
        for suffix in _CLUSTER_SUFFIXES:
            candidate = f"{module_dir}/{base_name}{suffix}"
            if candidate not in entries:
                continue
            predictions[candidate] = PredictedWrite(
                path=candidate,
                confidence=SEED_MODULE_NAME_CONFIDENCE,
                reason=(
                    "Module-name seed: task tokens align with module "
                    f"{module_dir!r}; coordinated NestJS cluster file."
                ),
            )
    return list(predictions.values())


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


def _llm_seed_expansion(
    task: TaskInput,
    repo_graph: dict[str, Any],
    existing_seed_paths: set[str],
    llm_client: Any,
    *,
    max_expansions: int = 5,
    manifest_size: int = 200,
) -> list[FileScope]:
    """Ask the planner LLM for additional files the seed indexers missed.

    Sends the task prompt + a list of repo files NOT in `existing_seed_paths`
    (top `manifest_size` by pagerank) and asks the LLM to propose up to
    `max_expansions` additional files. Each proposed file becomes a FileScope
    with `tier='candidate_context'`, `score=0.72`, and `signals=['planner']`.

    Filters out paths that don't exist in repo_graph. Returns an empty list if
    the LLM call fails or returns no proposals.
    """
    if llm_client is None:
        return []
    entries = _path_entries(repo_graph)
    if not entries:
        return []
    ranked: list[tuple[tuple[float, str], str]] = []
    for e in repo_graph.get("files") or []:
        if not isinstance(e, dict):
            continue
        p = e.get("path")
        if not isinstance(p, str) or p in existing_seed_paths or p not in entries:
            continue
        f = entries.get(p, e)
        pr = f.get("pagerank")
        score = (
            float(pr)
            if isinstance(pr, (int, float))
            else float(
                int(f.get("imported_by_count") or 0)
                + len(f.get("importers") or [])
                + len(f.get("resolved_imports") or [])
                + len(f.get("type_links") or [])
            )
        )
        ranked.append(((-score, p), p))
    ranked.sort()
    manifest = [x for _, x in ranked[:manifest_size]]
    if not manifest:
        return []
    nl, ex = "\n", sorted(existing_seed_paths)[:50]
    try:
        raw = llm_client.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a code-search assistant. Pick task-relevant repo files not in the "
                        'candidate list. Output ONLY JSON {"paths": ["relative/path", ...]}.'
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Task: {task.prompt}\n\nExisting candidates (do NOT re-propose):\n{nl.join(ex)}"
                        f"\n\nOther files (import centrality / pagerank order):\n{nl.join(manifest)}"
                        f"\n\nReturn up to {max_expansions} paths as JSON."
                    ),
                },
            ]
        )
    except Exception:
        return []
    t = (raw or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```\s*$", "", t)
    try:
        d = json.loads(t)
    except json.JSONDecodeError:
        i, j = t.find("{"), t.rfind("}")
        if i < 0 or j <= i:
            return []
        try:
            d = json.loads(t[i : j + 1])
        except json.JSONDecodeError:
            return []
    paths = d.get("paths") if isinstance(d, dict) else None
    if not isinstance(paths, list):
        return []
    known = set(entries)
    scopes: list[FileScope] = []
    for it in paths:
        path = (
            it.strip("./")
            if isinstance(it, str)
            else (_path_value(it) if isinstance(it, dict) else "")
        )
        if not path or path not in known or path in existing_seed_paths:
            continue
        pw = PredictedWrite(path=path, confidence=0.72, reason=LLM_SEED_EXPANSION_REASON)
        sg = {"planner"}
        scopes.append(
            FileScope(
                path=path,
                tier="candidate_context",
                score=0.72,
                signals=["planner"],
                reason=_scope_reason(pw, sg, "candidate_context"),
            )
        )
        if len(scopes) >= max_expansions:
            break
    return scopes


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
    hints_blob = json.dumps(task.hints.model_dump() if task.hints else {}, sort_keys=True)
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


def _merge(seeds: list[PredictedWrite], rerank: list[PredictedWrite]) -> list[PredictedWrite]:
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
        confidence = existing.confidence if _deterministic_reason(pw.reason) else pw.confidence
        merged[pw.path] = PredictedWrite(path=pw.path, confidence=confidence, reason=new_reason)
    return sorted(merged.values(), key=lambda p: (-p.confidence, p.path))


def _signals_for_reason(reason: str) -> set[str]:
    lower = reason.lower()
    signals: set[str] = set()
    if "explicit file mention" in lower or "path mentioned verbatim" in lower:
        signals.add("explicit")
    if lower.startswith("symbol "):
        signals.add("symbol")
    if lower.startswith("hint "):
        signals.add("hint")
    if "planner suspected file" in lower or "llm seed expansion" in lower:
        signals.add("planner")
    if "test-source mapping" in lower:
        signals.add("testlink")
    if "scope review" in lower:
        signals.add("scope_review")
    if (
        "test scaffold" in lower
        or " convention:" in lower
        or "config file inferred" in lower
        or "framework" in lower
    ):
        signals.add("framework")
    if ".env" in lower or "environment" in lower or "credential" in lower:
        signals.add("env")
    if "sibling pattern" in lower or "sibling-pattern" in lower:
        signals.add("sibling")
    if "bm25" in lower:
        signals.add("bm25")
    if "scip entity" in lower:
        signals.update({"scip", "entity"})
    elif "scip reference" in lower:
        signals.add("scip")
    if "pagerank" in lower:
        signals.add("pagerank")
    if "rose co-change" in lower or "co-change" in lower:
        signals.add("cochange")
    if "auth/role seed" in lower:
        signals.add("auth_role")
    if "package seed" in lower:
        signals.add("package")
    if "implied cluster seed" in lower:
        signals.add("cluster")
    if "module-name seed" in lower:
        signals.add("module_name")
    if "graph expansion" in lower:
        signals.add("graph")
    return signals


def _deterministic_reason(reason: str) -> bool:
    return bool(_signals_for_reason(reason))


def _graph_degree(path: str, entries: dict[str, dict[str, Any]]) -> tuple[int, int, int]:
    entry = entries.get(path, {})
    imports = entry.get("resolved_imports")
    importers = entry.get("importers")
    type_links = entry.get("type_links")
    return (
        len(imports) if isinstance(imports, list) else 0,
        len(importers) if isinstance(importers, list) else 0,
        len(type_links) if isinstance(type_links, list) else 0,
    )


def _is_graph_hub(path: str, entries: dict[str, dict[str, Any]]) -> bool:
    imports, importers, type_links = _graph_degree(path, entries)
    return (
        imports >= HUB_IMPORT_THRESHOLD
        or importers >= HUB_IMPORTER_THRESHOLD
        or imports + importers + type_links >= HUB_TOTAL_DEGREE_THRESHOLD
    )


def _reason_mentions_tests(reason: str) -> bool:
    return bool(re.search(r"\b(tests?|specs?|regression|coverage)\b", reason, re.I))


def _is_context_only_path(
    task: TaskInput,
    path: str,
    reason: str,
    signals: set[str],
    entries: dict[str, dict[str, Any]],
) -> bool:
    task_is_testy = _looks_like_test_task(task.prompt) or (
        "llm" in signals and _reason_mentions_tests(reason)
    )
    if _is_test_prediction(path) and not task_is_testy and "explicit" not in signals:
        return True
    if _is_graph_hub(path, entries) and "explicit" not in signals:
        return True
    return False


def _is_must_write(
    task: TaskInput,
    write: PredictedWrite,
    signals: set[str],
    entries: dict[str, dict[str, Any]],
) -> bool:
    if not signals:
        return False
    if "explicit" in signals:
        return True
    path_exists = write.path in entries
    if not path_exists and not (signals & {"env", "framework", "sibling"}):
        return False
    if _is_context_only_path(task, write.path, write.reason, signals, entries):
        return False
    if _is_test_prediction(write.path) and not (signals & {"explicit", "framework", "llm"}):
        return False
    task_tokens = _token_set(task.prompt)
    if {"bm25", "pagerank"} <= signals and write.confidence >= 0.75:
        if _task_matches_path(task_tokens, write.path, entries.get(write.path, {})):
            return True
    if signals <= CONTEXT_ONLY_SIGNALS:
        return False
    if "graph" in signals and not (signals & (HIGH_PRECISION_SIGNALS - {"framework"})):
        return False
    if "env" in signals and write.confidence >= SEED_ENV_LOCAL_CONFIDENCE:
        return True
    if (
        "framework" in signals
        and write.confidence >= SEED_TEST_SCAFFOLD_CONFIDENCE
        and any(keyword in task.prompt.lower() for keyword in _FRAMEWORK_KEYWORD_PRIORITY)
    ):
        return True
    if "llm" in signals and write.confidence >= 0.85:
        return True
    if "sibling" in signals and write.confidence >= SEED_SIBLING_PATTERN_PRIMARY_CONFIDENCE:
        return _task_matches_path(task_tokens, write.path, entries.get(write.path, {}))
    if "auth_role" in signals and write.confidence >= SEED_AUTH_ROLE_CONFIDENCE:
        return True
    if "package" in signals and write.confidence >= SEED_PACKAGE_JSON_CONFIDENCE:
        return True
    if "cluster" in signals and write.confidence >= SEED_CLUSTER_CONFIDENCE:
        return True
    if "module_name" in signals and write.confidence >= SEED_MODULE_NAME_CONFIDENCE:
        return True
    if len(signals & HIGH_PRECISION_SIGNALS) >= 2 and write.confidence >= 0.8:
        return True
    if "symbol" in signals and write.confidence >= SEED_SYMBOL_CONFIDENCE:
        return True
    high_precision = signals & HIGH_PRECISION_SIGNALS
    if (
        len(high_precision) >= 3
        and "llm" in signals
        and _task_matches_path(task_tokens, write.path, entries.get(write.path, {}))
        and write.confidence >= 0.7
    ):
        return True
    return False


def _task_evidence_tokens(task: TaskInput) -> set[str]:
    hints = task.hints.touches if task.hints else []
    return _token_set(" ".join([task.id, task.prompt, *hints]))


def _entry_matches_task_tokens(entry: dict[str, Any], task_tokens: set[str]) -> bool:
    fields: list[str] = []
    for key in ("symbols", "exports", "imports", "resolved_imports"):
        value = entry.get(key)
        if isinstance(value, list):
            fields.extend(item for item in value if isinstance(item, str))
    return bool(_token_set(" ".join(fields)) & task_tokens)


def _scip_matches_task_tokens(repo_graph: dict[str, Any], path: str, task_tokens: set[str]) -> bool:
    entities = _scip_entities_for_path(repo_graph, path)
    if not entities:
        return False
    text = " ".join(
        item
        for entity in entities
        for item in (entity.get("name", ""), entity.get("symbol", ""))
        if item
    )
    return bool(_token_set(text) & task_tokens)


def _has_candidate_context_evidence(
    task: TaskInput,
    repo_graph: dict[str, Any],
    path: str,
    entry: dict[str, Any],
) -> bool:
    task_tokens = _task_evidence_tokens(task)
    return (
        _task_matches_path(task_tokens, path, entry)
        or _entry_matches_task_tokens(entry, task_tokens)
        or _scip_matches_task_tokens(repo_graph, path, task_tokens)
    )


def _passes_structural_candidate_context_gate(
    task: TaskInput,
    repo_graph: dict[str, Any],
    write: PredictedWrite,
    signals: set[str],
    entries: dict[str, dict[str, Any]],
) -> bool:
    """Drop weak structural retrieval hits before they become worker context."""
    evidence_signals = signals - {"approved_replan"}
    if not evidence_signals:
        return False
    if _is_test_prediction(write.path) and not _compiler_is_test_task(task):
        if "explicit" not in evidence_signals and not (
            "testlink" in evidence_signals and evidence_signals & {"llm", "framework"}
        ):
            return False

    if "explicit" in evidence_signals:
        return True
    if evidence_signals == {"planner"} and "llm seed expansion" in write.reason.lower():
        return True
    if "must_write_neighbor" in evidence_signals and "graph" in evidence_signals:
        return True

    high_precision = evidence_signals & _CANDIDATE_HIGH_PRECISION_SIGNALS
    structural_signals = evidence_signals & CONTEXT_ONLY_SIGNALS

    if _is_graph_hub(write.path, entries):
        return bool(high_precision) and len(structural_signals) >= 2
    return (bool(high_precision) and len(evidence_signals) >= 2) or len(structural_signals) >= 3


def _scope_reason(write: PredictedWrite, signals: set[str], tier: str) -> str:
    signal_text = ", ".join(sorted(signals)) or "unknown"
    if tier == "must_write":
        return f"{write.reason} Signals: {signal_text}."
    return (
        f"{write.reason} Candidate context only; requires replan before write. "
        f"Signals: {signal_text}."
    )


def _build_file_scopes(
    task: TaskInput,
    repo_graph: dict[str, Any],
    writes: list[PredictedWrite],
    signal_map: dict[str, set[str]],
) -> list[FileScope]:
    entries = _path_entries(repo_graph)
    scopes: list[FileScope] = []
    for write in writes[:MAX_CONTEXT_PREDICTIONS]:
        signals = signal_map.get(write.path, set()) or _signals_for_reason(write.reason)
        is_must_write = _is_must_write(task, write, signals, entries)
        tier = "must_write" if is_must_write else "candidate_context"
        if is_must_write and "must_write_neighbor" in signals:
            tier = "candidate_context"
        if tier == "candidate_context" and not _passes_structural_candidate_context_gate(
            task, repo_graph, write, signals, entries
        ):
            continue
        scopes.append(
            FileScope(
                path=write.path,
                tier=tier,
                score=write.confidence,
                signals=sorted(signals),
                reason=_scope_reason(write, signals, tier),
            )
        )
    tier_order = {"must_write": 0, "candidate_context": 1, "needs_replan": 2}
    return sorted(scopes, key=lambda scope: (tier_order[scope.tier], -scope.score, scope.path))


def _to_predicted_write(scope: FileScope) -> PredictedWrite:
    return PredictedWrite(path=scope.path, confidence=scope.score, reason=scope.reason)


def _estimate_tokens(messages: list[dict[str, str]]) -> int:
    chars = sum(len(message.get("content", "") or "") for message in messages)
    return max(1, chars // 4)


def _value(obj: Any, *keys: str) -> Any:
    for key in keys:
        if isinstance(obj, dict):
            value = obj.get(key)
        else:
            value = getattr(obj, key, None)
        if value not in (None, ""):
            return value
    return None


def _path_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip("./")
    if isinstance(value, dict):
        return str(value.get("path") or value.get("file_path") or value.get("file") or "").strip(
            "./"
        )
    return str(
        getattr(value, "path", None)
        or getattr(value, "file_path", None)
        or getattr(value, "file", "")
    ).strip("./")


def _scip_reference_paths(entity: Any) -> list[str]:
    paths: list[str] = []
    for key in (
        "references",
        "reference_paths",
        "ref_paths",
        "referenced_by",
        "reference_locations",
    ):
        value = _value(entity, key)
        if not value:
            continue
        items = value if isinstance(value, list | tuple | set) else [value]
        paths.extend(path for item in items if (path := _path_value(item)))
    return list(dict.fromkeys(paths))


def _scip_entities_for_path(repo_graph: dict[str, Any], path: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    entities = repo_graph.get("scip_entities")
    if not isinstance(entities, list):
        return out
    for entity in entities:
        symbol = str(_value(entity, "symbol", "scip_symbol", "descriptor") or "")
        name = str(
            _value(entity, "name", "display_name", "identifier") or symbol.rsplit(".", 1)[-1]
        )
        definition_path = _path_value(
            _value(entity, "path", "file_path", "file", "definition_path", "relative_path")
        ) or _path_value(_value(entity, "definition", "definition_location", "location"))
        references = _scip_reference_paths(entity)
        if definition_path == path:
            out.append({"name": name, "symbol": symbol, "role": "definition"})
        elif path in references:
            out.append({"name": name, "symbol": symbol, "role": "reference"})
    return out


def _scope_review_evidence(
    scopes: list[FileScope],
    repo_graph: dict[str, Any],
    repo_root: Path | None,
) -> list[dict[str, Any]]:
    entries = _path_entries(repo_graph)
    out: list[dict[str, Any]] = []
    for scope in scopes[:MAX_CONTEXT_PREDICTIONS]:
        entry = entries.get(scope.path, {})
        snippet = ""
        text = _read_repo_file(repo_root, scope.path, max_chars=6_000)
        if text:
            snippet = "\n".join(
                line.strip()
                for line in text.splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            )[:700]
        out.append(
            {
                "path": scope.path,
                "tier": scope.tier,
                "score": round(scope.score, 3),
                "signals": list(scope.signals),
                "reason": scope.reason[:240],
                "symbols": (entry.get("symbols") or [])[:8],
                "scip_entities": _scip_entities_for_path(repo_graph, scope.path)[:8],
                "exports": (entry.get("exports") or [])[:8],
                "imports": (entry.get("resolved_imports") or entry.get("imports") or [])[:8],
                "importers": (entry.get("importers") or [])[:8],
                "exists": bool(entry),
                "snippet": snippet,
            }
        )
    return out


def _build_scope_review_prompt(
    task: TaskInput,
    scopes: list[FileScope],
    repo_graph: dict[str, Any],
    repo_root: Path | None,
) -> list[dict[str, str]]:
    system = (
        "You are pruning a candidate set retrieved by deterministic indexers. "
        "Output JSON with keep_paths, drop_paths, and optional promote_paths. "
        "Drop only files unrelated to the task. Do not invent paths."
    )
    user = (
        f"Task id: {task.id}\n"
        f"Task: {task.prompt}\n"
        f"Hints: {json.dumps(task.hints.model_dump() if task.hints else {}, sort_keys=True)}\n"
        f"Repo language: {repo_graph.get('language')}\n\n"
        "Candidate file evidence:\n"
        f"{json.dumps(_scope_review_evidence(scopes, repo_graph, repo_root), sort_keys=True)}\n\n"
        "Rules:\n"
        "- keep_paths retain a candidate at its current tier.\n"
        "- drop_paths remove weak or read-only context from the final scope.\n"
        "- promote_paths are only for candidates whose non-review evidence already supports write authority.\n"
        "- Do not add paths outside Candidate file evidence.\n"
        "- Tests are must_write only when the task explicitly asks for tests or the evidence links them to a source change.\n"
        "- Graph/PageRank/BM25-only files should usually be dropped unless they are directly task-grounded.\n"
        "- Scope review is not itself a reason to make a file must_write.\n"
        "Return JSON only."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse_scope_review(raw: str) -> tuple[set[str], set[str], set[str]]:
    text = (raw or "").strip()
    if not text:
        return set(), set(), set()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return set(), set(), set()
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return set(), set(), set()
    if not isinstance(payload, dict):
        return set(), set(), set()

    def _paths(*keys: str) -> set[str]:
        out: set[str] = set()
        for key in keys:
            value = payload.get(key)
            if not isinstance(value, list):
                continue
            out.update(path for item in value if (path := _path_value(item)))
        return out

    keep_paths = _paths("keep_paths", "keep", "candidate_context_paths")
    drop_paths = _paths("drop_paths", "drop")
    promote_paths = _paths("promote_paths", "promote", "must_write_paths")
    return keep_paths, drop_paths, promote_paths


def _add_scope_review_signal(signals: list[str]) -> list[str]:
    if "scope_review" not in signals:
        signals.append("scope_review")
    return sorted(set(signals))


def _scope_review_can_promote(
    task: TaskInput,
    scope: FileScope,
    promoted_score: float,
    entries: dict[str, dict[str, Any]],
) -> bool:
    non_review_signals = set(scope.signals) - {"scope_review"}
    if not non_review_signals:
        return False
    if "must_write_neighbor" in non_review_signals:
        return False
    return _is_must_write(
        task,
        PredictedWrite(
            path=scope.path,
            confidence=promoted_score,
            reason=scope.reason,
        ),
        non_review_signals,
        entries,
    )


def _scope_review_can_drop(scope: FileScope) -> bool:
    if scope.tier == "must_write":
        return False
    evidence_signals = set(scope.signals) - {"approved_replan"}
    high_precision = evidence_signals & _CANDIDATE_HIGH_PRECISION_SIGNALS
    return not high_precision


def _scope_review_ground_truth_count_estimate(scopes: list[FileScope]) -> int:
    # Scope review can only see the pre-review candidate set, so the most
    # conservative estimate is the candidate_context count before any drops.
    return sum(1 for scope in scopes if scope.tier == "candidate_context")


def _scope_review_drop_floor(scopes: list[FileScope]) -> int:
    return max(3, min(_scope_review_ground_truth_count_estimate(scopes), 6))


def _apply_scope_review(
    task: TaskInput,
    repo_graph: dict[str, Any],
    scopes: list[FileScope],
    keep_paths: set[str],
    drop_paths: set[str],
    promote_paths: set[str],
) -> list[FileScope]:
    if not keep_paths and not drop_paths and not promote_paths:
        return scopes
    known_paths = {scope.path for scope in scopes}
    keep_paths &= known_paths
    drop_paths &= known_paths
    promote_paths &= known_paths
    drop_paths -= keep_paths | promote_paths
    entries = _path_entries(repo_graph)
    drop_floor = _scope_review_drop_floor(scopes)
    accepted_drop_paths = {
        scope.path for scope in scopes if scope.path in drop_paths and _scope_review_can_drop(scope)
    }
    remaining_candidate_context = sum(
        1
        for scope in scopes
        if scope.tier == "candidate_context" and scope.path not in accepted_drop_paths
    )
    if accepted_drop_paths and remaining_candidate_context < drop_floor:
        return scopes
    out: list[FileScope] = []
    for scope in scopes:
        if scope.path in drop_paths and _scope_review_can_drop(scope):
            continue
        tier = scope.tier
        signals = list(scope.signals)
        reason = scope.reason
        score = scope.score
        if scope.path in promote_paths:
            promoted_score = max(score, 0.86)
            if _scope_review_can_promote(task, scope, promoted_score, entries):
                tier = "must_write"
                score = promoted_score
                signals = _add_scope_review_signal(signals)
                reason = (
                    f"{reason} Scope review promoted this retrieved candidate "
                    "to must_write after non-review signals met hard-scope rules."
                )
            else:
                signals = _add_scope_review_signal(signals)
                reason = (
                    f"{reason} Scope review requested promotion, but non-review "
                    "signals did not justify must_write."
                )
        elif scope.path in keep_paths:
            signals = _add_scope_review_signal(signals)
            reason = f"{reason} Scope review kept this file at its existing tier."
        elif scope.path in drop_paths:
            signals = _add_scope_review_signal(signals)
            reason = f"{reason} Scope review requested a drop, but protected evidence kept it."
        out.append(
            FileScope(
                path=scope.path,
                tier=tier,
                score=min(1.0, score),
                signals=sorted(set(signals)),
                reason=reason,
            )
        )
    tier_order = {"must_write": 0, "candidate_context": 1, "needs_replan": 2}
    return sorted(out, key=lambda scope: (tier_order[scope.tier], -scope.score, scope.path))


def _review_file_scopes(
    task: TaskInput,
    repo_graph: dict[str, Any],
    llm: LLMProtocol,
    repo_root: Path | None,
    scopes: list[FileScope],
) -> tuple[list[FileScope], int]:
    if not scopes:
        return scopes, 0
    messages = _build_scope_review_prompt(task, scopes, repo_graph, repo_root)
    token_estimate = _estimate_tokens(messages)
    try:
        reply = llm.complete(messages)
    except Exception:
        return scopes, token_estimate
    keep_paths, drop_paths, promote_paths = _parse_scope_review(reply)
    return (
        _apply_scope_review(task, repo_graph, scopes, keep_paths, drop_paths, promote_paths),
        token_estimate,
    )


def _predict_scoped_candidates(
    task: TaskInput,
    repo_graph: dict[str, Any],
    llm: LLMProtocol,
    repo_root: Path | None = None,
) -> tuple[list[PredictedWrite], dict[str, set[str]]]:
    seeds = _static_seed(task.prompt)
    seeds += _symbol_seed(task.prompt, repo_graph)
    if task.hints and task.hints.touches:
        seeds += _topical_seed(list(task.hints.touches), repo_graph)
    seeds += _planner_suspected_file_seed(task)
    seeds += _test_scaffold_seed(task, repo_root)
    seeds += _env_seed(task, repo_root)
    seeds += _sibling_pattern_seed(task, repo_graph)
    seeds += _index_seed(task, repo_root, repo_graph)
    seeds += _test_source_link_seed(task, repo_root, repo_graph, seeds)
    seeds += _auth_role_seed(task, repo_graph)
    seeds += _package_json_seed(task, repo_root)
    seeds += _implied_cluster_seed(task, repo_graph, seeds)
    seeds += _module_name_seed(task, repo_graph)

    signal_map: dict[str, set[str]] = {}
    for pw in seeds:
        signal_map.setdefault(pw.path, set()).update(_signals_for_reason(pw.reason))

    # Deduplicate seeds, keeping the highest-confidence variant per path.
    by_path: dict[str, PredictedWrite] = {}
    for pw in seeds:
        cur = by_path.get(pw.path)
        if cur is None or pw.confidence > cur.confidence:
            by_path[pw.path] = pw
    seeds = list(by_path.values())
    for scope in _llm_seed_expansion(task, repo_graph, {pw.path for pw in seeds}, llm):
        if scope.path not in by_path:
            by_path[scope.path] = PredictedWrite(
                path=scope.path,
                confidence=scope.score,
                reason=LLM_SEED_EXPANSION_REASON,
            )
            signal_map.setdefault(scope.path, set()).update({"planner"})
    seeds = list(by_path.values())

    rerank: list[PredictedWrite] = []
    try:
        reply = llm.complete(_build_prompt(task, repo_graph, seeds))
        rerank = _parse_llm_writes(reply)
    except Exception:
        # Failing closed: keep seeds. Logging is the CLI layer's responsibility.
        rerank = []
    for pw in rerank:
        signals = _signals_for_reason(pw.reason)
        if not _deterministic_reason(pw.reason):
            signals.add("llm")
        signal_map.setdefault(pw.path, set()).update(signals)

    merged = _merge(seeds, rerank)
    merged_by_path = {pw.path: pw for pw in merged}
    llm_paths = {pw.path for pw in rerank}
    _post_llm_must_write_neighbor_expansion(
        task, repo_graph, merged, merged_by_path, signal_map, llm_paths
    )
    merged = sorted(merged_by_path.values(), key=lambda p: (-p.confidence, p.path))
    return merged, signal_map


def predict_file_scopes_with_usage(
    task: TaskInput,
    repo_graph: dict[str, Any],
    llm: LLMProtocol,
    repo_root: Path | None = None,
) -> ScopePrediction:
    """Predict tiered file scope for a task.

    ``must_write`` entries are the only paths intended to become hard
    ``allowed_paths``. Wider graph/index/localization hits stay in
    ``candidate_context`` so workers can see them without receiving write
    authority.
    """

    writes, signal_map = _predict_scoped_candidates(task, repo_graph, llm, repo_root)
    scopes = _build_file_scopes(task, repo_graph, writes, signal_map)
    reviewed_scopes, scope_review_tokens = _review_file_scopes(
        task, repo_graph, llm, repo_root, scopes
    )
    return ScopePrediction(scopes=reviewed_scopes, scope_review_tokens=scope_review_tokens)


def predict_file_scopes(
    task: TaskInput,
    repo_graph: dict[str, Any],
    llm: LLMProtocol,
    repo_root: Path | None = None,
) -> list[FileScope]:
    """Predict tiered file scope for a task."""

    return predict_file_scopes_with_usage(task, repo_graph, llm, repo_root=repo_root).scopes


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
    scopes = predict_file_scopes(task, repo_graph, llm, repo_root=repo_root)
    return [_to_predicted_write(scope) for scope in scopes if scope.tier == "must_write"][
        :MAX_PREDICTIONS
    ]
