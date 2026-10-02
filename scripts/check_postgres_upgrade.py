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
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.postgres_store import PostgresConnection, tempo_row_factory
from app.services.repertoire_opportunities import _publish, _stable_id


DISCOVERY_FEN_KEY = "4k3/8/8/8/8/8/8/4K3 w - -"


def test_handled_discovery_postgres_upgrade_and_material_evidence_replay(dsn: str) -> None:
    opportunity_id = _stable_id("preserved-repertoire", "weak_known_decision", DISCOVERY_FEN_KEY, "queued")
    with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw_database:
        database = PostgresConnection(raw_database)
        row = database.execute("SELECT handled_evidence_json FROM repertoire_opportunities WHERE id=?",
                               (opportunity_id,)).fetchone()
        assert row[0] == '{"supporting_games":5}'
        assert database.execute("SELECT COUNT(*) FROM repertoire_opportunities WHERE handled_evidence_json IS NOT NULL").fetchone()[0] == 1
        def publish(supporting_games):
            _publish(database, repertoire_id="preserved-repertoire", kind="weak_known_decision",
                     fen_key=DISCOVERY_FEN_KEY, target="queued", card_id=None,
                     opponent_move_uci=None, score=1, evidence={"supporting_games": supporting_games})
        publish(7)
        assert database.execute("SELECT handled_evidence_json FROM repertoire_opportunities WHERE id=?",
                                (opportunity_id,)).fetchone()[0] is not None
        publish(8)
        row = database.execute("SELECT handled_evidence_json,admission_state,admitted_card_id,seen_at,card_id FROM repertoire_opportunities WHERE id=?",
                               (opportunity_id,)).fetchone()
        assert tuple(row) == (None, None, None, None, "known-plural")
        publish(8)
        assert database.execute("SELECT COUNT(*) FROM repertoire_opportunities WHERE id=?",
                                (opportunity_id,)).fetchone()[0] == 1
        assert database.execute("SELECT card_id FROM repertoire_opportunities WHERE id=?",
                                (opportunity_id,)).fetchone()[0] == "known-plural"
    with psycopg.connect(dsn) as database:
        assert database.execute("SELECT handled_evidence_json,admission_state FROM repertoire_opportunities WHERE id=%s",
                                (opportunity_id,)).fetchone() == (None, None)
    print("PASS handled discovery PostgreSQL upgrade and material-evidence replay")


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
            for admission_state in ("queued", "preparing", "failed"):
                opportunity_id = _stable_id("preserved-repertoire", "weak_known_decision", DISCOVERY_FEN_KEY, admission_state)
                database.execute(
                    "INSERT INTO repertoire_opportunities(id,repertoire_id,kind,fen_key,card_id,status,score,evidence_json,evidence_fingerprint,created_at,updated_at,admission_state,admitted_card_id,seen_at) "
                    "VALUES(%s,'preserved-repertoire','weak_known_decision',%s,'known-plural','active',1,'{\"supporting_games\":5}','revision','2026-01-01','2026-01-01',%s,'known-plural','2026-01-01')",
                    (opportunity_id, DISCOVERY_FEN_KEY, admission_state),
                )
            database.commit()
        apply_migrations(rehearsal_dsn)
        apply_migrations(rehearsal_dsn)
        with psycopg.connect(rehearsal_dsn) as database:
            versions = [row[0] for row in database.execute(
                "SELECT version FROM tempo_schema_migrations ORDER BY version"
            ).fetchall()]
            assert versions == list(range(1, POSTGRES_SCHEMA_VERSION + 1))
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
        test_handled_discovery_postgres_upgrade_and_material_evidence_replay(rehearsal_dsn)
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                sql.Identifier(database_name)
            ))
    print("PASS populated schema 16 upgrade, idempotent repeat, and preserved business rows")


if __name__ == "__main__":
    main()
