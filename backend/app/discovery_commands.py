"""Foreground admission of one validated discovery continuation."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .database import read_connection
from .postgres_store import PostgresConnection
from .services.cards import card_id
from .services.canonical_prefix import read_prefix
from .services.canonical_scope_freshness import opportunity_is_current, game_scope_generation
from .services.discovery_admission import (
    DISCOVERY_ADMISSION_PRIORITY, admission_intent_id, recommend_missing_continuations,
)
from .services.durable_tasks import enqueue_compact_postgres_task_in_transaction


def prepare_discovery_acceptance(
    opportunity_id: str, selected_move_uci: str, evidence_fingerprint: str,
) -> dict[str, Any]:
    """Do the potentially costly recommendation work before Celery opens a write."""
    with read_connection() as database:
        existing = database.execute(
            "SELECT id,evidence_fingerprint FROM discovery_admission_intents "
            "WHERE opportunity_id=? AND selected_move_uci=? AND evidence_fingerprint=?",
            (opportunity_id, selected_move_uci, evidence_fingerprint),
        ).fetchone()
    if existing is not None:
        return {"opportunity_id": opportunity_id, "selected_move_uci": selected_move_uci,
                "evidence_fingerprint": evidence_fingerprint}
    recommendation = recommend_missing_continuations(opportunity_id)
    if (recommendation["state"] != "ready"
            or recommendation["evidence_fingerprint"] != evidence_fingerprint):
        raise ValueError("Discovery evidence changed; refresh the preview")
    selected = next((candidate for candidate in recommendation["candidates"]
                     if candidate["move_uci"] == selected_move_uci), None)
    if selected is None:
        raise ValueError("The chosen move is not a validated recommendation")
    return {"opportunity_id": opportunity_id, "selected_move_uci": selected_move_uci,
            "evidence_fingerprint": evidence_fingerprint,
            "repertoire_id": recommendation["repertoire_id"],
            "starting_fen": recommendation["starting_fen"],
            "preview_moves_uci": selected["preview_moves_uci"],
            "recommendation": selected}


def accept_discovery(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, str]:
    opportunity_id = payload["opportunity_id"]
    selected_move_uci = payload["selected_move_uci"]
    fingerprint = payload["evidence_fingerprint"]
    prior = database.execute_native(
        "SELECT id,evidence_fingerprint,state FROM discovery_admission_intents "
        "WHERE opportunity_id=%s AND selected_move_uci=%s AND evidence_fingerprint=%s FOR UPDATE",
        (opportunity_id, selected_move_uci, fingerprint),
    ).fetchone()
    if prior is not None:
        if prior["state"] != "queued":
            task = database.execute_native(
                "SELECT state FROM background_tasks WHERE kind='discovery_admission' "
                "AND deduplication_key=%s", (prior["id"],),
            ).fetchone()
            if task is None or task["state"] == "failed" or prior["state"] == "failed":
                enqueue_compact_postgres_task_in_transaction(
                    database, "discovery_admission", prior["id"],
                    {"intent_id": prior["id"]}, priority=DISCOVERY_ADMISSION_PRIORITY,
                )
        return {"status": "preparing", "intent_id": prior["id"]}

    owner = database.execute_native("SELECT repertoire_id FROM repertoire_opportunities WHERE id=%s", (opportunity_id,)).fetchone()
    if owner is not None:
        read_prefix(database, owner[0], lock=True)
        game_scope_generation(database, lock=True)
    opportunity = database.execute_native(
        "SELECT * "
        "FROM repertoire_opportunities WHERE id=%s FOR UPDATE", (opportunity_id,),
    ).fetchone()
    if opportunity is None or opportunity["status"] != "active" or not opportunity_is_current(database, opportunity):
        raise HTTPException(404, "Active discovery not found")
    if opportunity["card_id"]:
        raise HTTPException(409, "This discovery already has a saved decision card")
    if (opportunity["evidence_fingerprint"] != fingerprint
            or opportunity["repertoire_id"] != payload.get("repertoire_id")):
        raise HTTPException(409, "Discovery evidence changed; refresh the preview")
    # Two different operation IDs can prepare the same continuation at once.
    # Recheck after locking the parent opportunity before inserting its child.
    prior = database.execute_native(
        "SELECT id,evidence_fingerprint,state FROM discovery_admission_intents "
        "WHERE opportunity_id=%s AND selected_move_uci=%s AND evidence_fingerprint=%s FOR UPDATE",
        (opportunity_id, selected_move_uci, fingerprint),
    ).fetchone()
    if prior is not None:
        return {"status": "preparing", "intent_id": prior["id"]}
    preview_moves = payload["preview_moves_uci"]
    starting_fen = payload["starting_fen"]
    if (not preview_moves or preview_moves[0] != selected_move_uci
            or payload["recommendation"]["move_uci"] != selected_move_uci):
        raise HTTPException(409, "The chosen move is not a validated recommendation")
    line_id = hashlib.sha256(
        f"{opportunity['repertoire_id']}\0{card_id(starting_fen, preview_moves)}".encode()
    ).hexdigest()
    intent_id = admission_intent_id(opportunity_id, selected_move_uci, fingerprint)
    now = datetime.now(timezone.utc).isoformat()
    database.execute_native(
        "INSERT INTO discovery_admission_intents("
        "id,opportunity_id,repertoire_id,evidence_fingerprint,starting_fen,"
        "selected_move_uci,preview_moves_json,recommendation_json,line_id,created_at,updated_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (intent_id, opportunity_id, opportunity["repertoire_id"], fingerprint,
         starting_fen, selected_move_uci, json.dumps(preview_moves),
         json.dumps(payload["recommendation"]), line_id, now, now),
    )
    database.execute_native(
        "UPDATE repertoire_opportunities SET admission_state='preparing',"
        "seen_at=COALESCE(seen_at,%s),updated_at=%s WHERE id=%s",
        (now, now, opportunity_id),
    )
    enqueue_compact_postgres_task_in_transaction(
        database, "discovery_admission", intent_id, {"intent_id": intent_id},
        priority=DISCOVERY_ADMISSION_PRIORITY,
    )
    return {"status": "preparing", "intent_id": intent_id}


register_command("discovery.accept", accept_discovery)
