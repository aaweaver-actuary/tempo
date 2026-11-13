"""Run retained graph cleanup, restart and history proof on disposable PostgreSQL."""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root), str(root / 'database'), str(root / 'backend')]


def main():
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Background proof requires the disposable test runner')
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    from database.tests.check_postgres_graph_retention import run
    run()


if __name__ == '__main__' and len(sys.argv) == 1:
    main()


def _await_queue_generation(read, wake, now, pause, task_id, generation, epoch):
    """Wait for the captured review generation and its durable projection."""
    deadline = now() + 90
    while True:
        task, projection = read()
        assert task and task['id'] == task_id and task['generation'] == generation, (
            f'queue generation changed after review: {task}'
        )
        if (task['state'] == 'complete' and projection and
                projection['published_refresh_epoch'] >= epoch):
            return task
        assert now() < deadline, f'post-review queue generation did not complete: {task}, {projection}'
        wake()
        pause(0.25)


def stale_delivery_witness(phase, project):
    """Real broker deliveries retain retired identities while Study progresses."""
    import json
    import hashlib
    import time
    from datetime import date, datetime, timezone
    import psycopg
    from psycopg.rows import dict_row
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    from app.celery_app import celery_app
    from app.command_gateway import request_digest
    from app.services import durable_tasks
    from tempo_storage import postgres

    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable' or not project.startswith('tempo-pg-regressions-'):
        raise RuntimeError('Broker witness requires the owned disposable test project')
    if phase not in ('before', 'after'):
        raise ValueError('Unknown stale-delivery phase')
    dsn = 'postgresql://postgres@postgres:5432/tempo'
    os.environ['TEMPO_DATABASE_WRITE_URL'] = dsn
    os.environ['TEMPO_DATABASE_READ_URL'] = dsn
    postgres.close_pools()
    prefix = 'retired-witness-' + project
    fg, bg, task_id = prefix + '-foreground', prefix + '-background', prefix + '-task'
    pack_id, puzzle_id = 'anastasiaMate-easy-01', '1QPdO'
    operation, delivery, game_task = (prefix + suffix for suffix in ('-operation', '-delivery', '-game-task'))
    identity = {'workspace_id': 'local', 'provider': 'lichess', 'game_id': prefix, 'decision_id': 'decision', 'occurrence_ply': 1}
    logical_id = json.dumps(list(identity.values()), separators=(',', ':'))
    effect = 'game-effect:' + hashlib.sha256(logical_id.encode()).hexdigest()
    selection = {'repertoire_id': prefix, 'game_id': prefix, 'occurrence_ply': 1}
    original_payload = json.dumps(selection, indent=1) + '\n'
    task_payload = json.dumps({'after': effect}, separators=(',', ':'))
    delivery_payload = json.dumps({'key': identity, 'source_key': prefix})
    digest = request_digest('game_evidence.product_accept', selection)
    retired_tables = ('next_opponent_accounts', 'next_opponent_snapshots', 'endgame_attempts',
                      'daily_chess_insights', 'daily_chess_snapshots', 'daily_statistics_jobs', 'game_insight_recommendations',
                      'game_analysis_publications', 'game_analysis_position_reports', 'game_analysis_position_errors',
                      'game_analysis_jobs', 'engine_scheduling_state')

    def snapshot():
        with psycopg.connect(dsn, row_factory=dict_row) as db:
            receipt = db.execute('SELECT row_to_json(r)::text AS row FROM operation_receipts r WHERE operation_id=%s', (fg,)).fetchone()
            absent = db.execute('SELECT count(*) AS n FROM operation_receipts WHERE operation_id=%s', (bg,)).fetchone()['n']
            task = db.execute('SELECT row_to_json(t)::text AS row FROM background_tasks t WHERE id=%s', (task_id,)).fetchone()
            attempts = db.execute('SELECT row_to_json(a)::text AS row FROM defense_attempts a ORDER BY attempt_id').fetchall()
            activation = db.execute('SELECT row_to_json(a)::text AS row FROM tactic_pack_activation a WHERE pack_id=%s', (pack_id,)).fetchone()
            progress = db.execute('SELECT row_to_json(p)::text AS row FROM tactic_progress p WHERE puzzle_id=%s', (puzzle_id,)).fetchone()
            grades = db.execute('SELECT row_to_json(r)::text AS row FROM reviews r WHERE card_id=%s ORDER BY id', (prefix,)).fetchall()
            assert len(grades) == 1
            source = db.execute('SELECT row_to_json(r)::text AS row FROM product_feedback_sources r WHERE source_key=%s', (prefix,)).fetchone()
            assert source
            effect_row = db.execute('SELECT identity_json FROM study_game_feedback WHERE effect_key=%s', (effect,)).fetchone()
            delivery_row = db.execute('SELECT effect_key,payload FROM product_feedback_deliveries WHERE operation_id=%s', (delivery,)).fetchone()
            command_row = db.execute('SELECT request_hash,payload_json FROM operation_receipts WHERE operation_id=%s', (operation,)).fetchone()
            task_row = db.execute('SELECT payload_json FROM background_tasks WHERE id=%s', (game_task,)).fetchone()
            assert json.loads(effect_row['identity_json']) == identity and delivery_row['effect_key'] == logical_id and delivery_row['payload']['key'] == identity
            assert command_row['request_hash'] == digest and command_row['payload_json'] == original_payload and json.loads(task_row['payload_json'])['after'] == effect
            scoped = {
                'effects': db.execute('SELECT effect_key FROM study_game_feedback WHERE card_id=%s OR effect_key=%s OR identity_json LIKE %s OR payload_json LIKE %s ORDER BY effect_key', (prefix, effect, '%' + prefix + '%', '%' + prefix + '%')).fetchall(),
                'deliveries': db.execute('SELECT operation_id FROM product_feedback_deliveries WHERE source_key=%s OR effect_key=%s ORDER BY operation_id', (prefix, logical_id)).fetchall(),
                'operations': db.execute('SELECT operation_id FROM operation_receipts WHERE operation_id LIKE %s OR payload_json LIKE %s ORDER BY operation_id', (prefix + '%', '%' + prefix + '%')).fetchall(),
                'tasks': db.execute('SELECT id FROM background_tasks WHERE id LIKE %s OR deduplication_key LIKE %s OR payload_json LIKE %s OR payload_json LIKE %s ORDER BY id', (prefix + '%', prefix + '%', '%' + prefix + '%', '%' + effect + '%')).fetchall(),
                'review_receipts': db.execute('SELECT attempt_id FROM review_attempt_receipts WHERE card_id=%s ORDER BY attempt_id', (prefix,)).fetchall(),
            }
            assert {key: [next(iter(row.values())) for row in values] for key, values in scoped.items()} == {
                'effects': [effect], 'deliveries': [delivery], 'operations': sorted([fg, operation]),
                'tasks': sorted([game_task, task_id]), 'review_receipts': []}
            history = {'existing_grades': grades, 'source': source['row'], 'scoped': scoped}
            for table, key, value in (('study_game_feedback', 'effect_key', effect),
                                      ('operation_receipts', 'operation_id', operation),
                                      ('product_feedback_deliveries', 'operation_id', delivery),
                                      ('background_tasks', 'id', game_task)):
                record = db.execute(f'SELECT row_to_json(r)::text AS row FROM {table} r WHERE {key}=%s', (value,)).fetchone()
                ledger = db.execute('SELECT row_to_json(r)::text AS row FROM tempo_retired_work r WHERE source_table=%s AND source_key=%s', (table, value)).fetchone()
                assert record and ledger, (table, value)
                assert db.execute(f"SELECT l.disposition='paused' AND l.source_record=to_jsonb(r) AS matches FROM tempo_retired_work l JOIN {table} r ON r.{key}=l.source_key WHERE l.source_table=%s AND l.source_key=%s", (table, value)).fetchone()['matches']
                history[table] = {'source': record['row'], 'retirement': ledger['row']}
            missing = {name: db.execute('SELECT to_regclass(%s) IS NULL AS missing', ('public.' + name,)).fetchone()['missing'] for name in retired_tables}
        assert receipt and task and activation and progress and absent == 0 and all(missing.values()) and len(attempts) == 1, (receipt, task, activation, progress, absent, missing, attempts)
        assert json.loads(attempts[0]['row']) == {'attempt_id': prefix, 'candidate_id': prefix, 'card_id': prefix, 'queue_entry_id': None, 'exercise_revision': 7, 'move_uci': 'e2e4', 'grade_json': '{\"status\":\"incorrect\"}', 'reviewed_at': '2026-01-01'}
        assert json.loads(activation['row']) == {'pack_id': pack_id, 'active': 1}
        assert json.loads(progress['row']) == {'puzzle_id': puzzle_id, 'deck_id': pack_id, 'card_id': None, 'clean_pass_at': '2026-01-01', 'admitted_at': '2026-01-02', 'admission_mode': 'light'}
        return {'receipt': receipt['row'], 'task': task['row'], 'defense_attempts': attempts, 'tactic_activation': activation['row'], 'tactic_progress': progress['row'], 'retired_tables_absent': missing, 'feedback_history': history}

    if phase == 'before':
        now = datetime.now(timezone.utc).isoformat()
        with psycopg.connect(dsn) as db:
            db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES (%s,%s,'synthetic','2026-01-01')", (prefix, prefix))
            db.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES (%s,%s,'prefix','synthetic','[]','2026-01-01','defense')", (prefix, prefix))
            db.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,source_kind) VALUES(%s,'correct','2026-01-01',0,7,'study')", (prefix,))
            db.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES (%s,'synthetic','synthetic','2026-01-01','rapid',0,'white','draw','synthetic','[]')", (prefix,))
            db.execute("INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,created_at,updated_at) VALUES (%s,%s,1,1,'synthetic',1,'{}','2026-01-01','2026-01-01')", (prefix, prefix))
            db.execute("INSERT INTO threat_training_candidates(id,finding_id,game_id,analysis_version,incident_id,player_ply,evidence_json,source_fingerprint,detector_version,policy_json,created_at,updated_at,card_id) VALUES (%s,%s,%s,1,'incident',1,'{}','synthetic',1,'{}','2026-01-01','2026-01-01',%s)", (prefix, prefix, prefix, prefix))
            db.execute("INSERT INTO tactic_pack_activation(pack_id,active) VALUES (%s,1)", (pack_id,))
            db.execute("INSERT INTO tactic_progress(puzzle_id,deck_id,card_id,clean_pass_at,admitted_at,admission_mode) VALUES (%s,%s,NULL,'2026-01-01','2026-01-02','light')", (puzzle_id, pack_id))
            db.execute("INSERT INTO defense_attempts(attempt_id,candidate_id,card_id,queue_entry_id,exercise_revision,move_uci,grade_json,reviewed_at) VALUES (%s,%s,%s,NULL,7,'e2e4',%s,'2026-01-01')", (prefix, prefix, prefix, '{"status":"incorrect"}'))
            # Bounded historical fixture mirrors schema 64's paused disposition;
            # the source rows retain their original bytes and clocks.
            db.execute("INSERT INTO product_feedback_sources(source_key,version,payload) VALUES(%s,7,%s::jsonb)", (prefix, original_payload))
            db.execute("INSERT INTO study_game_feedback(effect_key,identity_json,source_revision,payload_json,payload_hash,card_id,decision_id,received_at,status,reason,updated_at) VALUES(%s,%s,'source-revision',%s,'original-digest',%s,'decision','2026-01-01T00:00:00Z','held','original-hold','2026-01-02T00:00:00Z')", (effect, json.dumps(identity), delivery_payload, prefix))
            db.execute("INSERT INTO product_feedback_deliveries(operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload) VALUES(%s,%s,%s,'encounter','pending',123,%s::jsonb)", (delivery, prefix, logical_id, delivery_payload))
            db.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json,attempt_count,retry_cycle,cycle_attempt_count,last_error_json) VALUES(%s,'game_evidence.product_accept',%s,'pending',%s,3,2,1,%s)", (operation, digest, original_payload, '{"original":"error"}'))
            db.execute("""INSERT INTO background_tasks(id,kind,deduplication_key,generation,priority,state,phase,payload_json,
                attempt_count,max_attempts,next_attempt_at,lease_token,lease_expires_at,created_at,updated_at)
                VALUES(%s,'study_game_feedback',%s,7,100,'leased','original',%s,2,5,%s,'original-lease',%s,%s,%s)""",
                (game_task, game_task, task_payload, now, '2099-01-01T00:00:00+00:00', now, now))
            for table, key, value in (('study_game_feedback', 'effect_key', effect), ('operation_receipts', 'operation_id', operation),
                                      ('product_feedback_deliveries', 'operation_id', delivery), ('background_tasks', 'id', game_task)):
                db.execute(f"INSERT INTO tempo_retired_work(source_table,source_key,disposition,reason,source_record) SELECT %s,%s,'paused','fixture schema 64 disposition',to_jsonb(r) FROM {table} r WHERE {key}=%s", (table, value, value))
            db.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state) VALUES(%s,'endgames.attempt.create','synthetic-original','pending')", (fg,))
            db.execute("""INSERT INTO background_tasks(id,kind,deduplication_key,generation,priority,state,phase,payload_json,
                attempt_count,max_attempts,next_attempt_at,lease_token,lease_expires_at,created_at,updated_at)
                VALUES(%s,'defensive_threat_scan',%s,7,100,'leased','original','{}',2,5,%s,'original-lease',%s,%s,%s)""",
                (task_id, task_id, now, '2099-01-01T00:00:00+00:00', now, now))
        initial = snapshot()
        # Exercise the actual snapshot against extra linked records whose IDs do
        # not contain the fixture prefix. Remove only these exact test rows.
        negative_effect = 'negative-effect-' + project
        negative_task = 'negative-task-' + project
        def reject_extra(insert, cleanup, label):
            try:
                with psycopg.connect(dsn) as db:
                    insert(db)
                try:
                    snapshot()
                except AssertionError:
                    pass
                else:
                    raise AssertionError(label + ' escaped inventory')
            finally:
                with psycopg.connect(dsn) as db:
                    cleanup(db)
        reject_extra(
            lambda db: db.execute("INSERT INTO study_game_feedback(effect_key,identity_json,source_revision,card_id,decision_id,received_at,status,updated_at) VALUES(%s,%s,'revision',NULL,'decision',%s,'held',%s)", (negative_effect, json.dumps(identity), now, now)),
            lambda db: db.execute('DELETE FROM study_game_feedback WHERE effect_key=%s', (negative_effect,)), 'extra same-game effect')
        reject_extra(
            lambda db: db.execute("INSERT INTO background_tasks(id,kind,deduplication_key,generation,priority,state,payload_json,attempt_count,max_attempts,next_attempt_at,created_at,updated_at) VALUES(%s,'study_game_feedback',%s,1,1,'queued',%s,0,5,%s,%s,%s)", (negative_task, negative_task, task_payload, now, now, now)),
            lambda db: db.execute('DELETE FROM background_tasks WHERE id=%s', (negative_task,)), 'extra effect-linked task')
        assert snapshot() == initial
    else:
        initial = snapshot()

    tasks = [
        ('app.tasks.execute_foreground_command', [operation, 'game_evidence.product_accept', selection], 'foreground'),
        ('app.tasks.execute_background_slice', [{'id': game_task, 'kind': 'study_game_feedback', 'generation': 7, 'lease_token': 'original-lease'}], 'background'),
        ('app.tasks.execute_foreground_command', [fg, 'endgames.attempt.create', {'synthetic': True}], 'foreground'),
        ('app.tasks.execute_background_command', [bg, 'games.analysis.position.report', {'synthetic': True}], 'background'),
        ('app.tasks.execute_background_slice', [{'id': task_id, 'kind': 'defensive_threat_scan', 'generation': 7,
                                                 'lease_token': 'original-lease'}], 'background'),
    ]
    deliveries = []
    for index, (name, args, queue) in enumerate(tasks):
        transport_id = f'{prefix}-{phase}-{index}'
        result = celery_app.send_task(name, args=args, queue=queue, task_id=transport_id)
        value = result.get(timeout=45, interval=0.1, propagate=True)
        assert result.state == 'SUCCESS' and value in (None, False), (name, result.state, value)
        deliveries.append({'task': name, 'transport_id': transport_id, 'state': result.state, 'result': value})

    if phase == 'before':
        queue = durable_tasks.enqueue_task('daily_queue', 'current', {'queue_date': date.today().isoformat()}, priority=10, foreground=True)
        deadline = time.monotonic() + 90
        while True:
            with psycopg.connect(dsn, row_factory=dict_row) as db:
                row = db.execute('SELECT id,generation,state FROM background_tasks WHERE id=%s', (queue['id'],)).fetchone()
            if row and row['state'] == 'complete' and row['generation'] == queue['generation']:
                break
            assert time.monotonic() < deadline, f'retained daily queue did not complete: {row}'
            celery_app.send_task('app.tasks.progress_study_queue', queue='foreground')
            time.sleep(0.25)
    else:
        with psycopg.connect(dsn, row_factory=dict_row) as db:
            row = db.execute("SELECT id,generation,state FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'").fetchone()
        assert row and row['state'] == 'complete', f'retained queue did not stay complete after restart: {row}'
    if phase == 'before':
        from database.tests.check_postgres_queue_attempt_recovery import _exercise_ordinary_study_with_paused_history
        def assert_feedback_history():
            assert snapshot() == initial
        _exercise_ordinary_study_with_paused_history(assert_feedback_history)
        # The review has committed its own refresh request. Record the current
        # generation only after that request, then wait for its publication.
        def read_queue():
            with psycopg.connect(dsn, row_factory=dict_row) as db:
                current = db.execute("SELECT id,generation,state FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'").fetchone()
                projection = db.execute("SELECT requested_refresh_epoch,published_refresh_epoch FROM queue_projections WHERE queue_date=%s", (date.today().isoformat(),)).fetchone()
                return current, projection
        current, projection = read_queue()
        assert current and projection and projection['requested_refresh_epoch'] > 0, (current, projection)
        row = _await_queue_generation(read_queue,
            lambda: celery_app.send_task('app.tasks.progress_study_queue', queue='foreground'),
            time.monotonic, time.sleep, current['id'], current['generation'], projection['requested_refresh_epoch'])
    final = snapshot()
    assert final == initial, 'retired delivery changed the original durable state or recreated retired tables'
    print('STALE_DELIVERY_WITNESS ' + json.dumps({'phase': phase, 'snapshot': final, 'queue': dict(row), 'deliveries': deliveries}, sort_keys=True))


if __name__ == '__main__' and len(sys.argv) == 4 and sys.argv[1] == '--stale-delivery':
    stale_delivery_witness(sys.argv[2], sys.argv[3])
