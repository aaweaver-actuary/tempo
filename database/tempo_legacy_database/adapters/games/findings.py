"""Finding curation persistence; HTTP/application decisions remain outside."""


def locked_finding(database, finding_id):
    return database.execute_native(
        "SELECT f.*,g.adaptive_excluded FROM current_game_findings f "
        "JOIN imported_games g ON g.id=f.game_id WHERE f.id=%s FOR UPDATE OF f,g",
        (finding_id,),
    ).fetchone()


def defer_finding(database, finding_id, review_after, now):
    database.execute(
        "UPDATE game_findings SET review_after=?,updated_at=? WHERE id=?",
        (review_after, now, finding_id),
    )


def ignore_finding(database, finding_id, now):
    database.execute(
        "UPDATE game_findings SET status='ignored',review_after=NULL,updated_at=? WHERE id=?",
        (now, finding_id),
    )


def linked_miss_event(database, finding):
    return database.execute(
        """SELECT id FROM current_repertoire_decision_events repertoire_decision_events
               WHERE game_id=? AND repertoire_id=? AND ply=? AND card_id=? AND outcome='miss'""",
        (finding["game_id"], finding["repertoire_id"], finding["ply"], finding["card_id"]),
    ).fetchone()


def set_finding_decision(database, finding_id, decision, now):
    database.execute(
        "UPDATE game_findings SET status=?,updated_at=? WHERE id=?",
        (decision, now, finding_id),
    )
