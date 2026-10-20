"""Source-only plan/refusal checks: no Docker, sockets or backend fixture run."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('legacy_backend_native_plan', ROOT / 'scripts/qualify_legacy_backend.py')
plan = importlib.util.module_from_spec(spec); spec.loader.exec_module(plan)


def test_native_plan_has_nine_exact_cases_under_existing_budget():
    value = plan.describe()
    assert len(set(value['cases'])) == 9
    assert value['work_seconds'] == 900 and value['cleanup_seconds'] == 300
    assert value['total'] == dict(containers=3, cpus=1.85, memory_mib=1216, pids=288,
        extra_swap_mib=0, owned_internal_networks=1, published_ports=0)
    budget = json.loads((ROOT / 'config/resource-budgets.json').read_text())['development']
    assert value['total']['cpus'] <= budget['cpus'] and value['total']['memory_mib'] <= budget['memoryMiB']
    assert value['candidate_images']['redis'] == 'sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499'


@pytest.mark.parametrize('value', ['redis:7-alpine', 'latest', 'sha256:wrong'])
def test_native_plan_refuses_mutable_or_unknown_image_identity(value):
    with pytest.raises(ValueError, match='immutable image ID'): plan.image_id(value)


def test_native_plan_requires_new_grant_before_git_docker_or_any_resource(monkeypatch):
    monkeypatch.setattr(plan.subprocess, 'check_output', lambda *a, **k: pytest.fail('Inspected resources without a grant'))
    with pytest.raises(ValueError, match='Fresh parent-approved'):
        plan.host(SimpleNamespace(approved_window=False))


def test_native_caps_have_no_extra_swap_host_ports_or_live_mounts():
    for kind in plan.CAPS:
        arguments = plan.docker_arguments(kind, 'owned-' + kind, 'sha256:' + 'a' * 64, 'owned-internal')
        assert arguments[arguments.index('--memory') + 1] == arguments[arguments.index('--memory-swap') + 1]
        assert '--publish' not in arguments and '--env-file' not in arguments and '--mount' not in arguments
        assert arguments[arguments.index('--network') + 1] == 'owned-internal'


def test_redis_declared_data_volume_is_replaced_by_bounded_tmpfs():
    arguments = plan.docker_arguments('redis', 'owned-redis', 'sha256:' + 'a' * 64, 'owned-internal')
    tmpfs = [arguments[index + 1] for index, item in enumerate(arguments) if item == '--tmpfs']
    assert '/data:rw,size=32m' in tmpfs


@pytest.mark.parametrize('mounts', [[], [dict(Type='tmpfs', Destination='/data', RW=True)]])
def test_image_and_container_inventories_establish_no_redis_persistent_mount(mounts):
    assert plan.verify_image_volumes('redis', dict(Config=dict(Volumes={'/data': {}}))) == ['/data']
    container = dict(HostConfig=dict(Tmpfs={'/tmp': 'rw,size=32m', '/data': 'rw,size=33554432'}), Mounts=mounts)
    plan.verify_container_mounts('redis', container)
    assert plan.mounted_volume_names(container) == set()


@pytest.mark.parametrize('kind', ['helper', 'postgres', 'redis'])
def test_uncovered_image_volume_is_refused_before_allocation(kind):
    with pytest.raises(ValueError, match='uncovered persistent volume'):
        plan.verify_image_volumes(kind, dict(Config=dict(Volumes={'/unexpected': {}})))


@pytest.mark.parametrize('fault', ['missing_data', 'wrong_size', 'readonly_data', 'extra_tmpfs', 'anonymous', 'named', 'declared_extra'])
def test_redis_mount_inventory_rejects_leaks_or_unbounded_storage(fault):
    tmpfs, mounts = {'/tmp': 'rw,size=32m', '/data': 'rw,size=32m'}, []
    if fault == 'missing_data': tmpfs.pop('/data')
    if fault == 'wrong_size': tmpfs['/data'] = 'rw,size=64m'
    if fault == 'readonly_data': tmpfs['/data'] = 'ro,size=32m'
    if fault == 'extra_tmpfs': tmpfs['/unexpected'] = 'rw,size=32m'
    if fault in ('anonymous', 'named'):
        mounts.append(dict(Type='volume', Name='a' * 64 if fault == 'anonymous' else 'unrelated-named', Destination='/data', RW=True))
        assert plan.mounted_volume_names(dict(Mounts=mounts)) == {mounts[0]['Name']}
    config = dict(Volumes={'/unexpected': {}}) if fault == 'declared_extra' else {}
    with pytest.raises(ValueError): plan.verify_container_mounts('redis', dict(Config=config, HostConfig=dict(Tmpfs=tmpfs), Mounts=mounts))


def test_recorded_volume_absence_checks_only_exact_observed_identities():
    calls = []
    def capture(command, **options):
        calls.append((command, options))
        return subprocess.CompletedProcess(command, 1, '', 'Error response from daemon: no such volume: ' + command[-1])
    names = {'a' * 64, 'b' * 64}
    assert plan.verify_volume_absence(SimpleNamespace(capture=capture), names) == sorted(names)
    assert [item[0] for item in calls] == [['docker', 'volume', 'inspect', name] for name in sorted(names)]


@pytest.mark.parametrize('code,error', [(0, ''), (1, 'daemon unavailable'), (1, 'no such volume: another-volume')])
def test_surviving_or_unverified_recorded_volume_blocks_cleanup(code, error):
    def capture(command, **options): return subprocess.CompletedProcess(command, code, '[]', error)
    with pytest.raises(RuntimeError, match='volume remains/absence unknown'):
        plan.verify_volume_absence(SimpleNamespace(capture=capture), {'a' * 64})


def test_exact_native_node_inventory_accepts_required_parameterized_identity():
    plan.verify_case_inventory(dict(collected=list(plan.CASES)))
    assert plan.CASES[1].endswith('[prior]') and all('test_native.py::' in node for node in plan.CASES)


@pytest.mark.parametrize('result', [0, 1, 'timeout'])
def test_native_recovery_publishes_the_supported_task_in_a_bounded_owned_process(result):
    fixture = plan.shared.load('native_fixture_unit_recovery', ROOT / 'backend/integration/legacy_bridge/conftest.py')
    native = fixture.NativeBackend.__new__(fixture.NativeBackend)
    native.processes, native.calls = {}, []
    started, stopped, waits = [], [], []
    def start(key, source, role):
        started.append((key, source, role))
        def wait(timeout):
            waits.append(timeout)
            if result == 'timeout': raise subprocess.TimeoutExpired('owned-recovery', timeout)
            return result
        native.processes[key] = SimpleNamespace(wait=wait)
    native.start, native.stop = start, stopped.append
    if result == 0:
        native.recover_operations()
        assert native.calls == [dict(method='CELERY', task='app.tasks.recover_operations', queue='background')]
    else:
        with pytest.raises(subprocess.TimeoutExpired if result == 'timeout' else RuntimeError):
            native.recover_operations()
        assert native.calls == []
    assert waits == [5] and stopped == ['recovery']
    key, source, role = started[0]
    assert key == 'recovery' and role == 'tempo_writer'
    assert 'from app.celery_app import celery_app' in source
    assert 'celery_app.send_task("app.tasks.recover_operations",queue="background")' in source
    assert 'execute_command' not in source and 'recover_operations()' not in source


def test_native_operation_monitoring_is_background_without_changing_operation_identity():
    fixture = plan.shared.load('native_fixture_unit_monitor', ROOT / 'backend/integration/legacy_bridge/conftest.py')
    native = fixture.NativeBackend.__new__(fixture.NativeBackend)
    calls = []
    original = dict(operation_id='original:operation', state='complete', response=dict(saved=True))
    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return 200, original
    native.request = request
    assert native.operation('original:operation') is original
    assert calls == [(('GET', '/api/operations/original%3Aoperation'),
                      dict(headers={'X-Tempo-Work-Class': 'background'}))]


@pytest.mark.parametrize('fault', ['other_same_count', 'missing_prior', 'duplicate'])
def test_exact_native_node_inventory_rejects_same_count_substitution(fault):
    nodes = list(plan.CASES)
    if fault == 'other_same_count': nodes[0] = nodes[0] + '_unrelated'
    if fault == 'missing_prior': nodes[1] = nodes[1].removesuffix('[prior]')
    if fault == 'duplicate': nodes[1] = nodes[0]
    with pytest.raises(RuntimeError, match='Exact native node IDs changed'): plan.verify_case_inventory(dict(collected=nodes))


def wheel_fixture(tmp_path):
    site, wheel = tmp_path / 'installed', tmp_path / 'owned-0.1.0-py3-none-any.whl'
    expected = {'owned/__init__.py': b'VALUE="verified-source"\n', 'owned/data.json': b'{"owned": true}',
        'owned-0.1.0.dist-info/METADATA': b'Name: owned\nVersion: 0.1.0\n', 'owned-0.1.0.dist-info/RECORD': b'installer rewrites this'}
    with zipfile.ZipFile(wheel, 'w') as archive:
        for name, content in expected.items():
            archive.writestr(name, content)
            path = site / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
    return wheel, site


def test_installed_owned_package_content_is_compared_to_verified_wheel(tmp_path):
    wheel, site = wheel_fixture(tmp_path)
    (site / 'owned-0.1.0.dist-info/RECORD').write_text('genuine installer-specific metadata')
    result = plan.verify_installed_wheel(wheel, site)
    assert result['verified_files'] == 3 and result['owned_package_files'] == 2


@pytest.mark.parametrize('fault', ['changed_code', 'changed_data', 'changed_metadata', 'missing_code', 'extra_code'])
def test_retained_installed_artifact_drift_is_refused_without_reinstallation(tmp_path, fault):
    wheel, site = wheel_fixture(tmp_path)
    if fault == 'changed_code': (site / 'owned/__init__.py').write_text('VALUE="changed"')
    if fault == 'changed_data': (site / 'owned/data.json').write_text('{}')
    if fault == 'changed_metadata': (site / 'owned-0.1.0.dist-info/METADATA').write_text('Version: other')
    if fault == 'missing_code': (site / 'owned/__init__.py').unlink()
    if fault == 'extra_code': (site / 'owned/extra.py').write_text('VALUE="unexpected"')
    with pytest.raises(ValueError, match='Installed artifact'): plan.verify_installed_wheel(wheel, site)


def test_fresh_native_bytecode_prefix_ignores_retained_unverified_cache(tmp_path):
    import os
    import py_compile
    import sys
    wheel, site = wheel_fixture(tmp_path)
    source = site / 'owned/__init__.py'
    good, bad = b'VALUE="verified-source"\n', b'VALUE="unverified-code"\n'
    assert len(good) == len(bad)
    source.write_bytes(bad); os.utime(source, (1700000000, 1700000000))
    py_compile.compile(str(source), doraise=True, invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP)
    source.write_bytes(good); os.utime(source, (1700000000, 1700000000))
    plan.verify_installed_wheel(wheel, site)
    control = 'import sys;sys.path.insert(0,' + repr(str(site)) + ');import owned;print(owned.VALUE)'
    cached = subprocess.run([sys.executable, '-I', '-B', '-c', control], capture_output=True, text=True, timeout=5)
    assert cached.returncode == 0 and cached.stdout.strip() == 'unverified-code'
    prefix = tmp_path / 'fresh-native-cache'
    bootstrap = 'import sys;sys.pycache_prefix=' + repr(str(prefix)) + ';sys.path.insert(0,' + repr(str(site)) + ');import owned;print(owned.VALUE)'
    run = subprocess.run([sys.executable, '-I', '-B', '-c', bootstrap], capture_output=True, text=True, timeout=5)
    assert run.returncode == 0 and run.stdout.strip() == 'verified-source'
    assert not prefix.exists()


@pytest.mark.parametrize('fault', ['unqualified', 'head', 'image', 'inventory', 'bytes'])
def test_native_gate_refuses_incomplete_foreign_or_changed_artifacts(tmp_path, fault):
    (tmp_path / 'wheels').mkdir()
    artifacts = {}
    for index in range(12):
        name = str(index) + '.fixture'
        (tmp_path / 'wheels' / name).write_bytes(b'owned synthetic fixture')
        artifacts[name] = hashlib.sha256(b'owned synthetic fixture').hexdigest()
    host = dict(complete=True, cleanup_verified=True, source_head='head', image='image', plan=dict(artifact_test_total=373))
    inside = dict(complete=True, artifacts=artifacts)
    if fault == 'unqualified': host['cleanup_verified'] = False
    if fault == 'head': host['source_head'] = 'other'
    if fault == 'image': host['image'] = 'other'
    if fault == 'inventory': host['plan']['artifact_test_total'] = 255
    if fault == 'bytes': (tmp_path / 'wheels/0.fixture').write_bytes(b'changed')
    for name, document in [('host.json', host), ('inside.json', inside)]:
        (tmp_path / name).write_text(json.dumps(document))
    with pytest.raises(ValueError): plan.qualified_artifacts(tmp_path, 'head', 'image')


def test_native_feedback_fixture_publishes_through_writable_storage_not_read_only_view():
    """Actual helper, actual SQLite read-only view; no SQL-prefix fake database.

    This catches fixture topology before PostgreSQL admission. Native execution
    separately owns actual PostgreSQL schema/types/roles and transaction proof.
    """
    import ast
    import sqlite3
    source = ast.parse((ROOT / 'backend/integration/legacy_bridge/test_native.py').read_text())
    helper = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'insert_feedback_miss')
    namespace = {}
    exec(compile(ast.Module(body=[helper], type_ignores=[]), '<native-feedback-fixture>', 'exec'), namespace)
    with sqlite3.connect(':memory:') as database:
        database.executescript('''
            CREATE TABLE imported_games(id TEXT PRIMARY KEY,played_at TEXT);
            INSERT INTO imported_games VALUES('game','2026-10-19T10:00:00Z');
            CREATE TABLE game_derivation_jobs(game_id TEXT PRIMARY KEY,status TEXT,
                derivation_version INTEGER,published_repertoire_version INTEGER,updated_at TEXT);
            CREATE TABLE repertoire_decision_events_staged(id TEXT,game_id TEXT,derivation_version INTEGER,
                repertoire_id TEXT,card_id TEXT,ply INTEGER,fen_key TEXT,expected_uci TEXT,
                actual_uci TEXT,outcome TEXT,played_at TEXT,updated_at TEXT);
            CREATE VIEW repertoire_decision_events AS SELECT event.id,event.game_id,event.repertoire_id,
                event.card_id,event.ply,event.fen_key,event.expected_uci,event.actual_uci,event.outcome,
                event.played_at,event.updated_at FROM repertoire_decision_events_staged event
                JOIN game_derivation_jobs job ON job.game_id=event.game_id
                  AND job.published_repertoire_version=event.derivation_version;
            CREATE VIEW current_repertoire_decision_events AS SELECT * FROM repertoire_decision_events;
        ''')
        class NativeParameters:
            def execute(self, statement, parameters=()):
                return database.execute(statement.replace('%s', '?'), parameters)
        fixture = SimpleNamespace(case_id='fixture', identities=dict(game='game', ruy='ruy', italian='italian', physical='card'))
        first = namespace['insert_feedback_miss'](NativeParameters(), fixture)
        second = namespace['insert_feedback_miss'](NativeParameters(), fixture, repertoire='italian', suffix='other', ply=6)
        assert first == 'fixture-weak' and second == 'fixture-other'
        assert database.execute('SELECT game_id,derivation_version,published_repertoire_version FROM game_derivation_jobs').fetchall() == [('game', 1, 1)]
        assert database.execute('SELECT id,repertoire_id FROM current_repertoire_decision_events ORDER BY ply').fetchall() == [(first, 'ruy'), (second, 'italian')]
