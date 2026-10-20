"""Complete parameter-bound current-source projection for one locked card scope."""
import json
from .legacy_game_source import CARD_SOURCE_FIELDS,GAME_SOURCE_FIELDS


def current_source_rows(database,scope,requests):
    # One indexed projection for the complete contributor set. Inputs contain
    # identities only; no caller-supplied SQL, predicate or table name is used.
    inputs=[dict(key=request.encounter.key(),game_id=request.encounter.game_id,
        repertoire_id=request.encounter.repertoire_id) for request in requests]
    card_columns=','.join('card.'+name+' AS card_'+name for name in CARD_SOURCE_FIELDS)
    game_columns=','.join('game.'+name+' AS game_'+name for name in GAME_SOURCE_FIELDS)
    rows=database.execute_native('''SELECT requested.key,identity.target_id,identity.workspace_id,
        repertoire.scope_source_revision,repertoire.canonical_prefix_revision,
        repertoire.canonical_prefix_moves_json,scope.generation AS catalogue_generation,
        publication.generation AS graph_generation,publication.state AS graph_state,
        integrity.status AS integrity_status,integrity.scan_status,integrity.scan_generation,
        card.state AS card_state,'''+card_columns+','+game_columns+''',
        EXISTS(SELECT 1 FROM deleted_cards deleted WHERE deleted.card_id=card.id) AS deleted,
        EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block WHERE block.card_id=card.id) AS blocked,
        ARRAY(SELECT link.repertoire_id FROM repertoire_cards link WHERE link.card_id=card.id
            ORDER BY link.repertoire_id) AS memberships
        FROM jsonb_to_recordset(%s::jsonb) AS requested(key text,game_id text,repertoire_id text)
        CROSS JOIN legacy_learning_identity identity CROSS JOIN repertoire_game_scope scope
        LEFT JOIN cards card ON card.id=%s
        LEFT JOIN imported_games game ON game.id=requested.game_id
        LEFT JOIN repertoires repertoire ON repertoire.id=requested.repertoire_id
        LEFT JOIN opening_graph_publications publication ON publication.repertoire_id=repertoire.id
        LEFT JOIN repertoire_integrity_state integrity ON integrity.repertoire_id=repertoire.id
        WHERE identity.id=1 AND scope.id=1 AND identity.target_id=%s AND identity.workspace_id=%s
        ORDER BY requested.key''',
        (json.dumps(inputs),scope.card_id,scope.target_id,scope.workspace_id)).fetchall()
    if len(rows)!=len(inputs) or len({row['key'] for row in rows})!=len(inputs):
        raise ValueError('Complete current exposure source projection required')
    return {row['key']:dict(row) for row in rows}


def lock_card_scope(database,scope):
    """Existing prefix/card order; all exposure writers share the card edit lock."""
    database.execute_native("SELECT pg_advisory_xact_lock_shared(hashtextextended('tempo:prefix-transition:reservations',0))")
    database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",('tempo:card-edit:'+scope.card_id,))
    database.execute_native('SELECT id FROM cards WHERE id=%s FOR UPDATE',(scope.card_id,))
    identity=database.execute_native('SELECT target_id,workspace_id FROM legacy_learning_identity WHERE id=1 FOR SHARE NOWAIT').fetchone()
    if not identity or (identity['target_id'],identity['workspace_id'])!=(scope.target_id,scope.workspace_id):
        raise ValueError('Exposure target/workspace changed')


def lock_source_inputs(database,requests):
    """After card lock, never wait for a graph/metadata lock in reverse order."""
    from psycopg.errors import LockNotAvailable
    if not requests:return
    repertoires=sorted({request.encounter.repertoire_id for request in requests})
    games=sorted({request.encounter.game_id for request in requests})
    for repertoire in repertoires:
        locked=database.execute_native('SELECT pg_try_advisory_xact_lock_shared(hashtextextended(%s,0))',
            ('tempo:opening-graph:'+repertoire,)).fetchone()
        if not locked or not locked[0]:raise LockNotAvailable('Exposure source is being edited; retry')
    for table,column,values in [('repertoires','id',repertoires),('opening_graph_publications','repertoire_id',repertoires),
            ('repertoire_integrity_state','repertoire_id',repertoires),('imported_games','id',games)]:
        # Table/column are this owning adapter's closed constants, not inputs.
        database.execute_native(f'SELECT {column} FROM {table} WHERE {column}=ANY(%s) ORDER BY {column} FOR SHARE NOWAIT',(values,)).fetchall()
    database.execute_native('SELECT generation FROM repertoire_game_scope WHERE id=1 FOR SHARE NOWAIT').fetchone()


def queued_entry(database,card_id,day):
    return database.execute('''SELECT id,gameplay_priority_reason FROM daily_queue
        WHERE queue_date=? AND card_id=? AND status='queued' ORDER BY position,id LIMIT 1''',
        (day,card_id)).fetchone()


def buried_today(database,card_id,day):
    return bool(database.execute("SELECT 1 FROM daily_queue WHERE card_id=? AND queue_date=? AND status='buried' LIMIT 1",
        (card_id,day)).fetchone())
