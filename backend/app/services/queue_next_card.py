"""Explicit next-head preparation, injected by the current application assembly.

The preparation callback only reads/prepares before the command writer opens.
The selected owner unit applies in the receipt-fenced borrowed writer.
Head issuance only observes durable owner facts; it never calls a
producer, projects repertoire content or runs optional preparation in a review.
"""
from collections.abc import Callable

from app.study.interfaces.next_card import PreparedNextCardWork, NoNextCardWork

_prepare: Callable[[], PreparedNextCardWork] | None = None
_source_pending: Callable[..., bool] | None = None


def configure_next_card_work(*, prepare: Callable[[], PreparedNextCardWork],
                             source_pending: Callable[..., bool] | None = None):
    global _prepare, _source_pending
    _prepare, _source_pending = prepare, source_pending


def pending_next_card_preparation(database, day, *, candidate_card_id=None):
    from tempo_legacy_database.adapters.study.game_feedback import installed
    priority = None
    if installed(database):
        from tempo_legacy_database.adapters.study.game_feedback import pending_feedback_preparation, feedback_queue_priorities
        if candidate_card_id is not None:
            priority = feedback_queue_priorities(database, (candidate_card_id,)).get(candidate_card_id)
        if pending_feedback_preparation(database, day, before_priority=priority):
            return True
    return bool(_source_pending and _source_pending(database, day, before_priority=priority))


def prepare_next_card_command(payload):
    if payload:
        from fastapi import HTTPException
        raise HTTPException(422, 'Next-card preparation takes no source or target override')
    return _prepare() if _prepare else NoNextCardWork()


def prepared_next_card_result(database, prepared: PreparedNextCardWork):
    result = prepared.apply(database)
    return dict(progressed=result.progressed, pending=result.pending, reason=result.reason)
