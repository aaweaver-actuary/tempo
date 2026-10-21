"""Redis wake/runtime boundaries retain serialization and clock call order."""
import ast
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.services import background_runtime


def test_wake_runtime_compositions_delegate_redis_operations_and_scripts_to_database_owner():
    services = Path(__file__).resolve().parents[1] / 'app/services'
    for name in ('background_wakes', 'background_runtime'):
        tree = ast.parse((services / f'{name}.py').read_text())
        assert not any(isinstance(node, ast.Attribute) and node.attr in {'eval', 'mget', 'from_url'}
                       for node in ast.walk(tree)), name
        assert not any(isinstance(node, ast.Constant) and isinstance(node.value, str)
                       and ('redis.call(' in node.value or 'tempo:diagnostics:worker:' in node.value)
                       for node in ast.walk(tree)), name
        # Other .set calls are application-owned ContextVars, not Redis.
        assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                       and node.func.attr == 'set' and isinstance(node.func.value, ast.Call)
                       and isinstance(node.func.value.func, ast.Name)
                       and node.func.value.func.id == '_diagnostic_client'
                       for node in ast.walk(tree)), name


def test_runtime_publication_keeps_client_slot_sample_serialization_order_and_ttl(monkeypatch):
    events = []

    class Server:
        def set(self, key, value, ex):
            events.append(('set', key, value, ex))

    def client():
        events.append('client')
        return Server()

    def slot():
        events.append('slot')
        return 7

    def sample(_measurement):
        events.append('sample')
        def serialize():
            events.append('serialize')
            return 'unchanged-encoded-stage'
        return SimpleNamespace(model_dump_json=serialize)

    monkeypatch.setattr(background_runtime.redis_admission_gate, 'configured', lambda: True)
    monkeypatch.setattr(background_runtime, '_diagnostic_client', client)
    monkeypatch.setattr(background_runtime, '_worker_slot', slot)
    monkeypatch.setattr(background_runtime.RuntimeMeasurement, 'sample', sample)
    background_runtime.RuntimeMeasurement('daily_queue').publish(force=True)
    assert events == ['client', 'slot', 'sample', 'serialize',
                      ('set', 'tempo:diagnostics:worker:7', 'unchanged-encoded-stage', 15)]


def test_runtime_snapshot_reads_same_sixteen_slots_before_sampling_clock(monkeypatch):
    events = []
    actual_now = datetime.now(timezone.utc)

    class Server:
        def mget(self, keys):
            events.append(('mget', keys))
            return [None] * 16

    def client():
        events.append('client')
        return Server()

    class Clock:
        @staticmethod
        def now(actual_timezone):
            assert actual_timezone == timezone.utc
            events.append('now')
            return actual_now

    monkeypatch.setattr(background_runtime.redis_admission_gate, 'configured', lambda: True)
    monkeypatch.setattr(background_runtime, '_diagnostic_client', client)
    monkeypatch.setattr(background_runtime, 'datetime', Clock)
    result = background_runtime.snapshot()
    assert result.available and result.workers == []
    assert events == ['client', ('mget', [f'tempo:diagnostics:worker:{slot}' for slot in range(16)]), 'now']
