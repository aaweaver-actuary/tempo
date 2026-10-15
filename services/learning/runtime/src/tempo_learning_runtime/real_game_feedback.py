"""Retained Learning obligations; never reads source games or evidence tables."""

MISS_REASON = "Priority review · missed in a recent game"


def clear_satisfied_miss_priority(database, card_id):
    database.execute(
        "UPDATE learning_obligations SET satisfied_at=CURRENT_TIMESTAMP WHERE card_id=? AND satisfied_at IS NULL AND kind='real_game_miss' AND EXISTS(SELECT 1 FROM reviews r WHERE r.card_id=? AND r.source_kind='study' AND r.invalidated_at IS NULL AND CAST(r.reviewed_at AS TIMESTAMPTZ)>=learning_obligations.observed_at)",
        (card_id, card_id),
    )
    if not database.execute(
        "SELECT 1 FROM learning_obligations WHERE card_id=? AND kind='real_game_miss' AND satisfied_at IS NULL LIMIT 1",
        (card_id,),
    ).fetchone():
        database.execute(
            "UPDATE daily_queue SET gameplay_priority_reason=NULL WHERE card_id=? AND gameplay_priority_reason=?",
            (card_id, MISS_REASON),
        )


def outstanding_miss_sql(
    *, postgres=False, columns="DISTINCT event.card_id", additional_where=""
):
    return f"""SELECT {columns} FROM learning_obligations event JOIN cards card ON card.id=event.card_id
      WHERE event.kind='real_game_miss' AND event.satisfied_at IS NULL AND card.serving_active=1
        AND card.archived=0 AND card.pending_validation=0 AND card.superseded_by IS NULL
        AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=card.id)
        AND NOT EXISTS(SELECT 1 FROM reviews review WHERE review.card_id=card.id
          AND review.source_kind='study' AND review.invalidated_at IS NULL
          AND CAST(review.reviewed_at AS TIMESTAMPTZ)>=event.observed_at) {additional_where}"""


def has_outstanding_real_game_miss(database, card_id):
    return (
        database.execute(
            outstanding_miss_sql(columns="1", additional_where="AND card.id=? LIMIT 1"),
            (card_id,),
        ).fetchone()
        is not None
    )
