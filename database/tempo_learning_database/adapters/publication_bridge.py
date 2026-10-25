"""Existing set-based Content execution SQL; application owns accepted authority."""
from psycopg.types.json import Jsonb

from .publications import (
    accepted_digest, activate_accepted, activation_receipt, deactivate_cards,
    deleted_cards, insert_accepted, insert_activation_receipt, latest_generation,
    lock_publication, publication_card_ids, serving_for_update, upsert_serving,
)


def physical_alias(database, card_id):
    return database.execute("SELECT learning_card_id FROM content_card_aliases WHERE content_card_id=?", (card_id,)).fetchone()


def accepted_payload(database, publication_id, generation):
    return database.execute('SELECT digest,payload_json FROM accepted_publications WHERE publication_id=? AND generation=?', (publication_id, generation)).fetchone()


def serving_status(database, publication_id):
    return database.execute("SELECT serving.generation,serving.activation_revision,accepted.state,accepted.digest FROM serving_publications serving JOIN accepted_publications accepted ON accepted.publication_id=serving.publication_id AND accepted.generation=serving.generation WHERE serving.publication_id=?", (publication_id,)).fetchone()


def accepted_for_update(database, publication_id, generation):
    return database.execute("SELECT payload_json,state,digest FROM accepted_publications WHERE publication_id=? AND generation=? FOR UPDATE", (publication_id, generation)).fetchone()


def lock_cards(database, card_ids):
    database.execute_native("SELECT pg_advisory_xact_lock(hashtextextended('tempo:card-edit:' || card,0)) FROM (SELECT unnest(%s::text[]) AS card ORDER BY card) ordered", (card_ids,)).fetchall()


def cards_for_update(database, card_ids):
    return database.execute("SELECT * FROM cards WHERE id=ANY(?) ORDER BY id FOR UPDATE", (card_ids,)).fetchall()


def other_opening_owner(database, repertoire_ids, publication_id):
    return database.execute("SELECT 1 FROM cards card JOIN repertoire_cards membership ON membership.card_id=card.id WHERE membership.repertoire_id=ANY(?) AND card.content_type='opening' AND card.serving_active=1 AND card.publication_id IS DISTINCT FROM ? LIMIT 1", (repertoire_ids, publication_id)).fetchone()


def accepted_generations(database, publication_id, generations):
    return database.execute("SELECT generation,payload_json FROM accepted_publications WHERE publication_id=? AND generation=ANY(?)", (publication_id, generations)).fetchall()


def accepted_aliases(database, content_ids, learning_ids):
    return database.execute('SELECT content_card_id,learning_card_id,publication_id,binding_digest FROM content_card_aliases WHERE content_card_id=ANY(?) OR learning_card_id=ANY(?)', (content_ids, learning_ids)).fetchall()


def insert_repertoires(database, created_at, repertoire_ids, titles=None):
    database.execute_native("INSERT INTO repertoires(id,name,source_name,created_at) SELECT value,value,'Accepted Content publication',%s FROM unnest(%s::text[]) value ON CONFLICT DO NOTHING", (created_at, repertoire_ids))
    if titles:
        allowed = set(repertoire_ids)
        rows = [{"repertoire_id": identifier, "title": title} for identifier, title in sorted(titles.items())
                if identifier in allowed]
        database.execute_native("""UPDATE repertoires SET name=title.title
            FROM jsonb_to_recordset(%s) AS title(repertoire_id text,title text)
            WHERE repertoires.id=title.repertoire_id AND repertoires.id=ANY(%s::text[])""", (Jsonb(rows), repertoire_ids))


def insert_aliases(database, aliases):
    database.execute_native("INSERT INTO content_card_aliases SELECT * FROM jsonb_to_recordset(%s) AS x(content_card_id text,learning_card_id text,publication_id text,binding_digest text) ON CONFLICT DO NOTHING", (Jsonb(aliases),))


def upsert_cards(database, cards):
    database.execute_native("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,revision,trained_color,publication_id,publication_generation,serving_active,pending_validation)
        SELECT id,repertoire_id,'checkpoint',start_fen,moves_json,'new',due_date,'opening',revision,trained_color,publication_id,publication_generation,1,pending_validation
        FROM jsonb_to_recordset(%s) AS x(id text,repertoire_id text,start_fen text,moves_json text,due_date text,revision bigint,trained_color text,publication_id text,publication_generation bigint,pending_validation integer)
        ON CONFLICT(id) DO UPDATE SET repertoire_id=excluded.repertoire_id,start_fen=excluded.start_fen,moves_json=excluded.moves_json,revision=excluded.revision,trained_color=excluded.trained_color,publication_id=excluded.publication_id,publication_generation=excluded.publication_generation,serving_active=1,pending_validation=excluded.pending_validation""", (Jsonb(cards),))


def remove_memberships(database, card_ids):
    database.execute("DELETE FROM repertoire_cards WHERE card_id=ANY(?)", (card_ids,))


def insert_memberships(database, memberships):
    database.execute_native("INSERT INTO repertoire_cards SELECT * FROM jsonb_to_recordset(%s) AS x(repertoire_id text,card_id text)", (Jsonb(memberships),))


def remove_integrity_blocks(database, card_ids):
    database.execute("DELETE FROM repertoire_integrity_card_blocks WHERE card_id=ANY(?)", (card_ids,))


def insert_integrity_blocks(database, memberships):
    database.execute_native("INSERT INTO repertoire_integrity_card_blocks SELECT * FROM jsonb_to_recordset(%s) AS x(repertoire_id text,card_id text)", (Jsonb(memberships),))


def insert_lines(database, created_at, lines):
    database.execute_native("INSERT INTO repertoire_lines(id,repertoire_id,trained_color,created_at) SELECT id,repertoire_id,trained_color,%s FROM jsonb_to_recordset(%s) AS x(id text,repertoire_id text,trained_color text) ON CONFLICT DO NOTHING", (created_at, Jsonb(lines)))


def upsert_opening_publications(database, generation, repertoire_ids):
    database.execute_native("INSERT INTO opening_graph_publications SELECT value,%s,'ready' FROM unnest(%s::text[]) value ON CONFLICT(repertoire_id) DO UPDATE SET generation=excluded.generation,state='ready'", (generation, repertoire_ids))


def insert_opening_steps(database, steps):
    database.execute_native("INSERT INTO opening_graph_steps SELECT * FROM jsonb_to_recordset(%s) AS x(repertoire_id text,generation bigint,line_id text,decision_index bigint,card_id text,parent_card_id text,starting_fen text,moves_json text,trained_color text)", (Jsonb(steps),))


def supersede_card_queues(database, card_ids):
    database.execute("UPDATE daily_queue SET status='superseded' WHERE status='queued' AND card_id=ANY(?)", (card_ids,))


def supersede_accepted(database, publication_id):
    database.execute("UPDATE accepted_publications SET state='superseded' WHERE publication_id=? AND state='active'", (publication_id,))
