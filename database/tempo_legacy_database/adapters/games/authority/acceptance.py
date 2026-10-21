"""Existing exact-source acceptance locks/projections in the command transaction."""

from .headers import _CARD_COLUMNS, _GAME_COLUMNS, _GAME_HEADER_COLUMNS


def lock_imported_source(database, provider, provider_game_id):
    database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        ("tempo:imported-game:" + provider + ":" + provider_game_id,))


def lock_prefix_reservations(database):
    database.execute_native("SELECT pg_advisory_xact_lock_shared(hashtextextended('tempo:prefix-transition:reservations',0))")


def lock_opening_graph(database, repertoire_id):
    database.execute_native("SELECT pg_advisory_xact_lock_shared(hashtextextended(%s,0))",
        ("tempo:opening-graph:" + repertoire_id,))


def retained_contribution(database, workspace_id, obligation_id):
    return database.execute_native("""SELECT contribution.card_id,contribution.payload_json,
                contribution.evidence_id,contribution.game_id,contribution.obligation_revision
            FROM remediation_obligation_heads head JOIN remediation_contributions contribution
            USING(workspace_id,obligation_id,obligation_revision)
            WHERE head.workspace_id=%s AND head.obligation_id=%s""",
        (workspace_id, obligation_id)).fetchone()


def lock_card_edit(database, card_id):
    database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("tempo:card-edit:" + card_id,))


def lock_card_row(database, card_id):
    database.execute_native("SELECT id FROM cards WHERE id=%s FOR UPDATE", (card_id,))


def backend_identity(database):
    return database.execute_native("SELECT target_id,workspace_id FROM legacy_learning_identity WHERE id=1 FOR SHARE").fetchone()


def imported_game_header(database, game_id):
    return database.execute_native("SELECT " + _GAME_HEADER_COLUMNS +
        " FROM imported_games WHERE id=%s FOR SHARE", (game_id,)).fetchone()


def repertoire_revisions(database, repertoire_id):
    return database.execute_native("SELECT scope_source_revision,canonical_prefix_revision FROM repertoires WHERE id=%s FOR SHARE",
        (repertoire_id,)).fetchone()


def scope_generation(database):
    return database.execute_native("SELECT generation FROM repertoire_game_scope WHERE id=1 FOR SHARE").fetchone()


def graph_generation(database, repertoire_id):
    return database.execute_native("SELECT generation,state FROM opening_graph_publications WHERE repertoire_id=%s FOR SHARE",
        (repertoire_id,)).fetchone()


def integrity_status(database, repertoire_id):
    return database.execute_native("SELECT status,scan_status,scan_generation FROM repertoire_integrity_state WHERE repertoire_id=%s FOR SHARE",
        (repertoire_id,)).fetchone()


def imported_game(database, game_id):
    return database.execute_native("SELECT " + _GAME_COLUMNS + " FROM imported_games WHERE id=%s FOR SHARE",
        (game_id,)).fetchone()


def physical_card(database, card_id):
    return database.execute_native("SELECT " + _CARD_COLUMNS + " FROM cards WHERE id=%s", (card_id,)).fetchone()


def card_memberships(database, card_id):
    return database.execute_native("SELECT repertoire_id,canonical_route_source FROM repertoire_cards "
        "WHERE card_id=%s ORDER BY repertoire_id LIMIT 101 FOR SHARE", (card_id,)).fetchall()


def deleted_card(database, card_id):
    return database.execute_native("SELECT 1 FROM deleted_cards WHERE card_id=%s", (card_id,)).fetchone()


def blocked_card(database, card_id):
    return database.execute_native("SELECT 1 FROM repertoire_integrity_card_blocks WHERE card_id=%s", (card_id,)).fetchone()
