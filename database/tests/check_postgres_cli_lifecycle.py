"""Seed a stopped, empty disposable database at schema 16 for the CLI gate."""

from __future__ import annotations

# Preserve direct-script imports after relocation from scripts/.
import sys
from pathlib import Path
_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))
sys.path.insert(0, str(_REPOSITORY / "backend"))

import os
import argparse
import json
import re
import stat
import time
from pathlib import Path

import psycopg

from database.tooling import apply_postgres_migrations as migration_owner
from database.tooling.apply_postgres_migrations import MIGRATIONS, migration_source
from database.tooling.provision_postgres_roles import provision
from tempo_legacy_database.ddl.schema_version import POSTGRES_SCHEMA_VERSION


ABANDON_CARD = "cli-synthetic-abandon-orphan"


def _assert_disposable_pinned_database(database, guard):
    isolation = guard.get('isolation') or {}
    identity = isolation.get('identity') or {}
    if (os.getenv('TEMPO_TEST_INSTANCE') != 'disposable' or guard.get('state') != 'pending' or
            identity.get('target') != guard.get('target') or identity.get('trustedDaemon') is not True or
            not str(identity.get('project', '')).startswith('tempo-pg-regressions-') or
            isolation.get('phase') not in ('detached', 'verified') or isolation.get('original_allow_connections') is not True):
        raise RuntimeError('Disposable isolation authority is unavailable')
    row = database.execute("""SELECT current_database(), current_user, host(inet_client_addr()), pg_backend_pid(),
      (SELECT system_identifier::text FROM pg_control_system()),
      (SELECT oid FROM pg_database WHERE datname=current_database()),
      (SELECT datallowconn FROM pg_database WHERE datname=current_database()),
      (SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()),
      (SELECT count(*) FROM pg_prepared_xacts WHERE database=current_database()),
      current_setting('server_version_num'), current_setting('session_replication_role'),
      (SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database())""").fetchone()
    if (row[0] != isolation.get('target_name') or row[0] != (guard.get('database') or {}).get('name') or
            row[1] != isolation.get('admin_role') or row[2] not in ('127.0.0.1','::1') or row[3] <= 0 or
            row[4] != isolation.get('system_identifier') or row[5] != isolation.get('target_oid') or
            row[6] is not False or row[7] != 0 or row[8] != 0 or row[9] != '180006' or row[10] != 'origin' or
            row[11] != 'tempo-disposable-postgres-test'):
        raise RuntimeError('Disposable pinned database identity or admission changed')
    return row[3]


def _read_private_json(path):
    parent = os.lstat(os.path.dirname(path))
    if not stat.S_ISDIR(parent.st_mode) or stat.S_IMODE(parent.st_mode) != 0o700:
        raise RuntimeError('Private isolation state directory is invalid')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 1048576:
            raise RuntimeError('Private isolation state file is invalid')
        return json.load(stream)


def _write_private_json_once(path, value):
    # Publish only complete file contents; an existing receipt is never replaced.
    parent = os.path.dirname(path)
    temporary = os.path.join(parent, '.' + os.path.basename(path) + '.' + os.urandom(16).hex() + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream, sort_keys=True, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
        directory = os.open(parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _reuse_verified_receipt(path, expected):
    # Called only after the pinned connection has authenticated the original
    # witness against schema 62. Re-sync a previously published receipt before
    # the supervisor can rely on it after an interrupted directory fsync.
    if _read_private_json(path) != expected:
        raise RuntimeError('Original disposable receipt differs')
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory = os.open(os.path.dirname(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    if _read_private_json(path) != expected:
        raise RuntimeError('Original disposable receipt changed')


def create_disposable_handoff(root):
    """Allocate a new host-owned test handoff; callers own the daemon boundary."""
    if not os.path.isabs(root) or os.path.normpath(root) != root:
        raise RuntimeError('Disposable handoff root is invalid')
    uid = os.geteuid()
    current = root
    first = True
    while True:
        info = os.lstat(current)
        mode = stat.S_IMODE(info.st_mode)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, uid):
            raise RuntimeError('Disposable handoff ancestor is untrusted')
        if first:
            if info.st_uid != uid or mode != 0o700:
                raise RuntimeError('Disposable handoff root is not private and owned')
        elif mode & 0o022 and not (info.st_uid == 0 and mode & stat.S_ISVTX):
            raise RuntimeError('Disposable handoff ancestor is writable')
        parent = os.path.dirname(current)
        if parent == current:
            break
        first = False
        current = parent
    nonce = os.urandom(16).hex()
    handoff = os.path.join(root, 'handoff-' + os.urandom(16).hex())
    os.mkdir(handoff, 0o700)
    directory = os.open(root, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return handoff, nonce


@migration_owner.safe_postgres_errors
def run_disposable_pinned_isolation(database_url, *, guard_path, selection_path, binding_path, witness_path,
                                    pre62_path, handoff_directory, nonce):
    """Test-only pinned connection; caller owns the Docker isolation and lock.

    Production managed execution does not call this entry point. The caller
    must detach and inspect the exact registered Docker topology, close
    admission, and authenticate the ready PID before acknowledging the handoff.
    """
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Disposable isolation supervisor is not enabled')
    from database.tooling.verify_postgres_cli_state import (
        authenticate_pre62_59, check_captured_cut62_v6, cut62_witness_id,
        load_cut62_attempt_binding, load_cut62_defense_selection, load_cut62_witness,
        load_pre62_59, verify_cut62_v6, verify_pre62_59,
    )
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable' or not re.fullmatch('[0-9a-f]{32}', nonce or ''):
        raise RuntimeError('Disposable pinned handoff is unavailable')
    guard = _read_private_json(guard_path)
    isolation = guard.get('isolation') or {}
    binding = guard.get('defense_binding') or {}
    if (guard.get('version') != 1 or guard.get('state') != 'pending' or guard.get('starting_schema') not in (59,61) or
            guard.get('intended_schema') != 62 or isolation.get('phase') not in ('detached', 'verified') or
            binding.get('attempt_id') != guard.get('attempt_id') or binding.get('target') != guard.get('target') or
            binding.get('backup_filename') != (guard.get('backup') or {}).get('filename') or not (guard.get('backup') or {}).get('verified') or
            os.path.basename(witness_path) != guard.get('cut62_witness')):
        raise RuntimeError('Original disposable isolation guard is invalid')
    selection, selection_sha = load_cut62_defense_selection(selection_path)
    loaded_binding = load_cut62_attempt_binding(binding_path, selection, selection_sha)
    witness, secret = load_cut62_witness(witness_path)
    witness_id = cut62_witness_id(witness, secret)
    if (loaded_binding != binding or witness.get('binding') != binding or witness.get('selection') != selection or
            witness_id != (guard.get('cut62_identity') or {}).get('witness_id') or
            (guard.get('cut62_identity') or {}).get('attempt_id') != guard.get('attempt_id') or
            (guard.get('cut62_identity') or {}).get('backup_filename') != binding.get('backup_filename')):
        raise RuntimeError('Original disposable witness or binding differs')
    pre_witness = pre_secret = None
    if guard.get('starting_schema') == 59:
        checkpoint = guard.get('cut62_intermediate_verified') or {}
        if (checkpoint.get('schema') != 61 or checkpoint.get('attempt_id') != guard.get('attempt_id') or
                checkpoint.get('pre62_witness_id') != guard.get('pre62_witness_id') or
                checkpoint.get('backup_filename') != binding.get('backup_filename') or not pre62_path or
                os.path.basename(pre62_path) != guard.get('pre62_witness') or checkpoint.get('pre62_witness') != guard.get('pre62_witness')):
            raise RuntimeError('Original schema 59 checkpoint is invalid')
        pre_witness, pre_secret = load_pre62_59(pre62_path)
        if authenticate_pre62_59(pre_witness, pre_secret) != guard.get('pre62_witness_id'):
            raise RuntimeError('Original schema 59 witness differs')
    handoff = Path(handoff_directory)
    if not handoff.is_dir() or stat.S_IMODE(os.lstat(handoff).st_mode) != 0o700:
        raise RuntimeError('Disposable pinned handoff directory is invalid')
    with psycopg.connect(database_url) as database:
        backend = database.execute("""SELECT current_user,host(inet_client_addr()),
            (SELECT system_identifier::text FROM pg_control_system()),
            (SELECT oid FROM pg_database WHERE datname=current_database()),
            (SELECT extract(epoch FROM backend_start)::text FROM pg_stat_activity WHERE pid=pg_backend_pid())""").fetchone()
        database.rollback()
        if (backend[0] != isolation.get('admin_role') or backend[1] not in ('127.0.0.1','::1') or
                backend[2] != isolation.get('system_identifier') or backend[3] != isolation.get('target_oid')):
            raise RuntimeError('Disposable pinned backend identity differs')
        ready = {'version': 1, 'nonce': nonce, 'pid': database.info.backend_pid,
                 'backend_role': backend[0], 'backend_address': backend[1], 'backend_start': backend[4],
                 'target': guard['target'], 'attempt_id': guard['attempt_id'],
                 'daemon_id': isolation['identity']['daemonId'], 'container_id': isolation['identity']['containerId'],
                 'network_id': isolation['identity']['networkId'], 'image_id': isolation['identity']['imageId'],
                 'system_identifier': isolation['system_identifier'], 'target_oid': isolation['target_oid'],
                 'control_oid': isolation['control_oid'], 'backup_sha256': binding['backup_sha256'],
                 'selection_sha256': selection_sha, 'cut62_witness_id': witness_id,
                 'pre62_witness_id': guard.get('pre62_witness_id') if guard['starting_schema'] == 59 else None}
        _write_private_json_once(str(handoff / 'ready.json'), ready)
        deadline = time.monotonic() + 60
        while not (handoff / 'closed.json').exists():
            if time.monotonic() >= deadline:
                raise RuntimeError('Disposable pinned handoff timed out')
            time.sleep(.1)
        if _read_private_json(str(handoff / 'closed.json')) != ready or _read_private_json(guard_path) != guard:
            raise RuntimeError('Disposable pinned handoff changed')
        with database.transaction():
            database.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            if _assert_disposable_pinned_database(database, guard) != ready['pid']:
                raise RuntimeError('Disposable pinned backend changed')
            version = database.execute('SELECT COALESCE(max(version),0) FROM public.tempo_schema_migrations').fetchone()[0]
            if version == 61:
                if isolation.get('phase') != 'detached':
                    raise RuntimeError('Verified disposable phase cannot apply a migration')
                if pre_witness is not None:
                    observed = verify_pre62_59(database, pre_witness, pre_secret)
                    if observed.get('witness_id') != guard.get('pre62_witness_id') or observed.get('observed_schema') != 61:
                        raise RuntimeError('Original schema 59 history changed')
                if check_captured_cut62_v6(database, witness, secret).get('witness_id') != witness_id:
                    raise RuntimeError('Original schema 61 history changed')
            elif version == 62:
                if verify_cut62_v6(database, witness, secret).get('witness_id') != witness_id:
                    raise RuntimeError('Original committed witness differs')
            else:
                raise RuntimeError('Unsupported disposable isolation schema')
        if version == 61:
            migration_owner._ISOLATED_TEST_ENABLED = True
            try:
                migration_owner.apply_migrations(database_url, defense_selection=selection_path, cut62_binding=binding_path,
                    cut62_witness=witness_path, expected_selection_sha256=selection_sha,
                    expected_witness_id=witness_id, _pinned_connection=database,
                    _isolated_test_context={'capability': migration_owner._ISOLATED_TEST_CAPABILITY, 'guard': guard,
                                            'pre62_path': pre62_path, 'assert_fenced': _assert_disposable_pinned_database})
            finally:
                migration_owner._ISOLATED_TEST_ENABLED = False
        # The harness uses this bound, private observation to distinguish a
        # real post-commit crash from an earlier failure that also exits 1.
        with database.transaction():
            database.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            if (_assert_disposable_pinned_database(database, guard) != ready['pid'] or
                    database.execute('SELECT COALESCE(max(version),0) FROM public.tempo_schema_migrations').fetchone()[0] != 62):
                raise RuntimeError('Disposable post-commit schema differs')
        _write_private_json_once(str(handoff / 'committed.json'),
            {'version': 1, 'nonce': nonce, 'pid': ready['pid'], 'attempt_id': guard['attempt_id'],
             'cut62_witness_id': witness_id, 'schema': 62})
        if version == 61 and os.getenv('TEMPO_TEST_ISOLATION_CRASH_AFTER_COMMIT') == '1':
            os._exit(77)
        with database.transaction():
            database.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            if _assert_disposable_pinned_database(database, guard) != ready['pid'] or verify_cut62_v6(database, witness, secret).get('witness_id') != witness_id:
                raise RuntimeError('Disposable committed witness differs')
        if _read_private_json(guard_path) != guard:
            raise RuntimeError('Original disposable isolation guard changed')
        identity = isolation['identity']
        receipt = {'version': 1, 'target': guard['target'], 'attempt_id': guard['attempt_id'],
                   'target_oid': isolation['target_oid'], 'target_name': isolation['target_name'],
                   'system_identifier': isolation['system_identifier'], 'control_oid': isolation['control_oid'],
                   'control_name': isolation['control_name'], 'admin_role': isolation['admin_role'],
                   'daemon_id': identity['daemonId'], 'container_id': identity['containerId'],
                   'network_id': identity['networkId'], 'image_id': identity['imageId'],
                   'backup_filename': binding['backup_filename'], 'backup_sha256': binding['backup_sha256'],
                   'selection_sha256': selection_sha, 'pre62_witness_id': guard.get('pre62_witness_id') if guard['starting_schema'] == 59 else None,
                   'cut62_witness_id': witness_id, 'schema': 62}
        receipt_path = os.path.join(os.path.dirname(witness_path), 'verified-receipt.json')
        if os.path.lexists(receipt_path):
            if version != 62:
                raise RuntimeError('Receipt predates the committed schema')
            _reuse_verified_receipt(receipt_path, receipt)
        else:
            _write_private_json_once(receipt_path, receipt)
        if os.getenv('TEMPO_TEST_ISOLATION_CRASH_AFTER_RECEIPT') == '1':
            os._exit(78)



def abandon_fixture(database, *, seed=False):
    """Fixed synthetic data only; caller already owns the disposable CLI stack."""
    versions = [row[0] for row in database.execute("SELECT version FROM tempo_schema_migrations ORDER BY version")]
    if POSTGRES_SCHEMA_VERSION < 58 or versions != list(range(1, POSTGRES_SCHEMA_VERSION + 1)):
        raise RuntimeError("Abandonment fixture requires the genuine current migration ledger, including 058")
    if seed:
        import chess
        moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "d2d3", "f8c5", "c2c3"]
        board = chess.Board()
        for move in moves:
            board.push_uci(move)
        if database.execute("SELECT 1 FROM queue_attempt_origins WHERE card_id=%s", (ABANDON_CARD,)).fetchone():
            raise RuntimeError("Synthetic abandonment origin already exists")
        entry = database.execute("SELECT nextval(pg_get_serial_sequence('daily_queue','id'))").fetchone()[0]
        if entry == 13624:
            raise RuntimeError("Synthetic abandonment fixture must not use the user attempt identity")
        database.execute("""INSERT INTO queue_attempt_origins
            (queue_entry_id,card_id,revision,queue_date,cycle,admission_kind,admission_repertoire_id,
             attempt_failed,last_status,review_result_json,legacy,start_fen,moves_json,trained_color,content_type,
             issued_as_head,issued_at,issuance_sequence)
            VALUES(%s,%s,1,'2000-01-02',0,NULL,NULL,1,'buried',NULL,0,%s,%s,NULL,'opening',1,NULL,NULL)""",
            (entry, ABANDON_CARD, chess.STARTING_FEN, json.dumps(moves)))
    origin = database.execute("SELECT row_to_json(origin)::text FROM queue_attempt_origins origin WHERE card_id=%s",
                              (ABANDON_CARD,)).fetchall()
    if len(origin) != 1:
        raise RuntimeError("Exact synthetic abandonment origin is missing or ambiguous")
    identity = json.loads(origin[0][0])
    entry = identity["queue_entry_id"]
    snapshot = {"identity": {key: identity[key] for key in ("queue_entry_id", "card_id", "revision")},
                "origin_bytes": origin[0][0]}
    # Keep full stored rows (including FSRS, review clocks, failure markers and
    # other origins) in the comparison; do not project away accidental changes.
    for name, statement in {
        "origins": "SELECT row_to_json(stored)::text FROM queue_attempt_origins stored ORDER BY queue_entry_id,card_id,revision",
        "abandonments": "SELECT row_to_json(stored)::text FROM queue_attempt_abandonments stored ORDER BY queue_entry_id,card_id,revision",
        "cards": "SELECT row_to_json(stored)::text FROM cards stored ORDER BY id",
        "reviews": "SELECT row_to_json(stored)::text FROM reviews stored ORDER BY id",
        "queue": "SELECT row_to_json(stored)::text FROM daily_queue stored ORDER BY id",
        "review_receipts": "SELECT row_to_json(stored)::text FROM review_attempt_receipts stored ORDER BY queue_entry_id,card_id,attempt_id",
        "review_snapshots": "SELECT row_to_json(stored)::text FROM review_schedule_snapshots stored ORDER BY review_id",
        "grades": "SELECT row_to_json(stored)::text FROM study_position_grades stored ORDER BY effect_id",
    }.items():
        snapshot[name] = [row[0] for row in database.execute(statement)]
    snapshot["abandonment_receipt_count"] = database.execute("SELECT count(*) FROM queue_attempt_abandonments").fetchone()[0]
    snapshot["orphan_links"] = {}
    for table, column, value in (
        ("cards", "id", ABANDON_CARD), ("repertoire_cards", "card_id", ABANDON_CARD),
        ("daily_queue", "id", entry), ("review_attempt_receipts", "queue_entry_id", entry),
        ("opening_evidence_attempts", "queue_entry_id", entry),
        ("opening_evidence_presentations", "card_id", ABANDON_CARD),
        ("opening_evidence_queue_contexts", "queue_entry_id", entry),
        ("study_position_issued_plans", "queue_entry_id", entry),
    ):
        snapshot["orphan_links"][table] = database.execute(
            f"SELECT count(*) FROM {table} WHERE {column}=%s", (value,)).fetchone()[0]
    return snapshot


def main():
    if os.environ.get("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("CLI fixture requires an explicitly disposable database")
    dsn = os.environ["TEMPO_POSTGRES_ADMIN_URL"]
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--seed59", action="store_true", help="Seed populated historical data and advance only through schema 59")
    mode.add_argument("--history", action="store_true", help="Read only the original fixture history, allowing unrelated worker receipts")
    mode.add_argument("--defense-reference", action="store_true", help="Compute the synthetic approved Defense reference in a rolled-back schema-61 rehearsal")
    mode.add_argument("--normalization", action="store_true", help="Read the qualifying legacy migration-025 card and queue row")
    mode.add_argument("--seed-abandon-orphan", action="store_true", help="Seed one fixed synthetic orphan after the owned CLI target upgrades")
    mode.add_argument("--abandon-inspection", action="store_true", help="Read retained origin/receipt bytes and complete Study history for abandonment proof")
    mode.add_argument("--isolation-pinned", action="store_true", help="Run the disposable pinned test adapter")
    parser.add_argument("--isolation-defense", action="store_true", help="Use the fixed synthetic Defense cohort for the isolated test")
    parser.add_argument("--isolation-guard")
    parser.add_argument("--isolation-selection")
    parser.add_argument("--isolation-binding")
    parser.add_argument("--isolation-witness")
    parser.add_argument("--isolation-pre62")
    parser.add_argument("--isolation-handoff")
    parser.add_argument("--isolation-nonce")
    options = parser.parse_args()
    if options.isolation_defense and not (options.seed59 or options.defense_reference):
        raise RuntimeError('Synthetic Defense cohort requires seed59 or defense-reference')
    if options.isolation_pinned:
        if not all((options.isolation_guard, options.isolation_selection, options.isolation_binding,
                    options.isolation_witness, options.isolation_handoff, options.isolation_nonce)):
            raise RuntimeError('Disposable pinned test arguments are incomplete')
        run_disposable_pinned_isolation(dsn, guard_path=options.isolation_guard,
            selection_path=options.isolation_selection, binding_path=options.isolation_binding,
            witness_path=options.isolation_witness, pre62_path=options.isolation_pre62,
            handoff_directory=options.isolation_handoff, nonce=options.isolation_nonce)
        return
    if options.defense_reference:
        if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
            raise RuntimeError('Defense lifecycle fixture requires the disposable runner')
        database = psycopg.connect(dsn)
        try:
            from database.tooling.verify_postgres_cli_state import _cut62_defense_reference_digest, _cut62_session
            database.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            current = database.execute('SELECT max(version) FROM tempo_schema_migrations').fetchone()[0]
            if current not in (59, 61):
                raise RuntimeError('Synthetic Defense reference requires schema 59 or 61')
            if current == 59:
                for migration in sorted(MIGRATIONS.glob('[0-9][0-9][0-9]_*.sql'))[59:61]:
                    database.execute(migration_source(migration), prepare=False)
            _cut62_session(database)
            database.execute('SET LOCAL join_collapse_limit=1')
            database.execute('SET LOCAL jit=off')
            keys = [['cli-isolation-card', 'cli-isolation-candidate']] if options.isolation_defense else []
            count, digest = _cut62_defense_reference_digest(database, keys)
            if count != len(keys):
                raise RuntimeError('Synthetic Defense fixture selection changed')
            result = {'count': count, 'reference_digest': digest}
            database.rollback()
        finally:
            database.close()
        print(json.dumps(result))
        return
    if options.seed_abandon_orphan or options.abandon_inspection:
        connection_options = {} if options.seed_abandon_orphan else {"options": "-c default_transaction_read_only=on"}
        with psycopg.connect(dsn, **connection_options) as database:
            snapshot = abandon_fixture(database, seed=options.seed_abandon_orphan)
        print(json.dumps(snapshot))
        return
    if options.normalization:
        with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as database:
            record = database.execute("""SELECT json_build_object(
                'id',queue.id,'queue_date',queue.queue_date,'card_id',queue.card_id,
                'position',queue.position,'status',queue.status,'card_bucket',queue.card_bucket,
                'content_type',card.content_type,'repertoire_id',card.repertoire_id)
                FROM daily_queue queue JOIN cards card ON card.id=queue.card_id
                WHERE card.id='cli-legacy-tactical-miss'""").fetchone()
            if not record:
                raise RuntimeError("Qualifying migration-025 fixture is missing")
            print(json.dumps(record[0]))
        return
    if options.history:
        with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as database:
            history = {}
            for name, statement in {
                "reviews": "SELECT id,card_id,rating,reviewed_at,previous_interval,next_interval FROM reviews WHERE card_id='cli-history' ORDER BY id",
                "queue": "SELECT id,queue_date,card_id,position,status FROM daily_queue WHERE card_id IN ('cli-history','cli-legacy-tactical-miss') ORDER BY id",
                "receipt": "SELECT operation_id,command_name,request_hash,state,response_json,error_json,created_at,updated_at FROM operation_receipts WHERE operation_id='cli-preserved-receipt'",
            }.items():
                history[name] = database.execute(statement).fetchall()
            print(json.dumps(history, default=str))
        return
    with psycopg.connect(dsn) as database:
        tables = database.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'").fetchone()[0]
        if tables:
            raise RuntimeError("CLI fixture refuses to replace existing data")
        for migration in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))[:16]:
            database.execute(migration.read_text(), prepare=False)
        database.execute("INSERT INTO settings(id,new_cards_per_day,tactics_new_per_day) VALUES(1,0,0)")
        database.execute("INSERT INTO tactic_rotation(id) VALUES(1)")
        database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('cli-preserved','CLI preserved','regression','2000-01-01')")
        database.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,archived)
            VALUES('cli-history','cli-preserved','response',
                'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1','["e2e4"]','mature','2000-01-01',1)""")
        database.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES('cli-history','correct','2000-01-01',1,2)")
        database.execute("INSERT INTO daily_queue(queue_date,card_id,position,status) VALUES('2000-01-01','cli-history',0,'complete')")
        # This is migration 025's known game-tactics path, not an arbitrary
        # plural bucket. Archived historical rows stay outside current training.
        database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('__game_tactics__','Game tactics','regression','2000-01-01')")
        database.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,archived,content_type)
            VALUES('cli-legacy-tactical-miss','__game_tactics__','response',
                'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1','["e2e4"]','mature','2000-01-01',1,'tactics')""")
        database.execute("""INSERT INTO daily_queue(queue_date,card_id,position,status,card_bucket)
            VALUES('2000-01-01','cli-legacy-tactical-miss',1,'complete','tactics')""")
        database.execute("""INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,response_json)
            VALUES('cli-preserved-receipt','fixture','preserved','complete','{"ok":true}')""")
    if options.seed59:
        # Use the migration owner's installed module resource, including 050.
        # This disposable fixture retains the same populated origin and ledger.
        with psycopg.connect(dsn) as database:
            for migration in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))[16:59]:
                with database.transaction():
                    database.execute(migration_source(migration), prepare=False)
        if options.isolation_defense:
            with psycopg.connect(dsn) as database:
                database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES ('cli-isolation','CLI isolation','regression','2000-01-01')")
                database.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type)
                    VALUES ('cli-isolation-card','cli-isolation','prefix','fen','[]','2000-01-01','defense')""")
                database.execute("""INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
                    VALUES ('cli-isolation-game','synthetic','synthetic','2000-01-01','rapid',0,'white','draw','fen','[]')""")
                database.execute("""INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,created_at,updated_at)
                    VALUES ('cli-isolation-finding','cli-isolation-game',1,1,'synthetic',1,'{}','2000-01-01','2000-01-01')""")
                database.execute("""INSERT INTO threat_training_candidates(id,finding_id,game_id,analysis_version,incident_id,player_ply,evidence_json,source_fingerprint,detector_version,policy_json,created_at,updated_at,card_id)
                    VALUES ('cli-isolation-candidate','cli-isolation-finding','cli-isolation-game',1,'synthetic',1,'{}','synthetic',1,'{}','2000-01-01','2000-01-01','cli-isolation-card')""")
                database.execute("""INSERT INTO daily_queue(id,queue_date,card_id,cycle,position,status,admission_source)
                    VALUES (190000,'2000-01-02','cli-isolation-card',1,0,'queued','defense:cli-isolation-candidate')""")
                from psycopg import sql
                name = database.execute('SELECT current_database()').fetchone()[0]
                database.execute(sql.SQL("COMMENT ON DATABASE {} IS 'tempo-disposable-postgres-test'").format(sql.Identifier(name)))
    provision(dsn, Path("/run/secrets/reader_password").read_text().strip(),
              Path("/run/secrets/writer_password").read_text().strip())
    print(f"Seeded populated schema {59 if options.seed59 else 16} with preserved review, queue, and receipt history")


if __name__ == "__main__":
    main()
