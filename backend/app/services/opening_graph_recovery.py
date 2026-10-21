"""Explicit, single-repertoire maintenance recovery; never enables general workers.

Failed first publication and explicitly pinned queued rebuilds are separate modes.
Ordinary phase handlers retain ownership of graph, integrity and queue effects.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import time
import uuid
from zoneinfo import ZoneInfo

from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.opening_graph import recovery as storage
from tempo_legacy_database.adapters.opening_graph.recovery import read_rows
from ..card_deletion import omit_deleted_graph_steps
from .durable_tasks import defer_task_for_contention, fail_task, warm_completion_sql
from app.chess.opening_segments import stable_key
from .postgres_opening_graph import execute_postgres_opening_graph_slice, prepare_next_graph_line
from .postgres_integrity import execute_postgres_integrity_slice
from .redis_admission_gate import BackgroundAdmissionDeferred, background_lease


QUEUED_REBUILD_MODE = 'queued-rebuild'
_SELECTED_PHASES = {
    'opening_graph_rebuild': {'queued', 'stage', 'link', 'publish', 'classify', 'integrity', 'cleanup', 'finalize'},
    'integrity_scan': {'queued', 'scan', 'aggregate', 'evaluate', 'publish', 'validate_cards', 'complete'},
    'opening_segmentation': {'queued', 'scan', 'groups', 'cleanup', 'publish'},
}
_PREDECESSOR_FIELDS = ('id', 'generation', 'state', 'phase', 'lease_token', 'lease_expires_at')


def local_study_day():
    return datetime.now(ZoneInfo('America/New_York')).date().isoformat()


def _selected_scope_key(prepared):
    return stable_key(prepared['target'], prepared['task_id'], prepared['generation'],
                      prepared['scope_token'], prepared['day'], prepared['plan_version'],
                      {kind: pin['prior'] for kind, pin in prepared['tasks'].items() if kind != 'opening_graph_rebuild'})


def _validate_followup(database, prepared, kind, *, prior, lock):
    pin = prepared['tasks'][kind]
    row = storage.followup_task(database, kind, prepared['repertoire_id'], lock=lock)
    if prior:
        if pin['prior'] and (pin['prior']['state'] == 'leased' or pin['prior']['lease_token'] is not None or pin['prior']['lease_expires_at'] is not None):
            raise ValueError('Pinned downstream predecessor must not carry a lease')
        actual = {field: row[field] for field in _PREDECESSOR_FIELDS} if row else None
        if actual != pin['prior']:
            raise ValueError('Pinned downstream predecessor changed; refusing owner enqueue')
    elif (row is None or row['generation'] != pin['generation'] or pin['id'] not in {None, row['id']}
          or json.loads(row['payload_json']).get('repertoire_id') != prepared['repertoire_id']
          or json.loads(row['payload_json']).get('graph_generation') != prepared['generation']):
        raise ValueError('Owner did not retain the pinned matching downstream task')
    return row


def validate_selected_scope(database, prepared, *, lock=True):
    """Fence every selected claim AND owner write; hold source/policy rows to commit."""
    if prepared.get('plan_version') != 2:
        raise ValueError('Maintenance plan requires pinned downstream predecessors; use fresh preflight before any claims')
    if prepared.get('mode') != QUEUED_REBUILD_MODE or local_study_day() != prepared['day']:
        raise ValueError('Maintenance mode or local study day changed')
    identity = dict(storage.database_identity(database))
    if stable_key(*(identity[field] for field in ('cluster_id', 'database_oid', 'database', 'schema'))) != prepared['target']:
        raise ValueError('Database target identity mismatch')
    token = storage.scope_row(database, prepared['repertoire_id'], lock=lock)
    if token is None or not token['study_only_mode'] or dict(token) != prepared['scope_token']:
        raise ValueError('Source, study mode, depth, or frozen activation changed')
    graph = storage.graph_scope_task(database, prepared['repertoire_id'], lock=lock)
    if graph is None or (graph['id'], graph['generation']) != (prepared['task_id'], prepared['generation']):
        raise ValueError('Selected graph identity or generation changed')
    publication = storage.publication(database, prepared['repertoire_id'])
    published = graph['phase'] in {'classify', 'integrity', 'cleanup', 'finalize', 'published'}
    expected_publication = prepared['generation'] if published else prepared['previous_graph_generation']
    if publication is None or publication['state'] != 'ready' or publication['generation'] != expected_publication:
        raise ValueError('Selected graph publication changed or is not ready')
    if storage.active_transition(database, prepared['repertoire_id']):
        raise ValueError('An active prefix transition requires its own recovery owner')
    scan = _validate_followup(database, prepared, 'integrity_scan', prior=graph['state'] != 'complete', lock=lock)
    _validate_followup(database, prepared, 'opening_segmentation',
                       prior=graph['state'] != 'complete' or scan['state'] != 'complete', lock=lock)


def preflight_queued_rebuild(task_id, repertoire_id, generation, expected_target, *, source_revision, prefix_revision, day):
    """Read-only plan for one untouched queued rebuild with an older ready publication."""
    if target_id() != expected_target:
        raise ValueError('Database target identity mismatch')
    token = scope_token(repertoire_id)
    if (token['scope_source_revision'], token['canonical_prefix_revision']) != (source_revision, prefix_revision):
        raise ValueError('Pinned source or canonical prefix revision mismatch')
    task = storage.task_rows(task_id)
    if (len(task) != 1 or (task[0]['kind'], task[0]['deduplication_key'], task[0]['generation'],
                         task[0]['state'], task[0]['phase'], task[0]['attempt_count']) !=
            ('opening_graph_rebuild', repertoire_id, generation, 'queued', 'queued', 0)
            or json.loads(task[0]['payload_json']).get('repertoire_id') != repertoire_id):
        raise ValueError('Expected the exact untouched queued graph rebuild')
    publication = storage.publication_rows(repertoire_id)
    if len(publication) != 1 or publication[0]['state'] != 'ready' or publication[0]['generation'] >= generation:
        raise ValueError('Expected an older retained ready graph publication')
    if storage.newer_staging(repertoire_id, generation):
        raise ValueError('Untouched queued task already has target or newer staging')
    tasks = {'opening_graph_rebuild': {'id': task_id, 'generation': generation}}
    for kind in ('integrity_scan', 'opening_segmentation'):
        rows = storage.followup_predecessor(kind, repertoire_id)
        if rows and (rows[0]['state'] == 'leased' or rows[0]['lease_token'] is not None or rows[0]['lease_expires_at'] is not None):
            raise ValueError('A follow-up task already has a lease')
        tasks[kind] = {'id': rows[0]['id'] if rows else None,
                       'generation': max(rows[0]['generation'] if rows else 0,
                                         generation if kind == 'integrity_scan' else 0) + 1,
                       'prior': rows[0] if rows else None}
    prepared = {'mode': QUEUED_REBUILD_MODE, 'plan_version': 2, 'target': expected_target, 'task_id': task_id,
                'repertoire_id': repertoire_id, 'generation': generation, 'scope_token': token,
                'day': day, 'previous_graph_generation': publication[0]['generation'], 'tasks': tasks}
    with background_lease(), storage.authoritative_connection() as database:
        validate_selected_scope(database, prepared, lock=False)
    return prepared


def validate_selected_delivery(database, task, *, after_checkpoint=False):
    prepared = task['maintenance_scope']
    pin = prepared['tasks'].get(task['kind'])
    if (pin is None or (task['id'], task['generation']) != (pin['id'], pin['generation'])
            or task['payload'].get('repertoire_id') != prepared['repertoire_id']
            or task['payload'].get('maintenance_recovery_scope') != _selected_scope_key(prepared)
            or task['payload'].get('local_day') != prepared['day']
            or (task['kind'] != 'opening_graph_rebuild'
                and task['payload'].get('graph_generation') != prepared['generation'])):
        raise ValueError('Selected maintenance delivery differs from pinned scope')
    validate_selected_scope(database, prepared)
    next_kind = ({('opening_graph_rebuild', 'finalize'): 'integrity_scan',
                  ('integrity_scan', 'complete'): 'opening_segmentation'}).get((task['kind'], task['phase']))
    if next_kind and not after_checkpoint:
        # Transaction-local handoff authority. Existing row locks above protect
        # predecessors; the guarded upsert also fences the absent-row race.
        storage.set_selected_handoff(database, {'kind': next_kind, 'repertoire_id': prepared['repertoire_id'],
                                             'pin': prepared['tasks'][next_kind], 'graph_generation': prepared['generation'],
                                             'day': prepared['day']})


def validate_selected_enqueue(handoff, kind, repertoire_id, row, payload, *, after=False):
    if (kind, repertoire_id) != (handoff['kind'], handoff['repertoire_id']):
        raise ValueError('Selected owner attempted an unrelated handoff')
    pin = handoff['pin']
    actual_payload = json.loads(row['payload_json']) if after and row is not None else payload
    if not after:
        actual = {field: row[field] for field in _PREDECESSOR_FIELDS} if row else None
        if actual != pin['prior']:
            raise ValueError('Pinned downstream predecessor changed before enqueue')
    elif (row is None or row['generation'] != pin['generation'] or pin['id'] not in {None, row['id']}
          or (row['state'], row['phase'], row['lease_token'], row['lease_expires_at']) != ('queued', 'queued', None, None)
          or actual_payload != payload or actual_payload.get('repertoire_id') != repertoire_id
          or actual_payload.get('graph_generation') != handoff['graph_generation']
          or (kind == 'integrity_scan' and actual_payload.get('local_day') != handoff['day'])):
        raise ValueError('Owner enqueue differs from the pinned downstream result; rolling back')


def assert_selected_execution_not_started(prepared):
    """Never create a fresh preservation baseline after a claim/partial write."""
    with background_lease(), storage.authoritative_connection() as database:
        validate_selected_scope(database, prepared, lock=False)
        row = storage.unstarted_task(database, prepared['task_id'], prepared['generation'])
        claimed = storage.prior_claim(database, prepared['task_id'], prepared['generation'])
        if (row is None or (row['state'], row['phase'], row['attempt_count']) != ('queued', 'queued', 0)
                or json.loads(row['payload_json']).get('maintenance_recovery_scope') is not None or claimed):
            raise ValueError('Execution baseline is missing after maintenance started; preserve checkpoints and diagnose')


def wait_for_admission(operation):
    """Foreground denial is a yield, including preflight reads and lease release."""
    deadline = time.monotonic() + 900
    while True:
        try:
            return operation()
        except BackgroundAdmissionDeferred:
            if time.monotonic() >= deadline:
                raise RuntimeError('Foreground activity prevented maintenance for 15 minutes')
            time.sleep(.25)


def target_identity():
    return storage.target_identity_rows()[0]


def target_id():
    identity = target_identity()
    return stable_key(*(identity[field] for field in ('cluster_id', 'database_oid', 'database', 'schema')))


def scope_token(repertoire_id):
    rows = storage.scope_rows(repertoire_id)
    if len(rows) != 1 or not rows[0]['study_only_mode']:
        raise ValueError('Recovery requires an existing repertoire in study-only mode')
    return rows[0]


def validate_staged_graph(repertoire_id, generation):
    """Compare every staged occurrence to current source, and every physical card."""
    expected = []
    after_line_id = ''
    line_count = 0
    while (prepared := wait_for_admission(lambda: prepare_next_graph_line(repertoire_id, after_line_id))) is not None:
        expected.extend(prepared.steps)
        after_line_id = prepared.line_id
        line_count += 1
    deleted = {row['card_id'] for row in storage.deleted_cards()}
    expected = omit_deleted_graph_steps(tuple(expected), deleted)
    staged = storage.staged_rows(repertoire_id, generation)
    def expected_row(step):
        result = asdict(step)
        result['moves'] = list(result['moves'])
        result['decision_fen_keys'] = list(result['decision_fen_keys'])
        return result
    actual = []
    for row in staged:
        row.pop('generation')
        row['moves'] = json.loads(row.pop('moves_json'))
        row['decision_fen_keys'] = json.loads(row.pop('decision_fen_keys_json'))
        actual.append(row)
    expected_rows = sorted((expected_row(step) for step in expected), key=lambda row: (row['line_id'], row['decision_index']))
    if not expected_rows or actual != expected_rows:
        raise ValueError('Staged graph differs from current source lines; refusing publication')
    definitions = {}
    for step in expected:
        definition = (step.starting_fen, list(step.moves), step.trained_color)
        if step.card_id in definitions and definitions[step.card_id] != definition:
            raise ValueError('Conflicting staged card definitions')
        definitions[step.card_id] = definition
    memberships = {row['card_id'] for row in storage.membership_card_ids(repertoire_id)}
    if memberships != set(definitions):
        raise ValueError('Recovery would change repertoire memberships; refusing publication')
    for offset in range(0, len(definitions), 16):
        identifiers = sorted(definitions)[offset:offset + 16]
        cards = storage.physical_cards(identifiers)
        if len(cards) != len(identifiers):
            raise ValueError('Staged graph has missing cards')
        for card in cards:
            if (card['start_fen'], json.loads(card['moves_json']), card['trained_color']) != definitions[card['id']]:
                raise ValueError('Existing card differs from staged definition')
            kind = 'prefix' if any(step.card_id == card['id'] and step.segment_kind == 'prefix' for step in expected) else 'response'
            if card['kind'] != kind:
                raise ValueError('Recovery would change a frozen card kind')
    return {'source_lines': line_count, 'graph_steps': len(staged), 'physical_cards': len(definitions),
            'graph_checksum': stable_key(actual)}


def preflight(task_id, repertoire_id, generation, expected_target):
    if target_id() != expected_target:
        raise ValueError('Database target identity mismatch')
    token = scope_token(repertoire_id)
    task = storage.task_rows(task_id)
    if not task or task[0]['kind'] != 'opening_graph_rebuild' or task[0]['deduplication_key'] != repertoire_id:
        raise ValueError('Wrong recovery task or repertoire')
    task = task[0]
    if task['generation'] != generation or task['state'] != 'failed' or task['phase'] != 'publish':
        raise ValueError('Expected a failed graph task at its publish checkpoint')
    if json.loads(task['payload_json']).get('repertoire_id') != repertoire_id:
        raise ValueError('Task payload scope mismatch')
    if storage.publication_exists(repertoire_id):
        raise ValueError('Recovery is restricted to a first publication')
    validation = validate_staged_graph(repertoire_id, generation)
    if scope_token(repertoire_id) != token:
        raise ValueError('Source or frozen activation changed during preflight')
    return {'target': expected_target, 'task_id': task_id, 'repertoire_id': repertoire_id,
            'generation': generation, 'scope_token': token, 'validation': validation, 'task': task}


def claim_exact_task(database, task_id, kind, repertoire_id, generation, *, resume_failed=False, day, graph_generation=None,
                     selected_rebuild=None):
    """Explicit maintenance authority applies only to this exact row, not a kind."""
    task = storage.locked_task(database, task_id)
    if (task is None or task['kind'] != kind or task['deduplication_key'] != repertoire_id
            or task['generation'] != generation):
        raise ValueError('Recovery task identity or generation changed')
    payload = json.loads(task['payload_json'])
    if payload.get('repertoire_id') != repertoire_id:
        raise ValueError('Recovery payload scope mismatch')
    if kind in {'integrity_scan', 'opening_segmentation'} and payload.get('graph_generation') != graph_generation:
        raise ValueError('Follow-up task belongs to a different graph generation')
    now = datetime.now(timezone.utc)
    if selected_rebuild is not None:
        pin = selected_rebuild['tasks'].get(kind)
        if (pin is None or (task_id, generation) != (pin['id'], pin['generation'])
                or repertoire_id != selected_rebuild['repertoire_id'] or day != selected_rebuild['day']
                or resume_failed):
            raise ValueError('Unrelated task claim or changed maintenance pins')
        validate_selected_scope(database, selected_rebuild)
        marker = payload.get('maintenance_recovery_scope')
        if marker != _selected_scope_key(selected_rebuild):
            if marker is not None or (task['state'], task['phase'], task['attempt_count']) != ('queued', 'queued', 0):
                raise ValueError('Task was not started by this saved maintenance plan')
        elif payload.get('local_day') != day:
            raise ValueError('Saved task study day changed')
        if task['state'] == 'complete':
            return None
        expired = (task['state'] == 'leased' and task['lease_expires_at'] is not None
                   and datetime.fromisoformat(task['lease_expires_at']) <= now)
        if (task['state'] not in {'queued', 'retrying'} and not expired
                or task['phase'] not in _SELECTED_PHASES[kind]):
            raise ValueError('Selected task is leased, terminal, or at an unsafe checkpoint')
        if task['next_attempt_at'] and datetime.fromisoformat(task['next_attempt_at']) > now:
            raise ValueError('Selected task retry is not due; retain its cooldown')
        payload['maintenance_recovery_scope'] = _selected_scope_key(selected_rebuild)
    elif task['state'] == 'complete':
        return None
    allowed = {'queued', 'retrying'} | ({'failed'} if resume_failed else set())
    if selected_rebuild is None and (task['state'] not in allowed or (task['state'] == 'failed' and task['phase'] != 'publish')):
        raise ValueError('Recovery task is leased, terminal, or at an unsafe checkpoint')
    if selected_rebuild is None and kind == 'opening_graph_rebuild' and task['phase'] not in {'publish', 'classify', 'integrity', 'cleanup', 'finalize'}:
        raise ValueError('Recovery cannot restart graph staging')
    payload['local_day'] = day
    lease = str(uuid.uuid4())
    updated = storage.claim_task(database, (lease, (now + timedelta(seconds=60)).isoformat(), json.dumps(payload), now.isoformat(), task_id, generation))
    storage.record_claim(database, (task_id, generation, 'maintenance_recovery_claim', task['phase'], 'Explicit single-repertoire recovery', now.isoformat()))
    result = dict(updated)
    result['payload'] = payload
    result.pop('payload_json')
    if selected_rebuild is not None:
        result['maintenance_scope'] = selected_rebuild
    return result


def finish_queued_rebuild(prepared, *, progress=lambda value: None, save_plan=lambda value: None,
                          max_slices=20000, max_seconds=1200, stop_requested=lambda: False):
    """One bounded operator tree: graph, its scan, then its advisory segmentation.

    Queue refresh is requested by the ordinary integrity owner, never claimed here.
    Each committed slice remains durable on interruption; there is no global undo.
    """
    from .postgres_opening_segmentation import execute_segmentation_slice

    if not 0 < max_seconds <= 1200 or not 0 < max_slices <= 20000:
        raise ValueError('Maintenance budget exceeds 20 minutes or 20000 slices')
    deadline = time.monotonic() + max_seconds
    warm_completion_sql()
    slices = 0
    for kind, handler in (
        ('opening_graph_rebuild', execute_postgres_opening_graph_slice),
        ('integrity_scan', execute_postgres_integrity_slice),
        ('opening_segmentation', execute_segmentation_slice),
    ):
        pin = prepared['tasks'][kind]
        if kind != 'opening_graph_rebuild':
            followup = storage.followup_rows(kind, prepared['repertoire_id'])
            if (len(followup) != 1 or followup[0]['generation'] != pin['generation']
                    or pin['id'] not in {None, followup[0]['id']}
                    or json.loads(followup[0]['payload_json']).get('graph_generation') != prepared['generation']):
                raise ValueError('Owner did not request the pinned matching follow-up')
            pin['id'] = followup[0]['id']
            save_plan(prepared)
        while True:
            if stop_requested() or time.monotonic() >= deadline or slices >= max_slices:
                raise RuntimeError('Maintenance stopped at its budget; saved checkpoints are retained')
            try:
                with background_lease(), storage.maintenance_connection() as database:
                    task = claim_exact_task(database, pin['id'], kind, prepared['repertoire_id'], pin['generation'],
                                            day=prepared['day'], graph_generation=prepared['generation'], selected_rebuild=prepared)
            except BackgroundAdmissionDeferred:
                time.sleep(.25)
                continue
            if task is None:
                break
            progress({'slice': slices, 'kind': kind, 'task_id': task['id'], 'generation': task['generation'],
                      'lease_token': task['lease_token'], 'lease_expires_at': task['lease_expires_at'], 'phase': task['phase']})
            try:
                handler(task)
            except BackgroundAdmissionDeferred:
                defer_task_for_contention(task['id'], task['generation'], task['lease_token'], kind=kind)
                time.sleep(.25)
            except Exception as error:
                fail_task(task['id'], task['generation'], task['lease_token'], error)
                raise
            slices += 1
    with background_lease(), storage.authoritative_connection() as database:
        validate_selected_scope(database, prepared, lock=False)
        scan = storage.integrity_publication(database, prepared['repertoire_id'])
        segments = storage.segmentation_publication(database, prepared['repertoire_id'])
        scan_pin, segment_pin = prepared['tasks']['integrity_scan'], prepared['tasks']['opening_segmentation']
        if (scan is None or scan['status'] not in {'clean', 'needs_repair'} or scan['scan_status'] != 'idle'
                or scan['checked_at'] is None or scan['scan_generation'] != f"{scan_pin['id']}:{scan_pin['generation']}"
                or segments is None or segments['state'] != 'ready' or segments['graph_generation'] != prepared['generation']
                or segments['run_id'] != f"{segment_pin['id']}:{segment_pin['generation']}"):
            raise ValueError('Selected tasks completed without matching current integrity/segmentation publication')
    return {'state': 'complete', 'slices': slices, 'tasks': prepared['tasks'], 'day': prepared['day'],
            'integrity_status': scan['status'],
            'queue_refresh': 'shared ordinary daily refresh requested; not executed by this operator'}


def recover(prepared, *, day, progress=lambda value: None, max_slices=20000):
    """Run bounded ordinary handlers; leave mode, scores, and other tasks alone."""
    warm_completion_sql()
    repertoire_id = prepared['repertoire_id']
    kind, task_id, generation = 'opening_graph_rebuild', prepared['task_id'], prepared['generation']
    allow_failed = True
    for slice_number in range(max_slices):
        if scope_token(repertoire_id) != prepared['scope_token']:
            raise ValueError('Source, study mode, or Maia activation changed; stop recovery')
        try:
            with background_lease(), storage.maintenance_connection() as database:
                task = claim_exact_task(database, task_id, kind, repertoire_id, generation,
                                        resume_failed=allow_failed, day=day, graph_generation=prepared['generation'])
        except BackgroundAdmissionDeferred:
            time.sleep(.25)
            continue
        if task is None:
            if kind == 'integrity_scan':
                return {'state': 'complete', 'slices': slice_number, 'graph_task_id': prepared['task_id'], 'integrity_task_id': task_id}
            integrity = storage.integrity_task_rows(repertoire_id)
            if len(integrity) != 1 or json.loads(integrity[0]['payload_json']).get('graph_generation') != prepared['generation']:
                raise ValueError('Graph did not request the expected integrity task')
            kind, task_id, generation = 'integrity_scan', integrity[0]['id'], integrity[0]['generation']
            allow_failed = False
            continue
        allow_failed = False
        try:
            handler = execute_postgres_opening_graph_slice if kind == 'opening_graph_rebuild' else execute_postgres_integrity_slice
            handler(task)
        except BackgroundAdmissionDeferred:
            wait_for_admission(lambda: defer_task_for_contention(task_id, generation, task['lease_token'], kind=kind))
        except Exception as error:
            wait_for_admission(lambda: fail_task(task_id, generation, task['lease_token'], error))
            raise
        if slice_number % 25 == 0:
            progress({'slice': slice_number, 'kind': kind, 'phase': task['phase']})
        time.sleep(.01)
    raise RuntimeError('Recovery slice budget exhausted; saved checkpoints are retained')


def capture_evidence(repertoire_id, day, *, selected_rebuild=False):
    """Private maintenance evidence, paged without a long snapshot transaction."""
    activation = storage.frozen_activation()[0]
    evidence = {
        'recorded_at': datetime.now(timezone.utc).isoformat(), 'target': target_identity(),
        'settings': storage.settings_evidence(),
        'tasks': storage.task_evidence(repertoire_id),
        'sources': storage.source_evidence(repertoire_id),
        'graph': storage.graph_evidence(repertoire_id),
        'memberships': storage.membership_evidence(repertoire_id),
        'cards': storage.repertoire_card_evidence(repertoire_id),
        'reviews': storage.review_evidence(),
        'issued_origins': storage.issued_origin_evidence(),
        'activation': activation,
        'frozen_ranks': storage.frozen_rank_evidence(activation['run_id']),
        'queue': storage.queue_evidence(day),
    }
    if selected_rebuild:
        evidence['mode'] = QUEUED_REBUILD_MODE
        # A generated physical card may be shared with another repertoire.
        evidence['cards'] = storage.all_card_evidence()
        reviewed = {row['card_id'] for row in evidence['reviews']}
        schedule_fields = ('id', 'state', 'introduced_at', 'due_date', 'interval_days', 'ease', 'repetitions',
                           'lapses', 'fsrs_card_json', 'first_correct_at', 'stability', 'scheduling_mode',
                           'hard_correct_streak', 'recent_attempts_json', 'maximum_interval',
                           'reinforcement_pending', 'guided_review')
        evidence['protected_schedules'] = [{key: card[key] for key in schedule_fields}
                                           for card in evidence['cards']
                                           if card['introduced_at'] is not None or card['id'] in reviewed]
        issued = {row['queue_entry_id'] for row in evidence['issued_origins']}
        head = next((row['id'] for row in evidence['queue'] if row['status'] == 'queued'), None)
        evidence['protected_queue'] = [row for row in evidence['queue'] if row['id'] in issued or row['id'] == head]
    return evidence


def verify_preservation(before, after):
    """Allow new study records while checking that existing evidence survives."""
    def retained_rows(component, key, fields=None):
        row_key = lambda row: tuple(row[field] for field in key) if isinstance(key, tuple) else row[key]
        current = {row_key(row): row for row in after[component]}
        for previous in before[component]:
            row = current.get(row_key(previous))
            if row is None:
                return False
            if fields is None and row != previous:
                return False
            if fields is not None and any(row.get(field) != previous.get(field) for field in fields):
                return False
        return True
    content_fields = ('id', 'revision', 'start_fen', 'moves_json', 'trained_color', 'kind',
                      'content_type', 'source_ref', 'source_fen')
    checks = {
        'database_identity': before['target'] == after['target'],
        'settings': before['settings'] == after['settings'],
        'source_lines': before['sources'] == after['sources'],
        'memberships': before['memberships'] == after['memberships'],
        'card_definitions': retained_rows('cards', 'id', content_fields),
        'reviews': retained_rows('reviews', 'id'),
        'issued_attempt_definitions': retained_rows('issued_origins', ('queue_entry_id', 'card_id', 'revision'),
                                                 (*content_fields, 'card_id', 'queue_date', 'cycle', 'issued_as_head')),
        'frozen_activation': before['activation'] == after['activation'],
        'frozen_ranks': before['frozen_ranks'] == after['frozen_ranks'],
    }
    if before.get('mode') == QUEUED_REBUILD_MODE:
        checks['memberships'] = all(row in after['memberships'] for row in before['memberships']
                                    if row.get('canonical_route_source'))
        checks['issued_attempt_definitions'] = retained_rows('issued_origins', ('queue_entry_id', 'card_id', 'revision'))
        checks['protected_schedules'] = retained_rows('protected_schedules', 'id')
        checks['protected_queue'] = retained_rows('protected_queue', 'id')
    return {'passed': all(checks.values()), 'checks': checks}
