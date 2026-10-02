"""Durable validation of one saved line/card per background slice."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid

import chess

from ..database import background_read_connection, connection
from .canonical_prefix import (
    line_origin, position_key, prefix_projection, read_prefix, store_positions,
    validate_scoped_line,
)
from .durable_tasks import (
    advance_task_slice_in_transaction, complete_task_slice_in_transaction,
    enqueue_task_in_transaction, lock_current_slice,
)


TABLE_DEFINITIONS = (
    """CREATE TABLE IF NOT EXISTS canonical_prefix_previews(
        id TEXT PRIMARY KEY,repertoire_id TEXT NOT NULL REFERENCES repertoires(id) ON DELETE CASCADE,
        moves_json TEXT NOT NULL,expected_revision BIGINT NOT NULL,source_revision BIGINT NOT NULL,
        state TEXT NOT NULL DEFAULT 'checking',suggestion_json TEXT NOT NULL DEFAULT '[]',
        last_error TEXT,created_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS canonical_prefix_results(
        preview_id TEXT NOT NULL REFERENCES canonical_prefix_previews(id) ON DELETE CASCADE,
        item_id TEXT NOT NULL,name TEXT NOT NULL,status TEXT NOT NULL,reason TEXT,
        disagreement_ply BIGINT,origin_json TEXT,scope_start_ply BIGINT,
        PRIMARY KEY(preview_id,item_id))""",
    """CREATE TABLE IF NOT EXISTS canonical_prefix_positions(
        preview_id TEXT NOT NULL REFERENCES canonical_prefix_previews(id) ON DELETE CASCADE,
        fen_key TEXT NOT NULL,in_scope BIGINT NOT NULL,fen TEXT NOT NULL,route_json TEXT NOT NULL,
        ply BIGINT NOT NULL,PRIMARY KEY(preview_id,fen_key,in_scope))""",
)


def initialize_sqlite_schema(database) -> None:
    for definition in TABLE_DEFINITIONS:
        database.execute(definition)
    for event in ("INSERT", "UPDATE", "DELETE"):
        source = "OLD" if event == "DELETE" else "NEW"
        affected_repertoires = "id IN (OLD.repertoire_id,NEW.repertoire_id)" if event == "UPDATE" else f"id={source}.repertoire_id"
        for table in ("repertoire_lines", "repertoire_cards"):
            database.execute(
                f"CREATE TRIGGER IF NOT EXISTS canonical_source_{table}_{event.lower()} AFTER {event} ON {table} "
                f"BEGIN UPDATE repertoires SET scope_source_revision=scope_source_revision+1 WHERE {affected_repertoires}; END"
            )
    database.execute(
        "CREATE TRIGGER IF NOT EXISTS canonical_source_card_update AFTER UPDATE ON cards "
        "WHEN OLD.start_fen IS NOT NEW.start_fen OR OLD.moves_json IS NOT NEW.moves_json OR OLD.archived IS NOT NEW.archived "
        "OR OLD.repertoire_id IS NOT NEW.repertoire_id OR OLD.content_type IS NOT NEW.content_type "
        "BEGIN UPDATE repertoires SET scope_source_revision=scope_source_revision+1 "
        "WHERE id IN (OLD.repertoire_id,NEW.repertoire_id) OR id IN (SELECT repertoire_id FROM repertoire_cards WHERE card_id=NEW.id); END"
    )
    for event, source in (("INSERT", "NEW"), ("DELETE", "OLD")):
        database.execute(
            f"CREATE TRIGGER IF NOT EXISTS canonical_source_card_{event.lower()} AFTER {event} ON cards "
            f"BEGIN UPDATE repertoires SET scope_source_revision=scope_source_revision+1 "
            f"WHERE id={source}.repertoire_id OR id IN (SELECT repertoire_id FROM repertoire_cards WHERE card_id={source}.id); END"
        )


def request_preview(database, repertoire_id: str, moves: list[str]) -> dict:
    if repertoire_id.startswith("__"):
        raise ValueError("Canonical prefixes are only available for opening repertoires")
    if not database.execute("SELECT 1 FROM repertoires WHERE id=?", (repertoire_id,)).fetchone():
        raise KeyError("Repertoire not found")
    prefix = read_prefix(database, repertoire_id, lock=True)
    preview_id = str(uuid.uuid4())
    database.execute(
        "INSERT INTO canonical_prefix_previews(id,repertoire_id,moves_json,expected_revision,source_revision,created_at) VALUES(?,?,?,?,?,?)",
        (preview_id, repertoire_id, json.dumps(moves), prefix["revision"], prefix["source_revision"], datetime.now(timezone.utc).isoformat()),
    )
    # Prefix positions are bounded by the input limit, independent of repertoire size.
    seed = validate_scoped_line(chess.STARTING_FEN, moves, moves, [])
    store_positions(database, preview_id, seed["positions"])
    enqueue_task_in_transaction(
        database, "canonical_prefix_preview", preview_id,
        {"preview_id": preview_id, "repertoire_id": repertoire_id, "phase": "lines",
         "cursor": "", "pass": 0, "progress": False, "suggestion": None}, priority=65,
    )
    return {"preview_id": preview_id, "state": "checking", **prefix_projection(moves, prefix["revision"])}


def preview_projection(database, repertoire_id: str, preview_id: str, after: str = "") -> dict:
    preview = database.execute(
        "SELECT preview.*,task.state task_state,task.last_error task_error FROM canonical_prefix_previews preview "
        "LEFT JOIN background_tasks task ON task.kind='canonical_prefix_preview' AND task.deduplication_key=preview.id "
        "WHERE preview.id=? AND preview.repertoire_id=?", (preview_id, repertoire_id),
    ).fetchone()
    if preview is None:
        raise KeyError("Prefix preview not found")
    current = read_prefix(database, repertoire_id)
    state = preview["state"]
    error = preview["last_error"]
    if preview["task_state"] == "failed":
        state, error = "failed", preview["task_error"]
    elif current["revision"] != preview["expected_revision"] or current["source_revision"] != preview["source_revision"]:
        state, error = "stale", "The repertoire changed. Check the prefix again before saving."
    conflicts = [dict(row) for row in database.execute(
        "SELECT item_id,name,reason,disagreement_ply FROM canonical_prefix_results "
        "WHERE preview_id=? AND status='conflict' AND item_id>? ORDER BY item_id LIMIT 21", (preview_id, after),
    )]
    count = database.execute("SELECT COUNT(*) FROM canonical_prefix_results WHERE preview_id=? AND status='conflict'", (preview_id,)).fetchone()[0]
    return {"preview_id": preview_id, "state": state, "error": error,
            **prefix_projection(json.loads(preview["moves_json"]), int(preview["expected_revision"])),
            "suggestion": prefix_projection(json.loads(preview["suggestion_json"])),
            "conflicts": conflicts[:20], "conflict_count": count,
            "next_cursor": conflicts[19]["item_id"] if len(conflicts) > 20 else None}


def _next_item(database, preview: dict, payload: dict) -> dict | None:
    phase = payload["phase"]
    prefix_id = "card:" if phase == "cards" else "line:"
    cursor = payload.get("cursor", "")
    if phase == "cards":
        row = database.execute(
            "SELECT card.id,card.start_fen,card.moves_json,card.kind name FROM cards card "
            "WHERE (card.repertoire_id=? OR EXISTS(SELECT 1 FROM repertoire_cards link WHERE link.card_id=card.id AND link.repertoire_id=?)) AND card.content_type='opening' AND card.archived=0 "
            "AND card.id>? ORDER BY card.id LIMIT 1", (preview["repertoire_id"], preview["repertoire_id"], cursor),
        ).fetchone()
    else:
        row = database.execute(
            "SELECT line.id,line.name,line.start_fen,line.moves_json FROM repertoire_lines line "
            "LEFT JOIN canonical_prefix_results result ON result.preview_id=? AND result.item_id='line:' || line.id "
            "WHERE line.repertoire_id=? AND line.id>? AND (result.status IS NULL OR result.status='pending') "
            "ORDER BY line.id LIMIT 1", (preview["id"], preview["repertoire_id"], cursor),
        ).fetchone()
    return {**dict(row), "item_id": prefix_id + row["id"]} if row else None


def execute_prefix_preview_slice(task: dict) -> bool:
    payload = task["payload"]
    with background_read_connection() as database:
        stored_preview = database.execute("SELECT * FROM canonical_prefix_previews WHERE id=?", (payload["preview_id"],)).fetchone()
        if stored_preview is None:
            return False
        preview = dict(stored_preview)
        item = _next_item(database, preview, payload)
        prefix = read_prefix(database, preview["repertoire_id"])
        origin = None
        if item:
            origin = line_origin(database, preview["id"], item["start_fen"])
            # Saved histories survive removal/shortening of a prefix without rewriting cards.
            if origin is None and prefix["preview_id"]:
                prior = database.execute(
                    "SELECT origin_json FROM canonical_prefix_results WHERE preview_id=? AND item_id=? AND status='valid'",
                    (prefix["preview_id"], item["item_id"]),
                ).fetchone()
                if prior and prior["origin_json"] is not None:
                    origin = json.loads(prior["origin_json"])
                else:
                    origin = line_origin(database, prefix["preview_id"], item["start_fen"])
    # The read connection is closed before chess traversal.
    result = None
    suggestion = payload.get("suggestion")
    if item:
        moves = json.loads(item["moves_json"])
        try:
            result = validate_scoped_line(item["start_fen"], moves, json.loads(preview["moves_json"]), origin)
        except ValueError as error:
            result = {"status": "conflict", "reason": str(error), "disagreement_ply": None}
        if payload["phase"] == "lines" and payload.get("pass", 0) == 0 and position_key(item["start_fen"]) == position_key(chess.STARTING_FEN):
            if suggestion is None:
                suggestion = moves[:160]
            else:
                common_length = next((index for index, pair in enumerate(zip(suggestion, moves)) if pair[0] != pair[1]), min(len(suggestion), len(moves)))
                suggestion = suggestion[:common_length]
    with connection(background=True) as database:
        lease_current = lock_current_slice(database, task) if hasattr(database, "execute_native") else database.execute(
            "SELECT 1 FROM background_tasks WHERE id=? AND generation=? AND lease_token=? AND state='leased'",
            (task["id"], task["generation"], task["lease_token"]),
        ).fetchone()
        if not lease_current:
            return False
        current = read_prefix(database, preview["repertoire_id"], lock=True)
        if current["source_revision"] != preview["source_revision"] or current["revision"] != preview["expected_revision"]:
            database.execute("UPDATE canonical_prefix_previews SET state='stale',last_error=? WHERE id=?",
                             ("The repertoire changed. Check the prefix again.", preview["id"]))
            return complete_task_slice_in_transaction(database, task)
        if item and result:
            database.execute(
                "INSERT INTO canonical_prefix_results(preview_id,item_id,name,status,reason,disagreement_ply,origin_json,scope_start_ply) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(preview_id,item_id) DO UPDATE SET status=excluded.status,reason=excluded.reason,"
                "disagreement_ply=excluded.disagreement_ply,origin_json=excluded.origin_json,scope_start_ply=excluded.scope_start_ply",
                (preview["id"], item["item_id"], item["name"], result["status"], result.get("reason"), result.get("disagreement_ply"),
                 json.dumps(result["origin"]) if result.get("origin") is not None else None, result.get("scope_start_ply")),
            )
            if result["status"] == "valid" and payload["phase"] == "lines":
                store_positions(database, preview["id"], result["positions"])
            return advance_task_slice_in_transaction(database, task, next_phase=payload["phase"], next_payload={
                **payload, "cursor": item["id"], "suggestion": suggestion,
                "progress": payload.get("progress", False) or result["status"] == "valid",
            })
        if payload["phase"] == "lines":
            pending = database.execute("SELECT 1 FROM canonical_prefix_results WHERE preview_id=? AND status='pending' LIMIT 1", (preview["id"],)).fetchone()
            if pending and payload.get("progress"):
                return advance_task_slice_in_transaction(database, task, next_phase="lines", next_payload={
                    **payload, "cursor": "", "pass": payload.get("pass", 0) + 1, "progress": False,
                })
            return advance_task_slice_in_transaction(database, task, next_phase="cards", next_payload={**payload, "phase": "cards", "cursor": ""})
        # Disconnected items are finalized one at a time, retaining bounded writes.
        pending = database.execute("SELECT item_id FROM canonical_prefix_results WHERE preview_id=? AND status='pending' ORDER BY item_id LIMIT 1", (preview["id"],)).fetchone()
        if pending:
            database.execute("UPDATE canonical_prefix_results SET status='conflict' WHERE preview_id=? AND item_id=?", (preview["id"], pending["item_id"]))
            return advance_task_slice_in_transaction(database, task, next_phase="cards", next_payload=payload)
        conflict = database.execute("SELECT 1 FROM canonical_prefix_results WHERE preview_id=? AND status='conflict' LIMIT 1", (preview["id"],)).fetchone()
        database.execute("UPDATE canonical_prefix_previews SET state=?,suggestion_json=? WHERE id=?",
                         ("conflicts" if conflict else "ready", json.dumps(suggestion or []), preview["id"]))
        return complete_task_slice_in_transaction(database, task)
