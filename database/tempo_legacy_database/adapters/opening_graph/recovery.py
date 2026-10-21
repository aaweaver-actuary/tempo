"""Existing fixed graph recovery/evidence operations, retaining the original reader/pager."""

from .. import postgres_store


def read_rows(statement, parameters=()):
    with postgres_store.connection(read_only=True) as database:
        return [dict(row) for row in database.execute_native(statement, parameters).fetchall()]


def paged(table, key, predicate='TRUE', parameters=()):
    keys = key if isinstance(key, tuple) else (key,)
    columns = tuple('id' if field == 'id_numeric' else field for field in keys)
    cursor = tuple(0 if field in {'id_numeric', 'queue_entry_id', 'revision'} else '' for field in keys)
    order = ','.join(columns)
    placeholders = ','.join('%s' for _ in columns)
    rows = []
    while True:
        page = read_rows(f'SELECT * FROM {table} WHERE ({predicate}) AND ({order})>({placeholders}) ORDER BY {order} LIMIT 128',
                         (*parameters, *cursor))
        rows.extend(page)
        if len(page) < 128:
            return rows
        cursor = tuple(page[-1][column] for column in columns)


def task_rows(task_id):
    return read_rows('SELECT * FROM background_tasks WHERE id=%s', (task_id,))


def publication_rows(repertoire_id):
    return read_rows('SELECT generation,state FROM opening_graph_publications WHERE repertoire_id=%s', (repertoire_id,))


def newer_staging(repertoire_id, generation):
    return read_rows('SELECT 1 FROM opening_graph_steps WHERE repertoire_id=%s AND generation>=%s LIMIT 1', (repertoire_id, generation))


def scope_rows(repertoire_id):
    return read_rows("SELECT r.scope_source_revision,r.canonical_prefix_revision,"
    "s.initial_depth,s.study_only_mode,a.run_id,a.epoch "
    "FROM repertoires r CROSS JOIN settings s CROSS JOIN frozen_opening_activation a "
    "WHERE r.id=%s AND s.id=1 AND a.id=1", (repertoire_id,))


def staged_rows(repertoire_id, generation):
    return read_rows('SELECT * FROM opening_graph_steps WHERE repertoire_id=%s AND generation=%s '
    'ORDER BY line_id,decision_index', (repertoire_id, generation))


def publication_exists(repertoire_id):
    return read_rows('SELECT 1 FROM opening_graph_publications WHERE repertoire_id=%s', (repertoire_id,))


def record_claim(database, values):
    database.execute_native('INSERT INTO background_task_events(task_id,generation,event,phase,detail,created_at) '
    'VALUES(%s,%s,%s,%s,%s,%s)',
    values)


def followup_predecessor(kind, repertoire_id):
    return read_rows('SELECT id,generation,state,phase,lease_token,lease_expires_at FROM background_tasks WHERE kind=%s AND deduplication_key=%s', (kind, repertoire_id))


def target_identity_rows():
    return read_rows("SELECT (SELECT system_identifier::text FROM pg_control_system()) cluster_id,"
    "(SELECT oid::text FROM pg_database WHERE datname=current_database()) database_oid,"
    "current_database() database,current_schema() schema")


def physical_cards(identifiers):
    return read_rows('SELECT * FROM cards WHERE id=ANY(%s)', (identifiers,))


def frozen_activation():
    return read_rows('SELECT * FROM frozen_opening_activation WHERE id=1')


def settings_evidence():
    return read_rows('SELECT * FROM settings')


def task_evidence(repertoire_id):
    return read_rows('SELECT * FROM background_tasks WHERE deduplication_key=%s ORDER BY id', (repertoire_id,))


def source_evidence(repertoire_id):
    return read_rows('SELECT * FROM repertoire_lines WHERE repertoire_id=%s ORDER BY id', (repertoire_id,))


def graph_evidence(repertoire_id):
    return read_rows('SELECT * FROM opening_graph_steps WHERE repertoire_id=%s ORDER BY generation,line_id,decision_index', (repertoire_id,))


def membership_evidence(repertoire_id):
    return read_rows('SELECT * FROM repertoire_cards WHERE repertoire_id=%s ORDER BY card_id', (repertoire_id,))


def queue_evidence(day):
    return read_rows('SELECT q.*,c.content_type FROM daily_queue q JOIN cards c ON c.id=q.card_id WHERE q.queue_date=%s ORDER BY q.position,q.id', (day,))


def followup_task(database, kind, repertoire_id, *, lock):
    return database.execute_native(
    'SELECT * FROM background_tasks WHERE kind=%s AND deduplication_key=%s' + (' FOR UPDATE' if lock else ''),
    (kind, repertoire_id)).fetchone()


def scope_row(database, repertoire_id, *, lock):
    return database.execute_native(
    "SELECT r.scope_source_revision,r.canonical_prefix_revision,"
    "s.initial_depth,s.study_only_mode,a.run_id,a.epoch "
    "FROM repertoires r CROSS JOIN settings s CROSS JOIN frozen_opening_activation a "
    "WHERE r.id=%s AND s.id=1 AND a.id=1" + (' FOR SHARE OF r,s,a' if lock else ''),
    (repertoire_id,)).fetchone()


def graph_scope_task(database, repertoire_id, *, lock):
    return database.execute_native(
    "SELECT id,generation,state,phase FROM background_tasks WHERE kind='opening_graph_rebuild' "
    "AND deduplication_key=%s" + (' FOR SHARE' if lock else ''), (repertoire_id,)).fetchone()


def publication(database, repertoire_id):
    return database.execute_native(
    'SELECT generation,state FROM opening_graph_publications WHERE repertoire_id=%s',
    (repertoire_id,)).fetchone()


def active_transition(database, repertoire_id):
    return database.execute_native(
    "SELECT 1 FROM prefix_transition_applications WHERE repertoire_id=%s "
    "AND state IN ('publishing','recovery_required') LIMIT 1",
    (repertoire_id,)).fetchone()


def deleted_cards():
    return read_rows('SELECT card_id FROM deleted_cards')


def membership_card_ids(repertoire_id):
    return read_rows('SELECT card_id FROM repertoire_cards WHERE repertoire_id=%s', (repertoire_id,))


def locked_task(database, task_id):
    return database.execute_native('SELECT * FROM background_tasks WHERE id=%s FOR UPDATE', (task_id,)).fetchone()


def claim_task(database, values):
    return database.execute_native(
        "UPDATE background_tasks SET state='leased',lease_token=%s,lease_expires_at=%s,"
        "payload_json=%s,attempt_count=attempt_count+1,completed_at=NULL,updated_at=%s "
        "WHERE id=%s AND generation=%s RETURNING *",
        values,
    ).fetchone()


def followup_rows(kind, repertoire_id):
    return read_rows('SELECT id,generation,payload_json FROM background_tasks WHERE kind=%s AND deduplication_key=%s',
    (kind, repertoire_id))


def integrity_task_rows(repertoire_id):
    return read_rows("SELECT id,generation,payload_json FROM background_tasks WHERE kind='integrity_scan' AND deduplication_key=%s", (repertoire_id,))


def database_identity(database):
    return database.execute_native(
    "SELECT (SELECT system_identifier::text FROM pg_control_system()) cluster_id,"
    "(SELECT oid::text FROM pg_database WHERE datname=current_database()) database_oid,"
    "current_database() database,current_schema() schema").fetchone()


def unstarted_task(database, task_id, generation):
    return database.execute_native('SELECT state,phase,attempt_count,payload_json FROM background_tasks WHERE id=%s AND generation=%s',
    (task_id, generation)).fetchone()


def prior_claim(database, task_id, generation):
    return database.execute_native("SELECT 1 FROM background_task_events WHERE task_id=%s AND generation=%s AND event='maintenance_recovery_claim' LIMIT 1",
    (task_id, generation)).fetchone()


def integrity_publication(database, repertoire_id):
    return database.execute_native(
    'SELECT status,scan_status,scan_generation,checked_at FROM repertoire_integrity_state WHERE repertoire_id=%s',
    (repertoire_id,)).fetchone()


def segmentation_publication(database, repertoire_id):
    return database.execute_native(
    'SELECT state,graph_generation,run_id FROM opening_segmentation_state WHERE repertoire_id=%s',
    (repertoire_id,)).fetchone()


def repertoire_card_evidence(repertoire_id):
    return paged('cards', 'id', 'repertoire_id=%s', (repertoire_id,))


def review_evidence():
    return paged('reviews', 'id_numeric')


def issued_origin_evidence():
    return paged('queue_attempt_origins', ('queue_entry_id', 'card_id', 'revision'), 'issued_as_head=1')


def frozen_rank_evidence(run_id):
    return paged('frozen_opening_ranks', 'card_id', 'run_id=%s', (run_id,))


def all_card_evidence():
    return paged('cards', 'id')


def authoritative_connection():
    return postgres_store.connection(read_only=True, authoritative=True, background=True)


def maintenance_connection():
    return postgres_store.connection(background=True)


def set_selected_handoff(database, handoff):
    database._selected_rebuild_handoff = handoff
