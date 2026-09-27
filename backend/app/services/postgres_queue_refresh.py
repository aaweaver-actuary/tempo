"""Bounded PostgreSQL queue-refresh slices with durable phase and cursor state."""

from __future__ import annotations

from datetime import date
from typing import Any

from ..database import connection
from .. import postgres_store
from .activity_gate import activity_gate
from .durable_tasks import advance_task_slice_in_transaction, lock_current_slice


_ELIGIBILITY_PHASES = (
    "unlock_opening",
    "block_opening",
    "block_defense",
    "restore_due",
    "restore_study",
)
_UNLOCK_BATCH_SIZE = 8


def execute_postgres_queue_refresh_slice(task: dict[str, Any]) -> bool:
    """Apply one eligibility phase; later queue phases remain explicitly gated."""

    if not postgres_store.configured():
        raise RuntimeError("PostgreSQL queue refresh requires the PostgreSQL store")
    # Import lazily because main owns the SQLite-compatible eligibility SQL.
    from .. import main

    payload = task["payload"]
    queue_date = str(payload.get("queue_date") or date.today().isoformat())
    phase = str(payload.get("_queue_phase") or _ELIGIBILITY_PHASES[0])
    if phase not in _ELIGIBILITY_PHASES:
        raise RuntimeError(f"Queue refresh phase is not yet ported: {phase}")
    activity_gate.wait_for_foreground()
    with connection(background=True) as database:
        if not lock_current_slice(database, task):
            return False
        if phase == "unlock_opening":
            next_cursor = main._unlock_eligible_opening_cards(
                database, queue_date,
                after_card_id=str(payload.get("after_card_id") or ""),
                batch_size=_UNLOCK_BATCH_SIZE,
            )
            if next_cursor is not None:
                next_phase = phase
                next_payload = {"queue_date": queue_date, "_queue_phase": phase,
                                "after_card_id": next_cursor}
            else:
                next_phase = _ELIGIBILITY_PHASES[1]
                next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
        else:
            phase_handlers = {
                "block_opening": main._block_ineligible_opening_queue_entries,
                "block_defense": main._block_unapproved_defense_queue_entries,
                "restore_due": main._restore_eligible_due_queue_entries,
                "restore_study": main._restore_published_study_queue_entries,
            }
            phase_handlers[phase](database, queue_date)
            phase_index = _ELIGIBILITY_PHASES.index(phase)
            next_phase = (_ELIGIBILITY_PHASES[phase_index + 1]
                          if phase_index + 1 < len(_ELIGIBILITY_PHASES)
                          else "tactical_introductions")
            next_payload = {"queue_date": queue_date, "_queue_phase": next_phase}
        return advance_task_slice_in_transaction(
            database, task, next_phase=next_phase, next_payload=next_payload,
        )
