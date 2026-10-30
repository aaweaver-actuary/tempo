"""Actual optional handoff assembly, including borrowed head observation."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.product_composition import feedback_release,managed_runtime,next_card_priority,provider_priority
from test_product_delivery_qualification import native_handoff,owned_postgres


def test_release_capabilities_bind_real_implementations_and_detect_missing_authority(monkeypatch):
    from tempo_legacy_database.adapters.study.game_feedback_authority import CurrentOwnerDecisions
    actual = feedback_release.feedback_capabilities()
    assert actual.policy_version == 'first_departure_v1' and actual.receipt_interface_version == '0.2.2'
    assert all(value is True for name,value in actual.model_dump().items() if name not in {
        'policy_version','receipt_interface_version'})
    monkeypatch.setattr(CurrentOwnerDecisions,'prepare_qualified',None)
    assert feedback_release.feedback_capabilities().current_authority is False


@pytest.mark.parametrize('runtime_id',[None,'a'*12+':'+'b'*32])
def test_optional_handoff_registration_opens_no_connection_or_provider_and_retains_study(monkeypatch,runtime_id):
    registered = {}
    def forbidden():
        pytest.fail('Optional registration opened a connection')
    monkeypatch.setattr(managed_runtime,'current_managed_runtime_identity',lambda:runtime_id)
    monkeypatch.setattr(next_card_priority,'register_current_next_card_priority',lambda **kwargs:registered.update(kwargs))
    feedback_release.register_feedback_next_card_priority(read=forbidden)
    assert registered['read'] is forbidden and callable(registered['capture'])
    assert callable(registered['local_handoff_prepare']) and callable(registered['source_pending'])
    # SQLite/unknown optional capability is a false hint, with no I/O.
    assert registered['source_pending'](object(),'2026-10-30') is False


def test_root_discards_preparation_when_fresh_boot_changes_before_writer(monkeypatch):
    registered = {}
    tokens = iter(('old','new'))
    monkeypatch.setattr(managed_runtime,'current_managed_runtime_identity',lambda:next(tokens))
    monkeypatch.setattr(next_card_priority,'register_current_next_card_priority',lambda **kwargs:registered.update(kwargs))
    monkeypatch.setattr(provider_priority,'prepare_determined_feedback',lambda **kwargs:SimpleNamespace(mode_runtime_id='old'))
    feedback_release.register_feedback_next_card_priority(read=lambda:pytest.fail('unused reader'))
    assert registered['local_handoff_prepare']() is None


def test_native_root_observes_source_and_applies_one_unit_only_after_receipt_fence(native_handoff,monkeypatch):
    """Full source/publications -> registered pure root -> same borrowed writer."""
    from app.services import queue_next_card
    from tempo_legacy_database.adapters.games import product_feedback
    from tempo_legacy_database.adapters.study.game_feedback import PostgresGameFeedbackStore
    case = native_handoff
    monkeypatch.setattr(queue_next_card,'_prepare',None)
    monkeypatch.setattr(queue_next_card,'_source_pending',None)
    feedback_release.register_feedback_next_card_priority(workspace=case.workspace,
        read=case.case.transaction,clock=case.clock)
    prepared = queue_next_card.prepare_next_card_command({})
    assert isinstance(prepared,feedback_release.PreparedCurrentGamesWork)
    assert prepared.work.original_delivery == case.original
    assert case.store.delivery(case.original.operation_id).state == 'pending'
    # Read hints use exactly the borrowed connection and transaction. Missing
    # optional schema/identity must not abort the caller or block ordinary Study.
    with case.case.transaction() as database:
        xid = database.execute_native('SELECT txid_current()').fetchone()[0]
        monkeypatch.setattr(managed_runtime,'current_managed_runtime_identity',lambda:pytest.fail('writer filesystem read'))
        monkeypatch.setattr(product_feedback.PostgresGamesStore,'_transaction',lambda *args:pytest.fail('writer opened a Games connection'))
        assert queue_next_card.pending_next_card_preparation(database,'2026-10-30')
        assert database.execute_native('SELECT txid_current()').fetchone()[0] == xid
        database.execute_native('ALTER TABLE product_feedback_deliveries RENAME TO source_temporarily_unavailable')
        assert not queue_next_card.pending_next_card_preparation(database,'2026-10-30')
        assert database.execute_native('SELECT txid_current()').fetchone()[0] == xid
        database.execute_native('ALTER TABLE source_temporarily_unavailable RENAME TO product_feedback_deliveries')
        assert prepared.apply(database).progressed
        assert PostgresGameFeedbackStore(database).get(case.original.command.key) is not None
        assert database.execute_native("SELECT state FROM product_feedback_deliveries WHERE operation_id=%s",
            (case.original.operation_id,)).fetchone()[0] == 'acknowledged'
