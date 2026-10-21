"""Fixed prefix application persistence; caller retains every transaction and policy decision."""
from ..snapshot_reads import SnapshotRead, snapshot_rows


_INTEGRITY_QUERY = ("SELECT integrity.status,integrity.scan_status,integrity.scan_generation,task.id,task.generation,task.state,"
            "task.payload_json FROM repertoire_integrity_state integrity LEFT JOIN background_tasks task "
            "ON task.kind='integrity_scan' AND task.deduplication_key=integrity.repertoire_id WHERE integrity.repertoire_id=%s")

_STAGED_QUERY = ('SELECT line_id,decision_index,segment_kind,first_decision_index,last_decision_index,'
                 'decision_fen_keys_json,card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color '
                 'FROM opening_graph_steps WHERE repertoire_id=%s AND generation=%s')


def own_application(database, operation_id):
    database.execute_native("SELECT set_config('tempo.prefix_transition_operation',%s,true)", (operation_id,))


def release_fences(database, operation_id):
    database.execute_native('DELETE FROM prefix_transition_card_fences WHERE operation_id=%s', (operation_id,))
    database.execute_native('DELETE FROM prefix_transition_repertoire_fences WHERE operation_id=%s', (operation_id,))


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


def receipt_state(database, operation_id):
    return database.execute_native(
        'SELECT state FROM operation_receipts WHERE operation_id=%s',
        (operation_id,),
    ).fetchone()


def prior_application(database, operation_id):
    return database.execute_native(
        'SELECT state,graph_generation FROM prefix_transition_applications WHERE operation_id=%s',
        (operation_id,),
    ).fetchone()


def lock_pending_receipts(database, pending_ids):
    return database.execute_native(
        'SELECT operation_id FROM operation_receipts WHERE operation_id=ANY(%s) ORDER BY operation_id FOR UPDATE NOWAIT',
        (pending_ids,),
    ).fetchall()


def lock_source_tasks(database, repertoire_ids):
    return database.execute_native(
        "SELECT id FROM background_tasks WHERE kind IN ('opening_graph_rebuild','integrity_scan') AND deduplication_key=ANY(%s) ORDER BY id FOR UPDATE NOWAIT",
        (repertoire_ids,),
    ).fetchall()


def try_reservation_barrier(database):
    return database.execute_native(
        "SELECT pg_try_advisory_xact_lock(hashtextextended('tempo:prefix-transition:reservations',0))"
    ).fetchone()


def occupied_fences(database, card_ids, card_operation_id, repertoire_ids, repertoire_operation_id):
    return database.execute_native(
        'SELECT operation_id FROM prefix_transition_card_fences WHERE card_id=ANY(%s) AND operation_id<>%s '
        'UNION SELECT operation_id FROM prefix_transition_repertoire_fences WHERE repertoire_id=ANY(%s) AND operation_id<>%s',
        (card_ids, card_operation_id, repertoire_ids, repertoire_operation_id),
    ).fetchone()


def lock_repertoires(database, repertoire_ids):
    return database.execute_native(
        'SELECT id FROM repertoires WHERE id=ANY(%s) ORDER BY id FOR UPDATE NOWAIT',
        (repertoire_ids,),
    ).fetchall()


def share_integrity_rows(database, repertoire_ids):
    return database.execute_native(
        'SELECT repertoire_id FROM repertoire_integrity_state WHERE repertoire_id=ANY(%s) ORDER BY repertoire_id FOR SHARE NOWAIT',
        (repertoire_ids,),
    ).fetchall()


def lock_lines(database, repertoire_id):
    return database.execute_native(
        'SELECT id FROM repertoire_lines WHERE repertoire_id=%s ORDER BY id FOR UPDATE NOWAIT',
        (repertoire_id,),
    ).fetchall()


def lock_line_depths(database, repertoire_id):
    return database.execute_native(
        'SELECT depth.line_id FROM repertoire_line_training_depths depth JOIN repertoire_lines line ON line.id=depth.line_id WHERE line.repertoire_id=%s ORDER BY depth.line_id FOR UPDATE OF depth NOWAIT',
        (repertoire_id,),
    ).fetchall()


def lock_cards(database, card_ids):
    return database.execute_native(
        'SELECT id FROM cards WHERE id=ANY(%s) ORDER BY id FOR UPDATE NOWAIT',
        (card_ids,),
    ).fetchall()


def lock_memberships(database, card_ids, repertoire_id):
    return database.execute_native(
        'SELECT card_id FROM repertoire_cards WHERE card_id=ANY(%s) OR repertoire_id=%s ORDER BY card_id,repertoire_id FOR UPDATE NOWAIT',
        (card_ids, repertoire_id),
    ).fetchall()


def lock_attempt_rows(database, snapshot):
    for table, identity in (('daily_queue', 'id'), ('opening_evidence_attempts', 'attempt_id'), ('study_attempts', 'id')):
        database.execute_native(f'SELECT {identity} FROM {table} WHERE card_id=ANY(%s) ORDER BY {identity} FOR UPDATE NOWAIT', (list(snapshot.lookup_card_ids),)).fetchall()


def locked_application_state(database, operation_id):
    return database.execute_native(
        'SELECT state FROM prefix_transition_applications WHERE operation_id=%s FOR UPDATE',
        (operation_id,),
    ).fetchone()


def record_rejection(database, state, error, operation_id):
    database.execute_native(
        "UPDATE prefix_transition_applications SET state=%s,last_error=%s,updated_at=NOW() WHERE operation_id=%s",
        (state, error, operation_id),
    )


def locked_application(database, operation_id):
    return database.execute_native(
        'SELECT * FROM prefix_transition_applications WHERE operation_id=%s FOR UPDATE',
        (operation_id,),
    ).fetchone()


def next_graph_generation(database, repertoire_id):
    return database.execute_native(
        'SELECT COALESCE(MAX(generation),0)+1 FROM opening_graph_steps WHERE repertoire_id=%s',
        (repertoire_id,),
    ).fetchone()


def insert_application(database, operation_id, repertoire_id, plan_id, plan_json, generation):
    database.execute_native(
        "INSERT INTO prefix_transition_applications(operation_id,repertoire_id,plan_id,plan_json,graph_generation,state) VALUES(%s,%s,%s,%s,%s,'staging')",
        (operation_id, repertoire_id, plan_id, plan_json, generation),
    )


def insert_card_fences(database, operation_id, card_ids):
    database.execute_native(
        'INSERT INTO prefix_transition_card_fences(card_id,operation_id) SELECT card_id,%s FROM UNNEST(%s::text[]) AS identities(card_id)',
        (operation_id, card_ids),
    )


def insert_repertoire_fences(database, operation_id, repertoire_ids):
    database.execute_native(
        'INSERT INTO prefix_transition_repertoire_fences(repertoire_id,operation_id) SELECT repertoire_id,%s FROM UNNEST(%s::text[]) AS identities(repertoire_id)',
        (operation_id, repertoire_ids),
    )


def record_staging_task(database, task_id, operation_id):
    database.execute_native(
        'UPDATE prefix_transition_applications SET staging_task_id=%s WHERE operation_id=%s',
        (task_id, operation_id),
    )


def write_line_depths(database, line_ids, decision_counts):
    database.execute_native(
        'INSERT INTO repertoire_line_training_depths(line_id,learner_decision_count) '
        'SELECT line_id,learner_decision_count FROM UNNEST(%s::text[],%s::bigint[]) AS changes(line_id,learner_decision_count) '
        'ON CONFLICT(line_id) DO UPDATE SET learner_decision_count=excluded.learner_decision_count',
        (line_ids, decision_counts),
    )


def add_memberships(database, repertoire_ids, card_ids):
    database.execute_native(
        'INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) '
        'SELECT repertoire_id,card_id,0 FROM UNNEST(%s::text[],%s::text[]) AS memberships(repertoire_id,card_id)',
        (repertoire_ids, card_ids),
    )


def write_card_kinds(database, card_ids, kinds):
    database.execute_native(
        'UPDATE cards SET kind=classification.kind FROM UNNEST(%s::text[],%s::text[]) AS classification(card_id,kind) '
        'WHERE cards.id=classification.card_id AND cards.canonical_route_source=0',
        (card_ids, kinds),
    )


def retire_attempts(database, kind, operation_id, retired):
    table, identity = ('opening_evidence_attempts', 'attempt_id') if kind == 'opening_attempt' else ('study_attempts', 'id')
    database.execute_native(
        f'UPDATE {table} SET retired_operation_id=%s WHERE {identity}=ANY(%s)',
        (operation_id, retired),
    )


def retire_pending_commands(database, error, operation_ids):
    database.execute_native(
        "UPDATE operation_receipts SET state='failed',error_json=%s,attempt_token=NULL,lease_expires_at=NULL,next_retry_at=NULL,updated_at=NOW() WHERE operation_id=ANY(%s) AND state NOT IN ('complete','failed')",
        (error, operation_ids),
    )


def finalize_graph_task(database, task_id, generation):
    database.execute_native(
        "UPDATE background_tasks SET phase='finalize' WHERE id=%s AND generation=%s",
        (task_id, generation),
    )


def mark_publishing(database, task_id, operation_id):
    database.execute_native(
        "UPDATE prefix_transition_applications SET state='publishing',graph_task_id=%s,updated_at=NOW() WHERE operation_id=%s",
        (task_id, operation_id),
    )


def application(database, operation_id):
    return database.execute_native(
        'SELECT * FROM prefix_transition_applications WHERE operation_id=%s',
        (operation_id,),
    ).fetchone()


def lock_operation_receipt(database, operation_id):
    return database.execute_native(
        'SELECT operation_id FROM operation_receipts WHERE operation_id=%s FOR UPDATE',
        (operation_id,),
    ).fetchone()


def mark_staged(database, operation_id):
    database.execute_native(
        "UPDATE prefix_transition_applications SET state='staged',updated_at=NOW() WHERE operation_id=%s",
        (operation_id,),
    )


def requeue_pending_operation(database, operation_id):
    database.execute_native(
        "UPDATE operation_receipts SET state='queued',attempt_token=NULL,lease_expires_at=NULL,next_retry_at=NULL,updated_at=NOW()-INTERVAL '31 seconds' WHERE operation_id=%s AND state='pending'",
        (operation_id,),
    )


def requeue_failed_task(database, kind, task_id, optional_task_id, repertoire_id):
    database.execute_native(
        "UPDATE background_tasks SET state='queued',attempt_count=0,lease_token=NULL,lease_expires_at=NULL,last_error=NULL,completed_at=NULL,next_attempt_at=NOW()::text WHERE state='failed' AND kind=%s AND (id=%s OR (%s::text IS NULL AND deduplication_key=%s))",
        (kind, task_id, optional_task_id, repertoire_id),
    )


def resume_application(database, operation_id):
    database.execute_native(
        "UPDATE prefix_transition_applications SET state='publishing',last_error=NULL,updated_at=NOW() WHERE operation_id=%s",
        (operation_id,),
    )


def completed_queue(database, task_id, generation):
    return database.execute_native(
        "SELECT 1 FROM background_tasks WHERE id=%s AND generation>=%s AND state='complete'",
        (task_id, generation),
    ).fetchone()


def completion_target(database, task_id, generation, queue_day):
    return database.execute_native(
        "SELECT operation_id FROM prefix_transition_applications WHERE state IN ('publishing','recovery_required') AND queue_task_id=%s AND queue_generation<=%s AND queue_date=%s ORDER BY operation_id LIMIT 1",
        (task_id, generation, queue_day),
    ).fetchone()


def publication_ready(database, integrity_task_id, queue_task_id, queue_day, repertoire_id, graph_generation, scan_generation, integrity_generation, queue_generation, payload_queue_day):
    return database.execute_native(
        "SELECT queue_task.generation FROM opening_graph_publications graph JOIN background_tasks graph_task ON graph_task.kind='opening_graph_rebuild' AND graph_task.deduplication_key=graph.repertoire_id JOIN repertoire_integrity_state integrity ON integrity.repertoire_id=graph.repertoire_id JOIN background_tasks integrity_task ON integrity_task.id=%s JOIN background_tasks queue_task ON queue_task.id=%s JOIN queue_projections queue ON queue.queue_date=%s WHERE graph.repertoire_id=%s AND graph.generation=%s AND graph.state='ready' AND graph_task.generation=graph.generation AND graph_task.state='complete' AND integrity.status='clean' AND integrity.scan_status='idle' AND integrity.scan_generation=%s AND integrity_task.generation=%s AND integrity_task.state='complete' AND queue_task.generation>=%s AND queue_task.state='complete' AND queue_task.payload_json::jsonb->>'queue_date'=%s AND queue.state='ready' AND queue.refresh_pending=0",
        (integrity_task_id, queue_task_id, queue_day, repertoire_id, graph_generation, scan_generation, integrity_generation, queue_generation, payload_queue_day),
    ).fetchone()


def complete_application(database, result, operation_id):
    database.execute_native(
        "UPDATE prefix_transition_applications SET state='complete',result_json=%s,last_error=NULL,updated_at=NOW() WHERE operation_id=%s",
        (result, operation_id),
    )


def complete_receipt(database, result, operation_id):
    database.execute_native(
        "UPDATE operation_receipts SET state='complete',response_json=%s,error_json=NULL,last_error_json=NULL,attempt_token=NULL,lease_expires_at=NULL,updated_at=NOW() WHERE operation_id=%s",
        (result, operation_id),
    )


def linked_applications(database, staging_task_id, integrity_task_id, integrity_generation, queue_task_id, queue_generation, graph_task_id, graph_generation):
    return database.execute_native(
        "SELECT application.* FROM prefix_transition_applications application WHERE state NOT IN ('complete','rejected') "
        "AND (staging_task_id=%s OR (integrity_task_id=%s AND integrity_generation=%s) "
        "OR (queue_task_id=%s AND queue_generation<=%s) OR EXISTS(SELECT 1 FROM background_tasks task "
        "WHERE task.id=%s AND task.kind='opening_graph_rebuild' AND task.deduplication_key=application.repertoire_id "
        "AND application.graph_generation=%s)) ORDER BY operation_id FOR UPDATE",
        (staging_task_id, integrity_task_id, integrity_generation, queue_task_id, queue_generation, graph_task_id, graph_generation),
    ).fetchall()


def record_receipt_failure(database, state, error, last_error, operation_id):
    database.execute_native(
        "UPDATE operation_receipts SET state=%s,error_json=%s,last_error_json=%s,attempt_token=NULL,"
            "lease_expires_at=NULL,next_retry_at=NULL,updated_at=NOW() WHERE operation_id=%s",
        (state, error, last_error, operation_id),
    )


def integrity_rows(database, repertoire_id):
    return snapshot_rows(database, _INTEGRITY_QUERY, (repertoire_id,))


def integrity_evidence(repertoire_id, rows):
    return SnapshotRead(_INTEGRITY_QUERY, (repertoire_id,), rows)


def staged_rows(database, parameters):
    return snapshot_rows(database, _STAGED_QUERY, parameters)


def staged_evidence(parameters, rows):
    return SnapshotRead(_STAGED_QUERY, parameters, rows)


def snapshot_evidence(database, captured):
    return snapshot_rows(database, captured.query, captured.parameters, uses_sha256_evidence=captured.uses_sha256_evidence)
