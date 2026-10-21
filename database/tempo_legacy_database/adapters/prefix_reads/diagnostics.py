"""Existing prefix evidence and graph read projections/driver configuration."""
import psycopg
from psycopg_pool import PoolTimeout
import redis

from .. import postgres_store

READ_UNAVAILABLE_ERRORS = (psycopg.OperationalError, psycopg.errors.QueryCanceled,
            psycopg.errors.LockNotAvailable, psycopg.errors.TransactionTimeout,
            PoolTimeout, redis.RedisError)


def postgres_is_configured():
    return postgres_store.configured()


def diagnostic_connection():
    return postgres_store.connection(read_only=True, background=True, repeatable_read=True)


def repertoire(database, repertoire_id):
    return database.execute_native('SELECT id FROM repertoires WHERE id=%s', (repertoire_id,)).fetchone()


def graph_publication(database, repertoire_id):
    return database.execute_native(
        "SELECT publication.generation,publication.state,task.generation task_generation,task.state task_state "
        "FROM opening_graph_publications publication LEFT JOIN background_tasks task "
        "ON task.kind='opening_graph_rebuild' AND task.deduplication_key=publication.repertoire_id "
        "WHERE publication.repertoire_id=%s", (repertoire_id,),
    ).fetchone()


def prefix_rows(database, repertoire_id: str, generation: int, *, page_size: int, after: str = '', card_id: str | None = None) -> list[dict]:
    # Indexed, keyset-paged current prefix IDs first; no repertoire traversal.
    selection = 'step.card_id=%s' if card_id is not None else 'step.card_id>%s'
    rows = database.execute_native(
        "WITH prefix_ids AS MATERIALIZED (SELECT DISTINCT step.card_id FROM opening_graph_steps step "
        "JOIN cards card ON card.id=step.card_id "
        "WHERE step.repertoire_id=%s AND step.generation=%s AND step.segment_kind='prefix' "
        "AND step.last_decision_index>step.first_decision_index AND " + selection +
        " AND card.archived=0 AND card.content_type='opening' "
        "AND (card.repertoire_id=%s OR EXISTS(SELECT 1 FROM repertoire_cards link "
        "WHERE link.repertoire_id=%s AND link.card_id=card.id)) "
        "AND NOT EXISTS(SELECT 1 FROM repertoire_integrity_card_blocks block "
        "WHERE block.repertoire_id=%s AND block.card_id=card.id) ORDER BY step.card_id LIMIT %s) "
        "SELECT card.id card_id,card.revision,card.start_fen,card.moves_json,card.trained_color,snapshot.id, "
        "ARRAY(SELECT DISTINCT step.trained_color FROM opening_graph_steps step "
        "WHERE step.repertoire_id=%s AND step.generation=%s AND step.card_id=card.id "
        "AND step.segment_kind='prefix' AND step.last_decision_index>step.first_decision_index "
        "AND step.starting_fen=card.start_fen AND step.moves_json::jsonb=card.moves_json::jsonb "
        "ORDER BY step.trained_color LIMIT 2) colors "
        "FROM prefix_ids JOIN cards card ON card.id=prefix_ids.card_id "
        "LEFT JOIN opening_evidence_presentations snapshot ON snapshot.card_id=card.id "
        "AND snapshot.revision=card.revision AND snapshot.start_fen=card.start_fen "
        "AND snapshot.moves_json=card.moves_json AND snapshot.trained_color IS NOT DISTINCT FROM card.trained_color "
        "ORDER BY card.id",
        (repertoire_id, generation, card_id if card_id is not None else after,
         repertoire_id, repertoire_id, repertoire_id, 1 if card_id is not None else page_size + 1,
         repertoire_id, generation),
    ).fetchall()
    return [dict(row) for row in rows]


def attempt_rows(database, presentation_id, repertoire_id, color, card_id, revision, limit):
    return database.execute_native(
            "SELECT attempt_id,manifest_id,state,started_at FROM opening_evidence_attempts "
            "WHERE presentation_snapshot_id=%s AND repertoire_id=%s AND trained_color=%s "
            "AND card_id=%s AND card_revision=%s ORDER BY started_at DESC,attempt_id DESC LIMIT %s",
            (presentation_id, repertoire_id, color, card_id, revision, limit),
        ).fetchall()


def observation_rows(database, attempt_ids, limit):
    return database.execute_native(
            "SELECT attempt_id,decision_index,observation_json FROM opening_evidence_observations "
            "WHERE attempt_id=ANY(%s::text[]) ORDER BY attempt_id,decision_index LIMIT %s",
            (attempt_ids, limit),
        ).fetchall()
