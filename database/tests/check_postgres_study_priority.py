"""Real study-priority proof. Standalone execution owns every container it uses."""
from __future__ import annotations

# Preserve direct-script imports after relocation from scripts/.
import sys
from pathlib import Path
_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))
sys.path.insert(0, str(_REPOSITORY / "backend"))

import argparse
from contextlib import contextmanager
from datetime import date
import hashlib
import json
import os
import re
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import threading
import time
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'scripts'))
CASES = ['test_postgres_priority_unlock_rejected_pages_and_restart',
         'test_postgres_priority_recovery_ignores_terminal_history_and_future_work',
         'test_postgres_priority_real_review_replay_and_loaded_latency',
         'test_postgres_priority_control_poll_does_not_wait_for_database',
         'test_postgres_priority_sessions_expire_independently_and_stale_control_denies_optional',
         'test_postgres_priority_index_only_rollback_preserves_accepted_reviews']
FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'


def docker(*arguments, environment=None):
    result = subprocess.run(['docker', *arguments], check=False, capture_output=True, text=True,
                            env=environment, timeout=60)
    if result.returncode:
        raise RuntimeError(f'Docker {arguments[0]} failed: {result.stderr[:400]}')
    return result.stdout.strip()


def percentiles(samples):
    ordered = sorted(samples)
    return {f'p{percent}': ordered[min(len(ordered)-1, int(len(ordered)*percent/100))]
            for percent in (50, 95, 99)}


def api_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


@contextmanager
def private_database(parent_url):
    identity = 'tempo_study_priority_' + uuid4().hex
    with psycopg.connect(parent_url, autocommit=True) as parent:
        # An environment flag alone never establishes disposable ownership.
        comment = parent.execute("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()").fetchone()[0]
        if comment != 'tempo-disposable-postgres-test':
            raise RuntimeError('Refusing a parent database without the disposable-runner marker')
        parent.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(identity)))
    try:
        yield make_conninfo(parent_url, dbname=identity)
    finally:
        with psycopg.connect(parent_url, autocommit=True) as parent:
            parent.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(identity)))


def _advance_unlock_checkpoint_attempt(*, force_timeout=False):
    from tempo_legacy_database.adapters import postgres_store
    from app.services import queue_application
    from app.services.postgres_queue_refresh import _ELIGIBILITY_PHASES
    with postgres_store.connection(background=True) as database:
        row=database.execute("SELECT payload_json FROM background_tasks WHERE id='priority-unlock-checkpoint' AND generation=1 AND lease_token='priority-proof' AND state='leased' FOR UPDATE").fetchone()
        assert row is not None
        payload=json.loads(row[0])
        if force_timeout:
            database.execute_native('SELECT pg_sleep(0.3)')
        cursor=queue_application._unlock_eligible_opening_cards(database,date.today().isoformat(),after_card_id=payload.get('after_card_id',''),batch_size=8)
        payload={'queue_date':date.today().isoformat(),'_queue_phase':'unlock_opening' if cursor else _ELIGIBILITY_PHASES[1],**({'after_card_id':cursor} if cursor else {})}
        assert database.execute("UPDATE background_tasks SET payload_json=?,phase=? WHERE id='priority-unlock-checkpoint' AND generation=1 AND lease_token='priority-proof' AND state='leased'",(json.dumps(payload),payload['_queue_phase'])).rowcount==1
        return cursor


def advance_unlock_checkpoint(*, force_timeout=False):
    """Exercise the production retry contract without extending any DB deadline."""
    from tempo_legacy_database.adapters import postgres_store
    def persisted():
        with postgres_store.connection(read_only=True) as database:
            row = database.execute("SELECT payload_json FROM background_tasks WHERE id='priority-unlock-checkpoint' AND generation=1 AND lease_token='priority-proof' AND state='leased'").fetchone()
            assert row is not None
            return row[0]
    original_payload = persisted()
    for attempt in range(4):
        try:
            return _advance_unlock_checkpoint_attempt(force_timeout=force_timeout and attempt == 0)
        except psycopg.errors.TransactionTimeout:
            assert persisted() == original_payload, 'Aborted slice advanced its durable checkpoint'
            destination = os.environ.get('TEMPO_PRIORITY_CHECKPOINT_RETRY_EVIDENCE')
            if destination:
                with open(destination, 'a') as output:
                    output.write(json.dumps({'attempt': attempt, 'forced': force_timeout and attempt == 0,
                        'checkpoint_unchanged': True, 'payload': json.loads(original_payload)})+'\n')
            if attempt == 3:
                raise
            time.sleep(0.05)  # Yield outside the transaction, as the owning task does.


_CHECKPOINT_CHILD = r"""
import json
import re
import sys
try:
    from database.tests.check_postgres_study_priority import advance_unlock_checkpoint
    print(json.dumps(advance_unlock_checkpoint()))
except Exception as error:
    frames = []
    traceback = error.__traceback__
    while traceback is not None:
        frame = traceback.tb_frame
        function = frame.f_code.co_name
        frames.append({'module': frame.f_globals.get('__name__', ''),
                       'function': '_module' if function == '<module>' else function,
                       'line': traceback.tb_lineno})
        frames = frames[-8:]
        traceback = traceback.tb_next
    def symbol(value, module=False):
        pattern = r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*' if module else r'[A-Za-z_][A-Za-z0-9_]*'
        return isinstance(value, str) and len(value) <= 96 and re.fullmatch(pattern, value) is not None
    if (symbol(type(error).__name__) and frames
            and all(symbol(frame['module'], True) and symbol(frame['function'])
                    and type(frame['line']) is int and 1 <= frame['line'] <= 10_000_000
                    for frame in frames)):
        print('CHECKPOINT_FAILURE ' + json.dumps({'class': type(error).__name__,
                                                 'frames': frames}))
    sys.exit(1)
"""


def resume_unlock_checkpoint(environment):
    """Expose only bounded symbolic failures, never raw child output or messages."""
    try:
        resumed = subprocess.run([sys.executable, '-c', _CHECKPOINT_CHILD],
            cwd=ROOT / 'scripts', env=environment, check=False,
            capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Checkpoint child timed out') from None
    if resumed.returncode == 0:
        return json.loads(resumed.stdout)
    generic = f'Checkpoint child failed (exit code {resumed.returncode})'
    if resumed.returncode < 0:
        raise RuntimeError(generic)
    diagnostic = None
    if (len(resumed.stdout) <= 4096
            and resumed.stdout.startswith('CHECKPOINT_FAILURE ')):
        try:
            record = json.loads(resumed.stdout[len('CHECKPOINT_FAILURE '):])
            def symbol(value, *, module=False):
                pattern = r'[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*' if module else r'[A-Za-z_][A-Za-z0-9_]*'
                return isinstance(value, str) and len(value) <= 96 and re.fullmatch(pattern, value) is not None
            if (type(record) is dict and set(record) == {'class', 'frames'}
                    and symbol(record['class']) and type(record['frames']) is list
                    and 1 <= len(record['frames']) <= 8
                    and all(type(frame) is dict and set(frame) == {'module', 'function', 'line'}
                            and symbol(frame['module'], module=True) and symbol(frame['function'])
                            and type(frame['line']) is int and 1 <= frame['line'] <= 10_000_000
                            for frame in record['frames'])):
                diagnostic = json.dumps(record, sort_keys=True)
        except (ValueError, TypeError, RecursionError):
            pass
    raise RuntimeError(generic + (': ' + diagnostic if diagnostic else ''))


def assert_monotonic_activity_renewal(server, namespace, *, script=None):
    """Actual Redis: a short pulse cannot retire another live foreground lease."""
    from app.services.redis_admission_gate import _REGISTER_FOREGROUND
    key = namespace + ':monotonic-renewal:' + uuid4().hex
    now = int(time.time() * 1000)
    try:
        registration = script if script is not None else _REGISTER_FOREGROUND
        server.eval(registration, 1, key, now, 'essential-pending', 30_000)
        server.eval(registration, 1, key, now + 1, 'held', 3000)
        server.eval(registration, 1, key, now + 2, 'held', 1)
        assert server.zscore(key, 'held') == now + 3001, 'An old short pulse shortened a held session'
        server.eval(registration, 1, key, now + 3, 'other-session', 1)
        lifetime = server.pttl(key)
        assert 29_000 <= lifetime <= 32_000, ('A short session changed the shared key lifetime', lifetime)
        assert server.zscore(key, 'essential-pending') == now + 30_000
        return {'name': 'test_postgres_priority_monotonic_activity_renewal', 'shared_key_pttl_ms': lifetime,
                'shorter_pulses_preserve_held_and_pending_leases': True}
    finally:
        server.delete(key)


def prepare_priority_control_fixture(namespace):
    """Use the existing bounded write client for direct invariant setup/edits.

    Production control polling keeps its separate 50 ms deadline. Fixture setup
    must not consume that deadline under a capped Redis scheduling period.
    """
    from app.services import redis_admission_gate
    shared = redis_admission_gate.client()
    return shared, assert_monotonic_activity_renewal(shared, namespace)


def _checkpoint_pythonpath(inherited):
    # Keep the owning image's source-package roots available to the fresh child.
    return os.pathsep.join([str(ROOT / 'backend'), str(ROOT / 'database'),
                            *(entry for entry in inherited.split(os.pathsep) if entry)])


HISTORICAL_TABLES = ('repertoires', 'cards', 'reviews', 'operation_receipts')
PRIORITY_INDEXES = ('idx_operation_retry_due', 'idx_operation_lease_due', 'idx_operation_queue_due')


def historical_migration_sources():
    """Resolve the actual registered sources and require the complete 1..49 prefix."""
    from database.tooling.apply_postgres_migrations import MIGRATIONS, migration_source, validate_migration_history
    files = sorted(path for path in MIGRATIONS.glob('[0-9][0-9][0-9]_*.sql')
                   if int(path.name[:3]) <= 49)
    validate_migration_history(files, [], expected_version=49)
    return [(path, migration_source(path)) for path in files]


def require_ledger(database, version):
    actual = [row[0] for row in database.execute(
        'SELECT version FROM tempo_schema_migrations ORDER BY version').fetchall()]
    if actual != list(range(1, version + 1)):
        raise RuntimeError(f'Expected exact migration ledger 1..{version}, got {actual}')


def table_contract(database, table):
    columns = database.execute("""SELECT column_name, data_type, udt_name, is_nullable,
        is_identity, identity_generation FROM information_schema.columns
        WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position""", (table,)).fetchall()
    foreign_keys = database.execute("""SELECT pg_get_constraintdef(c.oid)
        FROM pg_constraint c JOIN pg_class t ON t.oid=c.conrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE n.nspname='public' AND t.relname=%s AND c.contype='f'
        ORDER BY pg_get_constraintdef(c.oid)""", (table,)).fetchall()
    if not columns:
        raise RuntimeError(f'Missing copy table {table}')
    return columns, foreign_keys


def require_copy_contract(source, destination):
    for table in HISTORICAL_TABLES:
        if table_contract(source, table) != table_contract(destination, table):
            raise RuntimeError(f'Schema or foreign keys differ for {table}')


def historical_rows(database, receipt_ids):
    predicates = {
        'repertoires': ("id='priority'", ()),
        'cards': ("id LIKE 'review-%%'", ()),
        'reviews': ("card_id LIKE 'review-%%'", ()),
        'operation_receipts': ('operation_id = ANY(%s)', (receipt_ids,)),
    }
    rows = {}
    for table, (where, args) in predicates.items():
        key = 'operation_id' if table == 'operation_receipts' else 'id'
        query = sql.SQL('SELECT * FROM {} WHERE ' + where + ' ORDER BY {}').format(
            sql.Identifier(table), sql.Identifier(key))
        rows[table] = database.execute(query, args).fetchall()
    expected = {'repertoires': 1, 'cards': 120, 'reviews': 60, 'operation_receipts': 60}
    if {table: len(values) for table, values in rows.items()} != expected or len(set(receipt_ids)) != 60:
        raise RuntimeError('Synthetic history has missing or duplicate rows')
    return rows


def copy_historical_rows(source, destination, receipt_ids):
    require_copy_contract(source, destination)
    rows = historical_rows(source, receipt_ids)
    for table in HISTORICAL_TABLES:
        columns = [column[0] for column in table_contract(source, table)[0]]
        query = sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(
            sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, columns)),
            sql.SQL(',').join(sql.Placeholder() for _ in columns))
        with destination.cursor() as cursor:
            cursor.executemany(query, rows[table])
    # The card-insert trigger advances the destination repertoire revision. Restore
    # the exact copied parent values after its children have been inserted.
    columns = [column[0] for column in table_contract(source, 'repertoires')[0]]
    assignments = sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(c)) for c in columns)
    destination.execute(sql.SQL('UPDATE repertoires SET {} WHERE id=%s').format(assignments),
                        (*rows['repertoires'][0], 'priority'))
    require_historical_rows(destination, receipt_ids, rows)

    return rows


def require_historical_rows(database, receipt_ids, expected):
    if historical_rows(database, receipt_ids) != expected:
        raise RuntimeError('Historical copy differs from source')
    for table in HISTORICAL_TABLES:
        count = database.execute(sql.SQL('SELECT COUNT(*) FROM {}').format(sql.Identifier(table))).fetchone()[0]
        if count != len(expected[table]):
            raise RuntimeError(f'Unexpected additional rows in historical {table}')


def require_same_cluster(source, destination):
    query = 'SELECT system_identifier, pg_postmaster_start_time() FROM pg_control_system()'
    if source.execute(query).fetchone() != destination.execute(query).fetchone():
        raise RuntimeError('Historical database is not on the original disposable cluster')


def require_priority_indexes(database, present):
    found = [database.execute('SELECT to_regclass(%s)', ('public.' + name,)).fetchone()[0] is not None
             for name in PRIORITY_INDEXES]
    if found != [present] * len(PRIORITY_INDEXES):
        raise RuntimeError(f'Unexpected migration 49 indexes: {found}')


def study_snapshot(database):
    # Preserve the original overlap proof's whole-table fingerprints on the
    # latest-schema database, including rows outside the historical copy.
    return tuple(database.execute("SELECT md5(COALESCE(string_agg(row_to_json(item)::text,',' ORDER BY item.id),'')) FROM " + table + " item").fetchone()[0]
                 for table in ('cards', 'reviews')) + (
        database.execute('SELECT COUNT(*) FROM operation_receipts').fetchone()[0],)


def historical_rollback_proof(parent_url, owned_url, receipt_ids):
    from tempo_legacy_database.ddl.schema_version import POSTGRES_SCHEMA_VERSION
    sources = historical_migration_sources()
    with psycopg.connect(owned_url) as original:
        original_snapshot = study_snapshot(original)
    with private_database(parent_url) as historical_url:
        with psycopg.connect(historical_url) as historical:
            for version, (path, source) in enumerate(sources, 1):
                with historical.transaction():
                    historical.execute(source, prepare=False)
                    require_ledger(historical, version)
            require_priority_indexes(historical, True)
            with psycopg.connect(owned_url) as original:
                require_same_cluster(original, historical)
                require_ledger(original, POSTGRES_SCHEMA_VERSION)
                before = copy_historical_rows(original, historical, receipt_ids)
                historical.commit()
                historical.execute((ROOT / 'database/tempo_legacy_database/ddl/rollbacks/049_study_priority_recovery.sql').read_text(), prepare=False)
                require_ledger(historical, 48)
                require_priority_indexes(historical, False)
                require_historical_rows(historical, receipt_ids, before)
                if study_snapshot(original) != original_snapshot:
                    raise RuntimeError('Original study snapshot changed during rollback')
                historical.commit()
                with historical.transaction():
                    historical.execute(sources[-1][1], prepare=False)
                    require_ledger(historical, 49)
                    require_priority_indexes(historical, True)
                    require_historical_rows(historical, receipt_ids, before)
                require_ledger(original, POSTGRES_SCHEMA_VERSION)
                if historical_rows(original, receipt_ids) != before:
                    raise RuntimeError('Original accepted history changed')
                if study_snapshot(original) != original_snapshot:
                    raise RuntimeError('Original study snapshot changed during rollback proof')
    with psycopg.connect(owned_url) as original:
        if study_snapshot(original) != original_snapshot:
            raise RuntimeError('Original study snapshot changed during historical database cleanup')
    return {'schema_path': [49, 48, 49], 'accepted_reviews_retained': 60,
            'source_and_copy_exact_rows_unchanged': True, 'original_ledger_unchanged': True,
            'study_snapshot_unchanged': True}


def stop_owned_writers(processes, loader, stop_load):
    stop_load.set()
    if loader:
        loader.join(timeout=1)
        if loader.is_alive():
            raise RuntimeError('Owned background loader did not stop')
    for process in reversed(processes):
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    processes.clear()


def run_proof(parent_url, redis_url, evidence, *, graph_rows=1_400_000, receipt_rows=415_000, postgres_container=None):
    from database.tooling.apply_postgres_migrations import apply_migrations
    with private_database(parent_url) as owned_url:
        apply_migrations(owned_url, defense_zero=True)
        reader = 'priority_reader_' + secrets.token_hex(8)
        reader_password = secrets.token_urlsafe(24)
        configuration = dict(os.environ)
        configuration.update(TEMPO_DATABASE_READ_URL=owned_url, TEMPO_DATABASE_WRITE_URL=owned_url,
            TEMPO_REDIS_URL=redis_url, TEMPO_ADMISSION_NAMESPACE='tempo:priority:' + uuid4().hex,
            TEMPO_REDIS_NAMESPACE='tempo:priority:' + uuid4().hex + ':', TEMPO_COORDINATOR_MODE='external',
            TEMPO_TRAINING_TIMINGS='1', TEMPO_TEST_INSTANCE='disposable',
            PYTHONPATH=_checkpoint_pythonpath(configuration.get('PYTHONPATH', '')),
            TEMPO_PRIORITY_CHECKPOINT_RETRY_EVIDENCE=str(evidence / 'checkpoint-retries.jsonl'),
            TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS='250', TEMPO_POSTGRES_BACKGROUND_LOCK_TIMEOUT_MS='25')
        original = dict(os.environ)
        os.environ.update(configuration)
        from app import main
        from tempo_legacy_database.adapters import postgres_store
        from app.command_gateway import claim_recoverable_operation
        from app.services import queue_application, redis_admission_gate, study_priority
        from app.services.background_runtime import measure_training_timing
        from unittest.mock import patch
        saved_admission_keys = redis_admission_gate._FOREGROUND_KEY, redis_admission_gate._BACKGROUND_KEY
        saved_policy_keys = study_priority.POLICY_KEY, study_priority.GENERATION_KEY
        redis_admission_gate._FOREGROUND_KEY = configuration['TEMPO_ADMISSION_NAMESPACE'] + ':foreground'
        redis_admission_gate._BACKGROUND_KEY = configuration['TEMPO_ADMISSION_NAMESPACE'] + ':background'
        study_priority.POLICY_KEY = configuration['TEMPO_ADMISSION_NAMESPACE'] + ':study-policy'
        study_priority.GENERATION_KEY = study_priority.POLICY_KEY + ':generation'
        redis_admission_gate.client.cache_clear(); study_priority.control_client.cache_clear()
        processes = []
        handles = []
        results = {}; stop_load = threading.Event(); loader = None
        try:
            with psycopg.connect(owned_url) as database:
                database.execute('INSERT INTO settings(id,study_only_mode) VALUES(1,0)')
                database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('priority','Priority','synthetic',%s)", (date.today().isoformat(),))
                database.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(reader), sql.Literal(reader_password)))
                database.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(reader)))
                database.execute(sql.SQL('GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}').format(sql.Identifier(reader)))
                database.execute(sql.SQL('ALTER ROLE {} SET default_transaction_read_only=on').format(sql.Identifier(reader)))
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type) SELECT 'locked-'||lpad(i::text,6,'0'),'priority','prefix',%s,'[\"e2e4\"]','locked',%s,'opening' FROM generate_series(1,2048) i", (FEN, date.today().isoformat()))
                # Ineligible candidates still advance; no parent-wide query is needed.
            with psycopg.connect(owned_url) as database:
                database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES('load-line','priority','Load','white',%s,'[\"e2e4\"]',%s)", (FEN, date.today().isoformat()))
            for start in range(0, graph_rows, 5_000):
                with psycopg.connect(owned_url) as database:
                    database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color) SELECT 'priority',1,'load-line',i,'locked-'||lpad((1+(i%%2048))::text,6,'0'),'unpracticed',%s,%s,'[\"e2e4\"]','white' FROM generate_series(%s::bigint,%s::bigint) i", (FEN, FEN, start, min(graph_rows-1,start+4_999)))
            with psycopg.connect(owned_url) as database:
                database.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json) SELECT 'history-'||i,'cards.review','synthetic','complete','{}' FROM generate_series(1,%s) i", (receipt_rows,))
                database.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json,next_retry_at,lease_expires_at,updated_at) SELECT 'future-'||i,'cards.review','synthetic','retrying','{}',NOW()+INTERVAL '1 day',NULL,NOW() FROM generate_series(1,5000) i")
                for index, state in enumerate(('queued','executing','retrying')):
                    database.execute("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json,next_retry_at,lease_expires_at,updated_at) VALUES(%s,'cards.review','synthetic',%s,'{}',NOW()-INTERVAL '1 minute',NOW()-INTERVAL '1 minute',NOW()-INTERVAL '1 minute')", ('due-'+str(index),state))
                database.execute('ANALYZE operation_receipts'); database.execute('ANALYZE opening_graph_steps'); database.execute('ANALYZE cards')
            with psycopg.connect(owned_url) as database:
                database.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,state,published_at) VALUES('priority',1,'ready',%s)",(date.today().isoformat(),))
            with psycopg.connect(owned_url) as database:
                database.execute("INSERT INTO background_tasks(id,kind,deduplication_key,state,phase,payload_json,next_attempt_at,created_at,updated_at,lease_token) VALUES('priority-unlock-checkpoint','daily_queue','priority-unlock','leased','unlock_opening',%s,%s,%s,%s,'priority-proof')",(json.dumps({'queue_date':date.today().isoformat(),'_queue_phase':'unlock_opening'}),*[date.today().isoformat()]*3))
            cursor = ''
            pages = 0
            while cursor is not None:
                cursor = advance_unlock_checkpoint(force_timeout=pages == 0)
                pages += 1
                if pages == 2:
                    postgres_store.close_pools()
                    # A fresh process needs the repository namespace after this
                    # proof's relocation from scripts/ to database/tests/.
                    restart_environment = {**configuration,
                        'PYTHONPATH': os.pathsep.join((str(ROOT), configuration['PYTHONPATH']))}
                    cursor = resume_unlock_checkpoint(restart_environment)
                    assert cursor == 'locked-000048'
                    pages += 1
                assert pages <= 129
            with psycopg.connect(owned_url) as database:
                database.execute("DELETE FROM background_tasks WHERE id='priority-unlock-checkpoint' AND lease_token='priority-proof'")
            assert pages == 128
            with psycopg.connect(owned_url) as database:
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type) VALUES('z-root','priority','prefix',%s,'[\"e2e4\"]','locked',%s,'opening')",(FEN,date.today().isoformat()))
                database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color) VALUES('priority',1,'load-line',%s,'z-root',NULL,%s,%s,'[\"e2e4\"]','white')",(graph_rows+1,FEN,FEN))
                database.execute("UPDATE opening_graph_publications SET state='failed'")
            with postgres_store.connection(background=True) as database:
                assert queue_application._unlock_eligible_opening_cards(database,date.today().isoformat(),after_card_id='z-previous',batch_size=8) is None
            with psycopg.connect(owned_url) as database:
                assert database.execute("SELECT state FROM cards WHERE id='z-root'").fetchone()[0] == 'locked'
                database.execute("UPDATE opening_graph_publications SET state='ready'")
            with postgres_store.connection(background=True) as database:
                queue_application._unlock_eligible_opening_cards(database,date.today().isoformat(),after_card_id='z-previous',batch_size=8)
            with psycopg.connect(owned_url) as database:
                assert database.execute("SELECT state FROM cards WHERE id='z-root'").fetchone()[0] == 'new'
            checkpoint_retries = [json.loads(line) for line in (evidence / 'checkpoint-retries.jsonl').read_text().splitlines()]
            assert any(item['forced'] and item['checkpoint_unchanged'] for item in checkpoint_retries)
            results[CASES[0]] = {'transaction_deadline_rollback_retries': checkpoint_retries, 'pages': pages, 'examined': 2048, 'restart_after_page': 2, 'fresh_process_resumed_durable_checkpoint':True, 'published_graph_steps':graph_rows}
            timings = []
            recovered = []
            for _ in range(4):
                started = time.perf_counter()
                operation = claim_recoverable_operation()
                timings.append((time.perf_counter()-started)*1000)
                if operation: recovered.append(operation['operation_id'])
            assert sorted(recovered) == ['due-0','due-1','due-2']
            assert max(timings) < 250, timings
            results[CASES[1]] = {'terminal_receipts': receipt_rows, 'future_receipts': 5000,
                                 'claimed': recovered, 'duration_ms': timings}
            # Existing roles and existing parent data are never altered.
            reader_url = make_conninfo(owned_url, user=reader, password=reader_password)
            port = api_port(); origin = f'http://127.0.0.1:{port}'
            for label, command, overrides in (
                ('foreground', [sys.executable,'-m','celery','-A','app.celery_app:celery_app','worker','--queues=foreground','--pool=solo','--concurrency=1','--prefetch-multiplier=1','--loglevel=INFO'], {}),
                ('api', [sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port',str(port)],
                 {'TEMPO_DATABASE_READ_URL':reader_url,'TEMPO_DATABASE_WRITE_URL':None}),
            ):
                log = open(evidence / (label+'.log'), 'w'); handles.append(log)
                child_environment = {**configuration, **overrides}
                child_environment = {key:value for key,value in child_environment.items() if value is not None}
                processes.append(subprocess.Popen(command,cwd=ROOT,env=child_environment,stdout=log,stderr=subprocess.STDOUT))
            with psycopg.connect(owned_url) as database:
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type) SELECT 'review-'||lpad(i::text,4,'0'),'priority','prefix',%s,'[\"e2e4\"]','learning',%s,'tactic' FROM generate_series(1,120) i", (FEN,date.today().isoformat()))
                database.execute("INSERT INTO daily_queue(queue_date,card_id,position) SELECT %s,id,row_number() OVER(ORDER BY id) FROM cards WHERE id LIKE 'review-%%'",(date.today().isoformat(),))
                database.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending) VALUES(%s,'ready',1,0)",(date.today().isoformat(),))
            with httpx.Client(base_url=origin,timeout=20) as client:
                deadline = time.monotonic()+30
                while True:
                    try:
                        if client.get('/api/queue/window?limit=2').status_code == 200: break
                    except httpx.TransportError: pass
                    assert time.monotonic() < deadline, 'Owned study assembly did not become ready'
                    time.sleep(0.1)
                samples = {'idle': [], 'loaded': []}; replay_samples = []; receipt_ids = []
                server_samples = []; network_samples = []; priority_yield = []; progress = [0]
                stop_load = threading.Event(); load_started = threading.Event(); load_idle = threading.Event()
                def background_load():
                    from tempo_legacy_database.connections import background_connection
                    from app.services.activity_gate import BackgroundAdmissionDeferred, activity_gate
                    while not stop_load.is_set():
                        try:
                            with activity_gate.background_job('study_priority_rehearsal','owned-load',yielding=True):
                                with background_connection() as database:
                                    load_idle.clear(); load_started.set()
                                    database.execute_native('SELECT pg_sleep(0.02)')
                                    progress[0] += 1
                            load_idle.set()
                        except BackgroundAdmissionDeferred:
                            load_idle.set(); stop_load.wait(0.01)
                        except Exception:
                            load_idle.set(); raise
                loader = None
                for index in range(60):
                    if index == 30:
                        loader = threading.Thread(target=background_load,daemon=True); loader.start()
                        assert load_started.wait(5), 'Loaded run did not execute actual background SQL'
                        priority_started = time.perf_counter()
                        activity = client.post('/api/system/browser-activity?session_id=rehearsal',headers={'X-Tempo-Work-Class':'background'})
                        assert activity.status_code == 200
                        assert load_idle.wait(0.25), 'Background section did not yield'
                        priority_yield.append((time.perf_counter()-priority_started)*1000)
                    if index >= 30:
                        client.post('/api/system/browser-activity?session_id=rehearsal',headers={'X-Tempo-Work-Class':'background'})
                    queue = client.get('/api/queue/window?limit=2').json()
                    first, second = queue['cards'][:2]
                    attempt = uuid4().hex
                    receipt_ids.append('review-attempt:' + attempt)
                    payload = {'outcome':'correct','queue_entry_id':first['queue_entry_id'],
                        'expected_revision':first['revision'],'attempt_id':attempt,
                        'prepared_next':{'queue_entry_id':second['queue_entry_id'],'card_id':second['id'],
                            'revision':second['revision'],'cycle':second['cycle'],'local_date':queue['local_date']}}
                    started = time.perf_counter()
                    response = client.post('/api/cards/'+first['id']+'/review',json=payload,headers={'Idempotency-Key':'review-attempt:'+attempt})
                    assert response.status_code == 200, response.text
                    saved = response.json(); assert saved['persisted'] and saved['prepared_continuation']['card_id'] == second['id']
                    confirmed_ms = (time.perf_counter()-started)*1000
                    server_ms = float(response.headers['X-Tempo-Clock-Finished-Ms'])-float(response.headers['X-Tempo-Clock-Started-Ms'])
                    server_samples.append(server_ms); network_samples.append(max(0,confirmed_ms-server_ms))
                    samples['idle' if index < 30 else 'loaded'].append(confirmed_ms)
                    replay_started = time.perf_counter()
                    replay = client.post('/api/cards/'+first['id']+'/review',json=payload,headers={'Idempotency-Key':'review-attempt:'+attempt})
                    assert replay.status_code == 200 and replay.json() == saved
                    replay_samples.append((time.perf_counter()-replay_started)*1000)
                with psycopg.connect(owned_url) as database:
                    assert database.execute("SELECT COUNT(*) FROM reviews WHERE card_id LIKE 'review-%%'").fetchone()[0] == 60
                before_quiet = progress[0]
                deadline = time.monotonic()+5
                while progress[0] == before_quiet and time.monotonic() < deadline: time.sleep(0.05)
                stop_load.set()
                if loader: loader.join(timeout=1)
                assert progress[0] > before_quiet, 'Useful optional work did not return during quiet time'
                for lane in samples.values():
                    assert percentiles(lane)['p95'] <= 500 and percentiles(lane)['p99'] <= 1000, percentiles(lane)
                results[CASES[2]] = {'confirmed_save_ms': {lane:percentiles(values) for lane,values in samples.items()},
                    'server_request_ms':percentiles(server_samples),'transport_and_client_ms':percentiles(network_samples),
                    'replay_ms':percentiles(replay_samples),'samples':samples,'reviews':60,
                    'optional_yield_ms':priority_yield,'background_sections':progress[0]}
                assert max(priority_yield) <= 250
                control_times = []
                with psycopg.connect(owned_url) as locked:
                    locked.execute('LOCK TABLE settings IN ACCESS EXCLUSIVE MODE')
                    if postgres_container: docker('pause',postgres_container)
                    try:
                        for _ in range(10):
                            started = time.perf_counter()
                            response = client.get('/api/system/foreground-active',headers={'X-Tempo-Work-Class':'background'})
                            assert response.status_code == 200
                            control_times.append((time.perf_counter()-started)*1000)
                        assert max(control_times) < 250
                    finally:
                        if postgres_container: docker('unpause',postgres_container)
                results[CASES[3]] = {'database_paused':bool(postgres_container),'settings_locked':True,'control_ms':control_times}
                shared, renewal = prepare_priority_control_fixture(configuration['TEMPO_ADMISSION_NAMESPACE'])
                results[CASES[3]]['monotonic_renewal'] = renewal
                foreground_key = redis_admission_gate._FOREGROUND_KEY
                for session in ('device_a','device_b'):
                    assert client.post('/api/system/browser-activity?session_id='+session,headers={'X-Tempo-Work-Class':'background'}).status_code == 200
                shared.zadd(foreground_key,{'browser-activity:device_a':int(time.time()*1000)-1})
                snapshot = client.get('/api/system/foreground-active',headers={'X-Tempo-Work-Class':'background'}).json()
                assert snapshot['active'] is True and snapshot['available'] is True
                shared.zadd(foreground_key,{'browser-activity:device_b':int(time.time()*1000)-1})
                encoded = shared.get(study_priority.POLICY_KEY)
                expired = json.loads(encoded); expired['expires_at_ms'] = 0
                shared.set(study_priority.POLICY_KEY,json.dumps(expired),ex=15)
                stale = client.get('/api/system/foreground-active',headers={'X-Tempo-Work-Class':'background'}).json()
                assert stale['active'] is True and stale['available'] is False
                shared.set(study_priority.POLICY_KEY,encoded,ex=15)
                results[CASES[4]] = {'independent_devices':2,'expired_policy_denies_optional':True}
            stop_owned_writers(processes, loader, stop_load)
            postgres_store.close_pools()
            results[CASES[5]] = historical_rollback_proof(parent_url, owned_url, receipt_ids)
            assert set(results) == set(CASES)
            return results
        finally:
            stop_load.set()
            if loader: loader.join(timeout=1)
            for process in reversed(processes):
                process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)
            for handle in handles: handle.close()
            postgres_store.close_pools()
            redis_admission_gate.client.cache_clear(); study_priority.control_client.cache_clear()
            redis_admission_gate._FOREGROUND_KEY, redis_admission_gate._BACKGROUND_KEY = saved_admission_keys
            study_priority.POLICY_KEY, study_priority.GENERATION_KEY = saved_policy_keys
            os.environ.clear(); os.environ.update(original)
            with psycopg.connect(owned_url) as database:
                database.execute(sql.SQL('DROP OWNED BY {}').format(sql.Identifier(reader)))
            with psycopg.connect(parent_url,autocommit=True) as parent:
                parent.execute(sql.SQL('DROP ROLE IF EXISTS {}').format(sql.Identifier(reader)))


def record_owned_rehearsal(owner, present):
    directory = os.environ.get('TEMPO_DEVELOPMENT_BUDGET_DIRECTORY')
    token = os.environ.get('TEMPO_DEVELOPMENT_BUDGET_TOKEN')
    if not directory or not token:
        raise RuntimeError('Standalone proof requires scripts/development-budget.mjs')
    from urllib.parse import quote
    root = Path(directory)
    metadata = json.loads((root/'owner.json').read_text())
    if metadata['token'] != token: raise RuntimeError('Rehearsal budget owner changed')
    resource = root/'resources'/quote('rehearsal:'+owner,safe='')
    if present: resource.write_text(json.dumps({'token':token,'pid':os.getpid(),'owner':owner}))
    else: resource.unlink()


def main():
    from app.product_composition.database_infrastructure import bind_database_infrastructure
    bind_database_infrastructure()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--list',action='store_true')
    parser.add_argument('--quick',action='store_true')
    parser.add_argument('--owned-stack',action='store_true')
    parser.add_argument('--evidence',type=Path,default=ROOT/'test-results/study-priority-native')
    args = parser.parse_args()
    if args.list: print(json.dumps(CASES)); return
    args.evidence.mkdir(parents=True,exist_ok=True)
    if args.owned_stack:
        if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
            raise RuntimeError('The owning disposable runner is required')
        result = run_proof('postgresql://postgres@postgres:5432/tempo','redis://redis:6379/0',args.evidence,
            graph_rows=10_000 if args.quick else 1_400_000,receipt_rows=20_000 if args.quick else 415_000)
        (args.evidence/'native.json').write_text(json.dumps({'complete':True,'cases':result},indent=2)+'\n')
        print(json.dumps(result,indent=2)); return
    owner = 'tempo-study-priority-'+secrets.token_hex(8)
    password = secrets.token_urlsafe(32)
    names = [owner+'-postgres',owner+'-redis']
    report = {'owner':owner,'complete':False,'cleanup':False,'cases':{},'quick':args.quick,
              'source_sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in [Path(__file__), * (ROOT/'backend/app').rglob('*.py')]},
              'revision':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()}
    attempted = []
    record_owned_rehearsal(owner,True)
    try:
        for name,image,port,memory,cpu in ((names[0],'postgres:18.6-trixie',5432,'512m','1'),
                                           (names[1],'redis:7-alpine',6379,'64m','0.1')):
            attempted.append(name)
            docker('run','--detach','--name',name,'--label','tempo.owner='+owner,'--label','tempo.disposable=true',
                   '--label','tempo.resource-class=development','--cpus',cpu,'--memory',memory,
                   '--publish',f'127.0.0.1::{port}','--env','POSTGRES_PASSWORD',image,
                   environment={**os.environ,'POSTGRES_PASSWORD':password})
        postgres_port = json.loads(docker('inspect',names[0]))[0]['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort']
        redis_port = json.loads(docker('inspect',names[1]))[0]['NetworkSettings']['Ports']['6379/tcp'][0]['HostPort']
        parent_url = make_conninfo(host='127.0.0.1',port=postgres_port,user='postgres',password=password,dbname='postgres')
        deadline = time.monotonic()+30
        while True:
            try:
                with psycopg.connect(parent_url) as database:
                    database.execute("COMMENT ON DATABASE postgres IS 'tempo-disposable-postgres-test'")
                break
            except psycopg.OperationalError:
                assert time.monotonic()<deadline,'Owned PostgreSQL did not become ready'; time.sleep(0.1)
        report['cases'] = run_proof(parent_url,f'redis://127.0.0.1:{redis_port}/0',args.evidence,
            graph_rows=10_000 if args.quick else 1_400_000,receipt_rows=20_000 if args.quick else 415_000,
            postgres_container=names[0])
        report['complete'] = True
    finally:
        cleanup_errors=[]
        for name in reversed(attempted):
            try:
                details = json.loads(docker('inspect',name))[0]
                assert details['Config']['Labels']['tempo.owner'] == owner
                if details['State'].get('Paused'): docker('unpause',name)
                docker('rm','--force','--volumes',name)
            except Exception as error: cleanup_errors.append(str(error))
        if not cleanup_errors: record_owned_rehearsal(owner,False)
        report['cleanup'] = not cleanup_errors
        report['cleanup_errors'] = cleanup_errors
        (args.evidence/'native.json').write_text(json.dumps(report,indent=2)+'\n')
        if cleanup_errors: raise RuntimeError('Owned resource cleanup failed: '+str(cleanup_errors))
    print(json.dumps(report,indent=2))


if __name__ == '__main__': main()
