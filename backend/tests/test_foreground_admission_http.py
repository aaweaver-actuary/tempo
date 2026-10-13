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
