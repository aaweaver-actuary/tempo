"""Fixed authored Study HTTP/fallback queries; routing and policy remain in application."""

from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.adapters.commands.studies.storage import (
    active_card_identity,
    archive_card,
    archive_exercise,
    archive_exercise_cards,
    archive_study,
    block_card_queue,
    chapter_identifiers,
    insert_chapter,
    insert_link,
    insert_revision,
    insert_study,
    latest_source_version,
    next_chapter_position,
    next_exercise_cycle,
    next_queue_position,
    publish_exercise,
    publish_revision,
    record,
    rename_chapter,
    resume_exercise_cards,
    set_chapter_position,
    source_validity,
    suspend_exercise_cards,
    unarchive_study,
    update_card_revision,
    update_exercise_source,
    update_point_value,
    update_sibling_group,
    update_study,
)
from tempo_legacy_database.adapters.study_attempts.storage import (
    active_review_card,
    attempt,
    exercise_revision,
    latest_effective_review_id,
    queued_review_entry,
)


def native_commands_enabled():
    return postgres_store.configured()


def revision_record(database, exercise_id, revision):
    return database.execute(
        "SELECT * FROM study_exercise_revisions WHERE exercise_id=? AND revision=?",
        (exercise_id, revision),
    ).fetchone()


def exercise_card_status(database, exercise_id):
    return database.execute(
        "SELECT id,state,due_date FROM cards WHERE study_exercise_id=? AND archived=0",
        (exercise_id,),
    ).fetchone()


def position_fen(database, position_id):
    return database.execute("SELECT fen FROM study_positions WHERE id=?",
                                        (position_id,)).fetchone()[0]


def study_rows(database):
    rows = database.execute(
            "SELECT * FROM studies ORDER BY archived,title,id"
        )
    return map(lambda row: row, rows)


def chapter_rows(database, study_id):
    rows = database.execute(
            "SELECT * FROM study_chapters WHERE study_id=? ORDER BY position,id", (study_id,)
        )
    return map(lambda row: row, rows)


def archive_study_cards(database, study_id):
    database.execute("UPDATE cards SET archived=1 WHERE study_exercise_id IN (SELECT id FROM study_exercises WHERE study_id=?)", (study_id,))


def source_rows(database, chapter_id):
    rows = database.execute(
            "SELECT id,source_group_id,version,filename,record_index,headers_json,diagnostics_json,valid,created_at FROM study_sources WHERE chapter_id=? ORDER BY created_at,record_index",
            (chapter_id,),
        )
    return map(lambda row: row, rows)


def position_rows(database, chapter_id):
    rows = database.execute(
            "SELECT * FROM study_positions WHERE source_id IN (SELECT id FROM study_sources WHERE chapter_id=?) ORDER BY source_id,node_path",
            (chapter_id,),
        )
    return map(lambda row: row, rows)


def chapter_exercise_rows(database, chapter_id):
    rows = database.execute(
            "SELECT id FROM study_exercises WHERE position_id IN (SELECT id FROM study_positions WHERE source_id IN (SELECT id FROM study_sources WHERE chapter_id=?)) ORDER BY created_at,id",
            (chapter_id,),
        )
    return map(lambda row: row, rows)


def link_rows(database, study_id):
    rows = database.execute("SELECT * FROM study_links WHERE study_id=?", (study_id,))
    return map(lambda row: row, rows)


def source_chapter_identity(database, source_id):
    return database.execute("SELECT chapter_id FROM study_sources WHERE id=?", (source_id,)).fetchone()[0]


def source_version_rows(database, chapter_id, source_group_id):
    rows = database.execute(
            "SELECT source_group_id,version,sha256 FROM study_sources WHERE chapter_id=? AND source_group_id=? ORDER BY version DESC",
            (chapter_id, source_group_id),
        )
    return map(lambda row: row, rows)


def matching_source_versions(database, chapter_id, digest, source_group_id):
    return database.execute(
            """SELECT source_group_id,version,record_index FROM study_sources
               WHERE chapter_id=? AND sha256=? AND (? IS NULL OR source_group_id=?)
               ORDER BY version DESC""",
            (chapter_id, digest, source_group_id, source_group_id),
        ).fetchall()


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
                 headers_json, diagnostics_json, valid, created_at),
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
                     move_uci, fen, history_json, comment,
                     starting_comment, nags_json, arrows_json,
                     squares_json, node_path, valid),
                )


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
            """INSERT INTO study_exercises(id,study_id,position_id,sibling_group,source_json,point_value,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (exercise_id, study_id, position_id, sibling_group,
             source_json, point_value, created_at, created_at),
        )


def active_card(database, exercise_id):
    return database.execute("SELECT * FROM cards WHERE study_exercise_id=? AND archived=0", (exercise_id,)).fetchone()


def insert_reset_card(database, card_id, fen, due_date, exercise_id, revision):
    database.execute(
                    """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
                       content_type,study_exercise_id,revision) VALUES(?,NULL,'exercise',?,'[]','new',?,'study_exercise',?,?)""",
                    (card_id, fen, due_date, exercise_id, revision),
                )


def block_exercise_queue(database, exercise_id):
    database.execute("UPDATE daily_queue SET status='blocked' WHERE card_id IN (SELECT id FROM cards WHERE study_exercise_id=?) AND status='queued'", (exercise_id,))


def insert_enrolled_card(database, card_id, fen, due_date, exercise_id):
    database.execute(
            """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
               content_type,study_exercise_id) VALUES(?,NULL,'exercise',?,'[]','new',?,'study_exercise',?)""",
            (card_id, fen, due_date, exercise_id),
        )


def queued_exercise_entry(database, card_id, queue_date):
    return database.execute("SELECT id FROM daily_queue WHERE card_id=? AND queue_date=? AND status='queued' ORDER BY cycle DESC LIMIT 1",
                                    (card_id, queue_date)).fetchone()


def append_explicit_entry(database, queue_date, card_id, cycle, position):
    return database.execute("""INSERT INTO daily_queue(queue_date,card_id,cycle,position,admission_kind,card_bucket)
                            VALUES(?,?,?,?,'explicit','study_exercise') RETURNING id""",
                         (queue_date, card_id, cycle, position)).fetchone()[0]


def insert_attempt(
    database,
    attempt_id,
    exercise_id,
    revision,
    card_id,
    queue_entry_id,
    cycle,
    context,
    answer_json,
    answer_hash,
    assessment_json,
    assessment_method,
    grader_version,
    hint_seen,
    solution_seen,
    started_at,
    committed_at,
):
    database.execute(
            """INSERT INTO study_attempts(id,exercise_id,revision,card_id,queue_entry_id,cycle,context,
               answer_json,answer_hash,assessment_json,assessment_method,grader_version,hint_seen,
               solution_seen_before_answer,started_at,committed_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (attempt_id, exercise_id, revision, card_id, queue_entry_id,
             cycle, context, answer_json, answer_hash, assessment_json,
             assessment_method,
             grader_version, hint_seen, solution_seen,
             started_at, committed_at),
        )


def exercise_attempt(database, attempt_id, exercise_id):
    return database.execute("SELECT * FROM study_attempts WHERE id=? AND exercise_id=?", (attempt_id, exercise_id)).fetchone()


def attempt_revision(database, attempt_id, exercise_id):
    return database.execute("SELECT revision FROM study_attempts WHERE id=? AND exercise_id=?", (attempt_id, exercise_id)).fetchone()


def chapter_summary_rows(database, study_id):
    rows = database.execute(
            """SELECT chapter.id,chapter.title,COUNT(DISTINCT exercise.id) exercise_count,
                      COUNT(DISTINCT attempt.id) attempts,
                      SUM(CASE WHEN attempt.id IS NOT NULL AND attempt.id=(SELECT first.id FROM study_attempts first WHERE first.exercise_id=exercise.id ORDER BY committed_at,id LIMIT 1) AND json_extract(attempt.result_json,'$.rating')='correct' THEN exercise.point_value ELSE NULL END) first_attempt_points
               FROM study_chapters chapter
               LEFT JOIN study_sources source ON source.chapter_id=chapter.id
               LEFT JOIN study_positions position ON position.source_id=source.id
               LEFT JOIN study_exercises exercise ON exercise.position_id=position.id
               LEFT JOIN study_attempts attempt ON attempt.exercise_id=exercise.id
               WHERE chapter.study_id=? GROUP BY chapter.id ORDER BY chapter.position""",
            (study_id,),
        )
    return map(lambda row: row, rows)


def current_source_pgn(database, study_id):
    return [row[0] for row in database.execute(
            """SELECT source.raw_pgn FROM study_sources source
               JOIN study_chapters chapter ON chapter.id=source.chapter_id
               WHERE chapter.study_id=? AND source.version=(
                 SELECT MAX(latest.version) FROM study_sources latest
                 WHERE latest.chapter_id=source.chapter_id AND latest.source_group_id=source.source_group_id)
               ORDER BY chapter.position,source.created_at,source.record_index""",
            (study_id,),
        )]
