"""Foreground PostgreSQL commands for discovery state changes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .postgres_store import PostgresConnection


def dismiss_opportunity(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    repertoire_id = str(payload["repertoire_id"])
    opportunity_id = str(payload["opportunity_id"])
    evidence = database.execute_native(
        "SELECT evidence_json FROM repertoire_opportunities "
        "WHERE id=%s AND repertoire_id=%s AND status='active' FOR UPDATE",
        (opportunity_id, repertoire_id),
    ).fetchone()
    if evidence is None:
        raise HTTPException(404, "Active opportunity not found")
    database.execute_native(
        "UPDATE repertoire_opportunities SET status='dismissed',"
        "dismissed_evidence_json=%s,updated_at=%s WHERE id=%s",
        (evidence[0], datetime.now(timezone.utc).isoformat(), opportunity_id),
    )
    return {"dismissed": True}


def acknowledge_opportunity(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    now = datetime.now(timezone.utc).isoformat()
    saved = database.execute_native(
        "UPDATE repertoire_opportunities SET seen_at=%s,snoozed_until=NULL,updated_at=%s "
        "WHERE id=%s AND repertoire_id=%s AND status='active' RETURNING id",
        (now, now, str(payload["opportunity_id"]), str(payload["repertoire_id"])),
    ).fetchone()
    if saved is None:
        raise HTTPException(404, "Active discovery not found")
    return {"acknowledged": True}


def snooze_opportunity(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    now = datetime.now(timezone.utc)
    saved = database.execute_native(
        "UPDATE repertoire_opportunities SET seen_at=COALESCE(seen_at,%s),"
        "snoozed_until=%s,updated_at=%s "
        "WHERE id=%s AND repertoire_id=%s AND status='active' RETURNING id",
        (now.isoformat(), (now + timedelta(days=7)).isoformat(), now.isoformat(),
         str(payload["opportunity_id"]), str(payload["repertoire_id"])),
    ).fetchone()
    if saved is None:
        raise HTTPException(404, "Active discovery not found")
    return {"snoozed": True}


register_command("opportunities.dismiss", dismiss_opportunity)
register_command("opportunities.acknowledge", acknowledge_opportunity)
register_command("opportunities.snooze", snooze_opportunity)
