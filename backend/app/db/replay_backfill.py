"""Idempotent migration of legacy LIVE events into Replay metadata.

Older installations have a populated ``events`` table but predate the
privacy-safe ``replay_events`` table.  Replay must be able to use those
events without exposing their original payloads, so this module copies only
the allow-listed projection produced by :mod:`app.core.replay`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, outerjoin, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.replay import safe_event_payload_from_legacy
from app.db.database import AsyncSessionLocal
from app.db.models import (
    EventRecord,
    ReplayEventRecord,
    ReplayMigration,
    ReplaySessionTombstone,
    SessionRecord,
)
from app.models.events import EventType
from app.services.app_settings import load_settings

logger = logging.getLogger(__name__)

_BACKFILL_BATCH_SIZE = 1_000
_BACKFILL_KEY = "legacy_events_v1"


async def backfill_replay_history(
    *,
    batch_size: int = _BACKFILL_BATCH_SIZE,
    session_ids: set[str] | None = None,
) -> dict[str, int]:
    """Backfill safe Replay rows from existing events exactly once.

    ``event_key`` is unique, so a second startup only performs harmless
    conflict checks.  Tombstoned sessions are intentionally excluded: a user
    who deleted Replay history must not have it silently rebuilt from LIVE
    rows.  Invalid legacy events are skipped and never copied verbatim.
    """
    settings, _ = load_settings()
    if not bool(settings.get("replay_history_enabled", True)):
        return {"inserted": 0, "skipped": 0}
    if session_ids is not None and not session_ids:
        return {"inserted": 0, "skipped": 0}

    inserted = 0
    skipped = 0
    pending: list[dict[str, Any]] = []
    tombstoned = select(1).where(
        ReplaySessionTombstone.session_id == EventRecord.session_id
    ).exists()
    last_event_id = 0
    async with AsyncSessionLocal() as db:
        if session_ids is None:
            completed = await db.scalar(
                select(ReplayMigration.key).where(ReplayMigration.key == _BACKFILL_KEY)
            )
            missing_legacy_row = await db.scalar(
                select(EventRecord.id)
                .select_from(
                    outerjoin(
                        EventRecord,
                        ReplayEventRecord,
                        ReplayEventRecord.source_event_id == EventRecord.id,
                    )
                )
                .where(
                    ReplayEventRecord.id.is_(None),
                    ~tombstoned,
                    EventRecord.event_type.in_(
                        event_type.value for event_type in EventType
                    ),
                )
                .limit(1)
            )
            if completed is not None and missing_legacy_row is None:
                return {"inserted": 0, "skipped": 0}
        while True:
            statement = (
                select(
                    EventRecord.id,
                    EventRecord.session_id,
                    EventRecord.timestamp,
                    EventRecord.event_type,
                    func.json_extract(EventRecord.data, "$.agent_id").label("agent_id"),
                    func.json_extract(EventRecord.data, "$.agent_name").label("agent_name"),
                    func.json_extract(EventRecord.data, "$.agent_type").label("agent_type"),
                    func.json_extract(EventRecord.data, "$.source").label("source"),
                    func.json_extract(EventRecord.data, "$.project_name").label("project_name"),
                    func.json_extract(EventRecord.data, "$.model").label("model"),
                    func.json_extract(EventRecord.data, "$.tool_name").label("tool_name"),
                    func.json_extract(EventRecord.data, "$.tool_use_id").label("tool_use_id"),
                    func.json_extract(EventRecord.data, "$.error_type").label("error_type"),
                    func.json_extract(EventRecord.data, "$.restored").label("restored"),
                )
                .select_from(
                    outerjoin(
                        EventRecord,
                        ReplayEventRecord,
                        ReplayEventRecord.source_event_id == EventRecord.id,
                    )
                )
                .join(SessionRecord, SessionRecord.id == EventRecord.session_id)
                .where(
                    ReplayEventRecord.id.is_(None),
                    EventRecord.id > last_event_id,
                    ~tombstoned,
                )
                .order_by(EventRecord.id.asc())
            )
            if session_ids is not None:
                statement = statement.where(EventRecord.session_id.in_(session_ids))
            statement = statement.limit(batch_size)
            rows = (await db.execute(statement)).all()
            if not rows:
                break
            for row in rows:
                payload = safe_event_payload_from_legacy(
                    session_id=row.session_id,
                    timestamp=row.timestamp,
                    event_type=row.event_type,
                    source_event_id=row.id,
                    agent_id=row.agent_id,
                    agent_name=row.agent_name,
                    agent_type=row.agent_type,
                    source=row.source,
                    project_name=row.project_name,
                    model=row.model,
                    tool_name=row.tool_name,
                    tool_use_id=row.tool_use_id,
                    error_type=row.error_type,
                    restored=row.restored,
                )
                if payload is None:
                    skipped += 1
                    logger.warning(
                        "Replay backfill skipped event session=%s type=%s",
                        row.session_id,
                        row.event_type,
                    )
                    continue
                pending.append(payload)
            last_event_id = rows[-1].id
            if pending:
                inserted += await _insert_batch(db, pending)
                pending.clear()
            await db.commit()
            # Give live event ingestion and the event loop a scheduling point
            # between bounded write transactions.
            await asyncio.sleep(0)

        if session_ids is None:
            marker_insert = sqlite_insert(ReplayMigration).values(
                key=_BACKFILL_KEY,
                completed_at=datetime.now(UTC),
            )
            marker_insert = marker_insert.on_conflict_do_nothing(index_elements=["key"])
            await db.execute(marker_insert)
            await db.commit()

    if inserted or skipped:
        logger.info(
            "Replay backfill complete inserted=%d skipped=%d",
            inserted,
            skipped,
        )
    return {"inserted": inserted, "skipped": skipped}


async def _insert_batch(db: Any, payloads: list[dict[str, Any]]) -> int:
    """Insert one bounded batch and return rows newly accepted by SQLite."""
    statement = sqlite_insert(ReplayEventRecord).values(payloads)
    statement = statement.on_conflict_do_nothing(index_elements=["event_key"])
    result = await db.execute(statement)
    return int(getattr(result, "rowcount", 0) or 0)
