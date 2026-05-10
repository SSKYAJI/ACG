from __future__ import annotations

from pathlib import Path

from acg.index.framework import FrameworkIndexer, detect_frameworks
from acg.schema import TaskInput


def touch(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def paths_for(root: Path, prompt: str) -> set[str]:
    task = TaskInput(id="task", prompt=prompt)
    return {write.path for write in FrameworkIndexer().predict(task, root, {})}


def test_next_app_router_api_prefers_src_app(tmp_path: Path) -> None:
    touch(tmp_path, "next.config.ts")
    touch(tmp_path, "src/app/page.tsx")

    assert "src/app/api/billing/route.ts" in paths_for(tmp_path, "Add billing API endpoint")


def test_next_page_and_layout_conventions(tmp_path: Path) -> None:
    touch(tmp_path, "next.config.js")

    assert "app/settings/page.tsx" in paths_for(tmp_path, "Add settings page")
    assert "app/dashboard/layout.tsx" in paths_for(tmp_path, "Add dashboard layout")


def test_next_middleware_convention(tmp_path: Path) -> None:
    touch(tmp_path, "next.config.js")

    assert "middleware.ts" in paths_for(tmp_path, "Add middleware for auth redirects")


def test_t3_router_and_prisma_model(tmp_path: Path) -> None:
    touch(tmp_path, "next.config.js")
    touch(tmp_path, "package.json", '{"dependencies":{"@trpc/server":"latest","prisma":"latest"}}')

    paths = paths_for(tmp_path, "Add billing router and subscription model")
    assert "server/api/routers/billing.ts" in paths
    assert "prisma/schema.prisma" in paths
    assert "t3" in detect_frameworks(tmp_path, {})


def test_django_view_and_model_use_existing_app(tmp_path: Path) -> None:
    touch(tmp_path, "manage.py")
    touch(tmp_path, "shop/views.py")
    touch(tmp_path, "shop/models.py")

    paths = paths_for(tmp_path, "Add checkout endpoint and order model")
    assert {"shop/views.py", "shop/serializers.py", "shop/urls.py", "shop/models.py"} <= paths


def test_rails_controller_and_model(tmp_path: Path) -> None:
    touch(tmp_path, "Gemfile", 'gem "rails"')

    paths = paths_for(tmp_path, "Add billing controller and invoice model")
    assert "app/controllers/billing_controller.rb" in paths
    assert "app/models/invoice.rb" in paths


def test_fastapi_route(tmp_path: Path) -> None:
    touch(tmp_path, "pyproject.toml", 'dependencies = ["fastapi"]')
    touch(tmp_path, "app/routers/__init__.py")

    assert "app/routers/payments.py" in paths_for(tmp_path, "Add payments route")


def test_no_fingerprint_returns_no_predictions(tmp_path: Path) -> None:
    assert paths_for(tmp_path, "Add billing API endpoint") == set()


def test_flask_route_with_blueprints_dir(tmp_path: Path) -> None:
    touch(tmp_path, "requirements.txt", "flask==3.0\n")
    touch(tmp_path, "app/__init__.py", "from flask import Flask\napp = Flask(__name__)\n")
    touch(tmp_path, "app/blueprints/__init__.py")
    touch(tmp_path, "app/blueprints/users.py", "from flask import Blueprint\nbp = Blueprint('u', __name__)\n")

    paths = paths_for(tmp_path, "Add billing route")
    assert "flask" in detect_frameworks(tmp_path, {})
    assert "app/blueprints/billing.py" in paths
    assert "tests/test_billing.py" in paths


def test_flask_route_falls_back_to_app_py(tmp_path: Path) -> None:
    # Single-module Flask app: no blueprints dir, just app.py.
    touch(
        tmp_path,
        "app.py",
        "from flask import Flask\napp = Flask(__name__)\n",
    )
    touch(tmp_path, "requirements.txt", "flask\n")

    paths = paths_for(tmp_path, "Add health route")
    assert "flask" in detect_frameworks(tmp_path, {})
    assert "app.py" in paths


def test_flask_detected_from_module_signal_only(tmp_path: Path) -> None:
    # No requirements/pyproject mention; Flask detected from import signal.
    touch(tmp_path, "wsgi.py", "from flask import Flask\napp = Flask(__name__)\n")
    assert "flask" in detect_frameworks(tmp_path, {})
