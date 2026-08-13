"""Privacy-safe Replay API.

The LIVE event table is an internal restoration source. This router exposes
only the allow-listed Replay metadata and redacted character state projection.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.replay import (
    normalized_event_from_record,
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
from app.services.app_settings import load_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/replay", tags=["replay"])
_SESSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


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
        None,
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


def _history_enabled() -> bool:
    settings, _ = load_settings()
    return bool(settings.get("replay_history_enabled", True))


@router.get("/storage")
async def get_replay_storage(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> dict[str, Any]:
    """Return bounded Replay storage statistics for the settings screen."""
    settings, _ = load_settings()
    event_count = int(
        await db.scalar(select(func.count(ReplayEventRecord.id))) or 0
    )
    session_count = int(
        await db.scalar(select(func.count(func.distinct(ReplayEventRecord.session_id)))) or 0
    )
    oldest = await db.scalar(select(func.min(ReplayEventRecord.timestamp)))
    newest = await db.scalar(select(func.max(ReplayEventRecord.timestamp)))
    return {
        "enabled": bool(settings.get("replay_history_enabled", True)),
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
    result = await db.execute(select(SessionRecord).order_by(SessionRecord.updated_at.desc()))
    output: list[dict[str, Any]] = []
    for record in result.scalars().all():
        events, _ = await _safe_event_views(db, record.id)
        if not events:
            continue
        summary = _summary(record, events)
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
    events, _ = await _safe_event_views(db, session_id)
    if not events:
        raise HTTPException(status_code=404, detail="Replay history not found")
    return _summary(record, events)


@router.get("/sessions/{session_id}/events")
async def get_replay_events(
    session_id: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    offset: int = Query(0, ge=0),
    limit: int = Query(2000, ge=1, le=2000),
) -> list[dict[str, Any]]:
    _require_session_id(session_id)
    if not _history_enabled():
        return []
    await _session_record(db, session_id)
    views, raw = await _safe_event_views(db, session_id)
    # State reconstruction is server-side and the returned projection is
    # redacted before serialization. No raw event payload crosses this route.
    by_source = {record.id: record for record in raw}
    machine = StateMachine()
    entries: list[dict[str, Any]] = []
    for view in views:
        raw_record = by_source.get(int(view["id"])) if str(view["id"]).isdigit() else None
        if raw_record is None:
            continue
        event = normalized_event_from_record(raw_record)
        if event is None:
            continue
        machine.transition(event)
        entries.append(
            {"event": view, "state": redacted_game_state(machine.to_game_state(session_id))}
        )
    return entries[offset : offset + limit]


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
    return {"deleted": int(getattr(result, "rowcount", 0) or 0)}
