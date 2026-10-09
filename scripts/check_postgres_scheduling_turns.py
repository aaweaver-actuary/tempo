"""Real persisted fairness, short contention and checkpoint replay proof."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import postgres_store
from app.database import background_connection
from app.services import durable_tasks
from app.services.activity_gate import activity_gate, BackgroundAdmissionDeferred


KINDS = ('opening_graph_rebuild', 'game_derivation_positions',
         'repertoire_priority', 'opening_segmentation', 'game_sync_record')


def proof_scheduling_turns(parent_database_url):
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Scheduling proof requires an explicitly disposable PostgreSQL instance')
    identity = 'tempo_turns_' + uuid.uuid4().hex
    database_url = make_conninfo(parent_database_url, dbname=identity)
    created = False
    postgres_store.close_pools()
    try:
        with psycopg.connect(parent_database_url, autocommit=True) as parent:
            parent.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(identity)))
            created = True
        from apply_postgres_migrations import apply_migrations
        from check_postgres_graph_retention import owned_admission_scope
        apply_migrations(database_url)
        with psycopg.connect(database_url) as database:
            database.execute('INSERT INTO settings(id) VALUES(1)')
        with patch.dict(os.environ, {'TEMPO_DATABASE_WRITE_URL': database_url,
                                    'TEMPO_DATABASE_READ_URL': database_url}), owned_admission_scope(identity):
            clock = datetime(2026, 10, 9, 0, tzinfo=timezone.utc)
            with patch.object(durable_tasks, '_now', return_value=clock):
                for kind in KINDS:
                    durable_tasks.enqueue_task(kind, kind, {'cursor': 0},
                        priority=40 if kind == KINDS[0] else 130, foreground=False)
                with psycopg.connect(database_url) as database:
                    # Representative imported-game stage backlog with newer,
                    # numerically higher-priority requests in the same class.
                    for kind, count in (('game_derivation_positions', 2143), ('game_derivation_compare', 2014)):
                        database.execute("INSERT INTO background_tasks(id,kind,deduplication_key,priority,"
                            "state,phase,payload_json,next_attempt_at,created_at,updated_at) "
                            "SELECT %s||number,%s,%s||number,1,'queued','indexed','{\"cursor\":0}',"
                            "'2026-10-09T00:00:00+00:00','2026-10-09T00:01:00+00:00',"
                            "'2026-10-09T00:01:00+00:00' FROM generate_series(1,%s) number",
                            (kind, kind, kind, count))
                allowed = (*KINDS, 'game_derivation_compare')
                durations = []
                observed = []
                for index in range(14):
                    if index == 1:
                        with activity_gate.foreground():
                            try:
                                durable_tasks.claim_task(allowed_kinds=allowed)
                            except BackgroundAdmissionDeferred:
                                pass
                            else:
                                raise AssertionError('Foreground work did not retain scheduling intent')
                        with psycopg.connect(database_url) as foreground:
                            foreground.execute("SELECT * FROM background_scheduling_turns WHERE lane='durable' FOR UPDATE")
                            started = time.perf_counter()
                            assert durable_tasks.claim_task(allowed_kinds=allowed) is None
                            assert time.perf_counter()-started < 0.25
                    postgres_store.close_pools()
                    started = time.perf_counter()
                    claimed = durable_tasks.claim_task(allowed_kinds=allowed)
                    assert claimed is not None
                    durations.append(time.perf_counter()-started)
                    observed.append(claimed['kind'])
                    with background_connection() as database:
                        assert durable_tasks.advance_task_slice_in_transaction(
                            database, claimed, next_phase='slice',
                            next_payload={'cursor': claimed['payload']['cursor']+1})
                    with background_connection() as database:
                        assert not durable_tasks.complete_task_slice_in_transaction(database, claimed)
                expected = [KINDS[index] for index in (0, 1, 0, 1, 2, 3, 4)] * 2
                assert observed == expected, observed
                with psycopg.connect(database_url) as database:
                    rows = database.execute("SELECT kind,payload_json FROM background_tasks WHERE deduplication_key=kind").fetchall()
                    cursors = {kind: json.loads(payload)['cursor'] for kind, payload in rows}
                    assert cursors == dict(zip(KINDS, (4, 4, 2, 2, 2))), cursors
                assert max(durations) < 0.25, durations
                print('PASS test_postgres_persisted_scheduling_turns_advance_games_with_foreground_contention_restart_and_replay; '
                      f'4157 queued game stages; max claim including pool reopen {max(durations):.4f}s')
    finally:
        postgres_store.close_pools()
        if created:
            with psycopg.connect(parent_database_url, autocommit=True) as parent:
                parent.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(identity)))


if __name__ == '__main__':
    proof_scheduling_turns(os.environ['TEMPO_SCHEDULING_PROOF_URL'])
