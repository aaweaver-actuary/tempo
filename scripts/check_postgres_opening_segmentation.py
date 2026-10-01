"""Named AS-14/19/21 checks on the runner-owned disposable PostgreSQL instance."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, date
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import postgres_store
from app.services import postgres_opening_segmentation as worker
from app.services.durable_tasks import claim_task, warm_completion_sql
from app.services.opening_graph import GraphInput, build_graph
from app.services.review_service import apply_scheduling_review
from app.opening_segmentation_api import segmentation_list, segmentation_detail, save_preference
from app.command_gateway import execute_command


def main():
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Segmentation rehearsal requires the disposable PostgreSQL instance')
    os.environ['TEMPO_DATABASE_WRITE_URL'] = 'postgresql://postgres@postgres:5432/tempo'
    os.environ['TEMPO_DATABASE_READ_URL'] = os.environ['TEMPO_DATABASE_WRITE_URL']
    os.environ['TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS'] = '50'
    repertoire_id = 'segmentation-rehearsal-' + uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    starting_fen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
    lines = tuple({'id': f'{repertoire_id}-{index}', 'name': f'Branch {index}', 'start_fen': starting_fen,
                   'moves_json': json.dumps(['e2e4', reply, 'g1f3']), 'trained_color': 'white', 'learner_decision_count': 2}
                  for index, reply in enumerate(['e7e5', 'c7c5', 'e7e6']))
    steps = build_graph(GraphInput(repertoire_id, lines, 2))
    card_ids = tuple(step.card_id for step in steps)
    def snapshot():
        with postgres_store.connection(read_only=True) as database:
            return {table: [dict(row) for row in database.execute_native(statement, (list(card_ids),)).fetchall()]
                    for table, statement in {
                        'cards': 'SELECT * FROM cards WHERE id=ANY(%s) ORDER BY id',
                        'reviews': 'SELECT * FROM reviews WHERE card_id=ANY(%s) ORDER BY id',
                        'queue': 'SELECT * FROM daily_queue WHERE card_id=ANY(%s) ORDER BY id',
                    }.items()}
    owned_card_ids = []
    operation_id = None
    traversals = []
    original_traverse = worker.presentation_occurrences
    prepared_event, reviewed_event = threading.Event(), threading.Event()
    def delayed_traverse(*args):
        traversals.append(args[1]['id'])
        prepared_event.set()
        assert reviewed_event.wait(5), 'Foreground review was blocked by traversal'
        return original_traverse(*args)
    warm_completion_sql()
    try:
        with postgres_store.connection(read_only=False) as database:
            database.execute_native('INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,%s,%s)',
                                    (repertoire_id, 'Segmentation rehearsal', 'test.pgn', now))
            for line, step in zip(lines, steps):
                database.execute_native('INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                    (line['id'], repertoire_id, line['name'], 'white', starting_fen, line['moves_json'], now))
                created_card = database.execute_native("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,trained_color) VALUES(%s,%s,'prefix',%s,%s,%s,'white') ON CONFLICT DO NOTHING RETURNING id",
                    (step.card_id, repertoire_id, starting_fen, line['moves_json'], date.today().isoformat())).fetchone()
                if created_card is None:
                    raise RuntimeError('Segmentation fixture overlaps an existing card; preserve existing data')
                owned_card_ids.append(step.card_id)
                database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s)', (repertoire_id, step.card_id))
                database.execute_native('INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color) VALUES(%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (repertoire_id, line['id'], step.decision_index, step.segment_kind, step.first_decision_index, step.last_decision_index,
                     json.dumps(step.decision_fen_keys), step.card_id, step.parent_card_id, step.decision_fen_key, step.starting_fen, json.dumps(step.moves), step.trained_color))
            database.execute_native("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,1,'ready',%s)", (repertoire_id, now))
            worker.request_segmentation_in_transaction(database, repertoire_id, 1)
        first = claim_task('opening_segmentation')
        assert first and first['payload']['repertoire_id'] == repertoire_id
        worker.presentation_occurrences = delayed_traverse
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(worker.execute_segmentation_slice, first)
            assert prepared_event.wait(5)
            started = time.perf_counter()
            with postgres_store.connection(read_only=False) as database:
                apply_scheduling_review(database, card_ids[0], 'correct', guided=False, source_kind='study',
                    source_ref=f'foreground:{repertoire_id}', light_first_interval_days=7,
                    reviewed_at=datetime.now(timezone.utc), review_day=date.today())
            review_ms = (time.perf_counter() - started) * 1000
            expected_learning_state = snapshot()
            reviewed_event.set()
            assert future.result(timeout=5)
        worker.presentation_occurrences = original_traverse
        # Replay a stale claimed slice, then simulate process restart by reclaiming its durable cursor.
        assert not worker.execute_segmentation_slice(first)
        traversals.clear()
        def count_traverse(*args):
            traversals.append(args[1]['id']); return original_traverse(*args)
        worker.presentation_occurrences = count_traverse
        slices = 1
        for _ in range(200):
            task = claim_task('opening_segmentation')
            if task is None: break
            assert task['payload']['repertoire_id'] == repertoire_id
            worker.execute_segmentation_slice(task)
            slices += 1
            assert snapshot() == expected_learning_state, 'Advisory slice changed learning or queue state'
        else: raise AssertionError('Segmentation failed to terminate')
        assert len(traversals) == 2, 'Uninterrupted cursor re-traversed an already prepared presentation'
        listing = segmentation_list(repertoire_id)
        assert listing['state'] == 'ready' and listing['recommendations']
        recommendation = next(item for item in listing['recommendations'] if item['kind'] == 'shared_trunk')
        assert recommendation['decisions_before'] == 6 and recommendation['decisions_after'] == 4
        detail = segmentation_detail(repertoire_id, recommendation['id'])
        assert len(detail['routes']) == 3 and len(detail['segments']) == 4
        for _ in range(3): assert segmentation_list(repertoire_id) == listing
        assert len(traversals) == 2, 'Cached reads traversed chess'
        operation_id = 'segmentation-preference-' + uuid.uuid4().hex
        payload = {'repertoire_id': repertoire_id, 'recommendation_id': recommendation['id'], 'request': {
            'content_version': listing['content_version'], 'graph_generation': 1,
            'source_fingerprint': detail['source_fingerprint'], 'choice': 'keep_current'}}
        first_result = execute_command(operation_id, 'opening.segmentation.preference', payload)
        assert execute_command(operation_id, 'opening.segmentation.preference', payload) == first_result
        assert not segmentation_list(repertoire_id)['recommendations']
        with postgres_store.connection(read_only=False) as database:
            worker.invalidate_segmentation_in_transaction(database, repertoire_id)
        from fastapi import HTTPException
        try:
            with postgres_store.connection(read_only=False) as database: save_preference(database, payload)
        except HTTPException as error: assert error.status_code == 409
        else: raise AssertionError('Stale recommendation accepted')
        assert snapshot() == expected_learning_state
        # Supersede a claimed generation before its publication; it may never expose ready results.
        with postgres_store.connection(read_only=False) as database: worker.request_segmentation_in_transaction(database, repertoire_id, 1)
        stale = claim_task('opening_segmentation')
        with postgres_store.connection(read_only=False) as database: worker.invalidate_segmentation_in_transaction(database, repertoire_id)
        assert not worker.execute_segmentation_slice(stale)
        assert segmentation_list(repertoire_id)['state'] == 'stale'
        print(json.dumps({'test': 'test_segmentation_analysis_yields_restarts_and_replays_idempotently',
                          'slices': slices, 'foreground_review_during_traversal_ms': round(review_ms, 2),
                          'cached_read_traversals': 0, 'background_transaction_budget_ms': 50}))
    finally:
        reviewed_event.set()
        worker.presentation_occurrences = original_traverse
        with postgres_store.connection(read_only=False) as database:
            if operation_id: database.execute_native("DELETE FROM operation_receipts WHERE operation_id=%s", (operation_id,))
            database.execute_native('DELETE FROM background_tasks WHERE kind=%s AND deduplication_key=%s', ('opening_segmentation', repertoire_id))
            database.execute_native('DELETE FROM cards WHERE id=ANY(%s)', (owned_card_ids,))
            database.execute_native('DELETE FROM repertoires WHERE id=%s', (repertoire_id,))
        postgres_store.close_pools()


if __name__ == '__main__': main()
