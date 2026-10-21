"""Fixed authored Study command queries; application grading and queue decisions stay outside."""


def record(database, table_name, identifier):
    if table_name not in {"studies", "study_chapters", "study_positions", "study_exercises"}:
        raise AssertionError("Unknown study table")
    return database.execute(f"SELECT * FROM {table_name} WHERE id=?", (identifier,)).fetchone()


def insert_study(database, identifier, title, description, source_json, now):
    database.execute(
        "INSERT INTO studies(id,title,description,source_json,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?)",
        (identifier, title, description,
         source_json, now, now),
    )


def locked_study_exists(database, study_id):
    return database.execute("SELECT 1 FROM studies WHERE id=? FOR UPDATE", (study_id,)).fetchone()


def update_study(database, title, description, source_json, updated_at, study_id):
    database.execute(
        "UPDATE studies SET title=?,description=?,source_json=?,updated_at=? WHERE id=?",
        (
            title,
            description,
            source_json,
            updated_at,
            study_id,
        ),
    )


def locked_study_identity(database, study_id):
    return database.execute(
        "SELECT id FROM studies WHERE id=? FOR UPDATE", (study_id,),
    ).fetchone()


def archive_study(database, updated_at, study_id):
    database.execute(
        "UPDATE studies SET archived=1,updated_at=? WHERE id=?",
        (updated_at, study_id),
    )


def archive_study_cards(database, study_id):
    database.execute(
        """UPDATE cards SET archived=1
           WHERE study_exercise_id IN (SELECT id FROM study_exercises WHERE study_id=?)""",
        (study_id,),
    )


def unarchive_study(database, updated_at, study_id):
    database.execute(
        "UPDATE studies SET archived=0,updated_at=? WHERE id=?",
        (updated_at, study_id),
    )


def locked_exercise(database, exercise_id):
    return database.execute(
        "SELECT * FROM study_exercises WHERE id=? FOR UPDATE", (exercise_id,),
    ).fetchone()


def suspend_exercise_cards(database, exercise_id):
    database.execute(
        "UPDATE cards SET pending_validation=1 WHERE study_exercise_id=? AND archived=0",
        (exercise_id,),
    )


def block_exercise_queue(database, exercise_id):
    database.execute(
        """UPDATE daily_queue SET status='blocked' WHERE card_id IN
           (SELECT id FROM cards WHERE study_exercise_id=?) AND status='queued'""",
        (exercise_id,),
    )


def resume_exercise_cards(database, exercise_id):
    database.execute(
        "UPDATE cards SET pending_validation=0 WHERE study_exercise_id=? AND archived=0",
        (exercise_id,),
    )


def archive_exercise(database, exercise_id):
    database.execute("UPDATE study_exercises SET status='archived' WHERE id=?", (exercise_id,))


def archive_exercise_cards(database, exercise_id):
    database.execute("UPDATE cards SET archived=1 WHERE study_exercise_id=?", (exercise_id,))


def locked_study(database, study_id):
    return database.execute("SELECT * FROM studies WHERE id=? FOR UPDATE", (study_id,)).fetchone()


def source_validity(database, source_id):
    return database.execute(
        "SELECT chapter_id,valid FROM study_sources WHERE id=?", (source_id,),
    ).fetchone()


def insert_exercise(
    database,
    exercise_id,
    study_id,
    position_id,
    sibling_group,
    source_json,
    point_value,
    created_at,
):
    database.execute(
        """INSERT INTO study_exercises(id,study_id,position_id,sibling_group,source_json,
           point_value,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)""",
        (exercise_id, study_id, position_id, sibling_group,
         source_json,
         point_value, created_at, created_at),
    )


def insert_revision(database, exercise_id, revision, specification_json, digest, created_at):
    database.execute(
        "INSERT INTO study_exercise_revisions VALUES(?,?,?,?,?)",
        (exercise_id, revision, specification_json, digest, created_at),
    )


def exercise_revision(database, exercise_id, revision):
    return database.execute(
        "SELECT specification_json FROM study_exercise_revisions WHERE exercise_id=? AND revision=?",
        (exercise_id, revision),
    ).fetchone()


def active_card_identity(database, exercise_id):
    return database.execute(
        "SELECT id FROM cards WHERE study_exercise_id=? AND archived=0", (exercise_id,),
    ).fetchone()


def insert_enrolled_card(database, card_id, fen, due_date, exercise_id):
    database.execute(
        """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
           content_type,study_exercise_id) VALUES(?,NULL,'exercise',?,'[]','new',?,'study_exercise',?)""",
        (card_id, fen, due_date, exercise_id),
    )


def publish_exercise(database, exercise_id):
    database.execute("UPDATE study_exercises SET status='published' WHERE id=?", (exercise_id,))


def locked_active_card(database, exercise_id):
    return database.execute(
        "SELECT * FROM cards WHERE study_exercise_id=? AND archived=0 FOR UPDATE",
        (exercise_id,),
    ).fetchone()


def queued_exercise_entry(database, card_id, queue_date):
    return database.execute(
        """SELECT id FROM daily_queue WHERE card_id=? AND queue_date=? AND status='queued'
           ORDER BY cycle DESC LIMIT 1""",
        (card_id, queue_date),
    ).fetchone()


def next_exercise_cycle(database, card_id, queue_date):
    return database.execute(
        "SELECT COALESCE(MAX(cycle),-1)+1 FROM daily_queue WHERE card_id=? AND queue_date=?",
        (card_id, queue_date),
    ).fetchone()[0]


def next_queue_position(database, queue_date):
    return database.execute(
        "SELECT COALESCE(MAX(position),-1)+1 FROM daily_queue WHERE queue_date=?",
        (queue_date,),
    ).fetchone()[0]


def append_explicit_entry(database, queue_date, card_id, cycle, position):
    return database.execute(
        """INSERT INTO daily_queue(queue_date,card_id,cycle,position,admission_kind,card_bucket)
           VALUES(?,?,?,?,'explicit','study_exercise') RETURNING id""",
        (queue_date, card_id, cycle, position),
    ).fetchone()[0]


def publish_revision(database, revision, updated_at, exercise_id):
    database.execute(
        "UPDATE study_exercises SET current_revision=?,updated_at=? WHERE id=?",
        (revision, updated_at, exercise_id),
    )


def update_exercise_source(database, source_json, exercise_id):
    database.execute(
            "UPDATE study_exercises SET source_json=? WHERE id=?",
            (source_json, exercise_id),
        )


def update_sibling_group(database, sibling_group, exercise_id):
    database.execute(
            "UPDATE study_exercises SET sibling_group=? WHERE id=?",
            (sibling_group, exercise_id),
        )


def update_point_value(database, point_value, exercise_id):
    database.execute(
            "UPDATE study_exercises SET point_value=? WHERE id=?",
            (point_value, exercise_id),
        )


def archive_card(database, card_id):
    database.execute("UPDATE cards SET archived=1 WHERE id=?", (card_id,))


def block_card_queue(database, card_id):
    database.execute(
                "UPDATE daily_queue SET status='blocked' WHERE card_id=? AND status='queued'",
                (card_id,),
            )


def insert_reset_card(database, card_id, fen, due_date, exercise_id, revision):
    database.execute(
                """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
                   content_type,study_exercise_id,revision)
                   VALUES(?,NULL,'exercise',?,'[]','new',?,'study_exercise',?,?)""",
                (card_id, fen, due_date,
                 exercise_id, revision),
            )


def update_card_revision(database, revision, exercise_id):
    database.execute(
            "UPDATE cards SET revision=? WHERE study_exercise_id=? AND archived=0",
            (revision, exercise_id),
        )


def block_revised_exercise_queue(database, exercise_id):
    database.execute(
            """UPDATE daily_queue SET status='blocked' WHERE card_id IN
               (SELECT id FROM cards WHERE study_exercise_id=?) AND status='queued'""",
            (exercise_id,),
        )


def next_chapter_position(database, study_id):
    return database.execute(
        "SELECT COALESCE(MAX(position),-1)+1 FROM study_chapters WHERE study_id=?",
        (study_id,),
    ).fetchone()[0]


def insert_chapter(database, chapter_id, study_id, title, description, position):
    database.execute(
        "INSERT INTO study_chapters(id,study_id,title,description,position) VALUES(?,?,?,?,?)",
        (chapter_id, study_id, title, description, position),
    )


def lock_study_parent(database, study_id):
    database.execute("SELECT id FROM studies WHERE id=? FOR UPDATE", (study_id,))


def chapter_identifiers(database, study_id):
    return [row[0] for row in database.execute(
        "SELECT id FROM study_chapters WHERE study_id=?", (study_id,),
    )]


def set_chapter_position(database, position, chapter_id):
    database.execute("UPDATE study_chapters SET position=? WHERE id=?", (position, chapter_id))


def rename_chapter(database, title, description, chapter_id):
    database.execute(
        "UPDATE study_chapters SET title=?,description=? WHERE id=?",
        (title, description, chapter_id),
    )


def source_chapter(database, source_id):
    return database.execute(
            "SELECT chapter_id FROM study_sources WHERE id=?", (source_id,),
        ).fetchone()


def insert_link(
    database,
    link_id,
    study_id,
    source_position_id,
    target_position_id,
    target_exercise_id,
    relation,
):
    database.execute(
        "INSERT INTO study_links VALUES(?,?,?,?,?,?)",
        (link_id, study_id, source_position_id, target_position_id,
         target_exercise_id, relation),
    )


def locked_chapter_study(database, chapter_id):
    return database.execute(
        "SELECT study_id FROM study_chapters WHERE id=? FOR UPDATE", (chapter_id,),
    ).fetchone()


def matching_source_versions(database, chapter_id, digest, source_group_id):
    source_group_filter = " AND source_group_id=?" if source_group_id else ""
    return database.execute(
        "SELECT source_group_id,version,record_index FROM study_sources "
        f"WHERE chapter_id=? AND sha256=?{source_group_filter} ORDER BY version DESC",
        (chapter_id, digest)
        + ((source_group_id,) if source_group_id else ()),
    ).fetchall()


def latest_source_version(database, chapter_id, source_group_id):
    return database.execute(
            "SELECT MAX(version) FROM study_sources WHERE chapter_id=? AND source_group_id=?",
            (chapter_id, source_group_id),
        ).fetchone()[0]


def insert_source(
    database,
    source_id,
    chapter_id,
    source_group_id,
    version,
    raw_pgn,
    digest,
    filename,
    record_index,
    headers_json,
    diagnostics_json,
    valid,
    created_at,
):
    database.execute(
            """INSERT INTO study_sources(id,chapter_id,source_group_id,version,raw_pgn,sha256,
               filename,record_index,headers_json,diagnostics_json,valid,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (source_id, chapter_id, source_group_id, version,
             raw_pgn, digest, filename, record_index,
             headers_json,
             diagnostics_json,
             valid, created_at),
        )


def insert_position(
    database,
    position_id,
    source_id,
    parent_id,
    child_index,
    move_uci,
    fen,
    history_json,
    comment,
    starting_comment,
    nags_json,
    arrows_json,
    squares_json,
    node_path,
    valid,
):
    database.execute(
                """INSERT INTO study_positions(id,source_id,parent_id,child_index,move_uci,fen,
                   history_json,comment,starting_comment,nags_json,arrows_json,squares_json,node_path,valid)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (position_id, source_id, parent_id, child_index,
                 move_uci, fen,
                 history_json,
                 comment, starting_comment,
                 nags_json,
                 arrows_json,
                 squares_json,
                 node_path, valid),
            )
