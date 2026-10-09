"""Regular disposable PostgreSQL regression for large current/historical graphs."""
from datetime import date
import json
import os
from pathlib import Path
import sys
import time

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app import postgres_store
from app.main import _unlock_eligible_opening_cards, _OPENING_UNLOCK_ELIGIBILITY_SQL


def proof_graph_scale(database_url: str, *, compare_baseline: bool = False):
    if os.getenv('TEMPO_TEST_INSTANCE') != 'disposable':
        raise RuntimeError('Queue scale proof requires an explicitly disposable database')
    with psycopg.connect(database_url, row_factory=postgres_store.tempo_row_factory) as raw:
        # Session-local fixtures disappear on disconnect and cannot change the
        # caller's application tables. Both selectors use identical indexes.
        raw.execute('CREATE TEMP TABLE reviews(card_id TEXT,source_kind TEXT,invalidated_at TEXT)')
        raw.execute('CREATE INDEX ON reviews(card_id)')
        raw.execute('CREATE TEMP TABLE cards(id TEXT PRIMARY KEY,content_type TEXT,state TEXT,archived INTEGER)')
        raw.execute('CREATE TEMP TABLE opening_graph_publications(repertoire_id TEXT PRIMARY KEY,generation BIGINT)')
        raw.execute('CREATE TEMP TABLE opening_graph_steps(repertoire_id TEXT,generation BIGINT,card_id TEXT,parent_card_id TEXT)')
        raw.execute("INSERT INTO cards SELECT 'locked-'||lpad(n::text,6,'0'),'opening','locked',0 FROM generate_series(0,99999) n")
        raw.execute("INSERT INTO cards VALUES('learning-parent','opening','learning',0),('practiced-parent','opening','learning',0),('mature-parent','opening','mature',0)")
        raw.execute("INSERT INTO reviews VALUES('practiced-parent','study',NULL)")
        raw.execute("INSERT INTO opening_graph_publications VALUES('current',2)")
        raw.execute("INSERT INTO opening_graph_steps SELECT 'current',2,'locked-'||lpad((n%100000)::text,6,'0'),'learning-parent' FROM generate_series(0,799999) n")
        raw.execute("INSERT INTO opening_graph_steps SELECT 'current',1,'locked-'||lpad(n::text,6,'0'),NULL FROM generate_series(0,99999) n")
        raw.execute("INSERT INTO opening_graph_steps SELECT 'current',2,'locked-'||lpad(n::text,6,'0'),NULL FROM generate_series(99979,99998) n")
        raw.execute("INSERT INTO opening_graph_steps VALUES('current',2,'locked-099999','practiced-parent')")
        raw.execute('CREATE INDEX ON opening_graph_steps(card_id,repertoire_id,generation,parent_card_id)')
        raw.execute('CREATE INDEX ON opening_graph_steps(repertoire_id,generation,card_id NULLS FIRST) WHERE parent_card_id IS NULL')
        raw.execute('CREATE INDEX ON opening_graph_steps(parent_card_id,card_id NULLS FIRST,repertoire_id,generation) WHERE parent_card_id IS NOT NULL')
        raw.execute("CREATE INDEX ON cards(id) WHERE content_type='opening' AND state='locked' AND archived=0")
        raw.execute("CREATE INDEX ON cards(id) WHERE state='mature'")
        raw.execute('ANALYZE reviews')
        raw.execute('ANALYZE cards')
        raw.execute('ANALYZE opening_graph_steps')
        raw.execute('ANALYZE opening_graph_publications')
        raw.commit()
        measurements = {}
        if compare_baseline:
            started = time.perf_counter()
            selected = raw.execute(postgres_store.postgres_sql(
                f'SELECT id FROM cards WHERE {_OPENING_UNLOCK_ELIGIBILITY_SQL} AND id>? ORDER BY id LIMIT ?'), ('',9)).fetchall()
            measurements['baseline_selector_seconds'] = time.perf_counter()-started
            print('BASELINE '+json.dumps(measurements),flush=True)
            assert len(selected) == 9
            raw.commit()
        cursor = ''
        durations = []
        wrapped = postgres_store.PostgresConnection(raw)
        while cursor is not None:
            started = time.perf_counter()
            with raw.transaction():
                raw.execute("SET LOCAL transaction_timeout='250ms'")
                cursor = _unlock_eligible_opening_cards(wrapped, date.today().isoformat(), after_card_id=cursor, batch_size=8)
            durations.append(time.perf_counter()-started)
        unlocked = [row[0] for row in raw.execute("SELECT id FROM cards WHERE state='new' ORDER BY id")]
        assert unlocked == [f'locked-{number:06}' for number in range(99979,100000)]
        assert len(durations) == 3, durations
        measurements.update(fixture_cards=100000,fixture_graph_steps=900021,
                            slice_count=len(durations),maximum_slice_seconds=max(durations))
        print('PASS test_postgres_daily_queue_large_graph_unlock_meets_transaction_budget '+json.dumps(measurements))
        return measurements


if __name__ == '__main__':
    proof_graph_scale(os.getenv('TEMPO_QUEUE_PROOF_URL','postgresql://postgres@postgres:5432/tempo'),
                      compare_baseline='--compare' in sys.argv)
