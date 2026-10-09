"""Owned Redis publication fault; never pauses or edits shared broker queues."""
from contextlib import contextmanager
import os
from pathlib import Path
import selectors
import socket
import sys
import threading
import time
from urllib.parse import urlsplit, urlunsplit
import uuid

import redis

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.celery_app import celery_app


@contextmanager
def first_connection_without_replies(redis_url):
    endpoint = urlsplit(redis_url)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen(8)
    listener.settimeout(0.1)
    stopped = threading.Event()
    first_read = threading.Event()
    connections = []
    connection_threads = []

    def forward(client, first_connection):
        with client, socket.create_connection((endpoint.hostname, endpoint.port or 6379), timeout=2) as server:
            with selectors.DefaultSelector() as selector:
                selector.register(client, selectors.EVENT_READ, server)
                selector.register(server, selectors.EVENT_READ, client)
                while not stopped.is_set():
                    for selected, _ in selector.select(0.1):
                        data = selected.fileobj.recv(65536)
                        if not data:
                            return
                        if first_connection and selected.fileobj is client:
                            first_read.set()
                        if not (first_connection and selected.fileobj is server):
                            selected.data.sendall(data)

    def accept_connections():
        while not stopped.is_set():
            try:
                client = listener.accept()[0]
            except socket.timeout:
                continue
            connections.append(client)
            connection_thread = threading.Thread(target=forward, args=(client, len(connections) == 1))
            connection_threads.append(connection_thread)
            connection_thread.start()

    accept_thread = threading.Thread(target=accept_connections)
    accept_thread.start()
    try:
        yield urlunsplit(endpoint._replace(netloc=f'127.0.0.1:{listener.getsockname()[1]}')), first_read
    finally:
        stopped.set()
        accept_thread.join(3)
        for connection_thread in connection_threads:
            connection_thread.join(3)
            assert not connection_thread.is_alive(), 'Owned proxy connection did not close'
        listener.close()
        assert not accept_thread.is_alive()


def test_redis_publication_deadline_preserves_independent_delivery_and_connection_recovery():
    proof_started = time.perf_counter()
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Socket recovery proof requires an explicitly disposable Redis instance')
    redis_url = os.environ['TEMPO_REDIS_URL']
    server = redis.Redis.from_url(redis_url, socket_timeout=2, socket_connect_timeout=2)
    server.ping()
    queue_name = 'tempo-socket-proof-' + uuid.uuid4().hex
    failures = []
    stalled_thread = None
    try:
        with first_connection_without_replies(redis_url) as (proxy_url, first_read):
            def acquire_stalled_channel():
                try:
                    with celery_app.connection_for_write(proxy_url) as connection:
                        connection.transport.create_channel(connection.transport)
                except redis.RedisError as error:
                    failures.append(error)

            stalled_thread = threading.Thread(target=acquire_stalled_channel)
            stalled_thread.start()
            assert first_read.wait(3), 'Owned connection did not reach real Redis'
            # An independent foreground publication remains usable while this
            # connection waits; no shared broker or admission state is paused.
            with celery_app.connection_for_write(redis_url) as connection:
                with connection.SimpleQueue(queue_name) as queue:
                    queue.put({'receipt': queue_name, 'attempt': 1})
            assert stalled_thread.is_alive(), 'Fault did not overlap the independent publication'
            stalled_thread.join(7)
            assert not stalled_thread.is_alive() and len(failures) == 1
            with celery_app.connection_for_write(proxy_url) as connection:
                with connection.SimpleQueue(queue_name) as queue:
                    retained = queue.get(block=False)
                    assert retained.payload == {'receipt': queue_name, 'attempt': 1}
                    retained.ack()
                    queue.put({'receipt': queue_name, 'attempt': 2})
                    resumed = queue.get(block=False)
                    assert resumed.payload == {'receipt': queue_name, 'attempt': 2}
                    resumed.ack()
                    try:
                        duplicated = queue.get(block=False)
                    except queue.Empty:
                        pass
                    else:
                        duplicated.ack()
                        raise AssertionError('Connection recovery duplicated a publication')
        print('PASS test_redis_publication_deadline_preserves_independent_delivery_and_connection_recovery',
              f'elapsed_seconds={time.perf_counter() - proof_started:.3f}', flush=True)
    finally:
        if stalled_thread is not None:
            stalled_thread.join(3)
            assert not stalled_thread.is_alive()
        # UUID-qualified queue/binding keys belong only to this invocation.
        for owned_key in server.scan_iter(match=f'*{queue_name}*'):
            server.delete(owned_key)
        server.close()


if __name__ == '__main__':
    test_redis_publication_deadline_preserves_independent_delivery_and_connection_recovery()
