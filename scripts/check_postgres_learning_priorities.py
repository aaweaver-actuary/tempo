"""Native #157 proofs on an exclusively owned disposable helper database."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event
import time
import uuid

import chess
import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import postgres_store, learning_priority_commands, repertoire_commands, pgn_import_commands
from app.command_gateway import execute_command, read_operation
from app.learning_priorities import LearningPriorityUpdate, read_learning_priority_snapshot, update_learning_priority
from app.schema_version import POSTGRES_SCHEMA_VERSION
from app.services.pgn import parse_pgn
import check_postgres_graph_retention as fixtures
from apply_postgres_migrations import apply_migrations

COMMAND = 'repertoires.learning_priority.update'
PREFERENCE_COLUMNS = ('learning_priority_weight', 'learning_priority_active', 'learning_priority_revision')


def snapshot():
    with postgres_store.connection(read_only=True) as database:
        return read_learning_priority_snapshot(database).document()


def save(policy_id, weight=2, active=True, revision=1, operation_id=None):
    operation_id = operation_id or uuid.uuid4().hex
    payload = {'policy_id': policy_id, 'request': {'learning_priority_weight': weight,
               'learning_priority_active': active, 'expected_revision': revision}}
    result = execute_command(operation_id, COMMAND, payload)
    return result, read_operation(operation_id, command_name=COMMAND)


def unchanged_state():
    """Every table except command receipts; strip only the three new preference columns."""
    with postgres_store.connection(read_only=True) as database:
        tables = database.execute_native("SELECT tablename FROM pg_tables WHERE schemaname='public' "
                                         "AND tablename NOT IN ('operation_receipts','tempo_schema_migrations') ORDER BY tablename").fetchall()
        contents = {}
        for row in tables:
            table = row[0]
            records = database.raw.execute(sql.SQL('SELECT row_to_json(record)::text FROM {} record').format(sql.Identifier(table))).fetchall()
            documents = [json.loads(record[0]) for record in records]
            if table == 'repertoires':
                for document in documents:
                    for column in PREFERENCE_COLUMNS:
                        document.pop(column, None)
            contents[table] = sorted(json.dumps(document, sort_keys=True) for document in documents)
        return contents


def seed():
    now = datetime.now(timezone.utc).isoformat()
    with postgres_store.connection() as database:
        for policy_id in ('italian', 'ruy'):
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)", (policy_id, policy_id, policy_id+'.pgn', now))
            database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,'Line','white',?,'[\"e2e4\",\"e7e5\"]',?)", (policy_id+'-line',policy_id,chess.STARTING_FEN,now))
        database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,fsrs_card_json,introduced_at) "
                         "VALUES('shared','italian','prefix',?,'[\"e2e4\"]','learning',?,'{}',?)", (chess.STARTING_FEN,now[:10],now))
        for policy_id in ('italian', 'ruy'):
            database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,'shared')", (policy_id,))
        database.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES('shared','correct',?,0,7)", (now,))
        database.execute("INSERT INTO daily_queue(queue_date,card_id,position,admission_repertoire_id,admission_kind) VALUES(?,'shared',3,'italian','review')", (now[:10],))
        database.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,published_at) VALUES('italian',1,?)", (now,))
        database.execute("INSERT INTO repertoire_coverage_runs(id,repertoire_id,status,settings_json,total_nodes,created_at,updated_at) VALUES('coverage','italian','complete','{}',1,?,?)", (now,now))
        database.execute("INSERT INTO repertoire_coverage_nodes(id,run_id,repertoire_id,fen,fen_key,ply,trained_color,routes_json,covered_replies_json,updated_at) VALUES('node','coverage','italian',?,?,1,'white','[]','[]',?)", (chess.STARTING_FEN,chess.STARTING_FEN,now))
        database.execute("INSERT INTO repertoire_coverage_candidates(node_id,move_uci,explorer_probability,maia_probability,blended_probability,source_state) VALUES('node','e7e5',0.4,0.5,0.45,'complete')")


def test_postgres_learning_priority_populated_upgrade_defaults_and_replay_preserve_study_state():
    before = unchanged_state()
    # Exclusively owned helper: recreate the pre-feature schema with populated rows.
    with psycopg.connect(os.environ['TEMPO_DATABASE_WRITE_URL']) as database:
        for column in PREFERENCE_COLUMNS:
            database.execute(sql.SQL('ALTER TABLE repertoires DROP COLUMN {}').format(sql.Identifier(column)))
        database.execute('DELETE FROM tempo_schema_migrations WHERE version=%s', (POSTGRES_SCHEMA_VERSION,))
    apply_migrations(os.environ['TEMPO_DATABASE_WRITE_URL'])
    apply_migrations(os.environ['TEMPO_DATABASE_WRITE_URL'])
    assert unchanged_state() == before
    assert all(entry['learning_priority_weight'] == 1 and entry['learning_priority_active'] and entry['revision'] == 1 for entry in snapshot()['policies'])
    for column, invalid in [('learning_priority_weight', -1), ('learning_priority_weight', float('nan')),
                            ('learning_priority_weight', float('inf')), ('learning_priority_active', 2), ('learning_priority_revision', 0)]:
        try:
            with psycopg.connect(os.environ['TEMPO_DATABASE_WRITE_URL']) as database:
                database.execute(sql.SQL("UPDATE repertoires SET {}=%s WHERE id='italian'").format(sql.Identifier(column)), (invalid,))
        except psycopg.IntegrityError:
            pass
        else:
            raise AssertionError(f'Database accepted invalid {column}')


def test_postgres_learning_priority_concurrent_same_policy_stale_and_independent_policy_updates():
    locked = Event()
    release = Event()
    blocker_pid = []
    def first_writer():
        with postgres_store.connection() as database:
            blocker_pid.append(database.raw.info.backend_pid)
            result = update_learning_priority(database, 'italian', LearningPriorityUpdate(
                learning_priority_weight=2, learning_priority_active=True, expected_revision=1), postgres=True)
            locked.set()
            assert release.wait(5), 'Concurrent proof did not release the owned row lock'
            return result
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(first_writer)
        try:
            assert locked.wait(5)
            second = executor.submit(save, 'italian', 3)
            deadline = time.monotonic() + 5
            while True:
                with psycopg.connect(os.environ['TEMPO_DATABASE_WRITE_URL']) as database:
                    waiting = database.execute('SELECT pid FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid))', (blocker_pid[0],)).fetchall()
                if waiting:
                    break
                assert time.monotonic() < deadline, 'Competing update never reached the real row lock'
                Event().wait(0.01)
            # A different policy remains writable while Italian's row is locked.
            assert save('ruy', 1.5)[0]['revision'] == 2
        finally:
            release.set()
        assert first.result(timeout=5)['revision'] == 2
        assert second.result(timeout=5)[1]['error']['status_code'] == 409
    assert snapshot()['policies'][0]['learning_priority_weight'] == 2


def test_postgres_learning_priority_restart_receipt_replay_noop_and_no_derived_side_effects():
    before = unchanged_state()
    operation_id = uuid.uuid4().hex
    result, receipt = save('italian', 0, False, 2, operation_id)
    assert result['revision'] == 3 and receipt['state'] == 'complete'
    assert unchanged_state() == before
    expected_snapshot = snapshot()
    environment = {**os.environ, 'TEMPO_LEARNING_PRIORITY_CHILD': 'snapshot'}
    child = subprocess.run([sys.executable, str(Path(__file__).resolve())], env=environment,
                           capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stderr
    assert json.loads(child.stdout) == expected_snapshot
    postgres_store.close_pools()
    assert save('italian', 0, False, 2, operation_id)[0] == result
    assert save('italian', 0, False, 3)[0] == result  # matching-revision no-op
    assert save('italian', 0, False, 2)[1]['error']['status_code'] == 409
    assert snapshot() == expected_snapshot
    assert unchanged_state() == before


def test_postgres_learning_priority_native_reimport_rename_delete_and_new_identity_defaults():
    pgn = '[Event "Priority"]\n\n1. e4 e5 2. Nf3 Nc6 3. Bc4 *'
    games, lines = parse_pgn(pgn)
    def admit(color):
        payload = pgn_import_commands.prepare_import_payload('priority.pgn', color, 6, games, lines)
        result = execute_command(uuid.uuid4().hex, 'imports.pgn.admit', payload)
        assert result is not None
        return result['repertoire_id']
    white_id = admit('white')
    black_id = admit('black')
    assert white_id != black_id
    assert save(white_id, 2.5, False)[0]['revision'] == 2
    execute_command(uuid.uuid4().hex, 'repertoires.rename', {'repertoire_id': white_id, 'name': 'Renamed'})
    assert admit('white') == white_id
    entries = {entry['policy_id']: entry for entry in snapshot()['policies']}
    assert entries[white_id]['learning_priority_weight'] == 2.5 and not entries[white_id]['learning_priority_active']
    assert entries[white_id]['revision'] == 2 and entries[black_id]['learning_priority_weight'] == 1
    assert execute_command(uuid.uuid4().hex, 'repertoires.delete', {'repertoire_id': white_id, 'learned_cards': 'delete'}) is not None
    assert save(white_id, 4, True, 2)[1]['error']['status_code'] == 404
    new_id = admit('white')
    assert new_id != white_id
    entries = {entry['policy_id']: entry for entry in snapshot()['policies']}
    assert white_id not in entries and entries[new_id]['learning_priority_weight'] == 1


def main():
    fixtures.validate_disposable_database()
    with fixtures.owned_fixture_database():
        seed()
        for proof in (test_postgres_learning_priority_populated_upgrade_defaults_and_replay_preserve_study_state,
                      test_postgres_learning_priority_concurrent_same_policy_stale_and_independent_policy_updates,
                      test_postgres_learning_priority_restart_receipt_replay_noop_and_no_derived_side_effects,
                      test_postgres_learning_priority_native_reimport_rename_delete_and_new_identity_defaults):
            started = time.monotonic()
            proof()
            print(f'PASS {proof.__name__} ({time.monotonic()-started:.3f}s)', flush=True)


if __name__ == '__main__':
    if os.getenv('TEMPO_LEARNING_PRIORITY_CHILD') == 'snapshot':
        print(json.dumps(snapshot()))
    else:
        main()
