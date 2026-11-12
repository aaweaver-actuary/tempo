"""One G03 split proof in a freshly migrated, invocation-owned database.

This is not a position synthetic-schema selector or the retired TEMPO224 suite.
"""
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import uuid

spec = importlib.util.spec_from_file_location('position_bridge', Path(__file__).with_name('position-native-pytest.py'))
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
ROOT = bridge.ROOT
NODEID = 'database/tests/native_review/test_postgres.py::test_native_split_then_refresh_retains_verified_after_four_placement'
CHECK = 'prefix-split-placement'


def selected_nodeid(environment):
    if environment.get('TEMPO_NATIVE_POSITION_CHECK') != CHECK:
        raise RuntimeError('Unknown native split check')
    return NODEID


@contextlib.contextmanager
def application_database(dsn, project, postgres):
    """Only this proof process borrows the new database; never inherit app pools."""
    from psycopg.conninfo import conninfo_to_dict
    info = conninfo_to_dict(dsn)
    if (not bridge.re.fullmatch(r'tempo-pg-regressions-[0-9]+-[a-f0-9]{8}', project)
            or info.get('host') != 'postgres' or info.get('port') != '5432'
            or not bridge.re.fullmatch(r'tempo_position_test_[a-f0-9]{32}', info.get('dbname', ''))):
        raise RuntimeError('Refusing non-owned split application database')
    values = {'TEMPO_DATABASE_WRITE_URL': dsn, 'TEMPO_DATABASE_READ_URL': dsn,
              'TEMPO224_ADMIN': dsn, 'TEMPO224_FIXTURE': 'tempo224-' + project}
    original = {key: os.environ.get(key) for key in values}
    postgres.close_pools()
    os.environ.update(values)
    try:
        yield
    finally:
        postgres.close_pools()
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def execute():
    import psycopg
    from psycopg import sql
    import pytest
    project, revision = bridge.identity(os.environ)
    selected_nodeid(os.environ)
    for path in (ROOT, ROOT / 'database', ROOT / 'backend'):
        sys.path.insert(0, str(path))
    from database.tooling.apply_postgres_migrations import apply_migrations
    from tempo_storage import postgres
    database = 'tempo_position_test_' + uuid.uuid4().hex
    dsn = 'postgresql://postgres@postgres:5432/' + database
    evidence = {'project': project, 'database': database, 'head': revision,
                'expected': [NODEID], 'runtime': {'python': sys.version, 'pytest': pytest.__version__,
                'image': os.environ.get('TEMPO_NATIVE_RUNTIME_IMAGE')}, 'cleanup': 'pending'}
    recorder = bridge.ExactCase(NODEID)
    def run_case():
        # Same migration owner and initial rows as bootstrap_postgres_test.py.
        # No role provisioning: this isolated fixture process uses its owned admin DSN.
        with contextlib.redirect_stdout(sys.stderr):
            apply_migrations(dsn)
        with psycopg.connect(dsn) as raw:
            raw.execute('INSERT INTO settings(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
            raw.execute('INSERT INTO tactic_rotation(id) VALUES(1) ON CONFLICT(id) DO NOTHING')
        os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
        os.environ.pop('PYTEST_ADDOPTS', None)
        os.chdir(ROOT)
        with application_database(dsn, project, postgres), contextlib.redirect_stdout(sys.stderr):
            code = pytest.main(['-q', '-p', 'no:cacheprovider', '-o', 'addopts=', '--rootdir=' + str(ROOT), NODEID], plugins=[recorder])
        evidence.update(collected=recorder.collected, reports=recorder.reports,
                        tests=[{'id': NODEID, 'status': 'passed' if recorder.passed(code) else 'failed', 'retries': 0}])
        if not recorder.passed(code):
            raise RuntimeError('Exact native split case did not execute once without skips/failures')
    try:
        with psycopg.connect('postgresql://postgres@postgres:5432/tempo', autocommit=True) as admin:
            bridge.with_owned_database(admin, sql, database, project, run_case, evidence)
    finally:
        print(json.dumps(evidence))


def main():
    bridge.identity(os.environ)
    selected_nodeid(os.environ)
    if sys.argv[1:] == ['--execute']:
        execute()
        return
    if sys.argv[1:]:
        raise RuntimeError('Unknown native split proof arguments')
    with tempfile.TemporaryDirectory(prefix='tempo-split-pytest-') as directory:
        root = Path(directory)
        subprocess.run([sys.executable, '-m', 'venv', '--system-site-packages', str(root / 'venv')], check=True, stdout=sys.stderr)
        requirements = root / 'requirements.txt'
        requirements.write_text(bridge.locked_requirements(tomllib.loads((ROOT / 'backend/uv.lock').read_text())))
        python = str(root / 'venv/bin/python')
        subprocess.run([python, '-m', 'pip', 'install', '--ignore-installed', '--no-deps', '--only-binary=:all:', '--require-hashes', '-r', str(requirements)], check=True, stdout=sys.stderr)
        subprocess.run([python, str(Path(__file__).resolve()), '--execute'], check=True)


if __name__ == '__main__':
    main()
