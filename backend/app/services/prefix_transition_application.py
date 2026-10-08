"""Issue 80: one exact-plan application, invisible staging and atomic activation."""
from dataclasses import dataclass, replace
from datetime import date
import json
import logging

from fastapi import HTTPException
from psycopg.errors import SerializationFailure

from .. import postgres_store, prefix_evaluation_api as structural, prefix_transition_api as planner_api
from ..command_gateway import register_command
from ..prefix_transition_contracts import PrefixTransitionApplyRequest, PrefixTransitionPlan
from ..queue_position_lock import lock_queue_date_for_position
from ..snapshot_reads import SnapshotRead, snapshot_rows
from .durable_tasks import enqueue_task_in_transaction, lock_current_slice, advance_task_slice_in_transaction, complete_task_slice_in_transaction
from .prefix_evaluation import snapshot_identity
from .prefix_transition import iter_transition_plan, transition_snapshot_identity, validate_shortening
from .postgres_opening_graph import stage_graph_steps, create_graph_cards, remove_obsolete_graph_memberships, publish_graph_generation

COMMAND = 'repertoire.prefix_transition.apply'
TASK_KIND = 'prefix_transition_application'
_LOGGER = logging.getLogger(__name__)


def conflict(code, message):
    return HTTPException(409, {'code': code, 'message': message})


def validate_approved_plan(request, plan, *, today):
    if plan.status == 'blocked':
        raise conflict('blocked_plan', 'This transition is blocked. Resolve its named blockers and request a fresh plan.')
    if (request.study_day != today or request.plan_id != plan.plan_id or request.snapshot_id != plan.snapshot_id
            or request.transition_snapshot_id != plan.transition_snapshot_id or request.graph_generation != plan.graph_generation
            or request.study_day != plan.study_day or tuple(sorted(request.selected_line_ids)) != plan.selected_line_ids):
        raise conflict('stale_plan', 'The approved transition changed. Refresh the source and approve a fresh plan with a new operation identity.')


@dataclass(frozen=True)
class PreparedApplication:
    payload: dict
    plan: PrefixTransitionPlan | None
    snapshot: object | None = None
    reads: tuple = ()
    creations: tuple = ()
    repertoire_ids: tuple = ()
    pending_ids: tuple = ()
    queue_days: tuple = ()
    staged_generation: int | None = None


def prepare_application(payload):
    request = PrefixTransitionApplyRequest.model_validate(payload['request'])
    with postgres_store.connection(read_only=True, authoritative=True) as database:
        receipt = database.execute_native('SELECT state FROM operation_receipts WHERE operation_id=%s',
                                          (payload['operation_id'],)).fetchone()
        if receipt and receipt[0] in {'complete', 'failed'}:
            return PreparedApplication(payload, None)
        prior = database.execute_native('SELECT state,graph_generation FROM prefix_transition_applications WHERE operation_id=%s',
                                        (payload['operation_id'],)).fetchone()
    if prior and prior[0] in {'publishing', 'complete', 'recovery_required', 'rejected'}:
        return PreparedApplication(payload, None)
    # Worker preparation is secondary and must not inherit its own foreground
    # lease. These are exactly the planner's limits and admission rules.
    with structural.diagnostic_request() as deadline:
        source = structural.load_snapshot(payload['repertoire_id'], deadline)
        if snapshot_identity(source) != request.snapshot_id:
            raise conflict('stale_plan', 'The repertoire source changed. Approve a fresh plan.')
        validate_shortening(source, request.selected_line_ids, request.candidate_depths)
        evaluation = planner_api._calculate(structural.iter_prefix_evaluation(source, request.selected_line_ids, request.candidate_depths), deadline)
        lookup_ids = {step['card_id'] for phase in ('current', 'proposed') for step in evaluation['whole_repertoire'][phase]['steps']}
        lookup_ids.update(planner_api.discover_memberships(payload['repertoire_id'], deadline))
        for split in source.prefix_overrides:
            lookup_ids.update((split.source_card_id, split.shortened_card_id, split.continuation_card_id))
        reads = []
        snapshot = planner_api.load_transition_snapshot(payload['repertoire_id'], lookup_ids, request.study_day, deadline, reads=reads)
        plan = planner_api._calculate(iter_transition_plan(snapshot, request.selected_line_ids, request.candidate_depths, evaluation), deadline)
        refreshed = planner_api.load_transition_snapshot(payload['repertoire_id'], lookup_ids, request.study_day, deadline)
        if transition_snapshot_identity(refreshed) != plan.transition_snapshot_id:
            raise conflict('stale_plan', 'Study or source state changed during preparation. Approve a fresh plan.')
        validate_approved_plan(request, plan, today=date.today().isoformat())
    if plan.status != 'no_op':
        integrity_query = (
            "SELECT integrity.status,integrity.scan_status,integrity.scan_generation,task.id,task.generation,task.state,"
            "task.payload_json FROM repertoire_integrity_state integrity LEFT JOIN background_tasks task "
            "ON task.kind='integrity_scan' AND task.deduplication_key=integrity.repertoire_id WHERE integrity.repertoire_id=%s")
        with postgres_store.connection(read_only=True, background=True) as database:
            integrity_rows = snapshot_rows(database, integrity_query, (plan.repertoire_id,))
        integrity = json.loads(integrity_rows[0]) if len(integrity_rows) == 1 else None
        if not (integrity and integrity['status'] == 'clean' and integrity['scan_status'] == 'idle'
                and integrity['state'] == 'complete' and integrity['scan_generation'] == f"{integrity['id']}:{integrity['generation']}"
                and json.loads(integrity['payload_json'])['graph_generation'] == plan.graph_generation):
            raise conflict('source_integrity_not_ready', 'Wait for the current source graph to finish its clean integrity scan, then approve a fresh plan.')
        reads.append(SnapshotRead(integrity_query, (plan.repertoire_id,), integrity_rows))
        structural.check_available(deadline)
    if prior and prior[0] == 'staged':
        query = ('SELECT line_id,decision_index,segment_kind,first_decision_index,last_decision_index,'
                 'decision_fen_keys_json,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color '
                 'FROM opening_graph_steps WHERE repertoire_id=%s AND generation=%s')
        parameters = (plan.repertoire_id, prior[1])
        with postgres_store.connection(read_only=True, background=True) as database:
            staged = snapshot_rows(database, query, parameters)
        expected = []
        for step in plan.proposed_steps:
            expected.append(dict(line_id=step.line_id, decision_index=step.decision_index, segment_kind=step.segment_kind,
                                 first_decision_index=step.first_decision_index, last_decision_index=step.last_decision_index,
                                 decision_fen_keys_json=json.dumps(step.decision_fen_keys), card_id=step.card_id,
                                 parent_card_id=step.parent_card_id, decision_fen_key=step.decision_fen_key,
                                 starting_fen=step.starting_fen, moves_json=json.dumps(step.moves), trained_color=step.trained_color))
        if sorted((json.loads(row) for row in staged), key=lambda row: (row['line_id'], row['decision_index'])) != sorted(expected, key=lambda row: (row['line_id'], row['decision_index'])):
            raise conflict('stale_plan', 'The staged graph does not match the approved transition. Request a fresh plan.')
        reads.append(SnapshotRead(query, parameters, staged))
        structural.check_available(deadline)
    creations = []
    for card in plan.cards:
        if card.lifecycle != 'create':
            continue
        fresh_card_is_root = json.loads(card.schedule_json)['state'] == 'new'
        reference = next(step for step in plan.proposed_steps if step.card_id == card.card_id
                         and (step.parent_card_id is None) == fresh_card_is_root)
        creations.append(replace(reference, segment_kind='prefix' if card.kind_after == 'prefix' else 'decision'))
        structural.check_available(deadline)
    return PreparedApplication(payload, plan, snapshot, tuple(reads), tuple(creations),
                               tuple(sorted({row['id'] for row in snapshot.rows('repertoires')} | {plan.repertoire_id})),
                               tuple(sorted(attempt.object_id for attempt in plan.attempts if attempt.kind == 'pending_command')),
                               tuple(sorted({row['queue_date'] for row in snapshot.rows('daily_queue')} | {plan.study_day})),
                               prior[1] if prior and prior[0] == 'staged' else None)


def own_application(database, operation_id):
    database.execute_native("SELECT set_config('tempo.prefix_transition_operation',%s,true)", (operation_id,))


def lock_and_revalidate(database, prepared):
    """Receipt/task -> repertoire -> card -> attempt/queue; raw comparisons only."""
    snapshot = prepared.snapshot
    repertoire_ids = list(prepared.repertoire_ids)
    pending_ids = list(prepared.pending_ids)
    database.execute_native('SELECT operation_id FROM operation_receipts WHERE operation_id=ANY(%s) ORDER BY operation_id FOR UPDATE NOWAIT', (pending_ids,)).fetchall()
    database.execute_native("SELECT id FROM background_tasks WHERE kind IN ('opening_graph_rebuild','integrity_scan') AND deduplication_key=ANY(%s) ORDER BY id FOR UPDATE", (repertoire_ids,)).fetchall()
    for repertoire_id in repertoire_ids:
        database.execute_native('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f'tempo:opening-graph:{repertoire_id}',))
    for card_id in snapshot.lookup_card_ids:
        database.execute_native('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (f'tempo:card-edit:{card_id}',))
    occupied = database.execute_native(
        'SELECT operation_id FROM prefix_transition_card_fences WHERE card_id=ANY(%s) AND operation_id<>%s '
        'UNION SELECT operation_id FROM prefix_transition_repertoire_fences WHERE repertoire_id=ANY(%s) AND operation_id<>%s',
        (list(snapshot.lookup_card_ids), prepared.payload['operation_id'], repertoire_ids, prepared.payload['operation_id']),
    ).fetchone()
    if occupied and prepared.plan.status != 'no_op':
        raise conflict('prefix_transition_in_progress', 'A conflicting prefix application owns these identities. Wait for its publication or recovery.')
    database.execute_native('SELECT id FROM repertoires WHERE id=ANY(%s) ORDER BY id FOR UPDATE', (repertoire_ids,)).fetchall()
    database.execute_native('SELECT repertoire_id FROM repertoire_integrity_state WHERE repertoire_id=ANY(%s) ORDER BY repertoire_id FOR SHARE', (repertoire_ids,)).fetchall()
    database.execute_native('SELECT id FROM repertoire_lines WHERE repertoire_id=%s ORDER BY id FOR UPDATE', (prepared.plan.repertoire_id,)).fetchall()
    database.execute_native('SELECT depth.line_id FROM repertoire_line_training_depths depth JOIN repertoire_lines line ON line.id=depth.line_id WHERE line.repertoire_id=%s ORDER BY depth.line_id FOR UPDATE OF depth', (prepared.plan.repertoire_id,)).fetchall()
    database.execute_native('SELECT id FROM cards WHERE id=ANY(%s) ORDER BY id FOR UPDATE', (list(snapshot.lookup_card_ids),)).fetchall()
    database.execute_native('SELECT card_id FROM repertoire_cards WHERE card_id=ANY(%s) OR repertoire_id=%s ORDER BY card_id,repertoire_id FOR UPDATE', (list(snapshot.lookup_card_ids), prepared.plan.repertoire_id)).fetchall()
    queue_days = prepared.queue_days
    for queue_day in queue_days:
        lock_queue_date_for_position(database, queue_day)
    for table, identity in (('daily_queue', 'id'), ('opening_evidence_attempts', 'attempt_id'), ('study_attempts', 'id')):
        database.execute_native(f'SELECT {identity} FROM {table} WHERE card_id=ANY(%s) ORDER BY {identity} FOR UPDATE', (list(snapshot.lookup_card_ids),)).fetchall()
    for captured in prepared.reads:
        if snapshot_rows(database, captured.query, captured.parameters) != captured.rows:
            _LOGGER.warning('prefix_transition_stale_input query=%s', captured.query[:160])
            raise conflict('stale_plan', 'Source, cards, schedules, memberships or attempts changed before the write. Approve a fresh plan.')
    if prepared.plan.study_day != date.today().isoformat():
        raise conflict('stale_plan', 'The study day changed before application. Approve a fresh plan.')


def release_fences(database, operation_id):
    database.execute_native('DELETE FROM prefix_transition_card_fences WHERE operation_id=%s', (operation_id,))
    database.execute_native('DELETE FROM prefix_transition_repertoire_fences WHERE operation_id=%s', (operation_id,))


def reject_unactivated_application(database, operation_id, error):
    application = database.execute_native('SELECT state FROM prefix_transition_applications WHERE operation_id=%s FOR UPDATE', (operation_id,)).fetchone()
    if not application:
        return False
    committed = application[0] in {'publishing', 'recovery_required'}
    database.execute_native("UPDATE prefix_transition_applications SET state=%s,last_error=%s,updated_at=NOW() WHERE operation_id=%s",
                            ('recovery_required' if committed else 'rejected', str(error)[:500], operation_id))
    if not committed:
        release_fences(database, operation_id)
    return committed


def apply_command(database, prepared):
    operation_id = prepared.payload['operation_id']
    # A recovered command must never revalidate the pre-application snapshot
    # against its already committed business effect.
    application = database.execute_native('SELECT * FROM prefix_transition_applications WHERE operation_id=%s FOR UPDATE', (operation_id,)).fetchone()
    if application and application['state'] in {'publishing', 'recovery_required', 'complete'}:
        if application['state'] == 'complete':
            return json.loads(application['result_json'])
        resume_publication(database, application)
        return {'status': 'pending'}
    if application and application['state'] == 'rejected':
        raise conflict('stale_plan', 'This transition was rejected before activation. Approve a fresh plan with a new operation identity.')
    if prepared.plan is None:
        raise conflict('stale_plan', 'No current prepared transition is available.')
    lock_and_revalidate(database, prepared)
    if prepared.plan.status == 'no_op':
        return {'status': 'complete', 'operation_id': operation_id, 'plan_id': prepared.plan.plan_id,
                'repertoire_id': prepared.plan.repertoire_id, 'graph_generation': prepared.plan.graph_generation, 'no_op': True}
    if application is None:
        generation = database.execute_native('SELECT COALESCE(MAX(generation),0)+1 FROM opening_graph_steps WHERE repertoire_id=%s', (prepared.plan.repertoire_id,)).fetchone()[0]
        database.execute_native("INSERT INTO prefix_transition_applications(operation_id,repertoire_id,plan_id,plan_json,graph_generation,state) VALUES(%s,%s,%s,%s,%s,'staging')",
                                (operation_id, prepared.plan.repertoire_id, prepared.plan.plan_id, prepared.plan.model_dump_json(), generation))
        with database.raw.cursor() as cursor:
            cursor.executemany('INSERT INTO prefix_transition_card_fences(card_id,operation_id) VALUES(%s,%s)', [(card_id, operation_id) for card_id in prepared.snapshot.lookup_card_ids])
            cursor.executemany('INSERT INTO prefix_transition_repertoire_fences(repertoire_id,operation_id) VALUES(%s,%s)', [(repertoire_id, operation_id) for repertoire_id in prepared.repertoire_ids])
        task = enqueue_task_in_transaction(database, TASK_KIND, operation_id, {'operation_id': operation_id, 'offset': 0}, priority=40)
        database.execute_native('UPDATE prefix_transition_applications SET staging_task_id=%s WHERE operation_id=%s', (task['id'], operation_id))
        return {'status': 'pending'}
    if application['state'] == 'staging':
        return {'status': 'pending'}
    if prepared.staged_generation != application['graph_generation']:
        raise SerializationFailure('Staging finished during preparation; recapture its exact graph before activation')
    activate_application(database, prepared, application)
    return {'status': 'pending'}


def activate_application(database, prepared, application):
    plan = prepared.plan
    operation_id = application['operation_id']
    generation = application['graph_generation']
    own_application(database, operation_id)
    # Strict creates are protected by missing-ID reservations, never ON CONFLICT
    # adoption. Existing cards retain every scheduling/default/history column.
    create_graph_cards(database, prepared.creations, plan.study_day, strict=True)
    with database.raw.cursor() as cursor:
        cursor.executemany('INSERT INTO repertoire_line_training_depths(line_id,learner_decision_count) VALUES(%s,%s) ON CONFLICT(line_id) DO UPDATE SET learner_decision_count=excluded.learner_decision_count', [(change.line_id, change.after) for change in plan.depth_changes])
        cursor.executemany('INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(%s,%s,0)', [(membership.repertoire_id, membership.card_id) for membership in plan.memberships if membership.action == 'add_generated'])
        cursor.executemany('UPDATE cards SET kind=%s WHERE id=%s AND canonical_route_source=0', [(card.kind_after, card.card_id) for card in plan.cards if card.kind_after is not None])
    obsolete_ids = tuple(membership.card_id for membership in plan.memberships if membership.action == 'obsolete')
    remove_obsolete_graph_memberships(database, plan.repertoire_id, generation, obsolete_ids)
    for kind, table, identity in (('opening_attempt', 'opening_evidence_attempts', 'attempt_id'), ('study_attempt', 'study_attempts', 'id')):
        retired = [attempt.object_id for attempt in plan.attempts if attempt.kind == kind and attempt.action == 'retire_with_conflict']
        database.execute_native(f'UPDATE {table} SET retired_operation_id=%s WHERE {identity}=ANY(%s)', (operation_id, retired))
    retired_commands = [attempt.object_id for attempt in plan.attempts if attempt.kind == 'pending_command' and attempt.action == 'retire_with_conflict']
    error = json.dumps({'message': 'The original presentation was archived by an approved prefix transition.', 'code': 'card_archived', 'status_code': 409, 'retryable': False})
    database.execute_native("UPDATE operation_receipts SET state='failed',error_json=%s,attempt_token=NULL,lease_expires_at=NULL,next_retry_at=NULL,updated_at=NOW() WHERE operation_id=ANY(%s) AND state NOT IN ('complete','failed')", (error, retired_commands))
    publish_graph_generation(database, plan.repertoire_id, generation)
    graph_task = enqueue_task_in_transaction(database, 'opening_graph_rebuild', plan.repertoire_id,
                                            {'repertoire_id': plan.repertoire_id, 'local_day': plan.study_day}, priority=40, minimum_generation=generation-1)
    if graph_task['generation'] != generation:
        raise conflict('stale_plan', 'The graph generation changed before activation.')
    database.execute_native("UPDATE background_tasks SET phase='finalize' WHERE id=%s AND generation=%s", (graph_task['id'], generation))
    database.execute_native("UPDATE prefix_transition_applications SET state='publishing',graph_task_id=%s,updated_at=NOW() WHERE operation_id=%s", (graph_task['id'], operation_id))


def execute_application_slice(task):
    """One eight-step invisible stage or one bounded finalization, then yield."""
    operation_id = task['payload']['operation_id']
    with postgres_store.connection(read_only=True, background=True) as database:
        application = database.execute_native('SELECT * FROM prefix_transition_applications WHERE operation_id=%s', (operation_id,)).fetchone()
    if not application:
        raise RuntimeError('Transition application state is missing')
    plan = PrefixTransitionPlan.model_validate_json(application['plan_json'])
    offset = int(task['payload'].get('offset', 0))
    batch = plan.proposed_steps[offset:offset+8] if application['state'] == 'staging' else ()
    from .redis_admission_gate import background_lease
    with background_lease():
        with postgres_store.connection(read_only=False, background=True) as database:
            # Completion may race command replay; receipt comes before task/app.
            database.execute_native('SELECT operation_id FROM operation_receipts WHERE operation_id=%s FOR UPDATE', (operation_id,)).fetchone()
            if not lock_current_slice(database, task):
                return False
            current = database.execute_native('SELECT * FROM prefix_transition_applications WHERE operation_id=%s FOR UPDATE', (operation_id,)).fetchone()
            if current['state'] == 'staging':
                own_application(database, operation_id)
                stage_graph_steps(database, batch, current['graph_generation'])
                if batch:
                    return advance_task_slice_in_transaction(database, task, next_phase='stage', next_payload={'operation_id': operation_id, 'offset': offset+len(batch)})
                database.execute_native("UPDATE prefix_transition_applications SET state='staged',updated_at=NOW() WHERE operation_id=%s", (operation_id,))
                database.execute_native("UPDATE operation_receipts SET state='queued',attempt_token=NULL,lease_expires_at=NULL,next_retry_at=NULL,updated_at=NOW()-INTERVAL '31 seconds' WHERE operation_id=%s AND state='pending'", (operation_id,))
            elif current['state'] in {'publishing', 'recovery_required'}:
                finish_application(database, current, plan)
            return complete_task_slice_in_transaction(database, task)


def resume_publication(database, application):
    own_application(database, application['operation_id'])
    for kind, task_id in (('opening_graph_rebuild', None), ('integrity_scan', application['integrity_task_id']), ('daily_queue', application['queue_task_id'])):
        database.execute_native("UPDATE background_tasks SET state='queued',attempt_count=0,lease_token=NULL,lease_expires_at=NULL,last_error=NULL,completed_at=NULL,next_attempt_at=NOW()::text WHERE state='failed' AND kind=%s AND (id=%s OR (%s::text IS NULL AND deduplication_key=%s))", (kind, task_id, task_id, application['repertoire_id']))
    database.execute_native("UPDATE prefix_transition_applications SET state='publishing',last_error=NULL,updated_at=NOW() WHERE operation_id=%s", (application['operation_id'],))
    if application['queue_task_id'] and database.execute_native(
            "SELECT 1 FROM background_tasks WHERE id=%s AND generation>=%s AND state='complete'",
            (application['queue_task_id'], application['queue_generation'])).fetchone():
        enqueue_completion(database, application['queue_task_id'], application['queue_generation'], application['queue_date'])


def enqueue_completion(database, queue_task_id, queue_generation, queue_day):
    application = database.execute_native("SELECT operation_id FROM prefix_transition_applications WHERE state IN ('publishing','recovery_required') AND queue_task_id=%s AND queue_generation<=%s AND queue_date=%s ORDER BY operation_id LIMIT 1", (queue_task_id, queue_generation, queue_day)).fetchone()
    if application:
        enqueue_task_in_transaction(database, TASK_KIND, application[0], {'operation_id': application[0], 'phase': 'complete'}, priority=40)


def finish_application(database, application, plan):
    from ..queue_commands import request_queue_refresh_in_transaction
    today = date.today().isoformat()
    if application['queue_date'] != today:
        task = request_queue_refresh_in_transaction(database, today)
        record_queue_target(database, application['repertoire_id'], application['graph_generation'], task, today)
        return
    ready = database.execute_native("SELECT 1 FROM opening_graph_publications graph JOIN background_tasks graph_task ON graph_task.kind='opening_graph_rebuild' AND graph_task.deduplication_key=graph.repertoire_id JOIN repertoire_integrity_state integrity ON integrity.repertoire_id=graph.repertoire_id JOIN background_tasks integrity_task ON integrity_task.id=%s JOIN background_tasks queue_task ON queue_task.id=%s JOIN queue_projections queue ON queue.queue_date=%s WHERE graph.repertoire_id=%s AND graph.generation=%s AND graph.state='ready' AND graph_task.generation=graph.generation AND graph_task.state='complete' AND integrity.status='clean' AND integrity.scan_status='idle' AND integrity.scan_generation=%s AND integrity_task.generation=%s AND integrity_task.state='complete' AND queue_task.generation>=%s AND queue_task.state='complete' AND queue_task.payload_json::jsonb->>'queue_date'=%s AND queue.state='ready' AND queue.refresh_pending=0", (application['integrity_task_id'], application['queue_task_id'], today, application['repertoire_id'], application['graph_generation'], f"{application['integrity_task_id']}:{application['integrity_generation']}", application['integrity_generation'], application['queue_generation'], today)).fetchone()
    if not ready:
        raise RuntimeError('Transition publication is not at the recorded clean graph/integrity/queue steady state; retry the existing operation after resolving its linked tasks.')
    result = json.dumps({'status': 'complete', 'operation_id': application['operation_id'], 'plan_id': plan.plan_id,
                         'repertoire_id': plan.repertoire_id, 'graph_generation': application['graph_generation'], 'queue_date': today, 'no_op': False})
    database.execute_native("UPDATE prefix_transition_applications SET state='complete',result_json=%s,last_error=NULL,updated_at=NOW() WHERE operation_id=%s", (result, application['operation_id']))
    database.execute_native("UPDATE operation_receipts SET state='complete',response_json=%s,error_json=NULL,last_error_json=NULL,attempt_token=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE operation_id=%s", (result, application['operation_id']))
    release_fences(database, application['operation_id'])
    enqueue_completion(database, application['queue_task_id'], application['queue_generation'], today)


def record_integrity_target(database, repertoire_id, graph_generation, task):
    database.execute_native("UPDATE prefix_transition_applications SET integrity_task_id=%s,integrity_generation=%s WHERE repertoire_id=%s AND graph_generation=%s AND state IN ('publishing','recovery_required')", (task['id'], task['generation'], repertoire_id, graph_generation))


def record_queue_target(database, repertoire_id, graph_generation, task, queue_day):
    database.execute_native("UPDATE prefix_transition_applications SET queue_task_id=%s,queue_generation=%s,queue_date=%s WHERE repertoire_id=%s AND graph_generation=%s AND state IN ('publishing','recovery_required')", (task['id'], task['generation'], queue_day, repertoire_id, graph_generation))


def lock_linked_receipts(database, task_id, generation):
    """Failure reporting follows the same receipt-before-task lock order."""
    database.execute_native(
        "SELECT receipt.operation_id FROM operation_receipts receipt JOIN prefix_transition_applications application "
        "ON application.operation_id=receipt.operation_id WHERE application.state NOT IN ('complete','rejected') "
        "AND (application.staging_task_id=%s OR (application.integrity_task_id=%s AND application.integrity_generation=%s) "
        "OR (application.queue_task_id=%s AND application.queue_generation<=%s) OR EXISTS(SELECT 1 FROM background_tasks task "
        "WHERE task.id=%s AND task.kind='opening_graph_rebuild' AND task.deduplication_key=application.repertoire_id "
        "AND application.graph_generation=%s)) ORDER BY receipt.operation_id FOR UPDATE OF receipt",
        (task_id, task_id, generation, task_id, generation, task_id, generation),
    ).fetchall()


def record_linked_failure(database, task_id, generation, error):
    applications = database.execute_native(
        "SELECT application.* FROM prefix_transition_applications application WHERE state NOT IN ('complete','rejected') "
        "AND (staging_task_id=%s OR (integrity_task_id=%s AND integrity_generation=%s) "
        "OR (queue_task_id=%s AND queue_generation<=%s) OR EXISTS(SELECT 1 FROM background_tasks task "
        "WHERE task.id=%s AND task.kind='opening_graph_rebuild' AND task.deduplication_key=application.repertoire_id "
        "AND application.graph_generation=%s)) ORDER BY operation_id FOR UPDATE",
        (task_id, task_id, generation, task_id, generation, task_id, generation),
    ).fetchall()
    for application in applications:
        committed = reject_unactivated_application(database, application['operation_id'], error)
        error_json = json.dumps({'message': str(error)[:500], 'status_code': 409,
                                 'detail': {'code': 'publication_recovery_required' if committed else 'staging_failed',
                                            'message': 'Retry this operation to resume its linked publication tasks.' if committed
                                            else 'Staging failed before activation. The source is unchanged; approve a fresh plan.'}})
        database.execute_native(
            "UPDATE operation_receipts SET state=%s,error_json=%s,last_error_json=%s,attempt_token=NULL,"
            "lease_expires_at=NULL,next_retry_at=NULL,updated_at=NOW() WHERE operation_id=%s",
            ('blocked' if committed else 'failed', None if committed else error_json,
             error_json if committed else None, application['operation_id']),
        )


register_command(COMMAND, apply_command, prepare=prepare_application)
