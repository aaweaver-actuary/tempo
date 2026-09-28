"""Foreground PostgreSQL commands for endgame training templates."""

from __future__ import annotations

from datetime import date, datetime, timezone
import uuid
from typing import Any

import chess
from fastapi import HTTPException

from .command_gateway import register_command
from .models import EndgameTemplateRequest
from .postgres_store import PostgresConnection
from .queue_commands import request_queue_refresh_in_transaction
from .services.cards import card_id
from .services.endgames import generate_position, normalized_material


def create_endgame_template(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    request = EndgameTemplateRequest.model_validate(payload)
    try:
        white_material = normalized_material(request.white_material)
        black_material = normalized_material(request.black_material)
        sample_fen = generate_position(white_material, black_material, request.trained_color)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    template_key = ":".join((white_material, black_material, request.trained_color, request.goal_mix))
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 1))", (template_key,),
    )
    existing = database.execute_native(
        "SELECT id,card_id FROM endgame_templates WHERE white_material=%s "
        "AND black_material=%s AND trained_color=%s AND goal_mix=%s AND enabled=1",
        (white_material, black_material, request.trained_color, request.goal_mix),
    ).fetchone()
    if existing:
        return {"id": existing["id"], "card_id": existing["card_id"],
                "sample_fen": sample_fen, "already_exists": True}
    template_id = str(uuid.uuid4())
    template_card_id = card_id(
        chess.STARTING_FEN,
        [f"template:{template_key}"],
    )
    now = datetime.now(timezone.utc).isoformat()
    today = date.today().isoformat()
    database.execute_native(
        "INSERT INTO repertoires(id,name,source_name,created_at) "
        "VALUES('__endgames__','Endgames','Generated material templates',%s) "
        "ON CONFLICT(id) DO NOTHING", (now,),
    )
    database.execute_native(
        "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,"
        "content_type,state,introduced_at) "
        "VALUES(%s,'__endgames__','checkpoint',%s,'[]',%s,'endgame','learning',%s)",
        (template_card_id, sample_fen, today, today),
    )
    database.execute_native(
        "INSERT INTO endgame_templates(id,card_id,name,white_material,black_material,"
        "trained_color,goal_mix,enabled,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,1,%s)",
        (template_id, template_card_id, request.name, white_material, black_material,
         request.trained_color, request.goal_mix, now),
    )
    request_queue_refresh_in_transaction(database, today)
    return {"id": template_id, "card_id": template_card_id,
            "sample_fen": sample_fen, "already_exists": False}


register_command("endgames.template.create", create_endgame_template)


def create_endgame_attempt(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    template_id = str(payload["template_id"])
    template = database.execute_native(
        "SELECT id FROM endgame_templates WHERE id=%s AND enabled=1 FOR UPDATE",
        (template_id,),
    ).fetchone()
    if template is None:
        raise HTTPException(404, "Endgame template not found")
    attempt_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    database.execute_native(
        "INSERT INTO endgame_attempts(id,template_id,start_fen,target,created_at) "
        "VALUES(%s,%s,%s,%s,%s)",
        (attempt_id, template_id, payload["fen"], payload["target"], created_at),
    )
    return {"id": attempt_id, "fen": payload["fen"],
            "target": payload["target"], "moves": payload["moves"]}


register_command("endgames.attempt.create", create_endgame_attempt)
