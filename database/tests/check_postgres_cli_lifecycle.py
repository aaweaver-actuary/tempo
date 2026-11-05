"""Seed a stopped, empty disposable database at schema 16 for the CLI gate."""

from __future__ import annotations

# Preserve direct-script imports after relocation from scripts/.
import sys
from pathlib import Path
_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))
sys.path.insert(0, str(_REPOSITORY / "backend"))

import os
import argparse
import json
import re
from pathlib import Path

import psycopg

from database.tooling.apply_postgres_migrations import MIGRATIONS, migration_source
from database.tooling.provision_postgres_roles import provision
from tempo_legacy_database.ddl.schema_version import POSTGRES_SCHEMA_VERSION


ABANDON_CARD = "cli-synthetic-abandon-orphan"


def abandon_fixture(database, *, seed=False):
    """Fixed synthetic data only; caller already owns the disposable CLI stack."""
    versions = [row[0] for row in database.execute("SELECT version FROM tempo_schema_migrations ORDER BY version")]
    if POSTGRES_SCHEMA_VERSION < 58 or versions != list(range(1, POSTGRES_SCHEMA_VERSION + 1)):
        raise RuntimeError("Abandonment fixture requires the genuine current migration ledger, including 058")
    if seed:
        import chess
        moves = ["e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6", "d2d3", "f8c5", "c2c3"]
        board = chess.Board()
        for move in moves:
            board.push_uci(move)
        if database.execute("SELECT 1 FROM queue_attempt_origins WHERE card_id=%s", (ABANDON_CARD,)).fetchone():
            raise RuntimeError("Synthetic abandonment origin already exists")
        entry = database.execute("SELECT nextval(pg_get_serial_sequence('daily_queue','id'))").fetchone()[0]
        if entry == 13624:
            raise RuntimeError("Synthetic abandonment fixture must not use the user attempt identity")
        database.execute("""INSERT INTO queue_attempt_origins
            (queue_entry_id,card_id,revision,queue_date,cycle,admission_kind,admission_repertoire_id,
             attempt_failed,last_status,review_result_json,legacy,start_fen,moves_json,trained_color,content_type,
             issued_as_head,issued_at,issuance_sequence)
            VALUES(%s,%s,1,'2000-01-02',0,NULL,NULL,1,'buried',NULL,0,%s,%s,NULL,'opening',1,NULL,NULL)""",
            (entry, ABANDON_CARD, chess.STARTING_FEN, json.dumps(moves)))
    origin = database.execute("SELECT row_to_json(origin)::text FROM queue_attempt_origins origin WHERE card_id=%s",
                              (ABANDON_CARD,)).fetchall()
    if len(origin) != 1:
        raise RuntimeError("Exact synthetic abandonment origin is missing or ambiguous")
    identity = json.loads(origin[0][0])
    entry = identity["queue_entry_id"]
    snapshot = {"identity": {key: identity[key] for key in ("queue_entry_id", "card_id", "revision")},
                "origin_bytes": origin[0][0]}
    # Keep full stored rows (including FSRS, review clocks, failure markers and
    # other origins) in the comparison; do not project away accidental changes.
    for name, statement in {
        "origins": "SELECT row_to_json(stored)::text FROM queue_attempt_origins stored ORDER BY queue_entry_id,card_id,revision",
        "abandonments": "SELECT row_to_json(stored)::text FROM queue_attempt_abandonments stored ORDER BY queue_entry_id,card_id,revision",
        "cards": "SELECT row_to_json(stored)::text FROM cards stored ORDER BY id",
        "reviews": "SELECT row_to_json(stored)::text FROM reviews stored ORDER BY id",
        "queue": "SELECT row_to_json(stored)::text FROM daily_queue stored ORDER BY id",
        "review_receipts": "SELECT row_to_json(stored)::text FROM review_attempt_receipts stored ORDER BY queue_entry_id,card_id,attempt_id",
        "review_snapshots": "SELECT row_to_json(stored)::text FROM review_schedule_snapshots stored ORDER BY review_id",
        "grades": "SELECT row_to_json(stored)::text FROM study_position_grades stored ORDER BY effect_id",
    }.items():
        snapshot[name] = [row[0] for row in database.execute(statement)]
    snapshot["abandonment_receipt_count"] = database.execute("SELECT count(*) FROM queue_attempt_abandonments").fetchone()[0]
    snapshot["orphan_links"] = {}
    for table, column, value in (
        ("cards", "id", ABANDON_CARD), ("repertoire_cards", "card_id", ABANDON_CARD),
        ("daily_queue", "id", entry), ("review_attempt_receipts", "queue_entry_id", entry),
        ("opening_evidence_attempts", "queue_entry_id", entry),
        ("opening_evidence_presentations", "card_id", ABANDON_CARD),
        ("opening_evidence_queue_contexts", "queue_entry_id", entry),
        ("study_position_issued_plans", "queue_entry_id", entry),
    ):
        snapshot["orphan_links"][table] = database.execute(
            f"SELECT count(*) FROM {table} WHERE {column}=%s", (value,)).fetchone()[0]
    return snapshot


def main():
    if os.environ.get("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("CLI fixture requires an explicitly disposable database")
    dsn = os.environ["TEMPO_POSTGRES_ADMIN_URL"]
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--seed59", action="store_true", help="Seed populated historical data and advance only through schema 59")
    mode.add_argument("--seed-defense-v2", metavar="TARGET", help="Seed and describe the owned all-group Defense fixture")
    mode.add_argument("--history", action="store_true", help="Read only the original fixture history, allowing unrelated worker receipts")
    mode.add_argument("--normalization", action="store_true", help="Read the qualifying legacy migration-025 card and queue row")
    mode.add_argument("--seed-abandon-orphan", action="store_true", help="Seed one fixed synthetic orphan after the owned CLI target upgrades")
    mode.add_argument("--abandon-inspection", action="store_true", help="Read retained origin/receipt bytes and complete Study history for abandonment proof")
    options = parser.parse_args()
    if options.seed_defense_v2:
        from urllib.parse import urlparse
        if not re.fullmatch(r'tempo-pg-regressions-[a-z0-9-]+-cli', os.environ.get('TEMPO_TEST_COMPOSE_PROJECT', '')):
            raise RuntimeError('Defense fixture requires the owned CLI test project')
        parsed = urlparse(dsn)
        if parsed.hostname != 'postgres' or parsed.path != '/tempo' or parsed.username != 'postgres':
            raise RuntimeError('Defense fixture requires the owned disposable database')
        from database.tooling import verify_postgres_cli_state as guard
        keys = [['cli-defense-move','cli-defense-candidate-move','reviewed'],
                ['cli-defense-quiet','cli-defense-candidate-quiet','no_activity'],
                ['cli-defense-recognition','cli-defense-candidate-recognition','recognition_only']]
        with psycopg.connect(dsn) as database:
            database.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ WRITE')
            if database.execute('SELECT max(version) FROM tempo_schema_migrations').fetchone()[0] != 59:
                raise RuntimeError('Defense fixture requires the owned schema 59 database')
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES ('cli-defense-rep','synthetic','synthetic','2026-01-01')")
            for card in ('move','quiet','recognition'):
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES (%s,'cli-defense-rep','prefix','fen','[]','2026-01-01','defense')", ('cli-defense-'+card,))
            database.execute("INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES ('cli-defense-game','synthetic','synthetic','2026-01-01','rapid',0,'white','draw','fen','[]')")
            database.execute("INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,created_at,updated_at) VALUES ('cli-defense-finding','cli-defense-game',1,1,'synthetic',1,'{}','2026-01-01','2026-01-01')")
            for number, card in enumerate(('move','quiet','recognition'), 1):
                database.execute("INSERT INTO threat_training_candidates(id,finding_id,game_id,analysis_version,incident_id,player_ply,evidence_json,source_fingerprint,detector_version,policy_json,created_at,updated_at,card_id) VALUES (%s,'cli-defense-finding','cli-defense-game',1,%s,%s,'{}','synthetic',1,'{}','2026-01-01','2026-01-01',%s)", ('cli-defense-candidate-'+card,'cli-defense-incident-'+card,number,'cli-defense-'+card))
                database.execute("INSERT INTO daily_queue(id,queue_date,card_id,position,status) VALUES (%s,'2026-01-01',%s,%s,%s)", (920000+number,'cli-defense-'+card,number,'complete' if card=='move' else 'queued'))
            database.execute("INSERT INTO defense_recognition_submissions(attempt_id,candidate_id,queue_entry_id,exercise_revision,answer_json,recognition_correct,created_at) VALUES ('cli-defense-recognition-id','cli-defense-candidate-recognition',920003,7,'{}',1,'2026-01-01')")
            database.execute("INSERT INTO defense_recognition_submissions(attempt_id,candidate_id,queue_entry_id,exercise_revision,answer_json,recognition_correct,created_at) VALUES ('cli-defense-before-move','cli-defense-candidate-move',920001,7,'{}',1,'2026-01-01')")
            database.execute("INSERT INTO defense_attempts(attempt_id,candidate_id,card_id,queue_entry_id,exercise_revision,move_uci,grade_json,reviewed_at) VALUES ('cli-defense-move-id','cli-defense-candidate-move','cli-defense-move',920001,7,'e2e4','{\"status\":\"correct\"}','2026-01-01')")
            database.execute("INSERT INTO reviews(id,card_id,rating,reviewed_at,previous_interval,next_interval,source_kind,source_ref) VALUES (920001,'cli-defense-move','good','2026-01-02',1,2,'study','defense:cli-defense-move-id')")
            result={'card_id':'cli-defense-move','next_due':'2026-01-03','interval_days':1,'state':'learning','requeue_today':False,'requeue_after_cards':None,'stability':1.0,'scheduling_mode':'standard','hard_correct_streak':1,'suggest_shorter_prefix':False,'idempotent':False}
            database.execute('UPDATE daily_queue SET review_result_json=%s WHERE id=920001',(json.dumps(result),))
            guard._cut62_history_session(database)
            count, groups, digest = guard._cut62_draft_history_reference(database, keys)
            if (count, groups) != (3, {'no_activity':1,'recognition_only':1,'reviewed':1}):
                raise RuntimeError('Owned Defense fixture did not produce all three exact groups')
        print(json.dumps({'version':2,'target':options.seed_defense_v2,'cohort':'defense-history','keys':keys,
                          'expected':{'count':count,'groups':groups,'reference_digest':digest}}))
        return
    if options.seed_abandon_orphan or options.abandon_inspection:
        connection_options = {} if options.seed_abandon_orphan else {"options": "-c default_transaction_read_only=on"}
        with psycopg.connect(dsn, **connection_options) as database:
            snapshot = abandon_fixture(database, seed=options.seed_abandon_orphan)
        print(json.dumps(snapshot))
        return
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
    if options.seed59:
        # Use the migration owner's installed module resource, including 050.
        # This disposable fixture retains the same populated origin and ledger.
        with psycopg.connect(dsn) as database:
            for migration in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql"))[16:59]:
                with database.transaction():
                    database.execute(migration_source(migration), prepare=False)
    provision(dsn, Path("/run/secrets/reader_password").read_text().strip(),
              Path("/run/secrets/writer_password").read_text().strip())
    print(f"Seeded populated schema {59 if options.seed59 else 16} with preserved review, queue, and receipt history")


if __name__ == "__main__":
    main()
