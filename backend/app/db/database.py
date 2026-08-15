import logging
import re
from collections.abc import AsyncIterator
from time import perf_counter
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()

_engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 15},
    poolclass=StaticPool,
)


@event.listens_for(_engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_connection: Any, _connection_record: Any) -> None:  # pyright: ignore[reportUnusedFunction]
    """Enable WAL mode and busy timeout on every SQLite connection.

    WAL mode allows concurrent readers alongside a single writer, which
    prevents the "database is locked" errors that occur when multiple
    async tasks (hook events, pollers, git service) write concurrently.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _sql_operation(statement: str) -> str:
    """Return a short parameter-free operation label for diagnostics."""
    tokens = re.sub(r"\s+", " ", statement).strip().split(" ")
    if not tokens:
        return "unknown"
    verb = tokens[0].lower()
    for marker in ("from", "into", "update", "join"):
        if marker in tokens[1:]:
            index = tokens.index(marker)
            if index + 1 < len(tokens):
                return f"{verb} {tokens[index + 1].strip('\\\"`[]')}"[:80]
    return verb


@event.listens_for(_engine.sync_engine, "before_cursor_execute")
def _slow_query_start(
    connection: Any,
    _cursor: Any,
    statement: str,
    _parameters: Any,
    _context: Any,
    _executemany: bool,
) -> None:
    connection.info.setdefault("_slow_query_starts", []).append(
        (perf_counter(), _sql_operation(statement))
    )


@event.listens_for(_engine.sync_engine, "after_cursor_execute")
def _slow_query_end(
    connection: Any,
    _cursor: Any,
    _statement: str,
    _parameters: Any,
    _context: Any,
    _executemany: bool,
) -> None:
    starts = connection.info.get("_slow_query_starts")
    if not starts:
        return
    started, operation = starts.pop()
    duration_ms = (perf_counter() - started) * 1000
    if duration_ms >= 500:
        logger.warning(
            "sqlite_slow_query operation=%s duration_ms=%.1f",
            operation,
            duration_ms,
        )


_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""

    pass


def get_engine() -> AsyncEngine:
    """Get the current database engine."""
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get the current async session factory."""
    return _session_factory


def override_engine(new_engine: AsyncEngine) -> None:
    """Override the database engine and session factory for testing."""
    global _engine, _session_factory
    _engine = new_engine
    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_db() -> AsyncIterator[AsyncSession]:
    """Dependency for getting a database session.

    Rolls back automatically on any exception raised by the route body so
    callers no longer need a per-route ``try/except`` just to call
    ``db.rollback()`` before re-raising (ARC-024).
    """
    async with _session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


class AsyncSessionLocal:
    """Context manager for database sessions."""

    def __init__(self) -> None:
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> AsyncSession:
        self._session = _session_factory()
        return self._session

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._session:
            if exc_type is not None:
                await self._session.rollback()
            await self._session.close()
