"""Existing pasted-line queries on the caller's unchanged transaction."""


def repertoires(database):
    return [dict(row) for row in database.execute(
        """SELECT id,name FROM repertoires
           WHERE id NOT IN ('__tactics__','__endgames__','__game_mistakes__','__game_tactics__','__captured_tactics__','__defense__','__retained_cards__') ORDER BY id"""
    )]


def lines(database):
    return [dict(row) for row in database.execute(
        "SELECT id,repertoire_id,trained_color,start_fen,moves_json FROM repertoire_lines ORDER BY id"
    )]


def coverage_gap(database, node_id, opponent_move):
    return database.execute(
        'SELECT n.fen,n.repertoire_id FROM repertoire_coverage_candidates c\n           JOIN repertoire_coverage_nodes n ON n.id=c.node_id\n           WHERE c.node_id=%s AND c.move_uci=%s', (node_id, opponent_move)
    ).fetchone()


def insert_line(database, line_id, repertoire_id, name, trained_color, starting_fen, moves_json, created_at):
    return database.execute(
        'INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)\n                   VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',
        (line_id, repertoire_id, name, trained_color, starting_fen, moves_json, created_at),
    ).rowcount


def initial_depth(database):
    return database.execute("SELECT initial_depth FROM settings WHERE id=1").fetchone()[0]


def set_line_depth(database, line_id, depth):
    database.execute(
        'INSERT INTO repertoire_line_training_depths(line_id,learner_decision_count) VALUES(%s,%s) ON CONFLICT DO NOTHING',
        (line_id, depth),
    )


def cover_gap(database, node_id, opponent_move):
    database.execute(
        'UPDATE repertoire_coverage_candidates SET covered=1 WHERE node_id=%s AND move_uci=%s',
        (node_id, opponent_move),
    )
