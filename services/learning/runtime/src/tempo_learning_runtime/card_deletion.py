"""Call-site composition for the existing deleted-card review guard."""
from tempo_learning_database.adapters.card_deletion import is_card_deleted
from .review_conflicts import ReviewConflict


def require_card_not_deleted(database, card_id):
    if is_card_deleted(database, card_id):
        raise ReviewConflict("card_deleted", "This card was permanently deleted")
