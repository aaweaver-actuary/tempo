"""Persist narrow refresh intent without replacing useful unchanged work."""
from datetime import datetime, timedelta, timezone
import json


def _now():
    return datetime.now(timezone.utc)


def input_version(database, kind, repertoire_id):
    from .postgres_priority import _priority_source_version
    from .introduction_priorities import SCORING_VERSION as PRIORITY_SCORING_VERSION
    from .repertoire_opportunities import SCORING_VERSION as OPPORTUNITY_SCORING_VERSION

    # Existing source locks serialize even the first request. Do not traverse
    # evidence or perform calculation inside a request transaction.
    source_version = _priority_source_version(
        database, repertoire_id, lock=hasattr(database, 'execute_native'),
    )
    if source_version is None:
        raise KeyError('Repertoire source is unavailable; restore it before refreshing')
    scope = database.execute(
        'SELECT scope_source_revision,canonical_prefix_revision,canonical_prefix_preview_id '
        'FROM repertoires WHERE id=?', (repertoire_id,),
    ).fetchone()
    graph = database.execute(
        'SELECT generation,state FROM opening_graph_publications WHERE repertoire_id=?',
        (repertoire_id,),
    ).fetchone()
    priority = database.execute(
        'SELECT generation FROM repertoire_priority_publications WHERE repertoire_id=?',
        (repertoire_id,),
    ).fetchone() if kind == 'repertoire_opportunity' else None
    scoring_version = (PRIORITY_SCORING_VERSION if kind == 'repertoire_priority'
                       else OPPORTUNITY_SCORING_VERSION)
    return json.dumps([scoring_version, source_version, list(scope),
                       list(graph) if graph else None, priority[0] if priority else 0,
                       _now().date().isoformat()], separators=(',', ':'))


def request_refresh(database, kind, repertoire_id, *, quiet_seconds=5):
    """Return eligibility delay, or None when work is retained/satisfied.

    Lock order is source, priority job (when applicable), task, intent. Claims
    lock task then intent, so their window reset cannot deadlock with enqueue.
    Terminal failures retain explicit retry semantics. A missing task is not
    proof of publication: only accepted completion marks the published identity.
    """
    from tempo_legacy_database.adapters import postgres_store
    from .queue_refresh_wakeup import mark_queue_refresh_requested

    now = _now()
    requested_input_version = input_version(database, kind, repertoire_id)
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
    if current_intent and current_intent['input_version'] == requested_input_version:
        if kind == 'repertoire_priority':
            useful_work_exists = bool(priority_job and priority_job['status'] != 'complete'
                                      and (existing_task or not postgres_store.configured()))
        else:
            useful_work_exists = bool(existing_task and existing_task['state'] != 'complete')
        if useful_work_exists or current_intent['published_input_version'] == requested_input_version:
            if useful_work_exists and (existing_task is None or existing_task['state'] in {'queued', 'leased', 'retrying'}):
                mark_queue_refresh_requested()
            return None
    pending_since = (datetime.fromisoformat(current_intent['pending_since'])
                     if current_intent and current_intent['pending_since'] else now)
    eligible_at = min(now + timedelta(seconds=max(0, quiet_seconds)),
                      pending_since + timedelta(seconds=60))
    database.execute(
        'INSERT INTO analysis_refresh_requests(kind,repertoire_id,input_version,pending_since,requested_at) '
        'VALUES(?,?,?,?,?) ON CONFLICT(kind,repertoire_id) DO UPDATE SET '
        'input_version=excluded.input_version,pending_since=excluded.pending_since,requested_at=excluded.requested_at',
        (kind, repertoire_id, requested_input_version, pending_since.isoformat(), now.isoformat()),
    )
    mark_queue_refresh_requested()
    return max(0.0, (eligible_at - now).total_seconds())


def record_publication(database, kind, repertoire_id):
    """Called only after a generation-fenced accepted publication, in its commit."""
    database.execute(
        'UPDATE analysis_refresh_requests SET published_input_version=input_version,pending_since=NULL '
        'WHERE kind=? AND repertoire_id=?', (kind, repertoire_id),
    )
