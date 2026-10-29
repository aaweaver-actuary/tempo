"""Current R11 presentation guard at existing Study attempt boundaries."""
from app.contracts.review_conflicts import ReviewConflict
from tempo_legacy_database.adapters.study.recombination import current_presentation_available


def guard_current_attempt(database, queue_entry_id, card_id, revision):
    if not current_presentation_available(database, card_id):
        raise ReviewConflict('current_presentation_unavailable',
            'This presentation is no longer current. Refresh Study before continuing.')
