"""Read-only evidence checks for a registered managed PostgreSQL cutover.

This module does not open admission, detach networks, migrate, or provide a CLI.
The owning launcher must hold the target lock, exclusively control the Docker
daemon and host administration, and independently verify the recorded Docker
topology before and throughout use of the pinned connection.
"""
from __future__ import annotations

import os
import re
from psycopg.pq import TransactionStatus

from database.tooling.verify_postgres_cli_state import (
    authenticate_pre62_59, check_captured_cut62_v6, cut62_witness_id,
    load_cut62_attempt_binding, load_cut62_defense_selection, load_cut62_witness,
    load_pre62_59, verify_cut62_v6, verify_pre62_59,
)


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
    selection, selection_sha = load_cut62_defense_selection(selection_path)
    loaded_binding = load_cut62_attempt_binding(binding_path, selection, selection_sha)
    witness, secret = load_cut62_witness(witness_path)
    witness_id = cut62_witness_id(witness, secret)
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
    if check_captured_cut62_v6(database, witness, secret).get('witness_id') != witness_id:
        raise RuntimeError('Original managed schema 61 history differs')
    return selection, witness, secret, witness_id


def verify_committed_in_fenced_transaction(database, guard, expected_backend, *, selection_path,
                                          binding_path, witness_path, pre62_path=None):
    """Verify the original projected result while the pinned fence is closed."""
    assert_fenced_pinned_session(database, guard, expected_backend, read_only=True)
    _, witness, secret, witness_id, _, _ = _load_original_evidence(
        guard, selection_path=selection_path, binding_path=binding_path, witness_path=witness_path, pre62_path=pre62_path)
    if verify_cut62_v6(database, witness, secret).get('witness_id') != witness_id:
        raise RuntimeError('Original managed committed history differs')
    return witness_id
