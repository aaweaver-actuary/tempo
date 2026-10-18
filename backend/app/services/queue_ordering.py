"""Compatibility exports; executable semantics are Learning-owned."""
from tempo_learning_runtime import queue_ordering as _owned
Sequence = _owned.Sequence
TypedDict = _owned.TypedDict
MISS_REASON = _owned.MISS_REASON
QueueOrderingRow = _owned.QueueOrderingRow
SavedQueueOrder = _owned.SavedQueueOrder
QueueOrderPlan = _owned.QueueOrderPlan
PRESENTATION_VERSION = _owned.PRESENTATION_VERSION
_queue_membership_hash = _owned._queue_membership_hash
_queue_order_fingerprint = _owned._queue_order_fingerprint
_preserved_queue_fingerprint = _owned._preserved_queue_fingerprint
_queue_order_provenance = _owned._queue_order_provenance
_queue_presentation_state = _owned._queue_presentation_state


def _priority_projection(rows):
    """Normalize the backend's separate bridge label into shared presentation."""
    from .legacy_game_evidence_scope import BRIDGE_MISS_REASON
    return [{**row, "gameplay_priority_reason": MISS_REASON}
            if row.get("gameplay_priority_reason") == BRIDGE_MISS_REASON else row for row in rows]


def _priority_queue_order(rows):
    originals = {row["id"]: row for row in rows}
    return [originals[row["id"]] for row in _owned._priority_queue_order(_priority_projection(rows))]


def _protected_queue_ids(rows, *, preserve_through_entry_id=None):
    return _owned._protected_queue_ids(_priority_projection(rows), preserve_through_entry_id=preserve_through_entry_id)


def _plan_daily_queue_order(rows, day, saved, *, preserve_through_entry_id=None):
    originals = {row["id"]: row for row in rows}
    seed, _, ordered = _owned._plan_daily_queue_order(_priority_projection(rows), day, saved,
        preserve_through_entry_id=preserve_through_entry_id)
    return seed, _queue_membership_hash(rows), [originals[row["id"]] for row in ordered]
