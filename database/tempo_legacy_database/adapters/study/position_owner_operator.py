"""Conservative borrowed-transaction operator for position Study ownership.

This preliminary adapter can inspect a completed shadow seed and pause an
active owner. Stage, activation, resume and source roll-forward remain unavailable
until their release evidence and attempt-frontier adapters are integrated.
No method commits. Callers must roll back on exceptions.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from tempo_legacy_database.adapters.authored_repertoires.position_study_source import (
    PreparedPositionSource, fence_position_source,
)
from tempo_legacy_database.adapters.study.position_owner_epoch import owner_lock_key, _writer
from tempo_legacy_database.adapters.study.position_fsrs_namespace import fence_namespace
from tooling.position_study_seed import verify_seed_receipts_page
from tooling.position_study_donors import select_donors


class OperatorHold(ValueError):
    pass


def _lock(database, prepared, *, source_required=True):
    if not _writer(database):
        raise OperatorHold('borrowed_owner_writer_transaction_required')
    if not isinstance(prepared, PreparedPositionSource):
        raise OperatorHold('position_source_owner_preparation_required')
    if source_required:
        hold = fence_position_source(database, prepared)
        if hold is not None:
            raise OperatorHold(hold.reason)
    rep = prepared.authored_token.repertoire_id
    locked = database.execute_native(
        'SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',
        (owner_lock_key(rep),)).fetchone()
    if not locked or not locked[0]:
        raise OperatorHold('position_owner_busy')
    return rep


def preflight_in_writer(database, prepared, request, *, epoch):
    """Verify the complete seed and current donor facts while holding fences.

    Returns observable counts. Success is not activation authorization: release
    qualification and a fenced old-attempt frontier are still required.
    """
    rep = _lock(database, prepared)
    fence_namespace(database)
    run = database.execute_native('''SELECT * FROM study_position_seed_runs
        WHERE repertoire_id=%s AND owner_epoch=%s FOR UPDATE NOWAIT''', (rep, epoch)).fetchone()
    definitions = {(item.definition.key.variant, item.definition.key.position): item.definition
                   for route in prepared.routes for item in route.decisions}
    manifest = run['manifest_digest'] if run else None
    if (not run or run['status'] != 'complete' or run['manifest_digest'] != manifest
            or run['manifest_count'] != len(definitions)
            or run['seeded_count'] + run['held_count'] != len(definitions)
            or not isinstance(run['allocator_preflight_json'], dict)
            or run['allocator_preflight_json'].get('complete') is not True):
        raise OperatorHold('position_seed_incomplete')
    if not definitions:
        raise OperatorHold('position_empty_manifest')
    if run['held_count']:
        raise OperatorHold('position_seed_contains_holds')
    cursor, verified = None, 0
    while True:
        page = verify_seed_receipts_page(database, prepared, request, epoch=epoch, after=cursor, limit=100)
        verified += page['verified']
        cursor = page['next_after']
        if cursor is None:
            break
    if verified != len(definitions):
        raise OperatorHold('position_seed_receipt_count_mismatch')
    decisions = select_donors(request)
    selected = {(d.selected.variant, d.selected.position): d.selected for d in decisions if d.selected}
    if len(decisions) != len(definitions) or set(selected) != set(definitions):
        raise OperatorHold('position_donor_map_incomplete')
    receipts = database.execute_native('''SELECT variant,position,donor_card_id,donor_card_revision,
        donor_fingerprint FROM study_position_seed_outcomes WHERE repertoire_id=%s AND owner_epoch=%s''',
        (rep, epoch)).fetchall()
    if len(receipts) != len(selected):
        raise OperatorHold('position_donor_map_incomplete')
    for receipt in receipts:
        donor = selected.get((receipt['variant'], receipt['position']))
        if (donor is None or (receipt['donor_card_id'], receipt['donor_card_revision'], receipt['donor_fingerprint'])
                != (donor.donor_card_id, donor.donor_revision, donor.fsrs_fingerprint)):
            raise OperatorHold('position_donor_selection_changed')
    # Shared cards must never be silently assigned to a different owner.
    mixed = database.execute_native('''SELECT 1 FROM repertoire_cards mine
        JOIN repertoire_cards other ON other.card_id=mine.card_id
        WHERE mine.repertoire_id=%s AND other.repertoire_id<>mine.repertoire_id LIMIT 1''',
        (rep,)).fetchone()
    if mixed:
        raise OperatorHold('position_mixed_physical_owner')
    return {'repertoire_id': rep, 'epoch': epoch, 'source_digest': prepared.authored_token.authored_digest,
            'manifest_digest': manifest, 'verified': verified,
            'activation_hold': 'position_release_and_attempt_frontier_unqualified'}


def transition_in_writer(database, prepared, request, *, action, epoch, operation_id, now):
    """Pause an active owner with an atomic receipt.

    Stage/activation/resume/rollforward fail closed until their actual integration is
    present. Retrying an identical successful operation returns its saved receipt.
    """
    if action not in ('stage', 'pause', 'activate', 'resume', 'rollforward'):
        raise OperatorHold('position_operator_unknown_action')
    if not isinstance(operation_id, str) or not operation_id or not isinstance(epoch, str) or not epoch:
        raise OperatorHold('position_operator_identity_required')
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise OperatorHold('position_operator_timestamp_required')
    rep = _lock(database, prepared, source_required=action != 'pause')
    identity = {'action': action, 'repertoire_id': rep, 'epoch': epoch,
                'source_digest': prepared.authored_token.authored_digest}
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    database.execute_native('SAVEPOINT position_owner_operator')
    try:
        existing = database.execute_native('SELECT command_name,request_hash,state,response_json FROM operation_receipts WHERE operation_id=%s FOR UPDATE', (operation_id,)).fetchone()
        if existing:
            if existing['command_name'] != 'position_owner.transition' or existing['request_hash'] != digest or existing['state'] != 'complete':
                raise OperatorHold('position_operation_conflict')
            result = json.loads(existing['response_json'])
        else:
            owner = database.execute_native('''SELECT o.epoch,o.mode,e.source_digest FROM study_position_owners o
                JOIN study_position_epochs e ON e.repertoire_id=o.repertoire_id AND e.epoch=o.epoch
                WHERE o.repertoire_id=%s FOR UPDATE OF o NOWAIT''', (rep,)).fetchone()
            if not owner or owner['epoch'] != epoch or owner['source_digest'] != identity['source_digest']:
                raise OperatorHold('position_owner_epoch_or_source_changed')
            if action in ('stage', 'activate', 'resume', 'rollforward'):
                raise OperatorHold('position_release_and_attempt_frontier_unqualified')
            if owner['mode'] != 'active':
                raise OperatorHold('position_owner_not_active')
            mode = 'paused'
            result = identity | {'mode': mode, 'operation_id': operation_id}
            database.execute_native("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,response_json) VALUES (%s,%s,%s,'complete',%s)",
                                    (operation_id, 'position_owner.transition', digest, json.dumps(result, sort_keys=True)))
            database.execute_native('UPDATE study_position_owners SET mode=%s,updated_at=%s WHERE repertoire_id=%s', (mode, now, rep))
    except Exception:
        database.execute_native('ROLLBACK TO SAVEPOINT position_owner_operator')
        database.execute_native('RELEASE SAVEPOINT position_owner_operator')
        raise
    database.execute_native('RELEASE SAVEPOINT position_owner_operator')
    return result
