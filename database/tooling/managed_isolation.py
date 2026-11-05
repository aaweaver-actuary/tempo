"""Read-only evidence checks for a registered managed PostgreSQL cutover.

This module does not open admission, detach networks, migrate, or provide a CLI.
The owning launcher must hold the target lock, exclusively control the Docker
daemon and host administration, and independently verify the recorded Docker
topology before and throughout use of the pinned connection.
"""
from __future__ import annotations

import os
import re
import hashlib
import hmac
import json
import stat
from psycopg.pq import TransactionStatus

from database.tooling.verify_postgres_cli_state import (
    apply_cut62_history_v2, authenticate_pre62_59, check_captured_cut62_history_v2,
    load_cut62_attempt_binding, load_cut62_history_selection, load_cut62_history_v2,
    load_pre62_59, verify_cut62_history_v2, verify_pre62_59,
)

_MANAGED_NONEMPTY_ENABLED = False


def assert_fenced_pinned_session(database, guard, expected_backend, *, read_only=False):
    """Confirm the original target is closed and only the pinned backend exists.

    This database-side check does not establish Docker isolation or exclude a
    privileged administrator. It must run inside the owner's existing RR
    transaction after the launcher has independently established both fences.
    """
    if database.info.transaction_status != TransactionStatus.INTRANS:
        raise RuntimeError('Managed isolation requires an active transaction')
    isolation = guard.get('isolation') or {}
    identity = isolation.get('identity') or {}
    if (guard.get('state') != 'pending' or isolation.get('phase') not in ('detached', 'verified') or
            identity.get('target') != guard.get('target') or identity.get('trustedDaemon') is not True or
            isolation.get('original_allow_connections') is not True or not isinstance(expected_backend, dict)):
        raise RuntimeError('Original managed isolation identity is invalid')
    row = database.execute("""SELECT current_database(), current_user, host(inet_client_addr()), pg_backend_pid(),
      (SELECT system_identifier::text FROM pg_control_system()),
      (SELECT oid::bigint FROM pg_database WHERE datname=current_database()),
      (SELECT oid::bigint FROM pg_database WHERE datname='postgres'),
      (SELECT datallowconn FROM pg_database WHERE datname=current_database()),
      (SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()),
      (SELECT count(*) FROM pg_prepared_xacts WHERE database=current_database()),
      current_setting('server_version_num'), current_setting('session_replication_role'),
      current_setting('transaction_isolation'), current_setting('transaction_read_only'),
      (SELECT extract(epoch FROM backend_start)::text FROM pg_stat_activity WHERE pid=pg_backend_pid())""").fetchone()
    if (row[0] != guard.get('database', {}).get('name') or row[0] != isolation.get('target_name') or
            row[1] != isolation.get('admin_role') or row[1] != expected_backend.get('role') or
            row[2] not in ('127.0.0.1', '::1') or row[2] != expected_backend.get('address') or
            row[3] != expected_backend.get('pid') or row[3] <= 0 or row[4] != isolation.get('system_identifier') or
            row[5] != isolation.get('target_oid') or row[6] != isolation.get('control_oid') or
            isolation.get('control_name') != 'postgres' or row[5] == row[6] or row[7] is not False or
            row[8] != 0 or row[9] != 0 or row[10] != '180006' or row[11] != 'origin' or
            row[12] != 'repeatable read' or row[13] != ('on' if read_only else 'off') or row[14] != expected_backend.get('start')):
        raise RuntimeError('Original managed pinned session or admission differs')
    return row[3]


def _load_original_evidence(guard, *, selection_path, binding_path, witness_path, pre62_path=None):
    binding = guard.get('defense_binding') or {}
    attempt = guard.get('attempt_id')
    if (not re.fullmatch(r'[0-9a-f-]{36}', attempt or '', re.I) or guard.get('intended_schema') != 62 or
            guard.get('cut62_witness') != f'cut62-{attempt}.json' or
            not all(os.path.isabs(path or '') for path in (selection_path, binding_path, witness_path)) or
            any(os.path.normpath(path) != path for path in (selection_path, binding_path, witness_path)) or
            len({os.path.dirname(path) for path in (selection_path, binding_path, witness_path)}) != 1 or
            os.path.basename(selection_path) != 'defense-selection.json' or os.path.basename(binding_path) != 'binding.json' or
            os.path.basename(witness_path) != guard.get('cut62_witness')):
        raise RuntimeError('Original managed evidence identity is invalid')
    selection, selection_sha = load_cut62_history_selection(selection_path)
    loaded_binding = load_cut62_attempt_binding(binding_path, selection, selection_sha)
    witness, secret, witness_id = load_cut62_history_v2(witness_path, binding)
    identity = guard.get('cut62_identity') or {}
    if (not selection.get('keys') or loaded_binding != binding or binding.get('target') != guard.get('target') or
            binding.get('attempt_id') != guard.get('attempt_id') or not guard.get('backup', {}).get('verified') or
            binding.get('backup_filename') != guard.get('backup', {}).get('filename') or
            witness.get('selection') != selection or witness.get('binding') != binding or
            identity.get('witness_id') != witness_id or identity.get('attempt_id') != guard.get('attempt_id') or
            identity.get('backup_filename') != binding.get('backup_filename')):
        raise RuntimeError('Original managed selection, backup or witness differs')
    original = original_secret = None
    if guard.get('starting_schema') == 59:
        checkpoint = guard.get('cut62_intermediate_verified') or {}
        if (not pre62_path or checkpoint.get('schema') != 61 or checkpoint.get('attempt_id') != guard.get('attempt_id') or
                not os.path.isabs(pre62_path) or os.path.basename(pre62_path) != f'pre62-{attempt}.json' or
                guard.get('pre62_witness') != f'pre62-{attempt}.json' or
                checkpoint.get('backup_filename') != binding.get('backup_filename') or
                checkpoint.get('pre62_witness') != guard.get('pre62_witness') or
                checkpoint.get('pre62_witness_id') != guard.get('pre62_witness_id')):
            raise RuntimeError('Original managed schema 59 checkpoint differs')
        original, original_secret = load_pre62_59(pre62_path)
        if authenticate_pre62_59(original, original_secret) != guard.get('pre62_witness_id'):
            raise RuntimeError('Original managed schema 59 witness differs')
    elif guard.get('starting_schema') != 61:
        raise RuntimeError('Unsupported managed schema origin')
    return selection, witness, secret, witness_id, original, original_secret


def verify_originals_in_fenced_transaction(database, guard, expected_backend, *, selection_path,
                                           binding_path, witness_path, pre62_path=None):
    """Authenticate original source evidence in the existing pinned snapshot."""
    assert_fenced_pinned_session(database, guard, expected_backend)
    if guard['isolation']['phase'] != 'detached':
        raise RuntimeError('Managed source verification requires a detached phase')
    selection, witness, secret, witness_id, original, original_secret = _load_original_evidence(
        guard, selection_path=selection_path, binding_path=binding_path, witness_path=witness_path, pre62_path=pre62_path)
    if original is not None:
        checked = verify_pre62_59(database, original, original_secret, in_write_transaction=True)
        if checked.get('witness_id') != guard.get('pre62_witness_id') or checked.get('observed_schema') != 61:
            raise RuntimeError('Original managed schema 59 history differs')
    checked = check_captured_cut62_history_v2(database, witness, secret,
                                               guard['defense_binding']['selection_sha256'], guard['defense_binding'])
    if checked.get('observed_schema') != 61 or checked.get('witness_id') != witness_id:
        raise RuntimeError('Original managed schema 61 history differs')
    return selection, witness, secret, witness_id


def verify_committed_in_fenced_transaction(database, guard, expected_backend, *, selection_path,
                                          binding_path, witness_path, pre62_path=None):
    """Verify the original projected result while the pinned fence is closed."""
    # The v2 projection verifier requires an existing RR read-write transaction
    # even on the post-commit retry; it does not itself mutate retained rows.
    assert_fenced_pinned_session(database, guard, expected_backend)
    _, witness, secret, witness_id, _, _ = _load_original_evidence(
        guard, selection_path=selection_path, binding_path=binding_path, witness_path=witness_path, pre62_path=pre62_path)
    checked = verify_cut62_history_v2(database, witness, secret, guard['defense_binding'])
    if checked.get('observed_schema') != 62 or checked.get('witness_id') != witness_id:
        raise RuntimeError('Original managed committed history differs')
    return witness_id


def execute_managed_history_cut(database, guard, expected_backend, *, selection_path,
                                binding_path, witness_path, pre62_path=None):
    """Own the pinned 61-to-62 transaction and its post-commit verification.

    The launcher must retain the target lock and independently establish the
    Docker/admission fence for this entire call. This entry stays hard closed
    until the complete managed lifecycle is reviewed and qualified.
    """
    if not _MANAGED_NONEMPTY_ENABLED:
        raise RuntimeError('Managed nonempty Defense cleanup is unavailable')
    if database.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError('Managed pinned connection must be idle')
    paths = dict(selection_path=selection_path, binding_path=binding_path,
                 witness_path=witness_path, pre62_path=pre62_path)
    with database.transaction():
        database.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ WRITE')
        assert_fenced_pinned_session(database, guard, expected_backend)
        schema = database.execute('SELECT COALESCE(MAX(version),0) FROM public.tempo_schema_migrations').fetchone()[0]
        if schema == 61:
            _, witness, secret, witness_id = verify_originals_in_fenced_transaction(
                database, guard, expected_backend, **paths)
            result = apply_cut62_history_v2(database, witness, secret, guard['defense_binding'])
            if result.get('observed_schema') != 62 or result.get('witness_id') != witness_id:
                raise RuntimeError('Original managed history projection differs')
        elif schema == 62:
            witness_id = verify_committed_in_fenced_transaction(database, guard, expected_backend, **paths)
        else:
            raise RuntimeError('Managed history schema is unsupported')
    with database.transaction():
        database.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ WRITE')
        if verify_committed_in_fenced_transaction(database, guard, expected_backend, **paths) != witness_id:
            raise RuntimeError('Original managed committed history differs')
    return witness_id


def authenticate_managed_completion(guard, *, selection_path, binding_path, witness_path,
                                    receipt_path, completion_path, pre62_path=None):
    """Authenticate immutable completion after ordinary writes may resume.

    This verifies recorded evidence only; it deliberately does not compare old
    row snapshots with a database that may contain later accepted Study work.
    The host must independently validate ownership, target identity and lock.
    """
    isolation = guard.get('isolation') or {}
    identity = isolation.get('identity') or {}
    if (guard.get('state') not in ('pending', 'verified') or isolation.get('phase') not in ('verified', 'reattached') or
            identity.get('target') != guard.get('target') or identity.get('trustedDaemon') is not True):
        raise RuntimeError('Original managed completion state is invalid')
    directory = os.path.dirname(witness_path)
    if (not all(os.path.isabs(path or '') and os.path.normpath(path) == path for path in (receipt_path, completion_path)) or
            os.path.dirname(receipt_path) != directory or os.path.dirname(completion_path) != directory or
            os.path.basename(receipt_path) != 'verified-receipt.json' or os.path.basename(completion_path) != 'verified-completion.json'):
        raise RuntimeError('Original managed completion paths are invalid')

    def private_bytes(path):
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 1048576:
                raise RuntimeError('Original managed completion file is invalid')
            return stream.read()

    _, witness, secret, witness_id, _, _ = _load_original_evidence(
        guard, selection_path=selection_path, binding_path=binding_path, witness_path=witness_path, pre62_path=pre62_path)
    receipt = json.loads(private_bytes(receipt_path))
    completion_bytes = private_bytes(completion_path)
    completion = json.loads(completion_bytes)
    binding = guard['defense_binding']
    expected_receipt = {'version': 1, 'target': guard['target'], 'attempt_id': guard['attempt_id'],
        'target_oid': isolation.get('target_oid'), 'target_name': isolation.get('target_name'),
        'system_identifier': isolation.get('system_identifier'), 'control_oid': isolation.get('control_oid'),
        'control_name': isolation.get('control_name'), 'admin_role': isolation.get('admin_role'),
        'daemon_id': identity.get('daemonId'), 'container_id': identity.get('containerId'),
        'network_id': identity.get('networkId'), 'image_id': identity.get('imageId'),
        'backup_filename': binding['backup_filename'], 'backup_sha256': binding['backup_sha256'],
        'selection_sha256': binding['selection_sha256'],
        'pre62_witness_id': guard.get('pre62_witness_id') if guard['starting_schema'] == 59 else None,
        'cut62_witness_id': witness_id, 'schema': 62}
    expected_payload = {'version': 1, 'target': guard['target'], 'attempt_id': guard['attempt_id'], 'identity': identity,
        'target_oid': isolation.get('target_oid'), 'system_identifier': isolation.get('system_identifier'),
        'control_oid': isolation.get('control_oid'), 'backup': binding,
        'pre62_witness_id': expected_receipt['pre62_witness_id'], 'cut62_witness_id': witness_id, 'receipt': expected_receipt}
    canonical = json.dumps(expected_payload, sort_keys=True, separators=(',', ':')).encode()
    signature = hmac.new(secret, b'tempo-disposable-completion-v1\0' + canonical, hashlib.sha256).hexdigest()
    digest = hashlib.sha256(completion_bytes).hexdigest()
    if (receipt != expected_receipt or set(completion) != {'payload', 'signature'} or completion['payload'] != expected_payload or
            not isinstance(completion['signature'], str) or not hmac.compare_digest(completion['signature'], signature) or
            (guard['state'] == 'verified' and (guard.get('verified_schema') != 62 or isolation['phase'] != 'reattached' or
             isolation.get('completion') != {'filename': 'verified-completion.json', 'sha256': digest}))):
        raise RuntimeError('Original managed completion binding differs')
    return {'witness_id': witness_id, 'completion_sha256': digest}


def write_private_json_once(path, value):
    """Publish complete private evidence without replacing an original."""
    if not os.path.isabs(path) or os.path.normpath(path) != path:
        raise RuntimeError('Managed evidence path is invalid')
    parent = os.path.dirname(path)
    metadata = os.lstat(parent)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise RuntimeError('Managed evidence directory is invalid')
    temporary = os.path.join(parent, '.' + os.path.basename(path) + '.' + os.urandom(16).hex() + '.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        with os.fdopen(descriptor, 'w') as stream:
            json.dump(value, stream, sort_keys=True, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path, follow_symlinks=False)
        os.unlink(temporary)
        directory = os.open(parent, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def resync_private_json(path, expected):
    """Re-sync and re-read an already authenticated original, without replacing it."""
    if not os.path.isabs(path) or os.path.normpath(path) != path:
        raise RuntimeError('Managed evidence path is invalid')
    parent = os.path.dirname(path)
    metadata = os.lstat(parent)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise RuntimeError('Managed evidence directory is invalid')

    def read_and_sync(sync):
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 1048576:
                raise RuntimeError('Original managed evidence file is invalid')
            value = json.load(stream)
            if sync:
                os.fsync(stream.fileno())
            return value
    if read_and_sync(True) != expected:
        raise RuntimeError('Original managed evidence differs')
    directory = os.open(parent, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    if read_and_sync(False) != expected:
        raise RuntimeError('Original managed evidence changed')
