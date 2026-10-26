"""Existing Study attempt boundary; optional controls supply no source policy."""
from app.contracts.review_conflicts import ReviewConflict
from app.study.conflicts import ConflictError
from tempo_legacy_database.adapters.study.conflicts import PostgresConflictStore, installed
from tempo_legacy_database.adapters.study.recombination import current_presentation_available


def guard_current_attempt(database,queue_entry_id,card_id,revision):
    if not current_presentation_available(database,card_id):
        raise ReviewConflict('current_presentation_unavailable',
            'This presentation is no longer current. Refresh Study before continuing.')
    if not installed(database):
        return
    try:
        PostgresConflictStore(database).guard_attempt(queue_entry_id,card_id,revision)
    except ConflictError as error:
        raise ReviewConflict(str(error),
            'This repertoire occurrence is suspended or its scope is unproved. Resolve it and refresh Study.') from error
