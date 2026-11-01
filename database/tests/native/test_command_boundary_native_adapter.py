"""Selected command boundary proofs on fixture-owned PostgreSQL and Redis.

Run only under an explicitly granted native slot. The imported PostgreSQL fixture
owns its container and credentials; this adapter creates one labelled Redis.
"""
import json
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest
import redis
from psycopg.conninfo import make_conninfo

from test_product_introductions_postgres import owned_postgres  # noqa: F401


@pytest.fixture(scope='module')
def command_boundary_native(owned_postgres):
    name = 'tempo-command-boundary-' + uuid4().hex
    def docker(*args, check=True):
        return subprocess.run(['docker', *args], capture_output=True, text=True, check=check, timeout=20)
    image = docker('image', 'inspect', 'redis:7-alpine', '--format', '{{.Id}}').stdout.strip()
    client = None
    container_id = None
    evidence = Path(__file__).resolve().parents[3] / 'test-results' / 'command-boundary-native' / name
    evidence.mkdir(parents=True)
    report = {'owner': name, 'image_id': image, 'cleanup_verified': False}
    try:
        container_id = docker('run', '--detach', '--name', name, '--label', 'tempo.owner=' + name,
               '--label', 'tempo.disposable=true', '--memory', '64m', '--memory-swap', '64m',
               '--pids-limit', '64', '--tmpfs', '/data:rw,size=32m', '--publish', '127.0.0.1::6379', image,
               'redis-server', '--save', '', '--appendonly', 'no').stdout.strip()
        current = json.loads(docker('inspect', container_id).stdout)[0]
        assert current['Id'] == container_id and current['Image'] == image
        assert current['Config']['Labels']['tempo.owner'] == name
        assert current['HostConfig']['Memory'] == 64 * 1024 * 1024
        assert current['HostConfig']['MemorySwap'] == 64 * 1024 * 1024
        assert current['HostConfig']['PidsLimit'] == 64
        assert current['HostConfig']['Tmpfs'] == {'/data': 'rw,size=32m'}
        assert all(mount['Type'] == 'tmpfs' and mount['Destination'] == '/data'
                   for mount in current['Mounts'])
        binding = current['NetworkSettings']['Ports']['6379/tcp'][0]
        assert binding['HostIp'] == '127.0.0.1'
        port = binding['HostPort']
        report.update(container_id=container_id, memory_bytes=current['HostConfig']['Memory'],
                      memory_swap_bytes=current['HostConfig']['MemorySwap'], pids_limit=64,
                      loopback_port=int(port), image_id=image)
        redis_url = 'redis://127.0.0.1:' + port + '/0'
        client = redis.Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
        deadline = time.monotonic() + 30
        while True:
            try:
                if client.ping(): break
            except redis.RedisError:
                if time.monotonic() >= deadline: raise
                time.sleep(.1)
        yield make_conninfo(**owned_postgres), redis_url
    finally:
        if client is not None:
            client.close()
        found = docker('container', 'ls', '--all', '--filter', 'name=^/' + name + '$', '--format', '{{.ID}}').stdout.strip()
        if found:
            current = json.loads(docker('inspect', found).stdout)[0]
            assert current['Config']['Labels']['tempo.owner'] == name
            assert current['Config']['Labels']['tempo.disposable'] == 'true'
            assert container_id is None or current['Id'] == container_id
            report['oom_killed'] = current['State']['OOMKilled']
            for metric in ('memory.events', 'memory.peak'):
                observation = docker('exec', current['Id'], 'cat', '/sys/fs/cgroup/' + metric, check=False)
                report[metric] = observation.stdout.strip() if observation.returncode == 0 else 'unavailable'
            docker('rm', '--force', found)
        assert not docker('container', 'ls', '--all', '--filter', 'name=^/' + name + '$', '--format', '{{.ID}}').stdout.strip()
        report['cleanup_verified'] = True
        (evidence / 'redis.json').write_text(json.dumps(report, indent=2))


@pytest.fixture
def migrated_command_database(command_boundary_native, monkeypatch):
    parent_dsn, redis_url = command_boundary_native
    database_name = 'command_boundary_' + uuid4().hex
    with psycopg.connect(parent_dsn, autocommit=True) as parent:
        parent.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database_name)))
    dsn = make_conninfo(parent_dsn, dbname=database_name)
    # The DSN is supplied exclusively by owned_postgres, never the environment.
    monkeypatch.setenv('TEMPO_TEST_INSTANCE', 'disposable')
    monkeypatch.setenv('TEMPO_DATABASE_WRITE_URL', dsn)
    monkeypatch.setenv('TEMPO_DATABASE_READ_URL', dsn)
    monkeypatch.setenv('TEMPO_REDIS_URL', redis_url)
    monkeypatch.setenv('TEMPO_ADMISSION_NAMESPACE', 'tempo:command-proof:' + uuid4().hex)
    from database.tooling.apply_postgres_migrations import apply_migrations
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    from tempo_legacy_database.adapters import postgres_store
    from tempo_legacy_database.adapters.redis_control import admission
    try:
        bind_database_infrastructure()
        apply_migrations(dsn)
        with psycopg.connect(dsn) as connection:
            connection.execute('INSERT INTO settings(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
            connection.execute('INSERT INTO tactic_rotation(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
        import app.main  # actual command registration, not a test registry
        yield dsn
    finally:
        try:
            postgres_store.close_pools()
        finally:
            try:
                if admission.client.cache_info().currsize:
                    admission.client().close()
            finally:
                admission.client.cache_clear()
                with psycopg.connect(parent_dsn, autocommit=True) as parent:
                    parent.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database_name)))


def test_native_settings_descriptor_original_identity(migrated_command_database):
    from database.tests.check_postgres_study_only import prove_settings_descriptor_original_identity
    prove_settings_descriptor_original_identity()


def test_native_receipt_rollback_and_detail(migrated_command_database):
    from database.tests.check_postgres_operation_recovery import test_postgres_command_receipt_preserves_http_detail_and_failed_handler_rollback
    test_postgres_command_receipt_preserves_http_detail_and_failed_handler_rollback()


def test_native_receipt_conflict_and_recovery(migrated_command_database):
    from app.command_gateway import register_command
    from database.tests.check_postgres_operation_recovery import test_postgres_concurrent_conflict_preserves_deferred_receipt_recovery
    attempts = []
    should_fail = False  # main() has completed its initial retry before this case.
    def handler(database, payload):
        attempts.append(payload['value'])
        if should_fail:
            from psycopg.errors import TransactionTimeout
            raise TransactionTimeout('injected retryable failure')
        database.raw.execute('INSERT INTO internal_migrations(name,applied_at) VALUES(%s,NOW()::TEXT) ON CONFLICT(name) DO NOTHING', (payload['value'],))
        return payload
    register_command('test.recovery.state', handler)
    test_postgres_concurrent_conflict_preserves_deferred_receipt_recovery()


@pytest.mark.parametrize('case', [
    'test_issue80_same_operation_concurrency_stale_slice_lost_response_and_activation_rollback',
    'test_issue80_staging_failure_releases_fences_without_product_activation_and_noops_replay',
    'test_issue80_publication_failure_keeps_activation_and_retry_resumes_linked_tasks',
])
def test_native_prefix_lifecycle(migrated_command_database, case):
    from database.tests import check_postgres_prefix_transition_application as proof
    with proof.isolate_unrelated_publication_tasks():
        getattr(proof, case)()
