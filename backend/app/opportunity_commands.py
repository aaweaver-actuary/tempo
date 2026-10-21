"""Foreground PostgreSQL commands for discovery state changes."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from tempo_legacy_database.adapters.authored_repertoires import discovery_commands as storage
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.queue_position_lock import lock_queue_date_for_position
from .services.postgres_integrity import invalidate_integrity_in_transaction
from .services.postgres_opening_graph import request_graph_rebuild_in_transaction
from .services.repertoire_opportunities import active_opportunity_for_state_change, admit_existing_decision, enqueue_opportunity_refresh_in_transaction


def _active_state_action_opportunity(database: PostgresConnection, payload: dict[str, Any]):
    try:
        return active_opportunity_for_state_change(
            database, str(payload["repertoire_id"]), str(payload["opportunity_id"]),
        )
    except ValueError as error:
        raise HTTPException(409, str(error)) from error


def dismiss_opportunity(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    evidence = _active_state_action_opportunity(database, payload)
    if evidence is None:
        raise HTTPException(404, "Active opportunity not found")
    storage.dismiss(database, evidence["evidence_json"], datetime.now(timezone.utc).isoformat(), str(payload["opportunity_id"]))
    return {"dismissed": True}


def acknowledge_opportunity(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    if _active_state_action_opportunity(database, payload) is None:
        raise HTTPException(404, "Active discovery not found")
    now = datetime.now(timezone.utc).isoformat()
    saved = storage.acknowledge(database, now, now, str(payload["opportunity_id"]), str(payload["repertoire_id"]))
    if saved is None:
        raise HTTPException(404, "Active discovery not found")
    return {"acknowledged": True}


def snooze_opportunity(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    if _active_state_action_opportunity(database, payload) is None:
        raise HTTPException(404, "Active discovery not found")
    now = datetime.now(timezone.utc)
    saved = storage.snooze(database, now.isoformat(), (now + timedelta(days=7)).isoformat(), now.isoformat(), str(payload["opportunity_id"]), str(payload["repertoire_id"]))
    if saved is None:
        raise HTTPException(404, "Active discovery not found")
    return {"snoozed": True}


def train_opportunity(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    reviewed_fingerprint = payload.get("evidence_fingerprint")
    if not isinstance(reviewed_fingerprint, str) or not reviewed_fingerprint.strip():
        raise HTTPException(409, "Review the current evidence and train again with its evidence revision")
    repertoire_id = str(payload["repertoire_id"])
    opportunity_id = str(payload["opportunity_id"])
    from .services.canonical_prefix import read_prefix
    from .services.canonical_scope_freshness import game_scope_generation
    read_prefix(database, repertoire_id, lock=True)
    game_scope_generation(database, lock=True)
    opportunity = storage.locked_opportunity_card(database, opportunity_id, repertoire_id)
    if opportunity is None:
        raise HTTPException(404, "Discovery not found")
    source_card_id = opportunity[0]
    source_card = storage.locked_card_kind(database, source_card_id) if source_card_id else None
    local_day = date.today().isoformat()
    lock_queue_date_for_position(database, local_day)
    try:
        result = admit_existing_decision(database, repertoire_id, opportunity_id, reviewed_fingerprint)
    except KeyError as error:
        raise HTTPException(404, str(error)) from error
    except ValueError as error:
        raise HTTPException(409, str(error)) from error
    if source_card is not None and source_card[0] == "prefix" and not result["idempotent"]:
        split = storage.prefix_split_cards(database, source_card_id)
        if split is None:
            raise RuntimeError("Prefix split was not saved with discovery training")
        linked_repertoires = {str(row[0]) for row in storage.linked_repertoire_rows(database, [split[0], split[1]])}
        for linked_repertoire_id in sorted(linked_repertoires):
            invalidate_integrity_in_transaction(database, linked_repertoire_id)
            request_graph_rebuild_in_transaction(database, linked_repertoire_id, local_day)
    return result


def refresh_opportunities(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, bool]:
    repertoire_id = str(payload["repertoire_id"])
    exists = storage.repertoire(database, repertoire_id)
    if exists is None:
        raise HTTPException(404, "Repertoire not found")
    enqueue_opportunity_refresh_in_transaction(database, repertoire_id, quiet_seconds=0)
    return {"queued": True}


register_command("opportunities.dismiss", dismiss_opportunity)
register_command("opportunities.acknowledge", acknowledge_opportunity)
register_command("opportunities.snooze", snooze_opportunity)
register_command("opportunities.train", train_opportunity)
register_command("opportunities.refresh", refresh_opportunities)
