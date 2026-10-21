"""Existing structural source projections and repeatable-read resource scope."""

from contextlib import contextmanager

from ...prefix_reads.diagnostics import (
    postgres_is_configured, diagnostic_connection, repertoire, graph_publication,
    psycopg, PoolTimeout, redis,
)


def source_sizes(database, repertoire_id):
    return database.execute_native(
        "SELECT COUNT(*) line_count,COALESCE(SUM(octet_length(moves_json)),0) source_bytes "
        "FROM repertoire_lines WHERE repertoire_id=%s", (repertoire_id,),
    ).fetchone()


def source_rows(database, repertoire_id, limit):
    return database.execute_native(
        "SELECT line.id,line.name,line.start_fen,line.moves_json,line.trained_color,depth.learner_decision_count "
        "FROM repertoire_lines line LEFT JOIN repertoire_line_training_depths depth ON depth.line_id=line.id "
        "WHERE line.repertoire_id=%s ORDER BY line.id LIMIT %s", (repertoire_id, limit),
    ).fetchall()


def graph_steps(database, repertoire_id, generation, limit):
    return database.execute_native(
        "SELECT * FROM opening_graph_steps WHERE repertoire_id=%s AND generation=%s "
        "ORDER BY line_id,decision_index LIMIT %s", (repertoire_id, generation, limit),
    ).fetchall()


def split_rows(database, limit):
    return database.execute_native(
        "SELECT split.source_card_id,split.source_revision,split.shortened_card_id,"
        "shortened.start_fen shortened_start_fen,shortened.moves_json shortened_moves_json,"
        "shortened.revision shortened_revision,split.continuation_card_id,"
        "continuation.start_fen continuation_start_fen,continuation.moves_json continuation_moves_json,"
        "continuation.revision continuation_revision FROM prefix_splits split "
        "LEFT JOIN cards shortened ON shortened.id=split.shortened_card_id "
        "LEFT JOIN cards continuation ON continuation.id=split.continuation_card_id "
        "ORDER BY split.source_card_id LIMIT %s", (limit,),
    ).fetchall()


def published_cards(database, repertoire_id, generation):
    return database.execute_native(
        "SELECT DISTINCT step.card_id id,card.start_fen,card.moves_json,card.trained_color,card.revision,card.archived,"
        "EXISTS(SELECT 1 FROM repertoire_cards link WHERE link.repertoire_id=step.repertoire_id AND link.card_id=step.card_id) linked "
        "FROM opening_graph_steps step LEFT JOIN cards card ON card.id=step.card_id "
        "WHERE step.repertoire_id=%s AND step.generation=%s ORDER BY step.card_id",
        (repertoire_id, generation),
    ).fetchall()


@contextmanager
def structural_snapshot(reads):
    with diagnostic_connection() as database:
        if reads is not None:
            from ...snapshot_reads import RecordingReader
            database = RecordingReader(database, reads)
        yield database
