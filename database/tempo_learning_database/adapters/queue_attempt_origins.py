"""Learning queue-attempt origin persistence on the caller's transaction."""
from __future__ import annotations


def recovery_origins(database, queue_entry_id, card_id):
    contexts = database.execute(
        "SELECT * FROM queue_attempt_origins WHERE queue_entry_id=? AND card_id=?",
        (queue_entry_id, card_id),
    ).fetchall()
    return contexts


def current_card_definition(database, card_id):
    current = database.execute(
        "SELECT revision,start_fen,moves_json,trained_color,content_type FROM cards WHERE id=?",
        (card_id,),
    ).fetchone()
    return current


def failure_marker_facts(database, entry_id):
    contexts = database.execute(
        "SELECT * FROM queue_attempt_origins WHERE queue_entry_id=?", (entry_id,)
    ).fetchall()
    current = database.execute(
        """SELECT c.id,c.revision,c.start_fen,c.moves_json,c.trained_color,c.content_type
        FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.id=?""",
        (entry_id,),
    ).fetchone()
    return contexts, current


def retain_recovered_completion(database, entry: dict, result_json: str) -> None:
    """A removed projection still needs its canonical competing-review context."""
    database.execute(
        "UPDATE queue_attempt_origins SET last_status='complete',review_result_json=? "
        "WHERE queue_entry_id=? AND card_id=? AND revision=?",
        (result_json, entry["id"], entry["card_id"], entry["revision"]),
    )
