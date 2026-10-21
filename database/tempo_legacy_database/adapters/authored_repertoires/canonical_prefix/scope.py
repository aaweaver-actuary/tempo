"""Existing prefix metadata and certified-position storage operations."""

import json
import sqlite3


def prefix_row(database, repertoire_id, *, lock=False):
    suffix = " FOR UPDATE" if lock and hasattr(database, "execute_native") else ""
    return database.execute(
        "SELECT canonical_prefix_moves_json,canonical_prefix_revision,"
        "canonical_prefix_preview_id,scope_source_revision FROM repertoires WHERE id=?" + suffix,
        (repertoire_id,),
    ).fetchone()


def line_anchor(database, preview_id, starting_position_key):
    return database.execute(
        "SELECT position.route_json FROM canonical_prefix_positions position "
        "JOIN canonical_prefix_previews preview ON preview.id=position.preview_id "
        "JOIN repertoires repertoire ON repertoire.id=preview.repertoire_id "
        "WHERE position.preview_id=? AND position.fen_key=? "
        "AND position.source_revision=repertoire.scope_source_revision "
        "ORDER BY position.in_scope DESC,position.ply,position.route_json LIMIT 1",
        (preview_id, starting_position_key),
    ).fetchone()


def begin_batch_scope(database):
    if isinstance(database, sqlite3.Connection) and not database.in_transaction:
        database.execute("BEGIN IMMEDIATE")


def preview_source_revision(database, preview_id):
    return database.execute("SELECT source_revision FROM canonical_prefix_previews WHERE id=?", (preview_id,)).fetchone()[0]


def write_positions(database, preview_id, positions, source_revision):
    replacement_guard = (
        " WHERE excluded.source_revision>canonical_prefix_positions.source_revision OR "
        "(excluded.source_revision=canonical_prefix_positions.source_revision AND "
        "(excluded.ply,excluded.route_json)<(canonical_prefix_positions.ply,canonical_prefix_positions.route_json))"
    )
    if hasattr(database, "execute_native"):
        database.execute_native(
            "INSERT INTO canonical_prefix_positions(preview_id,fen_key,in_scope,fen,route_json,ply,source_revision) "
            "SELECT %s,fen_key,in_scope,fen,route_json,ply,%s FROM jsonb_to_recordset(%s::jsonb) "
            "AS position(fen_key text,in_scope bigint,fen text,route_json text,ply bigint) "
            "ON CONFLICT(preview_id,fen_key,in_scope) DO UPDATE SET fen=excluded.fen,route_json=excluded.route_json,"
            "ply=excluded.ply,source_revision=excluded.source_revision" + replacement_guard,
            (preview_id, source_revision, json.dumps(positions)),
        )
    else:
        database.executemany(
            "INSERT INTO canonical_prefix_positions(preview_id,fen_key,in_scope,fen,route_json,ply,source_revision) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(preview_id,fen_key,in_scope) DO UPDATE SET fen=excluded.fen,route_json=excluded.route_json,"
            "ply=excluded.ply,source_revision=excluded.source_revision" + replacement_guard,
            [(preview_id, item["fen_key"], item["in_scope"], item["fen"], item["route_json"], item["ply"], source_revision) for item in positions],
        )
