"""Study priority: bound examined candidates, not only returned unlocks."""

from app.services import queue_application


def test_postgres_unlock_pages_advance_past_rejected_candidates_without_parent_scan():
    class Result:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows

    class Database:
        def __init__(self): self.calls = []
        def execute_native(self, statement, parameters):
            self.calls.append((statement, parameters))
            if "state='locked'" in statement and statement.startswith("SELECT id FROM cards"):
                return Result([(f"card-{index:04}",) for index in range(17)])
            assert "bounded_cards AS MATERIALIZED" in statement
            assert "publication.state='ready'" in statement
            assert "FROM cards parent\n    CROSS JOIN LATERAL" not in statement
            return Result([])
        def execute(self, statement, parameters):
            raise AssertionError("Rejected candidates must not issue an unbounded UPDATE")

    database = Database()
    cursor = queue_application._unlock_eligible_opening_cards(
        database, '2026-10-16', after_card_id='', batch_size=8)
    assert cursor == 'card-0015'
    assert database.calls[0][1] == ('', 17)
    assert len(database.calls[1][1][0]) == 16


def test_postgres_unlock_dense_page_preserves_eight_write_bound_and_commit_recheck():
    class Result:
        def __init__(self, rows): self.rows = rows
        def fetchall(self): return self.rows

    class Database:
        def __init__(self): self.updated = []
        def execute_native(self, statement, parameters):
            if statement.startswith("SELECT id FROM cards"):
                return Result([(f"card-{index:04}",) for index in range(20)])
            return Result([(f"card-{index:04}",) for index in range(8)])
        def execute(self, statement, parameters):
            assert "opening_graph_publications" in statement
            assert "invalidated_at IS NULL" in statement
            self.updated.extend(parameters)

    database = Database()
    assert queue_application._unlock_eligible_opening_cards(
        database, '2026-10-16', after_card_id='', batch_size=8) == 'card-0007'
    assert database.updated == [f"card-{index:04}" for index in range(8)]


def test_study_priority_maintenance_setup_timeouts_and_closed_connections_back_off_without_losing_work(monkeypatch):
    import psycopg
    from app import tasks
    now = [100.0]
    monkeypatch.setattr(tasks.time, 'monotonic', lambda: now[0])
    calls = []
    @tasks.bounded_maintenance
    def owned_recovery_regression():
        calls.append(now[0])
        if len(calls) == 1: raise psycopg.errors.TransactionTimeout('setup timeout')
        if len(calls) == 2: raise psycopg.InterfaceError('closed connection')
        return 'durable checkpoint resumed'
    assert owned_recovery_regression() is False
    assert owned_recovery_regression() is False and len(calls) == 1
    now[0] += 1
    assert owned_recovery_regression() is False and len(calls) == 2
    now[0] += 1
    assert owned_recovery_regression() is False and len(calls) == 2
    now[0] += 1
    assert owned_recovery_regression() == 'durable checkpoint resumed'
    assert 'owned_recovery_regression' not in tasks._maintenance_cooldowns


def test_owned_fixture_database_renews_private_policy_without_touching_parent_control(monkeypatch):
    import importlib
    import json
    from contextlib import nullcontext
    from pathlib import Path
    import threading
    import time
    from types import SimpleNamespace
    from tempo_legacy_database import connections as database
    from app.services import study_priority

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    fixtures = importlib.import_module('check_postgres_graph_retention')
    migrations = importlib.import_module('apply_postgres_migrations')
    clock = [1000.0]
    refreshed = threading.Event()
    parent_keys = study_priority.POLICY_KEY, study_priority.GENERATION_KEY
    parent_policy = json.dumps({'generation': 41, 'study_only_mode': False,
                                'expires_at_ms': 999000})

    class SharedControl:
        def __init__(self):
            self.values = {parent_keys[0]: parent_policy, parent_keys[1]: '41'}

        def incr(self, key):
            self.values[key] = str(int(self.values.get(key, 0)) + 1)
            return int(self.values[key])

        def exists(self, *keys):
            return sum(key in self.values for key in keys)

        def eval(self, script, count, *arguments):
            keys, values = arguments[:count], arguments[count:]
            if script == study_priority._PUBLISH:
                if int(self.values[keys[1]]) != int(values[0]):
                    return 0
                self.values[keys[0]] = values[1]
                refreshed.set()
                return 1
            if script == study_priority._READ:
                return [0, self.values.get(keys[1], ''), self.values.get(keys[2], '')]
            return self.values.get(keys[0]) or self.incr(keys[0])

        def delete(self, *keys):
            for key in keys:
                self.values.pop(key, None)

    class FixtureDatabase:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, statement, *_):
            self.statement = statement.as_string() if hasattr(statement, 'as_string') else statement
            return self
        def fetchone(self):
            if 'shobj_description' in self.statement:
                return (fixtures.DISPOSABLE_DATABASE_MARKER + ':tempo_incident_' + 'f' * 32,)
            if 'study_only_mode' in self.statement:
                return (0,)
            return None

    control = SharedControl()
    monkeypatch.setenv('TEMPO_REDIS_URL', 'redis://fixture.invalid/0')
    monkeypatch.setattr(fixtures.uuid, 'uuid4', lambda: SimpleNamespace(hex='f' * 32))
    monkeypatch.setattr(fixtures.psycopg, 'connect', lambda *_args, **_kwargs: FixtureDatabase())
    monkeypatch.setattr(fixtures.postgres_store, 'close_pools', lambda: None)
    monkeypatch.setattr(fixtures, 'owned_admission_scope', lambda _: nullcontext())
    monkeypatch.setattr(migrations, 'apply_migrations', lambda _: None)
    monkeypatch.setattr(database, 'background_read_connection', lambda **_: FixtureDatabase())
    monkeypatch.setattr(study_priority, 'control_client', lambda: control)
    monkeypatch.setattr(study_priority.time, 'time', lambda: clock[0])
    monkeypatch.setattr(study_priority, 'REFRESH_SECONDS', 0.01)

    with fixtures.owned_fixture_database() as owned_name:
        current = study_priority.control_snapshot()
        assert current['available'] is True, 'Private fixture cannot borrow an expired parent policy'
        assert current['study_only_mode'] is False
        assert study_priority.POLICY_KEY != parent_keys[0]
        assert owned_name in study_priority.POLICY_KEY
        generation = current['policy_generation']
        clock[0] += study_priority.POLICY_SECONDS + 1
        refreshed.clear()
        deadline = time.monotonic() + 1
        while not study_priority.control_snapshot()['available']:
            assert time.monotonic() < deadline, 'Private policy producer did not renew its expiring lease'
            refreshed.wait(0.01)
        assert study_priority.control_snapshot()['policy_generation'] == generation
    assert (study_priority.POLICY_KEY, study_priority.GENERATION_KEY) == parent_keys
    assert control.values == {parent_keys[0]: parent_policy, parent_keys[1]: '41'}


def test_postgres_sparse_unlock_proof_retains_three_write_batches_while_bounding_rejected_pages(monkeypatch):
    import bisect
    from contextlib import nullcontext
    import importlib
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    proof = importlib.import_module('check_postgres_daily_study_dispatch')
    identifier = 'bounded-sparse-regression'
    locked = [f'{identifier}-locked-{index:05}' for index in range(15000)]
    eligible = set(locked[-21:])
    unlocked = []

    class Result:
        def __init__(self, rows, rowcount=0):
            self.rows, self.rowcount = rows, rowcount
        def fetchall(self): return self.rows
        def fetchone(self): return self.rows[0]
        def __iter__(self): return iter(self.rows)

    class Database:
        def execute_native(self, statement, parameters):
            if statement.startswith('SELECT id FROM cards'):
                after, limit = parameters
                first = bisect.bisect_right(locked, after)
                return Result([(card_id,) for card_id in locked[first:first + limit]])
            assert 'bounded_cards AS MATERIALIZED' in statement
            candidates, limit = parameters
            return Result([(card_id,) for card_id in candidates if card_id in eligible][:limit])
        def execute(self, statement, parameters):
            if statement.startswith('UPDATE cards'):
                for card_id in parameters:
                    assert card_id in eligible and card_id not in unlocked
                    unlocked.append(card_id)
                    locked.remove(card_id)
                return Result([], len(parameters))
            assert "state='new'" in statement
            return Result([(card_id,) for card_id in sorted(unlocked)])

    database = Database()
    monkeypatch.setattr(proof.postgres_store, 'connection', lambda **_: nullcontext(database))
    monkeypatch.setattr(proof.psycopg, 'connect', lambda *_args, **_kwargs: nullcontext(database))
    observed = proof.proof_sparse_unlock(identifier)
    assert observed['slice_count'] == 939
    assert observed['write_batches'] == [8, 8, 5]
    assert observed['rejected_pages'] == 936
    assert observed['maximum_examined_candidates'] == 16
    assert unlocked == sorted(eligible)


def test_native_priority_control_fixture_preserves_poll_deadline_and_uses_bounded_write_client(monkeypatch):
    import importlib
    from pathlib import Path
    import redis
    from app.services import redis_admission_gate, study_priority

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts'))
    proof = importlib.import_module('check_postgres_study_priority')
    monkeypatch.setenv('TEMPO_REDIS_URL', 'redis://127.0.0.1:1/0')
    # Construct only; neither client connects. Production poll/write deadlines
    # stay exactly as configured, including when this fixture is repaired.
    poll = study_priority.control_client.__wrapped__()
    writer = redis_admission_gate.client.__wrapped__()
    try:
        assert poll.connection_pool.connection_kwargs['socket_timeout'] == 0.05
        assert poll.connection_pool.connection_kwargs['socket_connect_timeout'] == 0.05
        assert writer.connection_pool.connection_kwargs['socket_timeout'] == 1
        assert writer.connection_pool.connection_kwargs['socket_connect_timeout'] == 1
    finally:
        poll.close()
        writer.close()

    calls = []
    class SetupClient:
        def eval(self, *_):
            calls.append('bounded-write')
            return 1
    class PollClient:
        def eval(self, *_):
            raise redis.TimeoutError('Controlled fixture setup exceeded the 50ms poll deadline')

    fixture_client = SetupClient()
    monkeypatch.setattr(redis_admission_gate, 'client', lambda: fixture_client)
    monkeypatch.setattr(study_priority, 'control_client', lambda: PollClient())
    def unchanged_invariant(server, namespace):
        assert namespace == 'private-native-regression'
        server.eval('Controlled direct fixture setup')
        return {'unchanged_monotonic_invariant': True}
    monkeypatch.setattr(proof, 'assert_monotonic_activity_renewal', unchanged_invariant)
    shared, result = proof.prepare_priority_control_fixture('private-native-regression')
    assert shared is fixture_client
    assert result == {'unchanged_monotonic_invariant': True}
    assert calls == ['bounded-write']
