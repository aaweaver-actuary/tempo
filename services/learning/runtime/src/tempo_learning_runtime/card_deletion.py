from .review_conflicts import ReviewConflict


def is_card_deleted(database, card_id):
    return (
        database.execute(
            "SELECT 1 FROM deleted_cards WHERE card_id=?", (card_id,)
        ).fetchone()
        is not None
    )


def require_card_not_deleted(database, card_id):
    if is_card_deleted(database, card_id):
        raise ReviewConflict("card_deleted", "This card was permanently deleted")
