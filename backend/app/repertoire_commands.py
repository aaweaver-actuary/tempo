"""Foreground repertoire writes for PostgreSQL."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from fastapi import HTTPException

from .command_gateway import register_command
from .postgres_store import PostgresConnection
from .models import RepertoireSettingsRequest
from .repertoire_settings import repertoire_settings_response
from .queue_commands import request_queue_refresh_in_transaction
from .services.durable_tasks import enqueue_task_in_transaction


_SYSTEM_REPERTOIRES = ("__tactics__", "__endgames__", "__game_mistakes__", "__game_tactics__", "__captured_tactics__")


def select_main_repertoire(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    repertoire_id = str(payload["repertoire_id"])
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        ("tempo:main-repertoire",),
    )
    # Every competing main-selection command locks the same eligible rows in
    # stable order before changing the single-main invariant.
    system_placeholders = ",".join("?" for _ in _SYSTEM_REPERTOIRES)
    repertoire_ids = [row[0] for row in database.execute(
        f"SELECT id FROM repertoires WHERE id NOT IN ({system_placeholders}) ORDER BY id FOR UPDATE",
        _SYSTEM_REPERTOIRES,
    )]
    if repertoire_id not in repertoire_ids:
        raise HTTPException(404, "Repertoire not found")
    database.execute(
        "UPDATE repertoires SET is_main=CASE WHEN id=? THEN 1 ELSE 0 END "
        f"WHERE id NOT IN ({system_placeholders})",
        (repertoire_id, *_SYSTEM_REPERTOIRES),
    )
    return {"id": repertoire_id, "is_main": True}


def rename_repertoire(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    repertoire_id = str(payload["repertoire_id"])
    name = str(payload["name"]).strip()
    if not database.execute(
        "UPDATE repertoires SET name=? WHERE id=?", (name, repertoire_id),
    ).rowcount:
        raise HTTPException(404, "Repertoire not found")
    return {"id": repertoire_id, "name": name}


def delete_repertoire(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    repertoire_id = str(payload["repertoire_id"])
    if repertoire_id in _SYSTEM_REPERTOIRES:
        raise HTTPException(400, "This system repertoire cannot be deleted")
    # Main selection and deletion must serialize across foreground workers.
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
        ("tempo:main-repertoire",),
    )
    # A leased graph or integrity worker locks its task before inserting rows
    # that reference the repertoire. Cancel those leases first so deletion
    # cannot race a stale generation into a foreign-key failure.
    now = datetime.now(timezone.utc).isoformat()
    database.execute_native(
        "WITH obsolete AS ("
        "SELECT id FROM background_tasks WHERE state IN ('queued','leased','retrying') "
        "AND (deduplication_key=%s OR payload_json::jsonb->>'repertoire_id'=%s) "
        "ORDER BY id FOR UPDATE) "
        "UPDATE background_tasks task SET state='complete',phase='cancelled',"
        "lease_token=NULL,lease_expires_at=NULL,completed_at=%s,updated_at=%s "
        "FROM obsolete WHERE task.id=obsolete.id",
        (repertoire_id, repertoire_id, now, now),
    )
    repertoire = database.execute_native(
        "SELECT id FROM repertoires WHERE id=%s FOR UPDATE", (repertoire_id,),
    ).fetchone()
    if repertoire is None:
        raise HTTPException(404, "Repertoire not found")
    database.execute_native(
        "UPDATE cards AS card SET repertoire_id=shared.replacement "
        "FROM (SELECT card.id,MIN(link.repertoire_id) AS replacement "
        "FROM cards card JOIN repertoire_cards link ON link.card_id=card.id "
        "WHERE card.repertoire_id=%s AND link.repertoire_id<>%s "
        "GROUP BY card.id) shared "
        "WHERE card.id=shared.id",
        (repertoire_id, repertoire_id),
    )
    database.execute_native("DELETE FROM repertoires WHERE id=%s", (repertoire_id,))
    replacement = database.execute_native(
        "SELECT id FROM repertoires WHERE id<>ALL(%s::text[]) "
        "ORDER BY created_at DESC LIMIT 1", (list(_SYSTEM_REPERTOIRES),),
    ).fetchone()
    if replacement:
        database.execute_native(
            "UPDATE repertoires SET is_main=CASE WHEN id=%s THEN 1 ELSE 0 END "
            "WHERE id<>ALL(%s::text[])",
            (replacement[0], list(_SYSTEM_REPERTOIRES)),
        )
    enqueue_task_in_transaction(
        database, "repertoire_game_refresh", "all", {"after_game_id": ""},
        priority=90,
    )
    return {"deleted": True, "id": repertoire_id}


register_command("repertoires.main.select", select_main_repertoire)
register_command("repertoires.rename", rename_repertoire)
register_command("repertoires.delete", delete_repertoire)


def update_repertoire_settings(database: PostgresConnection, payload: dict[str, Any]) -> dict[str, Any]:
    repertoire_id = str(payload["repertoire_id"])
    settings = RepertoireSettingsRequest.model_validate(payload["settings"])
    repertoire_settings_response(database, repertoire_id)
    database.execute("UPDATE repertoires SET new_cards_per_day=? WHERE id=?",
                     (settings.new_cards_per_day, repertoire_id))
    request_queue_refresh_in_transaction(database, date.today().isoformat())
    return repertoire_settings_response(database, repertoire_id)


register_command("repertoires.settings.update", update_repertoire_settings)
