"""Native study-only opening admission proof; exclusively disposable databases."""

# Preserve direct-script imports after relocation from scripts/.
import sys
from pathlib import Path
_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))
sys.path.insert(0, str(_REPOSITORY / "backend"))

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
import re
from pathlib import Path
import sys
import time
from threading import Barrier, Event
from statistics import median
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from app import main, tasks
from tempo_legacy_database.adapters import postgres_store  # registers the real worker commands
from app.command_gateway import execute_command
from app.services import durable_tasks, postgres_queue_refresh as worker
from database.tooling.apply_postgres_migrations import apply_migrations
from database.tests.check_postgres_opening_capacity import seed, snapshot, DAY


def enable(limit=2):
    steps = seed()
    with postgres_store.connection() as database:
        database.execute('UPDATE settings SET study_only_mode=1,new_cards_per_day=? WHERE id=1', (limit,))
    return steps


def claim_fixture_delivery():
    """Administer a fixture lease without testing scheduler wall-clock budgets.

    The production claim SQL, gates, lease and generation semantics still run
    on real PostgreSQL. Only fixture administration uses an ordinary connection;
    queue handler publications and contention tests retain background deadlines.
    """

    def administer(operation, *, label):
        with postgres_store.connection() as database:
            return operation(database)
    with patch.object(durable_tasks, 'submit_background_write', administer):
        return durable_tasks.claim_task(allowed_kinds=('daily_queue',))


def refresh(key='study-introductions'):
    durable_tasks.warm_completion_sql()
    durable_tasks.enqueue_task('daily_queue', key, {'queue_date': DAY})
    for slice_index in range(300):
        task = claim_fixture_delivery()
        if task is None:
            return slice_index
        worker.execute_postgres_queue_refresh_slice(task)
    raise AssertionError('Queue refresh did not terminate within 300 bounded slices')


def test_postgres_study_only_introduces_openings_without_analysis():
    enable()
    before = snapshot()
    assert not before['queue']
    with patch.object(worker, '_prepare_prioritized_openings', side_effect=AssertionError('Optional ranking ran')):
        slices = refresh()
    after = snapshot()
    assert len(after['queue']) == 2, f"Expected two fresh openings, got {len(after['queue'])}"
    assert after['reviews'] == before['reviews']
    assert all(card[3:] == dict((row[0], row[3:]) for row in before['cards'])[card[0]]
               for card in after['cards'])
    with postgres_store.connection(read_only=True) as database:
        assert database.execute("SELECT state FROM queue_projections WHERE queue_date=?", (DAY,)).fetchone()[0] == 'ready'
    refresh('study-introductions-replay')
    assert snapshot() == after
    print(json.dumps({'before_candidates': len(before['cards']), 'before_admissions': 0,
                      'after_admissions': len(after['queue']), 'slices': slices}))


def test_postgres_study_only_review_unlocks_child_preserves_fsrs_and_receipts():
    steps = enable(10)
    refresh()
    queued = snapshot()['queue']
    assert len(queued) == 2
    root_id = queued[0][0]
    with postgres_store.connection(read_only=True) as database:
        entry_id = database.execute('SELECT id FROM daily_queue WHERE card_id=?', (root_id,)).fetchone()[0]
    assert execute_command(uuid4().hex, 'queue.issue_head',
        {'entry_id': entry_id, 'queue_date': DAY, 'card_id': root_id, 'expected_revision': 1})['issued']
    assert main.queue_window(1)['cards'][0]['id'] == root_id
    operation_id = uuid4().hex
    review = {'card_id': root_id, 'review': {'outcome': 'correct', 'queue_entry_id': entry_id,
              'expected_revision': 1, 'attempt_id': 'study-intro-real-review'}}
    saved = execute_command(operation_id, 'cards.review', review)
    assert saved['persisted']
    before_refresh = snapshot()
    refresh('after-real-review')
    after_refresh = snapshot()
    children = {step.card_id for step in steps if step.parent_card_id == root_id}
    assert children and children.issubset({row[0] for row in after_refresh['queue']})
    grandchildren = {step.card_id for step in steps if step.parent_card_id in children}
    assert grandchildren.isdisjoint({row[0] for row in after_refresh['queue']})
    assert after_refresh['reviews'] == before_refresh['reviews']
    root_before = next(row for row in before_refresh['cards'] if row[0] == root_id)
    assert next(row for row in after_refresh['cards'] if row[0] == root_id) == root_before
    postgres_store.close_pools()
    assert execute_command(operation_id, 'cards.review', review) == saved
    assert snapshot()['reviews'] == after_refresh['reviews']


def test_postgres_study_only_daily_limits_and_empty_sets(limit):
    enable(limit)
    refresh()
    assert len(snapshot()['queue']) == min(limit, 2)
    before = snapshot()
    refresh('limit-replay')
    assert snapshot() == before
    with postgres_store.connection() as database:
        database.execute('UPDATE cards SET archived=1')
    refresh('empty')


def test_postgres_study_only_safety_exclusions_do_not_starve_later_roots(exclusion):
    steps = enable(2)
    root = next(step.card_id for step in steps if step.parent_card_id is None)
    with postgres_store.connection() as database:
        if exclusion in ('archived', 'pending_validation'):
            database.execute(f'UPDATE cards SET {exclusion}=1 WHERE id=?', (root,))
        elif exclusion == 'deleted':
            database.execute('INSERT INTO deleted_cards(card_id,deleted_at) VALUES(?,?)', (root, DAY))
        elif exclusion == 'superseded':
            database.execute("UPDATE cards SET superseded_by='replacement' WHERE id=?", (root,))
        elif exclusion == 'invalid':
            database.execute("UPDATE cards SET moves_json='[\"e2e5\"]' WHERE id=?", (root,))
        elif exclusion == 'trained_color':
            database.execute("UPDATE cards SET trained_color='black' WHERE id=?", (root,))
        elif exclusion == 'integrity':
            database.execute("INSERT INTO repertoire_integrity_issues(id,repertoire_id,kind,signature,created_at,updated_at) VALUES('block','rep','invalid_source','block',?,?)", (DAY, DAY))
            database.execute("INSERT INTO repertoire_integrity_card_blocks(repertoire_id,card_id,issue_id,scan_generation,published_at) VALUES('rep',?,'block','proof',?)", (root, DAY))
    refresh()
    admitted_ids = {row[0] for row in snapshot()['queue']}
    assert root not in admitted_ids
    assert len(admitted_ids) == 1
    assert not snapshot()['reviews']


def test_postgres_study_only_checkpoint_restart_and_stale_replay():
    enable()
    key = 'checkpoint'
    durable_tasks.enqueue_task('daily_queue', key, {'queue_date': DAY})
    for _ in range(100):
        task = claim_fixture_delivery()
        if task['payload'].get('_queue_phase') == 'study_opening_item':
            break
        worker.execute_postgres_queue_refresh_slice(task)
    else:
        raise AssertionError('Never reached checkpointed admission')
    assert worker.execute_postgres_queue_refresh_slice(task)
    assert len(snapshot()['queue']) == 1
    assert not worker.execute_postgres_queue_refresh_slice(task)
    postgres_store.close_pools()
    resumed = claim_fixture_delivery()
    assert resumed['payload']['study_opening_index'] == 1
    # Superseding a leased generation is cancellation, not a replay permission.
    durable_tasks.enqueue_task('daily_queue', key, {'queue_date': DAY})
    assert not worker.execute_postgres_queue_refresh_slice(resumed)
    for _ in range(100):
        claimed = claim_fixture_delivery()
        if claimed is None:
            break
        worker.execute_postgres_queue_refresh_slice(claimed)
    assert len(snapshot()['queue']) == 2
    assert len({row[0] for row in snapshot()['queue']}) == 2


def test_postgres_study_only_concurrent_refreshes_share_last_allowance():
    enable(2)
    page = worker._prepare_study_opening_page(DAY, {})
    assert len(page['items']) == 2
    with postgres_store.connection() as database:
        database.execute('UPDATE settings SET new_cards_per_day=1 WHERE id=1')
    barrier = Barrier(2)
    def admit(proposal):
        barrier.wait(timeout=5)
        with postgres_store.connection() as database:
            return worker._admit_one_prioritized_opening(database, DAY, proposal)
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(admit, page['items']))
    assert sum(bool(outcome) for outcome in outcomes) == 1
    assert len(snapshot()['queue']) == 1
    assert not snapshot()['reviews']


def test_postgres_study_only_shared_memberships_and_route_recheck():
    enable()
    page = worker._prepare_study_opening_page(DAY, {})
    proposal = page['items'][0]
    before = snapshot()
    with postgres_store.connection() as database:
        database.execute("UPDATE opening_graph_publications SET generation=2 WHERE repertoire_id='rep'")
    with postgres_store.connection() as database:
        assert not worker._admit_one_prioritized_opening(database, DAY, proposal)
    assert snapshot() == before
    with postgres_store.connection() as database:
        database.execute("UPDATE opening_graph_publications SET generation=1 WHERE repertoire_id='rep'")
        database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('shared','Shared','proof',?)", (DAY,))
        database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) SELECT 'shared-line','shared',name,trained_color,start_fen,moves_json,created_at FROM repertoire_lines ORDER BY id LIMIT 1")
        database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) SELECT 'shared',card_id FROM repertoire_cards WHERE repertoire_id='rep'")
        database.execute("INSERT INTO opening_graph_steps SELECT 'shared',generation,'shared-line',decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json FROM opening_graph_steps WHERE line_id=(SELECT id FROM repertoire_lines WHERE repertoire_id='rep' ORDER BY id LIMIT 1)")
        database.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES('shared',1,'ready',?)", (DAY,))
    refresh()
    assert len(snapshot()['queue']) == 2
    assert len({row[0] for row in snapshot()['queue']}) == 2


def test_postgres_study_only_blocked_pages_advance_in_stable_graph_order():
    enable(1)
    with postgres_store.connection() as database:
        root = database.execute("SELECT card_id FROM opening_graph_steps WHERE parent_card_id IS NULL ORDER BY line_id LIMIT 1").fetchone()[0]
        database.execute("UPDATE cards SET moves_json='[\"e2e5\"]' WHERE id=?", (root,))
        original_line = database.execute('SELECT id FROM repertoire_lines ORDER BY id LIMIT 1').fetchone()[0]
        for index in range(20):
            line_id = f'aaa-blocked-{index:02}'
            database.execute("INSERT INTO repertoire_lines SELECT ?,repertoire_id,name,trained_color,start_fen,moves_json,created_at FROM repertoire_lines WHERE id=?", (line_id, original_line))
            database.execute("INSERT INTO opening_graph_steps SELECT repertoire_id,generation,?,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json FROM opening_graph_steps WHERE line_id=?", (line_id, original_line))
    first_page = worker._prepare_study_opening_page(DAY, {})
    assert first_page['examined'] == 16 and not first_page['items'] and first_page['has_more']
    refresh()
    assert len(snapshot()['queue']) == 1
    assert snapshot()['queue'][0][0] != root


def test_postgres_study_only_real_game_miss_retains_quota_exception():
    steps = enable(0)
    descendant = next(step.card_id for step in steps if step.decision_index == 2)
    with postgres_store.connection() as database:
        database.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES('game','lichess','proof',?,'rapid',1,'white','1-0',?,'[]')", (DAY + 'T00:00:00+00:00', 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'))
        database.execute("INSERT INTO repertoire_decision_events_legacy(id,game_id,repertoire_id,card_id,ply,fen_key,expected_uci,actual_uci,outcome,played_at,updated_at) VALUES('miss','game','rep',?,1,'proof','e2e4','d2d4','miss',?,?)", (descendant, DAY + 'T00:00:00+00:00', DAY))
    refresh()
    assert [row[0] for row in snapshot()['queue']] == [descendant]
    assert not snapshot()['reviews']


def test_postgres_study_only_already_queued_card_does_not_consume_planned_allowance():
    enable(1)
    first = worker._prepare_study_opening_page(DAY, {})['items'][0]
    with postgres_store.connection() as database:
        database.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_kind) VALUES(?,?,0,'explicit')",
                         (DAY, first['card_id']))
    remaining = worker._prepare_study_opening_page(DAY, {})['items']
    assert len(remaining) == 1 and remaining[0]['card_id'] != first['card_id'], \
        'Already queued card starved the next eligible opening with unused allowance'
    refresh()
    assert len(snapshot()['queue']) == 2
    with postgres_store.connection(read_only=True) as database:
        assert database.execute('SELECT introduced_at FROM cards WHERE id=?', (first['card_id'],)).fetchone()[0] is None
        assert database.execute('SELECT COUNT(*) FROM cards WHERE introduced_at=?', (DAY,)).fetchone()[0] == 1


def test_postgres_study_only_foreground_contention_rolls_back_and_resumes():
    enable()
    page = worker._prepare_study_opening_page(DAY, {})
    proposal = page['items'][0]
    before = snapshot()
    from tempo_legacy_database.adapters.queue_position_lock import lock_queue_date_for_position
    from psycopg.errors import LockNotAvailable
    def background_admit():
        with postgres_store.connection(background=True) as database:
            worker._admit_one_prioritized_opening(database, DAY, proposal)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with postgres_store.connection() as foreground:
            lock_queue_date_for_position(foreground, DAY)
            blocked = pool.submit(background_admit)
            try:
                blocked.result(timeout=5)
            except LockNotAvailable:
                pass
            else:
                raise AssertionError('Background admission ignored foreground queue contention')
            assert foreground.execute('SELECT COUNT(*) FROM reviews').fetchone()[0] == 0
    assert snapshot() == before
    with postgres_store.connection(background=True) as database:
        assert worker._admit_one_prioritized_opening(database, DAY, proposal)
    assert len(snapshot()['queue']) == 1


def restart_proof(admin_url, database_name, stage):
    if not re.fullmatch(r'tempo_study_intros_restart_[0-9a-f]{32}', database_name or ''):
        raise RuntimeError('Restart proof requires a uniquely named disposable database')
    owned_url = make_conninfo(admin_url, dbname=database_name)
    if stage == 'prepare':
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database_name)))
        apply_migrations(owned_url)
    with patch.dict(os.environ, {'TEMPO_DATABASE_WRITE_URL': owned_url, 'TEMPO_DATABASE_READ_URL': owned_url,
                                'OPENING_CAPACITY_FRONTIER_EXPANSION': '0'}), \
            patch('app.services.queue_refresh_wakeup.wake_queue_refresh'):
        try:
            if stage == 'prepare':
                test_postgres_study_only_review_unlocks_child_preserves_fsrs_and_receipts()
                expected = json.loads(json.dumps(snapshot()))
                marker = durable_tasks.enqueue_task('daily_queue', 'native-restart-marker',
                    {'queue_date': DAY, 'expected_native_restart_state': expected})
                with postgres_store.connection() as database:
                    database.execute("UPDATE background_tasks SET state='complete' WHERE id=?", (marker['id'],))
            else:
                with postgres_store.connection(read_only=True) as database:
                    marker = database.execute("SELECT payload_json FROM background_tasks WHERE deduplication_key='native-restart-marker'").fetchone()
                    delivered = dict(database.execute("SELECT * FROM background_tasks WHERE deduplication_key='after-real-review'").fetchone())
                expected = json.loads(marker[0])['expected_native_restart_state']
                assert json.loads(json.dumps(snapshot())) == expected
                delivered['payload'] = json.loads(delivered.pop('payload_json'))
                # Replay the original completed delivery, rather than replacing
                # it with a fresh generation. The task fence must reject it.
                assert not worker.execute_postgres_queue_refresh_slice(delivered)
                assert json.loads(json.dumps(snapshot())) == expected
                print('PASS test_postgres_study_only_server_restart_preserves_introductions_reviews_and_replay')
        finally:
            postgres_store.close_pools()
            if stage == 'verify':
                with psycopg.connect(admin_url, autocommit=True) as admin:
                    admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database_name)))


def measure_foreground_with_bounded_preparation():
    """Small paired native queue-read measurement, not a production SLO claim."""
    enable(10)
    refresh()
    with postgres_store.connection(read_only=True) as database:
        head = database.execute('SELECT id,card_id FROM daily_queue ORDER BY position LIMIT 1').fetchone()
    assert execute_command(uuid4().hex, 'queue.issue_head',
        {'entry_id': head[0], 'queue_date': DAY, 'card_id': head[1], 'expected_revision': 1})['issued']
    main.queue_window(1)  # warm the identical foreground path before both cohorts
    before = snapshot()
    def reads():
        durations = []
        for _ in range(30):
            started = time.perf_counter()
            with worker.activity_gate.foreground():
                assert main.queue_window(1)['cards'][0]['id'] == head[1]
            durations.append((time.perf_counter() - started) * 1000)
        return durations
    control = reads()
    stop = Event()
    entered = Event()
    def prepare():
        pages = 0
        entered.set()
        while not stop.is_set():
            page = worker._prepare_study_opening_page(DAY, {})
            assert page['examined'] <= 16
            pages += 1
        return pages
    with ThreadPoolExecutor(max_workers=1) as pool:
        background = pool.submit(prepare)
        assert entered.wait(timeout=5)
        try:
            with_preparation = reads()
        finally:
            stop.set()
        pages = background.result(timeout=10)
    assert snapshot() == before
    def summary(values):
        return {'samples': len(values), 'median_ms': round(median(values), 3),
                'p95_ms': round(sorted(values)[28], 3), 'max_ms': round(max(values), 3)}
    print(json.dumps({'foreground_queue_control': summary(control),
                      'foreground_queue_with_bounded_preparation': summary(with_preparation),
                      'completed_background_pages': pages}))


@contextmanager
def owned_database(admin_url, template_name):
    database_name = 'tempo_study_intros_' + uuid4().hex
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE {}').format(
            sql.Identifier(database_name), sql.Identifier(template_name)))
    owned_url = make_conninfo(admin_url, dbname=database_name)
    with patch.dict(os.environ, {'TEMPO_DATABASE_WRITE_URL': owned_url,
                                'TEMPO_DATABASE_READ_URL': owned_url,
                                'OPENING_CAPACITY_FRONTIER_EXPANSION': '0'}), \
            patch('app.services.queue_refresh_wakeup.wake_queue_refresh'):
        try:
            yield
        finally:
            postgres_store.close_pools()
            with psycopg.connect(admin_url, autocommit=True) as admin:
                admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database_name)))


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case')
    parser.add_argument('--restart-stage', choices=['prepare', 'verify'])
    parser.add_argument('--restart-database')
    parser.add_argument('--measure', action='store_true')
    arguments = parser.parse_args()
    if os.environ.get('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Opening admission proof refuses non-disposable targets')
    admin_url = os.environ.get('TEMPO_STUDY_INTRO_TEST_ADMIN_URL', 'postgresql://postgres@postgres:5432/postgres')
    if arguments.restart_stage:
        restart_proof(admin_url, arguments.restart_database, arguments.restart_stage)
        return
    tests = [(test_postgres_study_only_introduces_openings_without_analysis, [None]),
             (test_postgres_study_only_review_unlocks_child_preserves_fsrs_and_receipts, [None]),
             (test_postgres_study_only_daily_limits_and_empty_sets, [0, 1, 2]),
             (test_postgres_study_only_safety_exclusions_do_not_starve_later_roots,
              ['archived', 'pending_validation', 'deleted', 'superseded', 'invalid', 'trained_color', 'integrity']),
             (test_postgres_study_only_checkpoint_restart_and_stale_replay, [None]),
             (test_postgres_study_only_concurrent_refreshes_share_last_allowance, [None]),
             (test_postgres_study_only_shared_memberships_and_route_recheck, [None]),
             (test_postgres_study_only_blocked_pages_advance_in_stable_graph_order, [None]),
             (test_postgres_study_only_real_game_miss_retains_quota_exception, [None]),
             (test_postgres_study_only_already_queued_card_does_not_consume_planned_allowance, [None]),
             (test_postgres_study_only_foreground_contention_rolls_back_and_resumes, [None])]
    selected = [(test, parameters) for test, parameters in tests if not arguments.case or test.__name__ == arguments.case]
    if not selected:
        raise RuntimeError('No native regression matched')
    template_name = 'tempo_study_intros_template_' + uuid4().hex
    with psycopg.connect(admin_url, autocommit=True) as admin:
        admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(template_name)))
    try:
        apply_migrations(make_conninfo(admin_url, dbname=template_name))
        if arguments.measure:
            with owned_database(admin_url, template_name):
                measure_foreground_with_bounded_preparation()
            return
        for test, parameters in selected:
            for parameter in parameters:
                started = time.monotonic()
                with owned_database(admin_url, template_name):
                    test(parameter) if parameter is not None else test()
                print(f'PASS {test.__name__}[{parameter}] ({time.monotonic() - started:.3f}s)', flush=True)
    finally:
        postgres_store.close_pools()
        with psycopg.connect(admin_url, autocommit=True) as admin:
            admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(template_name)))


if __name__ == '__main__':
    run()
