"""Focused composition checks on the combined feedback/queue/discovery tree."""
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.mark.parametrize('outstanding', [False, True])
def test_combined_queue_uses_feedback_projection_and_semantic_store(outstanding, monkeypatch):
    from app.services import queue_admission_adapter as queue
    from app.services.real_game_feedback import MISS_REASON
    from app.services import real_game_feedback_adapter as feedback
    from app.services.real_game_feedback_ports import MissCard
    from tempo_legacy_database.adapters import real_game_miss_projection as projection
    assert queue.outstanding_miss_query is projection.outstanding_miss_query
    assert queue.has_outstanding_real_game_miss is feedback.has_outstanding_real_game_miss
    assert queue.MISS_REASON == MISS_REASON
    database, calls = object(), []
    def factory(connection):
        assert connection is database
        def facts(card_id):
            calls.append(card_id)
            return MissCard(card_id, 'learning', 'day') if outstanding else None
        return SimpleNamespace(outstanding_card=facts)
    monkeypatch.setattr(feedback, 'BorrowedFeedbackStore', factory)
    assert queue.QueueAdmissionAdapter(database).has_outstanding_miss('physical') is outstanding
    assert calls == ['physical']


def test_combined_queue_root_keeps_caller_adapter_and_admission_arguments(monkeypatch):
    from app.services import queue_application as composition
    database, store, calls = object(), object(), []
    def factory(connection):
        calls.append(('adapter', connection))
        return store
    def admit(*arguments):
        calls.append(('application', *arguments))
        return 17
    monkeypatch.setattr(composition, 'QueueAdmissionAdapter', factory)
    monkeypatch.setattr(composition.queue_admission, 'admit_prioritized_opening_cards', admit)
    assert composition.admit_prioritized_opening_cards(database, 'day', {'rep': 2}, 9) == 17
    assert calls == [('adapter', database), ('application', store, 'day', {'rep': 2}, 9)]


def test_combined_discovery_command_wires_borrowed_store_and_semantic_identity(monkeypatch):
    from app import discovery_commands as composition
    database, store, calls = object(), object(), []
    payload = dict(opportunity_id='opportunity', selected_move_uci='e2e4', evidence_fingerprint='fingerprint')
    identity = composition.admission_intent_id('opportunity', 'e2e4', 'fingerprint')
    def factory(connection):
        calls.append(('adapter', connection))
        return store
    def accept(borrowed, selected, *, intent_id):
        calls.append(('application', borrowed, selected, intent_id))
        return dict(status='preparing', intent_id=intent_id)
    monkeypatch.setattr(composition, 'PostgresDiscoveryAcceptanceStore', factory)
    monkeypatch.setattr(composition, 'apply_discovery_acceptance', accept)
    assert composition.accept_discovery(database, payload) == dict(status='preparing', intent_id=identity)
    assert calls == [('adapter', database), ('application', store, payload, identity)]
