"""Existing worker paths retain claim, callback, exception and failure-write order."""
import pytest

from tempo_game_evidence import worker
from tempo_game_evidence_database.adapters.store import EvidenceStore


@pytest.mark.parametrize('kind,operation', [('rescope', 'rescope'), ('reconcile', 'reconcile'),
                                           ('compare', 'compare'), ('existing-fallback', 'compare')])
def test_existing_worker_paths_forward_same_job_and_callback(monkeypatch, kind, operation):
    store = EvidenceStore('unused')
    job, callback, events = {'kind': kind}, object(), []
    monkeypatch.setattr(store, 'claim_job', lambda: events.append('claim') or job)

    def rescope(value):
        assert value is job
        events.append('rescope')

    def reconcile(owner, value):
        assert owner is store and value is job
        events.append('reconcile')

    def compare(value, before_publish):
        assert value is job and before_publish is callback
        events.append('compare')

    monkeypatch.setattr(store, '_rescope', rescope)
    monkeypatch.setattr(worker, 'resume_reconciliation', reconcile)
    monkeypatch.setattr(store, '_compare', compare)
    assert store.process_one(before_publish=callback) is True
    assert events == ['claim', operation]


def test_idle_worker_does_not_run_work_or_record_failure(monkeypatch):
    store = EvidenceStore('unused')
    events = []
    monkeypatch.setattr(store, 'claim_job', lambda: events.append('claim'))

    def forbidden(*_, **__):
        raise AssertionError('idle worker performed work')

    monkeypatch.setattr(store, '_compare', forbidden)
    monkeypatch.setattr(store, '_rescope', forbidden)
    monkeypatch.setattr(worker, 'resume_reconciliation', forbidden)
    monkeypatch.setattr(store, 'record_job_failure', forbidden)
    assert store.process_one(before_publish=object()) is False
    assert events == ['claim']


@pytest.mark.parametrize('kind', ['rescope', 'reconcile', 'compare'])
def test_work_error_retains_exception_and_fenced_failure_write_order(monkeypatch, kind):
    store = EvidenceStore('unused')
    job, events = {'kind': kind, 'job_key': 'job', 'lease_token': 'lease'}, []
    error = ValueError('synthetic work failure')
    monkeypatch.setattr(store, 'claim_job', lambda: events.append('claim') or job)

    def fail(*_, **__):
        events.append('work')
        raise error

    class Connection:
        def __enter__(self): events.append('failure.enter'); return self
        def __exit__(self, *_): events.append('failure.exit')
        def execute(self, _query, parameters):
            assert parameters == ('job', 'lease')
            assert "next_attempt_at=now()+interval '5 seconds'" in _query
            events.append('failure.write')

    monkeypatch.setattr(store, '_rescope', fail)
    monkeypatch.setattr(worker, 'resume_reconciliation', fail)
    monkeypatch.setattr(store, '_compare', fail)
    monkeypatch.setattr(store, 'connect', Connection)
    with pytest.raises(ValueError) as caught:
        store.process_one()
    assert caught.value is error
    assert events == ['claim', 'work', 'failure.enter', 'failure.write', 'failure.exit']


@pytest.mark.parametrize('origin', ['claim', 'cancellation'])
def test_claim_failure_or_base_exception_does_not_record_work_failure(monkeypatch, origin):
    store = EvidenceStore('unused')
    error = ValueError('claim failed') if origin == 'claim' else KeyboardInterrupt('stop')

    def fail(*_, **__):
        raise error

    def forbidden(*_):
        raise AssertionError('non-work failure was persisted')

    monkeypatch.setattr(store, 'claim_job', fail if origin == 'claim' else lambda: {'kind': 'compare'})
    monkeypatch.setattr(store, '_compare', fail)
    monkeypatch.setattr(store, 'record_job_failure', forbidden)
    with pytest.raises(type(error)) as caught:
        store.process_one()
    assert caught.value is error


def test_failure_write_error_retains_original_exception_context(monkeypatch):
    store = EvidenceStore('unused')
    work_error, write_error = ValueError('work failed'), RuntimeError('failure write unavailable')
    monkeypatch.setattr(store, 'claim_job', lambda: {'kind': 'compare'})

    def fail_work(*_):
        raise work_error

    def fail_connection():
        raise write_error

    monkeypatch.setattr(store, '_compare', fail_work)
    monkeypatch.setattr(store, 'connect', fail_connection)
    with pytest.raises(RuntimeError) as caught:
        store.process_one()
    assert caught.value is write_error and write_error.__context__ is work_error
