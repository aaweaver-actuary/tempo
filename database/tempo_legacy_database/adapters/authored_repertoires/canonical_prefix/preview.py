"""Existing finite preview projections and writes, without preview policy."""

from ...projections.repertoire_statistics import repertoire_presence
from ...repertoire_scope.game_refresh import leased_local_task, uses_native_queries
from ..prefix_commands import set_empty_prefix_preview


def reusable_preview(database, repertoire_id, moves_json, revision, source_revision):
    return database.execute(
        "SELECT preview.id,preview.state FROM canonical_prefix_previews preview "
        "JOIN background_tasks task ON task.kind='canonical_prefix_preview' AND task.deduplication_key=preview.id "
        "WHERE preview.repertoire_id=? AND preview.moves_json=? AND preview.expected_revision=? "
        "AND preview.source_revision=? AND preview.state IN ('checking','ready','conflicts') "
        "AND task.state NOT IN ('failed','superseded') ORDER BY preview.created_at DESC LIMIT 1",
        (repertoire_id, moves_json, revision, source_revision),
    ).fetchone()


def insert_preview_values(database, values):
    database.execute(
        "INSERT INTO canonical_prefix_previews(id,repertoire_id,moves_json,expected_revision,source_revision,created_at) VALUES(?,?,?,?,?,?)",
        values,
    )


def preview_task_projection(database, preview_id, repertoire_id):
    return database.execute(
        "SELECT preview.*,task.state task_state,task.last_error task_error FROM canonical_prefix_previews preview "
        "LEFT JOIN background_tasks task ON task.kind='canonical_prefix_preview' AND task.deduplication_key=preview.id "
        "WHERE preview.id=? AND preview.repertoire_id=?", (preview_id, repertoire_id),
    ).fetchone()


def conflict_rows(database, preview_id, after):
    return [dict(row) for row in database.execute(
        "SELECT item_id,name,reason,disagreement_ply FROM canonical_prefix_results "
        "WHERE preview_id=? AND status='conflict' AND item_id>? ORDER BY item_id LIMIT 21", (preview_id, after),
    )]


def conflict_count(database, preview_id):
    return database.execute("SELECT COUNT(*) FROM canonical_prefix_results WHERE preview_id=? AND status='conflict'", (preview_id,)).fetchone()[0]


def next_card(database, preview_id, repertoire_id, cursor):
    return database.execute(
        "SELECT card.id,card.start_fen,card.moves_json,card.kind name FROM cards card "
        "LEFT JOIN canonical_prefix_results result ON result.preview_id=? AND result.item_id='card:' || card.id "
        "WHERE card.content_type='opening' AND card.archived=0 "
        "AND (EXISTS(SELECT 1 FROM repertoire_cards source_link WHERE source_link.card_id=card.id AND source_link.repertoire_id=? AND source_link.canonical_route_source=1) "
        "OR (card.repertoire_id=? AND card.canonical_route_source=1 AND NOT EXISTS(SELECT 1 FROM repertoire_cards owner_link WHERE owner_link.card_id=card.id AND owner_link.repertoire_id=?))) "
        "AND card.id>? AND (result.status IS NULL OR result.status='pending') ORDER BY card.id LIMIT 1",
        (preview_id, repertoire_id, repertoire_id, repertoire_id, cursor),
    ).fetchone()


def next_line(database, preview_id, repertoire_id, cursor):
    return database.execute(
        "SELECT line.id,line.name,line.start_fen,line.moves_json FROM repertoire_lines line "
        "LEFT JOIN canonical_prefix_results result ON result.preview_id=? AND result.item_id='line:' || line.id "
        "WHERE line.repertoire_id=? AND line.id>? AND (result.status IS NULL OR result.status='pending') "
        "ORDER BY line.id LIMIT 1", (preview_id, repertoire_id, cursor),
    ).fetchone()


def stored_preview(database, preview_id):
    return database.execute("SELECT * FROM canonical_prefix_previews WHERE id=?", (preview_id,)).fetchone()


def prior_origin(database, preview_id, item_id, source_revision):
    return database.execute(
        "SELECT result.origin_json FROM canonical_prefix_results result "
        "JOIN canonical_prefix_previews preview ON preview.id=result.preview_id "
        "WHERE result.preview_id=? AND result.item_id=? AND result.status='valid' AND preview.source_revision=?",
        (preview_id, item_id, source_revision),
    ).fetchone()


def mark_stale(database, error, preview_id):
    database.execute("UPDATE canonical_prefix_previews SET state='stale',last_error=? WHERE id=?",
                             (error, preview_id))


def prioritize_retention(database, task_id):
    database.execute("UPDATE background_tasks SET priority=200 WHERE id=?", (task_id,))


def upsert_result_values(database, values):
    database.execute(
        "INSERT INTO canonical_prefix_results(preview_id,item_id,name,status,reason,disagreement_ply,origin_json,scope_start_ply) "
        "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(preview_id,item_id) DO UPDATE SET status=excluded.status,reason=excluded.reason,"
        "disagreement_ply=excluded.disagreement_ply,origin_json=excluded.origin_json,scope_start_ply=excluded.scope_start_ply",
        values,
    )


def next_pending_result(database, preview_id):
    return database.execute("SELECT item_id FROM canonical_prefix_results WHERE preview_id=? AND status='pending' ORDER BY item_id LIMIT 1", (preview_id,)).fetchone()


def mark_pending_conflict(database, preview_id, item_id):
    database.execute("UPDATE canonical_prefix_results SET status='conflict' WHERE preview_id=? AND item_id=?", (preview_id, item_id))


def conflicting_result(database, preview_id):
    return database.execute("SELECT 1 FROM canonical_prefix_results WHERE preview_id=? AND status='conflict' LIMIT 1", (preview_id,)).fetchone()


def finish_preview(database, state, suggestion_json, preview_id):
    database.execute("UPDATE canonical_prefix_previews SET state=?,suggestion_json=? WHERE id=?",
                         (state, suggestion_json, preview_id))
