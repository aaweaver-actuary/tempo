"""Admission denial releases capacity and unavailable evidence never admits."""
from contextlib import contextmanager

import pytest
import redis

from app.services import redis_admission_gate
from app.services.activity_gate import ApplicationActivityGate, BackgroundAdmissionDeferred


@pytest.mark.parametrize('response',[0,redis.ConnectionError('controlled outage')])
def test_redis_background_admission_checks_once_and_never_sleeps(monkeypatch, response):
    calls = []
    class Server:
        def eval(self, *args):
            calls.append(args)
            if isinstance(response, Exception):
                raise response
            return response
        def zrem(self, *args):
            pytest.fail('unowned lease must not be released')
    monkeypatch.setattr(redis_admission_gate, 'client', lambda: Server())
    monkeypatch.setattr(redis_admission_gate.time, 'sleep', lambda *args: pytest.fail('occupied worker'))
    with pytest.raises(BackgroundAdmissionDeferred):
        with redis_admission_gate.background_lease():
            pytest.fail('denied lease')
    assert len(calls) == 1


def test_admission_redis_outage_is_unavailable_and_recovers(monkeypatch):
    monkeypatch.setattr(redis_admission_gate, 'configured', lambda: True)
    def outage():
        raise redis.ConnectionError('controlled outage')
    monkeypatch.setattr(redis_admission_gate, 'foreground_present', outage)
    gate = ApplicationActivityGate()
    with pytest.raises(BackgroundAdmissionDeferred, match='unavailable'):
        gate.check_background_admission()
    monkeypatch.setattr(redis_admission_gate, 'foreground_present', lambda: False)
    gate.check_background_admission()


def test_control_bookkeeping_does_not_claim_shared_admission(monkeypatch):
    gate = ApplicationActivityGate()
    monkeypatch.setattr(redis_admission_gate, 'configured', lambda: True)
    @contextmanager
    def denied():
        raise BackgroundAdmissionDeferred('foreground')
        yield
    monkeypatch.setattr(redis_admission_gate, 'background_lease', denied)
    with gate.background_job('test', 'control', yielding=True), gate.background_control():
        with gate.background_database_section():
            assert gate.active_background_sections == 1
    assert gate.active_background_sections == 0
    with pytest.raises(BackgroundAdmissionDeferred):
        with gate.background_database_section():
            pytest.fail('control context leaked')


def test_background_http_admission_reports_waiting_and_retry_without_false_success(monkeypatch):
    from contextlib import contextmanager
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    @contextmanager
    def denied():
        raise BackgroundAdmissionDeferred('Waiting for foreground activity')
        yield
    monkeypatch.setattr(main, 'background_read_connection', denied)
    response = TestClient(main.app).get('/api/repertoire-coverage/maia/available',
                                       headers={'X-Tempo-Work-Class':'background'})
    assert response.status_code == 503 and response.headers['Retry-After'] == '1'
    assert response.json() == {'detail':'Waiting for foreground activity'}


def test_short_control_receipt_is_admitted_during_existing_background_reservation(monkeypatch):
    monkeypatch.setattr(redis_admission_gate, 'configured', lambda: False)
    gate = ApplicationActivityGate()
    with gate.background_job('test', 'reservation', yielding=True):
        with gate.background_database_section():
            with gate.background_control(), gate.background_database_section():
                assert gate.active_background_sections == 2
            assert gate.active_background_sections == 1
            with pytest.raises(BackgroundAdmissionDeferred):
                with gate.background_database_section():
                    pytest.fail('discretionary section must yield')
    assert gate.active_background_sections == 0
@pytest.mark.parametrize('receipt_exists', [True, False])
def test_background_receipt_read_is_prompt_during_foreground_without_false_success(monkeypatch, receipt_exists):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from app import command_gateway, postgres_store
    from app.services.activity_gate import activity_gate
    monkeypatch.delenv('TEMPO_REDIS_URL', raising=False)
    monkeypatch.delenv('TEMPO_FOREGROUND_ACTIVITY_URL', raising=False)
    monkeypatch.setattr(postgres_store, 'configured', lambda: True)
    receipt = ('coverage.maia.submit', 'digest', 'retrying', None, None, 0,
               None, None, None, '{}', 0, 0) if receipt_exists else None
    observations = []
    def read(statement, parameters):
        assert 'FROM operation_receipts WHERE operation_id=%s' in statement
        assert parameters == ('saved-receipt',)
        return SimpleNamespace(fetchone=lambda: receipt)
    @contextmanager
    def connection(**options):
        observations.append(options)
        yield SimpleNamespace(raw=SimpleNamespace(execute=read))
    monkeypatch.setattr(postgres_store, 'connection', connection)
    with activity_gate.foreground(), activity_gate.background_request():
        result = command_gateway.read_operation('saved-receipt', background=True)
        assert result['state'] == ('retrying' if receipt_exists else 'unknown')
        assert 'response' not in result
        assert activity_gate.active_background_sections == 0
        assert not activity_gate.in_background_control
    assert observations == [{'read_only': True, 'background': True}]
