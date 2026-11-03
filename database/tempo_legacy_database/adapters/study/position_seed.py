"""Restartable shadow seed with source, namespace and donor fences.

The borrowed writer never activates scheduling. Completion is a receipt for
F4 to inspect, not authorization to switch ownership.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json

from tempo_legacy_database.adapters.authored_repertoires.position_study_source import PreparedPositionSource
from .position_seed_donors import DonorDecision, select_donors
from .position_seed_inventory import InventoryRequest


@dataclass(frozen=True)
class SeedPreviewPage:
    decisions: tuple[DonorDecision, ...]
    next_after: tuple[str, str] | None
    source_provenance_json: str
    authored_digest: str


def preview_seed_page(prepared: PreparedPositionSource, request: InventoryRequest, *,
                      after: tuple[str, str] | None = None, limit: int = 100) -> SeedPreviewPage:
    """Preview a bounded page of deterministic decisions for one owner source.

    The cursor is a preview cursor, not a durable completion receipt. All
    inputs must be refreshed and fenced at the eventual writer boundary.
    """
    if not isinstance(prepared, PreparedPositionSource):
        raise ValueError("prepared_position_source_required")
    if not isinstance(request, InventoryRequest) or len(request.sources) != 1:
        raise ValueError("one_inventory_source_required")
    if len(request.donors) > 10000:
        raise ValueError("inventory_donors_exceed_bound")
    if request.sources[0].snapshot != prepared.snapshot:
        raise ValueError("inventory_source_mismatch")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("invalid_page_limit")
    if after is not None and (not isinstance(after, tuple) or len(after) != 2
                              or any(not isinstance(v, str) or not v for v in after)):
        raise ValueError("invalid_page_cursor")
    token = prepared.authored_token
    definitions = {(d.definition.key.repertoire_id, d.definition.key.variant,
                    d.definition.key.position, d.definition.expected_uci)
                   for route in prepared.routes for d in route.decisions}
    decisions = select_donors(request)
    rows = []
    for decision in decisions:
        selected, hold = decision.selected, decision.hold
        if selected is None:
            if hold.repertoire_id != token.repertoire_id:
                raise ValueError("inventory_repertoire_mismatch")
            if hold.variant is None or hold.position is None:
                # A repertoire-wide hold has no valid position cursor and must
                # remain visible rather than becoming an empty successful page.
                return SeedPreviewPage((decision,), None,
                    json.dumps(token.as_dict(), sort_keys=True, separators=(",", ":")), token.authored_digest)
            key = (hold.variant, hold.position)
        else:
            if (selected.repertoire_id, selected.variant, selected.position,
                    selected.selected_response_uci) not in definitions:
                raise ValueError("donor_position_not_in_prepared_source")
            key = (selected.variant, selected.position)
        rows.append((key, decision))
    rows.sort(key=lambda item: item[0])
    if len({key for key, _ in rows}) != len(rows):
        raise ValueError("duplicate_position")
    remaining = [item for item in rows if after is None or item[0] > after]
    page = remaining[:limit]
    cursor = page[-1][0] if len(remaining) > limit else None
    return SeedPreviewPage(tuple(item[1] for item in page), cursor,
        json.dumps(token.as_dict(), sort_keys=True, separators=(",", ":")), token.authored_digest)


# The following preflight is deliberately independent of migration writes. A
# durable run may only advance it while holding the shared allocation fence.
_POSITION_ID_MIN = 1 << 52
_POSITION_ID_MAX = (1 << 53) - 1


def scan_legacy_id_page(database, *, after: str | None = None, limit: int = 500):
    """Read one keyset page of legacy IDs and reject malformed/reserved IDs.

    A coordinator must combine all pages under the owner allocation protocol;
    an individual page is not a certificate that the namespace is safe.
    """
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("invalid_page_limit")
    if after is not None and (not isinstance(after, str) or not after):
        raise ValueError("invalid_page_cursor")
    rows = database.execute_native(
        """SELECT id, CASE WHEN octet_length(fsrs_card_json) <= 65536
                           THEN fsrs_card_json END fsrs_card_json FROM cards
           WHERE fsrs_card_json IS NOT NULL AND (%s::text IS NULL OR id > %s)
           ORDER BY id LIMIT %s""", (after, after, limit + 1)).fetchall()
    page = rows[:limit]
    for row in page:
        try:
            if not isinstance(row['id'], str) or not 0 < len(row['id']) <= 65536:
                raise ValueError
            value = json.loads(row['fsrs_card_json'])
            identifier = value['card_id']
            if type(identifier) is not int or identifier <= 0:
                raise ValueError
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise ValueError("legacy_fsrs_identity_invalid") from exc
        if _POSITION_ID_MIN <= identifier <= _POSITION_ID_MAX:
            raise ValueError("legacy_fsrs_identity_in_reserved_namespace")
    return (page[-1]['id'] if len(rows) > limit else None, len(page))


def fence_donor_facts(database, facts):
    """Lock and re-read up to 500 detached card facts in the borrowed writer.

    The cards' UPDATE locks also block new membership/block foreign-key rows;
    existing membership/block rows are locked explicitly before comparing E1's
    whole projection. Source graph fencing must already have run.
    """
    from .position_seed_inventory import CARD_PAGE_SQL, DonorFact

    facts = tuple(facts)
    if len(facts) > 500 or any(not isinstance(f, DonorFact) or not f.card_id for f in facts):
        raise ValueError('invalid_donor_fence_page')
    ids = sorted({f.card_id for f in facts})
    if len(ids) != len(facts):
        raise ValueError('ambiguous_donor_facts')
    if not ids:
        return
    for card_id in ids:
        result = database.execute_native(
            'SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',
            ('tempo:card-edit:' + card_id,)).fetchone()
        if not result or not result[0]:
            raise ValueError('donor_card_busy')
    rows = database.execute_native(
        'SELECT id FROM cards WHERE id=ANY(%s::text[]) ORDER BY id FOR UPDATE SKIP LOCKED',
        (ids,)).fetchall()
    if [row['id'] for row in rows] != ids:
        raise ValueError('donor_card_missing_or_busy')
    # Card and repertoire deletion acquire the same card-edit advisory key
    # before recording deleted_cards; that key fences tombstone insertion.
    # Parent card locks separately fence new FK membership/block rows.
    for table in ('repertoire_cards', 'repertoire_integrity_card_blocks'):
        database.execute_native(
            f'SELECT card_id FROM {table} WHERE card_id=ANY(%s::text[]) FOR UPDATE NOWAIT',
            (ids,)).fetchall()
    current = database.execute_native(CARD_PAGE_SQL, (ids,)).fetchall()
    by_id = {row['card_id']: row for row in current}
    for fact in facts:
        row = by_id.get(fact.card_id)
        if row is None:
            raise ValueError('donor_card_missing_or_busy')
        for field in ('repertoire_id', 'revision', 'state', 'due_date', 'fsrs_card_json',
                      'introduced_at', 'superseded_by', 'content_type', 'card_exists'):
            if row[field] != getattr(fact, field):
                raise ValueError('donor_facts_changed')
        for field in ('archived', 'pending_validation', 'integrity_blocked', 'deleted'):
            expected = getattr(fact, field)
            if expected is None or bool(row[field]) != expected:
                raise ValueError('donor_facts_changed')
        if tuple(row['owner_repertoire_ids']) != tuple(sorted(set(fact.owner_repertoire_ids))):
            raise ValueError('donor_ownership_changed')


def _manifest(prepared):
    import hashlib
    definitions = {}
    for route in prepared.routes:
        for item in route.decisions:
            d = item.definition
            key = (d.key.variant, d.key.position)
            if d.key.repertoire_id != prepared.authored_token.repertoire_id:
                raise ValueError('source_repertoire_mismatch')
            if key in definitions and definitions[key] != d:
                raise ValueError('source_definition_conflict')
            definitions[key] = d
    payload = [[key[0], key[1], definitions[key].expected_uci, definitions[key].policy_version]
               for key in sorted(definitions)]
    raw = json.dumps(payload, separators=(',', ':'), ensure_ascii=False)
    return definitions, hashlib.sha256(raw.encode()).hexdigest()


def seed_shadow_page(database, prepared, request, *, epoch, now, page_size=100):
    """Advance one preflight or seed page in a borrowed owner transaction.

    The caller commits or rolls back. An exception rolls back this call's
    savepoint; it never activates an epoch. A source and namespace guard are
    checked on every call. Until the owner namespace guard is installed this
    operation fails closed.
    """
    import hashlib
    from datetime import datetime
    from tempo_scheduling import clone_state
    from tempo_legacy_database.adapters.authored_repertoires.position_study_source import fence_position_source
    from tempo_legacy_database.adapters.study.position_owner_epoch import owner_lock_key
    from .position_seed_inventory import build_inventory
    if not isinstance(prepared, PreparedPositionSource):
        raise ValueError('prepared_position_source_required')
    if (not isinstance(epoch, str) or not epoch or not isinstance(now, datetime)
            or now.utcoffset() is None or type(page_size) is not int or not 1 <= page_size <= 100):
        raise ValueError('invalid_seed_request')
    try:
        if database.read_only or database.raw.info.transaction_status.name != 'INTRANS':
            raise ValueError('borrowed_writer_transaction_required')
    except AttributeError as exc:
        raise ValueError('borrowed_writer_transaction_required') from exc
    # This owner module and SQL verification are supplied with F1's installed
    # namespace guard. Never accept a boolean or a caller-issued certificate.
    try:
        from tempo_legacy_database.adapters.study.position_fsrs_namespace import fence_namespace
    except ImportError as exc:
        raise ValueError('position_namespace_guard_unavailable') from exc
    database.execute_native('SAVEPOINT position_seed_page')
    try:
        held = fence_position_source(database, prepared)
        if held is not None:
            raise ValueError(held.reason)
        token = prepared.authored_token
        lock = database.execute_native('SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0))',
                                       (owner_lock_key(token.repertoire_id),)).fetchone()
        if not lock or not lock[0]:
            raise ValueError('position_owner_busy')
        fence_namespace(database)
        result = _seed_shadow_page_locked(database, prepared, request, epoch, now, page_size,
                                          clone_state, hashlib, build_inventory)
    except Exception:
        database.execute_native('ROLLBACK TO SAVEPOINT position_seed_page')
        database.execute_native('RELEASE SAVEPOINT position_seed_page')
        raise
    database.execute_native('RELEASE SAVEPOINT position_seed_page')
    return result


def _seed_shadow_page_locked(db, prepared, request, epoch, now, page_size, clone_state, hashlib, build_inventory):
    token = prepared.authored_token
    rep = token.repertoire_id
    definitions, digest = _manifest(prepared)
    # Verify E1/E2 are based on exactly the prepared snapshot before writes.
    preview_seed_page(prepared, request, limit=1)
    decisions = select_donors(request)
    by_key = {}
    for decision in decisions:
        item = decision.selected or decision.hold
        if item.variant is None or item.position is None:
            raise ValueError('source_inventory_held')
        key = (item.variant, item.position)
        if key in by_key:
            raise ValueError('duplicate_position')
        by_key[key] = decision
    if set(by_key) != set(definitions):
        raise ValueError('source_manifest_mismatch')
    policy = 'earliest_due_then_card_id:v1'
    token_json = json.dumps(token.as_dict(), sort_keys=True, separators=(',', ':'))
    db.execute_native('INSERT INTO study_position_epochs VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',
                      (rep, epoch, token.authored_digest, now))
    ep = db.execute_native('SELECT source_digest FROM study_position_epochs WHERE repertoire_id=%s AND epoch=%s',
                           (rep, epoch)).fetchone()
    if not ep or ep['source_digest'] != token.authored_digest:
        raise ValueError('position_epoch_source_conflict')
    db.execute_native("INSERT INTO study_position_owners VALUES (%s,%s,'inactive',%s) ON CONFLICT DO NOTHING",
                      (rep, epoch, now))
    owner = db.execute_native('SELECT epoch,mode FROM study_position_owners WHERE repertoire_id=%s FOR UPDATE NOWAIT',
                              (rep,)).fetchone()
    if not owner or owner['epoch'] != epoch or owner['mode'] not in ('inactive', 'shadow'):
        raise ValueError('position_owner_not_shadow')
    db.execute_native('''INSERT INTO study_position_seed_runs
        (repertoire_id,owner_epoch,source_digest,source_token_json,selection_policy,manifest_digest,
         manifest_count,allocator_preflight_json,created_at)
        VALUES (%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING''',
        (rep, epoch, token.authored_digest, token_json, policy, digest, len(definitions),
         json.dumps({'after': None, 'scanned': 0, 'complete': False}), now))
    run = db.execute_native('SELECT * FROM study_position_seed_runs WHERE repertoire_id=%s AND owner_epoch=%s FOR UPDATE NOWAIT',
                            (rep, epoch)).fetchone()
    if (run['source_digest'] != token.authored_digest or run['source_token_json'] != json.loads(token_json)
            or run['selection_policy'] != policy or run['manifest_digest'] != digest
            or run['manifest_count'] != len(definitions)):
        raise ValueError('position_seed_run_conflict')
    preflight = run['allocator_preflight_json']
    if not preflight['complete']:
        cursor, count = scan_legacy_id_page(db, after=preflight['after'], limit=page_size)
        preflight = {'after': cursor, 'scanned': preflight['scanned'] + count, 'complete': cursor is None}
        db.execute_native('UPDATE study_position_seed_runs SET allocator_preflight_json=%s::jsonb WHERE repertoire_id=%s AND owner_epoch=%s',
                          (json.dumps(preflight), rep, epoch))
        return {'stage': 'preflight', 'complete': preflight['complete'], 'scanned': preflight['scanned']}
    recovering = run['status'] == 'complete'
    prior_holds = {}
    if recovering:
        eligible = sorted(key for key, decision in by_key.items() if decision.selected is not None)
        rows = db.execute_native('''SELECT * FROM study_position_seed_outcomes
            WHERE repertoire_id=%s AND owner_epoch=%s AND disposition='hold'
            AND (variant,position) IN (SELECT * FROM unnest(%s::text[],%s::text[]))
            ORDER BY variant,position LIMIT %s FOR UPDATE NOWAIT''',
            (rep, epoch, [key[0] for key in eligible], [key[1] for key in eligible], page_size + 1)).fetchall()
        more_recovery = len(rows) > page_size
        prior_holds = {(row['variant'], row['position']): dict(row) for row in rows[:page_size]}
        if not prior_holds:
            return {'stage': 'verification_required', 'seeded': run['seeded_count'], 'held': run['held_count']}
    checkpoint = ((run['checkpoint_variant'], run['checkpoint_position'])
                  if run['checkpoint_variant'] is not None else None)
    keys = (sorted(prior_holds) if recovering else
            [k for k in sorted(definitions) if checkpoint is None or k > checkpoint][:page_size])
    inventory = build_inventory(request)
    positions = {(p['variant'], p['position']): p for r in inventory['repertoires']
                 if r['repertoire_id'] == rep for p in r['positions']}
    candidates = {key: {candidate['card_id'] for candidate in positions[key]['donor_candidates']}
                  for key in keys}
    facts = {f.card_id: f for f in request.donors}
    unavailable = {key for key, ids in candidates.items()
                   if any(i not in facts or not facts[i].card_exists for i in ids)}
    ids = {i for key in keys if key not in unavailable for i in candidates[key]}
    fence_donor_facts(db, [facts[i] for i in sorted(ids)])
    seeded, held_count = run['seeded_count'], run['held_count']
    provenance = []
    for key in keys:
        definition, decision = definitions[key], by_key[key]
        prior = prior_holds.get(key)
        if prior and (prior['expected_uci'] != definition.expected_uci
                      or prior['policy_version'] != definition.policy_version):
            raise ValueError('position_receipt_conflict')
        state = db.execute_native('SELECT 1 FROM study_position_states WHERE repertoire_id=%s AND variant=%s AND position=%s FOR UPDATE NOWAIT',
                                  (rep, *key)).fetchone()
        if state:
            raise ValueError('position_state_already_exists')
        if decision.hold or key in unavailable:
            if recovering:
                raise ValueError('donor_currentness_unavailable')
            hold_reason = 'donor_currentness_unavailable' if key in unavailable else decision.hold.reason
            db.execute_native('''INSERT INTO study_position_seed_outcomes
                (repertoire_id,owner_epoch,variant,position,expected_uci,policy_version,disposition,hold_reason)
                VALUES (%s,%s,%s,%s,%s,%s,'hold',%s)''',
                (rep, epoch, *key, definition.expected_uci, definition.policy_version, hold_reason))
            held_count += 1
            continue
        selected = decision.selected
        if selected.selected_response_uci != definition.expected_uci:
            raise ValueError('source_definition_mismatch')
        allocated = db.execute_native("SELECT nextval('study_position_fsrs_id_seq')").fetchone()[0]
        if allocated == selected.donor_internal_card_id:
            raise ValueError('donor_identity_collision')
        clone = clone_state(selected.fsrs_card_json, allocated_new_card_id=allocated)
        fingerprint = hashlib.sha256(clone.encode()).hexdigest()
        from .position_seed_inventory import _legacy_introduction_day
        raw_introduced = facts[selected.donor_card_id].introduced_at
        if _legacy_introduction_day(raw_introduced) and date.fromisoformat(raw_introduced) > now.date():
            raise ValueError('introduction_day_after_adoption')
        introduced = now if _legacy_introduction_day(raw_introduced) else raw_introduced
        if prior or _legacy_introduction_day(raw_introduced):
            provenance.append({'variant': key[0], 'position': key[1],
                'prior_outcome': prior, 'raw_introduced_at': raw_introduced,
                'introduced_at': introduced.isoformat() if isinstance(introduced, datetime) else introduced,
                'introduction_basis': 'adoption_lower_bound' if _legacy_introduction_day(raw_introduced) else 'legacy_instant',
                'donor_card_id': selected.donor_card_id, 'donor_card_revision': selected.donor_revision,
                'donor_fingerprint': selected.fsrs_fingerprint})
        db.execute_native('''INSERT INTO study_position_states
            (repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,source_digest,
             fsrs_internal_card_id,fsrs_json,due_at,introduced_at,donor_card_id,donor_card_revision,
             donor_fingerprint,donor_selection_policy)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
            (rep, *key, definition.expected_uci, definition.policy_version, epoch, token.authored_digest,
             allocated, clone, selected.due_at, introduced, selected.donor_card_id, selected.donor_revision,
             selected.fsrs_fingerprint, policy))
        if prior:
            updated = db.execute_native('''UPDATE study_position_seed_outcomes SET
                disposition='seeded',hold_reason=NULL,donor_card_id=%s,donor_card_revision=%s,
                donor_fingerprint=%s,donor_selection_policy=%s,donor_fsrs_internal_card_id=%s,
                allocated_fsrs_internal_card_id=%s,clone_fingerprint=%s,introduced_at=%s
                WHERE repertoire_id=%s AND owner_epoch=%s AND variant=%s AND position=%s
                AND disposition='hold' AND hold_reason=%s AND expected_uci=%s AND policy_version=%s''',
                (selected.donor_card_id, selected.donor_revision, selected.fsrs_fingerprint, policy,
                 selected.donor_internal_card_id, allocated, fingerprint, introduced,
                 rep, epoch, *key, prior['hold_reason'], definition.expected_uci, definition.policy_version))
            if updated.rowcount != 1:
                raise ValueError('position_receipt_conflict')
        else:
            db.execute_native('''INSERT INTO study_position_seed_outcomes
            (repertoire_id,owner_epoch,variant,position,expected_uci,policy_version,disposition,donor_card_id,
             donor_card_revision,donor_fingerprint,donor_selection_policy,donor_fsrs_internal_card_id,
             allocated_fsrs_internal_card_id,clone_fingerprint,introduced_at)
            VALUES (%s,%s,%s,%s,%s,%s,'seeded',%s,%s,%s,%s,%s,%s,%s,%s)''',
            (rep, epoch, *key, definition.expected_uci, definition.policy_version, selected.donor_card_id,
             selected.donor_revision, selected.fsrs_fingerprint, policy, selected.donor_internal_card_id,
             allocated, fingerprint, introduced))
        seeded += 1
        if recovering:
            held_count -= 1
    complete = seeded + held_count == len(definitions)
    last = checkpoint if recovering else keys[-1] if keys else checkpoint
    db.execute_native('''UPDATE study_position_seed_runs SET checkpoint_variant=%s,checkpoint_position=%s,
        seeded_count=%s,held_count=%s,status=%s,completed_at=%s WHERE repertoire_id=%s AND owner_epoch=%s''',
        (last[0] if last else None, last[1] if last else None, seeded, held_count,
         'complete' if complete else 'incomplete', run['completed_at'] if recovering else now if complete else None, rep, epoch))
    return {'stage': ('recovering' if more_recovery else 'complete') if recovering else
            'complete' if complete else 'seeding', 'seeded': seeded, 'held': held_count,
            'provenance': provenance}


def verify_seed_receipts_page(database, prepared, request, *, epoch, after=None, limit=100):
    """Recheck original donor fingerprints on a bounded receipt page.

    Call under the same source, namespace, and owner fences as a seed page.
    A receipt's immutable provenance is compared with current donor bytes;
    current position FSRS bytes are deliberately not compared or changed, as
    accepted grades may have advanced them. This does not by itself authorize
    activation or persist a completion proof.
    """
    import hashlib
    from tempo_legacy_database.adapters.authored_repertoires.position_study_source import fence_position_source
    from tempo_legacy_database.adapters.study.position_owner_epoch import owner_lock_key
    from tempo_legacy_database.adapters.study.position_fsrs_namespace import fence_namespace
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('invalid_page_limit')
    if after is not None and (not isinstance(after, tuple) or len(after) != 2
                              or any(not isinstance(x, str) or not x for x in after)):
        raise ValueError('invalid_page_cursor')
    held = fence_position_source(database, prepared)
    if held is not None:
        raise ValueError(held.reason)
    rep = prepared.authored_token.repertoire_id
    row = database.execute_native('SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))',
                                  (owner_lock_key(rep),)).fetchone()
    if not row or not row[0]:
        raise ValueError('position_owner_busy')
    fence_namespace(database)
    token = prepared.authored_token
    run = database.execute_native('''SELECT source_digest,source_token_json,manifest_digest,manifest_count,status
        FROM study_position_seed_runs WHERE repertoire_id=%s AND owner_epoch=%s''', (rep, epoch)).fetchone()
    definitions, digest = _manifest(prepared)
    if (not run or run['source_digest'] != token.authored_digest or run['source_token_json'] != token.as_dict()
            or run['manifest_digest'] != digest or run['manifest_count'] != len(definitions)):
        raise ValueError('position_seed_run_conflict')
    preview_seed_page(prepared, request, limit=1)
    rows = database.execute_native('''SELECT * FROM study_position_seed_outcomes
        WHERE repertoire_id=%s AND owner_epoch=%s AND
        (%s::text IS NULL OR (variant,position) > (%s,%s)) ORDER BY variant,position LIMIT %s''',
        (rep, epoch, after[0] if after else None, *(after or (None, None)), limit + 1)).fetchall()
    page = rows[:limit]
    facts = {fact.card_id: fact for fact in request.donors}
    ids = {row['donor_card_id'] for row in page if row['disposition'] == 'seeded'}
    if not ids <= facts.keys():
        raise ValueError('donor_fact_missing')
    fence_donor_facts(database, [facts[i] for i in sorted(ids)])
    for receipt in page:
        key = (receipt['variant'], receipt['position'])
        definition = definitions.get(key)
        if (definition is None or receipt['expected_uci'] != definition.expected_uci
                or receipt['policy_version'] != definition.policy_version):
            raise ValueError('position_receipt_conflict')
        state = database.execute_native('''SELECT owner_epoch,source_digest,donor_card_id,
            donor_card_revision,donor_fingerprint,fsrs_internal_card_id,version,fsrs_json,introduced_at FROM study_position_states
            WHERE repertoire_id=%s AND variant=%s AND position=%s''', (rep, *key)).fetchone()
        if receipt['disposition'] == 'hold':
            if state is not None:
                raise ValueError('held_position_has_state')
            continue
        fact = facts[receipt['donor_card_id']]
        if (fact.revision != receipt['donor_card_revision'] or not fact.fsrs_card_json
                or hashlib.sha256(fact.fsrs_card_json.encode()).hexdigest() != receipt['donor_fingerprint']):
            raise ValueError('donor_fingerprint_changed')
        if (not state or state['owner_epoch'] != epoch or state['source_digest'] != token.authored_digest
                or state['donor_card_id'] != receipt['donor_card_id']
                or state['donor_card_revision'] != receipt['donor_card_revision']
                or state['donor_fingerprint'] != receipt['donor_fingerprint']
                or state['fsrs_internal_card_id'] != receipt['allocated_fsrs_internal_card_id']):
            raise ValueError('position_receipt_conflict')
        from .position_seed_inventory import _legacy_introduction_day
        if state['introduced_at'] != receipt['introduced_at']:
            raise ValueError('position_introduction_receipt_conflict')
        if _legacy_introduction_day(fact.introduced_at):
            records = database.execute_native('''SELECT evidence.value FROM operation_receipts
                CROSS JOIN LATERAL jsonb_array_elements(
                    COALESCE(response_json::jsonb->'page'->'provenance','[]'::jsonb)) evidence
                WHERE command_name='position_owner.seed' AND state='complete'
                AND response_json::jsonb->>'repertoire_id'=%s AND response_json::jsonb->>'epoch'=%s
                AND evidence.value->>'variant'=%s AND evidence.value->>'position'=%s''',
                (rep, epoch, *key)).fetchall()
            matches = [row[0] for row in records if
                row[0].get('raw_introduced_at') == fact.introduced_at
                and row[0].get('donor_card_id') == fact.card_id
                and row[0].get('donor_card_revision') == fact.revision
                and row[0].get('donor_fingerprint') == receipt['donor_fingerprint']
                and row[0].get('introduction_basis') == 'adoption_lower_bound'
                and datetime.fromisoformat(row[0]['introduced_at']) == receipt['introduced_at']]
            if len(matches) != 1:
                raise ValueError('position_introduction_provenance_missing')
        elif datetime.fromisoformat(fact.introduced_at.replace('Z', '+00:00')) != receipt['introduced_at']:
            raise ValueError('position_introduction_receipt_conflict')
        if state['version'] == 1 and hashlib.sha256(state['fsrs_json'].encode()).hexdigest() != receipt['clone_fingerprint']:
            raise ValueError('initial_clone_fingerprint_changed')
    return {'next_after': (page[-1]['variant'], page[-1]['position']) if len(rows) > limit else None,
            'verified': len(page), 'run_status': run['status']}
