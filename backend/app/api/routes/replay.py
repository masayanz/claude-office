"""Privacy-safe Replay API.

The LIVE event table is an internal restoration source. This router exposes
only the allow-listed Replay metadata and redacted character state projection.
"""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.replay import (
    normalized_event_from_record,
    normalized_event_from_replay_record,
    redacted_game_state,
    safe_display_text,
    safe_event_from_normalized,
    safe_event_from_record,
)
from app.core.state_machine import StateMachine
from app.db.database import get_db
from app.db.models import (
    EventRecord,
    ReplayEventRecord,
    ReplaySessionTombstone,
    SessionRecord,
)
from app.models.events import EventType
from app.services.app_settings import load_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/replay", tags=["replay"])
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_REPLAY_EVENT_TYPES = tuple(event_type.value for event_type in EventType)
_REPLAY_PAGE_SIZE = 2_000
_REPLAY_CACHE_SIZE = 32


@dataclass
class _ReplayCheckpoint:
    offset: int
    machine: StateMachine


# Sequential prefetches reuse the state at the end of the previous chunk.  A
# bounded process-local cache avoids replaying the whole session for every
# page, while a cache miss remains correct because the state machine can be
# rebuilt from the ordered database rows.
_REPLAY_CHECKPOINTS: dict[str, list[_ReplayCheckpoint]] = {}


def _require_session_id(session_id: str) -> None:
    if not _SESSION_ID.fullmatch(session_id):
        raise HTTPException(status_code=400, detail="Invalid session ID")


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


async def _raw_events(db: AsyncSession, session_id: str) -> list[EventRecord]:
    result = await db.execute(
        select(EventRecord)
        .where(EventRecord.session_id == session_id)
        .order_by(EventRecord.timestamp.asc(), EventRecord.id.asc())
    )
    return list(result.scalars().all())


async def _safe_records(db: AsyncSession, session_id: str) -> list[ReplayEventRecord]:
    result = await db.execute(
        select(ReplayEventRecord)
        .where(ReplayEventRecord.session_id == session_id)
        .order_by(ReplayEventRecord.timestamp.asc(), ReplayEventRecord.id.asc())
    )
    return list(result.scalars().all())


async def _session_record(db: AsyncSession, session_id: str) -> SessionRecord:
    result = await db.execute(select(SessionRecord).where(SessionRecord.id == session_id))
    record = result.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Replay session not found")
    return record


def _replay_order(*, safe: bool) -> tuple[Any, ...]:
    if safe:
        return (
            ReplayEventRecord.timestamp.asc(),
            func.coalesce(ReplayEventRecord.source_event_id, ReplayEventRecord.id).asc(),
            ReplayEventRecord.id.asc(),
        )
    return (EventRecord.timestamp.asc(), EventRecord.id.asc())


async def _raw_event_count(db: AsyncSession, session_id: str) -> int:
    return int(
        await db.scalar(
            select(func.count(EventRecord.id)).where(
                EventRecord.session_id == session_id,
                EventRecord.event_type.in_(_REPLAY_EVENT_TYPES),
            )
        )
        or 0
    )


def _raw_duplicate_key_columns() -> tuple[Any, ...]:
    """Return the stable lifecycle fields used to compact legacy duplicates."""
    return (
        EventRecord.event_type,
        EventRecord.timestamp,
        func.coalesce(func.json_extract(EventRecord.data, "$.agent_id"), "main"),
        func.coalesce(func.json_extract(EventRecord.data, "$.tool_use_id"), ""),
    )


async def _raw_compacted_count(db: AsyncSession, session_id: str) -> int:
    """Count Replay-visible legacy rows after duplicate suppression.

    Hook + JSONL + restore can leave many identical LIVE rows.  Keep those
    rows for restoration/audit, but do not make Replay render them repeatedly.
    SQLite performs the de-duplication by retaining the first row for each
    stable lifecycle key.
    """
    row_number = func.row_number().over(
        partition_by=_raw_duplicate_key_columns(), order_by=EventRecord.id.asc()
    ).label("replay_row_number")
    compacted = (
        select(EventRecord.id, row_number)
        .where(
            EventRecord.session_id == session_id,
            EventRecord.event_type.in_(_REPLAY_EVENT_TYPES),
        )
        .subquery()
    )
    return int(
        await db.scalar(
            select(func.count(compacted.c.id)).where(compacted.c.replay_row_number == 1)
        )
        or 0
    )


async def _raw_compacted_page(
    db: AsyncSession, session_id: str, *, offset: int, limit: int
) -> list[EventRecord]:
    row_number = func.row_number().over(
        partition_by=_raw_duplicate_key_columns(), order_by=EventRecord.id.asc()
    ).label("replay_row_number")
    compacted = (
        select(EventRecord.id, EventRecord.timestamp, row_number)
        .where(
            EventRecord.session_id == session_id,
            EventRecord.event_type.in_(_REPLAY_EVENT_TYPES),
        )
        .subquery()
    )
    id_result = await db.execute(
        select(compacted.c.id)
        .where(compacted.c.replay_row_number == 1)
        .order_by(compacted.c.timestamp.asc(), compacted.c.id.asc())
        .offset(offset)
        .limit(limit)
    )
    ids = [int(value) for value in id_result.scalars().all()]
    if not ids:
        return []
    result = await db.execute(select(EventRecord).where(EventRecord.id.in_(ids)))
    by_id = {row.id: row for row in result.scalars().all()}
    return [by_id[event_id] for event_id in ids if event_id in by_id]


async def _safe_event_count(db: AsyncSession, session_id: str) -> int:
    return int(
        await db.scalar(
            select(func.count(ReplayEventRecord.id)).where(
                ReplayEventRecord.session_id == session_id
            )
        )
        or 0
    )


async def _replay_summary(db: AsyncSession, record: SessionRecord) -> dict[str, Any] | None:
    """Build session metadata using bounded aggregate/column queries only."""
    if await db.get(ReplaySessionTombstone, record.id) is not None:
        return None
    safe_count = await _safe_event_count(db, record.id)
    # A populated privacy-safe table is the Replay index.  It already has
    # duplicate suppression and lets metadata render without scanning the
    # legacy JSON payload table.  Sessions not yet backfilled use the compact
    # raw fallback below.
    if safe_count > 0:
        raw_count = safe_count
    else:
        raw_count = await _raw_event_count(db, record.id)
    if raw_count == 0:
        return None

    use_safe = safe_count > 0
    if use_safe:
        base = ReplayEventRecord.session_id == record.id
        count = safe_count
        started = await db.scalar(select(func.min(ReplayEventRecord.timestamp)).where(base))
        ended = await db.scalar(select(func.max(ReplayEventRecord.timestamp)).where(base))
        first_result = await db.execute(
            select(
                ReplayEventRecord.source,
                ReplayEventRecord.model,
                ReplayEventRecord.project_name,
            )
            .where(base)
            .order_by(*_replay_order(safe=True))
            .limit(1)
        )
        first = first_result.first()
        max_agents = 0
        if count <= 5_000:
            max_agents = int(
                await db.scalar(
                    select(func.count(func.distinct(ReplayEventRecord.agent_id))).where(
                        base,
                        ReplayEventRecord.event_type == EventType.SUBAGENT_START.value,
                        ReplayEventRecord.agent_id.is_not(None),
                    )
                )
                or 0
            )
        source_values = [first[0]] if first and first[0] else []
        model_values = [first[1]] if first and first[1] else []
        project_values = [first[2]] if first and first[2] else []
    else:
        base = EventRecord.session_id == record.id
        valid = EventRecord.event_type.in_(_REPLAY_EVENT_TYPES)
        count = raw_count
        started = await db.scalar(select(func.min(EventRecord.timestamp)).where(base, valid))
        ended = await db.scalar(select(func.max(EventRecord.timestamp)).where(base, valid))
        first_result = await db.execute(
            select(
                func.json_extract(EventRecord.data, "$.source"),
                func.json_extract(EventRecord.data, "$.model"),
                func.json_extract(EventRecord.data, "$.project_name"),
            )
            .where(base, valid)
            .order_by(*_replay_order(safe=False))
            .limit(1)
        )
        first = first_result.first()
        # Calculating the exact peak over legacy JSON rows would make the
        # metadata request scan the same large session we are trying to avoid.
        # The async Replay backfill can populate the safe projection later;
        # small sessions still get an exact distinct-start count immediately.
        max_agents = 0
        if count <= 5_000:
            max_agents = int(
                await db.scalar(
                    select(
                        func.count(
                            func.distinct(func.json_extract(EventRecord.data, "$.agent_id"))
                        )
                    )
                    .where(
                        base,
                        valid,
                        EventRecord.event_type == EventType.SUBAGENT_START.value,
                        func.json_extract(EventRecord.data, "$.agent_id").is_not(None),
                    )
                )
                or 0
            )
        source_values = [first[0]] if first and first[0] else []
        model_values = [first[1]] if first and first[1] else []
        project_values = [first[2]] if first and first[2] else []

    sources = sorted(
        {safe_display_text(value) for value in source_values if safe_display_text(value)}
    )
    models = sorted(
        {safe_display_text(value) for value in model_values if safe_display_text(value)}
    )
    projects = [safe_display_text(value) for value in project_values]
    project_name = next((value for value in projects if value), None) or safe_display_text(
        record.project_name
    )
    started_at = _utc(started or record.created_at)
    ended_at = _utc(ended or record.updated_at)
    return {
        "id": record.id,
        "projectName": project_name,
        "source": sources[0] if len(sources) == 1 else (", ".join(sources) if sources else None),
        "sources": sources,
        "startedAt": started_at.isoformat().replace("+00:00", "Z"),
        "endedAt": ended_at.isoformat().replace("+00:00", "Z")
        if record.status == "completed"
        else None,
        "durationSeconds": max(0, int((ended_at - started_at).total_seconds())),
        "status": "in_progress" if record.status == "active" else record.status,
        "eventCount": count,
        # This is the number of distinct started agents, capped by the same
        # backend limit as StateMachine. It is a fast upper bound for legacy
        # rows and equals the peak for normal lifecycle streams.
        "maxAgents": min(max_agents, StateMachine.MAX_AGENTS),
        "models": models,
        "displayName": safe_display_text(record.display_name),
    }


async def _ordered_page(
    db: AsyncSession, session_id: str, *, offset: int, limit: int, use_safe: bool
) -> list[ReplayEventRecord | EventRecord]:
    if use_safe:
        result = await db.execute(
            select(ReplayEventRecord)
            .where(ReplayEventRecord.session_id == session_id)
            .order_by(*_replay_order(safe=True))
            .offset(offset)
            .limit(limit)
        )
        return list(result.scalars().all())
    return await _raw_compacted_page(db, session_id, offset=offset, limit=limit)


async def _raw_by_id(
    db: AsyncSession, rows: list[ReplayEventRecord | EventRecord]
) -> dict[int, EventRecord]:
    source_ids = [
        int(row.source_event_id)
        for row in rows
        if isinstance(row, ReplayEventRecord) and row.source_event_id is not None
    ]
    if not source_ids:
        return {}
    result = await db.execute(select(EventRecord).where(EventRecord.id.in_(source_ids)))
    return {row.id: row for row in result.scalars().all()}


def _checkpoint_for(session_id: str, offset: int) -> _ReplayCheckpoint | None:
    candidates = [
        checkpoint
        for checkpoint in _REPLAY_CHECKPOINTS.get(session_id, [])
        if checkpoint.offset <= offset
    ]
    return max(candidates, key=lambda checkpoint: checkpoint.offset) if candidates else None


def _save_checkpoint(session_id: str, offset: int, machine: StateMachine) -> None:
    checkpoints = _REPLAY_CHECKPOINTS.setdefault(session_id, [])
    checkpoints[:] = [checkpoint for checkpoint in checkpoints if checkpoint.offset != offset]
    checkpoints.append(_ReplayCheckpoint(offset, deepcopy(machine)))
    checkpoints.sort(key=lambda checkpoint: checkpoint.offset)
    del checkpoints[:-_REPLAY_CACHE_SIZE]


def _buffered_seconds(metadata: dict[str, Any], entries: list[dict[str, Any]]) -> float:
    """Return the recorded time covered by the currently buffered chunk."""
    if not entries:
        return 0.0
    try:
        start = datetime.fromisoformat(
            str(metadata["startedAt"]).replace("Z", "+00:00")
        )
        end = datetime.fromisoformat(
            str(entries[-1]["event"]["timestamp"]).replace("Z", "+00:00")
        )
        return max(0.0, (end - start).total_seconds())
    except (KeyError, TypeError, ValueError):
        return 0.0


async def _safe_event_views(
    db: AsyncSession, session_id: str
) -> tuple[list[dict[str, Any]], list[EventRecord]]:
    """Return safe event views, backfilling only in memory for older sessions."""
    raw = await _raw_events(db, session_id)
    tombstone = await db.get(ReplaySessionTombstone, session_id)
    if tombstone is not None:
        return [], raw
    records = await _safe_records(db, session_id)
    by_source = {record.source_event_id: record for record in records if record.source_event_id}
    views: list[dict[str, Any]] = []
    for record in raw:
        event = normalized_event_from_record(record)
        if event is None:
            continue
        safe_record = by_source.get(record.id)
        views.append(
            safe_event_from_record(safe_record)
            if safe_record is not None
            else safe_event_from_normalized(event, source_event_id=record.id)
        )
    # Synthetic/legacy Replay rows may not have a corresponding raw row.
    raw_ids = {record.id for record in raw}
    views.extend(
        safe_event_from_record(record)
        for record in records
        if record.source_event_id not in raw_ids
    )
    views.sort(key=lambda item: (item["timestamp"], item["id"]))
    return views, raw


def _max_agents(events: list[dict[str, Any]]) -> int:
    active: set[str] = set()
    maximum = 0
    for event in events:
        agent_id = str(event.get("agentId") or "")
        event_type = event.get("type")
        if event_type == "subagent_start" and agent_id and agent_id not in {"main", "boss"}:
            active.add(agent_id)
        elif event_type in {"subagent_stop", "cleanup"}:
            active.discard(agent_id)
        maximum = max(maximum, len(active))
    return maximum


def _summary(record: SessionRecord, events: list[dict[str, Any]]) -> dict[str, Any]:
    timestamps = [
        datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00")) for item in events
    ]
    started = min(timestamps) if timestamps else _utc(record.created_at)
    ended = max(timestamps) if timestamps else _utc(record.updated_at)
    duration_seconds = max(0, int((ended - started).total_seconds()))
    sources = sorted(
        {
            item.get("detail", {}).get("source")
            for item in events
            if item.get("detail", {}).get("source")
        }
    )
    models = sorted(
        {
            item.get("detail", {}).get("model")
            for item in events
            if item.get("detail", {}).get("model")
        }
    )
    project_name = next(
        (
            item.get("detail", {}).get("projectName")
            for item in events
            if item.get("detail", {}).get("projectName")
        ),
        safe_display_text(record.project_name),
    )
    display_name = safe_display_text(record.display_name)
    return {
        "id": record.id,
        "projectName": project_name,
        "source": sources[0] if len(sources) == 1 else (", ".join(sources) if sources else None),
        "sources": sources,
        "startedAt": started.isoformat().replace("+00:00", "Z"),
        "endedAt": ended.isoformat().replace("+00:00", "Z")
        if record.status == "completed"
        else None,
        "durationSeconds": duration_seconds,
        "status": "in_progress" if record.status == "active" else record.status,
        "eventCount": len(events),
        "maxAgents": _max_agents(events),
        "models": models,
        "displayName": display_name,
    }


async def _legacy_summary(
    db: AsyncSession, record: SessionRecord
) -> dict[str, Any] | None:
    """Summarize legacy LIVE rows without parsing their private payloads.

    This is the fast path used by the session list while the asynchronous
    Replay backfill is catching up.  Only allow-listed JSON metadata columns
    are selected; prompts, tool input, transcripts, and response bodies never
    leave SQLite.
    """
    valid = EventRecord.event_type.in_(_REPLAY_EVENT_TYPES)
    base = EventRecord.session_id == record.id
    event_count = int(
        await db.scalar(select(func.count(EventRecord.id)).where(base, valid)) or 0
    )
    if event_count == 0:
        return None
    started = await db.scalar(
        select(func.min(EventRecord.timestamp)).where(base, valid)
    )
    ended = await db.scalar(select(func.max(EventRecord.timestamp)).where(base, valid))
    started_at = _utc(started or record.created_at)
    ended_at = _utc(ended or record.updated_at)
    source_result = await db.execute(
        select(func.json_extract(EventRecord.data, "$.source"))
        .where(base, valid)
        .distinct()
    )
    sources = sorted(
        {
            safe_value
            for value in source_result.scalars().all()
            if (safe_value := safe_display_text(value))
        }
    )
    model_result = await db.execute(
        select(func.json_extract(EventRecord.data, "$.model"))
        .where(base, valid)
        .distinct()
    )
    models = sorted(
        {
            safe_value
            for value in model_result.scalars().all()
            if (safe_value := safe_display_text(value))
        }
    )
    project_result = await db.execute(
        select(func.json_extract(EventRecord.data, "$.project_name"))
        .where(base, valid)
        .distinct()
    )
    project_names = [
        safe_value
        for value in project_result.scalars().all()
        if (safe_value := safe_display_text(value))
    ]
    project_name = project_names[0] if project_names else safe_display_text(record.project_name)
    duration_seconds = max(0, int((ended_at - started_at).total_seconds()))
    return {
        "id": record.id,
        "projectName": project_name,
        "source": sources[0] if len(sources) == 1 else (", ".join(sources) if sources else None),
        "sources": sources,
        "startedAt": started_at.isoformat().replace("+00:00", "Z"),
        "endedAt": ended_at.isoformat().replace("+00:00", "Z")
        if record.status == "completed"
        else None,
        "durationSeconds": duration_seconds,
        "status": "in_progress" if record.status == "active" else record.status,
        "eventCount": event_count,
        # The exact live agent peak is available once the safe rows are
        # backfilled.  Legacy list summaries remain replayable and show 0
        # until that asynchronous catch-up has completed.
        "maxAgents": 0,
        "models": models,
        "displayName": safe_display_text(record.display_name),
    }


def _history_enabled() -> bool:
    settings, _ = load_settings()
    return bool(settings.get("replay_history_enabled", True))


@router.get("/storage")
async def get_replay_storage(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Return bounded Replay storage statistics for the settings screen."""
    settings, _ = load_settings()
    enabled = bool(settings.get("replay_history_enabled", True))
    event_count = int(
        await db.scalar(select(func.count(ReplayEventRecord.id))) or 0
    )
    session_count = int(
        await db.scalar(select(func.count(func.distinct(ReplayEventRecord.session_id)))) or 0
    )
    oldest = await db.scalar(select(func.min(ReplayEventRecord.timestamp)))
    newest = await db.scalar(select(func.max(ReplayEventRecord.timestamp)))
    # Once the privacy-safe table has rows, it is the Replay read model.  Do
    # not rescan the high-volume LIVE table just to render the Replay sidebar;
    # on large sessions that count/distinct query can keep the whole screen in
    # its loading state.  The raw fallback is only needed before backfill has
    # produced the first safe row.
    if enabled and event_count == 0:
        tombstoned = select(1).where(
            ReplaySessionTombstone.session_id == EventRecord.session_id
        ).exists()
        live_filter = EventRecord.event_type.in_(_REPLAY_EVENT_TYPES)
        live_event_count = int(
            await db.scalar(
                select(func.count(EventRecord.id)).where(live_filter, ~tombstoned)
            )
            or 0
        )
        live_session_count = int(
            await db.scalar(
                select(func.count(func.distinct(EventRecord.session_id))).where(
                    live_filter, ~tombstoned
                )
            )
            or 0
        )
        event_count = max(event_count, live_event_count)
        session_count = max(session_count, live_session_count)
        if oldest is None:
            oldest = await db.scalar(select(func.min(EventRecord.timestamp)).where(live_filter))
        if newest is None:
            newest = await db.scalar(select(func.max(EventRecord.timestamp)).where(live_filter))
    return {
        "enabled": enabled,
        "retentionDays": int(settings.get("replay_retention_days", 30) or 0),
        "eventCount": event_count,
        "sessionCount": session_count,
        "oldestAt": _utc(oldest).isoformat().replace("+00:00", "Z") if oldest else None,
        "newestAt": _utc(newest).isoformat().replace("+00:00", "Z") if newest else None,
    }


@router.get("/sessions")
async def list_replay_sessions(
    db: Annotated[AsyncSession, Depends(get_db)],
    source: str | None = None,
    project: str | None = None,
    model: str | None = None,
    order: str = Query("desc", pattern="^(asc|desc)$"),
    started_from: datetime | None = None,
    started_to: datetime | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[dict[str, Any]]:
    if not _history_enabled():
        return []
    safe_event_exists = bool(
        await db.scalar(select(ReplayEventRecord.id).limit(1))
    )
    if safe_event_exists:
        # SessionRecord also contains ordinary LIVE sessions that have no
        # Replay-safe rows.  Restrict the sidebar query to the read model so
        # a large history does not cause one metadata query per unrelated
        # session.
        candidate_ids = select(ReplayEventRecord.session_id).distinct()
    else:
        candidate_ids = (
            select(EventRecord.session_id)
            .where(EventRecord.event_type.in_(_REPLAY_EVENT_TYPES))
            .distinct()
        )
    result = await db.execute(
        select(SessionRecord)
        .where(SessionRecord.id.in_(candidate_ids))
        .order_by(SessionRecord.updated_at.desc())
    )
    output: list[dict[str, Any]] = []
    for record in result.scalars().all():
        summary = await _replay_summary(db, record)
        if summary is None:
            continue
        if project and project.lower() not in str(summary.get("projectName") or "").lower():
            continue
        if source and source.lower() not in {str(value).lower() for value in summary["sources"]}:
            continue
        if model and model.lower() not in {
            str(value).lower() for value in summary["models"]
        }:
            continue
        started = datetime.fromisoformat(summary["startedAt"].replace("Z", "+00:00"))
        if started_from and started < _utc(started_from):
            continue
        if started_to and started > _utc(started_to):
            continue
        output.append(summary)
    output.sort(key=lambda item: item["startedAt"], reverse=order == "desc")
    return output[:limit]


@router.get("/sessions/{session_id}")
async def get_replay_session(
    session_id: str, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict[str, Any]:
    _require_session_id(session_id)
    if not _history_enabled():
        raise HTTPException(status_code=404, detail="Replay history is disabled")
    record = await _session_record(db, session_id)
    started = perf_counter()
    summary = await _replay_summary(db, record)
    if summary is None:
        raise HTTPException(status_code=404, detail="Replay history not found")
    logger.info(
        "Replay metadata loaded session=%s events=%d query_ms=%.1f",
        session_id,
        summary["eventCount"],
        (perf_counter() - started) * 1000,
    )
    return summary


@router.get("/sessions/{session_id}/events")
async def get_replay_events(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: int | None = Query(None, ge=0),
    limit: int | None = Query(None, ge=1, le=5000),
) -> list[dict[str, Any]] | dict[str, Any]:
    _require_session_id(session_id)
    if not _history_enabled():
        return []
    record = await _session_record(db, session_id)
    if await db.get(ReplaySessionTombstone, session_id) is not None:
        return [] if offset is None and limit is None else {
            "items": [],
            "offset": offset or 0,
            "limit": limit or _REPLAY_PAGE_SIZE,
            "total": 0,
            "nextOffset": offset or 0,
            "hasMore": False,
        }
    safe_count = await _safe_event_count(db, session_id)
    if safe_count > 0:
        raw_count = safe_count
    else:
        raw_count = await _raw_compacted_count(db, session_id)
    if raw_count == 0:
        return [] if offset is None and limit is None else {
            "items": [],
            "offset": offset or 0,
            "limit": limit or _REPLAY_PAGE_SIZE,
            "total": 0,
            "nextOffset": offset or 0,
            "hasMore": False,
        }

    legacy_response = offset is None and limit is None
    page_offset = offset or 0
    page_limit = limit or _REPLAY_PAGE_SIZE
    total = raw_count
    use_safe = safe_count > 0

    started = perf_counter()
    checkpoint = _checkpoint_for(session_id, page_offset)
    base_offset = checkpoint.offset if checkpoint else 0
    machine = deepcopy(checkpoint.machine) if checkpoint else StateMachine()
    query_limit = page_limit + max(0, page_offset - base_offset)
    rows = await _ordered_page(
        db,
        session_id,
        offset=base_offset,
        limit=query_limit,
        use_safe=use_safe,
    )
    by_source = await _raw_by_id(db, rows)
    query_ms = (perf_counter() - started) * 1000

    if checkpoint is None:
        replay_source: str | None = None
        replay_model: str | None = None
        for row in rows:
            if isinstance(row, ReplayEventRecord):
                replay_source = row.source
                replay_model = row.model
            else:
                event = normalized_event_from_record(row)
                if event is not None:
                    replay_source = getattr(event.data, "source", None)
                    replay_model = getattr(event.data, "model", None)
            if replay_source or replay_model:
                break
        machine.initialize_main(replay_source, replay_model)

    entries: list[dict[str, Any]] = []
    absolute_offset = base_offset
    state_started = perf_counter()
    for row in rows:
        if isinstance(row, ReplayEventRecord):
            raw_record = by_source.get(row.source_event_id or -1)
            event = normalized_event_from_record(raw_record) if raw_record else None
            if event is None:
                event = normalized_event_from_replay_record(row)
            view = safe_event_from_record(row)
        else:
            event = normalized_event_from_record(row)
            view = safe_event_from_normalized(event, source_event_id=row.id) if event else None
        if event is None or view is None:
            absolute_offset += 1
            continue
        machine.transition(event)
        absolute_offset += 1
        if absolute_offset <= page_offset:
            continue
        entries.append(
            {"event": view, "state": redacted_game_state(machine.to_game_state(session_id))}
        )
        if len(entries) >= page_limit:
            break

    # Offset is the ordered database-row cursor, not the number of serialized
    # entries.  Keeping skipped/malformed rows in the cursor prevents a bad
    # legacy row from making the next request repeat or skip a valid event.
    next_offset = absolute_offset
    _save_checkpoint(session_id, next_offset, machine)
    state_ms = (perf_counter() - state_started) * 1000
    logger.info(
        "Replay chunk loaded session=%s offset=%d count=%d total=%d query_ms=%.1f state_ms=%.1f",
        session_id,
        page_offset,
        len(entries),
        total,
        query_ms,
        state_ms,
    )
    if legacy_response:
        return entries
    timestamps = [entry["event"]["timestamp"] for entry in entries]
    return {
        "items": entries,
        "offset": page_offset,
        "limit": page_limit,
        "total": total,
        "nextOffset": next_offset,
        "hasMore": next_offset < total,
        "loadedTimeRange": {
            "start": timestamps[0] if timestamps else None,
            "end": timestamps[-1] if timestamps else None,
        },
        "bufferedSeconds": _buffered_seconds(
            {
                "startedAt": _utc(record.created_at).isoformat().replace("+00:00", "Z")
            },
            entries,
        ),
    }


@router.delete("/sessions/{session_id}")
async def delete_replay_session(
    session_id: str, db: Annotated[AsyncSession, Depends(get_db)]
) -> dict[str, Any]:
    _require_session_id(session_id)
    await _session_record(db, session_id)
    result = await db.execute(
        delete(ReplayEventRecord).where(ReplayEventRecord.session_id == session_id)
    )
    db.add(ReplaySessionTombstone(session_id=session_id))
    await db.commit()
    _REPLAY_CHECKPOINTS.pop(session_id, None)
    return {"deleted": int(getattr(result, "rowcount", 0) or 0), "sessionId": session_id}


@router.delete("/history")
async def delete_replay_history(
    db: Annotated[AsyncSession, Depends(get_db)]
) -> dict[str, Any]:
    result = await db.execute(delete(ReplayEventRecord))
    session_result = await db.execute(select(SessionRecord.id))
    for session_id in session_result.scalars().all():
        db.add(ReplaySessionTombstone(session_id=session_id))
    await db.commit()
    _REPLAY_CHECKPOINTS.clear()
    return {"deleted": int(getattr(result, "rowcount", 0) or 0)}
