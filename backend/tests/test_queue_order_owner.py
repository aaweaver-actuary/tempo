"""Capture/preserve is one Study-owned operation across legacy callers."""
from app.study import queue_order
from app.services import review_service, queue_application, learning_review_adapter, prefix_split, threat_training
from app import queue_commands
from tempo_learning_runtime import queue_ordering


def test_capture_and_preserve_have_one_owner_at_actual_consumers():
    for consumer in (review_service, queue_application, learning_review_adapter, prefix_split, threat_training, queue_commands):
        assert consumer.capture_daily_queue_order is queue_order.capture_daily_queue_order
        assert consumer.preserve_daily_queue_order is queue_order.preserve_daily_queue_order


def test_public_policy_exports_preserve_existing_identity():
    for public, legacy in [('queue_membership_hash', '_queue_membership_hash'),
                           ('queue_order_fingerprint', '_queue_order_fingerprint'),
                           ('queue_order_provenance', '_queue_order_provenance'),
                           ('preserved_queue_fingerprint', '_preserved_queue_fingerprint')]:
        assert getattr(queue_ordering, public) is getattr(queue_ordering, legacy)


def test_capture_uses_one_borrowed_transaction_and_original_order(monkeypatch):
    events = []
    transaction = object()
    monkeypatch.setattr(queue_order.storage, 'queued_order_entries',
        lambda db, day: events.append(('entries', db, day)) or 'entries')
    monkeypatch.setattr(queue_order.storage, 'queue_membership_receipt',
        lambda db, day: events.append(('receipt', db, day)) or 'saved')
    monkeypatch.setattr(queue_order, 'activation', lambda db: events.append(('activation', db)) or 'token')
    monkeypatch.setattr(queue_order, 'queue_order_provenance',
        lambda entries, saved, token: events.append(('provenance', entries, saved, token)) or 'previous')
    assert queue_order.capture_daily_queue_order(transaction, 'day') == 'previous'
    assert events == [('entries', transaction, 'day'), ('receipt', transaction, 'day'),
                      ('activation', transaction), ('provenance', 'entries', 'saved', 'token')]
