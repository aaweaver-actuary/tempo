"""Issue #225: real Redis I/O failure must refuse HTTP work before dispatch."""

from contextlib import contextmanager
import socket
import threading
import time
import uuid

from fastapi.testclient import TestClient
import pytest
import redis

from app import main, command_dispatch
from app.services import redis_admission_gate
from app.services.activity_gate import activity_gate


@contextmanager
def owned_unavailable_peer(fault):
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    redis_url = f'redis://127.0.0.1:{listener.getsockname()[1]}/0'
    received = threading.Event()
    release = threading.Event()
    peer_errors = []
    peer_thread = None
    if fault == 'nonreply':
        listener.listen(1)
        listener.settimeout(5)
        def accept_without_reply():
            try:
                with listener.accept()[0] as connection:
                    connection.settimeout(5)
                    assert connection.recv(4096)
                    received.set()
                    release.wait(5)
            except Exception as error:
                peer_errors.append(error)
        peer_thread = threading.Thread(target=accept_without_reply)
        peer_thread.start()
    # A bound, non-listening socket reserves the refusal port for this test.
    try:
        yield redis_url, received
    finally:
        release.set()
        if peer_thread:
            peer_thread.join(6)
            assert not peer_thread.is_alive()
        listener.close()
        assert not peer_errors


@pytest.mark.parametrize('fault', ['refusal', 'nonreply'])
def test_issue225_foreground_redis_failure_refuses_before_dispatch(monkeypatch, fault):
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: True)
    monkeypatch.setattr(command_dispatch, 'dispatch_command',
                        lambda *args, **kwargs: pytest.fail('Unavailable admission dispatched a review'))
    monkeypatch.setattr(main.postgres_store, 'connection',
                        lambda *args, **kwargs: pytest.fail('Unavailable admission opened a database'))
    original_operation = str(uuid.uuid4())
    envelope = {'outcome': 'correct', 'attempt_id': str(uuid.uuid4()), 'queue_entry_id': 101}
    redis_admission_gate.client.cache_clear()
    try:
        with owned_unavailable_peer(fault) as (redis_url, received):
            monkeypatch.setenv('TEMPO_REDIS_URL', redis_url)
            started = time.monotonic()
            response = TestClient(main.app, raise_server_exceptions=False).post(
                '/api/cards/retained-card/review', json=envelope,
                headers={'Idempotency-Key': original_operation})
            elapsed = time.monotonic() - started
            assert response.status_code == 503
            assert response.json() == {'detail': 'Foreground admission unavailable; check Redis service status and retry with the same Idempotency-Key'}
            assert response.headers['Retry-After'] == '1'
            # Production Redis connect/read timeouts are one second; this also
            # bounds accidental outer retries without raising global timeouts.
            assert elapsed < 3
            if fault == 'nonreply':
                assert received.is_set(), 'Real Redis client never reached the silent peer'
            assert not activity_gate.foreground_requests_active
            assert not activity_gate.in_background
    finally:
        redis_admission_gate.client().close()
        redis_admission_gate.client.cache_clear()


@pytest.mark.parametrize('status', [200, 202, 409])
def test_issue225_admitted_http_response_is_preserved(monkeypatch, status):
    import asyncio
    from starlette.requests import Request
    from fastapi.responses import JSONResponse
    monkeypatch.delenv('TEMPO_REDIS_URL', raising=False)
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: False)
    expected = JSONResponse(status_code=status, content={'state': str(status)})
    async def downstream(request):
        assert activity_gate.foreground_requests_active
        return expected
    request = Request({'type': 'http', 'method': 'POST', 'path': '/api/cards/card/review',
                       'headers': [], 'query_string': b''})
    assert asyncio.run(main.prioritize_foreground_requests(request, downstream)) is expected
    assert not activity_gate.foreground_requests_active


def test_issue225_downstream_redis_error_is_not_reported_as_admission_rejection(monkeypatch):
    import asyncio
    from starlette.requests import Request
    monkeypatch.delenv('TEMPO_REDIS_URL', raising=False)
    monkeypatch.setattr(main.postgres_store, 'configured', lambda: False)
    async def downstream(request):
        raise redis.ConnectionError('Publication may already have committed')
    request = Request({'type': 'http', 'method': 'POST', 'path': '/api/cards/card/review',
                       'headers': [], 'query_string': b''})
    with pytest.raises(redis.ConnectionError, match='already have committed'):
        asyncio.run(main.prioritize_foreground_requests(request, downstream))
    assert not activity_gate.foreground_requests_active


@pytest.fixture
def observation_gate(monkeypatch):
    from app.services import activity_gate as gate_module, study_priority
    gate=gate_module.ApplicationActivityGate()
    monkeypatch.setattr(main,'activity_gate',gate)
    monkeypatch.setattr(gate_module,'activity_gate',gate)
    monkeypatch.setattr(main.postgres_store,'configured',lambda:False)
    monkeypatch.setattr(redis_admission_gate,'configured',lambda:False)
    monkeypatch.setattr(study_priority,'_local_generation',1)
    monkeypatch.setattr(study_priority,'_local_policy',dict(generation=1,
        study_only_mode=True,expires_at_ms=10**16))
    monkeypatch.delenv('TEMPO_FOREGROUND_ACTIVITY_URL',raising=False)
    return gate


@pytest.mark.parametrize('path',[
    '/api/health','/api/system/foreground-active',
    '/api/system/foreground-requests-active','/api/system/tasks',
])
@pytest.mark.parametrize('headers',[[],[(b'x-tempo-work-class',b'foreground')]])
def test_issue491_monitor_poll_cannot_mint_foreground_or_starve_work(observation_gate,path,headers):
    import asyncio
    from fastapi.responses import JSONResponse
    from starlette.requests import Request
    gate=observation_gate
    async def scenario():
        observing,worked=asyncio.Event(),asyncio.Event()
        async def polling(request):
            assert gate.in_background and not gate.foreground_requests_active
            assert main.foreground_active()['active'] is False
            observing.set()
            await asyncio.wait_for(worked.wait(),1)
            return JSONResponse({'observed':True})
        async def background():
            await asyncio.wait_for(observing.wait(),1)
            with gate.background_job('game_feedback_history','poll-regression',yielding=True):
                # Checks happen while the monitor request remains open.
                for _ in range(3): gate.check_background_admission()
            worked.set()
        request=Request({'type':'http','method':'GET','path':path,'headers':headers,'query_string':b''})
        response,_=await asyncio.gather(main.prioritize_foreground_requests(request,polling),background())
        assert response.status_code==200
    asyncio.run(scenario())
    assert not gate.foreground_requests_active and not gate.in_background


@pytest.mark.parametrize('path',['/api/health','/api/system/foreground-active','/api/system/tasks'])
def test_issue491_monitor_never_clears_genuine_foreground_hold(observation_gate,path):
    import asyncio
    from fastapi.responses import JSONResponse
    from starlette.requests import Request
    from app.services.activity_gate import BackgroundAdmissionDeferred
    gate=observation_gate
    async def polling(request):
        assert gate.in_background and main.foreground_active()['active'] is True
        with pytest.raises(BackgroundAdmissionDeferred,match='foreground'):
            gate.check_background_admission()
        return JSONResponse({'active':True})
    request=Request({'type':'http','method':'GET','path':path,'headers':[],'query_string':b''})
    with gate.foreground():
        assert asyncio.run(main.prioritize_foreground_requests(request,polling)).status_code==200
        assert gate.foreground_requests_active
    assert not gate.foreground_requests_active


@pytest.mark.parametrize('method,path',[
    ('GET','/api/queue/window'),('POST','/api/cards/card/review'),
    ('POST','/api/system/browser-activity'),('GET','/api/system/tasks/task-one'),
])
def test_issue491_real_study_and_unknown_routes_keep_foreground_lease(observation_gate,method,path):
    import asyncio
    from fastapi.responses import JSONResponse
    from starlette.requests import Request
    from app.services.activity_gate import BackgroundAdmissionDeferred
    gate=observation_gate
    async def foreground(request):
        assert gate.foreground_requests_active and not gate.in_background
        with pytest.raises(BackgroundAdmissionDeferred):gate.check_background_admission()
        return JSONResponse({'protected':True})
    request=Request({'type':'http','method':method,'path':path,'headers':[],'query_string':b''})
    assert asyncio.run(main.prioritize_foreground_requests(request,foreground)).status_code==200
    assert not gate.foreground_requests_active


def test_issue491_real_browser_input_quiet_lease_still_defers_and_expires(observation_gate,monkeypatch):
    from app.services.activity_gate import BackgroundAdmissionDeferred
    import app.services.activity_gate as gate_module
    gate=observation_gate
    now=[100.0]
    monkeypatch.setattr(gate_module.time,'monotonic',lambda:now[0])
    gate.record_browser_activity(3,session_id='actual-input')
    with pytest.raises(BackgroundAdmissionDeferred):gate.check_background_admission()
    assert main.foreground_active()['active'] is True
    now[0]=103.001
    gate.check_background_admission()
    assert main.foreground_active()['active'] is False
