"""Existing bounded legacy remediation integration facts/locks and driver facade."""
from ..postgres_store import PostgresConnection
from ..http_composition import availability


# Only owner-controlled SQL, never a producer-supplied predicate. The retained
# proof is not a claim that legacy cards have Content publication columns.
CURRENT_FACT_SQL = """SELECT DISTINCT ON (contribution.publication_id,contribution.publication_generation)
    contribution.payload_json,card.introduced_at
 FROM remediation_obligation_heads head
 JOIN remediation_contributions contribution USING(workspace_id,obligation_id,obligation_revision)
 JOIN cards card ON card.id=contribution.card_id AND card.revision=contribution.card_revision
 JOIN repertoires repertoire ON repertoire.id=contribution.payload_json::jsonb->'authority'->>'repertoire_id'
 JOIN repertoire_game_scope scope ON scope.id=1
 JOIN legacy_learning_identity identity ON identity.id=1
 JOIN opening_graph_publications publication ON publication.repertoire_id=repertoire.id
 JOIN imported_games game ON game.id=contribution.game_id
 WHERE head.workspace_id=%s AND head.card_id=%s AND head.active
 AND contribution.publication_id LIKE 'legacy-graph:%%'
 AND contribution.payload_json::jsonb->'authority'->>'kind'='legacy_graph'
 AND contribution.payload_json::jsonb->'authority'->>'workspace_id'=identity.workspace_id
 AND contribution.payload_json::jsonb->'authority'->>'target_id'=identity.target_id
 AND repertoire.scope_source_revision=(contribution.payload_json::jsonb->'authority'->>'scope_source_revision')::bigint
 AND repertoire.canonical_prefix_revision=(contribution.payload_json::jsonb->'authority'->>'canonical_prefix_revision')::bigint
 AND repertoire.canonical_prefix_moves_json::jsonb='[]'::jsonb
 AND scope.generation=(contribution.payload_json::jsonb->'authority'->>'catalogue_generation')::bigint
 AND publication.generation=contribution.publication_generation AND publication.state='ready'
 AND game.adaptive_excluded=0
 AND game.provider=contribution.payload_json::jsonb->'game_source'->>'provider'
 AND game.provider_game_id=contribution.payload_json::jsonb->'game_source'->>'provider_game_id'
 AND game.content_hash=contribution.payload_json::jsonb->'game_source'->>'content_hash'
 AND card.content_type='opening' AND card.revision=1 AND card.introduced_at IS NOT NULL
 AND card.state IN ('learning','mature')
 AND card.archived=0 AND card.pending_validation=0 AND card.superseded_by IS NULL
 AND NOT EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=card.id)
 AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block WHERE block.card_id=card.id)
 AND NOT EXISTS(SELECT 1 FROM repertoire_cards other WHERE other.card_id=card.id AND other.repertoire_id<>repertoire.id)
 AND (card.repertoire_id IS NULL OR card.repertoire_id=repertoire.id)
 AND (card.repertoire_id=repertoire.id OR EXISTS(SELECT 1 FROM repertoire_cards link
     WHERE link.card_id=card.id AND link.repertoire_id=repertoire.id))
 AND EXISTS(SELECT 1 FROM opening_graph_steps step WHERE step.repertoire_id=repertoire.id
     AND step.generation=publication.generation AND step.card_id=card.id
     AND step.line_id=contribution.payload_json::jsonb->'comparison'->>'route_id'
     AND step.starting_fen=card.start_fen AND step.moves_json::jsonb=card.moves_json::jsonb
     AND step.trained_color=card.trained_color)
 ORDER BY contribution.publication_id,contribution.publication_generation,contribution.obligation_revision DESC
 LIMIT 2"""


# Current imported-game authority is supplied by its owner. If any active source
# for this card is corrected/excluded/missing, defer the whole card until the
# producer replaces or withdraws it. Do not let another game's valid projection
# launder that stale incident through a card-level shared predicate.
SOURCE_FENCE_SQL = """SELECT DISTINCT contribution.game_id,
    COALESCE(game.provider=contribution.payload_json::jsonb->'game_source'->>'provider'
      AND game.provider_game_id=contribution.payload_json::jsonb->'game_source'->>'provider_game_id'
      AND game.content_hash=contribution.payload_json::jsonb->'game_source'->>'content_hash'
      AND game.color=contribution.payload_json::jsonb->'decision_scope_proof'->'scope'->>'learner_color'
      AND game.result IN ('win','loss','draw','1-0','0-1','1/2-1/2')
      AND game.adaptive_excluded=0,false) source_current
 FROM remediation_obligation_heads head
 JOIN remediation_contributions contribution USING(workspace_id,obligation_id,obligation_revision)
 JOIN remediation_game_heads accepted_game ON accepted_game.workspace_id=head.workspace_id
    AND accepted_game.game_id=contribution.game_id AND accepted_game.game_revision=contribution.game_revision
 LEFT JOIN imported_games game ON game.id=contribution.game_id
 WHERE head.workspace_id=%s AND head.card_id=%s AND head.active
 ORDER BY contribution.game_id LIMIT 4"""


def is_postgres(database):
    return isinstance(database, PostgresConnection)


def is_read_only(database):
    return database.read_only


def read_projection(database):
    from psycopg.rows import tuple_row
    class ReadProjection:
        info = database.raw.info
        def execute(self, statement, parameters=()):
            if not statement.lstrip().upper().startswith("SELECT "):
                raise RuntimeError("Legacy status is a read-only projection")
            return database.raw.cursor(row_factory=tuple_row).execute(statement, parameters)
    return ReadProjection()


def current_facts(database, workspace_id, card_id):
    return database.execute_native(CURRENT_FACT_SQL, (workspace_id, card_id)).fetchall()


def source_fences(database, workspace_id, card_id):
    return database.execute_native(SOURCE_FENCE_SQL, (workspace_id, card_id)).fetchall()


def try_lock_graph(database, repertoire_id):
    return database.execute_native("SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))",
        ("tempo:opening-graph:" + repertoire_id,)).fetchone()


def try_hold_authority(database, game_id, repertoire_id):
    return database.execute_native("""SELECT 1 FROM repertoires repertoire
            CROSS JOIN repertoire_game_scope scope CROSS JOIN legacy_learning_identity identity
            JOIN opening_graph_publications publication ON publication.repertoire_id=repertoire.id
            JOIN repertoire_integrity_state integrity ON integrity.repertoire_id=repertoire.id
            JOIN background_tasks graph ON graph.kind='opening_graph_rebuild' AND graph.deduplication_key=repertoire.id
            JOIN background_tasks scan ON scan.kind='integrity_scan' AND scan.deduplication_key=repertoire.id
            JOIN imported_games game ON game.id=%s
            WHERE repertoire.id=%s AND scope.id=1 AND identity.id=1
            FOR SHARE OF repertoire,scope,identity,publication,integrity,graph,scan,game SKIP LOCKED""",
        (game_id, repertoire_id)).fetchone()


def try_hold_source_games(database, sources):
    return database.execute_native("SELECT id FROM imported_games WHERE id IN (" +
        ",".join("%s" for _ in sources) + ") ORDER BY id FOR SHARE SKIP LOCKED",
        tuple(row["game_id"] for row in sources)).fetchall()


def issued_origin(database, queue_entry_id, card_id, revision):
    return database.execute("SELECT issued_as_head FROM queue_attempt_origins "
        "WHERE queue_entry_id=? AND card_id=? AND revision=?", (queue_entry_id, card_id, revision)).fetchone()


def bound_attempt(database, workspace_id, attempt_id):
    return database.execute_native("""SELECT attempt_id,card_id,card_revision,publication_id,publication_generation,issued_at
        FROM remediation_attempts WHERE workspace_id=%s AND attempt_id=%s""", (workspace_id, attempt_id)).fetchone()


def settlement_review(database, review_id, card_id):
    return database.execute("SELECT rating,guided,reviewed_at,source_kind,invalidated_at,source_ref FROM reviews "
        "WHERE id=? AND card_id=?", (review_id, card_id)).fetchone()


def settlement_origin(database, queue_entry_id, card_id, revision):
    return database.execute("SELECT attempt_failed FROM queue_attempt_origins WHERE queue_entry_id=? "
        "AND card_id=? AND revision=? AND issued_as_head=1", (queue_entry_id, card_id, revision)).fetchone()


def invalidated_review(database, review_id, card_id):
    return database.execute("SELECT invalidated_at FROM reviews WHERE id=? AND card_id=?", (review_id, card_id)).fetchone()


def clear_priority(database, card_id, miss_reason):
    database.execute("UPDATE daily_queue SET gameplay_priority_reason=NULL WHERE card_id=? "
        "AND gameplay_priority_reason=?", (card_id, miss_reason))


def lock_card(database, card_id):
    database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("tempo:card-edit:" + card_id,))
    database.execute("SELECT id FROM cards WHERE id=? FOR UPDATE", (card_id,))


def buried_entry(database, card_id, day):
    return database.execute("SELECT 1 FROM daily_queue WHERE card_id=? AND queue_date=? AND status='buried' LIMIT 1", (card_id, day)).fetchone()


def queued_entry(database, card_id, day):
    return database.execute("SELECT id FROM daily_queue WHERE card_id=? AND queue_date=? "
        "AND status='queued' ORDER BY position,id LIMIT 1", (card_id, day)).fetchone()


def target_identity(database):
    return database.execute_native("SELECT target_id,workspace_id FROM legacy_learning_identity WHERE id=1").fetchone()


def card_work(database, workspace_id, card_id):
    return database.execute_native("SELECT sequence,work_state,reason,queue_entry_id FROM remediation_cards "
        "WHERE workspace_id=%s AND card_id=%s", (workspace_id, card_id)).fetchone()


def current_revision(database, card_id):
    return database.execute("SELECT revision FROM cards WHERE id=?", (card_id,)).fetchone()


def active_obligations(database, workspace_id, card_id):
    return database.execute_native("SELECT COUNT(*) FROM remediation_obligation_heads WHERE workspace_id=%s "
        "AND card_id=%s AND active", (workspace_id, card_id)).fetchone()[0]


def latest_contribution(database, workspace_id, card_id):
    return database.execute_native("SELECT payload_json FROM remediation_contributions WHERE workspace_id=%s "
        "AND card_id=%s ORDER BY obligation_revision DESC,obligation_id LIMIT 1", (workspace_id, card_id)).fetchone()


def available_head(database, day, miss_reason):
    return database.execute(availability.available_attempt_sql(miss_reason) + " ORDER BY q.position,q.id LIMIT 1", (day,)).fetchone()


def attempt_binding_exists(database, workspace_id, attempt_id):
    return database.execute_native("SELECT 1 FROM remediation_attempts WHERE workspace_id=%s AND attempt_id=%s", (workspace_id, attempt_id)).fetchone()
