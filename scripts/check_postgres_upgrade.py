"""Rehearse a populated 16-to-current upgrade on a disposable database."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import uuid

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.apply_postgres_migrations import MIGRATIONS, apply_migrations
from backend.app.schema_version import POSTGRES_SCHEMA_VERSION


def main() -> None:
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("Upgrade rehearsal requires a disposable PostgreSQL instance")
    database_name = f"tempo_upgrade_{uuid.uuid4().hex[:12]}"
    admin_dsn = "postgresql://postgres@postgres:5432/postgres"
    with psycopg.connect(admin_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name)))
    rehearsal_dsn = f"postgresql://postgres@postgres:5432/{database_name}"
    try:
        with psycopg.connect(rehearsal_dsn) as database:
            for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
                if int(path.name[:3]) > 16:
                    break
                database.execute(path.read_text(), prepare=False)
                database.commit()
            database.execute(
                "INSERT INTO repertoires(id,name,source_name,created_at) "
                "VALUES('preserved-repertoire','Preserved','upgrade-test','2026-09-29')"
            )
            database.execute(
                "INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,"
                "response_json) VALUES('preserved-receipt','test.save','hash','complete',"
                "'{\"saved\":true}')"
            )
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES('__game_tactics__','Game tactics','Synthetic','2026-01-01')")
            for identifier, owner in [('known-plural','__game_tactics__'), ('unknown-plural','preserved-repertoire')]:
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,content_type,due_date,archived,interval_days) "
                                 "VALUES(%s,%s,'checkpoint','4k3/8/8/8/8/8/8/4K3 w - - 0 1','[\"e1d2\"]','tactics','2026-01-01',1,17)", (identifier,owner))
                database.execute("INSERT INTO daily_queue(queue_date,card_id,position,card_bucket) VALUES('2026-01-01',%s,0,'tactics')", (identifier,))
                database.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES(%s,'correct','2026-01-02',1,17)", (identifier,))
            database.commit()
        apply_migrations(rehearsal_dsn)
        apply_migrations(rehearsal_dsn)
        with psycopg.connect(rehearsal_dsn) as database:
            versions = [row[0] for row in database.execute(
                "SELECT version FROM tempo_schema_migrations ORDER BY version"
            ).fetchall()]
            assert versions == list(range(1, POSTGRES_SCHEMA_VERSION + 1))
            assert database.execute("SELECT new_cards_per_day FROM repertoires WHERE id='preserved-repertoire'").fetchone()[0] is None
            assert database.execute(
                "SELECT name FROM repertoires WHERE id='preserved-repertoire'"
            ).fetchone()[0] == "Preserved"
            assert database.execute(
                "SELECT state,response_json,attempt_count,cycle_attempt_count,retry_cycle "
                "FROM operation_receipts WHERE operation_id='preserved-receipt'"
            ).fetchone() == ("complete", '{"saved":true}', 0, 0, 0)
            assert database.execute("SELECT id,content_type,archived,interval_days FROM cards ORDER BY id").fetchall() == [
                ("known-plural", "tactic", 1, 17), ("unknown-plural", "tactics", 1, 17)]
            assert database.execute("SELECT card_id,card_bucket FROM daily_queue ORDER BY card_id").fetchall() == [
                ("known-plural", "tactic"), ("unknown-plural", "tactics")]
            assert database.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 2
            assert database.execute("SELECT COUNT(*) FROM tactic_captures").fetchone()[0] == 0
            assert database.execute(
                "SELECT COUNT(*) FROM pg_indexes WHERE indexname="
                "'idx_threat_candidate_requests_request_role'"
            ).fetchone()[0] == 1
            assert database.execute(
                "SELECT COUNT(*) FROM pg_indexes WHERE indexname IN ("
                "'idx_threat_candidate_requests_role_request',"
                "'idx_threat_analysis_requests_state_created')"
            ).fetchone()[0] == 2
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                sql.Identifier(database_name)
            ))
    print("PASS populated schema 16 upgrade, idempotent repeat, and preserved business rows")


if __name__ == "__main__":
    main()
