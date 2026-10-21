"""Game Evidence providers SQL operations on the caller transaction."""
from __future__ import annotations
from psycopg import Connection
from psycopg.types.json import Jsonb


def enqueue_import(database: Connection, import_id, workspace_id, document, cursor):
    database.execute("INSERT INTO import_cursors(import_id,workspace_id,configuration,cursor) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                         (import_id,workspace_id,Jsonb(document),Jsonb(cursor)))


def cancel_import(database: Connection, import_id):
    database.execute("UPDATE import_cursors SET status='cancelled' WHERE import_id=%s", (import_id,))


def claimable_import(database: Connection):
    return database.execute("""SELECT * FROM import_cursors WHERE status IN ('pending','retrying')
          AND next_attempt_at<=now() AND (leased_until IS NULL OR leased_until<now())
          AND NOT EXISTS(SELECT 1 FROM provider_leases provider WHERE provider.provider=configuration->>'provider'
            AND (provider.cooldown_until>now() OR provider.leased_until>now()))
          ORDER BY import_id LIMIT 1 FOR UPDATE SKIP LOCKED""").fetchone()


def acquire_provider_lease(database: Connection, provider, token):
    return database.execute("""INSERT INTO provider_leases(provider,token,leased_until) VALUES(%s,%s,now()+interval '90 seconds')
          ON CONFLICT(provider) DO UPDATE SET token=excluded.token,leased_until=excluded.leased_until
          WHERE (provider_leases.leased_until IS NULL OR provider_leases.leased_until<now())
            AND provider_leases.cooldown_until<=now() RETURNING token""", (provider,token)).fetchone()


def begin_import_attempt(database: Connection, token, import_id):
    database.execute("UPDATE import_cursors SET lease_token=%s,leased_until=now()+interval '90 seconds',attempts=attempts+1 WHERE import_id=%s", (token,import_id))


def saved_import_page(database: Connection, import_id, page_id):
    return database.execute("SELECT raw_response FROM import_pages WHERE import_id=%s AND page_id=%s AND validation_status!='invalid'", (import_id,page_id)).fetchone()


def locked_import(database: Connection, import_id):
    return database.execute("SELECT status,lease_token FROM import_cursors WHERE import_id=%s FOR UPDATE",(import_id,)).fetchone()


def locked_provider_lease(database: Connection, provider):
    return database.execute("SELECT token,leased_until>now() AS valid FROM provider_leases WHERE provider=%s FOR UPDATE",(provider,)).fetchone()


def complete_import_page(database: Connection, cursor, status, import_id):
    database.execute("UPDATE import_cursors SET cursor=%s,status=%s,reason=NULL,attempts=0 WHERE import_id=%s",
                                 (Jsonb(cursor),status,import_id))


def extend_provider_cooldown(database: Connection, retry_after, provider):
    database.execute("""UPDATE provider_leases SET cooldown_until=greatest(cooldown_until,
                  now()+%s*interval '1 second') WHERE provider=%s""",(retry_after,provider))


def record_import_failure(database: Connection, status, reason, next_attempt_at, import_id, token):
    database.execute("""UPDATE import_cursors SET status=%s,reason=%s,next_attempt_at=%s
              WHERE import_id=%s AND lease_token=%s AND status!='cancelled'""",
              (status,
               reason,next_attempt_at,import_id,token))


def record_unavailable_source(database: Connection, import_id, token, retry_after):
    database.execute("UPDATE import_cursors SET status='retrying',reason='provider_source_unavailable_or_invalid',next_attempt_at=now()+%s WHERE import_id=%s AND lease_token=%s AND status!='cancelled'",(retry_after,import_id,token))


def release_import_lease(database: Connection, import_id, token):
    database.execute("UPDATE import_cursors SET lease_token=NULL,leased_until=NULL WHERE import_id=%s AND lease_token=%s",(import_id,token))


def release_provider_lease(database: Connection, provider, token):
    database.execute("UPDATE provider_leases SET token=NULL,leased_until=NULL WHERE provider=%s AND token=%s",(provider,token))


def import_lease_state(database: Connection, import_id):
    return database.execute("SELECT status,lease_token,leased_until>now() AS valid FROM import_cursors WHERE import_id=%s FOR UPDATE",(import_id,)).fetchone()


def provider_lease_state(database: Connection, provider):
    return database.execute("SELECT token,leased_until>now() AS valid FROM provider_leases WHERE provider=%s",(provider,)).fetchone()


def save_import_page(database: Connection, import_id, page_id, raw, source_digest, status, reason):
    database.execute("""INSERT INTO import_pages(import_id,page_id,raw_response,source_digest,validation_status,validation_reason)
          VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(import_id,page_id) DO UPDATE SET
          raw_response=excluded.raw_response,source_digest=excluded.source_digest,
          validation_status=excluded.validation_status,validation_reason=excluded.validation_reason""",
          (import_id,page_id,raw,source_digest,status,reason))
