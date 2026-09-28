"""Foreground PostgreSQL commands for discovery state changes."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .postgres_store import PostgresConnection
from .queue_position_lock import lock_queue_date_for_position
from .services.postgres_integrity import invalidate_integrity_in_transaction
from .services.postgres_opening_graph import request_graph_rebuild_in_transaction
from .services.repertoire_opportunities import admit_existing_decision
from .services.durable_tasks import enqueue_task_in_transaction


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


def train_opportunity(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    repertoire_id = str(payload["repertoire_id"])
    opportunity_id = str(payload["opportunity_id"])
    opportunity = database.execute_native(
        "SELECT card_id FROM repertoire_opportunities "
        "WHERE id=%s AND repertoire_id=%s FOR UPDATE",
        (opportunity_id, repertoire_id),
    ).fetchone()
    if opportunity is None:
        raise HTTPException(404, "Discovery not found")
    source_card_id = opportunity[0]
    source_card = database.execute_native(
        "SELECT kind FROM cards WHERE id=%s FOR UPDATE", (source_card_id,),
    ).fetchone() if source_card_id else None
    local_day = date.today().isoformat()
    lock_queue_date_for_position(database, local_day)
    try:
        result = admit_existing_decision(database, repertoire_id, opportunity_id)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    if source_card is not None and source_card[0] == "prefix" and not result["idempotent"]:
        split = database.execute_native(
            "SELECT shortened_card_id,continuation_card_id FROM prefix_splits "
            "WHERE source_card_id=%s", (source_card_id,),
        ).fetchone()
        if split is None:
            raise RuntimeError("Prefix split was not saved with discovery training")
        linked_repertoires = {str(row[0]) for row in database.execute_native(
            "SELECT repertoire_id FROM repertoire_cards WHERE card_id=ANY(%s::text[])",
            ([split[0], split[1]],),
        )}
        for linked_repertoire_id in sorted(linked_repertoires):
            invalidate_integrity_in_transaction(database, linked_repertoire_id)
            request_graph_rebuild_in_transaction(database, linked_repertoire_id, local_day)
    return result


def refresh_opportunities(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    repertoire_id = str(payload["repertoire_id"])
    exists = database.execute_native(
        "SELECT 1 FROM repertoires WHERE id=%s", (repertoire_id,),
    ).fetchone()
    if exists is None:
        raise HTTPException(404, "Repertoire not found")
    enqueue_task_in_transaction(
        database, "repertoire_opportunity", repertoire_id,
        {"repertoire_id": repertoire_id, "phase": "summaries", "cursor": ""},
        priority=130,
    )
    return {"queued": True}


register_command("opportunities.dismiss", dismiss_opportunity)
register_command("opportunities.acknowledge", acknowledge_opportunity)
register_command("opportunities.snooze", snooze_opportunity)
register_command("opportunities.train", train_opportunity)
register_command("opportunities.refresh", refresh_opportunities)
