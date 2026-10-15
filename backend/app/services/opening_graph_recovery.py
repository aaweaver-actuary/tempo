"""Explicit, single-repertoire maintenance recovery; never enables general workers.

Only a failed first publication can start recovery. Ordinary phase handlers retain
ownership of graph, integrity and queue effects, including their lease fences.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
import time
import uuid

from .. import postgres_store
from ..card_deletion import omit_deleted_graph_steps
from .durable_tasks import defer_task_for_contention, fail_task, warm_completion_sql
from .opening_segmentation import stable_key
from .postgres_opening_graph import execute_postgres_opening_graph_slice, prepare_next_graph_line
from .postgres_integrity import execute_postgres_integrity_slice
from .redis_admission_gate import BackgroundAdmissionDeferred, background_lease


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


def read_rows(statement, parameters=()):
    with postgres_store.connection(read_only=True) as database:
        return [dict(row) for row in database.execute_native(statement, parameters).fetchall()]


def target_identity():
    return read_rows("SELECT (SELECT system_identifier::text FROM pg_control_system()) cluster_id,"
                     "(SELECT oid::text FROM pg_database WHERE datname=current_database()) database_oid,"
                     "current_database() database,current_schema() schema")[0]


def target_id():
    identity = target_identity()
    return stable_key(*(identity[field] for field in ('cluster_id', 'database_oid', 'database', 'schema')))


def scope_token(repertoire_id):
    rows = read_rows("SELECT r.scope_source_revision,r.canonical_prefix_revision,"
                     "s.initial_depth,s.study_only_mode,a.run_id,a.epoch "
                     "FROM repertoires r CROSS JOIN settings s CROSS JOIN frozen_opening_activation a "
                     "WHERE r.id=%s AND s.id=1 AND a.id=1", (repertoire_id,))
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
    deleted = {row['card_id'] for row in read_rows('SELECT card_id FROM deleted_cards')}
    expected = omit_deleted_graph_steps(tuple(expected), deleted)
    staged = read_rows('SELECT * FROM opening_graph_steps WHERE repertoire_id=%s AND generation=%s '
                       'ORDER BY line_id,decision_index', (repertoire_id, generation))
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
    memberships = {row['card_id'] for row in read_rows('SELECT card_id FROM repertoire_cards WHERE repertoire_id=%s', (repertoire_id,))}
    if memberships != set(definitions):
        raise ValueError('Recovery would change repertoire memberships; refusing publication')
    for offset in range(0, len(definitions), 16):
        identifiers = sorted(definitions)[offset:offset + 16]
        cards = read_rows('SELECT * FROM cards WHERE id=ANY(%s)', (identifiers,))
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
    task = read_rows('SELECT * FROM background_tasks WHERE id=%s', (task_id,))
    if not task or task[0]['kind'] != 'opening_graph_rebuild' or task[0]['deduplication_key'] != repertoire_id:
        raise ValueError('Wrong recovery task or repertoire')
    task = task[0]
    if task['generation'] != generation or task['state'] != 'failed' or task['phase'] != 'publish':
        raise ValueError('Expected a failed graph task at its publish checkpoint')
    if json.loads(task['payload_json']).get('repertoire_id') != repertoire_id:
        raise ValueError('Task payload scope mismatch')
    if read_rows('SELECT 1 FROM opening_graph_publications WHERE repertoire_id=%s', (repertoire_id,)):
        raise ValueError('Recovery is restricted to a first publication')
    validation = validate_staged_graph(repertoire_id, generation)
    if scope_token(repertoire_id) != token:
        raise ValueError('Source or frozen activation changed during preflight')
    return {'target': expected_target, 'task_id': task_id, 'repertoire_id': repertoire_id,
            'generation': generation, 'scope_token': token, 'validation': validation, 'task': task}


def claim_exact_task(database, task_id, kind, repertoire_id, generation, *, resume_failed=False, day, graph_generation=None):
    """Explicit maintenance authority applies only to this exact row, not a kind."""
    task = database.execute_native('SELECT * FROM background_tasks WHERE id=%s FOR UPDATE', (task_id,)).fetchone()
    if (task is None or task['kind'] != kind or task['deduplication_key'] != repertoire_id
            or task['generation'] != generation):
        raise ValueError('Recovery task identity or generation changed')
    if task['state'] == 'complete':
        return None
    allowed = {'queued', 'retrying'} | ({'failed'} if resume_failed else set())
    if task['state'] not in allowed or (task['state'] == 'failed' and task['phase'] != 'publish'):
        raise ValueError('Recovery task is leased, terminal, or at an unsafe checkpoint')
    if kind == 'opening_graph_rebuild' and task['phase'] not in {'publish', 'classify', 'integrity', 'cleanup', 'finalize'}:
        raise ValueError('Recovery cannot restart graph staging')
    payload = json.loads(task['payload_json'])
    if payload.get('repertoire_id') != repertoire_id:
        raise ValueError('Recovery payload scope mismatch')
    if kind == 'integrity_scan' and payload.get('graph_generation') != graph_generation:
        raise ValueError('Integrity task belongs to a different graph generation')
    payload['local_day'] = day
    now = datetime.now(timezone.utc)
    lease = str(uuid.uuid4())
    updated = database.execute_native(
        "UPDATE background_tasks SET state='leased',lease_token=%s,lease_expires_at=%s,"
        "payload_json=%s,attempt_count=attempt_count+1,completed_at=NULL,updated_at=%s "
        "WHERE id=%s AND generation=%s RETURNING *",
        (lease, (now + timedelta(seconds=60)).isoformat(), json.dumps(payload), now.isoformat(), task_id, generation),
    ).fetchone()
    database.execute_native('INSERT INTO background_task_events(task_id,generation,event,phase,detail,created_at) '
                            'VALUES(%s,%s,%s,%s,%s,%s)',
                            (task_id, generation, 'maintenance_recovery_claim', task['phase'], 'Explicit single-repertoire recovery', now.isoformat()))
    result = dict(updated)
    result['payload'] = payload
    result.pop('payload_json')
    return result


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
            with background_lease(), postgres_store.connection(background=True) as database:
                task = claim_exact_task(database, task_id, kind, repertoire_id, generation,
                                        resume_failed=allow_failed, day=day, graph_generation=prepared['generation'])
        except BackgroundAdmissionDeferred:
            time.sleep(.25)
            continue
        if task is None:
            if kind == 'integrity_scan':
                return {'state': 'complete', 'slices': slice_number, 'graph_task_id': prepared['task_id'], 'integrity_task_id': task_id}
            integrity = read_rows("SELECT id,generation,payload_json FROM background_tasks WHERE kind='integrity_scan' AND deduplication_key=%s", (repertoire_id,))
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


def capture_evidence(repertoire_id, day):
    """Private maintenance evidence, paged without a long snapshot transaction."""
    def paged(table, key, predicate='TRUE', parameters=()):
        rows, cursor = [], 0 if key in {'id_numeric', 'queue_entry_id'} else ''
        column = 'id' if key == 'id_numeric' else key
        while True:
            page = read_rows(f'SELECT * FROM {table} WHERE ({predicate}) AND {column}>%s ORDER BY {column} LIMIT 128', (*parameters, cursor))
            rows.extend(page)
            if len(page) < 128:
                return rows
            cursor = page[-1][column]
    activation = read_rows('SELECT * FROM frozen_opening_activation WHERE id=1')[0]
    return {
        'recorded_at': datetime.now(timezone.utc).isoformat(), 'target': target_identity(),
        'settings': read_rows('SELECT * FROM settings'),
        'tasks': read_rows('SELECT * FROM background_tasks WHERE deduplication_key=%s ORDER BY id', (repertoire_id,)),
        'sources': read_rows('SELECT * FROM repertoire_lines WHERE repertoire_id=%s ORDER BY id', (repertoire_id,)),
        'graph': read_rows('SELECT * FROM opening_graph_steps WHERE repertoire_id=%s ORDER BY generation,line_id,decision_index', (repertoire_id,)),
        'memberships': read_rows('SELECT * FROM repertoire_cards WHERE repertoire_id=%s ORDER BY card_id', (repertoire_id,)),
        'cards': paged('cards', 'id', 'repertoire_id=%s', (repertoire_id,)),
        'reviews': paged('reviews', 'id_numeric'),
        'issued_origins': paged('queue_attempt_origins', 'queue_entry_id', 'issued_as_head=1'),
        'activation': activation,
        'frozen_ranks': paged('frozen_opening_ranks', 'card_id', 'run_id=%s', (activation['run_id'],)),
        'queue': read_rows('SELECT q.*,c.content_type FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.queue_date=%s ORDER BY q.position,q.id', (day,)),
    }


def verify_preservation(before, after):
    """Allow new study records while checking that existing evidence survives."""
    def retained_rows(component, key, fields=None):
        current = {row[key]: row for row in after[component]}
        for previous in before[component]:
            row = current.get(previous[key])
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
        'issued_attempt_definitions': retained_rows('issued_origins', 'queue_entry_id',
                                                 (*content_fields, 'card_id', 'queue_date', 'cycle', 'issued_as_head')),
        'frozen_activation': before['activation'] == after['activation'],
        'frozen_ranks': before['frozen_ranks'] == after['frozen_ranks'],
    }
    return {'passed': all(checks.values()), 'checks': checks}
