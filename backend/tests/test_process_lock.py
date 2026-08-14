"""Cross-process database ownership lock tests."""

from pathlib import Path

import pytest

from app.db.database import get_engine
from app.db.process_lock import DatabaseProcessLock, sqlite_path_from_url


def test_memory_database_has_no_process_lock() -> None:
    assert sqlite_path_from_url("sqlite+aiosqlite:///:memory:") is None


def test_backend_tests_use_an_isolated_memory_database() -> None:
    assert get_engine().url.database == ":memory:"
    assert "visualizer.db" not in str(get_engine().url)


def test_second_backend_cannot_acquire_the_same_database_lock() -> None:
    database = Path(__file__).parent / ".process-lock-test.sqlite"
    first = DatabaseProcessLock(database)
    second = DatabaseProcessLock(database)
    try:
        first.acquire()
        with pytest.raises(RuntimeError, match="already using"):
            second.acquire()
    finally:
        second.release()
        first.release()
        database.unlink(missing_ok=True)
        database.with_name(f"{database.name}.lock").unlink(missing_ok=True)
