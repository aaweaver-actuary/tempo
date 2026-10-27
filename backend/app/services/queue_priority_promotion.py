"""Prepare one protected Study priority overlay; the canonical writer rechecks it."""
from dataclasses import dataclass
from .redis_admission_gate import BackgroundAdmissionDeferred

from .queue_ordering import _priority_queue_order, _queue_presentation_state
from .queue_presentation import presentation_rows, MISS_REASON
from tempo_legacy_database.adapters.queue_refresh.presentation import prepare_presentation_queries


class StalePriorityPromotion(BackgroundAdmissionDeferred):
    """Abort the borrowed command/cursor transaction and prepare again."""


@dataclass(frozen=True)
class PreparedPriorityPromotion:
    queue_date: str
    card_id: str
    entry_id: int
    priority_reason: str
    presentation_state: str
    changes: tuple[tuple[int, int, int], ...]  # entry, previous position, new position


def priority_changes(rows, card_id, priority_reason):
    # The current owner has accepted this command. Its target priority is owed
    # in this transaction even before the new effect receipt is inserted.
    projected = [{**row, 'gameplay_priority_reason':priority_reason}
                 if row['card_id'] == card_id else row for row in rows]
    return tuple((row['id'],row['position'],slot['position'])
        for slot,row in zip(sorted(projected,key=lambda row:(row['position'],row['id'])),
                            _priority_queue_order(projected))
        if slot['position'] != row['position'])


def prepare_priority_promotion(queue_date, card_id, *, read,
                               priority_reason='product_game_encounter'):
    """Read finite pages before the writer; never grant source/admission authority.

    None means no existing queued target: canonical introduction keeps its normal
    existing writer path. Full current presentation evidence is rechecked by that
    writer, including eligibility and every issued/protected attempt.
    """
    prepare_presentation_queries(MISS_REASON)
    rows, after = [], 0
    while True:
        with read() as database:
            page = presentation_rows(database,queue_date,after_entry_id=after,limit=16)
        rows.extend(page)
        if len(rows) > 4096:
            raise StalePriorityPromotion('priority_preparation_exceeds_bound')
        if len(page) < 16:
            break
        after = page[-1]['id']
    targets = [row for row in rows if row['card_id'] == card_id]
    if len(targets) != 1:
        return None
    return PreparedPriorityPromotion(queue_date,card_id,targets[0]['id'],priority_reason,
        _queue_presentation_state(rows),priority_changes(rows,card_id,priority_reason))
