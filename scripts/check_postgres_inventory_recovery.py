"""PR #117 lock ordering and manual recovery on runner-owned PostgreSQL."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import threading
import time
from unittest.mock import patch

import chess
import psycopg

from app import activity_commands, branch_commands, pgn_import_commands, postgres_store, tasks
from app.services import durable_tasks, postgres_opening_graph as graph
from app.services import postgres_position_inventory as inventory
from app.services.pgn import ParsedLine


class ObservedConnection(postgres_store.PostgresConnection):
    """Observe real statements without replacing SQL or PostgreSQL row locks."""

    def __init__(self, raw, before=lambda *_: None, after=lambda *_: None):
        super().__init__(raw)
        self.before = before
        self.after = after

    def execute_native(self, statement, parameters=()):
        self.before(statement, parameters)
        result = super().execute_native(statement, parameters)
        self.after(statement, parameters)
        return result

    def execute(self, statement, parameters=()):
        self.before(statement, parameters)
        result = super().execute(statement, parameters)
        self.after(statement, parameters)
        return result


def wait_event(event, description):
    assert event.wait(5), f'Timed out waiting for {description}'


@contextmanager
def concurrent_transactions(dsn):
    """Independent raw sessions expose lock cycles without timeout masking.

    Only these test-controlled transactions pause at synchronization points.
    Eventual production worker slices retain the ordinary transaction budgets.
    """
    workers = []
    release = threading.Event()
    outcomes = {}
    pids = {}
    ready = {}

    def launch(name, operation, *, before=lambda *_: None, after=lambda *_: None):
        ready[name] = threading.Event()

        def execute():
            try:
                with psycopg.connect(dsn, row_factory=postgres_store.tempo_row_factory) as raw:
                    pids[name] = raw.info.backend_pid
                    ready[name].set()
                    database = ObservedConnection(raw, before, after)
                    outcomes[name] = operation(database)
                    database.flush_background_metrics()
            except Exception as error:
                outcomes[name] = error

        worker = threading.Thread(target=execute, name=f'pr117-{name}')
        workers.append(worker)
        worker.start()
        wait_event(ready[name], f'{name} PostgreSQL session')

    def blocked(waiter, blocker):
        deadline = time.monotonic() + 5
        with psycopg.connect(dsn, autocommit=True) as observer:
            while time.monotonic() < deadline:
                assert not isinstance(outcomes.get(waiter), Exception), outcomes
                blocking_pids = observer.execute(
                    'SELECT pg_blocking_pids(%s)', (pids[waiter],)).fetchone()[0]
                if pids[blocker] in blocking_pids:
                    return
                release.wait(0.001)
            details = observer.execute(
                'SELECT pid,wait_event_type,wait_event,query FROM pg_stat_activity WHERE pid=ANY(%s)',
                (list(pids.values()),)).fetchall()
        raise AssertionError(f'{waiter} never waited for {blocker}: {details}; {outcomes}')

    try:
        yield launch, blocked, release, outcomes
    finally:
        release.set()
        for worker in workers:
            worker.join(7)
        assert all(not worker.is_alive() for worker in workers), 'Concurrency worker did not finish'
        assert not any(isinstance(result, Exception) for result in outcomes.values()), outcomes


def prepared_publication(repertoire_id):
    from scripts.check_postgres_position_inventory import claim, publish_graph, seed, run_to_completion
    with postgres_store.connection() as database:
        seed(database.raw, repertoire_id, [
            (repertoire_id+'-line', ['e2e4', 'e7e5', 'g1f3'], 'white', chess.STARTING_FEN)])
        database.execute_native('UPDATE repertoires SET source_name=%s WHERE id=%s',
                                (repertoire_id+'.pgn', repertoire_id))
        publish_graph(database, repertoire_id)
    run_to_completion(repertoire_id)
    with postgres_store.connection() as database:
        requested = graph.request_graph_rebuild_in_transaction(
            database, repertoire_id, datetime.now(timezone.utc).date().isoformat())
        publish_graph(database, repertoire_id, requested['generation'], publish=False)
        database.execute_native("UPDATE background_tasks SET phase='publish' WHERE id=%s", (requested['id'],))
        task = claim(database, repertoire_id, kind='opening_graph_rebuild')
        old_inventory = inventory.inventory_progress(database, repertoire_id)['publication_id']
    return task, old_inventory


def finish_graph_and_inventory(repertoire_id):
    from scripts.check_postgres_position_inventory import claim, run_to_completion
    # Import the normal worker's handler modules before starting short database
    # slices, as the deployed worker does; do not lazily import them in a slice.
    assert {'opening_graph_rebuild', inventory.TASK_KIND, inventory.RECONCILE_KIND} <= set(tasks._SUPPORTED_BACKGROUND_KINDS)
    for _ in range(100):
        with postgres_store.connection() as database:
            task = claim(database, repertoire_id, kind='opening_graph_rebuild')
        if task is None:
            break
        graph.execute_postgres_opening_graph_slice(task)
    else:
        raise AssertionError('Graph failed to finish in 100 bounded slices')
    run_to_completion(repertoire_id)
    with postgres_store.connection(read_only=True) as database:
        progress = inventory.inventory_progress(database, repertoire_id)
        assert progress['publication_id'] and not progress['stale'], progress
        identity = inventory.input_identity(database, repertoire_id)
        published = database.execute_native('SELECT * FROM inventory_generations WHERE id=%s',
                                            (progress['publication_id'],)).fetchone()
        assert all(published[field] == value for field, value in identity.items())
        assert database.execute_native(
            'SELECT COUNT(*) FROM inventory_generations WHERE repertoire_id=%s', (repertoire_id,)).fetchone()[0] == 1
        assert database.execute_native(
            'SELECT COUNT(*) FROM inventory_generation_routes WHERE generation_id=%s',
            (progress['publication_id'],)).fetchone()[0] == database.execute_native(
                'SELECT COUNT(*) FROM repertoire_lines WHERE repertoire_id=%s', (repertoire_id,)).fetchone()[0]


def test_pr117_foreground_mutation_and_graph_publication_have_no_lock_cycle(dsn):
    for mutation in ('import', 'branch'):
        for schedule in ('foreground_first', 'background_first'):
            for repetition in range(10):
                repertoire_id = f'pr117-{mutation}-{schedule}-{repetition}'
                task, old_inventory = prepared_publication(repertoire_id)
                with postgres_store.connection() as database:
                    reviewed_card = database.execute_native(
                        'SELECT card.id FROM cards card JOIN repertoire_cards link ON link.card_id=card.id '
                        'WHERE link.repertoire_id=%s ORDER BY card.id LIMIT 1', (repertoire_id,)).fetchone()[0]
                    database.execute_native("UPDATE cards SET state='mature',introduced_at=%s WHERE id=%s",
                                            ('2026-10-08T00:00:00+00:00', reviewed_card))
                    review_id = database.execute_native(
                        "INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) "
                        "VALUES(%s,'correct','2026-10-08',1,2) RETURNING id", (reviewed_card,)).fetchone()[0]
                    card_before = dict(database.execute_native('SELECT * FROM cards WHERE id=%s', (reviewed_card,)).fetchone())
                    review_before = dict(database.execute_native('SELECT * FROM reviews WHERE id=%s', (review_id,)).fetchone())
                moves = ['e2e4', 'c7c5', 'g1f3']
                import_payload = pgn_import_commands.prepare_import_payload(
                    repertoire_id+'.pgn', 'white', 6, 1, [ParsedLine(chess.STARTING_FEN, moves, [])])
                reached = threading.Event()

                def mutate(database):
                    if mutation == 'import':
                        result = pgn_import_commands.admit_pgn_import(database, import_payload)
                        assert result['repertoire_id'] == repertoire_id
                        return result
                    return branch_commands.add_repertoire_branch(database, {
                        'repertoire_id': repertoire_id, 'starting_fen': chess.STARTING_FEN,
                        'moves': moves, 'trained_color': 'white'})

                with concurrent_transactions(dsn) as (launch, blocked, release, outcomes):
                    if schedule == 'foreground_first':
                        def before_foreground(statement, _parameters):
                            if statement.startswith('SELECT id FROM background_tasks') and 'FOR UPDATE' in statement:
                                reached.set()
                                wait_event(release, 'foreground graph request release')
                        launch('foreground', mutate, before=before_foreground)
                        wait_event(reached, 'foreground repertoire mutation')
                        launch('background', lambda database: graph.publish_graph_in_transaction(database, task))
                        blocked('background', 'foreground')
                    else:
                        def after_background(statement, _parameters):
                            if statement.startswith('SELECT generation,lease_token,state'):
                                reached.set()
                                wait_event(release, 'background publication release')
                        launch('background', lambda database: graph.publish_graph_in_transaction(database, task),
                               after=after_background)
                        wait_event(reached, 'background graph task lock')
                        launch('foreground', mutate)
                        blocked('foreground', 'background')
                    release.set()
                assert outcomes['background'] is (schedule == 'background_first'), outcomes
                with postgres_store.connection(read_only=True) as database:
                    assert inventory.inventory_progress(database, repertoire_id)['publication_id'] == old_inventory
                    current_task = database.execute_native(
                        'SELECT * FROM background_tasks WHERE id=%s', (task['id'],)).fetchone()
                    assert current_task['generation'] > task['generation']
                    assert current_task['state'] == 'queued'
                    publication = database.execute_native(
                        'SELECT generation FROM opening_graph_publications WHERE repertoire_id=%s',
                        (repertoire_id,)).fetchone()[0]
                    assert publication == (task['generation'] if schedule == 'background_first' else 1)
                    if schedule == 'background_first':
                        queued_inventory = database.execute_native(
                            'SELECT payload_json,state FROM background_tasks WHERE kind=%s AND deduplication_key=%s',
                            (inventory.TASK_KIND, repertoire_id)).fetchone()
                        assert queued_inventory['state'] == 'queued'
                        generation_id = json.loads(queued_inventory['payload_json'])['inventory_id']
                        assert database.execute_native(
                            'SELECT graph_generation FROM inventory_generations WHERE id=%s',
                            (generation_id,)).fetchone()[0] == task['generation']
                finish_graph_and_inventory(repertoire_id)
                with postgres_store.connection(read_only=True) as database:
                    assert dict(database.execute_native('SELECT * FROM cards WHERE id=%s', (reviewed_card,)).fetchone()) == card_before
                    assert dict(database.execute_native('SELECT * FROM reviews WHERE id=%s', (review_id,)).fetchone()) == review_before
    print('PASS test_pr117_foreground_mutation_and_graph_publication_have_no_lock_cycle: 40 schedules', flush=True)


def test_pr117_graph_inventory_handoff_rolls_back_atomically(dsn):
    repertoire_id = 'pr117-atomic'
    task, old_inventory = prepared_publication(repertoire_id)
    with postgres_store.connection(read_only=True) as database:
        task_before = dict(database.execute_native('SELECT * FROM background_tasks WHERE id=%s', (task['id'],)).fetchone())
        generations_before = [dict(row) for row in database.execute_native(
            'SELECT * FROM inventory_generations WHERE repertoire_id=%s ORDER BY id', (repertoire_id,)).fetchall()]
        inventory_task_before = dict(database.execute_native(
            'SELECT * FROM background_tasks WHERE kind=%s AND deduplication_key=%s',
            (inventory.TASK_KIND, repertoire_id)).fetchone())
    original_request = inventory.request_inventory_in_transaction

    def interrupt(database, requested_repertoire):
        assert database.execute_native('SELECT generation FROM opening_graph_publications WHERE repertoire_id=%s',
                                        (repertoire_id,)).fetchone()[0] == task['generation']
        original_request(database, requested_repertoire)
        raise RuntimeError('pr117 rollback after inventory enqueue')

    try:
        with patch.object(inventory, 'request_inventory_in_transaction', interrupt):
            graph.execute_graph_publish_slice(task)
    except RuntimeError as error:
        assert str(error) == 'pr117 rollback after inventory enqueue'
    else:
        raise AssertionError('Publication rollback injection did not execute')
    with postgres_store.connection(read_only=True) as database:
        assert database.execute_native('SELECT generation FROM opening_graph_publications WHERE repertoire_id=%s',
                                        (repertoire_id,)).fetchone()[0] == 1
        assert inventory.inventory_progress(database, repertoire_id)['publication_id'] == old_inventory
        assert dict(database.execute_native('SELECT * FROM background_tasks WHERE id=%s', (task['id'],)).fetchone()) == task_before
        assert [dict(row) for row in database.execute_native(
            'SELECT * FROM inventory_generations WHERE repertoire_id=%s ORDER BY id', (repertoire_id,)).fetchall()] == generations_before
        assert dict(database.execute_native('SELECT * FROM background_tasks WHERE id=%s',
                                            (inventory_task_before['id'],)).fetchone()) == inventory_task_before
    assert graph.execute_graph_publish_slice(task)
    assert graph.execute_graph_publish_slice(task) is False
    finish_graph_and_inventory(repertoire_id)
    print('PASS test_pr117_graph_inventory_handoff_rolls_back_atomically', flush=True)


def partial_inventory(repertoire_id, *, stage_responses=False):
    from scripts.check_postgres_position_inventory import seed, publish_graph
    with postgres_store.connection() as database:
        seed(database.raw, repertoire_id, [
            (repertoire_id+'-line', ['e2e4', 'e7e5', 'g1f3'], 'white', chess.STARTING_FEN)])
        publish_graph(database, repertoire_id)
    task = durable_tasks.claim_task(kind=inventory.TASK_KIND)
    assert task and task['payload']['repertoire_id'] == repertoire_id
    assert inventory.execute_inventory_slice(task)
    generation_id = task['payload']['inventory_id']
    if stage_responses:
        for _ in range(20):
            task = durable_tasks.claim_task(kind=inventory.TASK_KIND)
            assert task and task['payload']['repertoire_id'] == repertoire_id
            assert inventory.execute_inventory_slice(task)
            with postgres_store.connection(read_only=True) as database:
                if database.execute_native('SELECT COUNT(*) FROM inventory_responses WHERE generation_id=%s',
                                            (generation_id,)).fetchone()[0]:
                    break
        else:
            raise AssertionError('Fixture did not stage any learner-response memberships')
    return generation_id


def test_pr117_inventory_failure_and_refresh_have_no_lock_cycle(dsn):
    repertoire_id = 'pr117-failure-refresh'
    failed_inventory = partial_inventory(repertoire_id)
    with postgres_store.connection() as database:
        database.execute_native('UPDATE background_tasks SET max_attempts=1 WHERE kind=%s AND deduplication_key=%s',
                                (inventory.TASK_KIND, repertoire_id))
    task = durable_tasks.claim_task(kind=inventory.TASK_KIND)
    reached = threading.Event()
    with concurrent_transactions(dsn) as (launch, blocked, release, outcomes):
        def after_failure(statement, _parameters):
            if 'UPDATE background_tasks SET state=' in statement:
                reached.set()
                wait_event(release, 'terminal failure release')

        def fail(database):
            with patch.object(durable_tasks, 'submit_background_write', lambda operation, **_: operation(database)):
                result = durable_tasks.fail_task(task['id'], task['generation'], task['lease_token'], RuntimeError('injected exhaustion'))
                assert result['state'] == 'failed', result
                return result

        def refresh(database):
            database.execute_native('UPDATE repertoire_lines SET moves_json=%s WHERE repertoire_id=%s',
                                    (json.dumps(['d2d4', 'd7d5', 'c2c4']), repertoire_id))
            return inventory.request_inventory_in_transaction(database, repertoire_id)

        launch('failure', fail, after=after_failure)
        wait_event(reached, 'failed task-row update')
        launch('refresh', refresh)
        blocked('refresh', 'failure')
        release.set()
    assert outcomes['failure']['state'] == 'failed'
    assert outcomes['refresh'] != failed_inventory
    from scripts.check_postgres_position_inventory import run_to_completion
    run_to_completion(repertoire_id)
    with postgres_store.connection(read_only=True) as database:
        assert inventory.inventory_progress(database, repertoire_id)['publication_id'] == outcomes['refresh']
        assert database.execute_native('SELECT COUNT(*) FROM inventory_generations WHERE id=%s',
                                        (failed_inventory,)).fetchone()[0] == 0
    print('PASS test_pr117_inventory_failure_and_refresh_have_no_lock_cycle', flush=True)


def test_pr117_inventory_manual_retry_publishes_replacement_without_stale_memberships(dsn):
    repertoire_id = 'pr117-manual-retry'
    failed_inventory = partial_inventory(repertoire_id, stage_responses=True)
    clock = datetime.now(timezone.utc)
    with patch.object(durable_tasks, '_now', lambda: clock):
        for attempt in range(1, 6):
            task = durable_tasks.claim_task(kind=inventory.TASK_KIND)
            assert task and task['attempt_count'] == attempt, task
            result = durable_tasks.fail_task(task['id'], task['generation'], task['lease_token'], RuntimeError('injected inventory failure'))
            assert result['state'] == ('failed' if attempt == 5 else 'retrying'), result
            clock += timedelta(seconds=65)
    with postgres_store.connection() as database:
        assert database.execute_native('SELECT state FROM inventory_generations WHERE id=%s',
                                        (failed_inventory,)).fetchone()[0] == 'failed'
        assert database.execute_native('SELECT COUNT(*) FROM inventory_generation_routes WHERE generation_id=%s',
                                        (failed_inventory,)).fetchone()[0] == 1
        assert database.execute_native('SELECT COUNT(*) FROM inventory_responses WHERE generation_id=%s',
                                        (failed_inventory,)).fetchone()[0] > 0
        retried = activity_commands.retry_failed_task(database, {'task_id': task['id']})
        assert retried['state'] == 'queued' and retried['attempts'] == 0
    retried_task = durable_tasks.claim_task(kind=inventory.TASK_KIND)
    assert retried_task['payload']['inventory_id'] == failed_inventory
    assert inventory.execute_inventory_slice(retried_task) is False
    with postgres_store.connection(read_only=True) as database:
        replacement_task = database.execute_native('SELECT * FROM background_tasks WHERE id=%s',
                                                   (task['id'],)).fetchone()
        replacement_id = json.loads(replacement_task['payload_json'])['inventory_id']
        assert replacement_task['state'] == 'queued'
        assert replacement_task['generation'] > retried_task['generation']
        assert replacement_id != failed_inventory
        assert inventory.inventory_progress(database, repertoire_id)['publication_id'] is None
    assert inventory.execute_inventory_slice(retried_task) is False
    from scripts.check_postgres_position_inventory import run_to_completion
    run_to_completion(repertoire_id)
    with postgres_store.connection(read_only=True) as database:
        progress = inventory.inventory_progress(database, repertoire_id)
        assert progress['publication_id'] == replacement_id and not progress['stale'], progress
        assert database.execute_native('SELECT state FROM background_tasks WHERE id=%s', (task['id'],)).fetchone()[0] == 'complete'
        assert database.execute_native('SELECT COUNT(*) FROM inventory_generations WHERE repertoire_id=%s',
                                        (repertoire_id,)).fetchone()[0] == 1
        for table in ('inventory_generation_routes', 'inventory_responses'):
            assert database.execute_native(f'SELECT COUNT(*) FROM {table} WHERE generation_id=%s',
                                            (failed_inventory,)).fetchone()[0] == 0
        assert database.execute_native(
            'SELECT COUNT(*) FROM inventory_generation_routes WHERE generation_id=%s', (replacement_id,)).fetchone()[0] == 1
    print('PASS test_pr117_inventory_manual_retry_publishes_replacement_without_stale_memberships', flush=True)


def test_pr117_inventory_publication_rejects_incomplete_routes(dsn):
    from scripts.check_postgres_position_inventory import claim, publish_graph, seed, run_to_completion
    repertoire_id = 'pr117-incomplete'
    with postgres_store.connection() as database:
        seed(database.raw, repertoire_id, [(repertoire_id+'-line',
            ['d2d4', 'g8f6', 'c2c4', 'e7e6', 'b1c3', 'f8b4'], 'white', chess.STARTING_FEN)])
        publish_graph(database, repertoire_id)
        registering = claim(database, repertoire_id)
    assert inventory.execute_inventory_slice(registering)
    with postgres_store.connection() as database:
        traversing = claim(database, repertoire_id)
    premature_publication = {**traversing, 'payload': {**traversing['payload'], 'phase': 'publish'}}
    try:
        inventory.execute_inventory_slice(premature_publication)
    except ValueError as error:
        assert str(error) == 'Inventory contains an unfinished route'
    else:
        raise AssertionError('An incomplete inventory became visible')
    with postgres_store.connection(read_only=True) as database:
        assert inventory.inventory_progress(database, repertoire_id)['publication_id'] is None
        assert database.execute_native('SELECT state FROM background_tasks WHERE id=%s',
                                        (traversing['id'],)).fetchone()[0] == 'leased'
    assert inventory.execute_inventory_slice(traversing)
    run_to_completion(repertoire_id)
    with postgres_store.connection(read_only=True) as database:
        assert inventory.inventory_progress(database, repertoire_id)['publication_id'] == traversing['payload']['inventory_id']
    print('PASS test_pr117_inventory_publication_rejects_incomplete_routes', flush=True)
