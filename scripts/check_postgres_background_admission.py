"""Real bounded admission/control/restart proof in the regular durability stage."""
import json
from contextlib import ExitStack
from datetime import datetime, timezone
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
        _proof_real_control_callbacks(database_url, identity)
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


def _proof_real_control_callbacks(database_url, identity):
    """Real registered callbacks commit; discretionary handlers never enter."""
    import chess.engine
    import httpx
    from app import game_analysis_commands, game_analysis_publication, main
    from app.services import game_analysis_worker, repertoire_coverage, threat_pipeline
    from app.services import redis_admission_gate

    assert redis_admission_gate.configured(), 'Control proof requires real shared Redis admission'
    report_game = identity + '-report-game'
    finalize_game = identity + '-finalize-game'
    report_id = identity + '-position'
    operation_ids = [identity + suffix for suffix in (
        '-position-result', '-finalize', '-stale-result', '-stale-finalize',
        '-maia-denied', '-threat-denied', '-claim-denied')]
    timestamp = datetime.now(timezone.utc).isoformat()
    raw_report = {'accepted': 'already-computed-result'}
    finalization = {
        'game_id': finalize_game, 'analysis_version': 2, 'expected_evidence_version': 1,
        'analysis_evidence_version': 3, 'lease_id': 'parent-lease',
        'idempotency_key': finalize_game + ':2:3',
        'prepared': {'evaluations': [{'already': 'computed'}]},
        'result': {'major_mistake_ply': None},
    }

    def prohibited(*_args, **_kwargs):
        raise AssertionError('Control command invoked engine/provider/traversal/publication')

    try:
        with psycopg.connect(database_url) as database:
            for game_id in (report_game, finalize_game):
                database.execute(
                    "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,"
                    "color,result,start_fen,moves_json,analysis_state) "
                    "VALUES(%s,'lichess','proof',%s,'rapid',1,'white','win',%s,'[]','analyzing')",
                    (game_id, timestamp, chess.STARTING_FEN))
                database.execute(
                    "INSERT INTO game_analysis_jobs(game_id,status,lease_id,analysis_version,"
                    "analysis_evidence_version,updated_at) VALUES(%s,'leased','parent-lease',2,1,%s)",
                    (game_id, timestamp))
            database.execute(
                "INSERT INTO game_analysis_position_reports(id,game_id,analysis_version,scan_pass,"
                "position_index,request_json,state,lease_id,parent_lease_id,updated_at) "
                "VALUES(%s,%s,2,'shallow',0,'accepted-request','leased','position-lease','parent-lease',%s)",
                (report_id, report_game, timestamp))

        with ExitStack() as boundaries:
            for module, names in (
                (chess.engine.SimpleEngine, ('popen_uci',)),
                (httpx.Client, ('request',)),
                (game_analysis_commands, ('prepare_position_claim', 'prepare_position_report', '_positions', '_confirmed_indices')),
                (game_analysis_worker, ('build_game_evaluations', '_positions', '_confirmed_indices')),
                (main, ('build_game_evaluations', '_validated_analysis_evaluations', 'classify_swings')),
                (game_analysis_publication, ('execute_game_analysis_publication_slice', 'execute_game_analysis_followup_slice', '_slice_rows')),
                (tasks, ('execute_game_analysis_publication_slice', 'execute_game_analysis_followup_slice', 'execute_game_findings_slice')),
                (threat_pipeline, ('execute_threat_scan_slice', 'execute_threat_validation', 'validate_analysis_report')),
                (repertoire_coverage, ('discover_opponent_positions',)),
            ):
                for name in names:
                    boundaries.enter_context(patch.object(module, name, prohibited))
            for command_name in ('coverage.maia.submit', 'threat.analysis.report', 'games.analysis.position.claim'):
                boundaries.enter_context(patch.dict(command_gateway._handlers, {command_name: prohibited}))
            with activity_gate.foreground():
                assert redis_admission_gate.foreground_present()
                position_payload = {'report_id': report_id, 'lease_id': 'position-lease',
                                    'request_json': 'accepted-request', 'report': raw_report}
                assert tasks.execute_background_command.run(operation_ids[0], 'games.analysis.position.report', position_payload) == {'status': 'complete'}
                queued = tasks.execute_background_command.run(operation_ids[1], 'games.analysis.finalize.admit', finalization)
                assert queued['status'] == 'preparing'
                assert tasks.execute_background_command.run(operation_ids[0], 'games.analysis.position.report', position_payload) is None
                assert tasks.execute_background_command.run(operation_ids[1], 'games.analysis.finalize.admit', finalization) is None
                # Completed result and publishing job cannot be overwritten by old deliveries.
                assert tasks.execute_background_command.run(operation_ids[2], 'games.analysis.position.report',
                    {**position_payload, 'lease_id': 'obsolete', 'report': {'obsolete': True}}) is None
                assert tasks.execute_background_command.run(operation_ids[3], 'games.analysis.finalize.admit',
                    {**finalization, 'analysis_version': 3}) is None
                for operation_id, command_name in zip(operation_ids[4:], (
                    'coverage.maia.submit', 'threat.analysis.report', 'games.analysis.position.claim')):
                    assert tasks.execute_background_command.run(operation_id, command_name, {'saved': True}) is None
                with activity_gate.background_job('test', identity, yielding=True), activity_gate.background_control(), background_connection() as database:
                    assert database.execute_native('SHOW transaction_timeout').fetchone()[0] == '250ms'
                    assert database.execute_native('SHOW lock_timeout').fetchone()[0] == '25ms'
                assert not activity_gate.in_background_control
                try:
                    activity_gate.check_background_admission()
                except BackgroundAdmissionDeferred:
                    pass
                else:
                    raise AssertionError('Control bypass leaked into ordinary admission')

        postgres_store.close_pools()
        with psycopg.connect(database_url) as database:
            assert database.execute('SELECT state,report_json FROM game_analysis_position_reports WHERE id=%s', (report_id,)).fetchone() == ('complete', json.dumps(raw_report))
            assert database.execute('SELECT status,lease_id FROM game_analysis_jobs WHERE game_id=%s', (report_game,)).fetchone() == ('queued', None)
            assert database.execute('SELECT status,lease_id FROM game_analysis_jobs WHERE game_id=%s', (finalize_game,)).fetchone() == ('publishing', None)
            publication = database.execute('SELECT status,next_ply,prepared_json FROM game_analysis_publications WHERE game_id=%s', (finalize_game,)).fetchone()
            assert publication[:2] == ('queued', 0) and json.loads(publication[2]) == finalization['prepared']
            assert database.execute("SELECT COUNT(*) FROM background_tasks WHERE kind='game_analysis_publish' AND deduplication_key=%s", (finalize_game,)).fetchone()[0] == 1
            assert database.execute('SELECT COUNT(*) FROM game_move_analysis_staged WHERE game_id IN (%s,%s)', (report_game, finalize_game)).fetchone()[0] == 0
            for operation_id in operation_ids[:2]:
                assert database.execute('SELECT state FROM operation_receipts WHERE operation_id=%s', (operation_id,)).fetchone() == ('complete',)
            for operation_id in operation_ids[2:4]:
                assert database.execute('SELECT state FROM operation_receipts WHERE operation_id=%s', (operation_id,)).fetchone() == ('failed',)
            for operation_id in operation_ids[4:]:
                receipt = database.execute('SELECT state,attempt_count,cycle_attempt_count,payload_json FROM operation_receipts WHERE operation_id=%s', (operation_id,)).fetchone()
                assert receipt[:3] == ('retrying', 0, 0) and json.loads(receipt[3]) == {'saved': True}
        print('PASS test_postgres_real_control_results_and_finalization_commit_without_analysis_during_foreground')
    finally:
        postgres_store.close_pools()
        with psycopg.connect(database_url) as database:
            database.execute('DELETE FROM background_tasks WHERE deduplication_key=%s', (finalize_game,))
            database.execute('DELETE FROM imported_games WHERE id IN (%s,%s)', (report_game, finalize_game))
            database.execute('DELETE FROM operation_receipts WHERE operation_id=ANY(%s)', (operation_ids,))
