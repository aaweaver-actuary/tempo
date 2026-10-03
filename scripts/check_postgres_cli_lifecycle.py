"""Seed a stopped, empty disposable database at schema 16 for the CLI gate."""

from __future__ import annotations

import os
import argparse
import json
from pathlib import Path

import psycopg

from apply_postgres_migrations import MIGRATIONS
from provision_postgres_roles import provision


def main():
    if os.environ.get("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("CLI fixture requires an explicitly disposable database")
    dsn = os.environ["TEMPO_POSTGRES_ADMIN_URL"]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--history", action="store_true", help="Read only the original fixture history, allowing unrelated worker receipts")
    parser.add_argument("--normalization", action="store_true", help="Read the qualifying legacy migration-025 card and queue row")
    options = parser.parse_args()
    if options.normalization:
        with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as database:
            record = database.execute("""SELECT json_build_object(
                'id',queue.id,'queue_date',queue.queue_date,'card_id',queue.card_id,
                'position',queue.position,'status',queue.status,'card_bucket',queue.card_bucket,
                'content_type',card.content_type,'repertoire_id',card.repertoire_id)
                FROM daily_queue queue JOIN cards card ON card.id=queue.card_id
                WHERE card.id='cli-legacy-tactical-miss'""").fetchone()
            if not record:
                raise RuntimeError("Qualifying migration-025 fixture is missing")
            print(json.dumps(record[0]))
        return
    if options.history:
        with psycopg.connect(dsn, options="-c default_transaction_read_only=on") as database:
            history = {}
            for name, statement in {
                "reviews": "SELECT id,card_id,rating,reviewed_at,previous_interval,next_interval FROM reviews WHERE card_id='cli-history' ORDER BY id",
                "queue": "SELECT id,queue_date,card_id,position,status FROM daily_queue WHERE card_id IN ('cli-history','cli-legacy-tactical-miss') ORDER BY id",
                "receipt": "SELECT operation_id,command_name,request_hash,state,response_json,error_json,created_at,updated_at FROM operation_receipts WHERE operation_id='cli-preserved-receipt'",
            }.items():
                history[name] = database.execute(statement).fetchall()
            print(json.dumps(history, default=str))
        return
    with psycopg.connect(dsn) as database:
        tables = database.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public'").fetchone()[0]
        if tables:
            raise RuntimeError("CLI fixture refuses to replace existing data")
        for migration in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))[:16]:
            database.execute(migration.read_text(), prepare=False)
        database.execute("INSERT INTO settings(id,new_cards_per_day,tactics_new_per_day) VALUES(1,0,0)")
        database.execute("INSERT INTO tactic_rotation(id) VALUES(1)")
        database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('cli-preserved','CLI preserved','regression','2000-01-01')")
        database.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,archived)
            VALUES('cli-history','cli-preserved','response',
                'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1','["e2e4"]','mature','2000-01-01',1)""")
        database.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES('cli-history','correct','2000-01-01',1,2)")
        database.execute("INSERT INTO daily_queue(queue_date,card_id,position,status) VALUES('2000-01-01','cli-history',0,'complete')")
        # This is migration 025's known game-tactics path, not an arbitrary
        # plural bucket. Archived historical rows stay outside current training.
        database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('__game_tactics__','Game tactics','regression','2000-01-01')")
        database.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,archived,content_type)
            VALUES('cli-legacy-tactical-miss','__game_tactics__','response',
                'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1','["e2e4"]','mature','2000-01-01',1,'tactics')""")
        database.execute("""INSERT INTO daily_queue(queue_date,card_id,position,status,card_bucket)
            VALUES('2000-01-01','cli-legacy-tactical-miss',1,'complete','tactics')""")
        database.execute("""INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,response_json)
            VALUES('cli-preserved-receipt','fixture','preserved','complete','{"ok":true}')""")
    provision(dsn, Path("/run/secrets/reader_password").read_text().strip(),
              Path("/run/secrets/writer_password").read_text().strip())
    print("Seeded populated schema 16 with preserved review, queue, and receipt history")


if __name__ == "__main__":
    main()
