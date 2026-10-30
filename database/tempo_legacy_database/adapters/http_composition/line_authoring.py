"""Existing line import/branch persistence on the caller-owned transaction."""


def initial_depth(database):
    return database.execute(
        "SELECT initial_depth FROM settings WHERE id=1",
    ).fetchone()[0]


def begin_import_if_needed(database):
    if not database.in_transaction:
        database.execute("BEGIN IMMEDIATE")


def matching_import_repertoire(database, filename, trained_color):
    return database.execute(
        "SELECT r.id FROM repertoires r WHERE source_name=? AND id NOT IN ('__tactics__','__game_mistakes__','__game_tactics__','__captured_tactics__','__defense__','__retained_cards__') AND EXISTS(SELECT 1 FROM repertoire_lines l WHERE l.repertoire_id=r.id AND l.trained_color=?) ORDER BY created_at DESC LIMIT 1",
        (filename, trained_color),
    ).fetchone()


def clear_main_repertoires(database):
    database.execute(
        "UPDATE repertoires SET is_main=0 WHERE id NOT IN ('__tactics__','__game_mistakes__','__game_tactics__','__captured_tactics__','__defense__','__retained_cards__')",
    )


def save_import_repertoire(database, rid, name, filename, now):
    database.execute(
        "INSERT INTO repertoires(id,name,source_name,created_at,is_main) VALUES(?,?,?,?,1) ON CONFLICT(id) DO UPDATE SET is_main=1,source_name=excluded.source_name",
        (rid, name, filename, now),
    )


def matching_import_line(database, rid, starting_fen, moves_json):
    return database.execute(
        "SELECT id FROM repertoire_lines WHERE repertoire_id=? AND start_fen=? AND moves_json=?",
        (rid, starting_fen, moves_json),
    ).fetchone()


def _insert_line(database, line_id, repertoire_id, name, trained_color, starting_fen, moves_json, created_at):
    return database.execute(
        "INSERT OR IGNORE INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,?,?,?,?,?)",
        (line_id, repertoire_id, name, trained_color, starting_fen, moves_json, created_at,),
    )


def insert_import_line(database, line_id, repertoire_id, name, trained_color, starting_fen, moves_json, created_at):
    return _insert_line(database, line_id, repertoire_id, name, trained_color, starting_fen, moves_json, created_at).rowcount


def insert_branch_line(database, line_id, repertoire_id, name, trained_color, starting_fen, moves_json, created_at):
    _insert_line(database, line_id, repertoire_id, name, trained_color, starting_fen, moves_json, created_at)


def set_import_line_depth(database, line_id, depth):
    database.execute(
        """INSERT INTO repertoire_line_training_depths(
                       line_id,learner_decision_count
                   ) VALUES(?,?) ON CONFLICT(line_id) DO UPDATE SET
                       learner_decision_count=excluded.learner_decision_count""",
        (line_id, depth),
    )


def save_import_annotation(database, rid, position_key, comment, arrows_json, squares_json, now):
    database.execute(
        """INSERT INTO position_annotations(repertoire_id,fen_key,comment,arrows_json,squares_json,updated_at)
                              VALUES(?,?,?,?,?,?) ON CONFLICT(repertoire_id,fen_key) DO UPDATE SET
                              comment=excluded.comment,arrows_json=excluded.arrows_json,squares_json=excluded.squares_json,updated_at=excluded.updated_at""",
        (rid, position_key, comment, arrows_json, squares_json, now),
    )


def existing_segment_rows(database, segment_ids):
    placeholders = ",".join("?" for _ in segment_ids)
    return database.execute(
        f"SELECT id FROM cards WHERE id IN ({placeholders})",
        tuple(sorted(segment_ids)),
    ).fetchall()


def deleted_segment_rows(database, segment_ids):
    placeholders = ",".join("?" for _ in segment_ids)
    yield from database.execute(
        f"SELECT card_id FROM deleted_cards WHERE card_id IN ({placeholders})",
        tuple(sorted(segment_ids)),
    )


def branch_line_presence(database, lid):
    return database.execute(
        "SELECT 1 FROM repertoire_lines WHERE id=?",
        (lid,),
    ).fetchone()


def set_branch_line_depth(database, lid, depth):
    database.execute(
        """INSERT INTO repertoire_line_training_depths(
                   line_id,learner_decision_count
               ) VALUES(?,?) ON CONFLICT(line_id) DO UPDATE SET
                   learner_decision_count=excluded.learner_decision_count""",
        (lid, depth),
    )


def source_gap(database, gap_node_id, gap_move_uci):
    return database.execute(
        """SELECT c.node_id,c.move_uci,n.repertoire_id
                   FROM repertoire_coverage_candidates c
                   JOIN repertoire_coverage_nodes n ON n.id=c.node_id
                   WHERE c.node_id=? AND c.move_uci=?""",
        (gap_node_id, gap_move_uci),
    ).fetchone()


def mark_source_gap_covered(database, gap_node_id, gap_move_uci):
    database.execute(
        "UPDATE repertoire_coverage_candidates SET covered=1 WHERE node_id=? AND move_uci=?",
        (gap_node_id, gap_move_uci),
    )


def removal_lines(database, repertoire_id):
    return database.execute(
        "SELECT * FROM repertoire_lines WHERE repertoire_id=?",
        (repertoire_id,),
    ).fetchall()


def remove_line(database, line_id):
    database.execute(
        "DELETE FROM repertoire_lines WHERE id=?",
        (line_id,),
    )
