"""Legacy adapter exports; Learning owns the shared implementation."""

from tempo_learning_runtime import review_reconciliation as _owned

SCHEDULE_COLUMNS = _owned.SCHEDULE_COLUMNS
card_schedule_state = _owned.card_schedule_state
save_schedule_snapshot = _owned.save_schedule_snapshot
_write_card_state = _owned._write_card_state
_review_day = _owned._review_day
_next_state = _owned._next_state
reconcile_completed_review = _owned.reconcile_completed_review
