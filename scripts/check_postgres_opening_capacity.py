"""Native PR197 admission proofs; run only on disposable PostgreSQL infrastructure."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date
import argparse
import json
import os
from pathlib import Path
import sys
from threading import Barrier
import time
import uuid

import chess
import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from scripts.apply_postgres_migrations import apply_migrations
from app import main, postgres_store
from app.queue_position_lock import lock_queue_date_for_position
from app.services import durable_tasks, postgres_queue_refresh as worker
from app.services.opening_graph import GraphInput, build_graph

DAY = date.today().isoformat()
ADMIN_DSN = 'postgresql://postgres@postgres:5432/postgres'


def seed():
    with postgres_store.connection() as database:
        database.execute("INSERT INTO settings(id,new_cards_per_day,tactics_new_per_day,study_new_per_day) VALUES(1,10,0,0)")
        database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('rep','Capacity','proof',?)", (DAY,))
        lines = []
        for prefix_move in ('e2e4', 'd2d4'):
            prefix_board = chess.Board()
            prefix_board.push_uci(prefix_move)
            for reply_index, reply in enumerate(sorted(prefix_board.legal_moves, key=lambda move: move.uci())[:4]):
                board = prefix_board.copy()
                board.push(reply)
                moves = [prefix_move, reply.uci()]
                for _ in range(3):
                    move = sorted(board.legal_moves, key=lambda candidate: candidate.uci())[0]
                    moves.append(move.uci())
                    board.push(move)
                line_id = f'{prefix_move}-{reply_index}'
                line = dict(id=line_id, start_fen=chess.STARTING_FEN, moves_json=json.dumps(moves), trained_color='white')
                lines.append(line)
                database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,'rep',?,'white',?,?,?)", (line_id, line_id, line['start_fen'], line['moves_json'], DAY))
        steps = build_graph(GraphInput('rep', tuple(lines), 1))
        for step in steps:
            database.execute("INSERT OR IGNORE INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type) VALUES(?,'rep',?,?,?,'locked',?,'opening')", (step.card_id, 'prefix' if step.segment_kind == 'prefix' else 'response', step.starting_fen, json.dumps(step.moves), DAY))
            database.execute("INSERT OR IGNORE INTO repertoire_cards(repertoire_id,card_id) VALUES('rep',?)", (step.card_id,))
            database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json) VALUES('rep',1,?,?,?,?,?,?,?,'white',?,?,?,?)", (step.line_id, step.decision_index, step.card_id, step.parent_card_id, step.decision_fen_key, step.starting_fen, json.dumps(step.moves), step.segment_kind, step.first_decision_index, step.last_decision_index, json.dumps(step.decision_fen_keys)))
        database.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES('rep',1,'ready',?)", (DAY,))
        main._unlock_eligible_opening_cards(database, DAY)
    return steps


def snapshot():
    with postgres_store.connection(read_only=True) as database:
        return {
            'queue': [tuple(row) for row in database.execute('SELECT card_id,position,cycle,admission_repertoire_id FROM daily_queue ORDER BY id')],
            'cards': [tuple(row) for row in database.execute('SELECT id,state,introduced_at,due_date,interval_days,ease,repetitions,lapses,fsrs_card_json,stability FROM cards ORDER BY id')],
            'reviews': [dict(row) for row in database.execute('SELECT * FROM reviews ORDER BY id')],
        }


def admit(proposal, *, background=True):
    with postgres_store.connection(background=background) as database:
        worker._admit_one_prioritized_opening(database, DAY, proposal)


def apply_plan():
    plan = worker._prepare_prioritized_openings(DAY)
    for proposal in plan:
        admit(proposal)
    return plan


def introduce_parents():
    os.environ['OPENING_CAPACITY_FRONTIER_EXPANSION'] = '0'
    assert len(apply_plan()) == 2
    os.environ['OPENING_CAPACITY_FRONTIER_EXPANSION'] = '1'
    plan = worker._prepare_prioritized_openings(DAY)
    assert len(plan) == 8 and all('capacity_route' in proposal for proposal in plan)
    return plan


def overlap(proposals):
    """Observe actual advisory-lock waiters before releasing a held date lock.

    Direct writer transactions use their ordinary connection timeout; worker-budget
    denial/retry is tested separately without changing production timeout settings.
    """
    barrier = Barrier(len(proposals) + 1)
    def attempt(proposal):
        with postgres_store.connection() as database:
            backend_pid = database.execute_native('SELECT pg_backend_pid()').fetchone()[0]
            barrier.wait(timeout=5)
            worker._admit_one_prioritized_opening(database, DAY, proposal)
            return backend_pid
    with ThreadPoolExecutor(max_workers=len(proposals)) as executor:
        with postgres_store.connection() as controller:
            lock_queue_date_for_position(controller, DAY)
            futures = [executor.submit(attempt, proposal) for proposal in proposals]
            barrier.wait(timeout=5)
            deadline = time.monotonic() + 3
            with postgres_store.connection(read_only=True) as observer:
                while True:
                    waiting = observer.execute_native("SELECT count(*) FROM pg_locks WHERE NOT granted AND locktype='advisory'").fetchone()[0]
                    if waiting:
                        break
                    assert time.monotonic() < deadline, 'Admission never reached the real date lock'
        backend_pids = [future.result(timeout=5) for future in futures]
    assert len(set(backend_pids)) == len(proposals), backend_pids


def assert_frontier(steps, expected_count=10):
    state = snapshot()
    expected_ids = {step.card_id for step in steps if step.decision_index <= 1}
    queued_ids = [row[0] for row in state['queue']]
    assert len(queued_ids) == expected_count and len(set(queued_ids)) == expected_count, state
    assert set(queued_ids) <= expected_ids
    assert len({row[1] for row in state['queue']}) == expected_count
    for card_id, lifecycle, introduced, *_ in state['cards']:
        assert (lifecycle, introduced) == (('learning', DAY) if card_id in queued_ids else ('locked', None))
    assert not state['reviews']


def test_postgres_capacity_concurrent_refreshes_preserve_quota_and_nonrecursive_frontier():
    steps = seed()
    os.environ['OPENING_CAPACITY_FRONTIER_EXPANSION'] = '1'
    plan = worker._prepare_prioritized_openings(DAY)
    assert len(plan) == 10 and all('capacity_route' not in item for item in plan[:2])
    # Two independently prepared refresh plans overlap at every admission boundary.
    other_plan = worker._prepare_prioritized_openings(DAY)
    assert plan == other_plan
    for left, right in zip(plan, other_plan):
        overlap([left, right])
    assert_frontier(steps)
    before = snapshot()
    apply_plan()
    assert snapshot() == before
    # With spare capacity, introduction alone must still not expose grandchildren.
    with postgres_store.connection() as database:
        database.execute('UPDATE settings SET new_cards_per_day=20 WHERE id=1')
    assert worker._prepare_prioritized_openings(DAY) == []
    assert_frontier(steps)


def test_postgres_capacity_competing_cards_share_last_quota_slot():
    steps = seed()
    plan = introduce_parents()
    # Two distinct children were planned while eight slots were available.
    # A committed limit change leaves one slot; the writer must recount under lock.
    with postgres_store.connection() as database:
        database.execute('UPDATE settings SET new_cards_per_day=3 WHERE id=1')
    overlap(plan[:2])
    assert_frontier(steps, 3)


def test_postgres_capacity_stale_proposal_rejected_without_side_effects(rejection):
    seed()
    proposal = introduce_parents()[0]
    candidate = proposal['card_id']
    parent = proposal['capacity_route']['parent_card_id']
    with postgres_store.connection() as database:
        if rejection == 'generation':
            database.execute('UPDATE opening_graph_publications SET generation=2')
        elif rejection == 'publication':
            database.execute("UPDATE opening_graph_publications SET state='failed'")
        elif rejection == 'parent':
            database.execute('UPDATE cards SET archived=1 WHERE id=?', (parent,))
        elif rejection == 'archived':
            database.execute('UPDATE cards SET archived=1 WHERE id=?', (candidate,))
        elif rejection == 'blocked':
            database.execute("INSERT INTO repertoire_integrity_issues(id,repertoire_id,kind,signature,created_at,updated_at) VALUES('block','rep','invalid_source','proof',?,?)", (DAY, DAY))
            database.execute("INSERT INTO repertoire_integrity_card_blocks(repertoire_id,card_id,issue_id,scan_generation,published_at) VALUES('rep',?,'block','proof',?)", (candidate, DAY))
        elif rejection == 'introduced':
            worker._admit_one_prioritized_opening(database, DAY, proposal)
        elif rejection == 'membership':
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('other','Other','proof',?)", (DAY,))
            database.execute("UPDATE cards SET repertoire_id='other' WHERE id=?", (candidate,))
            database.execute('DELETE FROM repertoire_cards WHERE card_id=?', (candidate,))
        elif rejection == 'quota':
            database.execute('UPDATE settings SET new_cards_per_day=3 WHERE id=1')
            other = next(item for item in worker._prepare_prioritized_openings(DAY) if item['card_id'] != candidate)
            worker._admit_one_prioritized_opening(database, DAY, other)
        elif rejection == 'disabled':
            os.environ['OPENING_CAPACITY_FRONTIER_EXPANSION'] = '0'
        elif rejection == 'study_only':
            database.execute('UPDATE settings SET study_only_mode=1 WHERE id=1')
        else:
            raise AssertionError(rejection)
    before = snapshot()
    admit(proposal)
    assert snapshot() == before, rejection


def test_postgres_capacity_duplicate_rollback_and_worker_checkpoint_replay():
    seed()
    proposal = introduce_parents()[0]
    before = snapshot()
    try:
        with postgres_store.connection(background=True) as database:
            worker._admit_one_prioritized_opening(database, DAY, proposal)
            assert database.execute('SELECT 1 FROM daily_queue WHERE card_id=?', (proposal['card_id'],)).fetchone()
            raise RuntimeError('intentional rollback')
    except RuntimeError as error:
        assert str(error) == 'intentional rollback'
    assert snapshot() == before
    overlap([proposal, proposal])
    admitted = snapshot()
    admit(proposal)
    assert snapshot() == admitted
    payload = {'queue_date': DAY, '_queue_phase': 'prioritized_opening_item', 'opening_plan': [proposal], 'opening_index': 0}
    with postgres_store.connection() as database:
        durable_tasks.enqueue_task_in_transaction(database, 'daily_queue', 'current', payload)
    task = durable_tasks.claim_task(kind='daily_queue')
    assert task and worker.execute_postgres_queue_refresh_slice(task)
    assert snapshot() == admitted
    assert not worker.execute_postgres_queue_refresh_slice(task), 'Old lease replay must be rejected'
    assert snapshot() == admitted
    resumed = durable_tasks.claim_task(kind='daily_queue')
    assert resumed and resumed['payload']['opening_index'] == 1


def test_postgres_capacity_background_lock_denial_is_atomic_and_retryable():
    seed()
    proposal = introduce_parents()[0]
    before = snapshot()
    with postgres_store.connection() as controller:
        lock_queue_date_for_position(controller, DAY)
        with ThreadPoolExecutor(max_workers=1) as executor:
            denied = executor.submit(admit, proposal)
            try:
                denied.result(timeout=3)
                raise AssertionError('Expected production background lock-budget denial')
            except psycopg.errors.LockNotAvailable:
                pass
    assert snapshot() == before
    admit(proposal)
    assert len(snapshot()['queue']) == 3


def test_postgres_capacity_flag_rollback_shared_owner_and_game_miss_exception():
    steps = seed()
    plan = introduce_parents()
    with postgres_store.connection() as database:
        database.execute("INSERT INTO repertoires(id,name,source_name,created_at,new_cards_per_day) VALUES('shared','Shared','proof',?,1)", (DAY,))
        database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) SELECT 'shared',id FROM cards")
        database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) SELECT id||'-shared','shared',name,trained_color,start_fen,moves_json,created_at FROM repertoire_lines WHERE repertoire_id='rep'")
        database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json) SELECT 'shared',generation,line_id||'-shared',decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json FROM opening_graph_steps WHERE repertoire_id='rep'")
        database.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES('shared',1,'ready',?)", (DAY,))
    before_schedules = [(row[0], *row[3:]) for row in snapshot()['cards']]
    alternate = {**plan[0], 'repertoire_id': 'shared', 'capacity_route': {
        **plan[0]['capacity_route'], 'repertoire_id': 'shared',
        'line_id': plan[0]['capacity_route']['line_id'] + '-shared'}}
    overlap([plan[0], alternate])
    for proposal in plan:
        admit(proposal)
    assert_frontier(steps)
    owners = [row[3] for row in snapshot()['queue']]
    assert owners.count('shared') <= 1 and owners.count('rep') <= 10
    with postgres_store.connection() as database:
        database.execute("UPDATE repertoires SET new_cards_per_day=? WHERE id='rep'", (owners.count('rep'),))
    assert [(row[0], *row[3:]) for row in snapshot()['cards']] == before_schedules
    os.environ['OPENING_CAPACITY_FRONTIER_EXPANSION'] = '0'
    before = snapshot()
    apply_plan()
    assert snapshot() == before
    # Ordinary real-game obligations remain admissible above the normal cap.
    grandchild = next(step.card_id for step in steps if step.decision_index == 2)
    with postgres_store.connection() as database:
        database.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES('game','lichess','proof',?,'rapid',1,'white','1-0',?,'[]')", (DAY + 'T00:00:00+00:00', chess.STARTING_FEN))
        database.execute("INSERT INTO repertoire_decision_events_legacy(id,game_id,repertoire_id,card_id,ply,fen_key,expected_uci,actual_uci,outcome,played_at,updated_at) VALUES('miss','game','rep',?,1,'proof','e2e4','d2d4','miss',?,?)", (grandchild, DAY + 'T00:00:00+00:00', DAY))
    miss_plan = worker._prepare_prioritized_openings(DAY)
    assert len(miss_plan) == 1 and miss_plan[0]['reason'] == main.MISS_REASON
    admit(miss_plan[0])
    assert len(snapshot()['queue']) == 11
    assert not snapshot()['reviews']


@contextmanager
def isolated_database(template_name):
    database_name = 'tempo_capacity_' + uuid.uuid4().hex[:12]
    with psycopg.connect(ADMIN_DSN, autocommit=True) as administrator:
        administrator.execute(sql.SQL('CREATE DATABASE {} TEMPLATE {}').format(sql.Identifier(database_name), sql.Identifier(template_name)))
    os.environ['TEMPO_DATABASE_WRITE_URL'] = f'postgresql://postgres@postgres:5432/{database_name}'
    os.environ['TEMPO_DATABASE_READ_URL'] = os.environ['TEMPO_DATABASE_WRITE_URL']
    os.environ['OPENING_CAPACITY_FRONTIER_EXPANSION'] = '1'
    try:
        yield
    finally:
        postgres_store.close_pools()
        with psycopg.connect(ADMIN_DSN, autocommit=True) as administrator:
            administrator.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database_name)))


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--case', help='Run one named proof (including all its parameter cases)')
    arguments = parser.parse_args()
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Capacity qualification requires disposable PostgreSQL infrastructure')
    template_name = 'tempo_capacity_template_' + uuid.uuid4().hex[:12]
    tests = [test_postgres_capacity_concurrent_refreshes_preserve_quota_and_nonrecursive_frontier,
             test_postgres_capacity_competing_cards_share_last_quota_slot,
             test_postgres_capacity_stale_proposal_rejected_without_side_effects,
             test_postgres_capacity_duplicate_rollback_and_worker_checkpoint_replay,
             test_postgres_capacity_background_lock_denial_is_atomic_and_retryable,
             test_postgres_capacity_flag_rollback_shared_owner_and_game_miss_exception]
    selected = [test for test in tests if not arguments.case or test.__name__ == arguments.case]
    if not selected:
        raise RuntimeError('No qualification cases matched')
    with psycopg.connect(ADMIN_DSN, autocommit=True) as administrator:
        administrator.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(template_name)))
    try:
        apply_migrations(f'postgresql://postgres@postgres:5432/{template_name}')
        for test in selected:
            parameters = ('generation', 'publication', 'parent', 'archived', 'blocked', 'introduced', 'membership', 'quota', 'disabled', 'study_only') if test == test_postgres_capacity_stale_proposal_rejected_without_side_effects else (None,)
            for parameter in parameters:
                started = time.monotonic()
                with isolated_database(template_name):
                    test(parameter) if parameter else test()
                print(f'PASS {test.__name__}' + (f'[{parameter}]' if parameter else '') + f' ({time.monotonic() - started:.3f}s)', flush=True)
    finally:
        postgres_store.close_pools()
        with psycopg.connect(ADMIN_DSN, autocommit=True) as administrator:
            administrator.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(template_name)))


if __name__ == '__main__':
    run()
