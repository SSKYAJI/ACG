"""Tests for the lockfile-aware write validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from acg.enforce import (
    EXIT_ALLOWED,
    EXIT_BLOCKED,
    EXIT_USER_ERROR,
    cli_validate,
    load_lock,
    validate_write,
)


@pytest.fixture
def demo_lock_path() -> Path:
    return Path(__file__).resolve().parent.parent / "examples" / "lockfile.dag.example.json"


def test_settings_can_write_its_own_page(demo_lock_path: Path) -> None:
    lock = load_lock(demo_lock_path)
    allowed, reason = validate_write(lock, "settings", "src/app/settings/page.tsx")
    assert allowed
    assert reason is None


def test_settings_cannot_write_auth_config(demo_lock_path: Path) -> None:
    lock = load_lock(demo_lock_path)
    allowed, reason = validate_write(lock, "settings", "src/server/auth/config.ts")
    assert not allowed
    assert reason and "src/server/auth/config.ts" in reason


def test_oauth_can_write_nested_route(demo_lock_path: Path) -> None:
    lock = load_lock(demo_lock_path)
    allowed, _ = validate_write(lock, "oauth", "src/app/api/auth/[...nextauth]/route.ts")
    assert allowed


def test_unknown_task_raises(demo_lock_path: Path) -> None:
    lock = load_lock(demo_lock_path)
    with pytest.raises(KeyError):
        validate_write(lock, "phantom", "anything.ts")


def test_cli_validate_exit_codes(demo_lock_path: Path) -> None:
    code, _ = cli_validate(demo_lock_path, "settings", "src/app/settings/page.tsx")
    assert code == EXIT_ALLOWED

    code, _ = cli_validate(demo_lock_path, "settings", "src/server/auth/config.ts")
    assert code == EXIT_BLOCKED

    code, _ = cli_validate(demo_lock_path, "ghost", "anything.ts")
    assert code == EXIT_USER_ERROR
