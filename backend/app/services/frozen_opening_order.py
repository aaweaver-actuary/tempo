"""Legacy call-site composition for frozen Study preference.

This module only binds the existing caller transaction and pure policy helpers.
Database operations and application decisions each have one implementation.
Legacy callers still need domain-boundary migration; this facade is not that proof.
"""
from app.study import frozen_order as policy
from tempo_legacy_database.adapters import frozen_order as storage
from app.study.openings.frozen_math import content_fingerprint, READER_VERSION
from .real_game_feedback import MISS_REASON

activation = storage.activation
card_exposed = storage.card_exposed
has_legacy_origin = storage.has_legacy_origin
stage_ranks = storage.stage_ranks
activate_run = storage.activate_run
rollback_run = storage.rollback_run
record_queue_changes = storage.record_queue_changes
restore_queue_changes = storage.restore_queue_changes


def annotate(database, candidates):
    rows = [dict(row) for row in candidates]
    token = activation(database)
    identifiers = sorted({row.get('card_id', row.get('id')) for row in rows}) if token[0] is not None else []
    facts = storage.annotation_facts(database, identifiers, token=token)
    return policy.annotate(rows, facts, fingerprint=content_fingerprint)


def proposal_valid(database, planned):
    def has_outstanding_miss(card_id):
        from .real_game_feedback_adapter import has_outstanding_real_game_miss
        return has_outstanding_real_game_miss(database, card_id)

    return policy.proposal_valid(
        planned, activation(database, lock=True), miss_reason=MISS_REASON,
        has_outstanding_miss=has_outstanding_miss,
        current_definition_valid=lambda card_id: annotate(database, [{'card_id': card_id}])[0]['frozen_valid'],
    )


def order_queue_slots(database, rows, *, preserve_through_entry_id=None, annotated=None, exposed_ids=None):
    annotated = annotate(database, rows) if annotated is None else annotated
    return policy.order_queue_slots(annotated, miss_reason=MISS_REASON,
                                   card_exposed=lambda card_id: card_exposed(database, card_id),
                                   preserve_through_entry_id=preserve_through_entry_id, exposed_ids=exposed_ids)


def stage_run(database, plan, now):
    return storage.stage_run(database, plan, now, reader_version=READER_VERSION)


def queue_hash(membership_hash, token):
    from .queue_ordering import _queue_order_fingerprint
    return _queue_order_fingerprint(membership_hash, token)


def queue_state(database, day):
    from app.chess.opening_segments import stable_key
    return stable_key(storage.queue_state_rows(database, day))


def stale_queue_predicate():
    return storage.stale_queue_predicate(MISS_REASON)


def legacy_queue_predicate():
    return storage.legacy_queue_predicate(MISS_REASON)
