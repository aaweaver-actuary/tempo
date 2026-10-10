"""Durable validation of one saved line/card per background slice."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import uuid

import chess

from .. import postgres_store
from ..database import background_read_connection, connection
from .canonical_prefix import (
    line_origin, position_key, prefix_projection, read_prefix, store_positions,
    validate_scoped_line,
)
from .durable_tasks import (
    advance_task_slice_in_transaction, complete_task_slice_in_transaction,
    enqueue_task_in_transaction, lock_current_slice, warm_completion_sql,
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
        ply BIGINT NOT NULL,source_revision BIGINT NOT NULL DEFAULT -1,PRIMARY KEY(preview_id,fen_key,in_scope))""",
)

RETAINED_PREVIEWS = 8
RETENTION_ROW_BUDGET = 64
RETENTION_VICTIM_BUDGET = 4
_EXPIRED_PREVIEW_ERROR = 'This compatibility preview expired. Check the prefix again before saving.'
_RETENTION_VICTIMS_SQL = (
    'SELECT id,state FROM canonical_prefix_previews WHERE repertoire_id=? AND id<>? '
    'AND id NOT IN (SELECT id FROM canonical_prefix_previews WHERE repertoire_id=? '
    'ORDER BY created_at DESC,id DESC LIMIT ?) '
    "AND id<>COALESCE((SELECT canonical_prefix_preview_id FROM repertoires WHERE id=?),'') "
    'ORDER BY created_at,id LIMIT ?'
)
_RETENTION_TASK_SQL = "SELECT id,state FROM background_tasks WHERE kind='canonical_prefix_preview' AND deduplication_key=?"
_RETIRE_TASK_SQL = (
    "UPDATE background_tasks SET state='superseded',generation=generation+1,"
    "lease_token=NULL,lease_expires_at=NULL WHERE id=? AND state<>'superseded'"
)
_INVALIDATE_PREVIEW_SQL = "UPDATE canonical_prefix_previews SET state='stale',last_error=? WHERE id=? AND state<>'stale'"
_DELETE_RESULTS_SQL = (
    'DELETE FROM canonical_prefix_results WHERE preview_id=? AND item_id IN '
    '(SELECT item_id FROM canonical_prefix_results WHERE preview_id=? ORDER BY item_id LIMIT ?)'
)
_DELETE_POSITIONS_SQL = (
    'DELETE FROM canonical_prefix_positions WHERE preview_id=? AND (fen_key,in_scope) IN '
    '(SELECT fen_key,in_scope FROM canonical_prefix_positions WHERE preview_id=? ORDER BY fen_key,in_scope LIMIT ?)'
)
_DELETE_EVENTS_SQL = (
    'DELETE FROM background_task_events WHERE task_id=? AND id IN '
    '(SELECT id FROM background_task_events WHERE task_id=? ORDER BY id LIMIT ?)'
)
_DELETE_RETIRED_TASK_SQL = (
    "DELETE FROM background_tasks WHERE id=? AND state='superseded' "
    'AND NOT EXISTS(SELECT 1 FROM background_task_events WHERE task_id=background_tasks.id)'
)
_DELETE_EMPTY_PREVIEW_SQL = (
    "DELETE FROM canonical_prefix_previews WHERE id=? AND state='stale' "
    'AND NOT EXISTS(SELECT 1 FROM canonical_prefix_results WHERE preview_id=canonical_prefix_previews.id) '
    'AND NOT EXISTS(SELECT 1 FROM canonical_prefix_positions WHERE preview_id=canonical_prefix_previews.id) '
    "AND NOT EXISTS(SELECT 1 FROM background_tasks WHERE kind='canonical_prefix_preview' "
    'AND deduplication_key=canonical_prefix_previews.id)'
)


def _warm_retention_sql() -> None:
    if postgres_store.configured():
        for statement in (_RETENTION_VICTIMS_SQL, _RETENTION_TASK_SQL + ' FOR UPDATE',
                          _RETIRE_TASK_SQL, _INVALIDATE_PREVIEW_SQL, _DELETE_RESULTS_SQL,
                          _DELETE_POSITIONS_SQL, _DELETE_EVENTS_SQL,
                          _DELETE_RETIRED_TASK_SQL, _DELETE_EMPTY_PREVIEW_SQL):
            postgres_store.postgres_sql(statement)
        warm_completion_sql()


def _cleanup_retention_victim(database, victim: dict, remaining_rows: int) -> int:
    """Spend this slice's shared row budget; parent cascades must be empty."""
    preview_id = victim['id']
    task_lock = ' FOR UPDATE' if hasattr(database, 'execute_native') else ''
    old_task = database.execute(_RETENTION_TASK_SQL + task_lock, (preview_id,)).fetchone()
    if old_task and old_task['state'] != 'superseded':
        # Retirement and certificate invalidation cannot be split across commits.
        if remaining_rows < 2:
            return remaining_rows
        remaining_rows -= database.execute(_RETIRE_TASK_SQL, (old_task['id'],)).rowcount
    if victim['state'] != 'stale':
        if not remaining_rows:
            return remaining_rows
        remaining_rows -= database.execute(_INVALIDATE_PREVIEW_SQL, (_EXPIRED_PREVIEW_ERROR, preview_id)).rowcount
    for statement, parent_id in ((_DELETE_RESULTS_SQL, preview_id),
                                  (_DELETE_POSITIONS_SQL, preview_id),
                                  (_DELETE_EVENTS_SQL, old_task['id'] if old_task else None)):
        if not remaining_rows:
            return remaining_rows
        if parent_id is not None:
            remaining_rows -= database.execute(statement, (parent_id, parent_id, remaining_rows)).rowcount
    if old_task and remaining_rows:
        deleted = database.execute(_DELETE_RETIRED_TASK_SQL, (old_task['id'],)).rowcount
        remaining_rows -= deleted
        if not deleted:
            return remaining_rows
    if remaining_rows:
        remaining_rows -= database.execute(_DELETE_EMPTY_PREVIEW_SQL, (preview_id,)).rowcount
    return remaining_rows


def _execute_retention_slice(task: dict) -> bool:
    """Clean at most four victims / 64 rows, checkpoint atomically, then yield."""
    payload = task["payload"]
    repertoire_id = payload["repertoire_id"]
    retention_parameters = (repertoire_id, payload['preview_id'], repertoire_id, RETAINED_PREVIEWS, repertoire_id)
    _warm_retention_sql()
    with background_read_connection(authoritative=True) as database:
        prepared_ids = {row['id'] for row in database.execute(
            _RETENTION_VICTIMS_SQL, (*retention_parameters, RETENTION_VICTIM_BUDGET)).fetchall()}
    with connection(background=True) as database:
        read_prefix(database, repertoire_id, lock=True)
        lease_current = lock_current_slice(database, task) if hasattr(database, 'execute_native') else database.execute(
            "SELECT 1 FROM background_tasks WHERE id=? AND generation=? AND lease_token=? AND state='leased'",
            (task['id'], task['generation'], task['lease_token']),
        ).fetchone()
        if not lease_current:
            return False
        remaining_rows = RETENTION_ROW_BUDGET
        # Creation, activation, and source writes use this same repertoire lock.
        # A prepared victim may now be protected; never trust the earlier page.
        victims = database.execute(_RETENTION_VICTIMS_SQL, (*retention_parameters, RETENTION_VICTIM_BUDGET)).fetchall()
        for victim in victims:
            if not remaining_rows:
                break
            if victim['id'] in prepared_ids:
                remaining_rows = _cleanup_retention_victim(database, victim, remaining_rows)
        if not database.execute(_RETENTION_VICTIMS_SQL, (*retention_parameters, 1)).fetchone():
            return complete_task_slice_in_transaction(database, task)
        return advance_task_slice_in_transaction(database, task, next_phase='retention', next_payload=payload)


def initialize_sqlite_schema(database) -> None:
    for definition in TABLE_DEFINITIONS:
        database.execute(definition)
    if 'source_revision' not in {row[1] for row in database.execute('PRAGMA table_info(canonical_prefix_positions)')}:
        database.execute('ALTER TABLE canonical_prefix_positions ADD COLUMN source_revision BIGINT NOT NULL DEFAULT -1')
    database.execute('CREATE INDEX IF NOT EXISTS canonical_prefix_preview_versions ON canonical_prefix_previews(repertoire_id,expected_revision,source_revision,created_at DESC)')
    from .canonical_scope_schema import initialize_scope_schema
    initialize_scope_schema(database)


def request_preview(database, repertoire_id: str, moves: list[str]) -> dict:
    if repertoire_id.startswith("__"):
        raise ValueError("Canonical prefixes are only available for opening repertoires")
    if not database.execute("SELECT 1 FROM repertoires WHERE id=?", (repertoire_id,)).fetchone():
        raise KeyError("Repertoire not found")
    prefix = read_prefix(database, repertoire_id, lock=True)
    reusable = database.execute(
        "SELECT preview.id,preview.state FROM canonical_prefix_previews preview "
        "JOIN background_tasks task ON task.kind='canonical_prefix_preview' AND task.deduplication_key=preview.id "
        "WHERE preview.repertoire_id=? AND preview.moves_json=? AND preview.expected_revision=? "
        "AND preview.source_revision=? AND preview.state IN ('checking','ready','conflicts') "
        "AND task.state NOT IN ('failed','superseded') ORDER BY preview.created_at DESC LIMIT 1",
        (repertoire_id, json.dumps(moves), prefix["revision"], prefix["source_revision"]),
    ).fetchone()
    if reusable:
        return {"preview_id": reusable["id"], "state": reusable["state"], **prefix_projection(moves, prefix["revision"])}
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
            "LEFT JOIN canonical_prefix_results result ON result.preview_id=? AND result.item_id='card:' || card.id "
            "WHERE card.content_type='opening' AND card.archived=0 "
            "AND (EXISTS(SELECT 1 FROM repertoire_cards source_link WHERE source_link.card_id=card.id AND source_link.repertoire_id=? AND source_link.canonical_route_source=1) "
            "OR (card.repertoire_id=? AND card.canonical_route_source=1 AND NOT EXISTS(SELECT 1 FROM repertoire_cards owner_link WHERE owner_link.card_id=card.id AND owner_link.repertoire_id=?))) "
            "AND card.id>? AND (result.status IS NULL OR result.status='pending') ORDER BY card.id LIMIT 1",
            (preview["id"], preview["repertoire_id"], preview["repertoire_id"], preview["repertoire_id"], cursor),
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
    if payload['phase'] == 'retention':
        return _execute_retention_slice(task)
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
                    "SELECT result.origin_json FROM canonical_prefix_results result "
                    "JOIN canonical_prefix_previews preview ON preview.id=result.preview_id "
                    "WHERE result.preview_id=? AND result.item_id=? AND result.status='valid' AND preview.source_revision=?",
                    (prefix["preview_id"], item["item_id"], prefix["source_revision"]),
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
        current = read_prefix(database, preview["repertoire_id"], lock=True)
        lease_current = lock_current_slice(database, task) if hasattr(database, "execute_native") else database.execute(
            "SELECT 1 FROM background_tasks WHERE id=? AND generation=? AND lease_token=? AND state='leased'",
            (task["id"], task["generation"], task["lease_token"]),
        ).fetchone()
        if not lease_current:
            return False
        if current["source_revision"] != preview["source_revision"] or current["revision"] != preview["expected_revision"]:
            database.execute("UPDATE canonical_prefix_previews SET state='stale',last_error=? WHERE id=?",
                             ("The repertoire changed. Check the prefix again.", preview["id"]))
            database.execute("UPDATE background_tasks SET priority=200 WHERE id=?", (task["id"],))
            return advance_task_slice_in_transaction(database, task, next_phase='retention',
                                                     next_payload={**payload, 'phase': 'retention'})
        if item and result:
            database.execute(
                "INSERT INTO canonical_prefix_results(preview_id,item_id,name,status,reason,disagreement_ply,origin_json,scope_start_ply) "
                "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(preview_id,item_id) DO UPDATE SET status=excluded.status,reason=excluded.reason,"
                "disagreement_ply=excluded.disagreement_ply,origin_json=excluded.origin_json,scope_start_ply=excluded.scope_start_ply",
                (preview["id"], item["item_id"], item["name"], result["status"], result.get("reason"), result.get("disagreement_ply"),
                 json.dumps(result["origin"]) if result.get("origin") is not None else None, result.get("scope_start_ply")),
            )
            if result["status"] == "valid":
                store_positions(database, preview["id"], result["positions"])
            return advance_task_slice_in_transaction(database, task, next_phase=payload["phase"], next_payload={
                **payload, "cursor": item["id"], "suggestion": suggestion,
                "progress": payload.get("progress", False) or result["status"] == "valid",
            })
        if payload["phase"] == "lines":
            return advance_task_slice_in_transaction(database, task, next_phase="cards", next_payload={**payload, "phase": "cards", "cursor": ""})
        # Disconnected items are finalized one at a time, retaining bounded writes.
        pending = database.execute("SELECT item_id FROM canonical_prefix_results WHERE preview_id=? AND status='pending' ORDER BY item_id LIMIT 1", (preview["id"],)).fetchone()
        if pending and payload.get("progress"):
            return advance_task_slice_in_transaction(database, task, next_phase="lines", next_payload={
                **payload, "phase": "lines", "cursor": "", "pass": payload.get("pass", 0) + 1, "progress": False,
            })
        if pending:
            database.execute("UPDATE canonical_prefix_results SET status='conflict' WHERE preview_id=? AND item_id=?", (preview["id"], pending["item_id"]))
            return advance_task_slice_in_transaction(database, task, next_phase="cards", next_payload=payload)
        conflict = database.execute("SELECT 1 FROM canonical_prefix_results WHERE preview_id=? AND status='conflict' LIMIT 1", (preview["id"],)).fetchone()
        database.execute("UPDATE canonical_prefix_previews SET state=?,suggestion_json=? WHERE id=?",
                         ("conflicts" if conflict else "ready", json.dumps(suggestion or []), preview["id"]))
        if not conflict and current["moves"] and current["moves"] == json.loads(preview["moves_json"]):
            # Recertification changes no opening assumption or study state.
            database.execute("UPDATE repertoires SET canonical_prefix_preview_id=? WHERE id=?",
                             (preview["id"], preview["repertoire_id"]))
        database.execute("UPDATE background_tasks SET priority=200 WHERE id=?", (task["id"],))
        return advance_task_slice_in_transaction(database, task, next_phase='retention',
                                                 next_payload={**payload, 'phase': 'retention'})
