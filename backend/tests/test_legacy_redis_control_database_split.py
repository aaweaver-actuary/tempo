"""Redis control persistence preserves patched identities and async timing."""
import ast
import asyncio
from pathlib import Path
import threading

from app.services import redis_admission_gate


def test_two_redis_control_compositions_delegate_all_storage_mechanics_to_database_owner():
    services = Path(__file__).resolve().parents[1] / 'app/services'
    operations = {'eval', 'incr', 'zrem', 'zcard', 'zremrangebyscore', 'pipeline',
                  'from_url', 'execute', 'execute_native', 'cursor'}
    for name in ('redis_admission_gate', 'study_priority'):
        tree = ast.parse((services / f'{name}.py').read_text())
        direct_operations = [node for node in ast.walk(tree)
                             if isinstance(node, ast.Attribute) and node.attr in operations]
        assert not direct_operations, f'{name} retains Redis or SQL mechanics'
        assert not any(isinstance(node, ast.Constant) and isinstance(node.value, str)
                       and 'redis.call(' in node.value for node in ast.walk(tree))


def test_async_foreground_lease_creates_client_and_samples_clock_before_worker_io(monkeypatch):
    owner = threading.get_ident()
    events = []
    key = 'private:async-foreground'
    lease_tokens = []

    class Server:
        def eval(self, script, count, actual_key, now, token, lease):
            assert threading.get_ident() != owner
            assert script == redis_admission_gate._REGISTER_FOREGROUND
            assert (count, actual_key, now, lease) == (1, key, 123000, 30000)
            lease_tokens.append(token)
            events.append('register')
            return 1

        def zrem(self, actual_key, token):
            assert threading.get_ident() != owner
            assert (actual_key, token) == (key, lease_tokens[0])
            events.append('release')

    server = Server()

    def client():
        assert threading.get_ident() == owner
        events.append('client')
        return server

    def clock():
        assert threading.get_ident() == owner
        events.append('clock')
        return 123

    monkeypatch.setattr(redis_admission_gate, 'client', client)
    monkeypatch.setattr(redis_admission_gate.time, 'time', clock)
    monkeypatch.setattr(redis_admission_gate, '_FOREGROUND_KEY', key)

    async def work():
        async with redis_admission_gate.async_foreground_lease():
            events.append('body')

    asyncio.run(work())
    assert events == ['client', 'clock', 'register', 'body', 'client', 'release']


def test_foreground_projection_prunes_before_count_with_patched_key_and_existing_clock_order(monkeypatch):
    events = []
    key = 'private:foreground-presence'

    class Pipeline:
        def zremrangebyscore(self, actual_key, low, now):
            events.append(('prune', actual_key, low, now))

        def zcard(self, actual_key):
            events.append(('count', actual_key))

        def execute(self):
            events.append(('execute',))
            return [0, 2]

    class Server:
        def pipeline(self, *, transaction):
            events.append(('pipeline', transaction))
            return Pipeline()

    def clock():
        events.append(('clock',))
        return 123

    def client():
        events.append(('client',))
        return Server()

    monkeypatch.setattr(redis_admission_gate, 'client', client)
    monkeypatch.setattr(redis_admission_gate.time, 'time', clock)
    monkeypatch.setattr(redis_admission_gate, '_FOREGROUND_KEY', key)
    assert redis_admission_gate.foreground_present() is True
    assert events == [('clock',), ('client',), ('pipeline', True), ('prune', key, '-inf', 123000),
                      ('count', key), ('execute',)]
