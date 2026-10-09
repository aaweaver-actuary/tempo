"""Coalesce unchanged source intent with a restart-safe bounded quiet window."""
from datetime import datetime, timedelta, timezone
import json


def _now():
    return datetime.now(timezone.utc)


def input_version(database, kind, repertoire_id):
    from .postgres_priority import _priority_source_version
    from .introduction_priorities import SCORING_VERSION as PRIORITY_SCORING_VERSION
    from .repertoire_opportunities import SCORING_VERSION as OPPORTUNITY_SCORING_VERSION
    # Serialize even the first request, when no coalescing row exists yet.
    source_version = _priority_source_version(database, repertoire_id, lock=hasattr(database, 'execute_native'))
    if source_version is None:
        raise KeyError('Repertoire source is unavailable; restore it before refreshing')
    scope = database.execute('SELECT scope_source_revision,canonical_prefix_revision,canonical_prefix_preview_id '
                             'FROM repertoires WHERE id=?', (repertoire_id,)).fetchone()
    graph = database.execute('SELECT generation,state FROM opening_graph_publications WHERE repertoire_id=?',
                             (repertoire_id,)).fetchone()
    priority = database.execute('SELECT generation FROM repertoire_priority_publications WHERE repertoire_id=?',
                                (repertoire_id,)).fetchone() if kind == 'repertoire_opportunity' else None
    return json.dumps([PRIORITY_SCORING_VERSION, OPPORTUNITY_SCORING_VERSION, source_version, list(scope), list(graph) if graph else None,
                       priority[0] if priority else 0], separators=(',', ':'))


def request_refresh(database, kind, repertoire_id, *, quiet_seconds=5):
    """Return the remaining delay, or None for unchanged intent.

    Version counters and publication identities are indexed bounded reads.
    Historical failures stay failed unless inputs change or an explicit retry
    control is used. Claim and window-reset share the lease transaction.
    """
    now = _now()
    source_version = input_version(database, kind, repertoire_id)
    suffix = ' FOR UPDATE' if hasattr(database, 'execute_native') else ''
    current = database.execute('SELECT input_version,pending_since FROM analysis_refresh_requests '
                               'WHERE kind=? AND repertoire_id=?' + suffix, (kind, repertoire_id)).fetchone()
    if current and current['input_version'] == source_version:
        return None
    pending_since = datetime.fromisoformat(current['pending_since']) if current and current['pending_since'] else now
    eligible_at = min(now + timedelta(seconds=max(0, quiet_seconds)), pending_since + timedelta(seconds=60))
    database.execute('INSERT INTO analysis_refresh_requests(kind,repertoire_id,input_version,pending_since,requested_at) '
                     'VALUES(?,?,?,?,?) ON CONFLICT(kind,repertoire_id) DO UPDATE SET '
                     'input_version=excluded.input_version,pending_since=excluded.pending_since,requested_at=excluded.requested_at',
                     (kind, repertoire_id, source_version, pending_since.isoformat(), now.isoformat()))
    return max(0.0, (eligible_at - now).total_seconds())
