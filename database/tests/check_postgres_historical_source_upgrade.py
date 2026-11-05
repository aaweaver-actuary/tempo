"""Prove populated historical upgrades without the retired profile runtime."""
import os
import secrets
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql

_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))
from database.tooling.apply_postgres_migrations import MIGRATIONS, apply_migrations, migration_source
from database.tooling.verify_postgres_cli_state import capture_cut62, verify_cut62, CUT62_RETIRED_TABLES, CUT62_PROTECTED_TABLES
from tempo_legacy_database.ddl.schema_version import POSTGRES_SCHEMA_VERSION

ADMIN_DSN = os.getenv("TEMPO_PROFILE_REHEARSAL_ADMIN_DSN", "postgresql://postgres@postgres:5432/postgres")
FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def apply_through(database, boundary):
    exists = database.execute("SELECT to_regclass('public.tempo_schema_migrations')").fetchone()[0]
    current = (database.execute("SELECT COALESCE(MAX(version), 0) FROM tempo_schema_migrations").fetchone()[0]
               if exists else 0)
    for migration in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
        version = int(migration.name[:3])
        if version > boundary:
            break
        if version <= current:
            continue
        database.execute(migration_source(migration), prepare=False)
        database.commit()


def seed(database):
    database.execute("INSERT INTO settings(id,lichess_username) VALUES(1,'historical')")
    database.execute("INSERT INTO studies(id,title,created_at,updated_at) VALUES('pgn-study','PGN','2026-01-01','2026-01-01')")
    database.execute("INSERT INTO study_chapters(id,study_id,title,position) VALUES('pgn-chapter','pgn-study','PGN',0)")
    database.execute("INSERT INTO study_sources(id,chapter_id,source_group_id,version,raw_pgn,sha256,filename,record_index,headers_json,diagnostics_json,valid,created_at) VALUES('pgn-source','pgn-chapter','pgn-group',1,%s,'synthetic','fixture.pgn',0,'{}','[]',1,'2026-01-01')", ('[Event "Synthetic"]\n\n1. e4 e5 *',))
    for identifier, timestamp in (
        ("offset", "2026-01-01T00:00:00-05:00"),
        ("naive", "2026-01-01T00:00:00"),
        ("malformed", "not-a-timestamp"),
        ("calendar", "2026-02-30T00:00:00Z"),
        ("displacement", "2026-01-01T00:00:00+99:00"),
    ):
        database.execute(
            "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,"
            "start_fen,moves_json,player_rating,opponent_rating) "
            "VALUES(%s,'lichess','historical',%s,'rapid',1,'white','1-0',%s,'[]',1450,1500)",
            (f"historical-{identifier}", timestamp, FEN),
        )


def seed_original_defense_attempt(database):
    database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES ('def-rep','Synthetic','synthetic','2026-01-01')")
    database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES ('def-card','def-rep','prefix',%s,'[]','2026-01-01','defense')", (FEN,))
    database.execute("INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,created_at,updated_at) VALUES ('def-finding','historical-offset',1,1,'synthetic',1,'{}','2026-01-01','2026-01-01')")
    database.execute("INSERT INTO threat_training_candidates(id,finding_id,game_id,analysis_version,incident_id,player_ply,evidence_json,source_fingerprint,detector_version,policy_json,created_at,updated_at,card_id) VALUES ('def-candidate','def-finding','historical-offset',1,'incident',1,'{}','synthetic',1,'{}','2026-01-01','2026-01-01','def-card')")
    database.execute("INSERT INTO defense_attempts(attempt_id,candidate_id,card_id,queue_entry_id,exercise_revision,move_uci,grade_json,reviewed_at) VALUES ('def-attempt','def-candidate','def-card',700002,1,'e2e4','{\"status\":\"incorrect\",\"diagnostic\":\"original\"}','2026-01-01')")
    return database.execute("SELECT * FROM defense_attempts ORDER BY attempt_id").fetchall()


def check_boundary_44(database):
    database.execute("SET TIME ZONE 'America/New_York'")
    parsed = dict(database.execute(
        "SELECT id,next_opponent_game_time(played_at) FROM imported_games WHERE username='historical'"))
    assert parsed == {
        "historical-offset": datetime(2026, 1, 1, 5, tzinfo=timezone.utc),
        "historical-naive": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "historical-malformed": None, "historical-calendar": None, "historical-displacement": None,
    }
    assert database.execute("SELECT next_opponent_game_time(NULL)").fetchone()[0] is None
    indexes = database.execute(
        "SELECT c.relname,i.indisvalid,i.indisready FROM pg_index i "
        "JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relname IN "
        "('idx_next_opponent_account_time','idx_next_opponent_speed_rating_time')").fetchall()
    assert len(indexes) == 2 and all(valid and ready for _, valid, ready in indexes)
    for table in ('next_opponent_accounts', 'next_opponent_snapshots'):
        assert database.execute('SELECT to_regclass(%s)', (table,)).fetchone()[0] == table
    for function in ('invalidate_next_opponent_inputs()', 'next_opponent_game_time(text)', 'protect_next_opponent_snapshot()'):
        assert database.execute('SELECT to_regprocedure(%s)', (function,)).fetchone()[0] is not None
    assert database.execute("SELECT 1 FROM pg_trigger WHERE tgrelid='imported_games'::regclass AND tgname='next_opponent_input_change' AND NOT tgisinternal").fetchone()


def check_upgrade(boundary):
    name = f"tempo_historical_upgrade_{uuid.uuid4().hex[:12]}"
    dsn = psycopg.conninfo.make_conninfo(ADMIN_DSN, dbname=name)
    with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        with psycopg.connect(dsn) as database:
            apply_through(database, boundary)
            seed(database)
            database.commit()
            assert [row[0] for row in database.execute(
                "SELECT version FROM tempo_schema_migrations ORDER BY version")] == list(range(1, boundary + 1))
            assert database.execute("SELECT to_regclass('next_opponent_accounts')").fetchone()[0] is None
            sources = database.execute("SELECT * FROM imported_games ORDER BY id").fetchall()
            pgns = database.execute("SELECT * FROM study_sources ORDER BY id").fetchall()
            cursor = database.execute("SELECT * FROM settings ORDER BY id")
            columns = sql.SQL(',').join(sql.Identifier(column.name) for column in cursor.description)
            settings = cursor.fetchall()
            turns = (database.execute("SELECT * FROM background_scheduling_turns").fetchall()
                     if boundary >= 43 else None)
            apply_through(database, 44)
            check_boundary_44(database)
            assert database.execute("SELECT * FROM imported_games ORDER BY id").fetchall() == sources
            apply_through(database, 61)
            defense_attempts = seed_original_defense_attempt(database)
            assert len(defense_attempts) == 1
            secret = secrets.token_bytes(32)
            witness = capture_cut62(database, secret)
            assert witness['version'] == 5 and len(CUT62_RETIRED_TABLES) == 12
            assert 'defense_attempts' in CUT62_PROTECTED_TABLES and 'defense_attempts' in witness['rows']
        for _ in range(2):
            apply_migrations(dsn, defense_zero=True)
            with psycopg.connect(dsn) as database:
                assert [row[0] for row in database.execute(
                    "SELECT version FROM tempo_schema_migrations ORDER BY version")] == list(range(1, POSTGRES_SCHEMA_VERSION + 1))
                assert database.execute("SELECT * FROM imported_games ORDER BY id").fetchall() == sources
                assert database.execute("SELECT * FROM study_sources ORDER BY id").fetchall() == pgns
                assert database.execute(sql.SQL("SELECT {} FROM settings ORDER BY id").format(columns)).fetchall() == settings
                if turns is not None:
                    assert database.execute("SELECT * FROM background_scheduling_turns").fetchall() == turns
                for index in ('idx_opening_graph_current_roots', 'idx_opening_graph_mature_parent_candidates',
                              'idx_cards_locked_openings', 'idx_cards_mature_parent'):
                    assert database.execute('SELECT to_regclass(%s)', (index,)).fetchone()[0] == index
                result = verify_cut62(database, witness, secret)
                assert result['version'] == 5 and set(result['retired']) == CUT62_RETIRED_TABLES
                assert database.execute("SELECT * FROM defense_attempts ORDER BY attempt_id").fetchall() == defense_attempts
                for retired in (*CUT62_RETIRED_TABLES,
                                'idx_next_opponent_account_time', 'idx_next_opponent_speed_rating_time'):
                    assert database.execute('SELECT to_regclass(%s)', (retired,)).fetchone()[0] is None
                for function in ('invalidate_next_opponent_inputs()', 'next_opponent_game_time(text)', 'protect_next_opponent_snapshot()'):
                    assert database.execute('SELECT to_regprocedure(%s)', (function,)).fetchone()[0] is None
                assert database.execute("SELECT 1 FROM pg_trigger WHERE tgrelid='imported_games'::regclass AND tgname='next_opponent_input_change' AND NOT tgisinternal").fetchone() is None
                assert {row[0] for row in database.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name='background_tasks' "
                    "AND column_name IN ('transaction_timeout_count','transaction_timeout_checkpoint')")} == {
                        'transaction_timeout_count', 'transaction_timeout_checkpoint'}
        print(f"PASS schema {boundary} historical source upgrade and migration 044")
    finally:
        with psycopg.connect(ADMIN_DSN, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def main():
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Historical upgrade proof requires a disposable PostgreSQL instance")
    for boundary in (39, 43):
        check_upgrade(boundary)


if __name__ == "__main__":
    main()
