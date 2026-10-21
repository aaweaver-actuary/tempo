"""Gameplay-event input projections and fixed publication operations."""


def source_game(database, game_id):
    return database.execute(
        """SELECT id,color,start_fen,moves_json,analysis_version,analysis_evidence_version
                  FROM imported_games WHERE id=?""",
        (game_id,),
    ).fetchone()


def source_analysis(database, game_id):
    return database.execute(
        "SELECT * FROM game_move_analysis WHERE game_id=? ORDER BY ply",
        (game_id,),
    ).fetchall()


def source_candidates(database, game_id):
    return database.execute(
        "SELECT * FROM game_move_analysis_candidates WHERE game_id=? ORDER BY ply,rank",
        (game_id,),
    ).fetchall()


def event_threshold_settings(database):
    return database.execute(
        "SELECT major_mistake_cp,engine_line_window_cp FROM settings WHERE id=1"
    ).fetchone()


def delete_classifier_events(database, game_id, classifier_version):
    database.execute(
        "DELETE FROM gameplay_events WHERE game_id=? AND classifier_version=?",
        (game_id, classifier_version),
    )


def insert_event_values(database, computed_events):
    database.executemany(
        """INSERT INTO gameplay_events(
                   id,game_id,analysis_version,classifier_version,ply,kind,motif,
                   beneficiary_color,created_by_color,outcome,confidence,loss_cp,
                   best_move_uci,actual_move_uci,principal_variation_json,evidence_json,
                   created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        computed_events,
    )
