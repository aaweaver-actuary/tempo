"""Learning-owned deletion fact on the caller's connection/transaction."""

def is_card_deleted(database, card_id):
    return (
        database.execute(
            "SELECT 1 FROM deleted_cards WHERE card_id=?", (card_id,)
        ).fetchone()
        is not None
    )
