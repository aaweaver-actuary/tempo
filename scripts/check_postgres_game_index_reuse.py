"""Real position receipts, scope-only reuse, foreground reviews and recovery."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
from threading import Event
import time
from unittest.mock import patch

import chess
import psycopg
from psycopg.errors import SerializationFailure

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import postgres_store
from app import review_commands  # Registers the real foreground review command.
from app.branch_commands import add_repertoire_branch
from app.command_gateway import execute_command
from app.database import background_connection
from app.services import repertoire_game_refresh as refresh, postgres_game_derivation as indexing
from app.services.activity_gate import activity_gate, BackgroundAdmissionDeferred
from app.services.durable_tasks import claim_task, enqueue_task_in_transaction


def proof_game_index_reuse(parent_database_url):
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Game-index proof requires disposable PostgreSQL')
    import check_postgres_graph_retention as fixtures
    with patch.object(fixtures, 'DATABASE_URL', parent_database_url), fixtures.owned_fixture_database() as identity:
        now = datetime.now(timezone.utc).isoformat()
        game_id, repertoire_id, card_id = identity+'-game', identity+'-rep', identity+'-study'
        database_url = fixtures.DATABASE_URL
        with postgres_store.connection() as database:
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,'Scope','synthetic',?)", (repertoire_id, now))
            database.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES(?,'lichess','proof',?,'rapid',1,'white','1-0',?,'[\"e2e4\",\"e7e5\",\"g1f3\"]')", (game_id, now, chess.STARTING_FEN))
            database.execute("INSERT INTO game_derivation_jobs(game_id,updated_at) VALUES(?,?)", (game_id, now))
            enqueue_task_in_transaction(database, 'game_derivation_positions', game_id, {'game_id':game_id,'derivation_version':1,'cursor':0})
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,introduced_at,content_type) VALUES(?,?,'prefix',?,'[\"e2e4\"]','learning',?,?,'tactics')", (card_id, repertoire_id, chess.STARTING_FEN, date.today().isoformat(), date.today().isoformat()))
            queue_id = database.execute("INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,0) RETURNING id", (date.today().isoformat(), card_id)).fetchone()[0]
        durations = []
        def drain_index():
            for _ in range(8):
                task = claim_task('game_derivation_positions')
                assert task and task['deduplication_key'] == game_id
                started = time.perf_counter()
                assert indexing.execute_game_position_index_slice(task)
                durations.append(time.perf_counter()-started)
                postgres_store.close_pools()
                with postgres_store.connection(read_only=True) as database:
                    status = database.execute('SELECT completed_phases,published_position_version FROM game_derivation_jobs WHERE game_id=?', (game_id,)).fetchone()
                if status[0] > 0:
                    return status[1]
            raise AssertionError('Position index did not publish its small bounded fixture')
        assert drain_index() == 1
        with postgres_store.connection(read_only=True) as database:
            receipt = database.execute('SELECT verified_from_start,position_count,published_at FROM game_position_index_sources WHERE game_id=? AND derivation_version=1', (game_id,)).fetchone()
            assert receipt[0] == 1 and receipt[1] == 4 and receipt[2]
        # A real scope command admits the sweep; it must retain position version 1.
        with postgres_store.connection() as database:
            add_repertoire_branch(database, {'repertoire_id':repertoire_id,'name':'New scope','trained_color':'white','starting_fen':chess.STARTING_FEN,'moves':['d2d4','d7d5','c2c4']})
        scope_task = claim_task('repertoire_game_refresh')
        assert scope_task
        entered, release = Event(), Event()
        original_fingerprint = refresh.source_fingerprint
        def paused_fingerprint(*source):
            assert activity_gate.active_background_sections == 0
            entered.set()
            assert release.wait(10), 'Foreground review did not release the source preparation'
            return original_fingerprint(*source)
        with patch.object(refresh, 'source_fingerprint', paused_fingerprint), ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(refresh.execute_repertoire_game_refresh_slice, scope_task)
            try:
                assert entered.wait(5)
                with psycopg.connect(database_url) as probe:
                    assert probe.execute('SELECT id FROM imported_games WHERE id=%s FOR UPDATE NOWAIT', (game_id,)).fetchone()
                started = time.perf_counter()
                with activity_gate.foreground():
                    review = execute_command(identity+'-review', 'cards.review', {'card_id':card_id,'review':{'outcome':'correct','queue_entry_id':queue_id,'attempt_id':identity+'-attempt','recorded_at':now}})
                review_seconds = time.perf_counter()-started
                assert review['persisted'] and not pending.done()
            finally:
                release.set()
            assert pending.result(timeout=10)
        with postgres_store.connection(read_only=True) as database:
            assert tuple(database.execute('SELECT derivation_version,completed_phases,published_position_version FROM game_derivation_jobs WHERE game_id=?', (game_id,)).fetchone()) == (2,1,1)
            assert database.execute('SELECT COUNT(*) FROM game_position_occurrences_staged WHERE game_id=?', (game_id,)).fetchone()[0] == 4
        assert not refresh.execute_repertoire_game_refresh_slice(scope_task), 'Stale sweep replay advanced the job'
        def request_scope():
            with postgres_store.connection() as database:
                enqueue_task_in_transaction(database, 'repertoire_game_refresh', 'all', {'after_game_id':''})
            task = claim_task('repertoire_game_refresh')
            assert task
            return task
        scope_task = request_scope()
        original_enqueue = refresh.enqueue_compact_postgres_task_in_transaction
        class Interrupted(Exception):
            pass
        def crash_after_compare(database, kind, *args, **options):
            if kind == 'repertoire_game_refresh':
                raise Interrupted()
            return original_enqueue(database, kind, *args, **options)
        try:
            with patch.object(refresh, 'enqueue_compact_postgres_task_in_transaction', crash_after_compare):
                refresh.execute_repertoire_game_refresh_slice(scope_task)
        except Interrupted:
            pass
        else:
            raise AssertionError('Injected scope rollback was not observed')
        postgres_store.close_pools()
        with postgres_store.connection(read_only=True) as database:
            assert database.execute('SELECT derivation_version FROM game_derivation_jobs WHERE game_id=?', (game_id,)).fetchone()[0] == 2
        assert refresh.execute_repertoire_game_refresh_slice(scope_task)
        with activity_gate.foreground():
            try:
                with background_connection():
                    raise AssertionError('Foreground activity admitted a scope slice')
            except BackgroundAdmissionDeferred:
                pass
        # Source mutation requires a full index, and a compute/commit race cannot stamp a receipt.
        with postgres_store.connection() as database:
            database.execute("UPDATE imported_games SET moves_json='[\"d2d4\",\"d7d5\"]' WHERE id=?", (game_id,))
        assert refresh.execute_repertoire_game_refresh_slice(request_scope())
        task = claim_task('game_derivation_positions')
        assert task and task['payload']['derivation_version'] == 4
        original_prepare = indexing.prepare_game_position
        def mutate_after_prepare(*args):
            prepared = original_prepare(*args)
            with postgres_store.connection() as database:
                database.execute("UPDATE imported_games SET moves_json='[\"c2c4\",\"e7e5\"]' WHERE id=?", (game_id,))
            return prepared
        try:
            with patch.object(indexing, 'prepare_game_position', mutate_after_prepare):
                indexing.execute_game_position_index_slice(task)
        except SerializationFailure:
            pass
        else:
            raise AssertionError('Source race published stale positions')
        with postgres_store.connection(read_only=True) as database:
            assert database.execute('SELECT COUNT(*) FROM game_position_index_sources WHERE game_id=? AND derivation_version=4', (game_id,)).fetchone()[0] == 0
        assert indexing.execute_game_position_index_slice(task)
        with postgres_store.connection() as database:
            database.execute("UPDATE imported_games SET moves_json='[\"g1f3\",\"d7d5\"]' WHERE id=?", (game_id,))
        task = claim_task('game_derivation_positions')
        try:
            indexing.execute_game_position_index_slice(task)
        except RuntimeError as error:
            assert 'source changed between slices' in str(error)
        else:
            raise AssertionError('A mixed-source index was accepted')
        assert refresh.execute_repertoire_game_refresh_slice(request_scope())
        assert drain_index() == 5
        with postgres_store.connection(read_only=True) as database:
            assert database.execute('SELECT COUNT(*) FROM game_position_occurrences WHERE game_id=?', (game_id,)).fetchone()[0] == 3
            assert database.execute('SELECT COUNT(*) FROM reviews WHERE card_id=?', (card_id,)).fetchone()[0] == 1
        # A compatible pre-upgrade cursor can finish, but cannot invent proof
        # for its already staged positions. The next scope change reindexes it.
        legacy_id = identity+'-legacy'
        with postgres_store.connection() as database:
            database.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES(?,'lichess','proof',?,'rapid',1,'white','1-0',?,'[\"e2e4\"]')", (legacy_id, now, chess.STARTING_FEN))
            database.execute('INSERT INTO game_derivation_jobs(game_id,updated_at) VALUES(?,?)', (legacy_id, now))
            database.execute('INSERT INTO game_position_occurrences_staged(game_id,derivation_version,ply,fen_key,move_uci) VALUES(?,1,0,?,?)', (legacy_id, ' '.join(chess.STARTING_FEN.split()[:4]), 'e2e4'))
            enqueue_task_in_transaction(database, 'game_derivation_positions', legacy_id, {'game_id':legacy_id,'derivation_version':1,'cursor':1})
        assert indexing.execute_game_position_index_slice(claim_task('game_derivation_positions'))
        with postgres_store.connection() as database:
            assert database.execute('SELECT verified_from_start FROM game_position_index_sources WHERE game_id=?', (legacy_id,)).fetchone()[0] == 0
            enqueue_task_in_transaction(database, 'repertoire_game_refresh', 'all', {'after_game_id':game_id})
        assert refresh.execute_repertoire_game_refresh_slice(claim_task('repertoire_game_refresh'))
        with postgres_store.connection(read_only=True) as database:
            assert tuple(database.execute('SELECT derivation_version,completed_phases FROM game_derivation_jobs WHERE game_id=?', (legacy_id,)).fetchone()) == (2,0)
        assert max(durations) < 0.25, durations
        print(json.dumps({'test':'test_postgres_scope_index_reuse_preserves_foreground_review_restart_replay_and_source_fences',
                          'initial_position_count':4,'reused_position_version':1,'recovered_position_version':5,
                          'max_index_slice_ms':round(max(durations)*1000,3),'foreground_review_ms':round(review_seconds*1000,3)}))


if __name__ == '__main__':
    proof_game_index_reuse(os.environ['TEMPO_GAME_INDEX_PROOF_URL'])
