"""Existing pasted-line queries on the caller's unchanged transaction."""
import sqlite3


def repertoires(database):
    return [dict(row) for row in database.execute(
        """SELECT id,name FROM repertoires
           WHERE id NOT IN ('__tactics__','__game_mistakes__','__game_tactics__','__captured_tactics__','__defense__','__retained_cards__') ORDER BY id"""
    )]


def lines(database):
    return [dict(row) for row in database.execute(
        "SELECT id,repertoire_id,trained_color,start_fen,moves_json FROM repertoire_lines ORDER BY id"
    )]


def coverage_gap(database, node_id, opponent_move):
    return database.execute(
        """SELECT n.fen,n.repertoire_id FROM repertoire_coverage_candidates c
           JOIN repertoire_coverage_nodes n ON n.id=c.node_id
           WHERE c.node_id=? AND c.move_uci=?""", (node_id, opponent_move)
    ).fetchone()


def begin_write_if_needed(database):
    if isinstance(database, sqlite3.Connection) and not database.in_transaction:
        database.execute("BEGIN IMMEDIATE")


def insert_line(database, line_id, repertoire_id, name, trained_color, starting_fen, moves_json, created_at):
    return database.execute(
        """INSERT OR IGNORE INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
        (line_id, repertoire_id, name, trained_color, starting_fen, moves_json, created_at),
    ).rowcount


def initial_depth(database):
    return database.execute("SELECT initial_depth FROM settings WHERE id=1").fetchone()[0]


def set_line_depth(database, line_id, depth):
    database.execute(
        "INSERT OR IGNORE INTO repertoire_line_training_depths(line_id,learner_decision_count) VALUES(?,?)",
        (line_id, depth),
    )


def cover_gap(database, node_id, opponent_move):
    database.execute(
        "UPDATE repertoire_coverage_candidates SET covered=1 WHERE node_id=? AND move_uci=?",
        (node_id, opponent_move),
    )
