"""Foreground PostgreSQL command for guided repertoire integrity repair."""

from __future__ import annotations

from datetime import date
import hashlib
import json
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from .command_gateway import register_command
from .postgres_store import PostgresConnection
from .services.cards import card_id
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
    database: PostgresConnection, source_line: Any, moves: list[str],
) -> bool:
    new_line_id = hashlib.sha256(
        f"{source_line['repertoire_id']}\0{card_id(source_line['start_fen'], moves)}".encode(),
    ).hexdigest()
    if new_line_id == source_line["id"]:
        return False
    database.execute_native(
        "INSERT INTO repertoire_lines("
        "id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING",
        (new_line_id, source_line["repertoire_id"], source_line["name"],
         source_line["trained_color"], source_line["start_fen"],
         json.dumps(moves), source_line["created_at"]),
    )
    database.execute_native(
        "INSERT INTO repertoire_line_training_depths(line_id,learner_decision_count) "
        "SELECT %s,learner_decision_count FROM repertoire_line_training_depths "
        "WHERE line_id=%s ON CONFLICT(line_id) DO UPDATE SET "
        "learner_decision_count=GREATEST("
        "repertoire_line_training_depths.learner_decision_count,"
        "excluded.learner_decision_count)",
        (new_line_id, source_line["id"]),
    )
    database.execute_native(
        "DELETE FROM repertoire_lines WHERE id=%s", (source_line["id"],),
    )
    return True


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
    if database.execute_native(
        "SELECT 1 FROM repertoires WHERE id=%s FOR UPDATE", (repertoire_id,),
    ).fetchone() is None:
        raise HTTPException(404, "Repertoire not found")
    issue = database.execute_native(
        "SELECT signature FROM repertoire_integrity_issues "
        "WHERE id=%s AND repertoire_id=%s FOR UPDATE",
        (prepared.issue_id, repertoire_id),
    ).fetchone()
    if issue is None:
        raise HTTPException(404, "Integrity issue not found")
    if issue[0] != prepared.signature:
        raise HTTPException(409, "This integrity issue changed; refresh and try again")
    if set(prepared.changed_lines) != set(prepared.expected_line_moves):
        raise HTTPException(422, "Repair line expectations do not match the changes")
    if set(prepared.changed_cards) != set(prepared.expected_card_moves):
        raise HTTPException(422, "Repair card expectations do not match the changes")
    for source_id, moves in prepared.changed_lines.items():
        source_line = database.execute_native(
            "SELECT * FROM repertoire_lines WHERE id=%s AND repertoire_id=%s FOR UPDATE",
            (source_id, repertoire_id),
        ).fetchone()
        if source_line is None or source_line["moves_json"] != prepared.expected_line_moves[source_id]:
            raise HTTPException(409, "A repertoire line changed during repair; refresh and try again")
        _replace_repertoire_line(database, source_line, moves)
    for source_id, moves in prepared.changed_cards.items():
        source_card = database.execute_native(
            "SELECT * FROM cards WHERE id=%s FOR UPDATE", (source_id,),
        ).fetchone()
        if source_card is None or source_card["moves_json"] != prepared.expected_card_moves[source_id]:
            raise HTTPException(409, "A repertoire card changed during repair; refresh and try again")
        linked = database.execute_native(
            "SELECT 1 FROM repertoire_cards WHERE repertoire_id=%s AND card_id=%s",
            (repertoire_id, source_id),
        ).fetchone()
        if source_card["repertoire_id"] != repertoire_id and linked is None:
            raise HTTPException(409, "A repertoire card was detached during repair")
        _rewrite_card(database, repertoire_id, source_card, moves)
    for card_identifier in prepared.unsupported_card_ids:
        _archive_unsupported_card(database, repertoire_id, card_identifier)
    invalidate_integrity_in_transaction(database, repertoire_id)
    return {
        "task_id": graph_task["id"], "task_generation": graph_task["generation"], "repertoire_id": repertoire_id,
        "issue_id": prepared.issue_id, "state": graph_task["state"],
    }


register_command("integrity.issue.resolve", resolve_integrity_issue)
