"""Existing line import/branch persistence on the caller-owned transaction."""


def initial_depth(database):
    return database.execute(
        "SELECT initial_depth FROM settings WHERE id=1",
    ).fetchone()[0]


def matching_import_repertoire(database, filename, trained_color):
    return database.execute(
        "SELECT r.id FROM repertoires r WHERE source_name=%s AND id NOT IN ('__tactics__','__endgames__','__game_mistakes__','__game_tactics__','__captured_tactics__','__defense__','__retained_cards__') AND EXISTS(SELECT 1 FROM repertoire_lines l WHERE l.repertoire_id=r.id AND l.trained_color=%s) ORDER BY created_at DESC LIMIT 1",
        (filename, trained_color),
    ).fetchone()


def clear_main_repertoires(database):
    database.execute(
        "UPDATE repertoires SET is_main=0 WHERE id NOT IN ('__tactics__','__endgames__','__game_mistakes__','__game_tactics__','__captured_tactics__','__defense__','__retained_cards__')",
    )


def save_import_repertoire(database, rid, name, filename, now):
    database.execute(
        'INSERT INTO repertoires(id,name,source_name,created_at,is_main) VALUES(%s,%s,%s,%s,1) ON CONFLICT(id) DO UPDATE SET is_main=1,source_name=excluded.source_name',
        (rid, name, filename, now),
    )


def matching_import_line(database, rid, starting_fen, moves_json):
    return database.execute(
        'SELECT id FROM repertoire_lines WHERE repertoire_id=%s AND start_fen=%s AND moves_json=%s',
        (rid, starting_fen, moves_json),
    ).fetchone()


def _insert_line(database, line_id, repertoire_id, name, trained_color, starting_fen, moves_json, created_at):
    return database.execute(
        'INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
        (line_id, repertoire_id, name, trained_color, starting_fen, moves_json, created_at,),
    )


def insert_import_line(database, line_id, repertoire_id, name, trained_color, starting_fen, moves_json, created_at):
    return _insert_line(database, line_id, repertoire_id, name, trained_color, starting_fen, moves_json, created_at).rowcount


def insert_branch_line(database, line_id, repertoire_id, name, trained_color, starting_fen, moves_json, created_at):
    _insert_line(database, line_id, repertoire_id, name, trained_color, starting_fen, moves_json, created_at)


def set_import_line_depth(database, line_id, depth):
    database.execute(
        'INSERT INTO repertoire_line_training_depths(\n                       line_id,learner_decision_count\n                   ) VALUES(%s,%s) ON CONFLICT(line_id) DO UPDATE SET\n                       learner_decision_count=excluded.learner_decision_count',
        (line_id, depth),
    )


def save_import_annotation(database, rid, position_key, comment, arrows_json, squares_json, now):
    database.execute(
        'INSERT INTO position_annotations(repertoire_id,fen_key,comment,arrows_json,squares_json,updated_at)\n                              VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(repertoire_id,fen_key) DO UPDATE SET\n                              comment=excluded.comment,arrows_json=excluded.arrows_json,squares_json=excluded.squares_json,updated_at=excluded.updated_at',
        (rid, position_key, comment, arrows_json, squares_json, now),
    )


def existing_segment_rows(database, segment_ids):
    placeholders = ",".join('%s' for _ in segment_ids)
    return database.execute(
        f"SELECT id FROM cards WHERE id IN ({placeholders})",
        tuple(sorted(segment_ids)),
    ).fetchall()


def deleted_segment_rows(database, segment_ids):
    placeholders = ",".join('%s' for _ in segment_ids)
    yield from database.execute(
        f"SELECT card_id FROM deleted_cards WHERE card_id IN ({placeholders})",
        tuple(sorted(segment_ids)),
    )


def branch_line_presence(database, lid):
    return database.execute(
        'SELECT 1 FROM repertoire_lines WHERE id=%s',
        (lid,),
    ).fetchone()


def set_branch_line_depth(database, lid, depth):
    database.execute(
        'INSERT INTO repertoire_line_training_depths(\n                   line_id,learner_decision_count\n               ) VALUES(%s,%s) ON CONFLICT(line_id) DO UPDATE SET\n                   learner_decision_count=excluded.learner_decision_count',
        (lid, depth),
    )


def source_gap(database, gap_node_id, gap_move_uci):
    return database.execute(
        'SELECT c.node_id,c.move_uci,n.repertoire_id\n                   FROM repertoire_coverage_candidates c\n                   JOIN repertoire_coverage_nodes n ON n.id=c.node_id\n                   WHERE c.node_id=%s AND c.move_uci=%s',
        (gap_node_id, gap_move_uci),
    ).fetchone()


def mark_source_gap_covered(database, gap_node_id, gap_move_uci):
    database.execute(
        'UPDATE repertoire_coverage_candidates SET covered=1 WHERE node_id=%s AND move_uci=%s',
        (gap_node_id, gap_move_uci),
    )


def removal_lines(database, repertoire_id):
    return database.execute(
        'SELECT * FROM repertoire_lines WHERE repertoire_id=%s',
        (repertoire_id,),
    ).fetchall()


def remove_line(database, line_id):
    database.execute(
        'DELETE FROM repertoire_lines WHERE id=%s',
        (line_id,),
    )
