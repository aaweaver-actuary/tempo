"""Repertoire-owned retained line and card mutations within the caller transaction."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import sqlite3
from typing import Any

from app.chess.cards import card_id
from tempo_legacy_database.connections import card_columns
from tempo_legacy_database.adapters.postgres_store import PostgresConnection
from tempo_legacy_database.adapters.authored_repertoires import (
    integrity_commands as line_storage,
)
from tempo_legacy_database.adapters.authored_repertoires.integrity import (
    storage as card_storage,
)


def replace_repertoire_line(
    database: PostgresConnection,
    source_line: Any,
    moves: list[str],
    *,
    validated_route: dict | None = None,
    certify: bool = True,
) -> bool:
    from app.services.canonical_prefix import (
        ensure_line_in_scope,
        certify_admitted_route,
    )

    if validated_route is None:
        validated_route = ensure_line_in_scope(
            database,
            source_line["repertoire_id"],
            source_line["start_fen"],
            moves,
            remember=False,
        )
    new_line_id = hashlib.sha256(
        f"{source_line['repertoire_id']}\0{card_id(source_line['start_fen'], moves)}".encode(),
    ).hexdigest()
    if new_line_id == source_line["id"]:
        return False
    line_storage.insert_replacement_line(
        database,
        (
            new_line_id,
            source_line["repertoire_id"],
            source_line["name"],
            source_line["trained_color"],
            source_line["start_fen"],
            json.dumps(moves),
            source_line["created_at"],
        ),
    )
    line_storage.copy_training_depth(database, new_line_id, source_line["id"])
    line_storage.delete_replaced_line(database, source_line["id"])
    if certify:
        certify_admitted_route(database, source_line["repertoire_id"], validated_route)
    return True


def rewrite_card(
    database: sqlite3.Connection,
    repertoire_id: str,
    row: sqlite3.Row,
    moves: list[str],
    *,
    validated_route: dict | None = None,
    certify: bool = True,
) -> bool:
    from app.services.canonical_prefix import (
        ensure_line_in_scope,
        certify_admitted_route,
    )

    if validated_route is None:
        validated_route = ensure_line_in_scope(
            database, repertoire_id, row["start_fen"], moves, remember=False
        )
    new_id = card_id(row["start_fen"], moves)
    if new_id == row["id"]:
        return False
    existing = card_storage.card_exists(database, (new_id,))
    if not existing:
        columns = card_columns(database)
        values = dict(row)
        values.update(
            {
                "id": new_id,
                "repertoire_id": repertoire_id,
                "moves_json": json.dumps(moves),
                "state": "new",
                "due_date": datetime.now(timezone.utc).date().isoformat(),
                "interval_days": 0,
                "repetitions": 0,
                "lapses": 0,
                "introduced_at": None,
                "archived": 0,
                "superseded_by": None,
                "canonical_route_source": 1,
            }
        )
        card_storage.insert_replacement_card(database, columns, values)
    card_storage.mark_card_authored(database, (new_id,))
    card_storage.upsert_authored_membership(database, (repertoire_id, new_id))
    card_storage.delete_membership(database, (repertoire_id, row["id"]))
    remaining = card_storage.card_has_membership(database, (row["id"],))
    if not remaining:
        card_storage.archive_superseded_card(database, (new_id, row["id"]))
    if certify:
        certify_admitted_route(database, repertoire_id, validated_route)
    return True
