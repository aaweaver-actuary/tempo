"""Intelligence-owned Games read projections on the caller's read transaction.

Only complete bound queries and row extraction live here. Cursor/limit policy and
response envelopes remain in the component's analysis/games_reader.py.
"""

def read_run_scope(database, run_id):
    row = database.execute("""SELECT r.run_id,r.revision,
            r.payload->>'input_id' AS input_id,
            (r.payload->>'input_revision')::bigint AS input_revision
            FROM analysis_runs r JOIN analysis_heads h USING(run_id,revision)
            WHERE r.run_id=%s""", (run_id,)).fetchone()
    return row


def list_run_scopes(database):
    rows = database.execute("""SELECT r.run_id,r.revision,
                r.payload->>'input_id' AS input_id,
                (r.payload->>'input_revision')::bigint AS input_revision
                FROM analysis_runs r JOIN analysis_heads h USING(run_id,revision)
                ORDER BY r.created_at DESC,r.run_id LIMIT 100""").fetchall()
    return rows


def read_input_scope(database, input_id, input_revision):
    return database.execute("""SELECT state,payload->'game_ids' AS game_ids,
            payload->'selections' AS selections FROM analysis_inputs
            WHERE input_id=%s AND revision=%s""",(input_id,input_revision)).fetchone()


def read_game_page(database, input_id, input_revision, after, limit):
    rows=database.execute("""SELECT game AS payload FROM analysis_pages p
            CROSS JOIN LATERAL jsonb_array_elements(p.payload->'games') AS game
            WHERE p.input_id=%s AND p.revision=%s AND game->>'game_id'>%s
            ORDER BY game->>'game_id' LIMIT %s""",
        (input_id,input_revision,after,limit)).fetchall()
    return [row['payload'] for row in rows]
