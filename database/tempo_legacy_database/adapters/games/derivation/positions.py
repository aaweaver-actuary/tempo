"""Position staging and generation publication queries, without chess replay."""


def game_moves(database, game_id):
    return database.execute(
        "SELECT start_fen,moves_json FROM imported_games WHERE id=?", (game_id,),
    ).fetchone()


def stage_position(database, game_id, version, ply, fen_key, move_uci):
    database.execute(
        """INSERT INTO game_position_occurrences_staged(
                   game_id,derivation_version,ply,fen_key,move_uci)
               VALUES(?,?,?,?,?) ON CONFLICT(game_id,derivation_version,ply) DO UPDATE SET
               fen_key=excluded.fen_key,move_uci=excluded.move_uci""",
        (game_id, version, ply, fen_key, move_uci),
    )


def staged_position_count(database, game_id, version):
    return database.execute(
        "SELECT COUNT(*) FROM game_position_occurrences_staged "
        "WHERE game_id=? AND derivation_version=?",
        (game_id, version),
    ).fetchone()[0]


def publish_position_generation(database, game_id, version):
    database.execute(
        "UPDATE game_derivation_jobs SET completed_phases=1,"
        "phase='comparing_repertoire',published_position_version=? "
        "WHERE game_id=? AND derivation_version=?",
        (version, game_id, version),
    )
