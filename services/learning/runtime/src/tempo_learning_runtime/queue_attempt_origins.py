from __future__ import annotations

def recover_queue_entry(database, card_id: str, request) -> dict:
    from .review_conflicts import ReviewConflict

    if not request.attempt_id or not request.queue_entry_id:
        raise ReviewConflict("queue_attempt_unprovable", "The original queue attempt cannot be verified. The completed result needs review.")
    contexts = database.execute(
        "SELECT * FROM queue_attempt_origins WHERE queue_entry_id=? AND card_id=?",
        (request.queue_entry_id, card_id),
    ).fetchall()
    if request.expected_revision is not None:
        contexts = [context for context in contexts if context["revision"] == request.expected_revision]
    if len(contexts) != 1:
        raise ReviewConflict("queue_attempt_unprovable", "The original queue attempt cannot be verified. The completed result needs review.")
    origin = dict(contexts[0])
    current = database.execute("SELECT revision,start_fen,moves_json,trained_color,content_type FROM cards WHERE id=?", (card_id,)).fetchone()
    if not current or any(current[column] != origin[column] for column in ("revision", "start_fen", "moves_json", "trained_color", "content_type")) or (origin["legacy"] and request.expected_revision is None and origin["revision"] != 1):
        raise ReviewConflict("card_revision_changed", "The card changed after this attempt. The completed result needs review.")
    if origin["last_status"] in {"buried", "complete"} and not origin["review_result_json"]:
        raise ReviewConflict("queue_attempt_retired", "This queue attempt was retired without a saved review. The completed result needs review.")
    return {**origin, "id": origin["queue_entry_id"],
            "status": "complete" if origin["review_result_json"] else "queued", "_recovered": True}

def retain_recovered_completion(database, entry: dict, result_json: str) -> None:
    """A removed projection still needs its canonical competing-review context."""
    database.execute(
        "UPDATE queue_attempt_origins SET last_status='complete',review_result_json=? "
        "WHERE queue_entry_id=? AND card_id=? AND revision=?",
        (result_json, entry["id"], entry["card_id"], entry["revision"]),
    )

def validate_failure_marker(database, entry_id: int, card_id=None, expected_revision=None) -> None:
    """A queue-only legacy marker cannot choose between replacement contexts."""
    from .review_conflicts import ReviewConflict

    contexts = database.execute("SELECT * FROM queue_attempt_origins WHERE queue_entry_id=?", (entry_id,)).fetchall()
    current = database.execute("""SELECT c.id,c.revision,c.start_fen,c.moves_json,c.trained_color,c.content_type
        FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.id=?""", (entry_id,)).fetchone()
    if card_id is not None:
        contexts = [origin for origin in contexts if origin["card_id"] == card_id
                    and (expected_revision is None or origin["revision"] == expected_revision)]
    if not current or len(contexts) != 1 or current["id"] != contexts[0]["card_id"] or any(
        current[column] != contexts[0][column]
        for column in ("revision", "start_fen", "moves_json", "trained_color", "content_type")
    ):
        raise ReviewConflict("queue_attempt_unprovable", "The guided attempt no longer matches this queue entry. Its completed review needs reconciliation.")
