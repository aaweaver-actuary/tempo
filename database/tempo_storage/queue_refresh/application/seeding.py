"""Existing legacy queue seed facts and writes; no allowance or admission policy."""



def opening_allowance(db):
    return db.execute("SELECT new_cards_per_day FROM settings WHERE id=1").fetchone()[
        0
    ]


def maximum_position(db, day):
    return db.execute(
        'SELECT COALESCE(MAX(position),-1) FROM daily_queue WHERE queue_date=%s', (day,)
    ).fetchone()[0]


def due_cards(db, day):
    from tempo_storage.queue_refresh.eligibility import position_due_exclusion
    position_exclusion = position_due_exclusion(db).replace('c.', 'cards.')
    return db.execute(
        "SELECT id FROM cards WHERE due_date<=%s AND state IN ('learning','mature') AND archived=0 AND COALESCE(pending_validation,0)=0\n           AND (cards.content_type!='opening' OR EXISTS(SELECT 1 FROM repertoires rr\n                      WHERE (rr.id=cards.repertoire_id OR EXISTS(SELECT 1 FROM repertoire_cards rc WHERE rc.card_id=cards.id AND rc.repertoire_id=rr.id))\n                        AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block\n                                       WHERE block.repertoire_id=rr.id AND block.card_id=cards.id)))\n           AND (cards.content_type!='study_exercise' OR (\n             EXISTS(SELECT 1 FROM study_exercises exercise JOIN studies study ON study.id=exercise.study_id\n                    WHERE exercise.id=cards.study_exercise_id AND exercise.status='published' AND study.archived=0)\n             AND NOT EXISTS(SELECT 1 FROM study_sibling_burials burial\n                            WHERE burial.exercise_id=cards.study_exercise_id AND burial.study_day=%s)))\n           AND id NOT IN(SELECT card_id FROM daily_queue WHERE queue_date=%s)\n           " + position_exclusion + " ORDER BY due_date,id",
        (day, day, day),
    ).fetchall()


def append_due_card(db, day, card_id, position):
    db.execute(
            'INSERT INTO daily_queue(queue_date,card_id,position) VALUES(%s,%s,%s)',
            (day, card_id, position),
        )






def study_allowance(db):
    return db.execute("SELECT study_new_per_day FROM settings WHERE id=1").fetchone()[0]


def admitted_study_count(db, day):
    return db.execute(
        "SELECT COUNT(*) FROM daily_queue q JOIN cards c ON c.id=q.card_id\n           WHERE q.queue_date=%s AND c.content_type='study_exercise' AND c.state='new'\n             AND q.status IN ('queued','complete','buried')", (day,),
    ).fetchone()[0]


def next_position(db, day):
    return db.execute('SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=%s', (day,)).fetchone()[0]


def new_study_cards(db, day, remaining_studies):
    return db.execute(
            "SELECT c.id FROM cards c\n               JOIN study_exercises e ON e.id=c.study_exercise_id\n               JOIN studies s ON s.id=e.study_id\n               WHERE c.content_type='study_exercise' AND c.state='new' AND c.archived=0\n                 AND c.pending_validation=0 AND c.due_date<=%s AND e.status='published' AND s.archived=0\n                 AND NOT EXISTS(SELECT 1 FROM study_sibling_burials b WHERE b.exercise_id=e.id AND b.study_day=%s)\n                 AND NOT EXISTS(SELECT 1 FROM daily_queue q WHERE q.card_id=c.id AND q.queue_date=%s)\n               ORDER BY e.created_at,e.id LIMIT %s",
            (day, day, day, remaining_studies),
        ).fetchall()


def append_study_card(db, day, card_id, position):
    db.execute("INSERT INTO daily_queue(queue_date,card_id,position,card_bucket,admission_kind) VALUES(%s,%s,%s,'study_exercise','new')",
                       (day, card_id, position))
