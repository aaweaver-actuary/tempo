"""Fixed progress, tactic facts; practice policy stays outside."""


def card_state_rows(database):
    yield from database.execute(
        "SELECT state,COUNT(*) n FROM cards WHERE archived=0 GROUP BY state",
    )


def review_count(database, day):
    return database.execute(
        "SELECT COUNT(*) FROM reviews WHERE date(reviewed_at)=? AND invalidated_at IS NULL",
        (day,),
    ).fetchone()[0]


def clean_card_count(database):
    return database.execute(
        "SELECT COUNT(DISTINCT card_id) FROM reviews WHERE rating='correct' AND guided=0 AND invalidated_at IS NULL",
    ).fetchone()[0]


def queued_card_count(database, day):
    return database.execute(
        """SELECT COUNT(*) FROM daily_queue q JOIN cards c ON c.id=q.card_id
                   WHERE q.queue_date=? AND q.status='queued'
                     AND (c.content_type!='defense' OR (SELECT include_defensive_cards_in_daily_stack FROM settings WHERE id=1)=1)""",
        (day,),
    ).fetchone()[0]


def blocked_count(database, day):
    return database.execute(
        "SELECT COALESCE((SELECT blocked_count FROM queue_projections WHERE queue_date=?),0)",
        (day,),
    ).fetchone()[0]


def tactic_progress_rows(database):
    return database.execute(
        "SELECT deck_id,puzzle_id,clean_pass_at FROM tactic_progress WHERE clean_pass_at IS NOT NULL OR puzzle_id NOT IN(SELECT puzzle_id FROM tactic_introductions) OR puzzle_id IN(SELECT puzzle_id FROM tactic_discovery_attempts) ORDER BY deck_id,puzzle_id",
    ).fetchall()
