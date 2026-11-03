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
    fixtures = importlib.import_module('database.tests.check_postgres_graph_retention')
    migrations = importlib.import_module('database.tooling.apply_postgres_migrations')
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
    proof = importlib.import_module('database.tests.check_postgres_daily_study_dispatch')
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
    proof = importlib.import_module('database.tests.check_postgres_study_priority')
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


def test_checkpoint_restart_success_preserves_cursor_and_process_bounds(monkeypatch):
    import importlib
    from types import SimpleNamespace
    proof = importlib.import_module('database.tests.check_postgres_study_priority')
    environment = {'PYTHONPATH': 'owned-source'}
    def run(command, **options):
        assert command == [proof.sys.executable, '-c', proof._CHECKPOINT_CHILD]
        assert options == {'cwd': proof.ROOT / 'scripts', 'env': environment,
                           'check': False, 'capture_output': True, 'text': True, 'timeout': 20}
        return SimpleNamespace(returncode=0, stdout='"locked-000048"\n', stderr='')
    monkeypatch.setattr(proof.subprocess, 'run', run)
    assert proof.resume_unlock_checkpoint(environment) == 'locked-000048'


def test_checkpoint_restart_reports_only_validated_symbolic_frames(monkeypatch):
    import importlib
    import json
    import pytest
    from types import SimpleNamespace
    proof = importlib.import_module('database.tests.check_postgres_study_priority')
    record = {'class': 'ModuleNotFoundError', 'frames': [
        {'module': 'database.tests.check_postgres_study_priority', 'function': '_module', 'line': 7}]}
    monkeypatch.setattr(proof.subprocess, 'run', lambda *_, **__: SimpleNamespace(
        returncode=1, stdout='CHECKPOINT_FAILURE ' + json.dumps(record) + '\n',
        stderr='closed connection postgres://secret SQL SELECT private'))
    with pytest.raises(RuntimeError) as caught:
        proof.resume_unlock_checkpoint({})
    assert str(caught.value) == 'Checkpoint child failed (exit code 1): ' + json.dumps(record, sort_keys=True)


def test_checkpoint_restart_rejects_messages_malicious_frames_and_raw_output(monkeypatch):
    import importlib
    import json
    import pytest
    from types import SimpleNamespace
    proof = importlib.import_module('database.tests.check_postgres_study_priority')
    frame = {'module': 'owned.module', 'function': 'checkpoint', 'line': 1}
    valid = {'class': 'RuntimeError', 'frames': [frame]}
    records = [dict(valid, message='postgres://secret'),
               dict(valid, frames=[dict(frame, module='postgres://secret')]),
               dict(valid, frames=[dict(frame, function='SQL SELECT secret')]),
               dict(valid, frames=[dict(frame, line=True)]),
               dict(valid, frames=[frame] * 9), dict(valid, **{'class': 'x' * 97})]
    outputs = [('CHECKPOINT_FAILURE ' + json.dumps(record), '') for record in records]
    outputs += [('', 'raw stderr postgres://secret'), ('raw stdout secret', 'CHECKPOINT_FAILURE ' + json.dumps(valid)),
                ('CHECKPOINT_FAILURE ' + json.dumps(valid) + '\nraw secret', ''),
                ('CHECKPOINT_FAILURE ' + 'x' * 4096, ''), ('', '')]
    for stdout, stderr in outputs:
        monkeypatch.setattr(proof.subprocess, 'run', lambda *_, **__: SimpleNamespace(
            returncode=1, stdout=stdout, stderr=stderr))
        with pytest.raises(RuntimeError) as caught:
            proof.resume_unlock_checkpoint({})
        assert str(caught.value) == 'Checkpoint child failed (exit code 1)'
    monkeypatch.setattr(proof.subprocess, 'run', lambda *_, **__: SimpleNamespace(
        returncode=-9, stdout='CHECKPOINT_FAILURE ' + json.dumps(valid), stderr=''))
    with pytest.raises(RuntimeError, match=r'^Checkpoint child failed \(exit code -9\)$'):
        proof.resume_unlock_checkpoint({})


def test_checkpoint_restart_timeout_does_not_expose_captured_output(monkeypatch):
    import importlib
    import pytest
    proof = importlib.import_module('database.tests.check_postgres_study_priority')
    def timeout(*_, **__):
        raise proof.subprocess.TimeoutExpired('secret command', 20, output='secret stdout', stderr='secret stderr')
    monkeypatch.setattr(proof.subprocess, 'run', timeout)
    with pytest.raises(RuntimeError) as caught:
        proof.resume_unlock_checkpoint({})
    assert str(caught.value) == 'Checkpoint child timed out'
    assert caught.value.__suppress_context__ is True


def test_checkpoint_child_wraps_import_and_call_without_exception_messages(capsys):
    import builtins
    import importlib
    import json
    import pytest
    from types import SimpleNamespace
    proof = importlib.import_module('database.tests.check_postgres_study_priority')
    for fail_import in (True, False):
        def checkpoint():
            raise RuntimeError('postgres://secret SQL SELECT private')
        def child_import(name, *arguments, **options):
            if name == 'database.tests.check_postgres_study_priority':
                if fail_import:
                    raise ModuleNotFoundError('postgres://secret import detail')
                return SimpleNamespace(advance_unlock_checkpoint=checkpoint)
            return builtins.__import__(name, *arguments, **options)
        with pytest.raises(SystemExit) as caught:
            exec(proof._CHECKPOINT_CHILD, {'__name__': 'checkpoint_test',
                 '__builtins__': {**vars(builtins), '__import__': child_import}})
        assert caught.value.code == 1
        captured = capsys.readouterr()
        assert captured.err == ''
        assert 'secret' not in captured.out and 'SELECT' not in captured.out
        assert captured.out.count('\n') == 1
        record = json.loads(captured.out.removeprefix('CHECKPOINT_FAILURE '))
        assert record['class'] == ('ModuleNotFoundError' if fail_import else 'RuntimeError')
        assert 1 <= len(record['frames']) <= 8
        assert all(set(frame) == {'module', 'function', 'line'} for frame in record['frames'])


def test_native_checkpoint_restart_resumes_private_postgres_checkpoint(monkeypatch, tmp_path):
    import importlib
    import json
    import os
    import pytest
    import subprocess
    import psycopg
    from psycopg.conninfo import conninfo_to_dict
    parent_url = os.environ.get('TEMPO_489_TEST_DSN')
    if not parent_url:
        pytest.skip('Requires the existing disposable PostgreSQL test runner')
    container = os.environ.get('TEMPO_489_TEST_CONTAINER', '')
    token = os.environ.get('TEMPO_DEVELOPMENT_BUDGET_TOKEN', '')
    assert container.startswith('tempo-489-pg-residual-') and token
    connection = conninfo_to_dict(parent_url)
    assert connection.get('host') == '127.0.0.1'
    assert connection.get('hostaddr', '127.0.0.1') == '127.0.0.1'
    assert connection.get('dbname') == 'tempo_489_residual'
    assert 'service' not in connection
    inspected = subprocess.run(['docker', 'inspect', container], check=True,
                               capture_output=True, text=True, timeout=20)
    inventory = json.loads(inspected.stdout)
    assert len(inventory) == 1
    owned = inventory[0]
    assert owned['Name'] == '/' + container
    labels = owned['Config']['Labels']
    assert labels['tempo.owner'] == container and labels['tempo.disposable'] == 'true'
    assert labels['tempo.resource-class'] == 'development'
    assert labels['tempo.resource-owner'] == token
    bindings = owned['NetworkSettings']['Ports']['5432/tcp']
    assert any(binding['HostIp'] == '127.0.0.1'
               and binding['HostPort'] == connection.get('port') for binding in bindings)
    # Only the verified helper-owned parent database receives this fixture marker.
    with psycopg.connect(parent_url, autocommit=True) as database:
        database.execute("COMMENT ON DATABASE tempo_489_residual IS 'tempo-disposable-postgres-test'")
    proof = importlib.import_module('database.tests.check_postgres_study_priority')
    original_resume = proof.resume_unlock_checkpoint

    class CheckpointResumed(BaseException):
        pass

    def resume_then_stop(environment):
        assert original_resume(environment) == 'locked-000048'
        # Leave through run_proof's existing role/pool/environment cleanup and
        # private_database's database cleanup, before Redis/API/load phases.
        raise CheckpointResumed

    monkeypatch.setattr(proof, 'resume_unlock_checkpoint', resume_then_stop)
    with pytest.raises(CheckpointResumed):
        proof.run_proof(parent_url, 'redis://127.0.0.1:1/0', tmp_path,
                        graph_rows=10_000, receipt_rows=20_000)
    retries = [json.loads(line) for line in (tmp_path / 'checkpoint-retries.jsonl').read_text().splitlines()]
    assert any(item['forced'] and item['checkpoint_unchanged'] for item in retries)
