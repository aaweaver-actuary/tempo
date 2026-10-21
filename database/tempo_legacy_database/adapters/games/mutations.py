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


def exclude_pending_findings(database, game_id, now):
    database.execute(
        "UPDATE game_findings SET status='excluded',updated_at=? "
        "WHERE game_id=? AND status='pending'", (now, game_id),
    )


def restore_excluded_findings(database, game_id, now):
    database.execute(
        "UPDATE game_findings SET status='pending',updated_at=? "
        "WHERE game_id=? AND status='excluded'", (now, game_id),
    )


def restart_derivation(database, game_id, now):
    database.execute(
        """INSERT INTO game_derivation_jobs(game_id,status,updated_at)
           VALUES(?,'queued',?) ON CONFLICT(game_id) DO UPDATE SET
           status='queued',last_error=NULL,
           derivation_version=game_derivation_jobs.derivation_version+1,
           completed_phases=0,next_attempt_at=NULL,updated_at=excluded.updated_at""",
        (game_id, now),
    )
    return database.execute(
        "SELECT derivation_version FROM game_derivation_jobs WHERE game_id=?",
        (game_id,),
    ).fetchone()


def affected_repertoire_ids(database, game_id):
    return [row[0] for row in database.execute(
        "SELECT repertoire_id FROM current_game_repertoire_matches game_repertoire_matches WHERE game_id=?",
        (game_id,),
    )]


def analyzed_game(database, game_id):
    return database.execute(
        "SELECT analysis_version FROM imported_games WHERE id=? AND analysis_state='ready'",
        (game_id,),
    ).fetchone()
