"""Real fixed-duration admission leases on invocation-owned Redis keys."""
import json
import os
import re
import sys
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend'), str(ROOT / 'database')]
from tempo_storage.redis_control import admission


def validate_environment(environment):
    project = environment.get('TEMPO_TEST_COMPOSE_PROJECT', '')
    revision = environment.get('TEMPO_NATIVE_CANDIDATE_REVISION', '')
    if (environment.get('TEMPO_TEST_INSTANCE') != 'disposable'
            or not re.fullmatch(r'tempo-pg-regressions-[0-9]+-[a-f0-9]{8}', project)
            or not re.fullmatch(r'[a-f0-9]{40}', revision)
            or environment.get('TEMPO_REDIS_URL') != 'redis://redis:6379/0'):
        raise RuntimeError('Refusing non-owned Redis admission proof')
    return project, revision


def proof():
    project, revision = validate_environment(os.environ)
    server = admission.client()
    namespace = 'tempo:test:admission-residual:' + uuid4().hex
    foreground, background, sentinel = namespace + ':foreground', namespace + ':background', namespace + ':sentinel'
    keys = (foreground, background, sentinel)
    if server.exists(*keys):
        raise RuntimeError('Fresh Redis proof namespace is not empty')
    try:
        server.set(sentinel, 'unrelated-sentinel')
        with patch.object(admission, '_FOREGROUND_KEY', foreground), patch.object(admission, '_BACKGROUND_KEY', background):
            now = lambda: int(time.time() * 1000)
            admission.acquire_foreground(server, now(), 'first')
            admission.acquire_foreground(server, now(), 'other')
            prior = server.zscore(foreground, 'first')
            admission.acquire_foreground(server, now(), 'first')
            assert server.zscore(foreground, 'first') >= prior
            assert 28_000 <= server.pttl(foreground) <= 31_000
            assert not admission.acquire_background(server, now(), 'blocked')
            admission.remove_foreground(server, 'first')
            assert server.zscore(foreground, 'first') is None
            assert server.zscore(foreground, 'other') is not None
            admission.record_browser_presence(server, now(), 'phone', 0.25)
            admission.remove_foreground(server, 'other')
            assert not admission.acquire_background(server, now(), 'blocked')
            deadline = time.monotonic() + 2
            while admission.has_foreground(server, now()) and time.monotonic() < deadline:
                time.sleep(0.02)
            assert not admission.has_foreground(server, now())
            assert admission.acquire_background(server, now(), 'old-background')
            assert 4_000 <= server.pttl(background) <= 6_000
            assert admission.acquire_background(server, now(), 'other-background')
            admission.remove_background(server, 'old-background')
            assert server.zscore(background, 'other-background') is not None
            assert admission.acquire_background(server, now(), 'expiring-background')
            deadline = time.monotonic() + 7
            while server.pttl(background) > 0 and time.monotonic() < deadline:
                time.sleep(0.05)
            assert server.exists(background) == 0
            assert admission.acquire_background(server, now(), 'new-background')
            assert server.zscore(background, 'expiring-background') is None
            admission.remove_background(server, 'new-background')
            admission.acquire_foreground(server, now(), 'expires-naturally')
            deadline = time.monotonic() + 33
            while server.exists(foreground) and time.monotonic() < deadline:
                time.sleep(0.1)
            assert server.exists(foreground) == 0
            assert admission.acquire_background(server, now(), 'after-expiry')
            admission.remove_background(server, 'after-expiry')
        assert server.get(sentinel) == b'unrelated-sentinel'
    finally:
        error = sys.exception()
        try:
            if server.get(sentinel) != b'unrelated-sentinel':
                raise RuntimeError('Admission proof sentinel changed')
            server.delete(*keys)
            if server.exists(*keys):
                raise RuntimeError('Admission proof keys remain')
        except Exception as cleanup_error:
            if error is None:
                raise
            error.add_note(f'Admission proof cleanup failed: {cleanup_error}')
    return {'project': project, 'revision': revision, 'case': 'redis_admission_real_lease_interference', 'cleanup': 'completed'}


def restart_phase(phase):
    project, revision = validate_environment(os.environ)
    evidence = Path('/evidence/restart.json')
    from database.tests.redis.check_redis_background_wakes import prepare_restart_proof, verify_restart_proof, queue_keys
    from app.services import background_wakes
    if phase == 'prepare':
        if evidence.exists():
            raise RuntimeError('Restart evidence already exists')
        prepare_restart_proof(evidence)
        return
    if phase != 'verify':
        raise RuntimeError('Unknown Redis proof phase')
    value = json.loads(evidence.read_text())
    queue = value['queue']
    if not re.fullmatch(r'tempo-wake-proof-[a-f0-9]{32}', queue):
        raise RuntimeError('Foreign Redis restart evidence')
    verify_restart_proof(evidence)
    server = background_wakes.client(os.environ['TEMPO_REDIS_URL'])
    prefix = queue + ':owner:'
    keys = [*queue_keys(queue), '_kombu.binding.' + queue,
            *(prefix + name + suffix for name in background_wakes.WAKE_TASK_NAMES for suffix in ('', ':publication'))]
    if server.exists(*keys):
        raise RuntimeError('Owned restart keys remain')
    print(json.dumps({'project': project, 'revision': revision,
                      'case': 'test_real_broker_persistent_wake_survives_aof_restart', 'cleanup': 'completed'}))


if __name__ == '__main__':
    if sys.argv[1:] == ['lease']:
        print(json.dumps(proof()))
    elif sys.argv[1:] in (['prepare'], ['verify']):
        restart_phase(sys.argv[1])
    else:
        raise RuntimeError('Unknown Redis residual selection')
