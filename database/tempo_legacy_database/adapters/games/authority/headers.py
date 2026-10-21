"""Existing fixed source/receipt reads over the retained legacy reader seam."""

from tempo_legacy_database.connections import background_read_connection

_REPERTOIRE_COLUMNS = "id,name,is_main,scope_source_revision,canonical_prefix_revision,canonical_prefix_moves_json,canonical_prefix_preview_id"
_CARD_COLUMNS = "id,revision,start_fen,moves_json,trained_color,content_type,archived,pending_validation,superseded_by,repertoire_id,canonical_route_source,introduced_at"
_GAME_COLUMNS = "id,provider,provider_game_id,content_hash,played_at,color,result,start_fen,moves_json,adaptive_excluded"
_GAME_HEADER_COLUMNS = "id,provider,provider_game_id,content_hash"


def read_rows(statement, parameters=()):
    with background_read_connection() as database:
        return [dict(row) for row in database.execute_native(statement, parameters).fetchall()]


def backend_identity(reader):
    return reader("SELECT target_id,workspace_id FROM legacy_learning_identity WHERE id=1")


def imported_game_header(reader, game_id):
    return reader("SELECT " + _GAME_HEADER_COLUMNS + " FROM imported_games WHERE id=%s", (game_id,))


def imported_game(reader, game_id):
    return reader("SELECT " + _GAME_COLUMNS + " FROM imported_games WHERE id=%s", (game_id,))


def repertoire_metadata(reader, repertoire_id):
    return reader("""SELECT repertoire.scope_source_revision,repertoire.canonical_prefix_revision,
        scope.generation catalogue_generation,graph.generation graph_generation,graph.state graph_state,
        integrity.status integrity_status,integrity.scan_status,integrity.scan_generation
        FROM repertoires repertoire CROSS JOIN repertoire_game_scope scope
        LEFT JOIN opening_graph_publications graph ON graph.repertoire_id=repertoire.id
        LEFT JOIN repertoire_integrity_state integrity ON integrity.repertoire_id=repertoire.id
        WHERE repertoire.id=%s AND scope.id=1""", (repertoire_id,))


def delivery_receipt(reader, workspace_id, delivery_id):
    return reader("SELECT payload_hash,response_json FROM remediation_deliveries "
        "WHERE workspace_id=%s AND delivery_id=%s", (workspace_id, delivery_id))


def accepted_game_head(reader, workspace_id, game_id):
    return reader("SELECT game_revision FROM remediation_game_heads WHERE workspace_id=%s AND game_id=%s",
                  (workspace_id, game_id))
