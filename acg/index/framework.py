"""Framework-convention indexer for greenfield path prediction."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from acg.schema import PredictedWrite, TaskInput

from .util import repo_files, tokenize

CONFIDENCE = 0.85
ENTITY_STOPWORDS = {
    "api",
    "app",
    "component",
    "controller",
    "endpoint",
    "feature",
    "flow",
    "layout",
    "middleware",
    "model",
    "page",
    "route",
    "router",
    "service",
    "the",
    "view",
}
VERB_WORDS = {"add", "build", "create", "implement", "make", "scaffold", "write"}
ROLE_PATTERNS = {
    "api": re.compile(r"\b(?:api|endpoint|webhook|route)\b", re.IGNORECASE),
    "page": re.compile(r"\bpage\b|/[a-z0-9_/-]+", re.IGNORECASE),
    "layout": re.compile(r"\blayout\b", re.IGNORECASE),
    "middleware": re.compile(r"\bmiddleware\b", re.IGNORECASE),
    "router": re.compile(r"\brouter\b", re.IGNORECASE),
    "model": re.compile(r"\bmodel|schema|table\b", re.IGNORECASE),
    "view": re.compile(r"\bview|endpoint|serializer|api|route\b", re.IGNORECASE),
    "controller": re.compile(r"\bcontroller\b", re.IGNORECASE),
    "route": re.compile(r"\broute|endpoint|api|router\b", re.IGNORECASE),
    "test": re.compile(r"\btests?|specs?|e2e|playwright|jest|vitest|pytest|cypress\b", re.IGNORECASE),
}
ROLE_WORDS = set(ROLE_PATTERNS) | {
    "api",
    "dashboard",
    "endpoint",
    "feature",
    "flow",
    "integration",
    "serializer",
    "subscription",
    "tab",
    "tests",
    "webhook",
}


@dataclass(frozen=True)
class FrameworkContext:
    name: str
    files: set[str]
    repo_root: Path | None


Template = Callable[[str, FrameworkContext], list[str]]


def _exists(repo_root: Path | None, rel_path: str) -> bool:
    return repo_root is not None and (repo_root / rel_path).exists()


def _has_any(files: set[str], names: Iterable[str]) -> bool:
    return any(name in files for name in names)


def detect_frameworks(repo_root: Path | None, repo_graph: dict[str, Any]) -> list[str]:
    """Detect framework fingerprints from config files and graph paths."""

    files = set(repo_files(repo_root, repo_graph))
    frameworks: list[str] = []
    package_json = _read_file(repo_root, "package.json")
    pyproject = _read_file(repo_root, "pyproject.toml")
    requirements = _read_file(repo_root, "requirements.txt")
    pipfile = _read_file(repo_root, "Pipfile")
    gemfile = _read_file(repo_root, "Gemfile")
    pom_xml = _read_file(repo_root, "pom.xml")
    python_manifests = (pyproject + "\n" + requirements + "\n" + pipfile).lower()

    has_next = _has_any(files, ("next.config.js", "next.config.ts", "next.config.mjs")) or (
        '"next"' in package_json
    )
    has_trpc = "@trpc/server" in package_json or any(path.startswith("server/api/") or path.startswith("src/server/api/") for path in files)
    has_prisma = "prisma/schema.prisma" in files or '"prisma"' in package_json
    if has_next:
        frameworks.append("next_app_router")
    if has_next and (has_trpc or has_prisma):
        frameworks.append("t3")
    if "manage.py" in files or "django" in python_manifests:
        frameworks.append("django")
    if (
        "Gemfile" in files
        and ("rails" in gemfile.lower() or "config/routes.rb" in files)
    ) or (repo_root is not None and (repo_root / "Gemfile").exists() and "rails" in gemfile.lower()):
        frameworks.append("rails")
    if (
        _has_any(files, ("vite.config.js", "vite.config.ts", "vite.config.mjs"))
        or '"vite"' in package_json
    ):
        frameworks.append("vite")
    if "fastapi" in python_manifests or any(path.endswith("main.py") for path in files):
        text = python_manifests + "\n".join(_read_file(repo_root, path).lower() for path in files if path.endswith("main.py"))
        if "fastapi" in text:
            frameworks.append("fastapi")
    flask_entrypoints = {
        "app.py",
        "wsgi.py",
        "application.py",
        "main.py",
        "__init__.py",
    }
    flask_candidates = [
        path
        for path in files
        if path.endswith(".py") and path.rsplit("/", 1)[-1] in flask_entrypoints
    ]
    if "flask" in python_manifests or any(
        _flask_signal(_read_file(repo_root, path)) for path in flask_candidates[:8]
    ):
        frameworks.append("flask")
    if "pom.xml" in files and "spring-boot" in pom_xml:
        frameworks.append("spring_boot")
    return frameworks


def _read_file(repo_root: Path | None, rel_path: str) -> str:
    if repo_root is None:
        return ""
    try:
        return (repo_root / rel_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _flask_signal(text: str) -> bool:
    """Heuristic: a module imports Flask or instantiates ``Flask(__name__)``."""

    if not text:
        return False
    lowered = text.lower()
    if "from flask" in lowered or "import flask" in lowered:
        return True
    return "flask(__name__)" in lowered.replace(" ", "")


def _slug(value: str) -> str:
    value = value.strip().strip("/").lower()
    value = re.sub(r"[^a-z0-9/_-]+", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-_/") or "feature"


def _snake(value: str) -> str:
    return _slug(value).replace("-", "_").replace("/", "_")


def _camel(value: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[-_/]+", _slug(value)) if part) or "Feature"


def _extract_entity(prompt: str, role: str) -> str:
    lowered = prompt.lower()
    if role == "model":
        model_match = re.search(
            r"\b([a-zA-Z][\w/-]*)\s+model\b|\bmodel\s+(?:for\s+)?([a-zA-Z][\w/-]*)",
            prompt,
            re.IGNORECASE,
        )
        if model_match:
            entity = _slug(next(group for group in model_match.groups() if group))
            if entity not in ENTITY_STOPWORDS:
                return entity
    route = re.search(r"/([a-z0-9][a-z0-9_/-]*)", lowered)
    if route and role in {"api", "page", "route"}:
        pieces = [piece for piece in route.group(1).split("/") if piece]
        if pieces:
            return _slug(pieces[-1])
    quoted = re.search(r"[`'\"]([a-zA-Z0-9_/-]+)[`'\"]", prompt)
    if quoted:
        return _slug(quoted.group(1))
    role_match = re.search(
        rf"\b([a-zA-Z][\w/-]*)\s+(?:{role}|api|endpoint|page|route|router|controller|model|view|webhook|tab|flow)\b",
        prompt,
        re.IGNORECASE,
    )
    if role_match:
        entity = _slug(role_match.group(1))
        if entity not in ENTITY_STOPWORDS:
            return entity
    after_add = re.search(
        r"\b(?:add|build|create|implement|scaffold|write)\s+(?:a|an|the)?\s*([a-zA-Z][\w/-]*)",
        prompt,
        re.IGNORECASE,
    )
    if after_add:
        entity = _slug(after_add.group(1))
        if entity not in ENTITY_STOPWORDS | VERB_WORDS:
            return entity
    for token in tokenize(prompt):
        if token not in ENTITY_STOPWORDS | ROLE_WORDS | VERB_WORDS and len(token) > 2:
            return _slug(token)
    return "feature"


def _app_prefix(ctx: FrameworkContext) -> str:
    return "src/app" if any(path.startswith("src/app/") for path in ctx.files) or _exists(ctx.repo_root, "src") else "app"


def _src_prefix(ctx: FrameworkContext, rel: str) -> str:
    src_rel = f"src/{rel}"
    if any(path == src_rel or path.startswith(f"src/{rel.rstrip('/')}/") for path in ctx.files):
        return src_rel
    return rel


def _django_app(ctx: FrameworkContext, entity: str) -> str:
    apps = sorted(
        {
            path.split("/")[0]
            for path in ctx.files
            if path.endswith(("views.py", "models.py", "urls.py")) and "/" in path
        }
    )
    if len(apps) == 1:
        return apps[0]
    for app in apps:
        if app in entity:
            return app
    return apps[0] if apps else _snake(entity)


def _spring_package(ctx: FrameworkContext) -> str:
    for path in sorted(ctx.files):
        if path.startswith("src/main/java/") and path.endswith(".java"):
            pieces = path.split("/")
            if len(pieces) > 4:
                return "/".join(pieces[: len(pieces) - 1])
    return "src/main/java/app"


def _next_api(entity: str, ctx: FrameworkContext) -> list[str]:
    return [f"{_app_prefix(ctx)}/api/{_slug(entity)}/route.ts"]


def _next_page(entity: str, ctx: FrameworkContext) -> list[str]:
    return [f"{_app_prefix(ctx)}/{_slug(entity)}/page.tsx"]


def _next_layout(entity: str, ctx: FrameworkContext) -> list[str]:
    return [f"{_app_prefix(ctx)}/{_slug(entity)}/layout.tsx"]


def _next_middleware(_: str, ctx: FrameworkContext) -> list[str]:
    return [_src_prefix(ctx, "middleware.ts")]


def _t3_router(entity: str, ctx: FrameworkContext) -> list[str]:
    return [_src_prefix(ctx, f"server/api/routers/{_snake(entity)}.ts")]


def _t3_model(_: str, _ctx: FrameworkContext) -> list[str]:
    return ["prisma/schema.prisma"]


def _django_view(entity: str, ctx: FrameworkContext) -> list[str]:
    app = _django_app(ctx, entity)
    return [f"{app}/views.py", f"{app}/serializers.py", f"{app}/urls.py"]


def _django_model(entity: str, ctx: FrameworkContext) -> list[str]:
    return [f"{_django_app(ctx, entity)}/models.py"]


def _rails_controller(entity: str, _ctx: FrameworkContext) -> list[str]:
    return [f"app/controllers/{_snake(entity)}_controller.rb"]


def _rails_model(entity: str, _ctx: FrameworkContext) -> list[str]:
    return [f"app/models/{_snake(entity)}.rb"]


def _fastapi_route(entity: str, ctx: FrameworkContext) -> list[str]:
    if any(path.startswith("app/routers/") for path in ctx.files) or _exists(ctx.repo_root, "app/routers"):
        return [f"app/routers/{_snake(entity)}.py"]
    return [f"app/api/{_snake(entity)}.py"]


def _flask_blueprint_dirs(ctx: FrameworkContext) -> list[str]:
    """Return repo-rooted directories likely to host Flask Blueprint modules."""

    candidates: set[str] = set()
    for path in ctx.files:
        if not path.endswith(".py"):
            continue
        parts = path.split("/")
        for idx, part in enumerate(parts[:-1]):
            if part in {"blueprints", "views", "routes"}:
                candidates.add("/".join(parts[: idx + 1]))
    return sorted(candidates)


def _flask_route(entity: str, ctx: FrameworkContext) -> list[str]:
    blueprint_dirs = _flask_blueprint_dirs(ctx)
    snake = _snake(entity)
    if blueprint_dirs:
        return [f"{blueprint_dirs[0]}/{snake}.py", f"tests/test_{snake}.py"]
    if any(path == "app.py" for path in ctx.files):
        return ["app.py", f"tests/test_{snake}.py"]
    if any(path.startswith("app/") for path in ctx.files):
        return [f"app/{snake}.py", f"tests/test_{snake}.py"]
    return [f"{snake}.py", f"tests/test_{snake}.py"]


def _spring_controller(entity: str, ctx: FrameworkContext) -> list[str]:
    return [f"{_spring_package(ctx)}/controllers/{_camel(entity)}Controller.java"]


def _vite_page(entity: str, ctx: FrameworkContext) -> list[str]:
    return [_src_prefix(ctx, f"pages/{_camel(entity)}.tsx")]


TEMPLATES: dict[str, dict[str, Template]] = {
    "next_app_router": {
        "api": _next_api,
        "page": _next_page,
        "layout": _next_layout,
        "middleware": _next_middleware,
    },
    "t3": {
        "router": _t3_router,
        "model": _t3_model,
        "api": _next_api,
        "page": _next_page,
    },
    "django": {
        "view": _django_view,
        "api": _django_view,
        "route": _django_view,
        "model": _django_model,
    },
    "rails": {
        "controller": _rails_controller,
        "model": _rails_model,
    },
    "fastapi": {
        "route": _fastapi_route,
        "api": _fastapi_route,
    },
    "flask": {
        "route": _flask_route,
        "api": _flask_route,
    },
    "spring_boot": {
        "controller": _spring_controller,
    },
    "vite": {
        "page": _vite_page,
    },
}


def _roles_for_prompt(prompt: str, framework: str) -> list[str]:
    roles: list[str] = []
    for role, pattern in ROLE_PATTERNS.items():
        if pattern.search(prompt) and role in TEMPLATES.get(framework, {}):
            roles.append(role)
    if "api" in roles and "route" in TEMPLATES.get(framework, {}) and "route" not in roles:
        roles.append("route")
    if not roles:
        for default in ("api", "page", "view", "route", "controller", "router"):
            if default in TEMPLATES.get(framework, {}):
                roles.append(default)
                break
    return roles


class FrameworkIndexer:
    """Predict greenfield paths from framework fingerprints and conventions."""

    name = "framework"

    def predict(
        self,
        task: TaskInput,
        repo_root: Path | None,
        repo_graph: dict[str, Any],
    ) -> list[PredictedWrite]:
        files = set(repo_files(repo_root, repo_graph))
        predictions: dict[str, PredictedWrite] = {}
        for framework in detect_frameworks(repo_root, repo_graph):
            ctx = FrameworkContext(framework, files, repo_root)
            for role in _roles_for_prompt(task.prompt, framework):
                entity = _extract_entity(task.prompt, role)
                for path in TEMPLATES[framework][role](entity, ctx):
                    predictions.setdefault(
                        path,
                        PredictedWrite(
                            path=path,
                            confidence=CONFIDENCE,
                            reason=f"{framework} convention for {role} task.",
                        ),
                    )
        return list(predictions.values())


def predict(
    task: TaskInput,
    repo_root: Path | None,
    repo_graph: dict[str, Any],
) -> list[PredictedWrite]:
    return FrameworkIndexer().predict(task, repo_root, repo_graph)
