"""Explicit next-head preparation, injected by the current application assembly.

The preparation callback finishes bounded local work before the command writer
opens. Head issuance only observes durable owner facts; it never calls a
producer, projects repertoire content or runs optional preparation in a review.
"""
from collections.abc import Callable

from app.study.interfaces.next_card import NextCardPreparation

_prepare: Callable[[], NextCardPreparation] | None = None
_source_pending: Callable[[object, str], bool] | None = None


def configure_next_card_work(*, prepare: Callable[[], NextCardPreparation],
                             source_pending: Callable[[object, str], bool] | None = None):
    global _prepare, _source_pending
    _prepare, _source_pending = prepare, source_pending


def pending_next_card_preparation(database, day):
    from tempo_legacy_database.adapters.study.game_feedback import installed
    if installed(database):
        from tempo_legacy_database.adapters.study.game_feedback import pending_feedback_preparation
        if pending_feedback_preparation(database, day):
            return True
    return bool(_source_pending and _source_pending(database, day))


def prepare_next_card_command(payload):
    if payload:
        from fastapi import HTTPException
        raise HTTPException(422, 'Next-card preparation takes no source or target override')
    return _prepare() if _prepare else NextCardPreparation()


def prepared_next_card_result(_database, prepared: NextCardPreparation):
    return dict(progressed=prepared.progressed, pending=prepared.pending, reason=prepared.reason)
