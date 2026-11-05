"""Apply checked-in PostgreSQL migrations during a stopped-writer window."""

from __future__ import annotations

# Preserve direct-script imports after relocation from scripts/.
import sys
from pathlib import Path
_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))

import argparse
from contextlib import nullcontext
import hashlib
from functools import wraps
import re
import stat
import time
from importlib import metadata
import json
import os
from pathlib import Path
import sys

import psycopg


MIGRATIONS = Path(__file__).resolve().parents[2] / "database/tempo_legacy_database/ddl/migrations"
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "database"))
from tempo_legacy_database.ddl.schema_version import POSTGRES_SCHEMA_VERSION


MODULE_MIGRATIONS = {
    "050_learning_remediation.sql": (
        "tempo-learning-remediation", "0.1.0",
        "tempo_remediation_database/ddl/migrations/001_remediation.sql",
        "96907361dc003e78703dc57182bd020f96e82532bcea6724083326171242da0d",
    ),
}


def migration_source(path: Path) -> str:
    """Only this owner registers module schema; no runtime core or source fallback."""
    local_source = path.read_text()
    registration = MODULE_MIGRATIONS.get(path.name)
    if registration is None:
        if "tempo-module-schema:" in local_source:
            raise RuntimeError("Unregistered module migration")
        return local_source
    distribution_name, version, resource, checksum = registration
    if (f"-- tempo-module-schema: {resource}\n" not in local_source
            or f"-- sha256: {checksum}\n" not in local_source):
        raise RuntimeError("Module migration registration changed")
    distribution = metadata.distribution(distribution_name)
    if distribution.version != version:
        raise RuntimeError("Module migration distribution version changed")
    content = distribution.locate_file(resource).read_bytes()
    if hashlib.sha256(content).hexdigest() != checksum:
        raise RuntimeError("Module migration checksum changed")
    return "SET LOCAL search_path=public,pg_catalog;\n" + content.decode("utf-8") + "\n" + local_source


def validate_migration_history(
    files: list[Path], applied_versions: list[int], *, expected_version: int,
) -> int:
    file_versions = [int(path.name[:3]) for path in files]
    if file_versions != list(range(1, expected_version + 1)):
        raise RuntimeError("Checked-in PostgreSQL migration files have a gap or duplicate")
    if applied_versions and applied_versions[-1] > expected_version:
        raise RuntimeError("PostgreSQL schema is newer than this image; stop the upgrade")
    if applied_versions != list(range(1, len(applied_versions) + 1)):
        raise RuntimeError("PostgreSQL migration history has a gap or duplicate; stop the upgrade")
    return len(applied_versions)


def safe_postgres_errors(function):
    """Do not expose database-provided detail, row keys, SQL or connection data."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except psycopg.Error as error:
            state = getattr(error, "sqlstate", None)
            state = state if isinstance(state, str) and re.fullmatch(r"[0-9A-Z]{5}", state) else "unknown"
            name = type(error).__name__
            name = name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) else "DatabaseError"
            raise RuntimeError(f"PostgreSQL migration operation failed ({name}, SQLSTATE {state}); keep writers stopped.") from None
    return wrapped


# Private capability is passed only by the disposable pinned-session helper.
# The managed production path never supplies it and remains fail-closed.
_ISOLATED_TEST_CAPABILITY = object()


def _assert_disposable_pinned_database(database, guard):
    isolation = guard.get('isolation') or {}
    identity = isolation.get('identity') or {}
    if (os.getenv('TEMPO_TEST_INSTANCE') != 'disposable' or guard.get('state') != 'pending' or
            identity.get('target') != guard.get('target') or identity.get('trustedDaemon') is not True or
            not str(identity.get('project', '')).startswith('tempo-pg-regressions-') or
            isolation.get('phase') != 'detached' or isolation.get('original_allow_connections') is not True):
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


@safe_postgres_errors
def apply_migrations(database_url: str, *, target_version: int | None = None,
                     defense_zero: bool = False, defense_selection: str | None = None,
                     cut62_binding: str | None = None, cut62_witness: str | None = None,
                     expected_selection_sha256: str | None = None, expected_witness_id: str | None = None,
                     _pinned_connection=None, _isolated_test_context=None) -> None:
    files = sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
    if not files:
        raise RuntimeError("No PostgreSQL migrations found")
    # Resolve/check every registered resource before opening a database session.
    sources = {path: migration_source(path) for path in files}
    if _pinned_connection is not None and int(_pinned_connection.info.transaction_status) != 0:
        raise RuntimeError("Pinned migration connection must be idle before migration")
    # An owning launcher may retain this exact connection across admission
    # isolation and post-commit verification. It remains responsible for close.
    connection = nullcontext(_pinned_connection) if _pinned_connection is not None else psycopg.connect(database_url)
    with connection as database:
        exists = database.execute(
            "SELECT to_regclass('public.tempo_schema_migrations') IS NOT NULL"
        ).fetchone()[0]
        applied_versions = ([row[0] for row in database.execute(
            "SELECT version FROM tempo_schema_migrations ORDER BY version"
        ).fetchall()] if exists else [])
        current_version = validate_migration_history(
            files, applied_versions, expected_version=POSTGRES_SCHEMA_VERSION,
        )
        if target_version is not None and (POSTGRES_SCHEMA_VERSION != 62 or target_version != 61 or current_version not in (59, 60)):
            raise RuntimeError("Bounded migration supports only schema 59 or interrupted 60 through 61 on a schema-62 image")
        database.commit()
        for path in files:
            version = int(path.name[:3])
            if target_version is not None and version > target_version:
                break
            if version <= current_version:
                continue
            if version != current_version + 1:
                raise RuntimeError(f"Missing migration before {path.name}")
            with database.transaction():
                if version == 62:
                    database.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    # A crossing must choose an explicit policy. The installed
                    # path reauthenticates the original witness and source in
                    # this very transaction before SQL can mutate anything.
                    from database.tooling.verify_postgres_cli_state import (
                        _cut62_defense_reference_digest, check_captured_cut62_v6,
                        cut62_witness_id, load_cut62_attempt_binding, verify_cut62_v6,
                        load_cut62_defense_selection, load_cut62_witness,
                    )
                    if defense_zero:
                        if any((defense_selection, cut62_binding, cut62_witness, expected_selection_sha256, expected_witness_id)):
                            raise RuntimeError("Conflicting schema 62 Defense policies")
                        count, digest = _cut62_defense_reference_digest(database, [])
                        selection = {'version': 1, 'target': 'explicit-historical-zero', 'cohort': 'defense-no-activity',
                                     'keys': [], 'expected': {'count': count, 'reference_digest': digest}}
                    else:
                        if not all((defense_selection, cut62_binding, cut62_witness, expected_selection_sha256, expected_witness_id)):
                            raise RuntimeError("Schema 62 requires an explicit approved Defense policy")
                        selection, selection_digest = load_cut62_defense_selection(defense_selection)
                        if selection_digest != expected_selection_sha256:
                            raise RuntimeError("Original Defense selection identity differs")
                        if selection['keys']:
                            if (_pinned_connection is None or not _isolated_test_context or
                                    _isolated_test_context.get('capability') is not _ISOLATED_TEST_CAPABILITY):
                                raise RuntimeError("Nonempty Defense cleanup requires a verified writer and DDL fence")
                            guard = _isolated_test_context['guard']
                            _assert_disposable_pinned_database(database, guard)
                            if guard.get('starting_schema') == 59:
                                from database.tooling.verify_postgres_cli_state import load_pre62_59, verify_pre62_59
                                old, old_secret = load_pre62_59(_isolated_test_context['pre62_path'])
                                checked = verify_pre62_59(database, old, old_secret)
                                if checked.get('witness_id') != guard.get('pre62_witness_id') or checked.get('observed_schema') != 61:
                                    raise RuntimeError('Original schema 59 witness differs inside the fence')
                        binding = load_cut62_attempt_binding(cut62_binding, selection, selection_digest)
                        witness, secret = load_cut62_witness(cut62_witness)
                        if (witness.get('version') != 6 or witness.get('selection') != selection or
                                witness.get('binding') != binding or cut62_witness_id(witness, secret) != expected_witness_id):
                            raise RuntimeError("Original Defense witness or binding differs")
                        check_captured_cut62_v6(database, witness, secret)
                        selection = witness['selection']
                    database.execute("SELECT set_config('tempo.cut62_defense_manifest', %s, true)",
                                     (json.dumps(selection, separators=(',', ':')),))
                database.execute(sources[path], prepare=False)
                applied_version = database.execute(
                    "SELECT COALESCE(MAX(version),0) FROM tempo_schema_migrations"
                ).fetchone()[0]
                if applied_version != version:
                    raise RuntimeError(f"{path.name} did not record version {version}")
                if version == 62 and not defense_zero:
                    verified = verify_cut62_v6(database, witness, secret)
                    if verified.get('witness_id') != expected_witness_id:
                        raise RuntimeError("Post-migration witness identity differs; transaction rolled back")
            current_version = version
            print(f"Applied {path.name}", flush=True)
        print(f"PostgreSQL schema version {current_version}", flush=True)


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
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(descriptor, 'w') as stream:
        json.dump(value, stream, sort_keys=True, separators=(',', ':'))
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(os.path.dirname(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@safe_postgres_errors
def run_disposable_pinned_isolation(database_url, *, guard_path, selection_path, binding_path, witness_path,
                                    pre62_path, handoff_directory, nonce):
    """Test-only pinned connection; caller owns the Docker isolation and lock.

    Production managed execution does not call this entry point. The caller
    must detach and inspect the exact registered Docker topology, close
    admission, and authenticate the ready PID before acknowledging the handoff.
    """
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
            guard.get('intended_schema') != 62 or isolation.get('phase') != 'detached' or
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
        ready = {'version': 1, 'nonce': nonce, 'pid': database.info.backend_pid}
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
            apply_migrations(database_url, defense_selection=selection_path, cut62_binding=binding_path,
                             cut62_witness=witness_path, expected_selection_sha256=selection_sha,
                             expected_witness_id=witness_id, _pinned_connection=database,
                             _isolated_test_context={'capability': _ISOLATED_TEST_CAPABILITY, 'guard': guard, 'pre62_path': pre62_path})
            if os.getenv('TEMPO_TEST_ISOLATION_CRASH_AFTER_COMMIT') == '1':
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
        _write_private_json_once(os.path.join(os.path.dirname(witness_path), 'verified-receipt.json'), receipt)


@safe_postgres_errors
def migration_status(database_url: str, *, reader_passfile=None, writer_passfile=None) -> dict[str, object]:
    """Validate both histories using an explicitly read-only database session."""
    files = sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))
    with psycopg.connect(database_url, options="-c default_transaction_read_only=on") as database:
        exists = database.execute(
            "SELECT to_regclass('public.tempo_schema_migrations') IS NOT NULL"
        ).fetchone()[0]
        applied = [row[0] for row in database.execute(
            "SELECT version FROM tempo_schema_migrations ORDER BY version"
        ).fetchall()] if exists else []
        current = validate_migration_history(files, applied, expected_version=POSTGRES_SCHEMA_VERSION)
        settings_exist = database.execute("SELECT to_regclass('public.settings') IS NOT NULL").fetchone()[0]
        initialized = bool(settings_exist and database.execute("SELECT 1 FROM settings WHERE id=1").fetchone())
        roles = {row[0] for row in database.execute(
            "SELECT rolname FROM pg_roles WHERE rolname IN ('tempo_reader','tempo_writer')"
        ).fetchall()}
        roles_ready = roles == {"tempo_reader", "tempo_writer"}
        if roles_ready:
            roles_ready = bool(database.execute("""
                SELECT COALESCE(bool_and(
                    has_table_privilege('tempo_reader', quote_ident(table_schema)||'.'||quote_ident(table_name), 'SELECT')
                    AND NOT has_table_privilege('tempo_reader', quote_ident(table_schema)||'.'||quote_ident(table_name), 'INSERT')
                    AND NOT has_table_privilege('tempo_reader', quote_ident(table_schema)||'.'||quote_ident(table_name), 'UPDATE')
                    AND NOT has_table_privilege('tempo_reader', quote_ident(table_schema)||'.'||quote_ident(table_name), 'DELETE')
                    AND has_table_privilege('tempo_writer', quote_ident(table_schema)||'.'||quote_ident(table_name), 'SELECT')
                    AND has_table_privilege('tempo_writer', quote_ident(table_schema)||'.'||quote_ident(table_name), 'INSERT')
                    AND has_table_privilege('tempo_writer', quote_ident(table_schema)||'.'||quote_ident(table_name), 'UPDATE')
                    AND has_table_privilege('tempo_writer', quote_ident(table_schema)||'.'||quote_ident(table_name), 'DELETE')
                ),false) FROM information_schema.tables
                WHERE table_schema='public' AND table_type='BASE TABLE'
            """).fetchone()[0])
            roles_ready = roles_ready and bool(database.execute("""
                SELECT COALESCE(bool_and(
                    has_sequence_privilege('tempo_writer', quote_ident(sequence_schema)||'.'||quote_ident(sequence_name), 'USAGE')
                    AND has_sequence_privilege('tempo_writer', quote_ident(sequence_schema)||'.'||quote_ident(sequence_name), 'SELECT')
                    AND has_sequence_privilege('tempo_writer', quote_ident(sequence_schema)||'.'||quote_ident(sequence_name), 'UPDATE')
                ),true) FROM information_schema.sequences WHERE sequence_schema='public'
            """).fetchone()[0])
            roles_ready = roles_ready and bool(database.execute("""
                SELECT bool_and(CASE WHEN rolname='tempo_reader'
                    THEN COALESCE(rolconfig @> ARRAY['default_transaction_read_only=on'],false)
                    ELSE NOT COALESCE(rolconfig @> ARRAY['default_transaction_read_only=on'],false) END)
                FROM pg_roles WHERE rolname IN ('tempo_reader','tempo_writer')
            """).fetchone()[0])
    credentials_ready = None
    if reader_passfile and writer_passfile:
        credentials_ready = True
        for role, passfile in (("tempo_reader", reader_passfile), ("tempo_writer", writer_passfile)):
            with psycopg.connect(database_url, user=role, passfile=str(passfile),
                                 options="-c default_transaction_read_only=on") as database:
                if database.execute("SELECT current_user").fetchone()[0] != role:
                    credentials_ready = False
    return {"expected_version": POSTGRES_SCHEMA_VERSION, "applied_versions": applied,
            "pending_versions": list(range(current + 1, POSTGRES_SCHEMA_VERSION + 1)),
            "initialized": initialized, "roles_ready": roles_ready, "credentials_ready": credentials_ready}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.getenv("TEMPO_POSTGRES_ADMIN_URL"))
    parser.add_argument("--check", action="store_true", help="Read-only history and initialization check")
    parser.add_argument("--json", action="store_true", help="Print --check as JSON")
    parser.add_argument("--target-version", type=int, help="Bound an approved schema-59/60 to 61 migration")
    parser.add_argument("--defense-zero", action="store_true", help="Explicitly select no Defense cleanup for a fresh or historical fixture")
    parser.add_argument("--defense-selection")
    parser.add_argument("--cut62-binding")
    parser.add_argument("--cut62-witness")
    parser.add_argument("--expected-selection-sha256")
    parser.add_argument("--expected-witness-id")
    parser.add_argument("--isolated-disposable", action="store_true")
    parser.add_argument("--isolation-guard")
    parser.add_argument("--isolation-handoff")
    parser.add_argument("--isolation-pre62-witness")
    parser.add_argument("--isolation-nonce")
    parser.add_argument("--reader-passfile", type=Path, help="Verify reader authentication during --check")
    parser.add_argument("--writer-passfile", type=Path, help="Verify writer authentication during --check")
    options = parser.parse_args()
    if not options.dsn:
        parser.error("Provide --dsn or TEMPO_POSTGRES_ADMIN_URL")
    if options.json and not options.check:
        parser.error("--json requires --check")
    if options.check and options.target_version is not None:
        parser.error("--target-version cannot be combined with --check")
    if options.isolated_disposable:
        if (options.check or options.target_version is not None or options.defense_zero or
                not all((options.defense_selection, options.cut62_binding, options.cut62_witness,
                         options.isolation_guard, options.isolation_handoff, options.isolation_nonce))):
            parser.error("Disposable isolation requires the complete private handoff")
        run_disposable_pinned_isolation(options.dsn, guard_path=options.isolation_guard,
            selection_path=options.defense_selection, binding_path=options.cut62_binding,
            witness_path=options.cut62_witness, pre62_path=options.isolation_pre62_witness,
            handoff_directory=options.isolation_handoff, nonce=options.isolation_nonce)
    elif options.check:
        status = migration_status(options.dsn, reader_passfile=options.reader_passfile, writer_passfile=options.writer_passfile)
        print(json.dumps(status) if options.json else f"Schema {len(status['applied_versions'])}; expected {status['expected_version']}; pending {status['pending_versions']}")
    else:
        apply_migrations(options.dsn, target_version=options.target_version, defense_zero=options.defense_zero,
                         defense_selection=options.defense_selection, cut62_binding=options.cut62_binding,
                         cut62_witness=options.cut62_witness, expected_selection_sha256=options.expected_selection_sha256,
                         expected_witness_id=options.expected_witness_id)


if __name__ == "__main__":
    main()
