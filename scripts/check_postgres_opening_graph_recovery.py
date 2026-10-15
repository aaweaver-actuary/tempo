"""Real PostgreSQL regression for a failed first publication in study-only mode."""
from contextlib import contextmanager
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from unittest.mock import patch

import chess
import psycopg
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from app import postgres_store
from app.services import frozen_opening_order as frozen
from app.services import opening_graph_recovery as recovery
from app.services import postgres_queue_refresh as queue
from app.services.durable_tasks import enqueue_task_in_transaction, lock_current_slice
from app.services.frozen_maia_math import content_fingerprint
from app.services.opening_graph import GraphInput, build_graph
from app.services.postgres_opening_graph import stage_graph_steps, create_graph_cards

ROOT = Path(__file__).resolve().parents[1]


def docker(*arguments):
    return subprocess.run(['docker', *arguments], check=True, text=True, capture_output=True).stdout.strip()


@contextmanager
def disposable_postgres():
    token = secrets.token_hex(5)
    postgres_name = 'tempo-caro-test-pg-' + token
    redis_name = 'tempo-caro-test-redis-' + token
    password = secrets.token_urlsafe(24)
    owned_containers = []
    try:
        owned_containers.append(docker('run', '-d', '--name', postgres_name, '--label', 'tempo.owner=caro-recovery',
               '-e', 'POSTGRES_PASSWORD=' + password, '-p', '127.0.0.1::5432',
               '--tmpfs', '/var/lib/postgresql', 'postgres:18.6-trixie'))
        owned_containers.append(docker('run', '-d', '--name', redis_name, '--label', 'tempo.owner=caro-recovery',
               '-p', '127.0.0.1::6379', 'redis:7.4-alpine'))
        postgres_port = docker('port', postgres_name, '5432').rsplit(':', 1)[1]
        redis_port = docker('port', redis_name, '6379').rsplit(':', 1)[1]
        url = f'postgresql://postgres:{password}@127.0.0.1:{postgres_port}/postgres'
        for _ in range(100):
            try:
                with psycopg.connect(url):
                    break
            except psycopg.OperationalError:
                time.sleep(.1)
        else:
            raise RuntimeError('Disposable PostgreSQL did not start')
        spec = importlib.util.spec_from_file_location('recovery_migrations', ROOT / 'scripts/apply_postgres_migrations.py')
        migrations = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migrations)
        migrations.apply_migrations(url)
        environment = {'TEMPO_DATABASE_READ_URL': url, 'TEMPO_DATABASE_WRITE_URL': url,
                       'TEMPO_REDIS_URL': f'redis://127.0.0.1:{redis_port}/0'}
        with patch.dict(os.environ, environment):
            postgres_store.close_pools()
            from app.services.redis_admission_gate import client
            client.cache_clear()
            yield url
            postgres_store.close_pools()
            client.cache_clear()
    finally:
        for name in owned_containers:
            subprocess.run(['docker', 'rm', '-f', name], check=False, capture_output=True)


def seed_failed_publication():
    day = datetime.now(timezone.utc).date().isoformat()
    repertoire = 'caro-' + secrets.token_hex(4)
    moves = ['e2e4', 'c7c6', 'd2d4', 'd7d5', 'e4e5', 'c8f5', 'g1f3', 'e7e6', 'f1e2', 'c6c5', 'e1g1']
    line = {'id': repertoire + '-line', 'start_fen': chess.STARTING_FEN, 'moves_json': json.dumps(moves), 'trained_color': 'white', 'learner_decision_count': 3}
    alternative = {**line, 'id': repertoire + '-line-other', 'moves_json': json.dumps(moves[:5] + ['c8g4', 'g1f3', 'e7e6', 'f1e2', 'c6c5', 'e1g1'])}
    lines = (line, alternative)
    steps = build_graph(GraphInput(repertoire, lines, 3))
    with postgres_store.connection() as database:
        database.execute_native('INSERT INTO settings(id,initial_depth,new_cards_per_day,study_only_mode) VALUES(1,3,1,1) ON CONFLICT(id) DO UPDATE SET initial_depth=3,new_cards_per_day=1,study_only_mode=1')
        database.execute_native('INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,%s,%s)', (repertoire, repertoire, 'fixture', day))
        database.execute_native('INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)', (line['id'], repertoire, 'Fixture', 'white', chess.STARTING_FEN, line['moves_json'], day))
        database.execute_native('INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)', (alternative['id'], repertoire, 'Other', 'white', chess.STARTING_FEN, alternative['moves_json'], day))
        stage_graph_steps(database, steps, 1)
        create_graph_cards(database, steps, day)
        for step in steps:
            database.execute_native('INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(%s,%s) ON CONFLICT DO NOTHING', (repertoire, step.card_id))
        task = enqueue_task_in_transaction(database, 'opening_graph_rebuild', repertoire, {'repertoire_id': repertoire, 'local_day': day, 'after_card_id': ''}, priority=40)
        database.execute_native("UPDATE background_tasks SET state='failed',phase='publish',attempt_count=5,last_error='terminating connection due to transaction timeout' WHERE id=%s", (task['id'],))
        parent = steps[0].card_id
        database.execute_native("UPDATE cards SET introduced_at=%s,state='learning' WHERE id=%s", (day, parent))
        database.execute_native("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval,source_kind) VALUES(%s,'again',%s,0,1,'study')", (parent, day))
        ranks = []
        ranked_steps = list({step.card_id: step for step in reversed(steps) if step.segment_kind != 'prefix'}.values())
        for ordinal, step in enumerate(ranked_steps, 1):
            card = dict(database.execute_native('SELECT * FROM cards WHERE id=%s', (step.card_id,)).fetchone())
            ranks.append({'id': step.card_id, 'rank': ordinal, 'fingerprint': content_fingerprint(card, [repertoire]), 'memberships': [repertoire]})
        plan = {'run_id': repertoire, 'checksum': repertoire, 'ranks': ranks}
        frozen.stage_run(database, plan, day)
        frozen.stage_ranks(database, plan, ranks)
        frozen.activate_run(database, plan)
        unrelated = enqueue_task_in_transaction(database, 'opening_graph_rebuild', repertoire + '-other', {'repertoire_id': repertoire + '-other'}, priority=1)
    return repertoire, task, steps, unrelated, day


def test_failed_first_publication_recovery_unlocks_practiced_parent_and_admits_maia_rank(disposable_postgres):
    repertoire, task, steps, unrelated, day = seed_failed_publication()
    prepared = recovery.preflight(task['id'], repertoire, 1, recovery.target_id())
    with postgres_store.connection(read_only=True) as database:
        before_reviews = [dict(row) for row in database.execute_native('SELECT * FROM reviews ORDER BY id').fetchall()]
        before_unrelated = dict(database.execute_native('SELECT * FROM background_tasks WHERE id=%s', (unrelated['id'],)).fetchone())
        before_ranks = [dict(row) for row in database.execute_native('SELECT * FROM frozen_opening_ranks ORDER BY run_id,ordinal').fetchall()]
    before_evidence = recovery.capture_evidence(repertoire, day)
    result = recovery.recover(prepared, day=day)
    assert result['state'] == 'complete'
    with postgres_store.connection() as database:
        from app.services.queue_application import _unlock_eligible_opening_cards
        _unlock_eligible_opening_cards(database, day)
        assert database.execute_native('SELECT state FROM cards WHERE id=%s', (steps[1].card_id,)).fetchone()[0] == 'new'
        assert database.execute_native('SELECT state FROM cards WHERE id=%s', (steps[2].card_id,)).fetchone()[0] == 'locked'
    page = queue._prepare_study_opening_page(day, {})
    eligible = [item for item in page['items'] if item['repertoire_id'] == repertoire]
    assert len(eligible) == 1 and eligible[0]['card_id'] == steps[5].card_id, {'eligible': eligible, 'steps': [(s.line_id, s.decision_index, s.card_id) for s in steps]}
    assert eligible[0]['frozen_rank'] < next(rank['ordinal'] for rank in before_ranks if rank['card_id'] == steps[1].card_id)
    with postgres_store.connection() as database:
        assert queue._admit_one_prioritized_opening(database, day, eligible[0])
    remaining = queue._prepare_study_opening_page(day, {})
    assert not [item for item in remaining['items'] if item['repertoire_id'] == repertoire]
    preservation = recovery.verify_preservation(before_evidence, recovery.capture_evidence(repertoire, day))
    assert preservation['passed'], preservation
    with postgres_store.connection(read_only=True) as database:
        assert database.execute_native('SELECT study_only_mode FROM settings WHERE id=1').fetchone()[0] == 1
        assert [dict(row) for row in database.execute_native('SELECT * FROM reviews ORDER BY id').fetchall()] == before_reviews
        assert dict(database.execute_native('SELECT * FROM background_tasks WHERE id=%s', (unrelated['id'],)).fetchone()) == before_unrelated
        assert [dict(row) for row in database.execute_native('SELECT * FROM frozen_opening_ranks ORDER BY run_id,ordinal').fetchall()] == before_ranks
        assert database.execute_native("SELECT COUNT(*) FROM queue_attempt_origins WHERE issued_as_head=1").fetchone()[0] == 0


def test_recovery_refuses_mismatched_source_and_target(disposable_postgres):
    repertoire, task, steps, unrelated, day = seed_failed_publication()
    with unittest.TestCase().assertRaisesRegex(ValueError, 'target identity'):
        recovery.preflight(task['id'], repertoire, 1, 'wrong-target')
    with postgres_store.connection() as database:
        database.execute_native("UPDATE repertoire_lines SET moves_json='[]' WHERE repertoire_id=%s", (repertoire,))
    with unittest.TestCase().assertRaisesRegex(ValueError, 'differs from current source'):
        recovery.preflight(task['id'], repertoire, 1, recovery.target_id())


def test_scoped_recovery_claim_preserves_publish_and_fences_other_tasks(disposable_postgres):
    repertoire, task, steps, unrelated, day = seed_failed_publication()
    with postgres_store.connection() as database:
        with unittest.TestCase().assertRaisesRegex(ValueError, 'identity or generation'):
            recovery.claim_exact_task(database, unrelated['id'], 'opening_graph_rebuild', repertoire, 1, resume_failed=True, day=day)
        claimed = recovery.claim_exact_task(database, task['id'], 'opening_graph_rebuild', repertoire, 1, resume_failed=True, day=day)
        assert claimed['phase'] == 'publish'
        assert lock_current_slice(database, claimed)
        assert not lock_current_slice(database, {**claimed, 'lease_token': 'stale'})
        with unittest.TestCase().assertRaisesRegex(ValueError, 'leased'):
            recovery.claim_exact_task(database, task['id'], 'opening_graph_rebuild', repertoire, 1, resume_failed=True, day=day)


def run_cases(url):
    for case in (test_failed_first_publication_recovery_unlocks_practiced_parent_and_admits_maia_rank,
                 test_recovery_refuses_mismatched_source_and_target,
                 test_scoped_recovery_claim_preserves_publish_and_fences_other_tasks):
        case(url)
        print('PASS ' + case.__name__, flush=True)


if __name__ == '__main__':
    if sys.argv[1:] == ['--disposable']:
        with disposable_postgres() as url:
            run_cases(url)
    elif not sys.argv[1:] and os.environ.get('TEMPO_TEST_INSTANCE') == 'disposable':
        from psycopg import sql
        database_name = 'tempo_caro_recovery_' + secrets.token_hex(6)
        admin_url = 'postgresql://postgres@postgres:5432/postgres'
        with psycopg.connect(admin_url, autocommit=True) as administrator:
            administrator.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(database_name)))
        try:
            url = 'postgresql://postgres@postgres:5432/' + database_name
            spec = importlib.util.spec_from_file_location('recovery_migrations', ROOT / 'scripts/apply_postgres_migrations.py')
            migrations = importlib.util.module_from_spec(spec); spec.loader.exec_module(migrations)
            migrations.apply_migrations(url)
            with patch.dict(os.environ, {'TEMPO_DATABASE_READ_URL': url, 'TEMPO_DATABASE_WRITE_URL': url}):
                postgres_store.close_pools()
                run_cases(url)
        finally:
            postgres_store.close_pools()
            with psycopg.connect(admin_url, autocommit=True) as administrator:
                administrator.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(database_name)))
    else:
        raise SystemExit('Use --disposable for task-owned containers, or the owning disposable PostgreSQL suite')
