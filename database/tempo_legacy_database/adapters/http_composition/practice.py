"""Tactic progress and human review counts from the borrowed connection."""


def review_count(database, day):
    return database.execute(
        "SELECT COUNT(*) FROM reviews WHERE date(reviewed_at)=? AND invalidated_at IS NULL "
        "AND source_kind!='game_lapse'",
        (day,),
    ).fetchone()[0]


def tactic_progress_rows(database):
    return database.execute(
        "SELECT deck_id,puzzle_id,clean_pass_at FROM tactic_progress WHERE clean_pass_at IS NOT NULL OR puzzle_id NOT IN(SELECT puzzle_id FROM tactic_introductions) OR puzzle_id IN(SELECT puzzle_id FROM tactic_discovery_attempts) ORDER BY deck_id,puzzle_id",
    ).fetchall()
