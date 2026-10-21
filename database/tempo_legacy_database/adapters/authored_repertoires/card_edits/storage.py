"""Fixed existing card-edit statements on the caller-owned transaction."""
import json

from ....connections import card_columns
from ...postgres_store import PostgresConnection


def current_publication(database, repertoire_id):
    return database.execute_native(
        "SELECT publication.generation,task.state FROM opening_graph_publications publication "
        "JOIN background_tasks task ON task.kind='opening_graph_rebuild' "
        "AND task.deduplication_key=publication.repertoire_id "
        "AND task.generation=publication.generation "
        "WHERE publication.repertoire_id=%s", (repertoire_id,),
    ).fetchone()


def graph_task(database, repertoire_id):
    return database.execute_native(
        "SELECT state FROM background_tasks WHERE kind='opening_graph_rebuild' "
        "AND deduplication_key=%s", (repertoire_id,),
    ).fetchone()


def card_scope(database, identifier):
    return database.execute(
        "SELECT repertoire_id FROM repertoire_cards WHERE card_id=? UNION SELECT repertoire_id FROM cards WHERE id=? ORDER BY repertoire_id", (identifier, identifier))


def lock_prefix_transition(database):
    database.execute_native("SELECT pg_advisory_xact_lock_shared(hashtextextended('tempo:prefix-transition:reservations',0))")


def lock_card_edit(database, locked_id):
    database.execute_native(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (f"tempo:card-edit:{locked_id}",),
    )


def locked_card(database, identifier):
    return database.execute_native(
        "SELECT * FROM cards WHERE id=%s FOR UPDATE", (identifier,),
    ).fetchone()


def locked_replacement(database, replacement_id):
    return database.execute_native(
        "SELECT id,revision FROM cards WHERE id=%s FOR UPDATE", (replacement_id,),
    ).fetchone()


def record_revision(database, identifier, revision, start_fen, moves_json, history_mode, now):
    database.execute_native(
        "INSERT INTO card_revisions(card_id,revision,start_fen,moves_json,history_mode,created_at) "
        "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(card_id,revision) DO NOTHING",
        (identifier, revision, start_fen, moves_json, history_mode, now),
    )


def replace_content(database, starting_fen, moves, source_fen, revision, identifier):
    database.execute_native(
        "UPDATE cards SET start_fen=%s,moves_json=%s,source_fen=%s,revision=%s,canonical_route_source=1 WHERE id=%s",
        (starting_fen, json.dumps(moves), source_fen, revision, identifier),
    )


def mark_canonical(database, replacement_id):
    database.execute_native("UPDATE cards SET canonical_route_source=1 WHERE id=%s", (replacement_id,))


def transfer_reviews(database, replacement_id, identifier):
    database.execute_native(
        "UPDATE reviews SET card_id=%s WHERE card_id=%s", (replacement_id, identifier),
    )


def complete_queued(database, identifier):
    database.execute_native(
        "UPDATE daily_queue SET status='complete' WHERE card_id=%s AND status='queued'",
        (identifier,),
    )


def supersede(database, replacement_id, identifier):
    database.execute_native(
        "UPDATE cards SET archived=1,superseded_by=%s WHERE id=%s",
        (replacement_id, identifier),
    )


def insert_replacement(database, columns, copied_fields):
    database.execute_native(
        f"INSERT INTO cards({','.join(columns)}) VALUES({','.join('%s' for _ in columns)})",
        tuple(copied_fields.get(column) for column in columns),
    )


def rebind_queued(database, replacement_id, identifier):
    database.execute_native(
        "UPDATE daily_queue SET card_id=%s WHERE card_id=%s AND status='queued'",
        (replacement_id, identifier),
    )


def copy_membership(database, replacement_id, identifier):
    database.execute_native(
        "INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) "
        "SELECT repertoire_id,%s,1 FROM repertoire_cards WHERE card_id=%s "
        "ON CONFLICT(repertoire_id,card_id) DO UPDATE SET canonical_route_source=1",
        (replacement_id, identifier),
    )


def remove_membership(database, identifier):
    database.execute_native(
        "DELETE FROM repertoire_cards WHERE card_id=%s", (identifier,),
    )


def admit_membership(database, repertoire_id, replacement_id):
    database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id,canonical_route_source) VALUES(?,?,1) ON CONFLICT(repertoire_id,card_id) DO UPDATE SET canonical_route_source=1", (repertoire_id, replacement_id))


def card_repertoires(database, replacement_id):
    return database.execute_native(
        "SELECT repertoire_id FROM repertoire_cards WHERE card_id=%s ORDER BY repertoire_id",
        (replacement_id,),
    )


def mark_pending(database, replacement_id):
    database.execute_native(
        "UPDATE cards SET pending_validation=1 WHERE id=%s", (replacement_id,),
    )


def locked_archive_identity(database, identifier):
    return database.execute_native(
        "SELECT id FROM cards WHERE id=%s FOR UPDATE", (identifier,),
    ).fetchone()


def archive(database, identifier):
    database.execute_native(
        "UPDATE cards SET archived=1 WHERE id=%s", (identifier,),
    )
