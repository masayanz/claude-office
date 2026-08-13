"""Inline SQLite schema migration.

Moved out of ``app.main`` in ARC-023. The migration runs on startup against
the SQLite database to add columns introduced after the initial schema.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def migrate_schema(conn: AsyncConnection) -> None:
    """Add columns to existing tables that were added after initial schema.

    Only runs for SQLite. Uses ALTER TABLE ADD COLUMN which is a no-op if
    the column already exists (checked via PRAGMA first).

    NOTE: This project intentionally uses inline schema migration instead of
    Alembic.  The backend is SQLite-only and single-instance, so the lightweight
    PRAGMA-based approach is sufficient.  Alembic was removed as a dependency
    (see pyproject.toml).
    """
    dialect = conn.dialect.name
    if dialect != "sqlite":
        return

    # ``create_all`` covers new databases, but older running installations
    # may have been created before Replay models existed.  Keep this explicit
    # and idempotent so a normal Backend restart repairs that schema without
    # requiring Alembic or a destructive database reset.
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS replay_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key VARCHAR NOT NULL UNIQUE,
                source_event_id INTEGER,
                session_id VARCHAR NOT NULL,
                timestamp DATETIME NOT NULL,
                event_type VARCHAR NOT NULL,
                agent_id VARCHAR,
                agent_name VARCHAR,
                agent_type VARCHAR,
                source VARCHAR,
                project_name VARCHAR,
                model VARCHAR,
                tool_name VARCHAR,
                tool_use_id VARCHAR,
                error_type VARCHAR,
                safe_state VARCHAR NOT NULL DEFAULT 'idle',
                safe_data JSON,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS replay_session_tombstones (
                session_id VARCHAR PRIMARY KEY,
                deleted_at DATETIME NOT NULL
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS replay_migrations (
                key VARCHAR PRIMARY KEY,
                completed_at DATETIME NOT NULL
            )
            """
        )
    )
    for index_sql in (
        "CREATE INDEX IF NOT EXISTS ix_events_session_id ON events(session_id)",
        "CREATE INDEX IF NOT EXISTS ix_events_session_timestamp "
        "ON events(session_id, timestamp)",
        "CREATE INDEX IF NOT EXISTS ix_events_session_replay_order "
        "ON events(session_id, timestamp, id)",
        "CREATE INDEX IF NOT EXISTS ix_replay_events_event_key ON replay_events(event_key)",
        "CREATE INDEX IF NOT EXISTS ix_replay_events_source_event_id "
        "ON replay_events(source_event_id)",
        "CREATE INDEX IF NOT EXISTS ix_replay_events_session_id ON replay_events(session_id)",
        "CREATE INDEX IF NOT EXISTS ix_replay_events_timestamp ON replay_events(timestamp)",
        "CREATE INDEX IF NOT EXISTS ix_replay_events_session_replay_order "
        "ON replay_events(session_id, timestamp, source_event_id, id)",
    ):
        await conn.execute(text(index_sql))

    new_columns: dict[str, str] = {
        "label": "TEXT DEFAULT NULL",
        "display_name": "TEXT DEFAULT NULL",
        "floor_id": "TEXT DEFAULT NULL",
        "room_id": "TEXT DEFAULT NULL",
        "team_name": "TEXT DEFAULT NULL",
        "teammate_name": "TEXT DEFAULT NULL",
        "is_lead": "BOOLEAN DEFAULT 0",
    }

    result = await conn.execute(text("PRAGMA table_info(sessions)"))
    existing = {row[1] for row in result.fetchall()}

    for col_name, col_def in new_columns.items():
        if col_name not in existing:
            await conn.execute(text(f"ALTER TABLE sessions ADD COLUMN {col_name} {col_def}"))
