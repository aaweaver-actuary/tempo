"""Synthetic queue-refresh case for an invocation-owned disposable PostgreSQL 18.6.

The caller owns transaction and disposal. Apply the repository's complete migration
history first; this helper does not alter schema or disable triggers. Use only a
fresh database created by an existing native fixture owner.
"""
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os

from tempo_storage.ddl.schema_version import POSTGRES_SCHEMA_VERSION

FEN = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
IDENTITY = 'queue-refresh-scale'


@dataclass(frozen=True)
class CaseFacts:
    queue_date: str
    queued_ids: tuple[int, ...]
    changed_kind_ids: tuple[int, ...]
    issued_ids: tuple[int, ...]
    blocked_id: int
    completed_id: int
    task_id: str
    protected_hash: str


def protected_hash(db, ids):
    rows = db.execute('''SELECT jsonb_build_object('queue',to_jsonb(q),'origin',to_jsonb(o))::text
        FROM daily_queue q JOIN queue_attempt_origins o ON o.queue_entry_id=q.id
        WHERE q.id=ANY(%s) ORDER BY q.id,o.card_id,o.revision''', (list(ids),)).fetchall()
    return hashlib.sha256('\n'.join(row[0] for row in rows).encode()).hexdigest()


def seed_queue_refresh_case(db, *, queued_count=1258, frozen_active=True,
                            issued_count=1, changed_kind_count=1):
    """Seed an empty disposable DB; IDs and protected state are deterministic.

    ``changed_kind_ids`` are the eligible due rows a consumer may plan as new;
    all other reorderable rows retain their existing kind. Queued count includes
    issued rows but excludes the separate blocked and complete rows.
    """
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Queue scale proof requires an explicitly disposable database')
    if not (queued_count in (32, 1258) and 1 <= issued_count < queued_count
            and 0 <= changed_kind_count <= queued_count - issued_count):
        raise ValueError('unsupported scale or protected/changed counts')
    if db.execute('SELECT MAX(version) FROM tempo_schema_migrations').fetchone()[0] != POSTGRES_SCHEMA_VERSION:
        raise RuntimeError('queue scale fixture requires the current registered schema')
    day = date.today().isoformat()
    if db.execute('SELECT 1 FROM daily_queue WHERE queue_date=%s LIMIT 1', (day,)).fetchone():
        raise RuntimeError('fixture requires an empty current-day queue')
    now = day + 'T00:00:00+00:00'
    db.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Queue scale','synthetic',%s)", (IDENTITY, now))
    db.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(%s,%s,'Route','white',%s,'[\"e2e4\"]',%s)", (IDENTITY, IDENTITY, FEN, now))
    db.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,trained_color,introduced_at)
        SELECT %s||'-'||lpad(n::text,5,'0'),%s,'prefix',%s,'["e2e4"]','learning',%s,'white',%s
        FROM generate_series(1,%s) n""", (IDENTITY, IDENTITY, FEN, day, now, queued_count + 2))
    db.execute('INSERT INTO repertoire_cards(repertoire_id,card_id) SELECT %s,id FROM cards WHERE repertoire_id=%s', (IDENTITY, IDENTITY))
    db.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,published_at) VALUES(%s,1,%s)", (IDENTITY, now))
    db.execute("""INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,card_id,decision_fen_key,starting_fen,moves_json,trained_color)
        SELECT %s,1,%s,n,id,%s,%s,'[\"e2e4\"]','white' FROM cards
        JOIN generate_series(1,%s) n ON id=%s||'-'||lpad(n::text,5,'0')""", (IDENTITY, IDENTITY, FEN, FEN, queued_count+2, IDENTITY))
    db.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(%s,'correct',%s,1,7)", (f'{IDENTITY}-00001', now))
    # Explicit IDs permit identical snapshots on a fresh database across runs.
    db.execute("""INSERT INTO daily_queue(id,queue_date,card_id,position,status,attempt_state,card_bucket,admission_kind,admission_repertoire_id)
        SELECT n,%s,%s||'-'||lpad(n::text,5,'0'),n,
          CASE WHEN n=%s THEN 'blocked' WHEN n=%s THEN 'complete' ELSE 'queued' END,
          CASE WHEN n<=%s THEN 'in_progress' ELSE 'clean' END,'opening','due',%s
        FROM generate_series(1,%s) n""", (day, IDENTITY, queued_count+1, queued_count+2, issued_count, IDENTITY, queued_count+2))
    db.execute("SELECT setval(pg_get_serial_sequence('daily_queue','id'), %s, true)", (queued_count + 2,))
    db.execute("UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id<=%s", (issued_count,))
    db.execute("INSERT INTO daily_queue_days(queue_date,seed,membership_hash,generated_at) VALUES(%s,1,%s,%s)", (day, IDENTITY, now))
    db.execute("INSERT INTO queue_projections(queue_date,state,generation,refresh_pending,blocked_count) VALUES(%s,'refreshing',1,1,1)", (day,))
    payload = json.dumps({'queue_date': day, '_queue_phase': 'publish_projection'}, sort_keys=True)
    db.execute("""INSERT INTO background_tasks(id,kind,deduplication_key,generation,state,phase,payload_json,next_attempt_at,lease_token,lease_expires_at,created_at,updated_at)
        VALUES(%s,'daily_queue',%s,1,'leased','publish_projection',%s,%s,%s,%s,%s,%s)""",
        (IDENTITY, day, payload, now, IDENTITY+'-lease', '2099-01-01T00:00:00+00:00', now, now))
    if frozen_active:
        db.execute("INSERT INTO frozen_opening_runs(run_id,plan_checksum,plan_json,created_at,reader_version) VALUES(%s,%s,'{}',%s,'fixture')", (IDENTITY, IDENTITY, now))
        db.execute("""INSERT INTO frozen_opening_ranks(run_id,card_id,ordinal,fingerprint,memberships_json,definition_json)
            SELECT %s,c.id,n,c.id,json_build_array(%s::text)::text,
              (jsonb_build_object('revision',c.revision,'start_fen',c.start_fen,'moves_json',c.moves_json,
              'trained_color',c.trained_color,'source_ref',c.source_ref,'source_fen',c.source_fen,
              'kind',c.kind,'content_type',c.content_type,'membership_count',1))::text
            FROM cards c JOIN generate_series(1,%s) n ON c.id=%s||'-'||lpad(n::text,5,'0')""", (IDENTITY, IDENTITY, queued_count+2, IDENTITY))
        db.execute('UPDATE frozen_opening_activation SET run_id=%s,epoch=1 WHERE id=1', (IDENTITY,))
    ids = tuple(range(1, queued_count+1))
    issued = ids[:issued_count]
    return CaseFacts(day, ids, ids[issued_count:issued_count+changed_kind_count], issued,
                     queued_count+1, queued_count+2, IDENTITY,
                     protected_hash(db, (*issued, queued_count+1, queued_count+2)))
