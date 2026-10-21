"""Native target-fence regressions; no PostgreSQL connection is permitted here."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from psycopg.conninfo import conninfo_to_dict, make_conninfo

NATIVE = Path(__file__).resolve().parents[2] / 'database/tempo_legacy_database/tests/queue_admission'
sys.path.insert(0, str(NATIVE))
import queue_fixture_guard as guard


@pytest.fixture
def owned_environment(tmp_path):
    name = 'tempo_queue_admission_' + 'a'*32
    receipt = dict(version=1, database=name, host='127.0.0.1', port=49173,
                   container_id='c'*64, system_identifier='123456789', database_oid='16391',
                   created_at=1000, deadline=1300)
    path = tmp_path/'owned-target.json'
    path.write_text(json.dumps(receipt))
    environment = dict(TEMPO_QUEUE_ADMISSION_FIXTURE=name, TEMPO_TEST_INSTANCE='disposable',
                       TEMPO_QUEUE_ADMISSION_TARGET_FILE=str(path))
    for key, role in (('TEMPO_QUEUE_ADMISSION_ADMIN', 'postgres'),
                      ('TEMPO_DATABASE_READ_URL', 'tempo_reader'), ('TEMPO_DATABASE_WRITE_URL', 'tempo_writer')):
        environment[key] = make_conninfo(host=receipt['host'], port=receipt['port'], dbname=name,
                                         user=role, password='disposable-fixture-only', sslmode='disable', connect_timeout=2)
    return environment, receipt, path


def forbidden_connection(*args, **kwargs):
    pytest.fail('Target rejection must precede every connection and write')


@pytest.mark.parametrize('change', [
    'missing_name', 'legacy_marker', 'missing_disposable', 'missing_receipt',
    'malformed_receipt', 'non_object_receipt', 'receipt_wrong_database',
    'receipt_expired', 'receipt_too_long', 'receipt_missing_container',
    'ambient_PGHOST', 'ambient_PGOPTIONS', 'ambient_PGPASSFILE', 'ambient_PGSERVICE',
    'all_dsns_stale_endpoint', 'admin_live_database', 'reader_live_database',
    'writer_live_database', 'reader_other_endpoint', 'writer_default_port',
    'writer_remote_host', 'writer_hostaddr_override', 'writer_service',
    'writer_options', 'writer_multiple_hosts', 'writer_wrong_role',
    'writer_missing_password', 'writer_missing_timeout',
])
def test_queue_native_target_rejects_unsafe_configuration_before_connection(owned_environment, monkeypatch, change):
    environment, receipt, path = owned_environment
    monkeypatch.setattr(guard.psycopg, 'connect', forbidden_connection)
    if change == 'missing_name': environment.pop('TEMPO_QUEUE_ADMISSION_FIXTURE')
    elif change == 'legacy_marker': environment['TEMPO_QUEUE_ADMISSION_FIXTURE'] = 'tempo224-'+'a'*16
    elif change == 'missing_disposable': environment.pop('TEMPO_TEST_INSTANCE')
    elif change == 'missing_receipt': environment.pop('TEMPO_QUEUE_ADMISSION_TARGET_FILE')
    elif change == 'malformed_receipt': path.write_text('{')
    elif change == 'non_object_receipt': path.write_text('[]')
    elif change.startswith('receipt_'):
        if change == 'receipt_wrong_database': receipt['database'] = 'tempo'
        if change == 'receipt_expired': receipt['deadline'] = 1099
        if change == 'receipt_too_long': receipt['deadline'] = 2000
        if change == 'receipt_missing_container': receipt.pop('container_id')
        path.write_text(json.dumps(receipt))
    elif change.startswith('ambient_'): environment[change.removeprefix('ambient_')] = 'stale-live-configuration'
    else:
        key = ('TEMPO_QUEUE_ADMISSION_ADMIN' if change.startswith('admin_') else
               'TEMPO_DATABASE_READ_URL' if change.startswith('reader_') else 'TEMPO_DATABASE_WRITE_URL')
        targets = ['TEMPO_QUEUE_ADMISSION_ADMIN', 'TEMPO_DATABASE_READ_URL', 'TEMPO_DATABASE_WRITE_URL'] if change == 'all_dsns_stale_endpoint' else [key]
        for key in targets:
            target = conninfo_to_dict(environment[key])
            if change.endswith('live_database'): target['dbname'] = 'tempo'
            if change in {'reader_other_endpoint', 'all_dsns_stale_endpoint'}: target['port'] = '49174'
            if change == 'writer_default_port': target.pop('port')
            if change == 'writer_remote_host': target['host'] = 'production.example.invalid'
            if change == 'writer_hostaddr_override': target['hostaddr'] = '203.0.113.1'
            if change == 'writer_service': target['service'] = 'live'
            if change == 'writer_options': target['options'] = '-c search_path=other'
            if change == 'writer_multiple_hosts': target['host'] = '127.0.0.1,production.example.invalid'
            if change == 'writer_wrong_role': target['user'] = 'postgres'
            if change == 'writer_missing_password': target.pop('password')
            if change == 'writer_missing_timeout': target.pop('connect_timeout')
            environment[key] = make_conninfo(**target)
    with pytest.raises(RuntimeError):
        guard.owned_target(environment, now=1100)


def test_queue_native_matching_owned_receipt_performs_no_connection_during_validation(owned_environment, monkeypatch):
    environment, receipt, _ = owned_environment
    monkeypatch.setattr(guard.psycopg, 'connect', forbidden_connection)
    target = guard.owned_target(environment, now=1100)
    assert (target.name, target.host, target.port, target.database_oid) == (receipt['database'], '127.0.0.1', '49173', '16391')


@pytest.mark.parametrize('changed', [None, 'database', 'schema', 'role', 'cluster', 'oid'])
def test_queue_native_identity_probe_is_read_only_and_checks_database_cluster_oid(owned_environment, monkeypatch, changed):
    environment, receipt, _ = owned_environment
    target = guard.owned_target(environment, now=1100)
    observed = [target.name, 'public', 'postgres', receipt['system_identifier'], receipt['database_oid']]
    if changed:
        observed[{'database':0, 'schema':1, 'role':2, 'cluster':3, 'oid':4}[changed]] = 'unexpected'
    calls = []
    @contextmanager
    def read_only_connect(dsn, **kwargs):
        assert dsn == target.admin_dsn
        assert kwargs == dict(connect_timeout=2, autocommit=True, options='-c default_transaction_read_only=on')
        def execute(statement):
            calls.append(statement)
            assert statement.startswith('SELECT current_database(),current_schema(),current_user,')
            return SimpleNamespace(fetchone=lambda: tuple(observed))
        yield SimpleNamespace(execute=execute)
    monkeypatch.setattr(guard.psycopg, 'connect', read_only_connect)
    if changed:
        with pytest.raises(RuntimeError, match='differs'):
            guard.verify_owned_target(target)
    else:
        guard.verify_owned_target(target)
    assert len(calls) == 1


def test_queue_native_case_guard_blocks_stale_urls_before_pool_or_fixture_write(owned_environment, monkeypatch):
    environment, _, _ = owned_environment
    spec = importlib.util.spec_from_file_location('queue_native_cases_guard_probe', NATIVE/'test_postgres.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    environment['TEMPO_DATABASE_WRITE_URL'] = make_conninfo(host='127.0.0.1', port=5432, dbname='tempo', user='postgres', password='unused')
    monkeypatch.setattr(native, 'os', SimpleNamespace(environ=environment))
    monkeypatch.setattr(native, 'owned_target', lambda env: guard.owned_target(env, now=1100))
    monkeypatch.setattr(native.postgres_store, 'close_pools', forbidden_connection)
    monkeypatch.setattr(native.postgres_store, 'connection', forbidden_connection)
    monkeypatch.setattr(guard.psycopg, 'connect', forbidden_connection)
    with pytest.raises(RuntimeError, match='Every fixture DSN'):
        next(native.fixture_database.__wrapped__())


def test_queue_native_environment_change_after_identity_check_precedes_fixture_write(owned_environment, monkeypatch):
    environment, _, _ = owned_environment
    spec = importlib.util.spec_from_file_location('queue_native_cases_changed_probe', NATIVE/'test_postgres.py')
    native = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(native)
    monkeypatch.setattr(native, 'os', SimpleNamespace(environ=environment))
    monkeypatch.setattr(native, 'owned_target', lambda env: guard.owned_target(env, now=1100))
    monkeypatch.setattr(native.postgres_store, 'close_pools', lambda: None)
    monkeypatch.setattr(native.postgres_store, 'connection', forbidden_connection)
    def mutate_after_probe(target):
        changed = conninfo_to_dict(environment['TEMPO_DATABASE_WRITE_URL'])
        changed['port'] = '49174'
        environment['TEMPO_DATABASE_WRITE_URL'] = make_conninfo(**changed)
    monkeypatch.setattr(native, 'verify_owned_target', mutate_after_probe)
    with pytest.raises(RuntimeError, match='Every fixture DSN'):
        next(native.fixture_database.__wrapped__())
