"""Disposable sparse-unlock, dispatch/replay, and real-browser backlog fixtures."""
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from threading import Event, Thread
from unittest.mock import patch
import uuid

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app import postgres_store, tasks
from app.main import _unlock_eligible_opening_cards
from app.queue_commands import request_queue_refresh_in_transaction
from app.services import durable_tasks

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


def proof():
    from check_postgres_scheduling_turns import proof_scheduling_turns
    proof_scheduling_turns(DSN)
    from check_postgres_queue_unlock_scale import proof_graph_scale
    proof_graph_scale(DSN)

    from check_postgres_background_admission import proof_background_admission
    proof_background_admission(DSN)
    import check_postgres_graph_retention as fixtures
    with patch.object(fixtures, 'DATABASE_URL', DSN), fixtures.owned_fixture_database():
        with patch.dict(globals(), {'DSN': os.environ['TEMPO_DATABASE_WRITE_URL']}):
            _proof_daily_queue_dispatch_and_sparse_unlock()


def _proof_daily_queue_dispatch_and_sparse_unlock():
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
        worker_finished = Event()
        def claim_owned(**kwargs):
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
            finally:
                worker_finished.set()
        with patch.object(tasks, 'claim_task', claim_owned), patch.object(tasks, 'execute_postgres_queue_refresh_slice', publish):
            worker = Thread(target=execute_at_capacity)
            with tasks.activity_gate.foreground():
                worker.start()
                assert worker_finished.wait(1), 'Admission denial occupied analysis capacity'
                assert not executions
                with psycopg.connect(DSN) as connection:
                    assert connection.execute("SELECT lease_token FROM background_tasks WHERE id=%s", (durable_task['id'],)).fetchone()[0] is None
                    assert connection.execute('SELECT 1').fetchone()[0] == 1
            worker.join(5)
            assert not worker.is_alive() and not worker_errors, worker_errors
            tasks.poll_background_tasks.run()
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
