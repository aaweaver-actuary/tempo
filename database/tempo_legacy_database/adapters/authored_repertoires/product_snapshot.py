"""Bounded repertoire-only SELECT over the existing borrowed PostgreSQL port.

One statement owns one MVCC view even inside a READ COMMITTED writer transaction.
Ordinary reads own no connection, transaction settings, locks, commits or schema.
The explicit writer-only reservation operation retains existing source locks
through its caller transaction; it owns no commit or content mutation.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.repertoire.interfaces import RepertoireSnapshot

MAX_SOURCE_ROWS = 10000
MAX_GRAPH_ROWS = 20000
MAX_ROW_BYTES = 65536
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024

# Raw text is bounded before JSON aggregation. Six bytes per source byte covers
# JSON escaping; 512 bytes per row covers fixed keys/scalars/array punctuation.
# A row-count sentinel prevents a bounded prefix from claiming completeness.
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
           coalesce(sum(bytes*6+512),0)+1024 estimated_bytes,
           coalesce(max(bytes),0) largest_row
    FROM sizes
), allowed AS (
    SELECT b.*,b.line_count<=l.source_limit AND b.card_count<=l.source_limit
        AND b.membership_count<=l.source_limit AND b.graph_count<=l.graph_limit
        AND b.estimated_bytes<=l.byte_limit AND b.largest_row<=l.row_byte_limit bounded
    FROM budget b CROSS JOIN limits l
)
SELECT jsonb_build_object(
    'exists',repertoire_count=1,'bounded',bounded,'estimated_bytes',estimated_bytes,
    'facts',CASE WHEN bounded AND repertoire_count=1 THEN jsonb_build_object(
        'repertoire',(SELECT to_jsonb(r) FROM repertoire r),
        'lines',coalesce((SELECT jsonb_agg(to_jsonb(s) ORDER BY id) FROM lines s),'[]'::jsonb),
        'cards',coalesce((SELECT jsonb_agg(to_jsonb(c) ORDER BY id) FROM cards c),'[]'::jsonb),
        'memberships',coalesce((SELECT jsonb_agg(to_jsonb(m) ORDER BY card_id) FROM memberships m),'[]'::jsonb),
        'graphs',coalesce((SELECT jsonb_agg(to_jsonb(g) ORDER BY line_id,decision_index) FROM graphs g),'[]'::jsonb)
    ) ELSE NULL END
) snapshot FROM allowed
"""


def read_repertoire(database, repertoire_id: str) -> dict:
    """One parameter-bound read; overflow fails, never a silently partial view."""
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
        raise ValueError("repertoire_snapshot_exceeds_bound")
    facts = document["facts"]
    if len(json.dumps(facts, ensure_ascii=False).encode()) > MAX_SNAPSHOT_BYTES:
        raise ValueError("repertoire_snapshot_exceeds_bound")
    return facts


def current_snapshot(database, repertoire_id: str):
    """Concrete RepertoireSnapshot reader for composition/current change stores."""
    from app.repertoire.interfaces import project_repertoire
    return project_repertoire(read_repertoire(database, repertoire_id))


def current_published_snapshot(database, repertoire_id: str):
    """Feedback read with explicit publication version; source audits stay separate.

    A missing/non-ready generation fails instead of borrowing an authored counter.
    Current Study capture/fencing remains mandatory before any learning effect.
    """
    from app.repertoire.interfaces import project_published_repertoire
    return project_published_repertoire(read_repertoire(database, repertoire_id))


class SnapshotReservationUnavailable(RuntimeError):
    """Optional publication must yield; caller rolls back its transaction."""
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def reserve_current_snapshot(database, repertoire_id: str) -> RepertoireSnapshot:
    """Reserve the existing complete audit view through the caller's short commit.

    Prepare immutable projection before source locks, then reserve and reread all
    facts. Equality permits reuse of that exact projection without chess work
    under source/card locks. No connection, commit, rollback or DML is owned here.
    All failures require the caller to roll back, releasing partial reservations.
    """
    raw = getattr(database, "raw", database)
    status = getattr(getattr(getattr(raw, "info", None), "transaction_status", None), "name", None)
    if getattr(database, "read_only", False) or status != "INTRANS":
        raise SnapshotReservationUnavailable("borrowed_writer_transaction_required")
    execute = getattr(database, "execute_native", None) or database.execute

    def rows(stage, statement, parameters=(), *, maximum=MAX_SOURCE_ROWS):
        result = execute(f"/*snapshot-reserve:{stage}*/ " + statement, parameters).fetchall()
        if len(result) > maximum:
            raise SnapshotReservationUnavailable("snapshot_reservation_exceeds_bound")
        return result

    def scalar(stage, statement, parameters=()):
        row = execute(f"/*snapshot-reserve:{stage}*/ " + statement, parameters).fetchone()
        if row is None:
            return None
        # Both the existing TempoRow/native port and raw psycopg dict rows work.
        return next(iter(row.values())) if isinstance(row, dict) else row[0]

    try:
        if not scalar("writer", "SELECT current_setting('transaction_read_only')='off'"):
            raise SnapshotReservationUnavailable("borrowed_writer_transaction_required")
        if scalar("isolation", "SELECT current_setting('transaction_isolation')") != "read committed":
            raise SnapshotReservationUnavailable("borrowed_read_committed_transaction_required")
        before = read_repertoire(database, repertoire_id)
        from app.repertoire.interfaces import project_repertoire
        prepared = project_repertoire(before)

        # Same graph-task -> prefix reservation -> graph -> repertoire order as
        # the retained repair owner; try/NOWAIT variants make optional work yield.
        rows("graph-task", "SELECT id FROM background_tasks WHERE kind='opening_graph_rebuild' "
             "AND deduplication_key=%s FOR UPDATE NOWAIT", (repertoire_id,), maximum=1)
        for key in ("tempo:prefix-transition:reservations", "tempo:opening-graph:" + repertoire_id):
            if not scalar("graph-reservation", "SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))", (key,)):
                raise SnapshotReservationUnavailable("snapshot_source_busy")
        if not rows("repertoire", "SELECT id FROM repertoires WHERE id=%s FOR UPDATE NOWAIT",
                    (repertoire_id,), maximum=1):
            raise SnapshotReservationUnavailable("snapshot_source_changed")
        if not rows("identity", "SELECT workspace_id FROM legacy_learning_identity WHERE id=1 FOR SHARE NOWAIT", maximum=1):
            raise SnapshotReservationUnavailable("snapshot_source_changed")
        rows("publication", "SELECT repertoire_id FROM opening_graph_publications WHERE repertoire_id=%s "
             "FOR SHARE NOWAIT", (repertoire_id,), maximum=1)
        rows("lines", "SELECT id FROM repertoire_lines WHERE repertoire_id=%s ORDER BY id LIMIT %s "
             "FOR SHARE NOWAIT", (repertoire_id, MAX_SOURCE_ROWS + 1))

        # Reserve all existing candidate cards, including nonopening cards which
        # could enter the read without changing their repertoire FK. Empty-move
        # content transitions need not advance the retained source counter.
        candidates = rows("scope-cards", "SELECT c.id FROM cards c WHERE c.repertoire_id=%s OR EXISTS("
            "SELECT 1 FROM repertoire_cards rc WHERE rc.repertoire_id=%s AND rc.card_id=c.id) "
            "ORDER BY c.id LIMIT %s", (repertoire_id, repertoire_id, MAX_SOURCE_ROWS + 1))
        card_ids = [next(iter(row.values())) if isinstance(row, dict) else row[0] for row in candidates]
        # Existing card-edit reservations serialize permanent deletion/recreation.
        # Card UPDATE row reservations also close FK insert phantoms for links
        # and integrity blocks; existing rows below protect deletion/reassignment.
        for card_id in card_ids:
            if not scalar("card-reservation", "SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))",
                          ("tempo:card-edit:" + card_id,)):
                raise SnapshotReservationUnavailable("snapshot_source_busy")
        locked_cards = rows("cards", "SELECT id FROM cards WHERE id=ANY(%s) ORDER BY id FOR UPDATE NOWAIT", (card_ids,))
        if len(locked_cards) != len(card_ids):
            raise SnapshotReservationUnavailable("snapshot_source_changed")
        rows("memberships", "SELECT repertoire_id,card_id FROM repertoire_cards WHERE repertoire_id=%s "
             "AND card_id=ANY(%s) ORDER BY card_id LIMIT %s FOR SHARE NOWAIT",
             (repertoire_id, card_ids, MAX_SOURCE_ROWS + 1))
        if before["graphs"]:
            rows("graphs", "SELECT line_id,decision_index FROM opening_graph_steps WHERE repertoire_id=%s "
                 "AND generation=%s ORDER BY line_id,decision_index LIMIT %s FOR SHARE NOWAIT",
                 (repertoire_id, before["repertoire"]["graph_generation"], MAX_GRAPH_ROWS + 1), maximum=MAX_GRAPH_ROWS)
        rows("integrity-blocks", "SELECT repertoire_id,card_id,issue_id FROM repertoire_integrity_card_blocks "
             "WHERE card_id=ANY(%s) ORDER BY repertoire_id,card_id,issue_id LIMIT %s FOR SHARE NOWAIT",
             (card_ids, MAX_SOURCE_ROWS + 1))
        rows("deletions", "SELECT card_id FROM deleted_cards WHERE card_id=ANY(%s) "
             "ORDER BY card_id LIMIT %s FOR SHARE NOWAIT", (card_ids, MAX_SOURCE_ROWS + 1))
        if read_repertoire(database, repertoire_id) != before:
            raise SnapshotReservationUnavailable("snapshot_source_changed")
        return prepared
    except Exception as error:
        if getattr(error, "sqlstate", None) in {"55P03", "57014", "40001", "40P01"}:
            raise SnapshotReservationUnavailable("snapshot_source_busy") from error
        raise
