"""Guard the bounded disposable split diagnostic against foreign identities."""
import ast
from pathlib import Path
import re
import uuid
import pytest
from native_study_fixture import owned_postgres


def test_split_diagnostic_binds_exact_owned_ids_and_rejects_foreign_values():
    path = Path(__file__).resolve().parents[2] / 'tests/fixtures/postgres-manual-prefix-diagnostics.py'
    tree = ast.parse(path.read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'validate_ids')
    namespace = {'re': re, 'uuid': uuid}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    validate = namespace['validate_ids']; intent = str(uuid.uuid4()); op = str(uuid.uuid4()); attempt = str(uuid.uuid4())
    assert validate('a' * 64, intent, op, 'review-attempt:' + attempt) == 'prefix-split-intent:' + intent
    for args in [('foreign', intent, op, 'review-attempt:' + attempt), ('a'*64, '../foreign', op, 'review-attempt:' + attempt),
                 ('a'*64, intent, op, 'review-attempt:foreign')]:
        with pytest.raises(ValueError): validate(*args)
    source = path.read_text()
    assert 'SET TRANSACTION READ ONLY' in source and 'tempo-disposable-postgres-test' in source
    assert 'attempt_token IS NOT NULL' in source and 'left(last_error_json,1000)' in source


def test_split_browser_preserves_original_and_cleanup_failures():
    path = Path(__file__).resolve().parents[2] / 'tests/browser/manual-prefix-split-http.spec.ts'
    source = path.read_text()
    assert 'Original split failure:' in source and 'Fixture cleanup failure:' in source
    assert 'throw original;' in source and "timeout:90_000" in source
    assert "split-diagnostic-failure" in source and '.catch(() => undefined)' in source


def test_native_split_diagnostic_reads_exact_and_missing_receipts(owned_postgres, tmp_path):
    import json
    import os
    import subprocess
    import sys
    import psycopg
    from psycopg import sql

    options = dict(owned_postgres.connection_options)
    with psycopg.connect(**{**options, 'autocommit': True}) as admin:
        assert admin.execute("SELECT count(*) FROM pg_database WHERE datname='tempo'").fetchone()[0] == 0
        admin.execute(sql.SQL('CREATE DATABASE tempo TEMPLATE {}').format(sql.Identifier(options['dbname'])))
        admin.execute("COMMENT ON DATABASE tempo IS 'tempo-disposable-postgres-test'")
    options['dbname'] = 'tempo'
    options_file = tmp_path / 'connection.json'
    options_file.write_text(json.dumps(options)); options_file.chmod(0o600)
    card = 'a' * 64; intent = str(uuid.uuid4()); acceptance = str(uuid.uuid4()); attempt = str(uuid.uuid4())
    review = 'review-attempt:' + attempt; child = 'prefix-split-intent:' + intent
    with psycopg.connect(**options) as db:
        db.execute("INSERT INTO study_deferred_prefix_splits(intent_id,card_id,source_revision,queue_entry_id,state,review_attempt_id,review_id,child_operation_id) VALUES(%s,%s,1,12,'ready',%s,7,%s)", (intent,card,attempt,child))
        db.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json,background,attempt_count,retry_cycle,cycle_attempt_count,attempt_token,last_error_json) VALUES(%s,'cards.prefix_split.apply_deferred','hash','retrying','{}',false,3,2,1,'private-token',%s)", (child, json.dumps({'message':'owned retry'})))
        db.execute("INSERT INTO review_attempt_receipts(attempt_id,card_id,queue_entry_id,outcome,guided,review_id,scheduling_status,result_json) VALUES(%s,%s,12,'correct',0,7,'scheduled','{}')", (attempt,card))
    script = Path(__file__).resolve().parents[2] / 'tests/fixtures/postgres-manual-prefix-diagnostics.py'
    wrapper = '''import json,runpy,sys,psycopg
opts=json.load(open(sys.argv[1])); target=sys.argv[2]; args=sys.argv[3:]; original=psycopg.connect
def connect(dsn, **kwargs):
 if dsn != 'postgresql://postgres@postgres:5432/tempo': raise RuntimeError('unexpected target')
 return original(**opts, **kwargs)
psycopg.connect=connect; sys.argv=[target,*args]; runpy.run_path(target,run_name='__main__')
'''
    result = subprocess.run([sys.executable, '-c', wrapper, str(options_file), str(script), card,intent,acceptance,review],
        capture_output=True, text=True, timeout=15, env={**os.environ,'TEMPO_TEST_INSTANCE':'disposable'})
    assert result.returncode == 0, result.stderr
    evidence = json.loads(result.stdout)
    assert evidence['expected_operations'] == [acceptance,review,child]
    assert evidence['intent']['child_operation_id'] == child and evidence['intent']['review_attempt_id'] == attempt
    assert evidence['review_receipt'] == {'attempt_id':attempt,'card_id':card,'queue_entry_id':12,'review_id':7}
    assert len(evidence['operations']) == 1
    operation = evidence['operations'][0]
    assert operation['operation_id'] == child and operation['state'] == 'retrying'
    assert (operation['attempt_count'],operation['retry_cycle'],operation['cycle_attempt_count'],operation['leased']) == (3,2,1,True)
    assert 'owned retry' in operation['last_error_json'] and 'private-token' not in result.stdout
