"""Bounded event staging and publication; event/signature policy is application-owned."""

_EVENT_COLUMNS = (
    "id,game_id,analysis_version,classifier_version,ply,kind,motif,"
    "beneficiary_color,created_by_color,outcome,confidence,loss_cp,"
    "best_move_uci,actual_move_uci,principal_variation_json,evidence_json,"
    "created_at,updated_at,derivation_version"
)


def restart_changed_event_source(database, game_id, version, next_version):
    database.execute(
        "UPDATE game_derivation_jobs SET derivation_version=?,completed_phases=0,"
        "phase='indexing_positions' WHERE game_id=? AND derivation_version=?",
        (next_version, game_id, version),
    )


def stage_event(database, event, version):
    database.execute_native(
        f"INSERT INTO gameplay_events_staged({_EVENT_COLUMNS}) "
        f"VALUES({','.join(['%s'] * 19)}) "
        "ON CONFLICT(game_id,derivation_version,id) DO NOTHING",
        (*event, version),
    )


def staged_event_count(database, game_id, version):
    return database.execute(
        "SELECT COUNT(*) FROM gameplay_events_staged "
        "WHERE game_id=? AND derivation_version=?", (game_id, version),
    ).fetchone()[0]


def publish_event_generation(database, game_id, version):
    database.execute(
        "UPDATE game_derivation_jobs SET completed_phases=5,"
        "phase='refreshing_features',published_events_version=? "
        "WHERE game_id=? AND derivation_version=?", (version, game_id, version),
    )
