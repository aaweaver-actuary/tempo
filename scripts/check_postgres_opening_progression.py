"""Real review/queue transactions in an owned freshly migrated disposable database."""
from contextlib import ExitStack
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
from threading import Event, Thread
import uuid

import chess
import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import main, postgres_store, review_commands
from app.models import ReviewRequest
from app.services.activity_gate import activity_gate, BackgroundAdmissionDeferred
from app.services.durable_tasks import claim_task
from app.services.postgres_queue_refresh import execute_postgres_queue_refresh_slice


def seed(database, identifier, today):
    root_id, child_id, grandchild_id = [identifier + suffix for suffix in ('-root', '-child', '-grandchild')]
    database.execute("INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES(?,'Exposure progression','synthetic',?,2)", (identifier, today))
    database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,'Main line','white',?,'[\"e2e4\",\"e7e5\",\"g1f3\",\"b8c6\",\"f1b5\"]',?)", (identifier, identifier, chess.STARTING_FEN, today))
    database.execute('INSERT INTO opening_graph_publications(repertoire_id,generation,published_at) VALUES(?,1,?)', (identifier, today))
    board = chess.Board()
    for index, (card_id, moves) in enumerate(zip((root_id, child_id, grandchild_id), (['e2e4'], ['e7e5','g1f3'], ['b8c6','f1b5']))):
        starting_fen = board.fen()
        for move in moves:
            decision_fen = ' '.join(board.fen().split()[:4])
            board.push_uci(move)
        parent_id = None if index == 0 else (root_id, child_id)[index - 1]
        database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at,trained_color,canonical_route_source) VALUES(?,?,?,?,?,?,?,?,'white',0)",
                         (card_id, identifier, 'prefix' if index == 0 else 'response', starting_fen, json.dumps(moves), 'learning' if index == 0 else 'locked', today, today if index == 0 else None))
        database.execute('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,0)', (identifier, card_id))
        database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,decision_fen_keys_json) VALUES(?,1,?,?,?,?,?,?,?,'white',?,?)",
                         (identifier, identifier, index, card_id, parent_id, decision_fen, starting_fen, json.dumps(moves), 'prefix' if index == 0 else 'decision', json.dumps([decision_fen])))
    root_entry = database.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind,admission_repertoire_id) VALUES(?,?,0,'new',?) RETURNING id", (today, root_id, identifier)).fetchone()[0]
    return root_id, child_id, grandchild_id, root_entry


def drain_queue_refresh(*, restart=False):
    for slice_index in range(200):
        task = claim_task('daily_queue')
        assert task is not None, 'Expected a runnable bounded queue slice'
        if restart and slice_index == 0:
            with postgres_store.connection(read_only=True) as database:
                before_denial = dict(database.execute('SELECT * FROM background_tasks WHERE id=?', (task['id'],)).fetchone())
            entered = Event()
            completed = Event()
            failures = []
            def run():
                entered.set()
                try:
                    execute_postgres_queue_refresh_slice(task)
                except BaseException as error:
                    failures.append(error)
                finally:
                    completed.set()
            with activity_gate.foreground():
                worker = Thread(target=run)
                worker.start()
                assert entered.wait(2)
                assert completed.wait(2), 'Denied background slice occupied the worker'
                assert len(failures) == 1 and isinstance(failures[0], BackgroundAdmissionDeferred), failures
                with postgres_store.connection(read_only=True) as database:
                    assert database.execute('SELECT 1').fetchone()[0] == 1
                    assert dict(database.execute('SELECT * FROM background_tasks WHERE id=?', (task['id'],)).fetchone()) == before_denial
            worker.join(5)
            assert not worker.is_alive()
            execute_postgres_queue_refresh_slice(task)
        else:
            execute_postgres_queue_refresh_slice(task)
        if restart and slice_index == 1:
            crashed_task = claim_task('daily_queue')
            assert crashed_task is not None
            with postgres_store.connection() as database:
                database.execute("UPDATE background_tasks SET lease_expires_at='2000-01-01' WHERE id=?", (crashed_task['id'],))
            postgres_store.close_pools()
            resumed_task = claim_task('daily_queue')
            assert resumed_task['lease_token'] != crashed_task['lease_token']
            assert execute_postgres_queue_refresh_slice(crashed_task) is False
            execute_postgres_queue_refresh_slice(resumed_task)
        with postgres_store.connection(read_only=True) as database:
            saved = database.execute("SELECT state,payload_json FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'").fetchone()
            assert 'preserve_through_entry_id' in json.loads(saved['payload_json'])
            if saved['state'] == 'complete':
                return
    raise AssertionError('Queue refresh failed to reach a durable complete projection')


def test_postgres_opening_practice_progression_is_atomic_restartable_and_quota_bound(database_url):
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Opening progression proof requires an explicitly disposable stack')
    original_write_url = os.environ.get('TEMPO_DATABASE_WRITE_URL')
    original_read_url = os.environ.get('TEMPO_DATABASE_READ_URL')
    fixture_database = 'opening_progression_' + uuid.uuid4().hex
    connection_info = psycopg.conninfo.conninfo_to_dict(database_url)
    postgres_store.close_pools()
    admin_info = {**connection_info, 'dbname': 'postgres'}
    fixture_scope = ExitStack()
    try:
        with psycopg.connect(**admin_info, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(fixture_database)))
        fixture_url = psycopg.conninfo.make_conninfo(**{**connection_info, 'dbname': fixture_database})
        from apply_postgres_migrations import apply_migrations
        apply_migrations(fixture_url)
        os.environ['TEMPO_DATABASE_WRITE_URL'] = fixture_url
        os.environ['TEMPO_DATABASE_READ_URL'] = fixture_url
        from check_postgres_graph_retention import owned_admission_scope
        fixture_scope.enter_context(owned_admission_scope(fixture_database))
        today = date.today().isoformat()
        with postgres_store.connection() as database:
            database.execute('INSERT INTO settings(id,tactics_new_per_day,study_new_per_day) VALUES(1,0,0)')
        for outcome, guided in [('correct', False), ('again', False), ('correct', True)]:
            identifier = 'practice-' + uuid.uuid4().hex
            with postgres_store.connection() as database:
                root_id, child_id, grandchild_id, root_entry = seed(database, identifier, today)
                main._unlock_eligible_opening_cards(database, today, after_card_id='', batch_size=8)
                assert database.execute('SELECT state FROM cards WHERE id=?', (child_id,)).fetchone()[0] == 'locked'
            request = ReviewRequest(queue_entry_id=root_entry, outcome=outcome, guided=guided,
                                    attempt_id=identifier+'-attempt', expected_revision=1)
            payload = {'card_id': root_id, 'review': request.model_dump(mode='json')}
            try:
                with postgres_store.connection() as database:
                    review_commands.submit_review(database, payload)
                    raise ValueError('Simulated crash before review commit')
            except ValueError as error:
                assert str(error) == 'Simulated crash before review commit'
            with postgres_store.connection(read_only=True) as database:
                assert database.execute('SELECT COUNT(*) FROM reviews WHERE card_id=?', (root_id,)).fetchone()[0] == 0
                assert database.execute('SELECT COUNT(*) FROM review_attempt_receipts WHERE attempt_id=?', (request.attempt_id,)).fetchone()[0] == 0
            with postgres_store.connection() as database:
                result = review_commands.submit_review(database, payload)
                assert result['state'] == 'learning'
                generation = database.execute("SELECT generation FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'").fetchone()[0]
                preserved_ids = [row[0] for row in database.execute("SELECT id FROM daily_queue WHERE queue_date=? AND status='queued' ORDER BY position,id", (today,))]
            postgres_store.close_pools()
            with postgres_store.connection() as database:
                assert review_commands.submit_review(database, payload) == result
                assert database.execute("SELECT generation FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'").fetchone()[0] == generation
            drain_queue_refresh(restart=True)
            with postgres_store.connection() as database:
                assert database.execute('SELECT state FROM cards WHERE id=?', (child_id,)).fetchone()[0] == 'learning'
                assert database.execute('SELECT state FROM cards WHERE id=?', (grandchild_id,)).fetchone()[0] == 'locked'
                assert database.execute('SELECT state FROM cards WHERE id=?', (root_id,)).fetchone()[0] == 'learning'
                queued_ids = [row[0] for row in database.execute("SELECT id FROM daily_queue WHERE queue_date=? AND status='queued' ORDER BY position,id", (today,))]
                assert [entry for entry in queued_ids if entry in preserved_ids] == preserved_ids
                assert queued_ids[:len(preserved_ids)] == preserved_ids
                child_entry = database.execute('SELECT id FROM daily_queue WHERE card_id=? AND cycle=0', (child_id,)).fetchone()[0]
                assert database.execute('SELECT COUNT(*) FROM daily_queue WHERE card_id=? AND cycle=0', (child_id,)).fetchone()[0] == 1
                child_request = ReviewRequest(queue_entry_id=child_entry, outcome='again', attempt_id=identifier+'-child-attempt', expected_revision=1)
                review_commands.submit_review(database, {'card_id': child_id, 'review': child_request.model_dump(mode='json')})
            drain_queue_refresh()
            with postgres_store.connection() as database:
                assert database.execute('SELECT state FROM cards WHERE id=?', (grandchild_id,)).fetchone()[0] == 'new'
                assert database.execute('SELECT COUNT(*) FROM daily_queue WHERE card_id=?', (grandchild_id,)).fetchone()[0] == 0
                assert database.execute('SELECT COUNT(*) FROM cards WHERE repertoire_id=? AND introduced_at=?', (identifier, today)).fetchone()[0] == 2
                database.execute('DELETE FROM cards WHERE repertoire_id=?', (identifier,))
                database.execute('DELETE FROM repertoires WHERE id=?', (identifier,))
        print('PASS test_postgres_opening_practice_progression_is_atomic_restartable_and_quota_bound')
    finally:
        fixture_scope.close()
        postgres_store.close_pools()
        for key, value in [('TEMPO_DATABASE_WRITE_URL', original_write_url), ('TEMPO_DATABASE_READ_URL', original_read_url)]:
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        with psycopg.connect(**admin_info, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE IF EXISTS {}').format(sql.Identifier(fixture_database)))


if __name__ == '__main__':
    test_postgres_opening_practice_progression_is_atomic_restartable_and_quota_bound('postgresql://postgres@postgres:5432/tempo')
