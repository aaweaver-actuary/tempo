"""Versioned comparison storage; computation and publication decisions stay outside."""


def imported_game(database, game_id):
    return database.execute(
        "SELECT id,color,start_fen,moves_json,played_at FROM imported_games WHERE id=?",
        (game_id,),
    ).fetchone()


def stage_match_values(database, values):
    """Write the existing ordered game/repertoire match storage fields."""
    database.execute(
        """INSERT INTO game_repertoire_matches_staged(
             game_id,derivation_version,repertoire_id,is_primary,classification,
             matched_player_decisions,repertoire_opportunities,deepest_covered_ply,
             first_player_deviation_ply,first_player_deviation_fen,
             first_player_deviation_expected_json,first_player_deviation_actual_uci,
             deviation_card_id,first_opponent_gap_ply,out_of_book_ply,
             timeline_json,updated_at,canonical_prefix_revision)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(game_id,derivation_version,repertoire_id) DO UPDATE SET
             is_primary=excluded.is_primary,classification=excluded.classification,
             matched_player_decisions=excluded.matched_player_decisions,
             repertoire_opportunities=excluded.repertoire_opportunities,
             deepest_covered_ply=excluded.deepest_covered_ply,
             first_player_deviation_ply=excluded.first_player_deviation_ply,
             first_player_deviation_fen=excluded.first_player_deviation_fen,
             first_player_deviation_expected_json=excluded.first_player_deviation_expected_json,
             first_player_deviation_actual_uci=excluded.first_player_deviation_actual_uci,
             deviation_card_id=excluded.deviation_card_id,
             first_opponent_gap_ply=excluded.first_opponent_gap_ply,
             out_of_book_ply=excluded.out_of_book_ply,
             timeline_json=excluded.timeline_json,updated_at=excluded.updated_at,
             canonical_prefix_revision=excluded.canonical_prefix_revision""",
        values,
    )


def stage_decision_event_values(database, values):
    """Write one existing primary-decision event's ordered storage fields."""
    database.execute(
        """INSERT INTO repertoire_decision_events_staged(
             id,game_id,derivation_version,repertoire_id,card_id,ply,fen_key,
             expected_uci,actual_uci,outcome,played_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(game_id,derivation_version,id) DO UPDATE SET
             card_id=excluded.card_id,expected_uci=excluded.expected_uci,
             actual_uci=excluded.actual_uci,outcome=excluded.outcome,
             updated_at=excluded.updated_at""",
        values,
    )


def record_repertoire_scope(database, game_id, scope_generation):
    database.execute("UPDATE imported_games SET repertoire_scope_generation=? WHERE id=?", (scope_generation, game_id))


def staged_match_count(database, game_id, version):
    return database.execute(
        "SELECT COUNT(*) FROM game_repertoire_matches_staged "
        "WHERE game_id=? AND derivation_version=?", (game_id, version),
    ).fetchone()[0]


def staged_decision_event_count(database, game_id, version):
    return database.execute(
        "SELECT COUNT(*) FROM repertoire_decision_events_staged "
        "WHERE game_id=? AND derivation_version=?", (game_id, version),
    ).fetchone()[0]


def stage_comparison_values(database, values):
    """Write the existing ordered comparison-summary storage fields."""
    database.execute(
        """INSERT INTO repertoire_comparisons_staged(
             game_id,derivation_version,repertoire_id,classification,
             divergence_ply,divergence_fen,expected_json,actual_uci,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(game_id,derivation_version) DO UPDATE SET
             repertoire_id=excluded.repertoire_id,
             classification=excluded.classification,
             divergence_ply=excluded.divergence_ply,
             divergence_fen=excluded.divergence_fen,
             expected_json=excluded.expected_json,
             actual_uci=excluded.actual_uci,updated_at=excluded.updated_at""",
        values,
    )


def publish_repertoire_generation(database, game_id, version):
    database.execute(
        "UPDATE game_derivation_jobs SET completed_phases=2,"
        "phase='refreshing_findings',published_repertoire_version=? "
        "WHERE game_id=? AND derivation_version=?",
        (version, game_id, version),
    )


def restart_changed_source(database, game_id, version, new_version):
    database.execute(
        "UPDATE game_derivation_jobs SET derivation_version=?,"
        "phase='comparing_repertoire' WHERE game_id=? AND derivation_version=?",
        (new_version, game_id, version),
    )
