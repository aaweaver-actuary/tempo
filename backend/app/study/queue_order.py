"""Capture and preserve the existing queue presentation receipt on a borrowed transaction."""

import hashlib
from datetime import UTC, datetime

from tempo_storage.frozen_order import activation
from tempo_storage.study.review import storage

from app.study.policy.queue_ordering import (
    preserved_queue_fingerprint,
    queue_order_provenance,
)


def _queue_order_inputs(database, queue_date):
    entries = storage.queued_order_entries(database, queue_date)
    saved = storage.queue_membership_receipt(database, queue_date)
    return entries, saved, activation(database)


def capture_daily_queue_order(database, queue_date):
    """Prove the pre-mutation rows and token have received presentation."""
    return queue_order_provenance(*_queue_order_inputs(database, queue_date))


def preserve_daily_queue_order(database, queue_date: str, *, previous=None) -> None:
    """Carry a verified receipt across one controlled positional mutation."""
    membership_hash = preserved_queue_fingerprint(
        *_queue_order_inputs(database, queue_date), previous=previous
    )
    seed = int(hashlib.sha256(queue_date.encode()).hexdigest()[:15], 16)
    storage.write_queue_order_receipt(
        database,
        queue_date,
        seed,
        membership_hash,
        lambda: datetime.now(UTC).isoformat(),
    )
