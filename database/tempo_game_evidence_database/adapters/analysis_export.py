"""Game Evidence analysis export SQL operations on the caller transaction."""
from __future__ import annotations
from psycopg import Connection
from psycopg.types.json import Jsonb


def begin_consistent_source_read(database: Connection):
    database.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")


def workspace_snapshot(database: Connection, workspace):
    return database.execute("SELECT snapshot_digest FROM workspace_inputs WHERE workspace_id=%s", (workspace,)).fetchone()


def snapshot_document(database: Connection, snapshot_digest):
    return database.execute("SELECT document FROM snapshots WHERE digest=%s", (snapshot_digest,)).fetchone()


def selected_games(database: Connection, workspace, game_ids):
    return database.execute("SELECT game.*,comparison.report,comparison.input_token FROM games game LEFT JOIN comparisons comparison ON comparison.game_id=game.game_id AND comparison.revision=game.report_revision WHERE game.workspace_id=%s AND (%s::text[] IS NULL OR game.game_id=ANY(%s::text[])) ORDER BY game.game_id LIMIT 101", (workspace, game_ids, game_ids)).fetchall()


def lock_export(database: Connection, input_id):
    database.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("analysis-export:" + input_id,))


def existing_manifest(database: Connection, input_id):
    return database.execute("SELECT manifest FROM analysis_exports WHERE input_id=%s", (input_id,)).fetchone()


def workspace_manifest(database: Connection, input_id, workspace):
    return database.execute("SELECT manifest FROM analysis_exports WHERE input_id=%s AND workspace_id=%s", (input_id,workspace)).fetchone()


def export_page(database: Connection, input_id, kind, index):
    return database.execute("SELECT document,digest FROM analysis_export_pages WHERE input_id=%s AND kind=%s AND page_index=%s", (input_id,kind,index)).fetchone()


def resume_manifest(database: Connection, input_id, workspace):
    return database.execute('SELECT manifest FROM analysis_exports WHERE input_id=%s AND workspace_id=%s',
                                   (input_id,workspace)).fetchone()


def exported_game_pages(database: Connection, input_id):
    return database.execute("SELECT document FROM analysis_export_pages WHERE input_id=%s AND kind='games'", (input_id,)).fetchall()


def referenced_page(database: Connection, input_id, kind, page_index):
    return database.execute("SELECT document FROM analysis_export_pages WHERE input_id=%s AND kind=%s AND page_index=%s", (input_id,kind,page_index)).fetchone()


def persist_export(database: Connection, input_id, workspace, manifest, pages):
    database.execute("INSERT INTO analysis_exports VALUES(%s,%s,%s)", (input_id, workspace, Jsonb(manifest)))
    with database.cursor() as cursor:
        cursor.executemany("INSERT INTO analysis_export_pages VALUES(%s,%s,%s,%s,%s)", [(input_id, kind,index,fingerprint,Jsonb(document)) for kind,index,fingerprint,document in pages])
