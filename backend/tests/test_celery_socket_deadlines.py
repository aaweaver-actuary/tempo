"""Real Redis client I/O must yield when a reachable peer stops answering."""

from contextlib import contextmanager
import socket
import threading

from celery import Celery
from redis.exceptions import RedisError
import pytest

from app.celery_app import celery_app


@contextmanager
def unresponsive_redis_peer():
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(1)
    listener.settimeout(10)
    request_received = threading.Event()
    release_peer = threading.Event()

    def accept_without_reply():
        with listener.accept()[0] as connection:
            connection.settimeout(10)
            assert connection.recv(4096)
            request_received.set()
            release_peer.wait(10)

    peer_thread = threading.Thread(target=accept_without_reply)
    peer_thread.start()
    try:
        yield f'redis://127.0.0.1:{listener.getsockname()[1]}/0', request_received, release_peer
    finally:
        release_peer.set()
        peer_thread.join(11)
        listener.close()
        assert not peer_thread.is_alive()


@pytest.mark.parametrize('connection_role', ['broker', 'results'])
def test_celery_unresponsive_redis_read_yields_an_error_instead_of_freezing_wakes(connection_role):
    errors = []
    with unresponsive_redis_peer() as (redis_url, request_received, release_peer):
        def read_from_peer():
            try:
                if connection_role == 'broker':
                    with celery_app.connection_for_write(redis_url) as connection:
                        # One real transport acquisition, without an outer retry
                        # loop that could conceal an unbounded individual read.
                        connection.transport.create_channel(connection.transport)
                else:
                    application = Celery('deadline-proof', backend=redis_url)
                    application.conf.update(
                        redis_socket_timeout=celery_app.conf.redis_socket_timeout,
                        redis_socket_connect_timeout=celery_app.conf.redis_socket_connect_timeout,
                    )
                    try:
                        application.backend.client.ping()
                    finally:
                        application.backend.client.close()
                        application.close()
            except RedisError as error:
                errors.append(error)

        reader_thread = threading.Thread(target=read_from_peer)
        reader_thread.start()
        try:
            assert request_received.wait(3), 'Real Redis client did not reach the owned peer'
            reader_thread.join(7)
            assert not reader_thread.is_alive(), 'Reachable Redis peer froze a scheduler I/O operation'
            assert len(errors) == 1, 'Unresponsive Redis must fail rather than report publication success'
        finally:
            # Release even the failing baseline before asserting resource cleanup.
            release_peer.set()
            reader_thread.join(3)
            assert not reader_thread.is_alive()
