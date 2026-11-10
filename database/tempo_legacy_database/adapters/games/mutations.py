"""Existing game mutation queries, without command/HTTP decisions."""


def lock_game(database, game_id):
    return database.execute(
        "SELECT id FROM imported_games WHERE id=? FOR UPDATE", (game_id,),
    ).fetchone()


def set_adaptive_exclusion(database, game_id, excluded):
    database.execute(
        "UPDATE imported_games SET adaptive_excluded=? WHERE id=?",
        (int(excluded), game_id),
    )
