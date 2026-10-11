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


def test_issue108_cross_repertoire_transposition_policy_independence_and_deletion(dsn):
    from scripts.check_postgres_position_inventory import seed, publish_graph, run_to_completion
    # Different authored move orders reach the same Black-to-move position.
    first = ['g1f3', 'g8f6', 'g2g3', 'g7g6', 'f1g2', 'f8g7', 'e1g1']
    second = ['g2g3', 'g7g6', 'g1f3', 'g8f6', 'f1g2', 'f8g7', 'e1g1']
    position = chess.Board()
    for move in first:
        position.push_uci(move)
    fen_key = inventory.canonical_fen(position.fen())
    with postgres_store.connection() as database:
        seed(database.raw, 'shared-first', [('shared-first-line', first, 'white', chess.STARTING_FEN)])
        publish_graph(database, 'shared-first')
    run_to_completion('shared-first')
    with postgres_store.connection(read_only=True) as database:
        original_position = dict(database.execute_native('SELECT * FROM inventory_positions WHERE fen_key=%s', (fen_key,)).fetchone())
        original_replies = inventory.legal_replies(database, fen_key)
        original_generation = inventory.inventory_progress(database, 'shared-first')['publication_id']
    with postgres_store.connection() as database:
        seed(database.raw, 'shared-second', [('shared-second-line', second, 'white', chess.STARTING_FEN)])
        publish_graph(database, 'shared-second')
        seed(database.raw, 'unrelated', [('unrelated-line', ['d2d4'], 'white', chess.STARTING_FEN)])
        publish_graph(database, 'unrelated')
    run_to_completion('shared-second'); run_to_completion('unrelated')
    with postgres_store.connection() as database:
        second_generation = inventory.inventory_progress(database, 'shared-second')['publication_id']
        assert original_generation != second_generation
        memberships = database.execute_native(
            'SELECT membership.generation_id,membership.line_id FROM inventory_generation_routes membership '
            'JOIN inventory_occurrences occurrence ON occurrence.route_id=membership.route_id '
            'WHERE occurrence.fen_key=%s AND occurrence.opponent=1 '
            'AND membership.generation_id=ANY(%s) ORDER BY membership.line_id',
            (fen_key, [original_generation, second_generation])).fetchall()
        assert {(row[0], row[1]) for row in memberships} == {
            (original_generation, 'shared-first-line'), (second_generation, 'shared-second-line')}
        assert database.execute_native('SELECT COUNT(*) FROM inventory_positions WHERE fen_key=%s', (fen_key,)).fetchone()[0] == 1
        assert inventory.legal_replies(database, fen_key) == original_replies
        assert {reply['move_uci'] for reply in original_replies} == {move.uci() for move in position.legal_moves}
        database.execute_native("DELETE FROM repertoires WHERE id='unrelated'")
        assert inventory.inventory_progress(database, 'shared-first')['publication_id'] == original_generation
        assert inventory.legal_replies(database, fen_key) == original_replies
        database.execute_native("DELETE FROM repertoires WHERE id='shared-first'")
        assert inventory.inventory_progress(database, 'shared-second')['publication_id'] == second_generation
        assert fen_key in {row['fen_key'] for row in inventory.published_positions(database, 'shared-second')['positions']}
        assert dict(database.execute_native('SELECT * FROM inventory_positions WHERE fen_key=%s', (fen_key,)).fetchone()) == original_position
        assert inventory.legal_replies(database, fen_key) == original_replies
    print('PASS test_issue108_cross_repertoire_transposition_policy_independence_and_deletion', flush=True)


def test_issue108_profile_changes_do_not_rebuild_inventory(dsn):
    from app.services.next_opponent_profile import build_profile
    from scripts.check_postgres_position_inventory import seed, publish_graph, run_to_completion
    profiles = [build_profile('inventory-fixture', [dict(
        id='profile-game', provider='lichess', username='inventory-fixture', rated=1,
        played_at='2026-10-08T12:00:00+00:00', speed=speed, player_rating=rating,
        opponent_rating=rating+100, rating_change=0, time_control='180+0')],
        as_of=datetime(2026,10,9,tzinfo=timezone.utc))
        for speed, rating in [('blitz',1400), ('rapid',2000)]]
    assert profiles[0].version != profiles[1].version
    with postgres_store.connection() as database:
        seed(database.raw, 'profile-independent', [('profile-independent-line', ['e2e4'], 'white', chess.STARTING_FEN)])
        publish_graph(database, 'profile-independent')
    run_to_completion('profile-independent')
    with postgres_store.connection() as database:
        publication = inventory.inventory_progress(database, 'profile-independent')
        task_before = dict(database.execute_native('SELECT * FROM background_tasks WHERE kind=%s AND deduplication_key=%s',
                                                   (inventory.TASK_KIND, 'profile-independent')).fetchone())
        positions_before = inventory.published_positions(database, 'profile-independent')
        for profile in profiles:
            database.execute_native('INSERT INTO next_opponent_snapshots(version,account,method_version,profile_json,published_at) '
                                    'VALUES(%s,%s,%s,%s,%s)', (profile.version, profile.source_account,
                                    profile.method_version, profile.model_dump_json(), datetime.now(timezone.utc).isoformat()))
        database.execute_native("INSERT INTO next_opponent_accounts(account,input_generation,published_generation,profile_version) "
                                "VALUES('inventory-fixture',1,1,%s)", (profiles[0].version,))
        database.execute_native("UPDATE next_opponent_accounts SET input_generation=2,published_generation=2,profile_version=%s "
                                "WHERE account='inventory-fixture'", (profiles[1].version,))
        assert inventory.request_inventory_in_transaction(database, 'profile-independent') == publication['publication_id']
        assert inventory.published_positions(database, 'profile-independent') == positions_before
        assert dict(database.execute_native('SELECT * FROM background_tasks WHERE kind=%s AND deduplication_key=%s',
                                            (inventory.TASK_KIND, 'profile-independent')).fetchone()) == task_before
    print('PASS test_issue108_profile_changes_do_not_rebuild_inventory', flush=True)


def test_issue108_newer_graph_publication_fences_computed_inventory_output(dsn):
    from scripts.check_postgres_position_inventory import seed, publish_graph, claim, run_to_completion
    repertoire_id = 'inventory-stale-compute'
    with postgres_store.connection() as database:
        seed(database.raw, repertoire_id, [('stale-compute-line', ['b2b3', 'e7e5', 'c1b2'], 'white', chess.STARTING_FEN)])
        publish_graph(database, repertoire_id)
        registering = claim(database, repertoire_id)
    assert inventory.execute_inventory_slice(registering)
    with postgres_store.connection() as database:
        stale_task = claim(database, repertoire_id)
    assert stale_task['payload']['phase'] == 'traverse', 'Fixture reused an already complete route'
    original_segment = inventory.inventory_segment
    computed = threading.Event()
    def replace_generation_after_computation(*args, **kwargs):
        result = original_segment(*args, **kwargs)
        # Computation has completed and the source reader is closed. A newer
        # published graph/task wins before the old result enters its writer.
        with postgres_store.connection() as database:
            database.execute_native("UPDATE repertoire_lines SET moves_json='[\"b2b3\",\"d7d5\",\"c1b2\"]' WHERE id='stale-compute-line'")
            publish_graph(database, repertoire_id, 2)
        computed.set()
        return result
    with patch.object(inventory, 'inventory_segment', replace_generation_after_computation):
        assert inventory.execute_inventory_slice(stale_task) is False
    assert computed.is_set()
    run_to_completion(repertoire_id)
    with postgres_store.connection(read_only=True) as database:
        accepted = inventory.published_positions(database, repertoire_id)
        assert accepted['input_identity']['graph_generation'] == 2 and not accepted['stale']
        accepted_generation = accepted['publication_id']
    assert inventory.execute_inventory_slice(stale_task) is False
    with postgres_store.connection(read_only=True) as database:
        assert inventory.published_positions(database, repertoire_id) == accepted
        assert database.execute_native('SELECT COUNT(*) FROM inventory_generation_routes WHERE generation_id=%s',
                                       (accepted_generation,)).fetchone()[0] == 1
    print('PASS test_issue108_newer_graph_publication_fences_computed_inventory_output', flush=True)


def test_pr117_pd82_inspection_preserves_queue_while_independent_publication_assigns_admission(dsn):
    """Hold a real queue checkpoint, without pausing inside a transaction.

    The old browser precondition accepted any target card, including this
    unpublished row. Inventory and inspection must leave it untouched; only
    the normal daily-queue randomization may finalize its admission kind.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import prefix_diagnostics_api as diagnostics, queue_commands
    from app.services import postgres_queue_refresh as queue_refresh
    from scripts.check_postgres_position_inventory import seed, publish_graph, claim, run_to_completion

    repertoire_id = 'pr117-pd82-publication'
    queue_date = datetime.now(timezone.utc).date().isoformat()
    with postgres_store.connection() as database:
        seed(database.raw, repertoire_id, [(repertoire_id+'-line',
            ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5'], 'white', chess.STARTING_FEN)])
        publish_graph(database, repertoire_id)
        card_id = database.execute_native(
            'SELECT id FROM cards WHERE repertoire_id=%s ORDER BY length(moves_json) DESC LIMIT 1',
            (repertoire_id,)).fetchone()[0]
        database.execute_native("UPDATE cards SET state='learning',due_date=%s WHERE id=%s", (queue_date, card_id))
        database.execute_native('INSERT INTO daily_queue(queue_date,card_id,position) VALUES(%s,%s,0)',
                                (queue_date, card_id))
        queue_task = queue_commands.request_queue_refresh_in_transaction(database, queue_date)
        # Establish the persisted checkpoint immediately before randomization.
        # Later deliveries execute the real handler with its normal budgets.
        database.execute_native("UPDATE background_tasks SET phase='randomize_queue',payload_json=%s WHERE id=%s",
            (json.dumps({'queue_date': queue_date, '_queue_phase': 'randomize_queue'}), queue_task['id']))

    snapshot_queries = {
        'cards': 'SELECT to_jsonb(row) FROM cards row ORDER BY id',
        'queue': 'SELECT to_jsonb(row) FROM daily_queue row ORDER BY id',
        'projection': 'SELECT to_jsonb(row) FROM queue_projections row ORDER BY queue_date',
        'origins': 'SELECT to_jsonb(row) FROM queue_attempt_origins row ORDER BY queue_entry_id',
        'reviews': 'SELECT to_jsonb(row) FROM reviews row ORDER BY id',
        'tasks': 'SELECT to_jsonb(row) FROM background_tasks row ORDER BY id',
        'events': 'SELECT to_jsonb(row) FROM background_task_events row ORDER BY id',
        'receipts': 'SELECT to_jsonb(row) FROM operation_receipts row ORDER BY operation_id',
        'inventory': 'SELECT to_jsonb(row) FROM inventory_publications row ORDER BY repertoire_id',
        'presentations': 'SELECT to_jsonb(row) FROM opening_evidence_presentations row ORDER BY id',
        'attempts': 'SELECT to_jsonb(row) FROM opening_evidence_attempts row ORDER BY attempt_id',
        'observations': 'SELECT to_jsonb(row) FROM opening_evidence_observations row ORDER BY attempt_id,decision_index',
    }
    snapshot_boundaries = []

    def snapshot():
        with postgres_store.connection(read_only=True) as database:
            captured = {name: [row[0] for row in database.execute_native(statement).fetchall()]
                        for name, statement in snapshot_queries.items()}
        snapshot_boundaries.append({'at': datetime.now(timezone.utc).isoformat(),
                                    'projection': captured['projection'],
                                    'admission_kind': captured['queue'][0]['admission_kind']})
        return captured

    partial = snapshot()
    assert partial['queue'][0]['admission_kind'] is None
    assert partial['projection'][0]['state'] == 'refreshing'
    assert partial['projection'][0]['refresh_pending'] == 1
    # Inventory publication completes while queue publication remains held.
    run_to_completion(repertoire_id)
    after_inventory = snapshot()
    assert after_inventory['queue'] == partial['queue']
    assert after_inventory['projection'] == partial['projection']
    assert next(row for row in after_inventory['tasks'] if row['id'] == queue_task['id']) == next(
        row for row in partial['tasks'] if row['id'] == queue_task['id'])

    application = FastAPI()
    application.include_router(diagnostics.router)
    client = TestClient(application)
    listing = client.get(f'/api/repertoires/{repertoire_id}/prefix-diagnostics')
    assert listing.status_code == 200, listing.text
    prefix = next(row for row in listing.json()['prefixes'] if row['card_id'] == card_id)
    assert prefix['manifest'] is not None, prefix
    detail_url = (f'/api/repertoires/{repertoire_id}/prefix-diagnostics/{card_id}'
                  f"?manifest_id={prefix['manifest']['manifest_id']}&graph_generation=1")
    original_connection = postgres_store.connection
    diagnostic_boundaries = []
    remaining_refusals = 2

    @contextmanager
    def observe_diagnostic_connection(**options):
        nonlocal remaining_refusals
        if options.get('repeatable_read'):
            assert options['read_only'] and options['background'], options
            diagnostic_boundaries.append({'at': datetime.now(timezone.utc).isoformat(),
                                          'refused': remaining_refusals > 0})
            if remaining_refusals:
                remaining_refusals -= 1
                raise psycopg.errors.TransactionTimeout('Controlled diagnostic database refusal')
        with original_connection(**options) as database:
            yield database

    before_inspection = snapshot()
    with patch.object(postgres_store, 'connection', observe_diagnostic_connection):
        for _ in range(2):
            refused = client.get(detail_url, headers={'X-Tempo-Work-Class': 'background'})
            assert refused.status_code == 503 and refused.headers['Retry-After'] == '1', refused.text
            assert refused.json() == {'detail': 'Prefix difficulty is temporarily unavailable. Retry after study work settles.'}
            assert snapshot() == before_inspection
        inspected = client.get(detail_url, headers={'X-Tempo-Work-Class': 'background'})
        assert inspected.status_code == 200, inspected.text
        assert inspected.json()['read_only'] is True
        assert all(row['coverage'] == 'unknown' for row in inspected.json()['decisions'])
    assert len(diagnostic_boundaries) == 3 and remaining_refusals == 0
    assert snapshot() == before_inspection

    publication_boundaries = []
    for _ in range(4):
        with postgres_store.connection() as database:
            delivered = claim(database, 'current', kind='daily_queue')
        if delivered is None:
            break
        publication_boundaries.append({'at': datetime.now(timezone.utc).isoformat(),
            'task_id': delivered['id'], 'generation': delivered['generation'],
            'phase': delivered['payload']['_queue_phase']})
        queue_refresh.execute_postgres_queue_refresh_slice(delivered)
    else:
        raise AssertionError('Held queue publication did not finish in four bounded deliveries')
    settled = snapshot()
    assert settled['queue'][0]['admission_kind'] == 'new'
    assert settled['projection'][0]['state'] == 'ready' and settled['projection'][0]['refresh_pending'] == 0
    assert settled['projection'][0]['generation'] == partial['projection'][0]['generation'] + 1
    assert publication_boundaries[0]['phase'] == 'randomize_queue'
    assert all(row['task_id'] == queue_task['id'] for row in publication_boundaries)
    # This is the original equality failure, reproduced without timing races.
    assert settled['queue'] != partial['queue']
    assert client.get(detail_url).status_code == 200
    assert snapshot() == settled
    print('PASS test_pr117_pd82_inspection_preserves_queue_while_independent_publication_assigns_admission ' +
          json.dumps({'snapshots': snapshot_boundaries, 'diagnostics': diagnostic_boundaries,
                      'queue_publication': publication_boundaries,
                      'admission_origin': 'daily_queue/randomize_queue', 'queue_task_id': queue_task['id'],
                      'receipts_unchanged_by_inspection': True}), flush=True)


def test_pr117_repertoire_deletion_and_inventory_slice_have_no_lock_cycle(dsn):
    from app.card_deletion import delete_repertoire_data
    from scripts.check_postgres_position_inventory import seed, publish_graph, claim

    for schedule in ('foreground_first', 'inventory_first'):
        repertoire_id = 'pr117-delete-lock-'+schedule
        moves = ['d2d4', 'd7d5', 'c2c4'] if schedule == 'foreground_first' else ['c2c4', 'e7e5', 'b1c3']
        with postgres_store.connection() as database:
            seed(database.raw, repertoire_id, [(repertoire_id+'-line', moves,
                                                'white', chess.STARTING_FEN)])
            publish_graph(database, repertoire_id)
            delivery = claim(database, repertoire_id)
        first_lock = threading.Event()

        def pause_after_first_row_lock(statement, _parameters):
            if 'FOR UPDATE' in statement and not first_lock.is_set():
                first_lock.set()
                wait_event(release, 'release first deletion/inventory row lock')

        with concurrent_transactions(dsn) as (launch, blocked, release, outcomes):
            if schedule == 'foreground_first':
                launch('foreground', lambda database: delete_repertoire_data(database, repertoire_id, 'delete'),
                       after=pause_after_first_row_lock)
                wait_event(first_lock, 'foreground first row lock')
                launch('inventory', lambda database: inventory._current(database, delivery))
                blocked('inventory', 'foreground')
            else:
                launch('inventory', lambda database: inventory._current(database, delivery),
                       after=pause_after_first_row_lock)
                wait_event(first_lock, 'inventory first row lock')
                launch('foreground', lambda database: delete_repertoire_data(database, repertoire_id, 'delete'))
                blocked('foreground', 'inventory')
            release.set()
        assert outcomes['foreground'] == {'deleted': True, 'id': repertoire_id}
        assert outcomes['inventory'] is (schedule == 'inventory_first')
        with postgres_store.connection(read_only=True) as database:
            assert not database.execute_native('SELECT 1 FROM repertoires WHERE id=%s', (repertoire_id,)).fetchone()
            assert not database.execute_native('SELECT 1 FROM inventory_generations WHERE repertoire_id=%s',
                                               (repertoire_id,)).fetchone()
    print('PASS test_pr117_repertoire_deletion_and_inventory_slice_have_no_lock_cycle: both lock schedules', flush=True)


def test_pr117_deleted_inventory_routes_collect_with_foreground_contention_restart_replay_and_reuse(dsn):
    import subprocess
    import sys
    from pathlib import Path
    from app.card_deletion import delete_repertoire_data
    from app.services import redis_admission_gate
    from app.services.redis_admission_gate import BackgroundAdmissionDeferred
    from scripts.check_postgres_position_inventory import seed, publish_graph, claim, run_to_completion

    first, survivor, replacement = 'pr117-gc-first', 'pr117-gc-survivor', 'pr117-gc-replacement'
    shared_moves = ['e2e4', 'e7e5', 'g1f3', 'b8c6', 'f1b5']
    private_moves = ['g1f3', 'g8f6', 'f3g1', 'f6g8'] * 16 + ['e2e4']
    with postgres_store.connection() as database:
        seed(database.raw, first, [(first+'-private', private_moves, 'white', chess.STARTING_FEN),
                                  (first+'-shared', shared_moves, 'white', chess.STARTING_FEN)])
        seed(database.raw, survivor, [(survivor+'-shared', shared_moves, 'white', chess.STARTING_FEN)])
        publish_graph(database, first)
        publish_graph(database, survivor)
    run_to_completion(first)
    run_to_completion(survivor)

    def routes_and_occurrences():
        with postgres_store.connection(read_only=True) as database:
            return ([dict(row) for row in database.execute_native('SELECT * FROM inventory_routes ORDER BY id').fetchall()],
                    [dict(row) for row in database.execute_native('SELECT * FROM inventory_occurrences ORDER BY route_id,ply').fetchall()])

    with postgres_store.connection(read_only=True) as database:
        generation_id = inventory.inventory_progress(database, first)['publication_id']
        private_route = database.execute_native('SELECT route_id FROM inventory_generation_routes WHERE generation_id=%s AND line_id=%s',
                                                (generation_id, first+'-private')).fetchone()[0]
        shared_route = database.execute_native('SELECT route_id FROM inventory_generation_routes WHERE generation_id=%s AND line_id=%s',
                                               (generation_id, first+'-shared')).fetchone()[0]
        unrelated_shared_memberships = [dict(row) for row in database.execute_native(
            'SELECT membership.* FROM inventory_generation_routes membership JOIN inventory_generations generation '
            'ON generation.id=membership.generation_id WHERE membership.route_id=%s '
            'AND generation.repertoire_id NOT IN (%s,%s) ORDER BY membership.generation_id,membership.line_id',
            (shared_route, first, survivor)).fetchall()]
        shared_occurrences = [dict(row) for row in database.execute_native(
            'SELECT * FROM inventory_occurrences WHERE route_id=%s ORDER BY ply', (shared_route,)).fetchall()]
        private_occurrences = [dict(row) for row in database.execute_native('SELECT * FROM inventory_occurrences WHERE route_id=%s ORDER BY ply',
                                                                           (private_route,)).fetchall()]
        survivor_before = inventory.published_positions(database, survivor)
        legal_before = [row[0] for row in database.execute_native('SELECT to_jsonb(row) FROM inventory_legal_replies row ORDER BY fen_key,move_uci').fetchall()]
    assert len(private_occurrences) > inventory.PAGE_SIZE
    with postgres_store.connection() as database:
        # Retain introduced cards under the ordinary deletion policy. Reusing
        # their route must not bypass permanent-card recreation exclusions.
        database.execute_native('UPDATE cards SET introduced_at=%s WHERE repertoire_id=%s',
                                (datetime.now(timezone.utc).isoformat(), first))
        delete_repertoire_data(database, first, 'keep')
        collector = database.execute_native('SELECT * FROM background_tasks WHERE kind=%s AND deduplication_key=%s',
                                            (inventory.RECONCILE_KIND, 'orphan_routes')).fetchone()
        assert collector is not None and collector['state'] == 'queued', 'Deleting a repertoire left orphaned routes without durable collection'
        delivered = claim(database, 'orphan_routes', kind=inventory.RECONCILE_KIND)
    before_denial = routes_and_occurrences()
    with redis_admission_gate.foreground_lease():
        try:
            inventory.execute_reconcile_slice(delivered)
        except BackgroundAdmissionDeferred:
            pass
        else:
            raise AssertionError('Orphan collection did not yield to foreground work')
        assert routes_and_occurrences() == before_denial
        with postgres_store.connection(read_only=True) as database:
            current = database.execute_native('SELECT * FROM background_tasks WHERE id=%s', (delivered['id'],)).fetchone()
            assert current['lease_token'] == delivered['lease_token'] and current['attempt_count'] == delivered['attempt_count']
    # A locked orphan cannot make the collector falsely report completion.
    with psycopg.connect(dsn) as locker:
        locker.execute('SELECT id FROM inventory_routes WHERE id=%s FOR UPDATE', (private_route,))
        inventory.execute_reconcile_slice(delivered)
        assert [row for row in routes_and_occurrences()[1] if row['route_id'] == private_route] == private_occurrences
        with postgres_store.connection(read_only=True) as database:
            assert database.execute_native('SELECT state FROM background_tasks WHERE id=%s', (delivered['id'],)).fetchone()[0] == 'queued'
    assert inventory.execute_reconcile_slice(delivered) is False, 'Consumed collector delivery replayed'
    for _ in range(128):
        with postgres_store.connection() as database:
            delivered = claim(database, 'orphan_routes', kind=inventory.RECONCILE_KIND)
        assert delivered is not None
        _, occurrences_before = routes_and_occurrences()
        inventory.execute_reconcile_slice(delivered)
        _, occurrences_after = routes_and_occurrences()
        assert 0 <= len(occurrences_before)-len(occurrences_after) <= inventory.PAGE_SIZE
        if sum(row['route_id'] == private_route for row in occurrences_after) < len(private_occurrences):
            break
    else:
        raise AssertionError('Collector did not reach the owned orphan')

    # Reuse between bounded cleanup deliveries must rebuild a partial artifact,
    # rather than treating its old completed flag as a complete cached route.
    with postgres_store.connection() as database:
        seed(database.raw, replacement, [(replacement+'-line', private_moves, 'white', chess.STARTING_FEN)])
        publish_graph(database, replacement)
    run_to_completion(replacement)
    with postgres_store.connection(read_only=True) as database:
        assert [dict(row) for row in database.execute_native('SELECT * FROM inventory_occurrences WHERE route_id=%s ORDER BY ply',
                                                            (private_route,)).fetchall()] == private_occurrences
        assert inventory.published_positions(database, survivor) == survivor_before
        assert database.execute_native('SELECT 1 FROM inventory_routes WHERE id=%s', (shared_route,)).fetchone()

    with postgres_store.connection() as database:
        delete_repertoire_data(database, survivor, 'delete')
        delete_repertoire_data(database, replacement, 'delete')
    assert inventory.execute_reconcile_slice(delivered) is False, 'Replacement collector generation accepted stale output'
    # A fresh worker process resumes only persisted state and the owned namespace.
    postgres_store.close_pools()
    child = subprocess.run([sys.executable, '-c', """
from scripts.check_postgres_position_inventory import postgres_store, inventory, _owned_inventory_admission
from app.services.durable_tasks import claim_task
with _owned_inventory_admission():
    delivery = claim_task(inventory.RECONCILE_KIND)
    assert delivery and delivery['deduplication_key'] == 'orphan_routes'
    inventory.execute_reconcile_slice(delivery)
    postgres_store.close_pools()
"""], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert child.returncode == 0, f'Restarted collector failed: {child.stderr}'
    last_delivery = delivered
    for _ in range(128):
        with postgres_store.connection() as database:
            delivered = claim(database, 'orphan_routes', kind=inventory.RECONCILE_KIND)
        if delivered is None:
            break
        last_delivery = delivered
        _, occurrences_before = routes_and_occurrences()
        inventory.execute_reconcile_slice(delivered)
        _, occurrences_after = routes_and_occurrences()
        assert 0 <= len(occurrences_before)-len(occurrences_after) <= inventory.PAGE_SIZE
    else:
        raise AssertionError('Orphan collector failed to complete bounded cleanup')
    final = routes_and_occurrences()
    assert not any(row['id'] == private_route for row in final[0])
    assert not any(row['route_id'] == private_route for row in final[1])
    # Earlier proof groups may legitimately share this immutable route. Exact
    # surviving provenance, rather than fixture ownership, decides collection.
    assert any(row['id'] == shared_route for row in final[0]) == bool(unrelated_shared_memberships)
    assert [row for row in final[1] if row['route_id'] == shared_route] == (
        shared_occurrences if unrelated_shared_memberships else [])
    assert inventory.execute_reconcile_slice(last_delivery) is False
    assert routes_and_occurrences() == final
    with postgres_store.connection(read_only=True) as database:
        assert [dict(row) for row in database.execute_native(
            'SELECT * FROM inventory_generation_routes WHERE route_id=%s ORDER BY generation_id,line_id',
            (shared_route,)).fetchall()] == unrelated_shared_memberships
        assert [row[0] for row in database.execute_native('SELECT to_jsonb(row) FROM inventory_legal_replies row ORDER BY fen_key,move_uci').fetchall()] == legal_before
    print('PASS test_pr117_deleted_inventory_routes_collect_with_foreground_contention_restart_replay_and_reuse', flush=True)
