"""Foreground PostgreSQL command for guided repertoire integrity repair."""

from __future__ import annotations

from datetime import date
import hashlib
import json
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from .services.repertoire_game_refresh import refresh_game_publications_after_mutation
from .command_gateway import register_command
from tempo_legacy_database.adapters.authored_repertoires import integrity_commands as storage
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from app.chess.cards import card_id
from .services.postgres_integrity import invalidate_integrity_in_transaction
from .services.postgres_opening_graph import request_graph_rebuild_in_transaction
from .services.repertoire_integrity import _archive_unsupported_card, _rewrite_card


class PreparedIntegrityRepair(BaseModel):
    repertoire_id: str
    issue_id: str
    signature: str
    changed_lines: dict[str, list[str]]
    changed_cards: dict[str, list[str]]
    expected_line_moves: dict[str, str]
    expected_card_moves: dict[str, str]
    unsupported_card_ids: list[str]


def _replace_repertoire_line(
    database: PostgresConnection, source_line: Any, moves: list[str], *, validated_route: dict | None = None, certify: bool = True,
) -> bool:
    from .services.canonical_prefix import ensure_line_in_scope, certify_admitted_route
    if validated_route is None:
        validated_route = ensure_line_in_scope(database, source_line["repertoire_id"], source_line["start_fen"], moves, remember=False)
    new_line_id = hashlib.sha256(
        f"{source_line['repertoire_id']}\0{card_id(source_line['start_fen'], moves)}".encode(),
    ).hexdigest()
    if new_line_id == source_line["id"]:
        return False
    storage.insert_replacement_line(database, (new_line_id, source_line["repertoire_id"], source_line["name"],
         source_line["trained_color"], source_line["start_fen"],
         json.dumps(moves), source_line["created_at"]))
    storage.copy_training_depth(database, new_line_id, source_line["id"])
    storage.delete_replaced_line(database, source_line["id"])
    if certify:
        certify_admitted_route(database, source_line["repertoire_id"], validated_route)
    return True


@refresh_game_publications_after_mutation
def resolve_integrity_issue(
    database: PostgresConnection, raw_payload: dict[str, Any],
) -> dict[str, Any]:
    prepared = PreparedIntegrityRepair.model_validate(raw_payload)
    repertoire_id = prepared.repertoire_id
    # Graph slices lock their task row before touching cards. Acquire that row
    # first so a repair cannot deadlock a graph slice while replacing a card.
    graph_task = request_graph_rebuild_in_transaction(
        database, repertoire_id, date.today().isoformat(),
    )
    if storage.locked_repertoire(database, repertoire_id) is None:
        raise HTTPException(404, "Repertoire not found")
    issue = storage.locked_issue_signature(database, prepared.issue_id, repertoire_id)
    if issue is None:
        raise HTTPException(404, "Integrity issue not found")
    if issue[0] != prepared.signature:
        raise HTTPException(409, "This integrity issue changed; refresh and try again")
    if set(prepared.changed_lines) != set(prepared.expected_line_moves):
        raise HTTPException(422, "Repair line expectations do not match the changes")
    if set(prepared.changed_cards) != set(prepared.expected_card_moves):
        raise HTTPException(422, "Repair card expectations do not match the changes")
    from .services.canonical_prefix import ensure_line_in_scope, certify_admitted_route
    line_changes = []
    card_changes = []
    for source_id, moves in prepared.changed_lines.items():
        source_line = storage.locked_source_line(database, source_id, repertoire_id)
        if source_line is None or source_line["moves_json"] != prepared.expected_line_moves[source_id]:
            raise HTTPException(409, "A repertoire line changed during repair; refresh and try again")
        line_changes.append((source_line, moves, ensure_line_in_scope(database, repertoire_id, source_line["start_fen"], moves, remember=False)))
    for source_id, moves in prepared.changed_cards.items():
        source_card = storage.locked_source_card(database, source_id)
        if source_card is None or source_card["moves_json"] != prepared.expected_card_moves[source_id]:
            raise HTTPException(409, "A repertoire card changed during repair; refresh and try again")
        linked = storage.repertoire_card_link(database, repertoire_id, source_id)
        if source_card["repertoire_id"] != repertoire_id and linked is None:
            raise HTTPException(409, "A repertoire card was detached during repair")
        card_changes.append((source_card, moves, ensure_line_in_scope(database, repertoire_id, source_card["start_fen"], moves, remember=False)))
    admitted_routes = []
    for source_line, moves, validated_route in line_changes:
        if _replace_repertoire_line(database, source_line, moves, validated_route=validated_route, certify=False):
            admitted_routes.append(validated_route)
    for source_card, moves, validated_route in card_changes:
        if _rewrite_card(database, repertoire_id, source_card, moves, validated_route=validated_route, certify=False):
            admitted_routes.append(validated_route)
    for card_identifier in prepared.unsupported_card_ids:
        _archive_unsupported_card(database, repertoire_id, card_identifier)
    for validated_route in admitted_routes:
        certify_admitted_route(database, repertoire_id, validated_route)
    invalidate_integrity_in_transaction(database, repertoire_id)
    return {
        "task_id": graph_task["id"], "repertoire_id": repertoire_id,
        "issue_id": prepared.issue_id, "state": graph_task["state"],
    }


register_command("integrity.issue.resolve", resolve_integrity_issue)
