"""Existing current-miss cursor and derivation phase write."""


def next_miss_event(database, game_id, previous_ply, previous_id):
    return database.execute(
        "SELECT id,ply FROM current_repertoire_decision_events repertoire_decision_events "
        "WHERE game_id=? AND outcome='miss' AND (ply,id)>(?,?) "
        "ORDER BY ply,id LIMIT 1",
        (game_id, previous_ply, previous_id),
    ).fetchone()


def advance_miss_phase(database, game_id, version):
    database.execute(
        "UPDATE game_derivation_jobs SET completed_phases=4,phase='refreshing_events' "
        "WHERE game_id=? AND derivation_version=?", (game_id, version),
    )
