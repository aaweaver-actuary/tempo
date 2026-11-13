"""Redis control persistence preserves patched identities and async timing."""

from tempo_storage.redis_control import admission as admission_storage

import ast
import asyncio
from pathlib import Path
import threading

from app.services import redis_admission_gate


def test_two_redis_control_compositions_delegate_all_storage_mechanics_to_database_owner():
    services = Path(__file__).resolve().parents[1] / "app/services"
    operations = {
        "eval",
        "incr",
        "zrem",
        "zcard",
        "zremrangebyscore",
        "pipeline",
        "from_url",
        "execute",
        "execute_native",
        "cursor",
    }
    for name in ("redis_admission_gate", "study_priority"):
        tree = ast.parse((services / f"{name}.py").read_text())
        direct_operations = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute) and node.attr in operations
        ]
        assert not direct_operations, f"{name} retains Redis or SQL mechanics"
        private_admission = {
            "_FOREGROUND_KEY", "_BACKGROUND_KEY", "_FOREGROUND_LEASE_MS",
            "_BACKGROUND_LEASE_MS", "_REGISTER_FOREGROUND", "_CLAIM_BACKGROUND",
        }
        assert not any(
            isinstance(node, ast.Attribute) and node.attr in private_admission
            or isinstance(node, ast.alias) and node.name in private_admission
            for node in ast.walk(tree)
        ), f"{name} depends on admission storage internals"
        assert not any(
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "redis.call(" in node.value
            for node in ast.walk(tree)
        )


def test_async_foreground_lease_creates_client_and_samples_clock_before_worker_io(
    monkeypatch,
):
    owner = threading.get_ident()
    events = []
    key = "private:async-foreground"
    lease_tokens = []

    class Server:
        def eval(self, script, count, actual_key, now, token, lease):
            assert threading.get_ident() != owner
            assert script == admission_storage._REGISTER_FOREGROUND
            assert (count, actual_key, now, lease) == (1, key, 123000, 30000)
            lease_tokens.append(token)
            events.append("register")
            return 1

        def zrem(self, actual_key, token):
            assert threading.get_ident() != owner
            assert (actual_key, token) == (key, lease_tokens[0])
            events.append("release")

    server = Server()

    def client():
        assert threading.get_ident() == owner
        events.append("client")
        return server

    def clock():
        assert threading.get_ident() == owner
        events.append("clock")
        return 123

    monkeypatch.setattr(redis_admission_gate, "client", client)
    monkeypatch.setattr(redis_admission_gate.time, "time", clock)
    monkeypatch.setattr(admission_storage, "_FOREGROUND_KEY", key)

    async def work():
        async with redis_admission_gate.async_foreground_lease():
            events.append("body")

    asyncio.run(work())
    assert events == ["client", "clock", "register", "body", "client", "release"]


def test_foreground_projection_prunes_before_count_with_patched_key_and_existing_clock_order(
    monkeypatch,
):
    events = []
    key = "private:foreground-presence"

    class Pipeline:
        def zremrangebyscore(self, actual_key, low, now):
            events.append(("prune", actual_key, low, now))

        def zcard(self, actual_key):
            events.append(("count", actual_key))

        def execute(self):
            events.append(("execute",))
            return [0, 2]

    class Server:
        def pipeline(self, *, transaction):
            events.append(("pipeline", transaction))
            return Pipeline()

    def clock():
        events.append(("clock",))
        return 123

    def client():
        events.append(("client",))
        return Server()

    monkeypatch.setattr(redis_admission_gate, "client", client)
    monkeypatch.setattr(redis_admission_gate.time, "time", clock)
    monkeypatch.setattr(admission_storage, "_FOREGROUND_KEY", key)
    assert redis_admission_gate.foreground_present() is True
    assert events == [
        ("clock",),
        ("client",),
        ("pipeline", True),
        ("prune", key, "-inf", 123000),
        ("count", key),
        ("execute",),
    ]


def test_admission_storage_owns_fixed_leases_and_atomic_policy_telemetry(monkeypatch):
    from tempo_storage.redis_control import admission, policy

    events = []
    server = object()
    monkeypatch.setattr(admission, '_FOREGROUND_KEY', 'owned:foreground')
    monkeypatch.setattr(admission, '_BACKGROUND_KEY', 'owned:background')
    monkeypatch.setattr(admission, 'register_foreground', lambda *args: events.append(('foreground', args)))
    monkeypatch.setattr(admission, 'claim_background', lambda *args: events.append(('background', args)) or False)
    monkeypatch.setattr(admission, 'release_foreground', lambda *args: events.append(('release-foreground', args)))
    monkeypatch.setattr(admission, 'release_background', lambda *args: events.append(('release-background', args)))
    monkeypatch.setattr(policy, 'policy_snapshot', lambda *args: events.append(('telemetry', args)) or (0, '', ''))
    admission.acquire_foreground(server, 100, 'attempt')
    admission.record_browser_presence(server, 101, 'phone', 2.5)
    assert admission.acquire_background(server, 102, 'background') is False
    admission.remove_foreground(server, 'attempt')
    admission.remove_background(server, 'background')
    assert admission.policy_telemetry(server, 'policy', 'generation', 103) == (0, '', '')
    assert admission.foreground_renewal_seconds() == 10
    assert events == [
        ('foreground', (server, 'owned:foreground', 100, 'attempt', 30000)),
        ('foreground', (server, 'owned:foreground', 101, 'browser-activity:phone', 2500)),
        ('background', (server, 'owned:foreground', 'owned:background', 102, 'background', 5000)),
        ('release-foreground', (server, 'owned:foreground', 'attempt')),
        ('release-background', (server, 'owned:background', 'background')),
        ('telemetry', (server, 'owned:foreground', 'policy', 'generation', 103)),
    ]
