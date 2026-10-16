"""Study control must be fast, expiring, session-scoped and conservative."""

from contextlib import contextmanager
from types import SimpleNamespace
import json

import pytest

from app.services import redis_admission_gate, study_priority
from app.services.activity_gate import ApplicationActivityGate


@pytest.fixture
def control(monkeypatch):
    class Server:
        generation = 0
        policy = None
        leases = {}
        def incr(self, key):
            assert key == study_priority.GENERATION_KEY
            self.generation += 1
            return self.generation
        def eval(self, script, number, *args):
            if script == study_priority._PUBLISH:
                _, _, generation, encoded, ttl = args
                assert ttl == 15
                if generation != self.generation: return 0
                self.policy = encoded
                return 1
            if script == study_priority._READ:
                return [int(bool(self.leases)), self.policy or '', self.generation]
            assert script == redis_admission_gate._REGISTER_FOREGROUND
            key, now, session, duration = args
            self.leases[session] = now + duration
            return 1
    server = Server()
    monkeypatch.setattr(study_priority, 'control_client', lambda: server)
    monkeypatch.setattr(redis_admission_gate, 'client', lambda: server)
    monkeypatch.setattr(redis_admission_gate, 'configured', lambda: True)
    return server


def test_study_control_unavailable_expired_or_refreshing_never_authorizes_optional_work(control, monkeypatch):
    monkeypatch.setattr(study_priority.time, 'time', lambda: 100)
    assert study_priority.control_snapshot()['active'] is True
    control.generation = 2
    for policy in [dict(generation=1, study_only_mode=False, expires_at_ms=200_000),
                   dict(generation=2, study_only_mode=False, expires_at_ms=99_999)]:
        control.policy = json.dumps(policy)
        assert study_priority.control_snapshot()['available'] is False
    control.policy = json.dumps(dict(generation=2, study_only_mode=False, expires_at_ms=115_000))
    assert study_priority.control_snapshot() == dict(active=False, available=True,
        study_only_mode=False, policy_generation=2)


def test_study_control_poll_never_reads_postgres_and_old_refresh_cannot_override_new_policy(control, monkeypatch):
    from app import database, main
    monkeypatch.setattr(database, 'background_read_connection',
        lambda **kwargs: pytest.fail('A worker control poll queried the study database'))
    control.generation = 2
    control.policy = json.dumps(dict(generation=2, study_only_mode=True, expires_at_ms=10**16))
    assert main.foreground_active()['study_only_mode'] is True
    assert not control.eval(study_priority._PUBLISH, 2, study_priority.POLICY_KEY,
        study_priority.GENERATION_KEY, 1,
        json.dumps(dict(generation=1, study_only_mode=False, expires_at_ms=10**16)), 15)
    assert main.foreground_active()['study_only_mode'] is True


def test_study_control_refresh_reserves_generation_before_bounded_settings_read(control, monkeypatch):
    from app import database
    @contextmanager
    def reader(**options):
        assert control.generation == 1
        assert options == {'pool_timeout_seconds': 0.1}
        yield SimpleNamespace(execute=lambda *_: SimpleNamespace(fetchone=lambda: (True,)))
    monkeypatch.setattr(database, 'background_read_connection', reader)
    assert study_priority.refresh_policy()
    assert study_priority.control_snapshot()['study_only_mode'] is True


def test_study_activity_sessions_extend_independently(control):
    gate = ApplicationActivityGate()
    gate.record_browser_activity(session_id='phone')
    gate.record_browser_activity(session_id='desktop')
    assert set(control.leases) == {'browser-activity:phone', 'browser-activity:desktop'}
    previous = control.leases['browser-activity:desktop']
    gate.record_browser_activity(session_id='phone')
    assert control.leases['browser-activity:desktop'] == previous


def test_study_activity_final_pulse_preserves_remaining_quiet_time(control, monkeypatch):
    from app import main
    now = [100.0]
    monkeypatch.setattr(redis_admission_gate.time, 'time', lambda: now[0])
    main.record_browser_activity(session_id='released', expires_in_ms=3000)
    assert control.leases['browser-activity:released'] == 103_000
    now[0] = 102.5
    main.record_browser_activity(session_id='released', expires_in_ms=500)
    assert control.leases['browser-activity:released'] == 103_000


def test_study_priority_background_policy_poll_never_opens_database_and_unknown_pauses_optional_work(monkeypatch):
    from app.services import study_mode, study_priority, redis_admission_gate
    from app.services.activity_gate import activity_gate
    monkeypatch.setattr(redis_admission_gate, 'configured', lambda: True)
    monkeypatch.setattr(study_mode, 'background_read_connection', lambda: (_ for _ in ()).throw(AssertionError('control polled PostgreSQL')))
    policy = {'available':False,'study_only_mode':None}
    monkeypatch.setattr(study_priority,'control_snapshot',lambda: policy)
    with activity_gate.background_job('statistics','regression'):
        assert study_mode.study_only_enabled()
        policy.update(available=True,study_only_mode=False)
        assert not study_mode.study_only_enabled()
