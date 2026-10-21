"""Existing legacy queue seed facts and writes; no allowance or admission policy."""

from tempo_legacy_database.adapters.real_game_miss_projection import outstanding_miss_query


def opening_allowance(db):
    return db.execute("SELECT new_cards_per_day FROM settings WHERE id=1").fetchone()[
        0
    ]


def maximum_position(db, day):
    return db.execute(
        "SELECT COALESCE(MAX(position),-1) FROM daily_queue WHERE queue_date=?", (day,)
    ).fetchone()[0]


def due_cards(db, day):
    return db.execute(
        """SELECT id FROM cards WHERE due_date<=? AND state IN ('learning','mature') AND archived=0 AND COALESCE(pending_validation,0)=0
           AND (cards.content_type!='opening' OR EXISTS(SELECT 1 FROM repertoires rr
                      WHERE (rr.id=cards.repertoire_id OR EXISTS(SELECT 1 FROM repertoire_cards rc WHERE rc.card_id=cards.id AND rc.repertoire_id=rr.id))
                        AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block
                                       WHERE block.repertoire_id=rr.id AND block.card_id=cards.id)))
           AND (cards.content_type!='study_exercise' OR (
             EXISTS(SELECT 1 FROM study_exercises exercise JOIN studies study ON study.id=exercise.study_id
                    WHERE exercise.id=cards.study_exercise_id AND exercise.status='published' AND study.archived=0)
             AND NOT EXISTS(SELECT 1 FROM study_sibling_burials burial
                            WHERE burial.exercise_id=cards.study_exercise_id AND burial.study_day=?)))
           AND id NOT IN(SELECT card_id FROM daily_queue WHERE queue_date=?) ORDER BY due_date,id""",
        (day, day, day),
    ).fetchall()


def append_due_card(db, day, card_id, position):
    db.execute(
            "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,?,?)",
            (day, card_id, position),
        )


def outstanding_studied_cards(db):
    return db.execute(outstanding_miss_query(
        db, additional_where="AND card.state IN ('learning','mature') ORDER BY event.card_id",
    )).fetchall()


def stale_miss_priority(db, day, miss_reason):
    return db.execute("SELECT DISTINCT card_id FROM daily_queue WHERE queue_date=? AND gameplay_priority_reason=?", (day, miss_reason)).fetchall()


def study_allowance(db):
    return db.execute("SELECT study_new_per_day FROM settings WHERE id=1").fetchone()[0]


def admitted_study_count(db, day):
    return db.execute(
        """SELECT COUNT(*) FROM daily_queue q JOIN cards c ON c.id=q.card_id
           WHERE q.queue_date=? AND c.content_type='study_exercise' AND c.state='new'
             AND q.status IN ('queued','complete','buried')""", (day,),
    ).fetchone()[0]


def next_position(db, day):
    return db.execute("SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?", (day,)).fetchone()[0]


def new_study_cards(db, day, remaining_studies):
    return db.execute(
            """SELECT c.id FROM cards c
               JOIN study_exercises e ON e.id=c.study_exercise_id
               JOIN studies s ON s.id=e.study_id
               WHERE c.content_type='study_exercise' AND c.state='new' AND c.archived=0
                 AND c.pending_validation=0 AND c.due_date<=? AND e.status='published' AND s.archived=0
                 AND NOT EXISTS(SELECT 1 FROM study_sibling_burials b WHERE b.exercise_id=e.id AND b.study_day=?)
                 AND NOT EXISTS(SELECT 1 FROM daily_queue q WHERE q.card_id=c.id AND q.queue_date=?)
               ORDER BY e.created_at,e.id LIMIT ?""",
            (day, day, day, remaining_studies),
        ).fetchall()


def append_study_card(db, day, card_id, position):
    db.execute("INSERT INTO daily_queue(queue_date,card_id,position,card_bucket,admission_kind) VALUES(?,?,?,'study_exercise','new')",
                       (day, card_id, position))
