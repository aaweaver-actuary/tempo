"""Real bounded admission/control/restart proof in the regular durability stage."""
import json
import os
from pathlib import Path
import sys
from threading import Event, Thread
import time
from unittest.mock import patch
import uuid

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
from app import command_gateway, postgres_store, tasks
from app.database import background_connection
from app.services import durable_tasks
from app.services.activity_gate import activity_gate, BackgroundAdmissionDeferred


def proof_background_admission(database_url):
    import check_postgres_graph_retention as fixtures
    with patch.object(fixtures, "DATABASE_URL", database_url), fixtures.owned_fixture_database():
        _proof_background_admission(os.environ["TEMPO_DATABASE_WRITE_URL"])


def _proof_background_admission(database_url):
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Admission proof requires an explicitly disposable database')
    identity = 'admission-proof-'+uuid.uuid4().hex
    command_name = identity+'.receipt'
    task = durable_tasks.enqueue_task('daily_queue', identity,
                                     {'checkpoint': 'saved'}, foreground=False)
    original_claim = durable_tasks.claim_task
    useful = []
    finished = Event()
    errors = []
    def poll():
        try:
            assert tasks.poll_background_tasks.run() is False
        except BaseException as error:
            errors.append(error)
        finally:
            finished.set()
    def claim_owned(**kwargs):
        return original_claim('daily_queue')
    def publish(claimed):
        assert claimed['id'] == task['id']
        useful.append(claimed['payload']['checkpoint'])
        with background_connection() as database:
            assert durable_tasks.complete_task_slice_in_transaction(database, claimed)
        return False
    def receipt(database, payload):
        return {'accepted': payload['value']}
    command_gateway.register_command(command_name, receipt)
    try:
        with patch.object(tasks, 'claim_task', claim_owned), patch.object(tasks, 'execute_postgres_queue_refresh_slice', publish):
            with activity_gate.foreground():
                started = time.perf_counter()
                worker = Thread(target=poll)
                worker.start()
                assert finished.wait(1), 'Denied work occupied the only analysis worker'
                worker.join(1)
                assert not errors and not worker.is_alive(), errors
                denied_seconds = time.perf_counter()-started
                with psycopg.connect(database_url) as database:
                    row = database.execute('SELECT state,attempt_count,lease_token FROM background_tasks WHERE id=%s',(task['id'],)).fetchone()
                    assert row == ('queued',0,None)
                    assert database.execute('SELECT 1').fetchone()[0] == 1
                with patch.object(tasks, '_CONTROL_BACKGROUND_COMMANDS', tasks._CONTROL_BACKGROUND_COMMANDS | {command_name}):
                    assert tasks.execute_background_command.run(identity,command_name,{'value':1}) == {'accepted':1}
                    assert tasks.execute_background_command.run(identity,command_name,{'value':1}) is None
            tasks.poll_background_tasks.run()
            tasks.poll_background_tasks.run()
            assert useful == ['saved']
        # An admission race must roll back domain writes and preserve the old cursor.
        task = durable_tasks.enqueue_task('daily_queue',identity,{'checkpoint':'resume'},foreground=False)
        with psycopg.connect(database_url) as database:
            database.execute("UPDATE background_tasks SET phase='admit_due' WHERE id=%s",(task['id'],))
        def raced(claimed):
            with background_connection() as database:
                database.execute("UPDATE background_tasks SET payload_json='{}' WHERE id=?",(claimed['id'],))
                raise BackgroundAdmissionDeferred('controlled foreground race')
            raise AssertionError('Denied slice unexpectedly committed')
        with patch.object(tasks, 'claim_task', claim_owned), patch.object(tasks, 'execute_postgres_queue_refresh_slice', raced):
            assert tasks.poll_background_tasks.run() is False
        with psycopg.connect(database_url) as database:
            row = database.execute('SELECT state,phase,payload_json,attempt_count,lease_token FROM background_tasks WHERE id=%s',(task['id'],)).fetchone()
            assert row == ('retrying','admit_due','{"checkpoint":"resume"}',0,None),row
            database.execute("UPDATE background_tasks SET next_attempt_at='2000-01-01' WHERE id=%s",(task['id'],))
        postgres_store.close_pools()
        with patch.object(tasks, 'claim_task', claim_owned), patch.object(tasks, 'execute_postgres_queue_refresh_slice', publish):
            tasks.poll_background_tasks.run()
            tasks.poll_background_tasks.run()
        assert useful == ['saved','resume']
        # Denied commands retain a recoverable receipt and no spent failure attempt.
        with activity_gate.foreground():
            assert tasks.execute_background_command.run(identity+'-denied',command_name,{'value':2}) is None
        with psycopg.connect(database_url) as database:
            row = database.execute('SELECT state,attempt_count,cycle_attempt_count,payload_json FROM operation_receipts WHERE operation_id=%s',(identity+'-denied',)).fetchone()
            assert row[:3] == ('retrying',0,0) and json.loads(row[3]) == {'value':2}
        print('PASS test_postgres_foreground_denial_control_receipt_restart_and_idempotent_replay '+json.dumps({'denial_seconds':denied_seconds,'useful_slices':len(useful)}))
    finally:
        command_gateway._handlers.pop(command_name,None)
        postgres_store.close_pools()
        with psycopg.connect(database_url) as database:
            database.execute('DELETE FROM background_tasks WHERE id=%s',(task['id'],))
            database.execute('DELETE FROM operation_receipts WHERE operation_id IN (%s,%s)',(identity,identity+'-denied'))
