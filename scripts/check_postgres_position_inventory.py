"""Named #108 upgrade, restart, replay and route-coverage proofs on disposable PostgreSQL."""
from __future__ import annotations
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import time
import threading
import subprocess
import uuid

import chess
import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from scripts.apply_postgres_migrations import MIGRATIONS, apply_migrations
from app import postgres_store
from app.services import postgres_position_inventory as inventory
from app.services.opening_graph import GraphInput, build_graph
from app.services.activity_gate import activity_gate
from app.services.postgres_opening_graph import stage_graph_steps, publish_graph_generation

NOW = '2026-10-09T00:00:00+00:00'


def seed(database, repertoire_id, lines):
    database.execute('INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,%s,%s)',
                     (repertoire_id, repertoire_id, 'inventory.pgn', NOW))
    for line_id, moves, trained_color, start_fen in lines:
        database.execute('INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) '
                         'VALUES(%s,%s,%s,%s,%s,%s,%s)',
                         (line_id, repertoire_id, line_id, trained_color, start_fen, json.dumps(moves), NOW))


def publish_graph(database, repertoire_id, generation=1):
    lines = tuple(dict(row) for row in database.execute_native('SELECT * FROM repertoire_lines WHERE repertoire_id=%s',
                                                               (repertoire_id,)).fetchall())
    steps = build_graph(GraphInput(repertoire_id, lines, 6))
    stage_graph_steps(database, steps, generation)
    for step in steps:
        database.execute_native('INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) '
                                "VALUES(%s,%s,'prefix',%s,%s,'2026-10-09') ON CONFLICT DO NOTHING",
                                (step.card_id, repertoire_id, step.starting_fen, json.dumps(step.moves)))
        database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s) ON CONFLICT DO NOTHING',
                                 (repertoire_id, step.card_id))
    publish_graph_generation(database, repertoire_id, generation)


def claim(database, repertoire_id=None, *, kind=inventory.TASK_KIND):
    token = str(uuid.uuid4())
    row = database.execute_native(
        "UPDATE background_tasks SET state='leased',lease_token=%s,lease_expires_at='2999-01-01' "
        "WHERE kind=%s AND deduplication_key=%s AND state IN ('queued','retrying') RETURNING *",
        (token, kind, repertoire_id or 'all')).fetchone()
    if row is None:
        return None
    return {**dict(row), 'payload': json.loads(row['payload_json'])}


def run_to_completion(repertoire_id, *, maximum=1000):
    slices = 0
    durations = []
    while slices < maximum:
        with postgres_store.connection() as database:
            task = claim(database, repertoire_id)
        if task is None:
            return slices, durations
        started = time.perf_counter()
        inventory.execute_inventory_slice(task)
        durations.append(time.perf_counter()-started)
        slices += 1
    raise AssertionError(f'Inventory failed to finish in {maximum} slices: {repertoire_id}')


def verify_issue108_upgrade_restart_replay_and_coverage(dsn):
    os.environ['TEMPO_DATABASE_WRITE_URL'] = dsn
    os.environ['TEMPO_DATABASE_READ_URL'] = dsn
    # The existing main schema is the upgrade input; schema 40 must schedule one
    # compact sweep and preserve every existing card and repertoire row.
    with psycopg.connect(dsn) as raw:
        for migration in sorted(MIGRATIONS.glob('[0-9][0-9][0-9]_*.sql')):
            if int(migration.name[:3]) > 39:
                break
            raw.execute(migration.read_text(), prepare=False)
        raw.execute('INSERT INTO settings(id) VALUES(1)')
        seed(raw, 'a', [('a-line', ['e2e4','e7e5','g1f3'], 'white', chess.STARTING_FEN),
                        ('a-end', ['e2e4','c7c5'], 'white', chess.STARTING_FEN)])
        seed(raw, 'b', [('b-line', ['e2e4','e7e5','g1f3'], 'white', chess.STARTING_FEN)])
    apply_migrations(dsn)
    with postgres_store.connection() as database:
        assert database.execute_native('SELECT COUNT(*) FROM repertoire_lines').fetchone()[0] == 3
        assert database.execute_native('SELECT COUNT(*) FROM background_tasks WHERE kind=%s',
                                        (inventory.RECONCILE_KIND,)).fetchone()[0] == 1
        publish_graph(database, 'a')
        publish_graph(database, 'b')
        generation = inventory.request_inventory_in_transaction(database, 'a')
        task_before = dict(database.execute_native('SELECT * FROM background_tasks WHERE kind=%s AND deduplication_key=%s',
                                                    (inventory.TASK_KIND, 'a')).fetchone())
        assert inventory.request_inventory_in_transaction(database, 'a') == generation
        task_after = dict(database.execute_native('SELECT * FROM background_tasks WHERE id=%s', (task_before['id'],)).fetchone())
        assert task_before == task_after, 'Duplicate requests reset a task'
    for _ in range(3):
        with postgres_store.connection() as database:
            sweep = claim(database,kind=inventory.RECONCILE_KIND)
        assert sweep is not None
        inventory.execute_reconcile_slice(sweep)
    with postgres_store.connection() as database:
        assert claim(database,kind=inventory.RECONCILE_KIND) is None
    # Roll back an entire route-registration/checkpoint commit, then replay the
    # exact claimed delivery. No partial membership may leak from the rollback.
    with postgres_store.connection() as database:
        first = claim(database, 'a')
    real_connection = inventory.connection
    @contextmanager
    def rollback_connection(**kwargs):
        with real_connection(**kwargs) as database:
            yield database
            raise RuntimeError('injected rollback')
    inventory.connection = rollback_connection
    try:
        try:
            inventory.execute_inventory_slice(first)
        except RuntimeError as error:
            assert str(error) == 'injected rollback'
        else:
            raise AssertionError('rollback injection did not run')
    finally:
        inventory.connection = real_connection
    with postgres_store.connection() as database:
        assert database.execute_native('SELECT COUNT(*) FROM inventory_generation_routes').fetchone()[0] == 0
    entering = threading.Event(); finished = threading.Event(); outcomes = []
    original_reader = inventory.background_read_connection
    @contextmanager
    def observed_reader():
        entering.set()
        with original_reader() as database:
            yield database
    inventory.background_read_connection = observed_reader
    def execute_worker():
        try:
            outcomes.append(inventory.execute_inventory_slice(first))
        except Exception as error:
            outcomes.append(error)
        finally:
            finished.set()
    worker = threading.Thread(target=execute_worker)
    try:
        with activity_gate.foreground():
            worker.start()
            assert entering.wait(2), 'Inventory did not reach foreground admission'
            with postgres_store.connection(read_only=True) as foreground:
                assert foreground.execute_native('SELECT COUNT(*) FROM inventory_generation_routes').fetchone()[0] == 0
            assert not finished.is_set(), 'Inventory ran through active foreground work'
        assert finished.wait(5), 'Inventory did not resume after foreground released'
        worker.join(1)
        assert outcomes == [True], outcomes
    finally:
        inventory.background_read_connection = original_reader
    assert inventory.execute_inventory_slice(first) is False, 'Duplicate delivery committed twice'
    # Restart after a committed cursor closes every pooled connection; the next
    # claim reconstructs progress solely from PostgreSQL.
    postgres_store.close_pools()
    child = subprocess.run([sys.executable, '-c',
        "from scripts.check_postgres_position_inventory import postgres_store,claim,inventory; "
        "with_database = postgres_store.connection(); db = with_database.__enter__(); "
        "task = claim(db,'a'); with_database.__exit__(None,None,None); "
        "assert task is not None; inventory.execute_inventory_slice(task); postgres_store.close_pools()"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert child.returncode == 0, f'Recreated worker failed: {child.stderr}'
    a_slices, a_durations = run_to_completion('a')
    b_slices, b_durations = run_to_completion('b')
    after_e4 = chess.Board(); after_e4.push_uci('e2e4')
    key = inventory.canonical_fen(after_e4.fen())
    with postgres_store.connection() as database:
        a = inventory.inventory_progress(database, 'a')['publication_id']
        b = inventory.inventory_progress(database, 'b')['publication_id']
        assert a and b and a != b
        assert database.execute_native('SELECT COUNT(*) FROM inventory_positions WHERE fen_key=%s', (key,)).fetchone()[0] == 1
        assert len(inventory.legal_replies(database, key)) == 20
        coverage = {row['move_uci']: row for row in inventory.route_coverage(database, a, 'a-line', 1)}
        assert coverage['e7e5']['coverage'] == 'route' and coverage['e7e5']['authored']
        assert coverage['c7c5']['coverage'] == 'missing' and not coverage['c7c5']['authored']
        assert coverage['c7c5']['legal_reply_count'] == 20
        ending = {row['move_uci']: row for row in inventory.route_coverage(database, a, 'a-end', 1)}
        assert ending['c7c5']['authored'] and ending['c7c5']['coverage'] == 'missing'
        assert inventory.source_evidence(database, inventory.source_cohort_key(after_e4.fen(),source_id='fixture',
            model_version='v1',schema_version='v1',rating_cohort='supported')) is None
        # Cache provenance is independently owned and survives deleting a repertoire.
        database.execute_native('INSERT INTO position_cohort_evidence(fen_key,source_id,model_version,schema_version,rating_cohort) '
                                "VALUES(%s,'fixture','v1','v1','supported')", (key,))
        database.execute_native("DELETE FROM repertoires WHERE id='b'")
        assert database.execute_native('SELECT COUNT(*) FROM position_cohort_evidence WHERE fen_key=%s', (key,)).fetchone()[0] == 1
        # Source mutation makes the old snapshot explicitly stale. A worker
        # delivering old input must not replace its publication pointer.
        database.execute_native("UPDATE repertoire_lines SET moves_json='[\"d2d4\",\"d7d5\",\"c2c4\"]' WHERE id='a-line'")
        assert inventory.inventory_progress(database, 'a')['stale']
        stale_generation = inventory.request_inventory_in_transaction(database, 'a')
    with postgres_store.connection() as database:
        stale_task = claim(database, 'a')
        database.execute_native("DELETE FROM repertoire_lines WHERE id='a-end'")
    assert inventory.execute_inventory_slice(stale_task) is False
    with postgres_store.connection() as database:
        assert inventory.inventory_progress(database, 'a')['publication_id'] == a
        publish_graph(database, 'a', 2)
    run_to_completion('a')
    with postgres_store.connection() as database:
        current = inventory.inventory_progress(database, 'a')
        assert current['publication_id'] != stale_generation and not current['stale']
        assert database.execute_native("SELECT COUNT(*) FROM inventory_generation_routes WHERE line_id='a-end'").fetchone()[0] == 0
        assert database.execute_native('SELECT COUNT(*) FROM inventory_generations WHERE repertoire_id=%s', ('a',)).fetchone()[0] == 1
    print(f'PASS issue108 upgrade/restart/rollback/replay/shared-cache/source-fences: a={a_slices} b={b_slices} '
          f'max_delivery_ms={max(a_durations+b_durations)*1000:.2f}', flush=True)


def verify_issue108_transpositions_duplicates_long_routes(dsn):
    first = ['g1f3','g8f6','g2g3','g7g6','f1g2','f8g7','e1g1']
    second = ['g2g3','g7g6','g1f3','g8f6','f1g2','f8g7','e1g1']
    with postgres_store.connection() as database:
        seed(database.raw, 'transpose', [('t-one',first,'white',chess.STARTING_FEN),
             ('t-two',second,'white',chess.STARTING_FEN), ('t-duplicate',first,'white',chess.STARTING_FEN)])
        publish_graph(database, 'transpose')
        # An unauthored reply at a custom opponent position reaches a published
        # learner response on the other route, with no cross-repertoire credit.
        board = chess.Board(); board.push_uci('e2e4')
        seed(database.raw, 'cross', [('cross-terminal',[],'white',board.fen())])
        publish_graph(database, 'cross')
        long_moves = ['g1f3','g8f6','f3g1','f6g8']*64
        seed(database.raw, 'large', [('large-line',long_moves,'white',chess.STARTING_FEN)])
        publish_graph(database, 'large')
        after_c5 = chess.Board(); after_c5.push_uci('e2e4'); after_c5.push_uci('c7c5')
        seed(database.raw, 'supported', [('supported-main',['e2e4','e7e5','g1f3'],'white',chess.STARTING_FEN),
             ('supported-transpose',['g1f3'],'white',after_c5.fen())])
        publish_graph(database, 'supported')
    run_to_completion('transpose'); run_to_completion('cross'); run_to_completion('supported')
    original_segment = inventory.inventory_segment
    observed_sizes = []
    def measured_segment(start_fen,moves,*args,**kwargs):
        observed_sizes.append(len(moves))
        return original_segment(start_fen,moves,*args,**kwargs)
    inventory.inventory_segment = measured_segment
    try:
        large_slices, durations = run_to_completion('large')
    finally:
        inventory.inventory_segment = original_segment
    assert max(observed_sizes) <= inventory.SEGMENT_PLIES
    with postgres_store.connection() as database:
        generation = inventory.inventory_progress(database,'transpose')['publication_id']
        route_ids = database.execute_native('SELECT route_id FROM inventory_generation_routes WHERE generation_id=%s '
                                            'AND line_id IN (%s,%s)', (generation,'t-one','t-duplicate')).fetchall()
        assert route_ids[0][0] == route_ids[1][0], 'Duplicate branches recalculated'
        positions = inventory.published_positions(database,'transpose')['positions']
        assert len({row['fen_key'] for row in positions}) == len(positions)
        supported = inventory.inventory_progress(database,'supported')['publication_id']
        support = {row['move_uci']:row for row in inventory.route_coverage(database,supported,'supported-main',1)}
        assert not support['c7c5']['authored'] and not support['c7c5']['route_response']
        assert support['c7c5']['transposition_response'] and support['c7c5']['coverage']=='transposition'
        route_page = inventory.published_routes(database,'transpose',limit=1)
        assert len(route_page['routes']) == 1 and route_page['next_line_id']
        next_page = inventory.published_routes(database,'transpose',after_line_id=route_page['next_line_id'],limit=1)
        assert next_page['routes'][0]['line_id'] != route_page['routes'][0]['line_id']
        occurrences = inventory.route_occurrences(database,generation,'t-one',limit=2)
        assert len(occurrences)==2 and occurrences[0]['ply']==0
        cross = inventory.inventory_progress(database,'cross')['publication_id']
        assert all(row['coverage']=='missing' for row in inventory.route_coverage(database,cross,'cross-terminal',0))
        before = len(observed_sizes)
        same = inventory.request_inventory_in_transaction(database,'large')
        assert same == inventory.inventory_progress(database,'large')['publication_id']
    assert run_to_completion('large')[0] == 0
    assert len(observed_sizes) == before
    print(f'PASS issue108 transpositions/duplicates/long-route bounds: slices={large_slices} '
          f'max_plies={max(observed_sizes)} max_delivery_ms={max(durations)*1000:.2f} unchanged_replay=0', flush=True)



def verify_issue108_scope_delta_and_card_authority(dsn):
    with postgres_store.connection() as database:
        seed(database.raw, 'scoped', [('scope-keep',['e2e4','e7e5','g1f3'],'white',chess.STARTING_FEN),
             ('scope-change',['d2d4','d7d5','c2c4'],'white',chess.STARTING_FEN)])
        publish_graph(database,'scoped')
    run_to_completion('scoped')
    with postgres_store.connection() as database:
        initial = inventory.inventory_progress(database,'scoped')['publication_id']
        kept_route = database.execute_native('SELECT route_id FROM inventory_generation_routes WHERE generation_id=%s AND line_id=%s',
                                             (initial,'scope-keep')).fetchone()[0]
        database.execute_native("UPDATE repertoire_lines SET moves_json='[\"c2c4\",\"e7e5\",\"g1f3\"]' WHERE id='scope-change'")
        publish_graph(database,'scoped',2)
    traversed = []
    original = inventory._traverse_slice
    def observed(task):
        traversed.append(task['payload']['route_id'])
        return original(task)
    inventory._traverse_slice = observed
    try:
        run_to_completion('scoped')
    finally:
        inventory._traverse_slice = original
    assert kept_route not in traversed, 'Unchanged route replayed'
    with postgres_store.connection() as database:
        current = inventory.inventory_progress(database,'scoped')['publication_id']
        assert database.execute_native('SELECT route_id FROM inventory_generation_routes WHERE generation_id=%s AND line_id=%s',
                                       (current,'scope-keep')).fetchone()[0] == kept_route
        # Change the scope boundary to after e4/e5/Nf3. Certificate metadata is
        # explicitly published; saved PGN routes and card history are preserved.
        revision = database.execute_native("SELECT scope_source_revision FROM repertoires WHERE id='scoped'").fetchone()[0]
        database.execute_native("INSERT INTO canonical_prefix_previews(id,repertoire_id,moves_json,expected_revision,source_revision,state,created_at) "
            "VALUES('inventory-preview','scoped','[\"e2e4\",\"e7e5\",\"g1f3\"]',0,%s,'ready',%s)",(revision,NOW))
        database.execute_native("DELETE FROM repertoire_lines WHERE id='scope-change'")
        database.execute_native("UPDATE repertoires SET canonical_prefix_moves_json='[\"e2e4\",\"e7e5\",\"g1f3\"]',"
            "canonical_prefix_revision=1,canonical_prefix_preview_id='inventory-preview' WHERE id='scoped'")
        inventory.request_inventory_in_transaction(database,'scoped')
    run_to_completion('scoped')
    with postgres_store.connection() as database:
        current = inventory.inventory_progress(database,'scoped')['publication_id']
        occurrences = inventory.route_occurrences(database,current,'scope-keep')
        assert [row['ply'] for row in occurrences] == [3], occurrences
        assert occurrences[0]['opponent'] == 1
        assert inventory.route_coverage(database,current,'scope-keep',1) == []
        assert len(inventory.published_positions(database,'scoped')['positions']) == 1
    print('PASS issue108 scope/delta: unchanged route reused, prior-scope memberships removed',flush=True)



def verify_issue108_fresh_install_has_no_upgrade_task(admin_dsn):
    database_name = 'tempo_inventory_fresh_'+uuid.uuid4().hex[:12]
    with psycopg.connect(admin_dsn,autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database_name)))
    dsn = psycopg.conninfo.make_conninfo(admin_dsn,dbname=database_name)
    try:
        apply_migrations(dsn)
        with psycopg.connect(dsn) as database:
            assert database.execute('SELECT COUNT(*) FROM background_tasks WHERE kind=%s',
                                    (inventory.RECONCILE_KIND,)).fetchone()[0] == 0
            assert database.execute('SELECT COUNT(*) FROM repertoires').fetchone()[0] == 0
        print('PASS issue108 fresh install schedules no unnecessary upgrade sweep',flush=True)
    finally:
        with psycopg.connect(admin_dsn,autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database_name)))


def main():
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Position inventory proof requires a disposable PostgreSQL instance')
    admin_dsn = os.getenv('TEMPO_INVENTORY_TEST_ADMIN_URL', 'postgresql://postgres@postgres:5432/postgres')
    database_name = 'tempo_inventory_'+uuid.uuid4().hex[:12]
    with psycopg.connect(admin_dsn,autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database_name)))
    dsn = psycopg.conninfo.make_conninfo(admin_dsn, dbname=database_name)
    try:
        verify_issue108_fresh_install_has_no_upgrade_task(admin_dsn)
        verify_issue108_upgrade_restart_replay_and_coverage(dsn)
        verify_issue108_transpositions_duplicates_long_routes(dsn)
        verify_issue108_scope_delta_and_card_authority(dsn)
    finally:
        postgres_store.close_pools()
        with psycopg.connect(admin_dsn,autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database_name)))
        print('PASS issue108 owned database cleanup', flush=True)

if __name__ == '__main__':
    main()
