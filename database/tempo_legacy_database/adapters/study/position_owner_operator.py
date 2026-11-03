"""Conservative borrowed-transaction operator for position Study ownership.

This adapter inspects a shadow seed and changes owner mode only when the
release and attempt-frontier checks pass. Compatible roll-forward preserves retained history.
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
from tempo_legacy_database.ddl.schema_version import POSTGRES_SCHEMA_VERSION
from tempo_legacy_database.adapters.study.position_fsrs_namespace import fence_namespace
from tempo_legacy_database.adapters.study.position_seed import (
    preview_seed_page, seed_shadow_page, verify_seed_receipts_page,
)
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



def _lock_repertoire(database, repertoire_id):
    """Pause without requiring current authored source to be readable."""
    if not _writer(database):
        raise OperatorHold('borrowed_owner_writer_transaction_required')
    if not isinstance(repertoire_id, str) or not repertoire_id:
        raise OperatorHold('repertoire_required')
    locked = database.execute_native('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',
                                     (owner_lock_key(repertoire_id),)).fetchone()
    if not locked or not locked[0]:
        raise OperatorHold('position_owner_busy')
    return repertoire_id


def _seed_request_identity(prepared, *, epoch, page_size, release):
    if not isinstance(epoch, str) or not epoch or type(page_size) is not int or not 1 <= page_size <= 100:
        raise OperatorHold('position_seed_request_invalid')
    if not isinstance(release, dict) or set(release) != {'registration', 'deployment', 'guard', 'qualification', 'revision', 'target', 'operation'}:
        raise OperatorHold('position_release_evidence_unavailable')
    return {'action': 'seed', 'repertoire_id': prepared.authored_token.repertoire_id,
            'epoch': epoch, 'page_size': page_size, 'source_token': prepared.authored_token.as_dict(),
            'release_revision': release['revision'], 'release_target': release['target']}


def plan_seed_in_writer(database, prepared, request, *, epoch, page_size, release):
    """Preview a seed page without allocating IDs or changing durable state."""
    _seed_request_identity(prepared, epoch=epoch, page_size=page_size, release=release)
    rep = _lock(database, prepared)
    evidence = validate_release_evidence(database, prepared, epoch=epoch, **release)
    fence_namespace(database)
    owner = database.execute_native('SELECT epoch,mode FROM study_position_owners WHERE repertoire_id=%s', (rep,)).fetchone()
    if owner and (owner['epoch'] != epoch or owner['mode'] not in ('inactive', 'shadow')):
        raise OperatorHold('position_owner_not_shadow')
    existing_epoch = database.execute_native('SELECT source_digest FROM study_position_epochs WHERE repertoire_id=%s AND epoch=%s', (rep, epoch)).fetchone()
    if existing_epoch and existing_epoch['source_digest'] != prepared.authored_token.authored_digest:
        raise OperatorHold('position_epoch_source_conflict')
    run = database.execute_native('''SELECT status,source_token_json,allocator_preflight_json,checkpoint_variant,
        checkpoint_position,seeded_count,held_count FROM study_position_seed_runs WHERE repertoire_id=%s AND owner_epoch=%s''', (rep, epoch)).fetchone()
    if run and run['source_token_json'] != prepared.authored_token.as_dict():
        raise OperatorHold('position_seed_run_conflict')
    after = (run['checkpoint_variant'], run['checkpoint_position']) if run and run['checkpoint_variant'] is not None else None
    page = preview_seed_page(prepared, request, after=after, limit=page_size)
    decisions = []
    for decision in page.decisions:
        item = decision.selected or decision.hold
        decisions.append({'variant': item.variant, 'position': item.position,
                          'disposition': 'seed_candidate' if decision.selected else 'hold',
                          'donor_card_id': decision.selected.donor_card_id if decision.selected else None,
                          'hold_reason': decision.hold.reason if decision.hold else None})
    return {'plan': True, 'action': 'seed', 'repertoire_id': rep, 'epoch': epoch,
            'source_digest': prepared.authored_token.authored_digest, 'page_size': page_size,
            'run': dict(run) if run else None, 'decisions': decisions,
            'next_after': page.next_after, 'release': evidence}


def seed_page_in_writer(database, prepared, request, *, epoch, operation_id, page_size, now, release):
    """Commit one F2 page and its replay receipt in the caller's transaction."""
    if not isinstance(operation_id, str) or not operation_id:
        raise OperatorHold('position_operator_identity_required')
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise OperatorHold('position_operator_timestamp_required')
    identity = _seed_request_identity(prepared, epoch=epoch, page_size=page_size, release=release)
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    _lock(database, prepared)
    database.execute_native('SAVEPOINT position_owner_seed')
    try:
        receipt = database.execute_native('SELECT command_name,request_hash,state,response_json FROM operation_receipts WHERE operation_id=%s FOR UPDATE', (operation_id,)).fetchone()
        if receipt:
            if receipt['command_name'] != 'position_owner.seed' or receipt['request_hash'] != digest or receipt['state'] != 'complete':
                raise OperatorHold('position_operation_conflict')
            result = json.loads(receipt['response_json'])
        else:
            evidence = validate_release_evidence(database, prepared, epoch=epoch, **release)
            page = seed_shadow_page(database, prepared, request, epoch=epoch, now=now, page_size=page_size)
            result = {'action': 'seed', 'operation_id': operation_id, 'repertoire_id': identity['repertoire_id'],
                      'epoch': epoch, 'source_digest': prepared.authored_token.authored_digest,
                      'page_size': page_size, 'page': page, 'release': evidence}
            database.execute_native("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,response_json) VALUES (%s,%s,%s,'complete',%s)",
                                    (operation_id, 'position_owner.seed', digest, json.dumps(result, sort_keys=True)))
    except Exception:
        database.execute_native('ROLLBACK TO SAVEPOINT position_owner_seed')
        database.execute_native('RELEASE SAVEPOINT position_owner_seed')
        raise
    database.execute_native('RELEASE SAVEPOINT position_owner_seed')
    return result


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
                              qualification, revision, target, operation=None):
    """Validate raw CLI receipts and qualification in the target writer.

    The host adapter has inspected the registered running target and passes the
    actual records through; no client-supplied success boolean is accepted.
    """
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        raise OperatorHold('position_release_identity_invalid')
    if not isinstance(target, str) or not re.fullmatch(r'[0-9a-f]{24}', target):
        raise OperatorHold('position_release_identity_invalid')
    if not all(isinstance(value, dict) for value in (deployment, qualification, registration)):
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
    if not isinstance(deployment.get('evidence'), dict):
        raise OperatorHold('position_deployment_receipt_unqualified')
    if (deployment.get('version') != 1 or deployment.get('revision') != revision
            or deployment.get('schema') != POSTGRES_SCHEMA_VERSION or deployment['evidence'].get('commit') != revision
            or not deployment.get('verified_at') or not isinstance(deployment.get('images'), dict)
            or not deployment['images'] or not all(deployment['images'].values())):
        raise OperatorHold('position_deployment_receipt_unqualified')
    if operation is not None and not isinstance(operation, dict):
        raise OperatorHold('position_operation_receipt_invalid')
    if operation and (operation.get('phase') == 'applying_migrations' or
            (operation.get('phase') == 'failed' and operation.get('failed_phase') == 'applying_migrations')):
        raise OperatorHold('position_migration_receipt_unqualified')
    backups = [deployment.get('backup')]
    if operation and operation.get('phase') == 'backup_verified' and operation.get('revision') == revision and operation.get('target') == registration.get('project'):
        backups.append(operation.get('backup'))
    if isinstance(guard, dict):
        dbname = database.execute_native('SELECT current_database()').fetchone()[0]
        if (not isinstance(guard.get('database'), dict) or not isinstance(guard.get('backup'), dict)
                or guard.get('version') != 1 or guard.get('target') != target or guard.get('state') != 'verified'
                or guard.get('verified_schema') != POSTGRES_SCHEMA_VERSION or not guard.get('verified_at')
                or guard['database'].get('name') != dbname or guard['database'].get('volume') != expected_volume):
            raise OperatorHold('position_migration_receipt_unqualified')
        backups.append(guard['backup'])
    elif guard is not None:
        raise OperatorHold('position_migration_receipt_unqualified')
    backup = next((candidate for candidate in backups if isinstance(candidate, dict)
                   and candidate.get('verified') is True and candidate.get('filename')
                   and candidate.get('restore_created') is True and candidate.get('restore_removed') is True), None)
    if backup is None:
        raise OperatorHold('position_backup_receipt_unqualified')
    versions = [row[0] for row in database.execute_native('SELECT version FROM tempo_schema_migrations ORDER BY version').fetchall()]
    if versions != list(range(1, POSTGRES_SCHEMA_VERSION + 1)):
        raise OperatorHold('position_schema_not_ready')
    expected = {'version': 1, 'revision': revision, 'schema': POSTGRES_SCHEMA_VERSION, 'target': target,
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
    from tempo_legacy_database.adapters.lifecycle.queue_attempt_abandonment import installed
    abandoned = '''AND NOT EXISTS (SELECT 1 FROM queue_attempt_abandonments a
        WHERE a.queue_entry_id=o.queue_entry_id AND a.card_id=o.card_id AND a.revision=o.revision)''' if installed(database) else ''
    row = database.execute_native('''SELECT o.queue_entry_id FROM queue_attempt_origins o
        WHERE o.issued_as_head=1 AND o.content_type='opening'
          AND (o.last_status <> 'complete' OR o.review_result_json IS NULL)
          ''' + abandoned + '''
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



def _compatible_states(database, prepared, *, epoch, expected_digest):
    """Lock all states and classify them against current fenced authority."""
    from app.study.position_learning.source_change_policy import ScopedPositionSource, compare_persisted_definition
    from tempo_evidence_math.position_learning import PositionDefinition, PositionKey, SourceCertificate
    rep = prepared.authored_token.repertoire_id
    rows = database.execute_native('''SELECT variant,position,expected_uci,policy_version,owner_epoch,source_digest
        FROM study_position_states WHERE repertoire_id=%s ORDER BY variant,position
        LIMIT 10001 FOR UPDATE NOWAIT''', (rep,)).fetchall()
    if not rows or len(rows) > 10000:
        raise OperatorHold('position_rollforward_state_count_invalid')
    definitions = {}
    for route in prepared.routes:
        for occurrence in route.decisions:
            definition = occurrence.definition
            key = (definition.key.variant, definition.key.position)
            if key in definitions and definitions[key] != definition:
                raise OperatorHold('position_source_definition_conflict')
            definitions[key] = definition
    if set(definitions) != {(row['variant'], row['position']) for row in rows}:
        raise OperatorHold('position_rollforward_position_set_changed')
    membership = prepared.snapshot.membership
    certificate = SourceCertificate(membership.snapshot_id, membership.source_revision, membership.digest, True, True)
    decisions = []
    for row in rows:
        if (row['owner_epoch'], row['source_digest']) != (epoch, expected_digest):
            raise OperatorHold('position_state_epoch_or_source_changed')
        key = PositionKey(rep, row['position'], row['variant'])
        previous = PositionDefinition(key, row['expected_uci'], row['policy_version'])
        routes = tuple(route for route in prepared.routes
                       if any(item.definition.key == key for item in route.decisions))
        scope = ScopedPositionSource(key, certificate, prepared.authored_token.authored_digest, routes)
        decisions.append(compare_persisted_definition(previous, scope).as_record())
    return decisions


def _fence_retained_frontier(database, repertoire_id):
    """Keep unfinished attempts and negative obligations on their original epoch."""
    # Serialize new durable evidence/effects while inspecting the bounded frontier.
    # NOWAIT avoids a lock-order wait against an existing writer.
    database.execute_native('LOCK TABLE opening_evidence_attempts, study_game_feedback IN SHARE MODE NOWAIT')
    if database.execute_native('''SELECT 1 FROM opening_evidence_attempts
        WHERE repertoire_id=%s AND state<>'complete' LIMIT 1''', (repertoire_id,)).fetchone():
        raise OperatorHold('position_evidence_attempt_unfinished')
    if database.execute_native('''SELECT 1 FROM study_position_issued_plans p
        LEFT JOIN queue_attempt_origins o ON o.queue_entry_id=p.queue_entry_id AND o.card_id=p.card_id
          AND o.revision=p.card_revision
        WHERE p.repertoire_id=%s AND (o.queue_entry_id IS NULL OR o.last_status<>'complete'
          OR o.review_result_json IS NULL) LIMIT 1''', (repertoire_id,)).fetchone():
        raise OperatorHold('position_issued_attempt_unfinished')
    if database.execute_native('''SELECT 1 FROM study_game_feedback f
        WHERE f.status IN ('pending','held','applied') AND (f.card_id IS NULL
          OR EXISTS(SELECT 1 FROM cards c WHERE c.id=f.card_id AND c.repertoire_id=%s)
          OR EXISTS(SELECT 1 FROM repertoire_cards rc WHERE rc.card_id=f.card_id AND rc.repertoire_id=%s))
        LIMIT 1''', (repertoire_id, repertoire_id)).fetchone():
        raise OperatorHold('position_negative_feedback_unsettled')


def _prior_owner_receipt(database, repertoire_id, epoch, source_digest, *, action=None, mode=None):
    row = database.execute_native('''SELECT operation_id,response_json FROM operation_receipts
        WHERE command_name='position_owner.transition' AND state='complete'
          AND response_json::jsonb->>'repertoire_id'=%s AND response_json::jsonb->>'epoch'=%s
          AND response_json::jsonb->>'source_digest'=%s
          AND (%s::text IS NULL OR response_json::jsonb->>'action'=%s)
          AND (%s::text IS NULL OR response_json::jsonb->>'mode'=%s)
        ORDER BY operation_id LIMIT 1''', (repertoire_id, epoch, source_digest, action, action, mode, mode)).fetchone()
    return row


def _retained_preflight(database, prepared, *, epoch):
    rep, digest = prepared.authored_token.repertoire_id, prepared.authored_token.authored_digest
    receipt = _prior_owner_receipt(database, rep, epoch, digest, action='rollforward')
    if not receipt:
        raise OperatorHold('position_seed_or_rollforward_receipt_missing')
    saved = json.loads(receipt['response_json'])
    decisions = _compatible_states(database, prepared, epoch=epoch, expected_digest=digest)
    if (saved.get('mode') != 'paused' or saved.get('state_count') != len(decisions)
            or any(item['disposition'] != 'preserve_schedule' for item in decisions)):
        raise OperatorHold('position_rollforward_provenance_changed')
    fence_namespace(database)
    return {'repertoire_id': rep, 'epoch': epoch, 'source_digest': digest,
            'verified': len(decisions), 'rollforward_operation_id': receipt['operation_id']}


def preview_rollforward_in_writer(database, prepared, *, epoch):
    rep = _lock(database, prepared)
    owner = database.execute_native('''SELECT o.epoch,o.mode,e.source_digest FROM study_position_owners o
        JOIN study_position_epochs e ON e.repertoire_id=o.repertoire_id AND e.epoch=o.epoch
        WHERE o.repertoire_id=%s FOR UPDATE OF o NOWAIT''', (rep,)).fetchone()
    if not owner or owner['epoch'] != epoch or owner['mode'] != 'paused':
        raise OperatorHold('position_owner_not_paused')
    decisions = _compatible_states(database, prepared, epoch=epoch, expected_digest=owner['source_digest'])
    return {'repertoire_id': rep, 'epoch': epoch, 'source_digest': prepared.authored_token.authored_digest,
            'decisions': decisions}



def _verify_current_epoch(database, prepared, request, *, action, epoch, repertoire_id):
    seeded = (action != 'resume' or database.execute_native(
        'SELECT 1 FROM study_position_seed_runs WHERE repertoire_id=%s AND owner_epoch=%s',
        (repertoire_id, epoch)).fetchone())
    if seeded:
        result = preflight_in_writer(database, prepared, request, epoch=epoch)
    else:
        _fence_retained_frontier(database, repertoire_id)
        result = _retained_preflight(database, prepared, epoch=epoch)
    fence_old_attempt_frontier(database, repertoire_id)
    return result


def transition_in_writer(database, prepared, request, *, action, epoch, operation_id, now, release=None, new_epoch=None, repertoire_id=None):
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
    if action == 'rollforward' and (not isinstance(new_epoch, str) or not new_epoch or new_epoch == epoch):
        raise OperatorHold('position_rollforward_new_epoch_required')
    if action == 'pause' and prepared is None:
        rep = _lock_repertoire(database, repertoire_id)
        stored = database.execute_native('SELECT source_digest FROM study_position_epochs WHERE repertoire_id=%s AND epoch=%s', (rep, epoch)).fetchone()
        if not stored:
            raise OperatorHold('position_owner_epoch_or_source_changed')
        source_digest = stored['source_digest']
    else:
        rep = _lock(database, prepared, source_required=action != 'pause')
        source_digest = prepared.authored_token.authored_digest
    identity = {'action': action, 'repertoire_id': rep, 'epoch': epoch,
                'source_digest': source_digest,
                'release_revision': release.get('revision') if isinstance(release, dict) else None,
                'release_target': release.get('target') if isinstance(release, dict) else None,
                'new_epoch': new_epoch if action == 'rollforward' else None}
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
                    or (action not in ('pause', 'rollforward') and owner['source_digest'] != identity['source_digest'])):
                raise OperatorHold('position_owner_epoch_or_source_changed')
            if action == 'rollforward':
                if owner['mode'] != 'paused':
                    raise OperatorHold('position_owner_not_paused')
                if not isinstance(release, dict) or set(release) != {'registration', 'deployment', 'guard', 'qualification', 'revision', 'target', 'operation'}:
                    raise OperatorHold('position_release_evidence_unavailable')
                evidence = validate_release_evidence(database, prepared, epoch=new_epoch, **release)
                fence_namespace(database)
                fence_old_attempt_frontier(database, rep)
                _fence_retained_frontier(database, rep)
                prior = _prior_owner_receipt(database, rep, epoch, owner['source_digest'], mode='active')
                if not prior or json.loads(prior['response_json']).get('mode') != 'active':
                    raise OperatorHold('position_prior_activation_receipt_missing')
                decisions = _compatible_states(database, prepared, epoch=epoch, expected_digest=owner['source_digest'])
                if any(item['disposition'] != 'preserve_schedule' for item in decisions):
                    raise OperatorHold('position_rollforward_definition_changed')
                if database.execute_native('SELECT 1 FROM study_position_epochs WHERE repertoire_id=%s AND epoch=%s', (rep, new_epoch)).fetchone():
                    raise OperatorHold('position_new_epoch_already_exists')
                database.execute_native('INSERT INTO study_position_epochs(repertoire_id,epoch,source_digest,created_at) VALUES (%s,%s,%s,%s)',
                                        (rep, new_epoch, identity['source_digest'], now))
                database.execute_native('UPDATE study_position_states SET owner_epoch=%s,source_digest=%s WHERE repertoire_id=%s AND owner_epoch=%s',
                                        (new_epoch, identity['source_digest'], rep, epoch))
                mode = 'paused'
            elif action == 'pause':
                if owner['mode'] != 'active':
                    raise OperatorHold('position_owner_not_active')
                mode = 'paused'
            else:
                allowed = {'stage': ('inactive', 'shadow'), 'activate': ('staged',), 'resume': ('paused',)}
                if owner['mode'] not in allowed[action]:
                    raise OperatorHold('position_owner_invalid_transition')
                if not isinstance(release, dict) or set(release) != {'registration', 'deployment', 'guard', 'qualification', 'revision', 'target', 'operation'}:
                    raise OperatorHold('position_release_evidence_unavailable')
                evidence = validate_release_evidence(database, prepared, epoch=epoch, **release)
                _verify_current_epoch(database, prepared, request, action=action, epoch=epoch, repertoire_id=rep)
                mode = 'staged' if action == 'stage' else 'active'
            result = identity | {'mode': mode, 'operation_id': operation_id,
                                 'owner_source_digest': owner['source_digest']}
            if action == 'rollforward':
                result.update({'prior_epoch': epoch, 'epoch': new_epoch, 'state_count': len(decisions),
                               'prior_operation_id': prior['operation_id'],
                               'source_token': prepared.authored_token.as_dict()})
            if action != 'pause':
                result['release'] = evidence
            database.execute_native("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,response_json) VALUES (%s,%s,%s,'complete',%s)",
                                    (operation_id, 'position_owner.transition', digest, json.dumps(result, sort_keys=True)))
            database.execute_native('UPDATE study_position_owners SET epoch=%s,mode=%s,updated_at=%s WHERE repertoire_id=%s',
                                    (new_epoch if action == 'rollforward' else epoch, mode, now, rep))
    except Exception:
        database.execute_native('ROLLBACK TO SAVEPOINT position_owner_operator')
        database.execute_native('RELEASE SAVEPOINT position_owner_operator')
        raise
    database.execute_native('RELEASE SAVEPOINT position_owner_operator')
    return result


def adopt_queue_in_writer(database, prepared, request, *, epoch, operation_id, now, release, page_size=100, plan=False):
    """Adopt only untouched current-day projections with a current unique owner."""
    from app.repertoire.interfaces.position_schedule_occurrences import inventory_from_snapshot
    from tempo_evidence_math.position_learning import Hold, SourceCertificate
    from tempo_legacy_database.adapters.authored_repertoires.position_study_source import prepare_position_source
    from tempo_learning_review import review_day_at

    identity = _seed_request_identity(prepared, epoch=epoch, page_size=page_size, release=release)
    identity['action'] = 'adopt-queue'
    if not isinstance(operation_id, str) or not operation_id or not isinstance(now, datetime) or now.utcoffset() is None:
        raise OperatorHold('position_operator_identity_required')
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    rep = _lock(database, prepared)
    database.execute_native('SAVEPOINT position_queue_adoption')
    try:
        prior = database.execute_native('SELECT command_name,request_hash,state,response_json FROM operation_receipts WHERE operation_id=%s FOR UPDATE', (operation_id,)).fetchone()
        if prior:
            if prior['command_name'] != 'position_owner.adopt_queue' or prior['request_hash'] != digest or prior['state'] != 'complete':
                raise OperatorHold('position_operation_conflict')
            result = json.loads(prior['response_json'])
        else:
            owner = database.execute_native("""SELECT o.epoch,o.mode,e.source_digest FROM study_position_owners o
                JOIN study_position_epochs e ON e.repertoire_id=o.repertoire_id AND e.epoch=o.epoch
                WHERE o.repertoire_id=%s FOR UPDATE OF o NOWAIT""", (rep,)).fetchone()
            if not owner or owner['epoch'] != epoch or owner['mode'] != 'active' or owner['source_digest'] != prepared.authored_token.authored_digest:
                raise OperatorHold('position_owner_epoch_or_source_changed')
            evidence = validate_release_evidence(database, prepared, epoch=epoch, **release)
            timezone = database.execute_native('SELECT timezone FROM settings WHERE id=1').fetchone()
            if not timezone:
                raise OperatorHold('current_study_policy_unavailable')
            day = review_day_at(now, timezone[0]).isoformat()
            inventory = request.sources[0].inventory
            bindings = {(b.card_id, b.card_revision) for pos in inventory.positions for b in pos.physical_bindings if b.available}
            card_ids = sorted({card for card, revision in bindings})
            candidates = database.execute_native("""SELECT id,card_id FROM daily_queue
                WHERE queue_date=%s AND status='queued' AND admission_repertoire_id IS NULL
                  AND card_id=ANY(%s) ORDER BY position,id LIMIT %s""", (day, card_ids, page_size + 1)).fetchall()
            if len(candidates) > page_size:
                raise OperatorHold('position_queue_adoption_exceeds_bound')
            for card in sorted({row['card_id'] for row in candidates}):
                if not database.execute_native('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))', ('tempo:card-edit:' + card,)).fetchone()[0]:
                    raise OperatorHold('position_queue_card_busy')
                if not database.execute_native('SELECT id FROM cards WHERE id=%s FOR UPDATE SKIP LOCKED', (card,)).fetchone():
                    raise OperatorHold('position_queue_card_busy')
            if not database.execute_native('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))', ('tempo:daily-queue-position:' + day,)).fetchone()[0]:
                raise OperatorHold('position_queue_day_busy')
            # Rebuild the source projection after locking cards: the authored fence
            # alone deliberately does not certify physical bindings.
            current = prepare_position_source(database, rep)
            if isinstance(current, Hold) or current.authored_token.as_dict() != prepared.authored_token.as_dict():
                raise OperatorHold('position_queue_source_changed')
            membership = current.snapshot.membership
            cert = SourceCertificate(membership.snapshot_id, membership.source_revision, membership.digest, True, True)
            current_inventory = inventory_from_snapshot(current.snapshot, source_certificate=cert)
            if isinstance(current_inventory, Hold):
                raise OperatorHold('position_queue_source_changed')
            current_bindings = {(b.card_id, b.card_revision) for pos in current_inventory.positions for b in pos.physical_bindings if b.available}
            if candidates:
                # The existing statement triggers invalidate pending frozen-order
                # plans and may wait on the prefix reservation. Do neither here.
                database.execute_native('LOCK TABLE frozen_opening_pending IN SHARE MODE NOWAIT')
                if database.execute_native('SELECT 1 FROM frozen_opening_pending LIMIT 1').fetchone():
                    raise OperatorHold('position_queue_frozen_order_pending')
                if not database.execute_native('SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))', ('tempo:prefix-transition:reservations',)).fetchone()[0]:
                    raise OperatorHold('position_queue_reservation_busy')
            rows = []
            for candidate in candidates:
                row = database.execute_native("""SELECT q.id,q.queue_date,q.card_id,q.cycle,q.position,q.admission_kind,
                    q.admission_repertoire_id,q.status,q.attempt_state,q.attempt_failed,q.review_result_json,
                    c.revision,c.start_fen,c.moves_json,c.trained_color,c.content_type
                    FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.id=%s FOR UPDATE OF q NOWAIT""", (candidate['id'],)).fetchone()
                if not row or row['card_id'] != candidate['card_id'] or row['queue_date'] != day or row['status'] != 'queued' or row['admission_repertoire_id'] is not None or row['attempt_state'] not in ('clean', 'reinforcement') or row['attempt_failed'] != 0 or row['review_result_json'] is not None or (row['card_id'], row['revision']) not in bindings or (row['card_id'], row['revision']) not in current_bindings:
                    raise OperatorHold('position_queue_projection_changed')
                owners = database.execute_native("""SELECT repertoire_id FROM cards WHERE id=%s AND repertoire_id IS NOT NULL
                    UNION SELECT repertoire_id FROM repertoire_cards WHERE card_id=%s""", (row['card_id'], row['card_id'])).fetchall()
                if {x['repertoire_id'] for x in owners} != {rep}:
                    raise OperatorHold('position_queue_owner_ambiguous')
                origins = database.execute_native('SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s FOR UPDATE NOWAIT', (row['id'],)).fetchall()
                if len(origins) != 1:
                    raise OperatorHold('position_queue_origin_ambiguous')
                o = origins[0]
                if (o['card_id'],o['revision'],o['queue_date'],o['cycle'],o['admission_kind'],o['admission_repertoire_id'],o['last_status'],o['attempt_failed'],o['review_result_json'],o['start_fen'],o['moves_json'],o['trained_color'],o['content_type'],o['issued_as_head'],o['issuance_sequence'],o['issued_at']) != (row['card_id'],row['revision'],day,row['cycle'],row['admission_kind'],None,'queued',0,None,row['start_fen'],row['moves_json'],row['trained_color'],row['content_type'],0,None,None):
                    raise OperatorHold('position_queue_origin_changed')
                blocked = database.execute_native("""SELECT 1 WHERE
                    EXISTS(SELECT 1 FROM queue_attempt_abandonments WHERE queue_entry_id=%s)
                    OR EXISTS(SELECT 1 FROM review_attempt_receipts WHERE queue_entry_id=%s)
                    OR EXISTS(SELECT 1 FROM opening_evidence_attempts WHERE queue_entry_id=%s)
                    OR EXISTS(SELECT 1 FROM study_attempts WHERE queue_entry_id=%s)
                    OR EXISTS(SELECT 1 FROM study_position_issued_plans WHERE queue_entry_id=%s)
                    OR EXISTS(SELECT 1 FROM queue_attempt_origins other WHERE other.card_id=%s AND other.queue_entry_id<>%s
                       AND other.issued_as_head=1 AND (other.last_status<>'complete' OR other.review_result_json IS NULL)
                       AND NOT EXISTS(SELECT 1 FROM queue_attempt_abandonments a WHERE a.queue_entry_id=other.queue_entry_id AND a.card_id=other.card_id AND a.revision=other.revision))""", (row['id'],)*5 + (row['card_id'],row['id'])).fetchone()
                if blocked:
                    raise OperatorHold('position_queue_attempt_exists')
                contexts = [dict(x) for x in database.execute_native('SELECT * FROM opening_evidence_queue_contexts WHERE queue_entry_id=%s ORDER BY presentation_snapshot_id,repertoire_id', (row['id'],)).fetchall()]
                origin_before = dict(o)
                before = {key: row[key] for key in ('id','queue_date','card_id','cycle','position','revision','admission_kind','admission_repertoire_id')}
                if not plan:
                    updated = database.execute_native("UPDATE daily_queue SET admission_repertoire_id=%s WHERE id=%s AND admission_repertoire_id IS NULL AND status='queued' RETURNING id", (rep,row['id'])).fetchone()
                    after = database.execute_native('SELECT * FROM queue_attempt_origins WHERE queue_entry_id=%s AND card_id=%s AND revision=%s', (row['id'],row['card_id'],row['revision'])).fetchone()
                    contexts_after = [dict(x) for x in database.execute_native('SELECT * FROM opening_evidence_queue_contexts WHERE queue_entry_id=%s ORDER BY presentation_snapshot_id,repertoire_id', (row['id'],)).fetchall()]
                    if not updated or not after or dict(after) != (origin_before | {'admission_repertoire_id': rep}) or contexts_after != contexts:
                        raise OperatorHold('position_queue_origin_trigger_mismatch')
                rows.append({'before': before, 'after': before | {'admission_repertoire_id': rep}})
            result = {'plan': plan, 'action': 'adopt-queue', 'operation_id': operation_id, 'repertoire_id': rep,
                      'epoch': epoch, 'source_token': prepared.authored_token.as_dict(), 'day': day, 'rows': rows, 'release': evidence}
            if not plan:
                database.execute_native("INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,response_json) VALUES (%s,%s,%s,'complete',%s)", (operation_id,'position_owner.adopt_queue',digest,json.dumps(result,sort_keys=True)))
    except Exception:
        database.execute_native('ROLLBACK TO SAVEPOINT position_queue_adoption')
        database.execute_native('RELEASE SAVEPOINT position_queue_adoption')
        raise
    database.execute_native('RELEASE SAVEPOINT position_queue_adoption')
    return result


def _prepare_request(repertoire_id):
    """Read a complete source and bounded donor facts before the writer fence."""
    from app.repertoire.interfaces.position_schedule_occurrences import inventory_from_snapshot
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
        membership = prepared.snapshot.membership
        cert = SourceCertificate(membership.snapshot_id, membership.source_revision, membership.digest, True, True)
        inventory = inventory_from_snapshot(prepared.snapshot, source_certificate=cert)
        if isinstance(inventory, Hold):
            raise OperatorHold(inventory.reason)
        ids = sorted({binding.card_id for position in inventory.positions
                      for binding in position.physical_bindings if binding.card_id})
        if len(ids) > 10000:
            raise OperatorHold('position_donor_map_exceeds_bound')
        donors = []
        for offset in range(0, len(ids), 500):
            rows = read_seed_donor_page(database, candidate_card_ids=tuple(ids[offset:offset + 500]))
            for row in rows:
                facts = dict(row)
                facts['owner_repertoire_ids'] = tuple(facts['owner_repertoire_ids'])
                for field in ('archived', 'pending_validation', 'deleted'):
                    value = facts[field]
                    facts[field] = bool(value) if value in (0, 1) else None
                donors.append(DonorFact(**facts))
        request = InventoryRequest((SourceFacts(prepared.snapshot, inventory),), tuple(donors))
    return prepared, request


def plan_in_writer(database, prepared, request, payload):
    """Apply the same readiness checks without changing ownership."""
    action, rep, epoch = payload['action'], payload['repertoire_id'], payload['epoch']
    if action == 'adopt-queue':
        from datetime import timezone
        return adopt_queue_in_writer(database, prepared, request, epoch=epoch, operation_id=payload['operation_id'], now=datetime.now(timezone.utc), release=payload['release'], page_size=payload.get('page_size', 100), plan=True)
    if action == 'seed':
        return plan_seed_in_writer(database, prepared, request, epoch=epoch,
                                   page_size=payload.get('page_size', 100), release=payload['release'])
    if action == 'pause':
        _lock_repertoire(database, rep)
    else:
        _lock(database, prepared)
    owner = database.execute_native('''SELECT o.epoch,o.mode,e.source_digest FROM study_position_owners o
        JOIN study_position_epochs e ON e.repertoire_id=o.repertoire_id AND e.epoch=o.epoch
        WHERE o.repertoire_id=%s''', (rep,)).fetchone()
    if (not owner or owner['epoch'] != epoch or (action not in ('pause', 'rollforward')
            and owner['source_digest'] != prepared.authored_token.authored_digest)):
        raise OperatorHold('position_owner_epoch_or_source_changed')
    modes = {'stage': ('inactive', 'shadow'), 'activate': ('staged',),
             'resume': ('paused',), 'pause': ('active',), 'rollforward': ('paused',)}
    if action not in modes or owner['mode'] not in modes[action]:
        raise OperatorHold('position_owner_invalid_transition')
    source_digest = owner['source_digest'] if action == 'pause' else prepared.authored_token.authored_digest
    result = {'plan': True, 'action': action, 'repertoire_id': rep, 'epoch': epoch,
              'current_mode': owner['mode'], 'source_digest': source_digest}
    if action not in ('pause', 'rollforward'):
        result['release'] = validate_release_evidence(database, prepared, epoch=epoch, **payload['release'])
        result['preflight'] = _verify_current_epoch(database, prepared, request, action=action, epoch=epoch, repertoire_id=rep)
    elif action == 'rollforward':
        new_epoch = payload.get('new_epoch')
        if not isinstance(new_epoch, str) or not new_epoch or new_epoch == epoch:
            raise OperatorHold('position_rollforward_new_epoch_required')
        result['release'] = validate_release_evidence(database, prepared, epoch=new_epoch, **payload['release'])
        fence_namespace(database)
        fence_old_attempt_frontier(database, rep)
        _fence_retained_frontier(database, rep)
        if not _prior_owner_receipt(database, rep, epoch, owner['source_digest'], mode='active'):
            raise OperatorHold('position_prior_activation_receipt_missing')
        result['rollforward'] = preview_rollforward_in_writer(database, prepared, epoch=epoch)
        if any(item['disposition'] != 'preserve_schedule' for item in result['rollforward']['decisions']):
            raise OperatorHold('position_rollforward_definition_changed')
        if database.execute_native('SELECT 1 FROM study_position_epochs WHERE repertoire_id=%s AND epoch=%s', (rep, new_epoch)).fetchone():
            raise OperatorHold('position_new_epoch_already_exists')
    return result


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
        if action not in ('seed', 'stage', 'activate', 'pause', 'resume', 'rollforward', 'adopt-queue') or not all(
                isinstance(value, str) and value for value in (rep, epoch, payload.get('operation_id'))):
            raise OperatorHold('position_operator_request_invalid')
        prepared, request = (None, None) if action == 'pause' else _prepare_request(rep)
        with connection() as database:
            # Start the caller-owned transaction before borrowing it in owner APIs.
            database.execute_native('SELECT 1')
            if payload['plan']:
                result = plan_in_writer(database, prepared, request, payload)
            elif action == 'adopt-queue':
                result = adopt_queue_in_writer(database, prepared, request, epoch=epoch, operation_id=payload['operation_id'], now=datetime.now(timezone.utc), release=payload['release'], page_size=payload.get('page_size', 100))
            elif action == 'seed':
                result = seed_page_in_writer(database, prepared, request, epoch=epoch, operation_id=payload['operation_id'],
                    page_size=payload.get('page_size', 100), now=datetime.now(timezone.utc), release=payload['release'])
            else:
                result = transition_in_writer(database, prepared, request, action=action, epoch=epoch,
                    operation_id=payload['operation_id'], now=datetime.now(timezone.utc),
                    release=payload['release'] if action != 'pause' else None,
                    new_epoch=payload.get('new_epoch'), repertoire_id=rep)
        print(json.dumps(result, sort_keys=True))
    except (ValueError, KeyError, TypeError) as exc:
        raise SystemExit('position operator held: ' + str(exc)) from exc


if __name__ == '__main__':
    main()
