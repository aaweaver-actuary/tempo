"""Authoritative legacy color, compatible retry, fencing and bounded cleanup."""
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
from psycopg.errors import LockNotAvailable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import postgres_store, activity_commands, review_commands
from app.command_gateway import execute_command
from app.services import postgres_opening_segmentation as segmentation
from app.services.activity_gate import activity_gate, BackgroundAdmissionDeferred
from app.services.durable_tasks import claim_task, enqueue_task_in_transaction


def proof_segmentation_provenance(parent_database_url):
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Segmentation provenance proof requires disposable PostgreSQL')
    import check_postgres_graph_retention as fixtures
    started = time.monotonic()
    with patch.object(fixtures, 'DATABASE_URL', parent_database_url), fixtures.owned_fixture_database() as identity:
        repertoire_id, card_id = identity + '-rep', identity + '-card'
        study_id = identity + '-study'
        now = datetime.now(timezone.utc).isoformat()
        moves = json.dumps(['e2e4', 'e7e5', 'g1f3'])
        with postgres_store.connection() as database:
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,'London','synthetic',?)", (repertoire_id, now))
            for line_id, color in [('white-route', 'white'), ('black-route', 'black')]:
                database.execute('INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,?,?,?,?,?)',
                                 (line_id, repertoire_id, line_id, color, chess.STARTING_FEN, moves, now))
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,trained_color,canonical_route_source) VALUES(?,?,'prefix',?,?,?,NULL,0)",
                             (card_id, repertoire_id, chess.STARTING_FEN, moves, date.today().isoformat()))
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type,state,introduced_at) VALUES(?,?,'prefix',?,'[\"e2e4\"]',?,'tactics','learning',?)",
                             (study_id, repertoire_id, chess.STARTING_FEN, date.today().isoformat(), date.today().isoformat()))
            queue_id = database.execute('INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,0) RETURNING id', (date.today().isoformat(), study_id)).fetchone()[0]
            graph = enqueue_task_in_transaction(database, 'opening_graph_rebuild', repertoire_id, {'repertoire_id': repertoire_id})
            database.execute("UPDATE background_tasks SET state='complete' WHERE id=?", (graph['id'],))
            database.execute('INSERT INTO opening_graph_publications(repertoire_id,generation,published_at) VALUES(?,1,?)', (repertoire_id, now))
            database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color) VALUES(?,1,'white-route',0,?,'position',?,?,'white')",
                             (repertoire_id, card_id, chess.STARTING_FEN, moves))
            queued = segmentation.request_segmentation_in_transaction(database, repertoire_id, 1)
        first = claim_task('opening_segmentation')
        assert first and first['id'] == queued['task_id']
        with activity_gate.foreground():
            try: segmentation.prepare_presentation(first)
            except BackgroundAdmissionDeferred: pass
            else: raise AssertionError('Foreground allowed an advisory source read')
        prepared, release = Event(), Event()
        original_compute = segmentation.presentation_occurrences
        def pause_compute(*arguments):
            assert activity_gate.active_background_sections == 0
            prepared.set()
            assert release.wait(10)
            return original_compute(*arguments)
        with patch.object(segmentation, 'presentation_occurrences', pause_compute), ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(segmentation.execute_segmentation_slice, first)
            try:
                assert prepared.wait(5)
                before = time.monotonic()
                with activity_gate.foreground():
                    result = execute_command(identity + '-review', 'cards.review', {'card_id': study_id, 'review': {
                        'outcome': 'correct', 'queue_entry_id': queue_id, 'attempt_id': identity + '-attempt', 'recorded_at': now}})
                foreground_ms = (time.monotonic() - before) * 1000
                assert result['persisted'] and not future.done()
            finally: release.set()
            assert future.result(timeout=5)
        assert not segmentation.execute_segmentation_slice(first), 'Stale leased delivery wrote a second page'
        with postgres_store.connection(read_only=True) as database:
            occurrences = database.execute('SELECT occurrence_json FROM opening_segmentation_occurrences WHERE run_id=? ORDER BY decision_index', (segmentation.run_identity(first),)).fetchall()
            assert len(occurrences) == 2 and all(json.loads(row[0])['trained_color'] == 'white' for row in occurrences)
            assert database.execute('SELECT trained_color FROM cards WHERE id=?', (card_id,)).fetchone()[0] is None
        # A missing or contradictory source must name the unresolved current card.
        with postgres_store.connection() as database:
            database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color) VALUES(?,1,'black-route',0,?,'position',?,?,'black')",
                             (repertoire_id, card_id, chess.STARTING_FEN, moves))
        for reason in ('conflicting', 'missing'):
            try: segmentation.prepare_presentation(first)
            except ValueError as error:
                assert 'segmentation_provenance_' + reason in str(error)
                assert repertoire_id in str(error) and card_id in str(error) and 'graph 1' in str(error)
            else: raise AssertionError('Unresolved provenance was accepted')
            with postgres_store.connection() as database:
                if reason == 'conflicting':
                    database.execute("DELETE FROM opening_graph_steps WHERE repertoire_id=? AND line_id='black-route'", (repertoire_id,))
                    database.execute("UPDATE opening_graph_steps SET moves_json='[\"e2e4\"]' WHERE repertoire_id=?", (repertoire_id,))
                else: database.execute('UPDATE opening_graph_steps SET moves_json=? WHERE repertoire_id=?', (moves, repertoire_id))
        next_task = claim_task('opening_segmentation')
        assert segmentation.execute_segmentation_slice(next_task)  # Advance to groups without rescanning.
        with postgres_store.connection() as database:
            saved = database.execute('SELECT generation,phase,payload_json FROM background_tasks WHERE id=?', (first['id'],)).fetchone()
            assert saved[1] == 'groups'
            database.execute("UPDATE background_tasks SET state='failed',last_error='interrupted' WHERE id=?", (first['id'],))
        postgres_store.close_pools()
        retried = execute_command(identity + '-retry', 'activity.task.retry', {'task_id': first['id']})
        assert retried['generation'] == first['generation'] and retried['phase'] == 'groups'
        with postgres_store.connection(read_only=True) as database:
            assert database.execute('SELECT payload_json FROM background_tasks WHERE id=?', (first['id'],)).fetchone()[0] == saved[2]
            assert database.execute('SELECT COUNT(*) FROM opening_segmentation_occurrences WHERE run_id=?', (segmentation.run_identity(first),)).fetchone()[0] == 2
        durations = []
        def drain():
            for _ in range(1000):
                task = claim_task('opening_segmentation')
                if task is None: return
                before = time.monotonic()
                segmentation.execute_segmentation_slice(task)
                durations.append(time.monotonic() - before)
            raise AssertionError('Bounded segmentation did not terminate')
        drain()
        with postgres_store.connection() as database:
            assert database.execute('SELECT state FROM opening_segmentation_state WHERE repertoire_id=?', (repertoire_id,)).fetchone()[0] == 'ready'
            database.execute('UPDATE background_tasks SET generation=2 WHERE id=?', (graph['id'],))
            database.execute('UPDATE opening_graph_publications SET generation=2 WHERE repertoire_id=?', (repertoire_id,))
            # Even ready projections must queue the changed graph, then clean up
            # thousands of superseded rows before removing their parent run.
            newer = segmentation.request_segmentation_in_transaction(database, repertoire_id, 2)
            assert newer['task_id'] == first['id']
            database.execute_native("INSERT INTO opening_segmentation_occurrences(run_id,card_id,decision_index,trunk_key,suffix_key,occurrence_json) SELECT %s,'obsolete-'||number,0,'trunk','suffix','{}' FROM generate_series(1,513) number",
                                    (segmentation.run_identity(first),))
            database.execute("INSERT INTO opening_segmentation_runs(id,repertoire_id) VALUES('prior-task-import:3',?)", (repertoire_id,))
            database.execute_native("INSERT INTO opening_segmentation_occurrences(run_id,card_id,decision_index,trunk_key,suffix_key,occurrence_json) SELECT 'prior-task-import:3','imported-'||number,0,'trunk','suffix','{}' FROM generate_series(1,143) number")
        obsolete = claim_task('opening_segmentation')
        with postgres_store.connection() as database:
            database.execute('UPDATE background_tasks SET generation=3 WHERE id=?', (graph['id'],))
        assert not segmentation.execute_segmentation_slice(obsolete), 'Obsolete graph published a current result'
        with postgres_store.connection() as database:
            database.execute('UPDATE opening_graph_publications SET generation=3 WHERE repertoire_id=?', (repertoire_id,))
            database.execute("UPDATE background_tasks SET state='failed',phase='groups' WHERE id=?", (first['id'],))
        restarted = execute_command(identity + '-new-graph', 'activity.task.retry', {'task_id': first['id']})
        assert restarted['generation'] > obsolete['generation'] and restarted['phase'] == 'queued'
        with postgres_store.connection(read_only=True) as database:
            payload = json.loads(database.execute('SELECT payload_json FROM background_tasks WHERE id=?', (first['id'],)).fetchone()[0])
            assert payload['graph_generation'] == 3 and payload['after_card_id'] == ''
        while True:
            task = claim_task('opening_segmentation')
            assert task
            if task['phase'] == 'cleanup': break
            segmentation.execute_segmentation_slice(task)
        # A real row lock yields within the unchanged background lock budget.
        with psycopg.connect(os.environ['TEMPO_DATABASE_WRITE_URL']) as blocker:
            blocker.execute('SELECT id FROM background_tasks WHERE id=%s FOR UPDATE', (task['id'],))
            try: segmentation.execute_segmentation_slice(task)
            except LockNotAvailable: pass
            else: raise AssertionError('Locked segmentation task did not yield')
        assert segmentation.execute_segmentation_slice(task)
        # One cleanup slice cannot remove an entire old generation through cascade.
        with postgres_store.connection(read_only=True) as database:
            remaining = database.execute('SELECT COUNT(*) FROM opening_segmentation_occurrences WHERE run_id=?', (segmentation.run_identity(first),)).fetchone()[0]
            assert remaining == 507, remaining  # 515 original/seeded rows, eight per slice.
        postgres_store.close_pools()
        drain()
        with postgres_store.connection(read_only=True) as database:
            assert database.execute('SELECT COUNT(*) FROM opening_segmentation_runs WHERE repertoire_id=?', (repertoire_id,)).fetchone()[0] == 1
            assert database.execute('SELECT COUNT(*) FROM opening_segmentation_occurrences WHERE run_id=?', (segmentation.run_identity(first),)).fetchone()[0] == 0
            current = database.execute('SELECT state,graph_generation FROM opening_segmentation_state WHERE repertoire_id=?', (repertoire_id,)).fetchone()
            assert current[0] == 'ready' and current[1] == 3
            assert database.execute('SELECT COUNT(*) FROM reviews WHERE card_id=?', (study_id,)).fetchone()[0] == 1
        print('PASS test_postgres_segmentation_current_color_retry_foreground_restart_fencing_and_bounded_cleanup',
              f'maximum slice {max(durations)*1000:.3f} ms; foreground review {foreground_ms:.3f} ms; schema46')
    print('Native proof duration:', round(time.monotonic()-started, 2), 'seconds')


if __name__ == '__main__':
    proof_segmentation_provenance(os.environ['TEMPO_SEGMENTATION_PROOF_URL'])
