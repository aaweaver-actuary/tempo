"""Exact native position proof in the runner's owned schema one-off container.

The disposable venv inherits the candidate image's runtime dependencies. Only
pytest and its locked dependencies are added, never to the production image.
"""
import contextlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib
import uuid

NODEID = 'database/tests/position_study/test_position_grade_effects.py::test_native_two_decisions_and_late_failure_rollback'
NODEIDS = {
    'position-queue-admission': 'backend/tests/test_position_queue_admission.py::test_native_refresh_admits_due_carrier_and_reuses_pending',
    'position-grade-effects': NODEID,
    'position-review-atomicity': 'database/tests/position_study/test_position_review_transport_atomicity.py::test_p05_real_issued_review_transport_effect_and_checkpoint_rollback',
    'position-introductions': 'backend/tests/test_position_introductions.py::test_native_authorized_introduction_writes_fresh_state_only_after_admission',
    'frozen-requeue-cycle': 'backend/tests/test_frozen_study_attempts_postgres.py::test_native_original_review_requeue_cycle_read_is_bound_and_read_only',
}


def selected_nodeid(environment):
    check = environment.get('TEMPO_NATIVE_POSITION_CHECK', 'position-grade-effects')
    if check not in NODEIDS:
        raise RuntimeError('Unknown native position check')
    return NODEIDS[check]


ROOT = Path(__file__).resolve().parents[2]
PYTEST_PACKAGES = {'pytest', 'iniconfig', 'packaging', 'pluggy', 'pygments'}


def identity(environment):
    project = environment.get('TEMPO_TEST_COMPOSE_PROJECT', '')
    revision = environment.get('TEMPO_NATIVE_CANDIDATE_REVISION', '')
    if (environment.get('TEMPO_TEST_INSTANCE') != 'disposable'
            or not re.fullmatch(r'tempo-pg-regressions-[0-9]+-[a-f0-9]{8}', project)
            or not re.fullmatch(r'[a-f0-9]{40}', revision)):
        raise RuntimeError('Refusing non-owned native proof environment')
    return project, revision


def locked_requirements(lock):
    selected = {p['name']: p for p in lock['package'] if p['name'] in PYTEST_PACKAGES}
    if selected.keys() != PYTEST_PACKAGES:
        raise RuntimeError('Missing locked pytest runtime')
    lines = []
    for name, package in sorted(selected.items()):
        hashes = [w['hash'] for w in package['wheels']]
        if not hashes or any(not re.fullmatch(r'sha256:[a-f0-9]{64}', h) for h in hashes):
            raise RuntimeError('Invalid pytest wheel hashes')
        lines.append(f"{name}=={package['version']} " + ' '.join('--hash=' + h for h in hashes))
    return '\n'.join(lines) + '\n'


class ExactCase:
    def __init__(self, nodeid=NODEID):
        self.nodeid = nodeid
        self.collected = []
        self.reports = []

    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]

    def pytest_runtest_logreport(self, report):
        self.reports.append({'id': report.nodeid, 'phase': report.when, 'status': report.outcome})

    def passed(self, exit_code):
        return (exit_code == 0 and self.collected == [self.nodeid]
                and self.reports == [{'id': self.nodeid, 'phase': phase, 'status': 'passed'}
                                     for phase in ('setup', 'call', 'teardown')])


def with_owned_database(admin, sql, name, project, execute, evidence):
    if not re.fullmatch(r'tempo_position_test_[a-f0-9]{32}', name):
        raise RuntimeError('Refusing non-owned position database name')
    # The outer runner has already validated Compose ownership and mounts.
    # Independently refuse an unmarked product database before creating anything.
    row = admin.execute("SELECT current_database(), shobj_description(oid, 'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()
    if row != ('tempo', 'tempo-disposable-postgres-test'):
        raise RuntimeError('Refusing PostgreSQL without disposable bootstrap marker')
    admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    try:
        admin.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier(name), sql.Literal(project)))
        return execute()
    finally:
        # Only the freshly created database is dropped; never a borrowed name.
        try:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))
            evidence['cleanup'] = 'dropped'
        except Exception:
            evidence['cleanup'] = 'failed'
            raise


def execute():
    import psycopg
    from psycopg import sql
    import pytest
    # Reject libpq environment overrides before the bootstrap connection too.
    if os.environ.get('TEMPO_NATIVE_POSITION_CHECK') == 'frozen-requeue-cycle':
        if any(name.startswith('PG') and name not in {'PG_VERSION', 'PG_MAJOR'}
               for name in os.environ):
            raise RuntimeError('Invalid owned frozen bridge identity: ambient libpq configuration')
    project, revision = identity(os.environ)
    nodeid = selected_nodeid(os.environ)
    database = 'tempo_position_test_' + uuid.uuid4().hex
    evidence = {'project': project, 'database': database, 'head': revision,
                'expected': [nodeid], 'runtime': {'python': sys.version, 'pytest': pytest.__version__, 'image': os.environ.get('TEMPO_NATIVE_RUNTIME_IMAGE')},
                'cleanup': 'pending'}
    recorder = ExactCase(nodeid)
    def run_case():
        os.environ['TEMPO_POSITION_TEST_DSN'] = 'postgresql://postgres@postgres:5432/' + database
        if os.environ.get('TEMPO_NATIVE_POSITION_CHECK') == 'frozen-requeue-cycle':
            os.environ['TEMPO_NATIVE_FROZEN_BRIDGE_DSN'] = os.environ['TEMPO_POSITION_TEST_DSN']
        else:
            os.environ.pop('TEMPO_NATIVE_FROZEN_BRIDGE_DSN', None)
        os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
        os.environ.pop('PYTEST_ADDOPTS', None)
        for path in (ROOT, ROOT / 'database', ROOT / 'backend'):
            sys.path.insert(0, str(path))
        os.chdir(ROOT)
        with contextlib.redirect_stdout(sys.stderr):
            code = pytest.main(['-q', '-p', 'no:cacheprovider', '-o', 'addopts=', '--rootdir=' + str(ROOT), nodeid], plugins=[recorder])
        evidence.update(collected=recorder.collected, reports=recorder.reports,
                        tests=[{'id': nodeid, 'status': 'passed' if recorder.passed(code) else 'failed', 'retries': 0}])
        if not recorder.passed(code):
            raise RuntimeError('Exact native pytest case did not execute once without skips/failures')
    try:
        with psycopg.connect('postgresql://postgres@postgres:5432/tempo', autocommit=True) as admin:
            with_owned_database(admin, sql, database, project, run_case, evidence)
        evidence['cleanup'] = 'dropped'
    except Exception:
        print(json.dumps(evidence), file=sys.stderr)
        raise
    finally:
        print(json.dumps(evidence))


def main():
    identity(os.environ)
    selected_nodeid(os.environ)
    if sys.argv[1:] == ['--execute']:
        execute()
        return
    if sys.argv[1:]:
        raise RuntimeError('Unknown native proof arguments')
    with tempfile.TemporaryDirectory(prefix='tempo-position-pytest-') as directory:
        root = Path(directory)
        subprocess.run([sys.executable, '-m', 'venv', '--system-site-packages', str(root / 'venv')], check=True, stdout=sys.stderr)
        requirements = root / 'requirements.txt'
        requirements.write_text(locked_requirements(tomllib.loads((ROOT / 'backend/uv.lock').read_text())))
        python = str(root / 'venv/bin/python')
        subprocess.run([python, '-m', 'pip', 'install', '--ignore-installed', '--no-deps', '--only-binary=:all:', '--require-hashes', '-r', str(requirements)], check=True, stdout=sys.stderr)
        subprocess.run([python, str(Path(__file__).resolve()), '--execute'], check=True)


if __name__ == '__main__':
    main()
