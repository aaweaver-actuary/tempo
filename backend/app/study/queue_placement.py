"""Place a card on the Study queue inside the caller-owned transaction."""
from datetime import date

from tempo_legacy_database.adapters.study.review import storage
from app.services.queue_presentation import presentation_rows
from app.services.queue_ordering import _priority_queue_order
from app.services.queue_priority_promotion import StalePriorityPromotion, priority_changes
from app.study.queue_order import capture_daily_queue_order, preserve_daily_queue_order

def ensure_card_queued_after(
    database,
    card_id: str,
    after_cards: int = 4,
    attempt_state: str = "gameplay",
    priority_reason: str | None = None,
    *, queue_date: str | None = None, protect_presented: bool = False,
    preserve_order: bool = True, prepared_priority=None, priority_order=None,
) -> None:
    day = queue_date or date.today().isoformat()
    previous_order = capture_daily_queue_order(database, day) if preserve_order else None
    if prepared_priority is not None:
        if (not protect_presented or preserve_order
                or (day,card_id,priority_reason) != (prepared_priority.queue_date,
                    prepared_priority.card_id,prepared_priority.priority_reason)
                or storage.priority_ownership_state(database,day,target_card_id=card_id) != prepared_priority.ownership_state):
            raise StalePriorityPromotion('priority_presentation_changed')
    entry = storage.queued_entry(database, day, card_id)
    if prepared_priority is not None and (not entry or entry['id'] != prepared_priority.entry_id):
        raise StalePriorityPromotion('priority_target_changed')
    if not entry:
        cycle = storage.next_cycle(database, day, card_id)
        entry = storage.insert_entry(database, day, card_id, cycle, protect_presented, attempt_state, priority_reason)
    elif priority_reason:
        storage.set_priority(database, priority_reason, entry["id"])
    if protect_presented:
        if prepared_priority is not None:
            changes = prepared_priority.changes
        else:
            rows = presentation_rows(database, day)
            if priority_reason in {'product_game_lapse','product_game_encounter'}:
                changes = priority_changes(rows,card_id,priority_reason,priority_order=priority_order)
            else:
                changes = tuple((row['id'],row['position'],slot['position'])
                    for slot,row in zip(sorted(rows,key=lambda row:(row['position'],row['id'])),
                                        _priority_queue_order(rows))
                    if slot['position'] != row['position'])
        try:
            storage.apply_priority_positions(database,day,changes)
        except storage.PriorityPositionConflict as error:
            raise StalePriorityPromotion('priority_positions_changed') from error
    else:
        ordered_ids = [row[0] for row in storage.other_queued_entries(database, day, entry["id"])]
        ordered_ids.insert(min(after_cards, len(ordered_ids)), entry["id"])
        for position, entry_id in enumerate(ordered_ids):
            storage.set_position(database, position, entry_id)
    storage.classify_entry(database, entry["id"])
    if preserve_order:
        preserve_daily_queue_order(database, day, previous=previous_order)


def ensure_discovery_card_queued(database, card_id: str, queue_date: str) -> None:
    """Queue a retained admission without waiting on or moving issued work."""
    from tempo_legacy_database.adapters.queue_position_lock import try_lock_discovery_queue_position
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred
    if not try_lock_discovery_queue_position(database, queue_date, card_id):
        raise BackgroundAdmissionDeferred("Study queue is busy or target already issued")
    ensure_card_queued_after(database, card_id, after_cards=0, attempt_state="guided",
                            priority_reason="Discovery · added continuation", queue_date=queue_date,
                            protect_presented=True)
