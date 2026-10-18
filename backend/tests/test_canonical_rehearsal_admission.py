"""Native canonical drivers yield without replacing claimed durable identities."""
import importlib.util
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import Mock

import pytest

from app.services import redis_admission_gate
from app.services.activity_gate import ApplicationActivityGate


@pytest.fixture
def canonical_proof(monkeypatch):
    path = Path(__file__).resolve().parents[2] / 'scripts/check_postgres_canonical_freshness.py'
    monkeypatch.syspath_prepend(str(path.parent))
    specification = importlib.util.spec_from_file_location('canonical_admission_proof', path)
    proof = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(proof)
    return proof


def test_canonical_task_driver_waits_for_real_local_policy_section_without_reclaiming_lease(canonical_proof, monkeypatch):
    proof = canonical_proof
    gate = ApplicationActivityGate()
    monkeypatch.setattr(redis_admission_gate, 'configured', lambda: False)
    entered, release = Event(), Event()
    task = {'id': 17, 'generation': 4, 'lease_token': 'original-lease', 'phase': 'matches'}
    claim = Mock(side_effect=[task, None])
    monkeypatch.setattr(proof, 'claim_owned', claim)
    closed = Mock()
    monkeypatch.setattr(proof.postgres_store, 'close_pools', closed)
    attempts, effects = [], []

    def publisher_read():
        with gate.background_control(), gate.background_database_section():
            entered.set()
            assert release.wait(2), 'The driver did not yield its local policy contention'

    def execute(original):
        attempts.append(original)
        with gate.background_job('comparison', 'original-game', yielding=True), gate.background_database_section():
            effects.append(original['lease_token'])

    with ThreadPoolExecutor(max_workers=1) as executor:
        publisher = executor.submit(publisher_read)
        assert entered.wait(2)
        try:
            # Releasing from the retry pause proves the first actual gate denial
            # happened before any business effect or second durable claim.
            import time
            original_sleep = time.sleep
            def yield_to_policy(_seconds):
                assert not effects and claim.call_count == 1
                release.set()
                publisher.result(timeout=2)
                original_sleep(0)
            try:
                import postgres_rehearsal_admission
            except ModuleNotFoundError:
                pass  # Original driver has no retry boundary; real denial must escape.
            else:
                monkeypatch.setattr(postgres_rehearsal_admission.time, 'sleep', yield_to_policy)
            assert proof.run_bounded_task_slices('comparison', 'original-game', execute) == {'matches'}
        finally:
            release.set()
            publisher.result(timeout=2)
    assert len(attempts) == 2 and all(original is task for original in attempts)
    assert effects == ['original-lease']
    assert claim.call_count == 2 and closed.call_count == 1
    assert task == {'id': 17, 'generation': 4, 'lease_token': 'original-lease', 'phase': 'matches'}


def test_canonical_admission_exhaustion_retains_original_task_and_cause(canonical_proof, monkeypatch):
    from postgres_rehearsal_admission import admitted_call
    import postgres_rehearsal_admission
    clock = iter([0, 11])
    monkeypatch.setattr(postgres_rehearsal_admission.time, 'monotonic', lambda: next(clock))
    task = {'id': 17, 'generation': 4, 'lease_token': 'original-lease'}
    deferred = redis_admission_gate.BackgroundAdmissionDeferred('Waiting for a database section')
    operation = Mock(side_effect=deferred)
    with pytest.raises(AssertionError, match='within 10 seconds') as failure:
        admitted_call(operation, task)
    assert failure.value.__cause__ is deferred
    operation.assert_called_once_with(task)
    assert task['lease_token'] == 'original-lease'


@pytest.mark.parametrize('failure', [RuntimeError('SQL failed'), ValueError('Invalid source')])
def test_canonical_task_driver_preserves_nonadmission_failure(canonical_proof, monkeypatch, failure):
    proof = canonical_proof
    task = {'id': 17, 'generation': 4, 'lease_token': 'original-lease', 'phase': 'matches'}
    claim = Mock(return_value=task)
    monkeypatch.setattr(proof, 'claim_owned', claim)
    operation = Mock(side_effect=failure)
    with pytest.raises(type(failure)) as raised:
        proof.run_bounded_task_slices('comparison', 'original-game', operation)
    assert raised.value is failure
    claim.assert_called_once()
    operation.assert_called_once_with(task)


def test_canonical_task_driver_preserves_original_120_slice_exhaustion(canonical_proof, monkeypatch):
    proof = canonical_proof
    task = {'id': 17, 'generation': 4, 'lease_token': 'original-lease', 'phase': 'matches'}
    claim, operation = Mock(return_value=task), Mock()
    monkeypatch.setattr(proof, 'claim_owned', claim)
    monkeypatch.setattr(proof.postgres_store, 'close_pools', Mock())
    with pytest.raises(AssertionError, match='failed to finish its bounded fixture'):
        proof.run_bounded_task_slices('comparison', 'original-game', operation)
    assert claim.call_count == operation.call_count == 120
