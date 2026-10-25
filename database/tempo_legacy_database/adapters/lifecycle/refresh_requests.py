"""Refresh source/status facts and accepted intent writes in the caller transaction."""


def input_source_rows(database, kind: str, repertoire_id: str):
    scope = database.execute(
        'SELECT scope_source_revision,canonical_prefix_revision,canonical_prefix_preview_id '
        'FROM repertoires WHERE id=?', (repertoire_id,),
    ).fetchone()
    graph = database.execute(
        'SELECT generation,state FROM opening_graph_publications WHERE repertoire_id=?',
        (repertoire_id,),
    ).fetchone()
    return scope, graph, None


def refresh_status_rows(database, kind: str, repertoire_id: str):
    suffix = ' FOR UPDATE' if hasattr(database, 'execute_native') else ''
    priority_job = None
    if kind == 'repertoire_priority':
        priority_job = database.execute(
            'SELECT status FROM repertoire_priority_jobs WHERE repertoire_id=?' + suffix,
            (repertoire_id,),
        ).fetchone()
    existing_task = database.execute(
        'SELECT state FROM background_tasks WHERE kind=? AND deduplication_key=?' + suffix,
        (kind, repertoire_id),
    ).fetchone()
    current_intent = database.execute(
        'SELECT input_version,published_input_version,pending_since FROM analysis_refresh_requests '
        'WHERE kind=? AND repertoire_id=?' + suffix, (kind, repertoire_id),
    ).fetchone()
    return priority_job, existing_task, current_intent


def save_refresh_intent(database, kind: str, repertoire_id: str, requested_input_version: str, pending_since, now) -> None:
    database.execute(
        'INSERT INTO analysis_refresh_requests(kind,repertoire_id,input_version,pending_since,requested_at) '
        'VALUES(?,?,?,?,?) ON CONFLICT(kind,repertoire_id) DO UPDATE SET '
        'input_version=excluded.input_version,pending_since=excluded.pending_since,requested_at=excluded.requested_at',
        (kind, repertoire_id, requested_input_version, pending_since.isoformat(), now.isoformat()),
    )


def record_publication(database, kind, repertoire_id):
    """Called only after a generation-fenced accepted publication, in its commit."""
    database.execute(
        'UPDATE analysis_refresh_requests SET published_input_version=input_version,pending_since=NULL '
        'WHERE kind=? AND repertoire_id=?', (kind, repertoire_id),
    )


def daily_queue_projection(database, queue_date):
    return database.execute(
        "SELECT state,refresh_pending FROM queue_projections WHERE queue_date=?",
        (queue_date,),
    ).fetchone()
