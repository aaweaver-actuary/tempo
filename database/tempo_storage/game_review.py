"""Native read operations for PGN replay and authored-line comparison."""
import json


def recent_games(database):
    return [dict(row) for row in database.execute(
        "SELECT id,provider,played_at,color,result,game_url FROM imported_games ORDER BY played_at DESC,id LIMIT 100"
    ).fetchall()]


def game(database, game_id):
    row = database.execute(
        "SELECT id,provider,played_at,color,result,start_fen,moves_json,game_url FROM imported_games WHERE id=%s",
        (game_id,),
    ).fetchone()
    return dict(row) if row else None


def authored_lines(database, repertoire_id, color):
    return [dict(row) for row in database.execute(
        "SELECT id,start_fen,moves_json FROM repertoire_lines WHERE repertoire_id=%s AND trained_color=%s ORDER BY id",
        (repertoire_id, color),
    ).fetchall()]
