"""The shared derivation-generation row lock; phase decisions stay outside."""


def lock_job(database, game_id):
    return database.execute(
        "SELECT derivation_version,completed_phases,status FROM game_derivation_jobs "
        "WHERE game_id=? FOR UPDATE", (game_id,),
    ).fetchone()
