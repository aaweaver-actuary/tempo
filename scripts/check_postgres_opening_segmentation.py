"""Named AS-14/19/21 checks on the runner-owned disposable PostgreSQL instance."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone, date
import json
import os
from pathlib import Path
import sys
import threading
import time
import uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import postgres_store
from app.services import postgres_opening_segmentation as worker
from app.services.durable_tasks import claim_task, warm_completion_sql
from app.services.opening_graph import GraphInput, build_graph
from app.services.review_service import apply_scheduling_review
from app.opening_segmentation_api import segmentation_list, segmentation_detail, save_preference
from app.command_gateway import execute_command


def test_issue77_reader_only_deployed_api_evaluates_without_product_writes(repertoire_id, lines, product_snapshot):
    """Maintenance seeds stay separate from the running reader-only API process."""
    from app.services import redis_admission_gate

    endpoint = f'http://api:8000/api/repertoires/{repertoire_id}/prefix-evaluation'
    before = product_snapshot()
    foreground_rejections = 0
    def reader_api_response(request):
        nonlocal foreground_rejections
        request_deadline = time.monotonic() + 10
        # Health probes are foreground requests too. Coordinate with their real
        # leases; never disable admission or treat a database failure as success.
        while time.monotonic() < request_deadline:
            if redis_admission_gate.foreground_present():
                time.sleep(0.01)
                continue
            try:
                with urlopen(request, timeout=request_deadline - time.monotonic()) as response:
                    assert response.status == 200
                    return json.load(response)
            except HTTPError as error:
                error_body = error.read().decode()
                detail = json.loads(error_body).get('detail', {})
                if (error.code != 503 or detail.get('code') != 'evaluation_busy' or
                        detail.get('message') != 'Study work is active. Retry the diagnostic when study is idle.'):
                    raise AssertionError(f'Reader-only API returned {error.code}: {error_body}') from error
                assert error.headers.get('Retry-After') == '1'
                foreground_rejections += 1
        raise AssertionError('Reader-only diagnostic never obtained foreground-idle admission within 10 seconds')

    source = reader_api_response(endpoint + '/source')
    assert source['snapshot_id'] and source['graph_generation'] == 1
    assert {route['id'] for route in source['lines']} == {line['id'] for line in lines}
    request = Request(endpoint + '/evaluate', method='POST', headers={'Content-Type': 'application/json'},
        data=json.dumps({'snapshot_id': source['snapshot_id'], 'selected_line_ids': [lines[0]['id']],
                         'candidate_depths': {lines[0]['id']: 1}}).encode())
    result = reader_api_response(request)
    assert result['snapshot_id'] == source['snapshot_id'] and result['status'] == 'changed'
    assert result['selected']['current']['metrics']['distinct_cards'] == 1
    assert result['selected']['proposed']['metrics']['distinct_cards'] == 2
    assert result['selected']['proposed']['metrics']['learner_decision_occurrences'] == 2
    assert result['whole_repertoire']['current']['metrics']['distinct_cards'] == 3
    assert result['whole_repertoire']['proposed']['metrics']['distinct_cards'] == 4
    assert product_snapshot() == before, 'Deployed reader-only prefix diagnostics wrote product state'
    transition_request = Request(
        f'http://api:8000/api/repertoires/{repertoire_id}/prefix-transition/plan', method='POST',
        headers={'Content-Type': 'application/json'}, data=json.dumps({
            'snapshot_id': source['snapshot_id'], 'selected_line_ids': [lines[0]['id']],
            'candidate_depths': {lines[0]['id']: 1}}).encode())
    transition = reader_api_response(transition_request)
    assert transition['dry_run'] and transition['status'] == 'ready', transition
    assert transition['depth_changes'] == [{'line_id': lines[0]['id'], 'before': 2, 'after': 1}]
    assert any(card['classification'] == 'new_replacement' for card in transition['cards'])
    assert product_snapshot() == before, 'Deployed reader-only transition planner wrote product state'
    print(json.dumps({'test': 'test_issue79_reader_only_deployed_api_plans_without_product_writes',
                      'plan_http_status': 200, 'status': transition['status'], 'product_state_unchanged': True}))
    print(json.dumps({'test': 'test_issue77_reader_only_deployed_api_evaluates_without_product_writes',
                      'source_http_status': 200, 'evaluate_http_status': 200,
                      'current_selected_cards': 1, 'proposed_selected_cards': 2,
                      'current_whole_cards': 3, 'proposed_whole_cards': 4,
                      'product_state_unchanged': True, 'foreground_rejections': foreground_rejections}))


def test_issue77_readonly_snapshot_and_foreground_concurrency(repertoire_id, lines, card_ids):
    """Real primary/query-only HTTP reads, response fencing and idle traversal."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app import prefix_evaluation_api as evaluator_api

    tables = ('cards', 'reviews', 'repertoire_cards', 'repertoire_lines',
              'repertoire_line_training_depths', 'opening_graph_steps', 'opening_graph_publications',
              'prefix_splits', 'opening_card_schedule_seeds', 'daily_queue', 'queue_projections',
              'background_tasks', 'operation_receipts', 'card_revisions', 'review_schedule_snapshots',
              'queue_attempt_origins', 'review_attempt_receipts', 'opening_evidence_attempts',
              'opening_evidence_observations', 'opening_evidence_events', 'study_attempts')
    def product_snapshot():
        with postgres_store.connection(read_only=True) as database:
            return {table: sorted(json.dumps(dict(row), sort_keys=True, default=str) for row in
                    database.execute_native(f'SELECT * FROM {table}').fetchall()) for table in tables}

    test_issue77_reader_only_deployed_api_evaluates_without_product_writes(repertoire_id, lines, product_snapshot)
    client = TestClient(app)  # No lifespan: the existing disposable product already owns startup.
    base_path = f'/api/repertoires/{repertoire_id}/prefix-evaluation'
    before = product_snapshot()
    source_response = client.get(base_path + '/source')
    assert source_response.status_code == 200, source_response.text
    source = source_response.json()
    for selection, depths in (([], None), ([lines[0]['id']], None),
                              ([line['id'] for line in lines[:2]], {line['id']: 1 for line in lines[:2]})):
        response = client.post(base_path + '/evaluate', json={
            'snapshot_id': source['snapshot_id'], 'selected_line_ids': selection, 'candidate_depths': depths})
        assert response.status_code == 200, response.text
    assert product_snapshot() == before, 'Prefix diagnostics wrote product state'
    test_issue79_readonly_planner_foreground_concurrency_and_stale_replay(
        repertoire_id, lines, card_ids, product_snapshot)

    original_connection = postgres_store.connection
    original_calculation = evaluator_api.iter_prefix_evaluation
    reader_pids = []
    prepared, released = threading.Event(), threading.Event()
    @contextmanager
    def observed_connection(**options):
        with original_connection(**options) as database:
            if options.get('repeatable_read'):
                assert options['read_only'] and not options.get('authoritative', False) and options['background']
                settings = database.execute_native(
                    "SELECT pg_backend_pid(),current_setting('transaction_read_only'),"
                    "current_setting('transaction_isolation'),current_setting('transaction_timeout')").fetchone()
                assert tuple(settings)[1:] == ('on', 'repeatable read', '50ms')
                reader_pids.append(settings[0])
            yield database
    def paused_calculation(*args):
        calculation = original_calculation(*args)
        try:
            next(calculation)
            prepared.set()
            assert released.wait(5), 'Foreground review was blocked during evaluation'
            return (yield from calculation)
        finally:
            calculation.close()
    postgres_store.connection = observed_connection
    evaluator_api.iter_prefix_evaluation = paused_calculation
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(client.post, base_path + '/evaluate', json={
                'snapshot_id': source['snapshot_id'], 'selected_line_ids': [lines[0]['id']]})
            assert prepared.wait(5), 'Evaluation did not reach its closed-connection calculation'
            with original_connection(read_only=True) as database:
                readers = database.execute_native('SELECT state,xact_start FROM pg_stat_activity WHERE pid=ANY(%s)', (reader_pids,)).fetchall()
                assert readers and all(row['state'] == 'idle' and row['xact_start'] is None for row in readers)
            started = time.perf_counter()
            with original_connection(read_only=False) as database:
                database.execute_native('SELECT id FROM cards WHERE id=%s FOR UPDATE NOWAIT', (card_ids[0],))
                apply_scheduling_review(database, card_ids[0], 'correct', guided=False, source_kind='study',
                    source_ref=f'prefix-evaluation:{repertoire_id}', light_first_interval_days=7,
                    reviewed_at=datetime.now(timezone.utc), review_day=date.today())
            review_ms = (time.perf_counter() - started) * 1000
            after_review = product_snapshot()
            released.set()
            response = future.result(timeout=5)
            assert response.status_code == 200 and response.json()['snapshot_id'] == source['snapshot_id'], response.text
            assert product_snapshot() == after_review, 'Evaluation changed state after the foreground review'
    finally:
        released.set()
        postgres_store.connection = original_connection
        evaluator_api.iter_prefix_evaluation = original_calculation

    def changed_source(*args):
        result = yield from original_calculation(*args)
        with original_connection(read_only=False) as database:
            database.execute_native('UPDATE repertoire_lines SET name=%s WHERE id=%s', ('Changed during evaluation', lines[0]['id']))
        return result
    evaluator_api.iter_prefix_evaluation = changed_source
    try:
        response = client.post(base_path + '/evaluate', json={
            'snapshot_id': source['snapshot_id'], 'selected_line_ids': [lines[0]['id']]})
        assert response.status_code == 409 and response.json()['detail']['code'] == 'stale_snapshot', response.text
        assert 'whole_repertoire' not in response.json()
    finally:
        evaluator_api.iter_prefix_evaluation = original_calculation
        with original_connection(read_only=False) as database:
            database.execute_native('UPDATE repertoire_lines SET name=%s WHERE id=%s', (lines[0]['name'], lines[0]['id']))
    replay = client.post(base_path + '/evaluate', json={
        'snapshot_id': source['snapshot_id'], 'selected_line_ids': [lines[0]['id']]})
    assert replay.status_code == 200, replay.text
    print(json.dumps({'test': 'test_issue77_readonly_snapshot_and_foreground_concurrency',
                      'foreground_review_during_evaluation_ms': round(review_ms, 2),
                      'source_changed_response': 409, 'background_transaction_budget_ms': 50}))


def test_issue79_readonly_planner_foreground_concurrency_and_stale_replay(repertoire_id, lines, card_ids, product_snapshot):
    """Release readers, permit a real review, and reject the stale plan."""
    from fastapi.testclient import TestClient
    from app.main import app
    from app import prefix_transition_api as planner_api
    from app import prefix_evaluation_api as evaluator_api

    client = TestClient(app)
    source = client.get(f'/api/repertoires/{repertoire_id}/prefix-evaluation/source').json()
    path = f'/api/repertoires/{repertoire_id}/prefix-transition/plan'
    payload = {'snapshot_id': source['snapshot_id'], 'selected_line_ids': [lines[0]['id']],
               'candidate_depths': {lines[0]['id']: 1}}
    before = product_snapshot()
    response = client.post(path, json=payload)
    assert response.status_code == 200 and response.json()['status'] == 'ready', response.text
    assert response.json() == client.post(path, json=payload).json()
    assert product_snapshot() == before
    test_issue79_pending_command_bindings_are_accounted_before_delivery(client, path, payload, card_ids[0], product_snapshot)
    original_connection = postgres_store.connection
    original_calculation = planner_api.iter_transition_plan
    prepared, released = threading.Event(), threading.Event()
    reader_pids = []
    @contextmanager
    def observed_connection(**options):
        with original_connection(**options) as database:
            if options.get('repeatable_read'):
                assert options['read_only'] and options['background'] and not options.get('authoritative')
                settings = database.execute_native(
                    "SELECT pg_backend_pid(),current_setting('transaction_read_only'),"
                    "current_setting('transaction_isolation'),current_setting('transaction_timeout')").fetchone()
                assert tuple(settings)[1:] == ('on', 'repeatable read', '50ms')
                reader_pids.append(settings[0])
            yield database
    def paused_calculation(*args):
        calculation = original_calculation(*args)
        try:
            next(calculation)
            prepared.set()
            assert released.wait(5), 'Foreground review was blocked during transition planning'
            return (yield from calculation)
        finally:
            calculation.close()
    postgres_store.connection = observed_connection
    planner_api.iter_transition_plan = paused_calculation
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(client.post, path, json=payload)
            assert prepared.wait(5)
            with original_connection(read_only=True) as database:
                readers = database.execute_native('SELECT state,xact_start FROM pg_stat_activity WHERE pid=ANY(%s)', (reader_pids,)).fetchall()
                assert readers and all(row['state'] == 'idle' and row['xact_start'] is None for row in readers)
            started = time.perf_counter()
            with original_connection(read_only=False) as database:
                database.execute_native('SELECT id FROM cards WHERE id=%s FOR UPDATE NOWAIT', (card_ids[0],))
                apply_scheduling_review(database, card_ids[0], 'correct', guided=False, source_kind='study',
                    source_ref=f'prefix-transition:{repertoire_id}', light_first_interval_days=7,
                    reviewed_at=datetime.now(timezone.utc), review_day=date.today())
            review_ms = (time.perf_counter() - started) * 1000
            after_review = product_snapshot()
            released.set()
            response = future.result(timeout=5)
            assert response.status_code == 409 and response.json()['detail']['code'] == 'stale_plan', response.text
            assert product_snapshot() == after_review
    finally:
        released.set()
        postgres_store.connection = original_connection
        planner_api.iter_transition_plan = original_calculation
    # Schedule changes keep the structural selection token, but require a fresh plan.
    assert evaluator_api.snapshot_identity(evaluator_api.load_snapshot(repertoire_id, time.monotonic() + 10)) == source['snapshot_id']
    replay = client.post(path, json=payload)
    assert replay.status_code == 200 and replay.json()['status'] == 'ready', replay.text
    assert product_snapshot() == after_review
    print(json.dumps({'test': 'test_issue79_readonly_planner_foreground_concurrency_and_stale_replay',
                      'foreground_review_ms': round(review_ms, 3), 'readers_idle_during_calculation': True,
                      'product_state_unchanged': True, 'stale_plan_rejected': True, 'fresh_retry_succeeded': True}))


def test_issue79_pending_command_bindings_are_accounted_before_delivery(client, path, payload, card_id, product_snapshot):
    """Real retained receipts bind direct reviews, checkpoints, and study answers."""
    pending = [(uuid.uuid4().hex, command, command_payload) for command, command_payload in (
        ('cards.review', {'card_id': card_id}),
        ('opening_evidence.checkpoint', {'checkpoint': {'manifest': {'card_id': card_id}}}),
        ('studies.attempts.submit', {'attempt': {'card_id': card_id}}),
    )]
    try:
        with postgres_store.connection(read_only=False) as database:
            for operation_id, command, command_payload in pending:
                # Blocked receipts cannot execute automatically during the read
                # rehearsal. Only their production payload bindings are needed.
                database.execute_native(
                    "INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json) VALUES(%s,%s,%s,'blocked',%s)",
                    (operation_id, command, operation_id, json.dumps(command_payload)))
        before = product_snapshot()
        response = client.post(path, json=payload)
        assert response.status_code == 200 and response.json()['status'] == 'ready', response.text
        pending_actions = {attempt['object_id']: attempt['action'] for attempt in response.json()['attempts']
                           if attempt['kind'] == 'pending_command'}
        assert all(pending_actions.get(operation_id) == 'retire_with_conflict' for operation_id, *_rest in pending), pending_actions
        assert product_snapshot() == before
    finally:
        with postgres_store.connection(read_only=False) as database:
            database.execute_native('DELETE FROM operation_receipts WHERE operation_id=ANY(%s)', ([item[0] for item in pending],))
    print(json.dumps({'test': 'test_issue79_pending_command_bindings_are_accounted_before_delivery',
                      'direct_review': True, 'nested_checkpoint': True, 'nested_study_answer': True,
                      'product_state_unchanged': True}))


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
    shared_target_repertoire_id = repertoire_id + '-authored-target'
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
            database.execute_native('INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,%s,%s)',
                                    (shared_target_repertoire_id, 'Authored compatible target', 'test.pgn', now))
            target_step = build_graph(GraphInput(shared_target_repertoire_id, ({'id': 'authored-target',
                'start_fen': starting_fen, 'moves_json': json.dumps(['e2e4']), 'trained_color': 'white',
                'learner_decision_count': 1},), 1))[0]
            created_target = database.execute_native(
                "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,trained_color,canonical_route_source) "
                "VALUES(%s,%s,'checkpoint',%s,%s,%s,'white',1) ON CONFLICT DO NOTHING RETURNING id",
                (target_step.card_id, shared_target_repertoire_id, starting_fen, json.dumps(target_step.moves), date.today().isoformat())).fetchone()
            if created_target:
                owned_card_ids.append(target_step.card_id)
                database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,1)',
                                        (shared_target_repertoire_id, target_step.card_id))
            for line, step in zip(lines, steps):
                database.execute_native('INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                    (line['id'], repertoire_id, line['name'], 'white', starting_fen, line['moves_json'], now))
                database.execute_native('INSERT INTO repertoire_line_training_depths(line_id,learner_decision_count) VALUES(%s,%s)',
                    (line['id'], line['learner_decision_count']))
                created_card = database.execute_native("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,trained_color,canonical_route_source) VALUES(%s,%s,'prefix',%s,%s,%s,'white',0) ON CONFLICT DO NOTHING RETURNING id",
                    (step.card_id, repertoire_id, starting_fen, line['moves_json'], date.today().isoformat())).fetchone()
                if created_card is None:
                    raise RuntimeError('Segmentation fixture overlaps an existing card; preserve existing data')
                owned_card_ids.append(step.card_id)
                database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,0)', (repertoire_id, step.card_id))
                database.execute_native('INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color) VALUES(%s,1,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                    (repertoire_id, line['id'], step.decision_index, step.segment_kind, step.first_decision_index, step.last_decision_index,
                     json.dumps(step.decision_fen_keys), step.card_id, step.parent_card_id, step.decision_fen_key, step.starting_fen, json.dumps(step.moves), step.trained_color))
            database.execute_native("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES(%s,1,'ready',%s)", (repertoire_id, now))
            worker.request_segmentation_in_transaction(database, repertoire_id, 1)
        first = claim_task('opening_segmentation')
        assert first and first['payload']['repertoire_id'] == repertoire_id
        test_issue77_readonly_snapshot_and_foreground_concurrency(repertoire_id, lines, card_ids)
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
        # Publish a different run and edit its route names midway through a read.
        # Repeatable read must return the old names with the old publication, never a mixture.
        from app import opening_segmentation_api as preview_api
        from fastapi import HTTPException
        original_read = preview_api.read_connection
        with postgres_store.connection(read_only=True) as database:
            original_run = database.execute_native('SELECT run_id FROM opening_segmentation_state WHERE repertoire_id=%s', (repertoire_id,)).fetchone()[0]
        publication_changed = False
        @contextmanager
        def publishing_read():
            nonlocal publication_changed
            with original_read() as database:
                class PublishingConnection:
                    def execute_native(self, statement, arguments=()):
                        nonlocal publication_changed
                        if 'SELECT DISTINCT line.id' in statement:
                            with postgres_store.connection(read_only=False) as writer:
                                writer.execute_native('UPDATE repertoire_lines SET name=%s WHERE repertoire_id=%s', ('New publication route', repertoire_id))
                                writer.execute_native('UPDATE opening_segmentation_state SET run_id=%s WHERE repertoire_id=%s', ('replacement-publication', repertoire_id))
                            publication_changed = True
                        return database.execute_native(statement, arguments)
                yield PublishingConnection()
        preview_api.read_connection = publishing_read
        try:
            coherent = segmentation_detail(repertoire_id, recommendation['id'], snapshot_id=detail['snapshot_id'])
            assert publication_changed and coherent == detail, 'Response mixed publication metadata and newer route names'
        finally:
            preview_api.read_connection = original_read
            with postgres_store.connection(read_only=False) as database:
                database.execute_native('UPDATE opening_segmentation_state SET run_id=%s WHERE repertoire_id=%s', (original_run, repertoire_id))
                for line in lines:
                    database.execute_native('UPDATE repertoire_lines SET name=%s WHERE id=%s', (line['name'], line['id']))
        for cursor in ('after_segment', 'after_route'):
            assert segmentation_detail(repertoire_id, recommendation['id'], snapshot_id=detail['snapshot_id'], **{cursor: 'last'})['snapshot_id'] == detail['snapshot_id']
            with postgres_store.connection(read_only=False) as database:
                database.execute_native('UPDATE opening_segmentation_state SET run_id=%s WHERE repertoire_id=%s', ('replacement-publication', repertoire_id))
            try:
                segmentation_detail(repertoire_id, recommendation['id'], snapshot_id=detail['snapshot_id'], **{cursor: 'last'})
            except HTTPException as error: assert error.status_code == 409
            else: raise AssertionError('A new publication accepted old pagination')
            finally:
                with postgres_store.connection(read_only=False) as database:
                    database.execute_native('UPDATE opening_segmentation_state SET run_id=%s WHERE repertoire_id=%s', (original_run, repertoire_id))
        print(json.dumps({'test': 'test_preview_response_remains_consistent_during_concurrent_postgres_publication', 'pagination_conflicts': 2}))
        operation_id = 'segmentation-preference-' + uuid.uuid4().hex
        payload = {'repertoire_id': repertoire_id, 'recommendation_id': recommendation['id'], 'request': {
            'content_version': listing['content_version'], 'graph_generation': 1,
            'source_fingerprint': detail['source_fingerprint'], 'snapshot_id': detail['snapshot_id'], 'choice': 'keep_current'}}
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
            database.execute_native('DELETE FROM repertoires WHERE id=%s', (shared_target_repertoire_id,))
        postgres_store.close_pools()


if __name__ == '__main__': main()
