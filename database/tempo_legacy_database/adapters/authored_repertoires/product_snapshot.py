"""Bounded repertoire-only SELECT over the existing borrowed PostgreSQL port.

One statement owns one MVCC view even inside a READ COMMITTED writer transaction.
No connection, transaction settings, locks, commits or schema are owned here.
"""
from __future__ import annotations

import json

MAX_SOURCE_ROWS = 10000
MAX_GRAPH_ROWS = 20000
MAX_ROW_BYTES = 65536
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024

# Raw row/count sentinels run before JSON serialization. For a complete bounded
# sample, count each row's actual JSON bytes rather than multiplying ordinary
# text by the worst possible escape expansion. 1024 bytes covers the fixed
# object/array wrapper; two bytes per row cover array separators. The unchanged
# final encoded-size check remains authoritative. No source field is omitted.
STATEMENT = """
WITH limits AS (
    SELECT %s::text repertoire_id, %s::int source_limit, %s::int graph_limit,
           %s::bigint byte_limit, %s::bigint row_byte_limit
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
    ORDER BY g.line_id,g.decision_index LIMIT (SELECT graph_limit+1 FROM limits)
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
           coalesce(sum(bytes*6+512),0)+1024 coarse_estimated_bytes,
           coalesce(max(bytes),0) largest_row
    FROM sizes
), reuse AS MATERIALIZED (
    -- A statement-local serialized scratch set is bounded by the existing
    -- conservative estimate to two final representations. Larger shapes keep
    -- streaming the size pass, rather than allocating an unbounded row cache.
    SELECT b.line_count<=l.source_limit AND b.card_count<=l.source_limit
        AND b.membership_count<=l.source_limit AND b.graph_count<=l.graph_limit
        AND b.largest_row<=l.row_byte_limit AND b.coarse_estimated_bytes<=2*l.byte_limit allowed
    FROM budget b CROSS JOIN limits l
), json_rows AS MATERIALIZED (
    SELECT 'repertoire' kind,to_jsonb(r) body FROM repertoire r CROSS JOIN reuse WHERE reuse.allowed
    UNION ALL SELECT 'lines',to_jsonb(s) FROM lines s CROSS JOIN reuse WHERE reuse.allowed
    UNION ALL SELECT 'cards',to_jsonb(c) FROM cards c CROSS JOIN reuse WHERE reuse.allowed
    UNION ALL SELECT 'memberships',to_jsonb(m) FROM memberships m CROSS JOIN reuse WHERE reuse.allowed
    UNION ALL SELECT 'graphs',to_jsonb(g) FROM graphs g CROSS JOIN reuse WHERE reuse.allowed
), serialized_budget AS (
    SELECT b.*,reuse.allowed reuse_rows,CASE WHEN b.line_count<=l.source_limit AND b.card_count<=l.source_limit
        AND b.membership_count<=l.source_limit AND b.graph_count<=l.graph_limit
        AND b.largest_row<=l.row_byte_limit THEN
          CASE WHEN reuse.allowed THEN 1024+coalesce((SELECT sum(octet_length(body::text)
              +CASE WHEN kind='repertoire' THEN 0 ELSE 2 END) FROM json_rows),0) ELSE
          1024
          +coalesce((SELECT sum(octet_length(to_jsonb(r)::text)) FROM repertoire r),0)
          +coalesce((SELECT sum(octet_length(to_jsonb(s)::text)+2) FROM lines s),0)
          +coalesce((SELECT sum(octet_length(to_jsonb(c)::text)+2) FROM cards c),0)
          +coalesce((SELECT sum(octet_length(to_jsonb(m)::text)+2) FROM memberships m),0)
          +coalesce((SELECT sum(octet_length(to_jsonb(g)::text)+2) FROM graphs g),0)
          END
        ELSE b.coarse_estimated_bytes END estimated_bytes
    FROM budget b CROSS JOIN limits l CROSS JOIN reuse
), allowed AS (
    SELECT b.*,b.line_count<=l.source_limit AND b.card_count<=l.source_limit
        AND b.membership_count<=l.source_limit AND b.graph_count<=l.graph_limit
        AND b.estimated_bytes<=l.byte_limit AND b.largest_row<=l.row_byte_limit bounded
    FROM serialized_budget b CROSS JOIN limits l
)
SELECT jsonb_build_object(
    'exists',repertoire_count=1,'bounded',bounded,'estimated_bytes',estimated_bytes,
    'line_count',line_count,'card_count',card_count,'membership_count',membership_count,
    'graph_count',graph_count,'largest_row',largest_row,
    'facts',NULL
) snapshot,
CASE WHEN bounded AND repertoire_count=1 THEN (CASE WHEN reuse_rows THEN jsonb_build_object(
        'repertoire',(SELECT body FROM json_rows WHERE kind='repertoire'),
        'lines',coalesce((SELECT jsonb_agg(body ORDER BY body->>'id') FROM json_rows WHERE kind='lines'),'[]'::jsonb),
        'cards',coalesce((SELECT jsonb_agg(body ORDER BY body->>'id') FROM json_rows WHERE kind='cards'),'[]'::jsonb),
        'memberships',coalesce((SELECT jsonb_agg(body ORDER BY body->>'card_id') FROM json_rows WHERE kind='memberships'),'[]'::jsonb),
        'graphs',coalesce((SELECT jsonb_agg(body ORDER BY body->>'line_id',(body->>'decision_index')::bigint) FROM json_rows WHERE kind='graphs'),'[]'::jsonb)
    ) ELSE jsonb_build_object(
        'repertoire',(SELECT to_jsonb(r) FROM repertoire r),
        'lines',coalesce((SELECT jsonb_agg(to_jsonb(s) ORDER BY id) FROM lines s),'[]'::jsonb),
        'cards',coalesce((SELECT jsonb_agg(to_jsonb(c) ORDER BY id) FROM cards c),'[]'::jsonb),
        'memberships',coalesce((SELECT jsonb_agg(to_jsonb(m) ORDER BY card_id) FROM memberships m),'[]'::jsonb),
        'graphs',coalesce((SELECT jsonb_agg(to_jsonb(g) ORDER BY line_id,decision_index) FROM graphs g),'[]'::jsonb)
    ) END)::text ELSE NULL END facts_json
FROM allowed
"""


def read_repertoire_json(database, repertoire_id: str) -> str:
    """Eager bounded JSON text; callers can decode after releasing their reader."""
    if not isinstance(repertoire_id, str) or not repertoire_id or len(repertoire_id) > 200:
        raise ValueError("invalid_repertoire_scope")
    execute = getattr(database, "execute_native", None) or database.execute
    row = execute(STATEMENT, (repertoire_id, MAX_SOURCE_ROWS, MAX_GRAPH_ROWS,
                             MAX_SNAPSHOT_BYTES, MAX_ROW_BYTES)).fetchone()
    try:
        document = row["snapshot"]
    except TypeError:
        document = row[0]
    if isinstance(document, str):
        document = json.loads(document)
    if not document["exists"]:
        raise ValueError("repertoire_not_found")
    if not document["bounded"]:
        from app.repertoire import RepertoireSnapshotBoundExceeded
        counts = (("line_count", "source_rows", MAX_SOURCE_ROWS),
                  ("card_count", "card_rows", MAX_SOURCE_ROWS),
                  ("membership_count", "membership_rows", MAX_SOURCE_ROWS),
                  ("graph_count", "graph_rows", MAX_GRAPH_ROWS))
        fields = [key for key, _, _ in counts] + ["estimated_bytes", "largest_row"]
        if (any(type(document.get(key)) is not int or document[key] < 0 for key in fields)
                or any(document[key] > limit + 1 for key, _, limit in counts)):
            raise ValueError("invalid_repertoire_snapshot_bound_metadata")
        complete = all(document[key] <= limit for key, _, limit in counts)
        violations = [dict(dimension=dimension, observed=document[key], limit=limit, observation="lower_bound")
                      for key, dimension, limit in counts if document[key] > limit]
        for key, dimension, limit, observation in (
                ("largest_row", "row_bytes", MAX_ROW_BYTES, "exact" if complete else "lower_bound"),
                ("estimated_bytes", "estimated_snapshot_bytes", MAX_SNAPSHOT_BYTES, "conservative_estimate")):
            if document[key] > limit:
                violations.append(dict(dimension=dimension, observed=document[key], limit=limit, observation=observation))
        if not violations:
            raise ValueError("invalid_repertoire_snapshot_bound_metadata")
        raise RepertoireSnapshotBoundExceeded(stage="read", sample_complete=complete, violations=violations)
    try:
        facts_json = row["facts_json"]
    except (TypeError, KeyError):
        facts_json = row[1] if not isinstance(row, dict) and len(row) > 1 else None
    # SQL's bounded JSON text is already the final serialization. Measuring it
    # before decoding avoids encoding the complete capture again while the
    # borrowed reader transaction is still open. Old scripted envelopes remain
    # readable; the public return value and source facts are unchanged.
    if facts_json is not None:
        if not isinstance(facts_json, str):
            raise ValueError("invalid_repertoire_snapshot_serialization")
        encoded_bytes = len(facts_json.encode())
    else:
        facts = document["facts"]
        facts_json = json.dumps(facts, ensure_ascii=False)
        encoded_bytes = len(facts_json.encode())
    if encoded_bytes > MAX_SNAPSHOT_BYTES:
        from app.repertoire import RepertoireSnapshotBoundExceeded
        raise RepertoireSnapshotBoundExceeded(stage="read", sample_complete=True, violations=[
            dict(dimension="snapshot_bytes", observed=encoded_bytes, limit=MAX_SNAPSHOT_BYTES, observation="exact")])
    return facts_json


def read_repertoire(database, repertoire_id: str) -> dict:
    """Compatible eager dictionary read over the same bounded one-statement port."""
    return json.loads(read_repertoire_json(database, repertoire_id))


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
