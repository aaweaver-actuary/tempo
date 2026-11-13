"""The disposable browser fixture can recover displacement after lost seed output."""
import ast
import json
from pathlib import Path

import pytest
from native_study_fixture import owned_postgres


def receipt_validator():
    path = Path(__file__).resolve().parents[2] / 'tests/fixtures/postgres-frozen-study-browser.py'
    tree = ast.parse(path.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'validate_displacement_receipt')
    namespace = {'json': json}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['validate_displacement_receipt']


def test_lost_seed_ack_recovers_exact_rows_and_rejects_foreign_or_changed_receipt():
    validate = receipt_validator()
    rows = [[17, 4, 24], [21, 7, 27]]
    receipt = {'kind': 'p12-frozen-displacement-v1', 'prefix': 'owned', 'day': '2026-11-13', 'rows': rows, 'cards': {'17': 'card-a', '21': 'card-b'}}
    assert validate(json.dumps(receipt), 'owned', '2026-11-13', []) == rows
    assert validate(json.dumps(receipt), 'owned', '2026-11-13', rows) == rows
    for bad in ({**receipt, 'prefix': 'foreign'}, {**receipt, 'day': '2026-11-14'},
                {**receipt, 'rows': [rows[0], rows[0]]}, {**receipt, 'rows': [[True, 4, 24]]}, {**receipt, 'cards': {'17': 'foreign'}}):
        with pytest.raises(RuntimeError, match='preserve fixture'):
            validate(json.dumps(bad), 'owned', '2026-11-13', [])
    with pytest.raises(RuntimeError, match='preserve fixture'):
        validate(json.dumps(receipt), 'owned', '2026-11-13', [[17, 4, 25]])


def test_seed_and_cleanup_share_invocation_fence_and_missing_receipt_fails_closed():
    source = (Path(__file__).resolve().parents[2] / 'tests/fixtures/postgres-frozen-study-browser.py').read_text()
    fence = source.index("if action in ('seed', 'seed-requeue', 'cleanup'):")
    lock = source.index('pg_advisory_xact_lock', fence)
    seed = source.index("if action in ('seed', 'seed-requeue'):", lock)
    cleanup = source.index("elif action == 'cleanup':", seed)
    receipt = source.index("SELECT source_json FROM studies", cleanup)
    missing = source.index("Missing owned displacement receipt; seed may still run", receipt)
    deletion = source.index('DELETE FROM operation_receipts', missing)
    assert fence < lock < seed < cleanup < receipt < missing < deletion
    for table in ('queue_attempt_origins', 'review_attempt_receipts', 'study_sources', 'study_positions', 'operation_receipts'):
        assert table in source[receipt:missing]


def test_native_frozen_fixture_lost_seed_ack_waits_and_restores(owned_postgres, tmp_path):
    """Real task-owned PostgreSQL: an in-flight seed and cleanup serialize."""
    import os
    import subprocess
    import sys
    import time
    import uuid
    import psycopg
    from psycopg import sql

    options = dict(owned_postgres.connection_options)
    with psycopg.connect(**{**options, 'autocommit': True}) as admin:
        assert admin.execute("SELECT count(*) FROM pg_database WHERE datname='tempo'").fetchone()[0] == 0
        admin.execute(sql.SQL('CREATE DATABASE tempo TEMPLATE {}').format(sql.Identifier(options['dbname'])))
        admin.execute("COMMENT ON DATABASE tempo IS 'tempo-disposable-postgres-test'")
    options['dbname'] = 'tempo'
    options_file = tmp_path / 'connection.json'
    options_file.write_text(json.dumps(options))
    options_file.chmod(0o600)
    prefix = 'p12-frozen-' + uuid.uuid4().hex
    day = '2026-11-13'
    with psycopg.connect(**options) as db:
        assert db.execute("SELECT current_database(),shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone() == ('tempo', 'tempo-disposable-postgres-test')
        db.execute("INSERT INTO queue_projections(queue_date,state,refresh_pending) VALUES(%s,'ready',0)", (day,))
        db.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date) VALUES('native-unrelated','white','prefix','rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1','[\"e2e4\"]','new',%s)", (day,))
        entry = db.execute("INSERT INTO daily_queue(queue_date,card_id,cycle,position) VALUES(%s,'native-unrelated',0,8) RETURNING id", (day,)).fetchone()[0]
    script = Path(__file__).resolve().parents[2] / 'tests/fixtures/postgres-frozen-study-browser.py'
    wrapper = '''import json,os,runpy,sys,psycopg
opts=json.load(open(sys.argv[1])); name=sys.argv[2]; target=sys.argv[3]; args=sys.argv[4:]
original=psycopg.connect
def connect(dsn):
 if dsn != 'postgresql://postgres@postgres:5432/tempo': raise RuntimeError('unexpected fixture target')
 return original(**opts, application_name='fe10-'+name)
psycopg.connect=connect
sys.argv=[target,*args]
runpy.run_path(target,run_name='__main__')
'''
    def start(action, *args):
        return subprocess.Popen([sys.executable, '-c', wrapper, str(options_file), action, str(script), action, prefix, day, *args],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env={**os.environ, 'TEMPO_TEST_INSTANCE': 'disposable'})
    with psycopg.connect(**options) as blocker:
        blocker.execute('SELECT id FROM daily_queue WHERE id=%s FOR UPDATE', (entry,))
        seed = start('seed')
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with psycopg.connect(**options) as probe:
                locked = probe.execute("SELECT count(*) FROM pg_stat_activity a JOIN pg_locks l ON l.pid=a.pid WHERE a.application_name='fe10-seed' AND l.locktype='advisory' AND l.granted").fetchone()[0]
            if locked: break
            if seed.poll() is not None: raise AssertionError(seed.communicate())
            time.sleep(.05)
        assert locked == 1
        cleanup = start('cleanup', '[]', '[]')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with psycopg.connect(**options) as probe:
                waiting = probe.execute("SELECT count(*) FROM pg_stat_activity a JOIN pg_locks l ON l.pid=a.pid WHERE a.application_name='fe10-cleanup' AND l.locktype='advisory' AND NOT l.granted").fetchone()[0]
            if waiting: break
            if cleanup.poll() is not None: raise AssertionError(cleanup.communicate())
            time.sleep(.05)
        assert waiting == 1
        blocker.commit()
    seed_out, seed_err = seed.communicate(timeout=30)
    assert seed.returncode == 0, seed_err
    # Simulate lost seed stdout: cleanup was already called with an empty receipt.
    cleanup_out, cleanup_err = cleanup.communicate(timeout=30)
    assert cleanup.returncode == 0, cleanup_err
    result = json.loads(cleanup_out)
    assert result['cleaned'] and result['displaced_count'] == result['restored'] == 1
    with psycopg.connect(**options) as db:
        assert db.execute('SELECT card_id,position FROM daily_queue WHERE id=%s', (entry,)).fetchone() == ('native-unrelated', 8)
        assert db.execute('SELECT 1 FROM studies WHERE id=%s', (prefix,)).fetchone() is None
