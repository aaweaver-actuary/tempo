"""Existing source digest dialects and bounded saved-line projection."""

import hashlib
import json
import sqlite3


class _SourceDigest:
    def __init__(self):
        self.digest = hashlib.sha256()

    def step(self, *values):
        self.digest.update(json.dumps(values, separators=(',', ':')).encode())

    def finalize(self):
        return self.digest.hexdigest()


def _source_token(database, repertoire_id):
    if isinstance(database, sqlite3.Connection):
        database.create_aggregate('tempo_source_digest', 4, _SourceDigest)
        return database.execute(
            'SELECT tempo_source_digest(id,trained_color,start_fen,moves_json) FROM '
            '(SELECT id,trained_color,start_fen,moves_json FROM repertoire_lines '
            'WHERE repertoire_id=? ORDER BY id)', (repertoire_id,),
        ).fetchone()[0]
    return database.execute_native(
        "SELECT md5(COALESCE(string_agg(md5(row_to_json(item)::text),'' ORDER BY item.id),'')) "
        "FROM (SELECT id,trained_color,start_fen,moves_json FROM repertoire_lines "
        "WHERE repertoire_id=%s) item", (repertoire_id,),
    ).fetchone()[0]


def source_line_page(database, repertoire_id, cursor, limit):
    return [dict(row) for row in database.execute(
        'SELECT id,start_fen,moves_json FROM repertoire_lines '
        'WHERE repertoire_id=? AND id>? ORDER BY id LIMIT ?',
        (repertoire_id, cursor, limit),
    )]
