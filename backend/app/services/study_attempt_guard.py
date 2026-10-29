"""Current presentation and scoped conflict guards at Study attempt boundaries."""
from app.contracts.review_conflicts import ReviewConflict
from tempo_legacy_database.adapters.study.recombination import current_presentation_available
from tempo_legacy_database.adapters.study.conflicts import PostgresConflictStore, installed
from app.study.conflicts import ConflictError


def guard_current_attempt(database, queue_entry_id, card_id, revision):
    if not current_presentation_available(database, card_id):
        raise ReviewConflict('current_presentation_unavailable',
            'This presentation is no longer current. Refresh Study before continuing.')

    if installed(database):
        try:
            PostgresConflictStore(database).guard_attempt(queue_entry_id,card_id,revision)
        except ConflictError as error:
            raise ReviewConflict(str(error),
                'This repertoire occurrence is held by a conflict. Refresh Study after resolving it.') from error
