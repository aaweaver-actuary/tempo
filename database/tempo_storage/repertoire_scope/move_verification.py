"""Existing source digest dialects and bounded saved-line projection."""

import hashlib
import json


def _source_token(database, repertoire_id):
    return database.execute_native(
        "SELECT md5(COALESCE(string_agg(md5(row_to_json(item)::text),'' ORDER BY item.id),'')) "
        "FROM (SELECT id,trained_color,start_fen,moves_json FROM repertoire_lines "
        "WHERE repertoire_id=%s) item", (repertoire_id,),
    ).fetchone()[0]


def source_line_page(database, repertoire_id, cursor, limit):
    return [dict(row) for row in database.execute(
        'SELECT id,start_fen,moves_json FROM repertoire_lines WHERE repertoire_id=%s AND id>%s ORDER BY id LIMIT %s',
        (repertoire_id, cursor, limit),
    )]
