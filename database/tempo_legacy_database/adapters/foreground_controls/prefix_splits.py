"""Prefix-card locks, published usage facts and acceptance/rejection storage."""


def locked_prefix_card(database, card_id: str):
    return database.execute_native(
        "SELECT id FROM cards WHERE id=%s FOR UPDATE", (card_id,),
    ).fetchone()


def published_usage(database, card_id: str):
    return database.execute(
        "SELECT COUNT(DISTINCT step.line_id) line_count,"
        "COUNT(DISTINCT step.repertoire_id) repertoire_count "
        "FROM opening_graph_steps step "
        "JOIN opening_graph_publications publication "
        "ON publication.repertoire_id=step.repertoire_id "
        "AND publication.generation=step.generation "
        "WHERE step.card_id=?", (card_id,),
    ).fetchone()


def linked_repertoire_rows(database, parent_card_id: str, continuation_card_id: str):
    return database.execute(
                "SELECT repertoire_id FROM repertoire_cards WHERE card_id IN (?,?)",
                (parent_card_id, continuation_card_id),
            )


def mark_split_pending_validation(database, parent_card_id: str, continuation_card_id: str) -> None:
    database.execute(
            "UPDATE cards SET pending_validation=1 WHERE id IN (?,?)",
            (parent_card_id, continuation_card_id),
        )


def latest_failed_study_review_id(database, card_id: str):
    return database.execute(
        "SELECT COALESCE(MAX(id),0) FROM reviews "
        "WHERE card_id=? AND rating='again' AND source_kind IN ('study','game_lapse') "
        "AND invalidated_at IS NULL", (card_id,),
    ).fetchone()[0]


def store_split_rejection(database, latest_failed_review_id: int, card_id: str) -> None:
    database.execute(
        "UPDATE cards SET prefix_split_rejected_after_review_id=? WHERE id=?",
        (latest_failed_review_id, card_id),
    )
