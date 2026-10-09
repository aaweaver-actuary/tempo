"""Disposable sparse-unlock, dispatch/replay, and real-browser backlog fixtures."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
import heapq
import json
import os
from pathlib import Path
import sys
import time
from threading import Event, Thread
from unittest.mock import patch
import uuid

import psycopg
from psycopg.errors import TransactionTimeout

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import postgres_store, tasks
from app.main import _unlock_eligible_opening_cards
from app.queue_commands import ensure_current_queue, request_queue_refresh_in_transaction
from app.services import durable_tasks
from app import activity_commands
from app.services.postgres_queue_refresh import execute_postgres_queue_refresh_slice

DSN = "postgresql://postgres@postgres:5432/tempo"
FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def configure():
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Daily study proof requires an explicitly disposable PostgreSQL instance")
    os.environ["TEMPO_DATABASE_WRITE_URL"] = DSN
    os.environ["TEMPO_DATABASE_READ_URL"] = DSN
    postgres_store.close_pools()


def seed(identifier, *, request_refresh, queue_date=None):
    today = queue_date or date.today().isoformat()
    if date.fromisoformat(today).isoformat() != today:
        raise ValueError('An ISO workspace study date is required')
    now = datetime.now(timezone.utc).isoformat()
    # The large cardinality proof is independent of browser fixture creation.
    locked_card_count = 128 if request_refresh else 15_000
    with psycopg.connect(DSN) as connection:
        connection.execute("INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES(%s,'Daily study backlog proof','synthetic',%s,0)", (identifier, now))
        connection.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(%s,%s,'Proof','white',%s,'[\"e2e4\"]',%s)", (identifier, identifier, FEN, now))
        for first_card in range(0, locked_card_count, 1000):
            connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,trained_color,canonical_route_source) SELECT %s||'-locked-'||lpad(number::text,5,'0'),%s,'prefix',%s,'[\"e2e4\"]','locked',%s,'white',0 FROM generate_series(%s::integer,%s::integer) number", (identifier, identifier, FEN, today, first_card, min(first_card+1000, locked_card_count)-1))
            connection.commit()
        for first_card in range(0, locked_card_count, 1000):
            connection.execute("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) SELECT %s,id,0 FROM cards WHERE repertoire_id=%s AND id>=%s AND id<=%s",
                (identifier, identifier, f'{identifier}-locked-{first_card:05}', f'{identifier}-locked-{min(first_card+1000, locked_card_count)-1:05}'))
            connection.commit()
        connection.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,published_at) VALUES(%s,1,%s)", (identifier, now))
        connection.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color) SELECT %s,1,%s,number,%s||'-locked-'||lpad(number::text,5,'0'),%s,%s,'[\"e2e4\"]','white' FROM generate_series(%s::integer,%s::integer) number", (identifier, identifier, identifier, FEN, FEN, locked_card_count-21, locked_card_count-1))
        connection.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,trained_color,introduced_at) VALUES(%s,%s,'prefix',%s,'[\"e2e4\"]','learning','2000-01-01','white','2000-01-01')", (identifier+'-due', identifier, FEN))
        connection.execute("INSERT INTO repertoire_cards VALUES(%s,%s)", (identifier, identifier+'-due'))
        connection.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(%s,'correct','2000-01-01',1,7)", (identifier+'-due',))
        # Eligible low-priority durable jobs remain behind foreground queue preparation.
        connection.execute("INSERT INTO background_tasks(id,kind,deduplication_key,priority,payload_json,next_attempt_at,created_at,updated_at) SELECT %s||'-analysis-'||number,'game_derivation_compare',%s||'-missing-game-'||number,127,json_build_object('game_id',%s||'-missing-game-'||number,'derivation_version',1)::text,%s,%s,%s FROM generate_series(1,3000) number", (identifier, identifier, identifier, now, now, now))
    if request_refresh:
        with postgres_store.connection() as connection:
            request_queue_refresh_in_transaction(connection, today)


def cleanup(identifier):
    postgres_store.close_pools()
    with psycopg.connect(DSN) as connection:
        connection.execute("DELETE FROM background_tasks WHERE id LIKE %s OR deduplication_key=%s", (identifier+'-%', identifier))
        connection.execute("DELETE FROM reviews WHERE card_id IN (SELECT id FROM cards WHERE repertoire_id=%s)", (identifier,))
        connection.execute("DELETE FROM cards WHERE repertoire_id=%s", (identifier,))
        connection.execute("DELETE FROM repertoires WHERE id=%s", (identifier,))


def proof_deadline_recovery(identifier):
    """Real fenced deadline/projection writes, recreation, rollback and replay."""

    queue_date = '2099-10-07'
    observed_clock = [datetime.now(timezone.utc)]
    publication_payload = {'queue_date': queue_date, '_queue_phase': 'publish_projection'}
    with psycopg.connect(DSN) as connection:
        assert connection.execute('SELECT 1 FROM queue_projections WHERE queue_date=%s', (queue_date,)).fetchone() is None
        connection.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending) VALUES(%s,'refreshing',0,1)", (queue_date,))

    def saved_state():
        with psycopg.connect(DSN, row_factory=psycopg.rows.dict_row) as connection:
            task = connection.execute("SELECT * FROM background_tasks WHERE kind='daily_queue' AND deduplication_key=%s", (identifier,)).fetchone()
            projection = connection.execute('SELECT * FROM queue_projections WHERE queue_date=%s', (queue_date,)).fetchone()
            return task, projection

    def claimed_owned():
        claimed = durable_tasks.claim_task('daily_queue')
        assert claimed and claimed['deduplication_key'] == identifier
        return claimed

    def deadline(claimed):
        return durable_tasks.defer_task_for_transaction_timeout(claimed, TransactionTimeout('Controlled daily queue deadline'))

    try:
        with patch.object(durable_tasks, '_now', lambda: observed_clock[0]):
            for _generation_number in range(7):
                durable_tasks.enqueue_task('daily_queue', identifier, publication_payload, priority=1, foreground=False)
                claimed = claimed_owned()
                assert deadline(claimed)
                saved_task, projection = saved_state()
                assert saved_task['transaction_timeout_count'] == 1
                assert datetime.fromisoformat(saved_task['next_attempt_at']) == observed_clock[0] + timedelta(seconds=1)
                assert (projection['state'], projection['refresh_pending'], projection['last_error']) == ('refreshing', 1, 'Controlled daily queue deadline')
                observed_clock[0] += timedelta(seconds=1)
                resumed = claimed_owned()
                assert not execute_postgres_queue_refresh_slice(resumed)
                saved_task, projection = saved_state()
                assert saved_task['state'] == 'complete' and saved_task['transaction_timeout_count'] == 0
                assert projection['state'] == 'ready' and projection['refresh_pending'] == 0 and projection['last_error'] is None
            print('PASS test_issue135_postgres_new_generations_do_not_inherit_deadline_cooldown')

            durable_tasks.enqueue_task('daily_queue', identifier, publication_payload, priority=1, foreground=False)
            claimed = claimed_owned()
            before_failure = saved_state()
            original_status_update = durable_tasks.update_queue_refresh_status_in_transaction

            def interrupted_status_update(*args, **kwargs):
                original_status_update(*args, **kwargs)
                raise RuntimeError('Controlled interruption after projection update')

            with patch.object(durable_tasks, 'update_queue_refresh_status_in_transaction', interrupted_status_update):
                try:
                    deadline(claimed)
                except RuntimeError as error:
                    assert str(error) == 'Controlled interruption after projection update'
                else:
                    raise AssertionError('Expected atomic publication interruption')
            assert saved_state() == before_failure
            print('PASS test_issue135_postgres_deadline_and_projection_roll_back_together')

            for timeout_count, delay_seconds in enumerate((1, 2, 4, 8, 16, 32, 60, 60), start=1):
                assert deadline(claimed)
                saved_task, _projection = saved_state()
                assert saved_task['transaction_timeout_count'] == timeout_count and saved_task['attempt_count'] == 0
                assert datetime.fromisoformat(saved_task['next_attempt_at']) == observed_clock[0] + timedelta(seconds=delay_seconds)
                postgres_store.close_pools()
                assert not deadline(claimed)
                assert saved_state()[0]['transaction_timeout_count'] == timeout_count
                observed_clock[0] += timedelta(seconds=delay_seconds)
                claimed = claimed_owned()
            crashed_delivery = claimed
            observed_clock[0] += timedelta(seconds=61)
            postgres_store.close_pools()
            resumed = claimed_owned()
            assert resumed['lease_token'] != crashed_delivery['lease_token']
            assert not deadline(crashed_delivery)
            assert saved_state()[0]['transaction_timeout_count'] == 8
            with postgres_store.connection(background=True) as connection:
                assert durable_tasks.advance_task_slice_in_transaction(connection, resumed,
                    next_phase='publish_projection', next_payload=publication_payload)
            saved_task, projection = saved_state()
            assert saved_task['transaction_timeout_count'] == 0 and saved_task['transaction_timeout_checkpoint'] is None
            assert projection['last_error'] is None and projection['state'] == 'refreshing'
            publication_delivery = claimed_owned()
            assert not execute_postgres_queue_refresh_slice(publication_delivery)
            published_state = saved_state()
            assert not execute_postgres_queue_refresh_slice(publication_delivery)
            assert not deadline(publication_delivery)
            assert saved_state() == published_state
            print('PASS test_issue135_postgres_deadline_restart_progress_and_publication_replay')

            durable_tasks.enqueue_task('daily_queue', identifier, publication_payload, priority=1, foreground=False)
            failed_delivery = claimed_owned()
            with psycopg.connect(DSN) as connection:
                connection.execute('UPDATE background_tasks SET attempt_count=max_attempts WHERE id=%s', (failed_delivery['id'],))
            failure = durable_tasks.fail_task(failed_delivery['id'], failed_delivery['generation'], failed_delivery['lease_token'], RuntimeError('Controlled terminal queue failure'))
            assert failure['state'] == 'failed'
            assert saved_state()[1]['state'] == 'failed' and saved_state()[1]['refresh_pending'] == 0
            with postgres_store.connection() as connection:
                activity_commands.retry_failed_task(connection, {'task_id': failed_delivery['id']})
            saved_task, projection = saved_state()
            assert saved_task['transaction_timeout_count'] == 0 and saved_task['transaction_timeout_checkpoint'] is None
            assert projection['state'] == 'refreshing' and projection['refresh_pending'] == 1 and projection['last_error'] is None
            replacement = durable_tasks.enqueue_task('daily_queue', identifier, publication_payload, priority=1, foreground=False)
            assert replacement['generation'] > failed_delivery['generation']
            assert not deadline(failed_delivery)
            assert durable_tasks.fail_task(failed_delivery['id'], failed_delivery['generation'], failed_delivery['lease_token'], RuntimeError('Stale queue failure'))['state'] == 'superseded'
            assert not execute_postgres_queue_refresh_slice(claimed_owned())
            print('PASS test_issue135_postgres_terminal_retry_and_replacement_fencing')

            durable_tasks.enqueue_task('daily_queue', identifier, publication_payload, priority=1, foreground=False)
            compact_replaced_delivery = claimed_owned()
            assert deadline(compact_replaced_delivery)
            with postgres_store.connection(background=True) as connection:
                durable_tasks.enqueue_compact_postgres_task_in_transaction(connection,
                    'daily_queue', identifier, publication_payload, priority=1)
            saved_task, _projection = saved_state()
            assert saved_task['generation'] == compact_replaced_delivery['generation'] + 1
            assert saved_task['transaction_timeout_count'] == 0 and saved_task['transaction_timeout_checkpoint'] is None
            assert not deadline(compact_replaced_delivery)
            assert not execute_postgres_queue_refresh_slice(claimed_owned())
            print('PASS test_issue135_postgres_compact_enqueue_resets_deadline_episode')

            # The worker is intentionally stopped for this proof. A real command
            # must publish a capacity hint only after its transaction commits.
            from app import command_gateway
            from app.celery_app import celery_app
            proof_command = 'proof.issue135.queue_refresh'
            operation_id = identifier + '-wakeup'
            failed_operation_id = identifier + '-rolled-back-wakeup'
            observed_wakes = []
            original_send = celery_app.send_task

            def enqueue_from_command(database, payload):
                durable_tasks.enqueue_task_in_transaction(database, 'daily_queue', identifier,
                    publication_payload, priority=1)
                if payload.get('fail'):
                    raise RuntimeError('Controlled queue request rollback')
                return {'accepted': True}

            def observe_committed_wake(name, **options):
                assert name == 'app.tasks.poll_background_tasks'
                assert options['queue'] == 'background' and options['ignore_result'] is True
                assert options['retry'] is False
                with psycopg.connect(DSN) as observer:
                    receipt = observer.execute('SELECT state FROM operation_receipts WHERE operation_id=%s', (operation_id,)).fetchone()
                    assert receipt[0] == 'complete'
                    # An independent writer can lock the task before broker I/O.
                    observed = observer.execute("SELECT state,lease_token FROM background_tasks WHERE kind='daily_queue' AND deduplication_key=%s FOR UPDATE NOWAIT", (identifier,)).fetchone()
                    assert observed == ('queued', None)
                publication = original_send(name, **options)
                observed_wakes.append(name)
                return publication

            command_gateway.register_command(proof_command, enqueue_from_command)
            try:
                with patch.object(celery_app, 'send_task', observe_committed_wake):
                    assert command_gateway.execute_command(operation_id, proof_command, {}) == {'accepted': True}
                    committed_state = saved_state()
                    assert command_gateway.execute_command(operation_id, proof_command, {}) == {'accepted': True}
                    assert command_gateway.execute_command(failed_operation_id, proof_command, {'fail': True}) is None
                    assert saved_state() == committed_state
                    assert observed_wakes == ['app.tasks.poll_background_tasks']
            finally:
                command_gateway._handlers.pop(proof_command)
                with psycopg.connect(DSN) as connection:
                    connection.execute('DELETE FROM operation_receipts WHERE operation_id IN (%s,%s)', (operation_id, failed_operation_id))
            assert not execute_postgres_queue_refresh_slice(claimed_owned())
            print('PASS test_issue135_postgres_command_queue_wake_follows_commit_and_never_rollback')
    finally:
        postgres_store.close_pools()
        with psycopg.connect(DSN) as connection:
            connection.execute('DELETE FROM queue_projections WHERE queue_date=%s', (queue_date,))



def proof_deferred_capacity_wakes(identifier):
    """No beat: consume real Redis poll messages with an explicit delivery clock."""
    from kombu import Exchange, Queue
    from kombu.exceptions import EncodeError, OperationalError
    from app import command_gateway
    from app.celery_app import celery_app
    from app.services import queue_refresh_wakeup

    queue_date = '2099-10-08'
    observed_clock = [datetime.now(timezone.utc)]
    publication_payload = {'queue_date': queue_date, '_queue_phase': 'publish_projection'}
    proof_command = 'proof.issue135.deferred_capacity'
    broker_queue = Queue(identifier + '-eta', Exchange(identifier + '-eta', type='direct'),
                         routing_key=identifier + '-eta')
    deliveries, publications, executions, polls, operation_ids = [], [], [], [], []
    fail_next_slice = [True]
    original_send = celery_app.send_task
    original_claim = durable_tasks.claim_task

    def saved_state():
        with psycopg.connect(DSN, row_factory=psycopg.rows.dict_row) as connection:
            task = connection.execute("SELECT * FROM background_tasks WHERE kind='daily_queue' AND deduplication_key=%s", (identifier,)).fetchone()
            projection = connection.execute('SELECT * FROM queue_projections WHERE queue_date=%s', (queue_date,)).fetchone()
            return task, projection

    def enqueue_from_command(database, payload):
        task = durable_tasks.enqueue_task_in_transaction(database, 'daily_queue', identifier,
            publication_payload, priority=1)
        database.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending) "
            "VALUES(?,'refreshing',?,1) ON CONFLICT(queue_date) DO UPDATE SET "
            "state='refreshing',generation=excluded.generation,refresh_pending=1,last_error=NULL",
            (queue_date, task['generation']))
        return {'accepted': True, 'generation': task['generation']}

    def command(suffix):
        operation_id = identifier + '-' + suffix
        operation_ids.append(operation_id)
        result = command_gateway.execute_command(operation_id, proof_command, {})
        with psycopg.connect(DSN) as observer:
            assert observer.execute('SELECT state FROM operation_receipts WHERE operation_id=%s', (operation_id,)).fetchone()[0] == 'complete'
        return result

    with celery_app.connection_for_write(connect_timeout=1,
            transport_options={'socket_timeout': 1, 'socket_connect_timeout': 1, 'max_retries': 0}) as broker:
        broker_queue(broker).declare()
        capacity_messages = broker.SimpleQueue(broker_queue)

        def publish_capacity(name, **options):
            assert name == 'app.tasks.poll_background_tasks' and options['queue'] == 'background'
            assert not options.get('args') and not options.get('kwargs') and 'task_id' not in options
            # Independent real writer must obtain the row before broker I/O.
            with psycopg.connect(DSN) as observer:
                saved = observer.execute("SELECT state,next_attempt_at,lease_token FROM background_tasks "
                    "WHERE kind='daily_queue' AND deduplication_key=%s FOR UPDATE NOWAIT", (identifier,)).fetchone()
                if 'eta' in options:
                    assert saved[0] == 'retrying' and saved[2] is None
                    assert datetime.fromisoformat(saved[1]) == options['eta']
                    assert options['retry'] is False and options['ignore_result'] is True
            # Only the transport queue is isolated; the production task name,
            # serializer and ETA envelope pass through real Celery and Redis.
            original_send(name, **{**options, 'queue': broker_queue})
            message = capacity_messages.get(block=False)
            try:
                assert message.headers['task'] == name
                assert message.payload[0] == [] and message.payload[1] == {}
                delivery_at = (datetime.fromisoformat(message.headers['eta'])
                    if message.headers.get('eta') else observed_clock[0])
                assert delivery_at == options.get('eta', observed_clock[0])
                publications.append((delivery_at, dict(options)))
                heapq.heappush(deliveries, (delivery_at, len(publications)))
            finally:
                message.ack()

        def claim_owned(**options):
            claimed = original_claim('daily_queue')
            if claimed is not None:
                assert claimed['deduplication_key'] == identifier
            return claimed

        def execute_slice(claimed):
            executions.append((claimed['generation'], observed_clock[0]))
            if fail_next_slice[0]:
                fail_next_slice[0] = False
                raise TransactionTimeout('Controlled deferred PostgreSQL queue deadline')
            return execute_postgres_queue_refresh_slice(claimed)

        def deliver_due():
            for _delivery_number in range(100):
                if not deliveries or deliveries[0][0] > observed_clock[0]:
                    return
                delivery_at, _sequence = heapq.heappop(deliveries)
                polls.append((delivery_at, tasks.poll_background_tasks.run()))
            raise AssertionError('Unbounded capacity delivery loop')

        command_gateway.register_command(proof_command, enqueue_from_command)
        try:
            with patch.object(durable_tasks, '_now', lambda: observed_clock[0]), \
                    patch.object(celery_app, 'send_task', publish_capacity), \
                    patch.object(tasks, 'claim_task', claim_owned), \
                    patch.object(tasks, 'execute_postgres_queue_refresh_slice', execute_slice):
                for scenario in ('recover', 'replace', 'earlier_progress'):
                    fail_next_slice[0] = True
                    first_execution = len(executions)
                    first_poll = len(polls)
                    accepted = command(scenario)
                    assert publications[-1][0] == observed_clock[0] and 'eta' not in publications[-1][1]
                    deliver_due()
                    saved_task, projection = saved_state()
                    eligibility = datetime.fromisoformat(saved_task['next_attempt_at'])
                    assert eligibility == observed_clock[0] + timedelta(seconds=1)
                    assert saved_task['state'] == 'retrying' and saved_task['generation'] == accepted['generation']
                    assert projection['state'] == 'refreshing' and projection['last_error'] is not None
                    assert polls[first_poll:] == [(observed_clock[0], True), (observed_clock[0], False)]
                    assert len(deliveries) == 1 and deliveries[0][0] == eligibility
                    current_generation = accepted['generation']
                    if scenario != 'recover':
                        if scenario == 'replace':
                            # Commit replacement without injecting a new poll.
                            with postgres_store.connection() as database:
                                replacement = enqueue_from_command(database, {})
                        else:
                            replacement = command(scenario + '-replacement')
                            deliver_due()
                        current_generation = replacement['generation']
                        assert current_generation == accepted['generation'] + 1
                    observed_clock[0] = eligibility - timedelta(microseconds=1)
                    deliver_due()
                    assert len(executions) == first_execution + (2 if scenario == 'earlier_progress' else 1)
                    observed_clock[0] = eligibility
                    heapq.heappush(deliveries, deliveries[0])  # duplicate broker delivery
                    deliver_due()
                    saved_task, projection = saved_state()
                    assert [generation for generation, _time in executions[first_execution:]] == [accepted['generation'], current_generation]
                    assert saved_task['state'] == 'complete' and saved_task['generation'] == current_generation
                    assert (projection['state'], projection['refresh_pending'], projection['last_error']) == ('ready', 0, None)
                    assert projection['generation'] == current_generation + 1
                    assert polls[-1] == (eligibility, False)
                    assert not deliveries
                print('PASS test_issue135_postgres_deferred_queue_recovers_without_periodic_polling')
                print('PASS test_issue135_postgres_delayed_wake_replacement_and_duplicate_are_fenced')

                for exception_type in (OperationalError, EncodeError):
                    with patch.object(celery_app, 'send_task', side_effect=exception_type('Controlled advisory broker failure')), \
                            patch.object(queue_refresh_wakeup._LOGGER, 'exception', wraps=queue_refresh_wakeup._LOGGER.exception) as logged:
                        accepted = command('broker-' + exception_type.__name__)
                        assert logged.call_count == 1
                    saved_task, projection = saved_state()
                    assert saved_task['generation'] == accepted['generation'] and saved_task['state'] == 'queued'
                    assert projection['generation'] == accepted['generation'] and projection['state'] == 'refreshing'
                    # Finish only this proof's retained task before the next scenario.
                    assert not execute_postgres_queue_refresh_slice(claim_owned())
                print('PASS test_issue135_postgres_advisory_broker_errors_preserve_committed_receipts')
        finally:
            command_gateway._handlers.pop(proof_command)
            capacity_messages.close()
            broker_queue(broker).delete()
            postgres_store.close_pools()
            with psycopg.connect(DSN) as connection:
                connection.execute('DELETE FROM operation_receipts WHERE operation_id=ANY(%s)', (operation_ids,))
                connection.execute('DELETE FROM queue_projections WHERE queue_date=%s', (queue_date,))



@contextmanager
def _lock_order_connection():
    """Diagnostic transactions accommodate barriers; production budgets stay unchanged."""
    with psycopg.connect(DSN, row_factory=postgres_store.tempo_row_factory,
                         options="-c statement_timeout=5000 -c transaction_timeout=10000") as raw:
        database = postgres_store.PostgresConnection(raw)
        yield database
        database.flush_background_metrics()


def _wait_for_lock_owner(waiting_pid, owning_pid, waiting_future):
    deadline = time.monotonic() + 3
    with psycopg.connect(DSN, autocommit=True) as observer:
        while True:
            blockers = observer.execute('SELECT pg_blocking_pids(%s)', (waiting_pid,)).fetchone()[0]
            if owning_pid in blockers:
                return
            assert not waiting_future.done(), f'Operation finished before lock wait: {waiting_future.result()}'
            assert time.monotonic() < deadline, f'Backend {waiting_pid} did not wait for {owning_pid}: {blockers}'


@contextmanager
def _queue_lock_order_fixture(*, task_queue_date='2099-10-09', leased=True):
    """Preserve the singleton, its pruned events, projections and metric buckets."""
    from check_postgres_repertoire_limits import restore_queue_environment

    queue_dates = ('2099-10-09', '2099-10-10')
    clock = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
    with _lock_order_connection() as database:
        saved_task = database.execute("SELECT * FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'").fetchone()
        snapshot = {
            'queue_dates': queue_dates,
            'task': dict(saved_task) if saved_task else None,
            'events': [dict(row) for row in database.execute('SELECT * FROM background_task_events WHERE task_id=? ORDER BY id', (saved_task['id'],))] if saved_task else [],
            'projections': [dict(row) for row in database.execute('SELECT * FROM queue_projections WHERE queue_date IN (?,?) ORDER BY queue_date', queue_dates)],
            'metrics': [dict(row) for row in database.execute("SELECT * FROM background_metric_buckets WHERE kind='daily_queue' ORDER BY shard,slot")],
            'stale_introductions': [], 'priority_epochs': [],
        }
    try:
        with patch.object(durable_tasks, '_now', lambda: clock):
            with _lock_order_connection() as database:
                database.execute("DELETE FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'")
                task = None
                if leased:
                    task = request_queue_refresh_in_transaction(database, task_queue_date)
                    database.execute("UPDATE background_tasks SET state='leased',phase='publish_projection',attempt_count=1,lease_token='lock-order-lease',lease_expires_at=? WHERE id=?",
                                     ((clock + timedelta(seconds=30)).isoformat(), task['id']))
                    task = dict(database.execute('SELECT * FROM background_tasks WHERE id=?', (task['id'],)).fetchone())
                    task['payload'] = json.loads(task.pop('payload_json'))
                for queue_date in queue_dates:
                    database.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending,last_error) VALUES(?,'refreshing',17,1,'Before race') ON CONFLICT(queue_date) DO UPDATE SET state='refreshing',generation=17,refresh_pending=1,last_error='Before race'", (queue_date,))
            yield task, clock
    finally:
        with _lock_order_connection() as database:
            restore_queue_environment(database, snapshot)


def _lock_order_state(queue_date):
    with _lock_order_connection() as database:
        task = database.execute("SELECT * FROM background_tasks WHERE kind='daily_queue' AND deduplication_key='current'").fetchone()
        projection = database.execute('SELECT * FROM queue_projections WHERE queue_date=?', (queue_date,)).fetchone()
        return dict(task) if task else None, dict(projection)


def test_issue135_postgres_ensure_current_timeout_deferral_lock_order():
    """B owns the task; A waits for it before B publishes to their projection."""
    from app.services import queue_refresh_wakeup

    queue_date = '2099-10-09'
    task_updated, release_deferral, ensure_connected = Event(), Event(), Event()
    deferral_pid, ensure_pid, wakes = [], [], []
    original_status = durable_tasks.update_queue_refresh_status_in_transaction

    def pause_before_projection(database, *args, **kwargs):
        deferral_pid.append(database.raw.info.backend_pid)
        task_updated.set()
        assert release_deferral.wait(4), 'Deferral barrier was not released'
        return original_status(database, *args, **kwargs)

    def background_write(operation, *, label):
        with _lock_order_connection() as database:
            return operation(database)

    def ensure():
        with _lock_order_connection() as database:
            ensure_pid.append(database.raw.info.backend_pid)
            ensure_connected.set()
            return ensure_current_queue(database, {'queue_date': queue_date})

    def publish(name, **options):
        assert name == 'app.tasks.poll_background_tasks' and options['queue'] == 'background'
        assert options['eta'] == clock + timedelta(seconds=1)
        assert not options.get('args') and not options.get('kwargs')
        assert options['retry'] is False and options['ignore_result'] is True
        # Independent NOWAIT access plus absent backend proves commit and close.
        with _lock_order_connection() as observer:
            assert observer.execute('SELECT 1 FROM pg_stat_activity WHERE pid=?', (deferral_pid[0],)).fetchone() is None
            saved = observer.execute('SELECT state,next_attempt_at,lease_token FROM background_tasks WHERE id=? FOR UPDATE NOWAIT', (task['id'],)).fetchone()
            assert saved['state'] == 'retrying' and saved['lease_token'] is None
            assert datetime.fromisoformat(saved['next_attempt_at']) == options['eta']
            assert observer.execute('SELECT last_error FROM queue_projections WHERE queue_date=?', (queue_date,)).fetchone()[0] == 'Controlled lock-order deadline'
        wakes.append(options['eta'])

    with _queue_lock_order_fixture() as (task, clock), \
            patch.object(durable_tasks, 'submit_background_write', background_write), \
            patch.object(durable_tasks, 'update_queue_refresh_status_in_transaction', pause_before_projection), \
            patch.object(tasks.celery_app, 'send_task', publish), \
            patch.object(queue_refresh_wakeup._LOGGER, 'exception') as broker_error, \
            ThreadPoolExecutor(max_workers=2) as executor:
        deferral = executor.submit(durable_tasks.defer_task_for_transaction_timeout, task, TransactionTimeout('Controlled lock-order deadline'))
        try:
            assert task_updated.wait(3), 'Deferral did not lock the task'
            foreground = executor.submit(ensure)
            assert ensure_connected.wait(3)
            _wait_for_lock_owner(ensure_pid[0], deferral_pid[0], foreground)
        finally:
            release_deferral.set()
        assert deferral.result(timeout=5)
        assert foreground.result(timeout=5) == {'queue_date': queue_date, 'refresh_pending': True, 'task_id': task['id']}
        assert not broker_error.called, 'Post-commit wake assertions or publication failed'
        saved, projection = _lock_order_state(queue_date)
        assert saved['generation'] == task['generation'] and saved['state'] == 'retrying'
        assert saved['lease_token'] is None and saved['lease_expires_at'] is None and saved['attempt_count'] == 0
        assert saved['transaction_timeout_count'] == 1
        assert json.loads(saved['transaction_timeout_checkpoint']) == [task['generation'], task['phase'], task['payload']]
        assert datetime.fromisoformat(saved['next_attempt_at']) == clock + timedelta(seconds=1)
        assert (projection['state'], projection['refresh_pending'], projection['last_error'], projection['generation']) == ('refreshing', 1, saved['last_error'], 17)
        assert wakes == [clock + timedelta(seconds=1)]
    print('PASS test_issue135_postgres_ensure_current_timeout_deferral_lock_order', flush=True)


def test_issue135_postgres_ensure_current_replacement_fences_timeout_deferral():
    """A owns the singleton; B's old fenced UPDATE waits until A replaces it."""
    queue_date = '2099-10-10'
    task_locked, release_foreground, deferral_connected = Event(), Event(), Event()
    foreground_pid, deferral_pid = [], []
    original_execute = postgres_store.PostgresConnection.execute

    def pause_after_task_lock(database, statement, parameters=()):
        cursor = original_execute(database, statement, parameters)
        if database.raw.info.backend_pid in foreground_pid and "deduplication_key='current' FOR UPDATE" in statement:
            task_locked.set()
            assert release_foreground.wait(4), 'Replacement barrier was not released'
        return cursor

    def ensure():
        with _lock_order_connection() as database:
            foreground_pid.append(database.raw.info.backend_pid)
            return ensure_current_queue(database, {'queue_date': queue_date})

    def background_write(operation, *, label):
        with _lock_order_connection() as database:
            deferral_pid.append(database.raw.info.backend_pid)
            deferral_connected.set()
            return operation(database)

    with _queue_lock_order_fixture() as (task, clock), \
            patch.object(postgres_store.PostgresConnection, 'execute', pause_after_task_lock), \
            patch.object(durable_tasks, 'submit_background_write', background_write), \
            patch.object(durable_tasks, 'wake_queue_refresh') as wake, \
            ThreadPoolExecutor(max_workers=2) as executor:
        foreground = executor.submit(ensure)
        try:
            assert task_locked.wait(3), 'Ensure did not lock the singleton'
            deferral = executor.submit(durable_tasks.defer_task_for_transaction_timeout, task, TransactionTimeout('Stale lock-order deadline'))
            assert deferral_connected.wait(3)
            _wait_for_lock_owner(deferral_pid[0], foreground_pid[0], deferral)
        finally:
            release_foreground.set()
        assert foreground.result(timeout=5)['task_id'] == task['id']
        assert deferral.result(timeout=5) is False
        saved, projection = _lock_order_state(queue_date)
        assert saved['generation'] == task['generation'] + 1 and saved['state'] == 'queued'
        assert json.loads(saved['payload_json']) == {'queue_date': queue_date}
        assert saved['lease_token'] is None and saved['lease_expires_at'] is None
        assert saved['last_error'] is None and saved['transaction_timeout_count'] == 0
        assert saved['transaction_timeout_checkpoint'] is None
        assert datetime.fromisoformat(saved['next_attempt_at']) == clock
        assert (projection['state'], projection['refresh_pending'], projection['last_error'], projection['generation']) == ('refreshing', 1, None, 17)
        assert _lock_order_state(task['payload']['queue_date'])[1]['last_error'] == 'Before race'
        assert not durable_tasks.defer_task_for_transaction_timeout(task, TransactionTimeout('Stale replay'))
        assert _lock_order_state(queue_date) == (saved, projection)
        wake.assert_not_called()
    print('PASS test_issue135_postgres_ensure_current_replacement_fences_timeout_deferral', flush=True)


def test_issue135_postgres_ensure_current_absent_task_creation_lock_order():
    """A observes no task; B creates/locks it; A waits without owning the projection."""
    queue_date = '2099-10-09'
    task_missing, task_inserted, release_creator, ensure_connected = Event(), Event(), Event(), Event()
    ensure_pid, creator_pid = [], []
    original_execute = postgres_store.PostgresConnection.execute

    def pause_after_missing_task(database, statement, parameters=()):
        cursor = original_execute(database, statement, parameters)
        if database.raw.info.backend_pid in ensure_pid and "deduplication_key='current' FOR UPDATE" in statement:
            task_missing.set()
            assert task_inserted.wait(4), 'Creator did not insert the singleton'
        return cursor

    def ensure():
        with _lock_order_connection() as database:
            ensure_pid.append(database.raw.info.backend_pid)
            ensure_connected.set()
            return ensure_current_queue(database, {'queue_date': queue_date})

    def create():
        # Commit creation before the lock race. Concurrent INSERT event identity
        # is a separate enqueue defect; this proof isolates task/projection order.
        with _lock_order_connection() as database:
            created = durable_tasks.enqueue_task_in_transaction(database, 'daily_queue', 'current',
                {'queue_date': queue_date}, priority=10)
        with _lock_order_connection() as database:
            creator_pid.append(database.raw.info.backend_pid)
            assert database.execute('SELECT id FROM background_tasks WHERE id=? FOR UPDATE', (created['id'],)).fetchone()
            task_inserted.set()
            assert release_creator.wait(4), 'Creator barrier was not released'
            durable_tasks.update_queue_refresh_status_in_transaction(database, 'daily_queue',
                {'queue_date': queue_date}, state='refreshing')
        return created

    with _queue_lock_order_fixture(leased=False) as (_task, clock), \
            patch.object(postgres_store.PostgresConnection, 'execute', pause_after_missing_task), \
            ThreadPoolExecutor(max_workers=2) as executor:
        foreground = executor.submit(ensure)
        try:
            assert ensure_connected.wait(3) and task_missing.wait(3)
            creator = executor.submit(create)
            assert task_inserted.wait(3)
            _wait_for_lock_owner(ensure_pid[0], creator_pid[0], foreground)
            with _lock_order_connection() as observer:
                observer.execute('SELECT queue_date FROM queue_projections WHERE queue_date=? FOR UPDATE NOWAIT', (queue_date,)).fetchone()
        finally:
            release_creator.set()
        created = creator.result(timeout=5)
        assert foreground.result(timeout=5)['task_id'] == created['id']
        saved, projection = _lock_order_state(queue_date)
        assert saved['state'] == 'queued' and saved['lease_token'] is None
        assert saved['generation'] == created['generation']
        assert datetime.fromisoformat(saved['next_attempt_at']) == clock
        assert (projection['state'], projection['refresh_pending'], projection['last_error'], projection['generation']) == ('refreshing', 1, None, 17)
    print('PASS test_issue135_postgres_ensure_current_absent_task_creation_lock_order', flush=True)



def test_issue135_postgres_first_ensure_calls_preserve_singleton_creation():
    """Two initially absent-task ensures serialize creation before projection writes."""
    queue_date = '2099-10-09'
    first_inserted, release_first, second_connected = Event(), Event(), Event()
    first_pid, second_pid = [], []
    original_enqueue = durable_tasks.enqueue_task_in_transaction

    def pause_first_insert(database, *args, **kwargs):
        queued = original_enqueue(database, *args, **kwargs)
        if database.raw.info.backend_pid in first_pid:
            first_inserted.set()
            assert release_first.wait(4), 'First ensure barrier was not released'
        return queued

    def ensure(pids, connected=None):
        with _lock_order_connection() as database:
            pids.append(database.raw.info.backend_pid)
            if connected is not None:
                connected.set()
            return ensure_current_queue(database, {'queue_date': queue_date})

    with _queue_lock_order_fixture(leased=False), \
            patch('app.queue_commands.enqueue_task_in_transaction', pause_first_insert), \
            ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(ensure, first_pid)
        try:
            assert first_inserted.wait(3), 'First ensure did not insert the task'
            second = executor.submit(ensure, second_pid, second_connected)
            assert second_connected.wait(3)
            _wait_for_lock_owner(second_pid[0], first_pid[0], second)
        finally:
            release_first.set()
        accepted = first.result(timeout=5)
        assert second.result(timeout=5) == accepted
        saved, projection = _lock_order_state(queue_date)
        assert saved['id'] == accepted['task_id'] and saved['generation'] == 1
        assert saved['state'] == 'queued' and saved['lease_token'] is None
        assert (projection['state'], projection['refresh_pending'], projection['last_error']) == ('refreshing', 1, None)
        with _lock_order_connection() as observer:
            assert observer.execute("SELECT COUNT(*) FROM background_task_events WHERE task_id=? AND event='enqueued'", (saved['id'],)).fetchone()[0] == 1
    print('PASS test_issue135_postgres_first_ensure_calls_preserve_singleton_creation', flush=True)


def proof():
    from check_redis_socket_deadlines import test_redis_publication_deadline_preserves_independent_delivery_and_connection_recovery
    test_redis_publication_deadline_preserves_independent_delivery_and_connection_recovery()
    from check_postgres_queue_unlock_scale import proof_graph_scale
    proof_graph_scale(DSN)
    from check_postgres_opening_progression import test_postgres_opening_practice_progression_is_atomic_restartable_and_quota_bound
    test_postgres_opening_practice_progression_is_atomic_restartable_and_quota_bound(DSN)
    identifier = 'daily-study-proof-'+str(uuid.uuid4())
    try:
        seed(identifier, request_refresh=False)
        cursor = ''
        slice_count = 0
        maximum_transaction_seconds = 0.0
        while cursor is not None:
            started = time.perf_counter()
            with postgres_store.connection(background=True) as connection:
                cursor = _unlock_eligible_opening_cards(connection, date.today().isoformat(), after_card_id=cursor, batch_size=8)
            maximum_transaction_seconds = max(maximum_transaction_seconds, time.perf_counter()-started)
            slice_count += 1
        assert slice_count == 3, slice_count
        with psycopg.connect(DSN) as connection:
            assert connection.execute("SELECT count(*) FROM cards WHERE repertoire_id=%s AND state='new'", (identifier,)).fetchone()[0] == 21
        print(f"PASS test_postgres_daily_queue_sparse_unlock: 15000 locked, 21 eligible, {slice_count} slices, max section {maximum_transaction_seconds:.3f}s")
        # Real PostgreSQL claims/commits and Redis admission; restrict selection
        # to a task owned by this proof while normal consumers are stopped.
        durable_task = durable_tasks.enqueue_task('daily_queue', identifier, {}, priority=1, foreground=False)
        original_claim = durable_tasks.claim_task
        executions = []
        claim_started = Event()
        def claim_owned(**kwargs):
            claim_started.set()
            return original_claim('daily_queue')
        def publish(claimed):
            assert claimed['id'] == durable_task['id']
            executions.append(claimed['lease_token'])
            with postgres_store.connection(background=True) as connection:
                assert durable_tasks.complete_task_slice_in_transaction(connection, claimed)
            return False
        worker_errors = []
        def execute_at_capacity():
            try:
                tasks.poll_background_tasks.run()
            except BaseException as error:
                worker_errors.append(error)
        with patch.object(tasks, 'claim_task', claim_owned), patch.object(tasks, 'execute_postgres_queue_refresh_slice', publish):
            worker = Thread(target=execute_at_capacity)
            with tasks.activity_gate.foreground():
                worker.start()
                assert claim_started.wait(2)
                with psycopg.connect(DSN) as connection:
                    assert connection.execute("SELECT lease_token FROM background_tasks WHERE id=%s", (durable_task['id'],)).fetchone()[0] is None
                    assert connection.execute('SELECT 1').fetchone()[0] == 1
            worker.join(5)
            assert not worker.is_alive() and not worker_errors, worker_errors
        assert len(executions) == 1
        executions.clear()
        durable_task = durable_tasks.enqueue_task('daily_queue', identifier, {}, priority=1, foreground=False)
        # Simulate a dead worker's committed lease, then expire just that row.
        crashed_delivery = original_claim('daily_queue')
        assert crashed_delivery['id'] == durable_task['id']
        with psycopg.connect(DSN) as connection:
            connection.execute("UPDATE background_tasks SET lease_expires_at='2000-01-01' WHERE id=%s", (durable_task['id'],))
        with patch.object(tasks, 'claim_task', claim_owned), patch.object(tasks, 'execute_postgres_queue_refresh_slice', publish):
            tasks.poll_background_tasks.run()
            tasks.execute_background_slice.run(crashed_delivery)
            assert len(executions) == 1 and executions[0] != crashed_delivery['lease_token']
            with psycopg.connect(DSN) as connection:
                assert connection.execute("SELECT state FROM background_tasks WHERE id=%s", (durable_task['id'],)).fetchone()[0] == 'complete'
        print('PASS test_postgres_background_execution_capacity_restart_and_legacy_replay')
        proof_deadline_recovery(identifier)
        proof_deferred_capacity_wakes(identifier)
        test_issue135_postgres_ensure_current_timeout_deferral_lock_order()
        test_issue135_postgres_ensure_current_replacement_fences_timeout_deferral()
        test_issue135_postgres_ensure_current_absent_task_creation_lock_order()
        test_issue135_postgres_first_ensure_calls_preserve_singleton_creation()
    finally:
        cleanup(identifier)


if __name__ == '__main__':
    configure()
    if len(sys.argv) == 1:
        proof()
    else:
        action, identifier, *queue_dates = sys.argv[1:]
        if len(queue_dates) > 1 or (queue_dates and action != 'seed'):
            raise ValueError('Only seed accepts one workspace study date')
        if not identifier.startswith('daily-study-proof-') or str(uuid.UUID(identifier.removeprefix('daily-study-proof-'))) != identifier.removeprefix('daily-study-proof-'):
            raise ValueError('A task-owned daily study proof identity is required')
        if action == 'seed':
            seed(identifier, request_refresh=True, queue_date=queue_dates[0] if queue_dates else None)
            tasks.celery_app.send_task('app.tasks.poll_background_tasks', queue='background')
        elif action == 'cleanup':
            cleanup(identifier)
        else:
            raise ValueError('Unknown fixture action')
