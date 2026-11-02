"""Conservative borrowed-transaction operator for position Study ownership.

This adapter inspects a shadow seed and changes owner mode only when the
release and attempt-frontier checks pass. Source roll-forward is not integrated.
No method commits. Callers must roll back on exceptions.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
import re

from tempo_legacy_database.adapters.authored_repertoires.position_study_source import (
    PreparedPositionSource, fence_position_source,
)
from tempo_legacy_database.adapters.study.position_owner_epoch import owner_lock_key, _writer
from tempo_legacy_database.adapters.study.position_fsrs_namespace import fence_namespace
from tempo_legacy_database.adapters.study.position_seed import verify_seed_receipts_page
from tempo_legacy_database.adapters.study.position_seed_donors import select_donors


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
    if (not run or run['status'] != 'complete'
            or run['manifest_count'] != len(definitions)
            or run['seeded_count'] + run['held_count'] != len(definitions)
            or not isinstance(run['allocator_preflight_json'], dict)
            or run['allocator_preflight_json'].get('complete') is not True):
        raise OperatorHold('position_seed_incomplete')
    if not definitions:
        raise OperatorHold('position_empty_manifest')
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
    by_key = {}
    for decision in decisions:
        fact = decision.selected or decision.hold
        if fact.repertoire_id != rep or fact.variant is None or fact.position is None:
            raise OperatorHold('position_donor_map_incomplete')
        key = (fact.variant, fact.position)
        if key in by_key:
            raise OperatorHold('position_donor_map_incomplete')
        by_key[key] = decision
    if set(by_key) != set(definitions):
        raise OperatorHold('position_donor_map_incomplete')
    receipts = database.execute_native('''SELECT variant,position,disposition,hold_reason,donor_card_id,donor_card_revision,
        donor_fingerprint FROM study_position_seed_outcomes WHERE repertoire_id=%s AND owner_epoch=%s''',
        (rep, epoch)).fetchall()
    if len(receipts) != len(by_key):
        raise OperatorHold('position_donor_map_incomplete')
    for receipt in receipts:
        decision = by_key.get((receipt['variant'], receipt['position']))
        if decision is None:
            raise OperatorHold('position_donor_map_incomplete')
        donor = decision.selected
        if donor is None:
            if receipt['disposition'] != 'hold' or receipt['hold_reason'] != decision.hold.reason:
                raise OperatorHold('position_donor_selection_changed')
        elif (receipt['disposition'] != 'seeded'
                or (receipt['donor_card_id'], receipt['donor_card_revision'], receipt['donor_fingerprint'])
                != (donor.donor_card_id, donor.donor_revision, donor.fsrs_fingerprint)):
            raise OperatorHold('position_donor_selection_changed')
    # Shared cards must never be silently assigned to a different owner.
    mixed = database.execute_native('''WITH owners AS (
        SELECT id card_id,repertoire_id FROM cards WHERE repertoire_id IS NOT NULL
        UNION SELECT card_id,repertoire_id FROM repertoire_cards)
        SELECT 1 FROM owners mine JOIN owners other ON other.card_id=mine.card_id
        WHERE mine.repertoire_id=%s AND other.repertoire_id<>mine.repertoire_id LIMIT 1''',
        (rep,)).fetchone()
    if mixed:
        raise OperatorHold('position_mixed_physical_owner')
    return {'repertoire_id': rep, 'epoch': epoch, 'source_digest': prepared.authored_token.authored_digest,
            'manifest_digest': manifest, 'verified': verified, 'held': run['held_count']}


_REQUIRED_QUALIFICATIONS = ('fresh_introduction', 'games_j2', 'online_only_l2', 'operator_n2')


def validate_release_evidence(database, prepared, *, epoch, registration, deployment, guard,
                              qualification, revision, target):
    """Validate raw CLI receipts and qualification in the target writer.

    The host adapter has inspected the registered running target and passes the
    actual records through; no client-supplied success boolean is accepted.
    """
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise OperatorHold('position_release_identity_invalid')
    if not isinstance(target, str) or not re.fullmatch(r'[0-9a-f]{24}', target):
        raise OperatorHold('position_release_identity_invalid')
    if not all(isinstance(value, dict) for value in (deployment, guard, qualification, registration)):
        raise OperatorHold('position_release_evidence_unavailable')
    try:
        volumes = sorted(volume['name'] for volume in registration['volumes'].values())
        key_bytes = json.dumps([registration.get('daemonId') or registration['context'], volumes],
                               ensure_ascii=False, separators=(',', ':')).encode()
        if registration.get('version') != 1 or hashlib.sha256(key_bytes).hexdigest()[:24] != target:
            raise OperatorHold('position_release_target_mismatch')
        expected_volume = registration['volumes'][registration['postgresVolumeKey']]['name']
    except (KeyError, TypeError) as exc:
        raise OperatorHold('position_registration_invalid') from exc
    backup = deployment.get('backup') or {}
    if not isinstance(backup, dict) or not isinstance(deployment.get('evidence'), dict):
        raise OperatorHold('position_deployment_receipt_unqualified')
    if (deployment.get('version') != 1 or deployment.get('revision') != revision
            or deployment.get('schema') != 57 or deployment['evidence'].get('commit') != revision
            or not deployment.get('verified_at') or not isinstance(deployment.get('images'), dict)
            or not deployment['images'] or not all(deployment['images'].values())
            or backup.get('verified') is not True or not backup.get('filename')
            or not backup.get('restore_created') or not backup.get('restore_removed')):
        raise OperatorHold('position_deployment_receipt_unqualified')
    dbname = database.execute_native('SELECT current_database()').fetchone()[0]
    if (not isinstance(guard.get('database'), dict) or not isinstance(guard.get('backup'), dict)
            or guard.get('version') != 1 or guard.get('target') != target or guard.get('state') != 'verified'
            or guard.get('verified_schema') != 57 or not guard.get('verified_at')
            or guard['database'].get('name') != dbname or guard['database'].get('volume') != expected_volume
            or guard['backup'].get('verified') is not True or not guard['backup'].get('filename')):
        raise OperatorHold('position_migration_receipt_unqualified')
    versions = [row[0] for row in database.execute_native('SELECT version FROM tempo_schema_migrations ORDER BY version').fetchall()]
    if versions != list(range(1, 58)):
        raise OperatorHold('position_schema_not_ready')
    expected = {'version': 1, 'revision': revision, 'schema': 57, 'target': target,
                'repertoire_id': prepared.authored_token.repertoire_id, 'epoch': epoch,
                'source_digest': prepared.authored_token.authored_digest}
    if any(qualification.get(key) != value for key, value in expected.items()):
        raise OperatorHold('position_qualification_identity_mismatch')
    checks = qualification.get('checks')
    if (not isinstance(checks, dict) or any(not isinstance(checks.get(name), dict)
            or checks[name].get('status') != 'passed' or not isinstance(checks[name].get('evidence'), str)
            or not checks[name]['evidence'].strip() for name in _REQUIRED_QUALIFICATIONS)):
        raise OperatorHold('position_qualification_incomplete')
    payload = json.dumps(qualification, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    return {'target': target, 'revision': revision,
            'qualification_sha256': hashlib.sha256(payload).hexdigest(), 'backup': backup['filename']}


def fence_old_attempt_frontier(database, repertoire_id):
    """Hold if any scoped, issued legacy attempt remains unfinished.

    Must be called with the exclusive owner advisory lock held. Unissued queue
    rows are intentionally ignored. A missing admission uses all physical owners;
    ambiguous ownership is held, not assigned retrospectively.
    """
    row = database.execute_native('''SELECT o.queue_entry_id FROM queue_attempt_origins o
        WHERE o.issued_as_head=1 AND o.content_type='opening'
          AND (o.last_status <> 'complete' OR o.review_result_json IS NULL)
          AND NOT EXISTS (SELECT 1 FROM study_position_issued_plans p
              WHERE p.queue_entry_id=o.queue_entry_id AND p.card_id=o.card_id AND p.card_revision=o.revision)
          AND (o.admission_repertoire_id=%s OR (o.admission_repertoire_id IS NULL AND
              (EXISTS (SELECT 1 FROM cards c WHERE c.id=o.card_id AND c.repertoire_id=%s)
               OR EXISTS (SELECT 1 FROM repertoire_cards rc WHERE rc.card_id=o.card_id AND rc.repertoire_id=%s)
               OR (NOT EXISTS (SELECT 1 FROM cards c WHERE c.id=o.card_id AND c.repertoire_id IS NOT NULL)
                   AND NOT EXISTS (SELECT 1 FROM repertoire_cards rc WHERE rc.card_id=o.card_id)))))
        LIMIT 1''', (repertoire_id, repertoire_id, repertoire_id)).fetchone()
    if row:
        raise OperatorHold('position_old_issued_attempt_unfinished')


def transition_in_writer(database, prepared, request, *, action, epoch, operation_id, now, release=None):
    """Pause an active owner with an atomic receipt.

    Stage, activate and resume require concrete release evidence and a quiescent
    frontier. Retrying an identical successful operation returns its saved receipt.
    """
    if action not in ('stage', 'pause', 'activate', 'resume', 'rollforward'):
        raise OperatorHold('position_operator_unknown_action')
    if not isinstance(operation_id, str) or not operation_id or not isinstance(epoch, str) or not epoch:
        raise OperatorHold('position_operator_identity_required')
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise OperatorHold('position_operator_timestamp_required')
    rep = _lock(database, prepared, source_required=action != 'pause')
    identity = {'action': action, 'repertoire_id': rep, 'epoch': epoch,
                'source_digest': prepared.authored_token.authored_digest,
                'release_revision': release.get('revision') if isinstance(release, dict) else None,
                'release_target': release.get('target') if isinstance(release, dict) else None}
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
            if (not owner or owner['epoch'] != epoch
                    or (action != 'pause' and owner['source_digest'] != identity['source_digest'])):
                raise OperatorHold('position_owner_epoch_or_source_changed')
            if action == 'rollforward':
                raise OperatorHold('position_rollforward_not_integrated')
            if action == 'pause':
                if owner['mode'] != 'active':
                    raise OperatorHold('position_owner_not_active')
                mode = 'paused'
            else:
                allowed = {'stage': ('inactive', 'shadow'), 'activate': ('staged',), 'resume': ('paused',)}
                if owner['mode'] not in allowed[action]:
                    raise OperatorHold('position_owner_invalid_transition')
                if not isinstance(release, dict) or set(release) != {'registration', 'deployment', 'guard', 'qualification', 'revision', 'target'}:
                    raise OperatorHold('position_release_evidence_unavailable')
                evidence = validate_release_evidence(database, prepared, epoch=epoch, **release)
                preflight_in_writer(database, prepared, request, epoch=epoch)
                fence_old_attempt_frontier(database, rep)
                mode = 'staged' if action == 'stage' else 'active'
            result = identity | {'mode': mode, 'operation_id': operation_id,
                                 'owner_source_digest': owner['source_digest']}
            if action != 'pause':
                result['release'] = evidence
            database.execute_native("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,response_json) VALUES (%s,%s,%s,'complete',%s)",
                                    (operation_id, 'position_owner.transition', digest, json.dumps(result, sort_keys=True)))
            database.execute_native('UPDATE study_position_owners SET mode=%s,updated_at=%s WHERE repertoire_id=%s', (mode, now, rep))
    except Exception:
        database.execute_native('ROLLBACK TO SAVEPOINT position_owner_operator')
        database.execute_native('RELEASE SAVEPOINT position_owner_operator')
        raise
    database.execute_native('RELEASE SAVEPOINT position_owner_operator')
    return result


def _prepare_request(repertoire_id):
    """Read a complete source and bounded donor facts before the writer fence."""
    from app.repertoire.position_index import build_index
    from tempo_evidence_math.position_learning import Hold, SourceCertificate
    from tempo_legacy_database.adapters.authored_repertoires.position_study_source import prepare_position_source
    from tempo_legacy_database.adapters.postgres_store import connection
    from tempo_legacy_database.adapters.study.position_seed_inventory import (
        DonorFact, InventoryRequest, SourceFacts, read_seed_donor_page,
    )
    with connection(read_only=True, authoritative=True, repeatable_read=True) as database:
        prepared = prepare_position_source(database, repertoire_id)
        if isinstance(prepared, Hold):
            raise OperatorHold(prepared.reason)
        index = build_index(prepared.snapshot)
        ids = sorted({binding.presentation_id for node in index.nodes.values()
                      for occurrence in node.occurrences for binding in occurrence.bindings
                      if binding.presentation_id})
        if len(ids) > 10000:
            raise OperatorHold('position_donor_map_exceeds_bound')
        donors = []
        for offset in range(0, len(ids), 500):
            rows = read_seed_donor_page(database, candidate_card_ids=tuple(ids[offset:offset + 500])).fetchall()
            for row in rows:
                facts = dict(row)
                facts['owner_repertoire_ids'] = tuple(facts['owner_repertoire_ids'])
                for field in ('archived', 'pending_validation'):
                    value = facts[field]
                    facts[field] = bool(value) if value in (0, 1) else None
                donors.append(DonorFact(**facts))
        membership = prepared.snapshot.membership
        cert = SourceCertificate(membership.snapshot_id, membership.source_revision, membership.digest, True, True)
        request = InventoryRequest((SourceFacts(prepared.snapshot, index, cert),), tuple(donors))
    return prepared, request


def main():
    """Private CLI transport executed by the registered foreground worker."""
    import argparse
    import base64
    from datetime import timezone
    from tempo_legacy_database.adapters.postgres_store import connection
    parser = argparse.ArgumentParser()
    parser.add_argument('--request-base64', required=True)
    args = parser.parse_args()
    try:
        raw = base64.b64decode(args.request_base64, validate=True)
        if len(raw) > 100000:
            raise OperatorHold('position_operator_request_too_large')
        payload = json.loads(raw)
        if not isinstance(payload, dict) or payload.get('version') != 1 or type(payload.get('plan')) is not bool:
            raise OperatorHold('position_operator_request_invalid')
        action, rep, epoch = payload['action'], payload['repertoire_id'], payload['epoch']
        if action not in ('stage', 'activate', 'pause', 'resume', 'rollforward') or not all(
                isinstance(value, str) and value for value in (rep, epoch, payload.get('operation_id'))):
            raise OperatorHold('position_operator_request_invalid')
        prepared, request = _prepare_request(rep)
        with connection() as database:
            if payload['plan']:
                _lock(database, prepared, source_required=action != 'pause')
                owner = database.execute_native('''SELECT o.epoch,o.mode,e.source_digest FROM study_position_owners o
                    JOIN study_position_epochs e ON e.repertoire_id=o.repertoire_id AND e.epoch=o.epoch
                    WHERE o.repertoire_id=%s''', (rep,)).fetchone()
                if (not owner or owner['epoch'] != epoch or (action != 'pause'
                        and owner['source_digest'] != prepared.authored_token.authored_digest)):
                    raise OperatorHold('position_owner_epoch_or_source_changed')
                modes = {'stage': ('inactive', 'shadow'), 'activate': ('staged',),
                         'resume': ('paused',), 'pause': ('active',)}
                if action != 'rollforward' and owner['mode'] not in modes[action]:
                    raise OperatorHold('position_owner_invalid_transition')
                result = {'plan': True, 'action': action, 'repertoire_id': rep, 'epoch': epoch,
                          'current_mode': owner['mode'], 'source_digest': prepared.authored_token.authored_digest}
                if action not in ('pause', 'rollforward'):
                    result['release'] = validate_release_evidence(database, prepared, epoch=epoch, **payload['release'])
                    result['preflight'] = preflight_in_writer(database, prepared, request, epoch=epoch)
                    fence_old_attempt_frontier(database, rep)
                elif action == 'rollforward':
                    raise OperatorHold('position_rollforward_not_integrated')
            else:
                result = transition_in_writer(database, prepared, request, action=action, epoch=epoch,
                    operation_id=payload['operation_id'], now=datetime.now(timezone.utc),
                    release=payload['release'] if action != 'pause' else None)
        print(json.dumps(result, sort_keys=True))
    except (ValueError, KeyError, TypeError) as exc:
        raise SystemExit('position operator held: ' + str(exc)) from exc


if __name__ == '__main__':
    main()
