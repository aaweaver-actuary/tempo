"""Bounded repertoire-only SELECT over the existing borrowed PostgreSQL port.

One statement owns one MVCC view even inside a READ COMMITTED writer transaction.
No connection, transaction settings, locks, commits or schema are owned here.
"""
from __future__ import annotations

import json

MAX_SOURCE_ROWS = 10000
MAX_ROW_BYTES = 65536

# Raw text is bounded before JSON aggregation. Six bytes per source byte covers
# JSON escaping; 512 bytes per row covers fixed keys/scalars/array punctuation.
# A row-count sentinel prevents a bounded prefix from claiming completeness.
_SOURCE_CTES = """
WITH limits AS (
    SELECT %s::text repertoire_id, %s::int source_limit,
           %s::bigint row_byte_limit
), repertoire AS MATERIALIZED (
    SELECT r.id,r.name,r.scope_source_revision,r.canonical_prefix_revision,
           r.canonical_prefix_moves_json,p.generation graph_generation,p.state graph_state,
           (SELECT workspace_id FROM legacy_learning_identity WHERE id=1) workspace_id
    FROM repertoires r CROSS JOIN limits l
    LEFT JOIN opening_graph_publications p ON p.repertoire_id=r.id
    WHERE r.id=l.repertoire_id
), lines AS MATERIALIZED (
    SELECT s.id,s.repertoire_id,s.name,s.trained_color,s.start_fen,s.moves_json
    FROM repertoire_lines s CROSS JOIN limits l WHERE s.repertoire_id=l.repertoire_id
    ORDER BY s.id LIMIT (SELECT source_limit+1 FROM limits)
), cards AS MATERIALIZED (
    SELECT c.id,c.repertoire_id,c.revision,c.start_fen,c.moves_json,c.trained_color,
           c.content_type,c.archived,c.pending_validation,c.superseded_by,
           c.canonical_route_source,c.introduced_at,
           EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks b WHERE b.card_id=c.id) integrity_blocked,
           EXISTS(SELECT 1 FROM deleted_cards d WHERE d.card_id=c.id) deleted
    FROM cards c CROSS JOIN limits l
    WHERE c.content_type='opening' AND (c.repertoire_id=l.repertoire_id OR EXISTS(
        SELECT 1 FROM repertoire_cards rc WHERE rc.repertoire_id=l.repertoire_id AND rc.card_id=c.id))
    ORDER BY c.id LIMIT (SELECT source_limit+1 FROM limits)
), memberships AS MATERIALIZED (
    SELECT rc.repertoire_id,rc.card_id,rc.canonical_route_source
    FROM repertoire_cards rc JOIN cards c ON c.id=rc.card_id CROSS JOIN limits l
    WHERE rc.repertoire_id=l.repertoire_id ORDER BY rc.card_id
    LIMIT (SELECT source_limit+1 FROM limits)
), graphs AS MATERIALIZED (
    SELECT g.repertoire_id,g.generation,g.line_id,g.decision_index,g.card_id,
           g.starting_fen,g.moves_json,g.trained_color
    FROM opening_graph_steps g JOIN repertoire r
      ON r.id=g.repertoire_id AND r.graph_generation=g.generation AND r.graph_state='ready'
    ORDER BY g.line_id,g.decision_index
), sizes AS (
    SELECT 'repertoire' kind,octet_length(concat_ws('',id,name,scope_source_revision,
        canonical_prefix_revision,canonical_prefix_moves_json,graph_generation,graph_state,workspace_id)) bytes
    FROM repertoire
    UNION ALL SELECT 'lines',octet_length(concat_ws('',id,repertoire_id,name,trained_color,start_fen,moves_json)) FROM lines
    UNION ALL SELECT 'cards',octet_length(concat_ws('',id,repertoire_id,revision,start_fen,moves_json,
        trained_color,content_type,archived,pending_validation,superseded_by,canonical_route_source,
        introduced_at,integrity_blocked,deleted)) FROM cards
    UNION ALL SELECT 'memberships',octet_length(concat_ws('',repertoire_id,card_id,canonical_route_source)) FROM memberships
    UNION ALL SELECT 'graphs',octet_length(concat_ws('',repertoire_id,generation,line_id,decision_index,
        card_id,starting_fen,moves_json,trained_color)) FROM graphs
), budget AS (
    SELECT count(*) FILTER (WHERE kind='repertoire') repertoire_count,
           count(*) FILTER (WHERE kind='lines') line_count,
           count(*) FILTER (WHERE kind='cards') card_count,
           count(*) FILTER (WHERE kind='memberships') membership_count,
           count(*) FILTER (WHERE kind='graphs') graph_count,
           coalesce(sum(bytes*6+512),0)+1024 estimated_bytes,
           coalesce(max(bytes),0) largest_row
    FROM sizes
), allowed AS (
    SELECT b.*,b.line_count<=l.source_limit AND b.card_count<=l.source_limit
        AND b.membership_count<=l.source_limit
        AND b.largest_row<=l.row_byte_limit bounded
    FROM budget b CROSS JOIN limits l
)
"""

STATEMENT = _SOURCE_CTES + """SELECT jsonb_build_object(
    'exists',repertoire_count=1,'bounded',bounded,'estimated_bytes',estimated_bytes,
    'line_count',line_count,'card_count',card_count,'membership_count',membership_count,
    'graph_count',graph_count,'largest_row',largest_row,
    'facts',CASE WHEN bounded AND repertoire_count=1 THEN jsonb_build_object(
        'repertoire',(SELECT to_jsonb(r) FROM repertoire r),
        'lines',coalesce((SELECT jsonb_agg(to_jsonb(s) ORDER BY id) FROM lines s),'[]'::jsonb),
        'cards',coalesce((SELECT jsonb_agg(to_jsonb(c) ORDER BY id) FROM cards c),'[]'::jsonb),
        'memberships',coalesce((SELECT jsonb_agg(to_jsonb(m) ORDER BY card_id) FROM memberships m),'[]'::jsonb),
        'graphs',coalesce((SELECT jsonb_agg(to_jsonb(g) ORDER BY line_id,decision_index) FROM graphs g),'[]'::jsonb)
    ) ELSE NULL END
) snapshot FROM allowed
"""


def _refuse_unbounded_source(document):
    from app.repertoire import RepertoireSnapshotBoundExceeded
    counts = (("line_count", "source_rows", MAX_SOURCE_ROWS),
              ("card_count", "card_rows", MAX_SOURCE_ROWS),
              ("membership_count", "membership_rows", MAX_SOURCE_ROWS))
    fields = [key for key, _, _ in counts] + ["estimated_bytes", "largest_row"]
    if (any(type(document.get(key)) is not int or document[key] < 0 for key in fields)
            or any(document[key] > limit + 1 for key, _, limit in counts)):
        raise ValueError("invalid_repertoire_snapshot_bound_metadata")
    complete = all(document[key] <= limit for key, _, limit in counts)
    violations = [dict(dimension=dimension, observed=document[key], limit=limit, observation="lower_bound")
                  for key, dimension, limit in counts if document[key] > limit]
    for key, dimension, limit, observation in (
            ("largest_row", "row_bytes", MAX_ROW_BYTES, "exact" if complete else "lower_bound"),):
        if document[key] > limit:
            violations.append(dict(dimension=dimension, observed=document[key], limit=limit, observation=observation))
    if not violations:
        raise ValueError("invalid_repertoire_snapshot_bound_metadata")
    raise RepertoireSnapshotBoundExceeded(stage="read", sample_complete=complete, violations=violations)


def read_repertoire(database, repertoire_id: str) -> dict:
    """One parameter-bound read; overflow fails, never a silently partial view."""
    if not isinstance(repertoire_id, str) or not repertoire_id or len(repertoire_id) > 200:
        raise ValueError("invalid_repertoire_scope")
    execute = getattr(database, "execute_native", None) or database.execute
    row = execute(STATEMENT, (repertoire_id, MAX_SOURCE_ROWS, MAX_ROW_BYTES)).fetchone()
    try:
        document = row["snapshot"]
    except TypeError:
        document = row[0]
    if isinstance(document, str):
        document = json.loads(document)
    if not document["exists"]:
        raise ValueError("repertoire_not_found")
    if not document["bounded"]:
        _refuse_unbounded_source(document)
    facts = document["facts"]
    return facts


def current_snapshot(database, repertoire_id: str):
    """Concrete RepertoireSnapshot reader for composition/current change stores."""
    from app.repertoire.snapshot import project_repertoire
    return project_repertoire(read_repertoire(database, repertoire_id))


def current_published_snapshot(database, repertoire_id: str):
    """Feedback read with explicit publication version; source audits stay separate.

    A missing/non-ready generation fails instead of borrowing an authored counter.
    Current Study capture/fencing remains mandatory before any learning effect.
    """
    from app.repertoire.snapshot import project_published_repertoire
    return project_published_repertoire(read_repertoire(database, repertoire_id))


# These game-bound reads preserve the original full-source count/row-byte
# refusals. Only physical facts outside the proven candidate route set are
# omitted; complete authored lines remain a separate mandatory owner input.
_HEADER_FIELDS = ('id', 'scope_source_revision', 'canonical_prefix_revision',
                  'canonical_prefix_moves_json', 'graph_generation', 'graph_state', 'workspace_id')

_AUTHORED_STATEMENT = _SOURCE_CTES + """SELECT jsonb_build_object(
    'exists',repertoire_count=1,'bounded',bounded,'estimated_bytes',estimated_bytes,
    'line_count',line_count,'card_count',card_count,'membership_count',membership_count,
    'graph_count',graph_count,'largest_row',largest_row,
    'facts',CASE WHEN bounded AND repertoire_count=1 THEN jsonb_build_object(
        'repertoire',(SELECT to_jsonb(r) FROM repertoire r),
        'lines',coalesce((SELECT jsonb_agg(to_jsonb(s) ORDER BY id) FROM lines s),'[]'::jsonb),
        'card_count',card_count,'membership_count',membership_count,'graph_count',graph_count
    ) ELSE NULL END
) snapshot FROM allowed
"""

_CANDIDATE_STATEMENT = _SOURCE_CTES + """, requested AS (
    SELECT unnest(%s::text[]) line_id
), candidate_graphs AS MATERIALIZED (
    SELECT g.* FROM graphs g JOIN requested wanted ON wanted.line_id=g.line_id
    ORDER BY g.line_id,g.decision_index
), candidate_cards AS MATERIALIZED (
    SELECT c.* FROM cards c WHERE EXISTS(
        SELECT 1 FROM candidate_graphs g WHERE g.card_id=c.id)
), candidate_memberships AS MATERIALIZED (
    SELECT m.* FROM memberships m WHERE EXISTS(
        SELECT 1 FROM candidate_graphs g WHERE g.card_id=m.card_id)
), candidate_budget AS (
    SELECT count(*) candidate_graph_count,
        (SELECT count(*) FROM requested wanted JOIN lines source ON source.id=wanted.line_id) requested_line_count
    FROM candidate_graphs
)
SELECT jsonb_build_object(
    'exists',repertoire_count=1,'bounded',bounded,'estimated_bytes',estimated_bytes,
    'line_count',line_count,'card_count',card_count,'membership_count',membership_count,
    'graph_count',graph_count,'largest_row',largest_row,
    'candidate_graph_count',candidate_graph_count,'requested_line_count',requested_line_count,
    'facts',CASE WHEN bounded AND repertoire_count=1 THEN jsonb_build_object(
        'repertoire',(SELECT to_jsonb(r) FROM repertoire r),
        'route_ids',coalesce((SELECT jsonb_agg(line_id ORDER BY line_id) FROM requested),'[]'::jsonb),
        'cards',coalesce((SELECT jsonb_agg(to_jsonb(c) ORDER BY id) FROM candidate_cards c),'[]'::jsonb),
        'memberships',coalesce((SELECT jsonb_agg(to_jsonb(m) ORDER BY card_id) FROM candidate_memberships m),'[]'::jsonb),
        'graphs',coalesce((SELECT jsonb_agg(to_jsonb(g) ORDER BY line_id,decision_index) FROM candidate_graphs g),'[]'::jsonb)
    ) ELSE NULL END
) snapshot FROM allowed CROSS JOIN candidate_budget
"""


def _read_game_bound_document(database, statement, parameters):
    execute = getattr(database, 'execute_native', None) or database.execute
    row = execute(statement, parameters).fetchone()
    try:
        document = row['snapshot']
    except TypeError:
        document = row[0]
    if isinstance(document, str):
        document = json.loads(document)
    if not document['exists']:
        raise ValueError('repertoire_not_found')
    if not document['bounded']:
        _refuse_unbounded_source(document)
    return document


def read_authored_scope(database, repertoire_id: str) -> dict:
    """One MVCC view of every authored line and the unchanged full-source bounds.

    This is not a complete physical publication. Its consumer must derive an
    exact game boundary and obtain the separately complete candidate relation.
    No connection or transaction is owned or kept open by this adapter.
    """
    if not isinstance(repertoire_id, str) or not repertoire_id or len(repertoire_id) > 200:
        raise ValueError('invalid_repertoire_scope')
    document = _read_game_bound_document(database, _AUTHORED_STATEMENT,
        (repertoire_id, MAX_SOURCE_ROWS, MAX_ROW_BYTES))
    return document['facts']


def _candidate_routes(route_ids):
    if (not isinstance(route_ids, (tuple, list)) or len(route_ids) > MAX_SOURCE_ROWS
            or any(not isinstance(value, str) or not value or len(value) > 200 for value in route_ids)
            or len(set(route_ids)) != len(route_ids)):
        raise ValueError('invalid_candidate_route_scope')
    return tuple(sorted(route_ids))


def _same_header(actual, expected):
    return all(field in actual and field in expected and actual[field] == expected[field]
               for field in _HEADER_FIELDS)


def read_candidate_bindings(database, *, repertoire_id: str, expected_header: dict,
                            route_ids: tuple[str, ...]) -> dict:
    """Complete physical source for all derived surviving routes, in one MVCC view.

    Stored FEN hints never filter this relation. Graph rows survive missing or
    nonmember physical cards, making that absence observable to the Repertoire
    owner. No caller readiness assertion or SQL-generated mapping is accepted.
    """
    if (not isinstance(repertoire_id, str) or not repertoire_id or len(repertoire_id) > 200
            or not isinstance(expected_header, dict) or expected_header.get('id') != repertoire_id):
        raise ValueError('invalid_repertoire_scope')
    routes = _candidate_routes(route_ids)
    document = _read_game_bound_document(database, _CANDIDATE_STATEMENT,
        (repertoire_id, MAX_SOURCE_ROWS, MAX_ROW_BYTES, list(routes)))
    if document.get('requested_line_count') != len(routes):
        raise ValueError('first_departure_candidate_routes_changed')
    facts = document['facts']
    if (facts is None or not _same_header(facts['repertoire'], expected_header)
            or facts['repertoire'].get('graph_state') != 'ready'):
        raise ValueError('first_departure_selected_publication_changed')
    return facts


def fence_candidate_bindings(database, expected: dict) -> None:
    """Linearize complete candidate freshness in the caller's short writer.

    Caller has already fenced all selected headers and holds the existing graph
    shared advisory locks. The exact relation read below detects added/removed
    graph, physical, membership and availability facts at its MVCC snapshot.
    Integrity publication has no global advisory exclusion: a later commit may
    order after this freshness point. The selected target's existing locked
    current-owner validation and atomic effect remain mandatory afterward.
    """
    if database.read_only or database.raw.info.transaction_status.name != 'INTRANS':
        raise ValueError('borrowed_owner_writer_transaction_required')
    if not isinstance(expected, dict) or not isinstance(expected.get('repertoire'), dict):
        raise ValueError('invalid_candidate_route_scope')
    routes = _candidate_routes(expected.get('route_ids'))
    current = read_candidate_bindings(database, repertoire_id=expected['repertoire']['id'],
        expected_header=expected['repertoire'], route_ids=routes)
    # Compare canonical values rather than SQL text or unstable query row order.
    # Names are not a source/policy fence, matching _fence_selected_publication.
    def identity(facts):
        return dict(repertoire={key:facts['repertoire'][key] for key in _HEADER_FIELDS},
            route_ids=facts['route_ids'], cards=facts['cards'],
            memberships=facts['memberships'], graphs=facts['graphs'])
    if identity(current) != identity(expected):
        raise ValueError('first_departure_candidate_bindings_changed')
