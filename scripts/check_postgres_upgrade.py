"""Rehearse historical upgrades and populated current-main evidence admission."""

from __future__ import annotations

import os
from pathlib import Path
import sys
import uuid
import json
from datetime import date, datetime, timezone
from unittest.mock import patch

import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.apply_postgres_migrations import MIGRATIONS, apply_migrations
from backend.app.schema_version import POSTGRES_SCHEMA_VERSION
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.postgres_store import PostgresConnection, tempo_row_factory
from app.services.repertoire_opportunities import _publish, _stable_id, list_opportunities
from app import postgres_store, discovery_commands
from app.services import discovery_admission


DISCOVERY_FEN_KEY = "4k3/8/8/8/8/8/8/4K3 w - -"


def test_postgres_handled_upgrade_requires_matching_queued_revision_and_card(dsn: str) -> None:
    expected = {
        "matching-queued": ("A", "queued", "known-plural", '{"supporting_games":5}'),
        "mismatched-queued": ("B", None, None, None),
        "ambiguous-queued": ("B", None, None, None),
        "wrong-card-queued": ("A", None, None, None),
        "unfinished-intent": ("A", None, None, None),
        "preparing": ("A", "preparing", "known-plural", None),
        "failed": ("A", "failed", "known-plural", None),
    }
    with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw_database:
        database = PostgresConnection(raw_database)
        for target, expected_state in expected.items():
            identifier = _stable_id("preserved-repertoire", "weak_known_decision", DISCOVERY_FEN_KEY, target)
            row = database.execute("SELECT evidence_fingerprint,admission_state,admitted_card_id,handled_evidence_json,evidence_json,card_id FROM repertoire_opportunities WHERE id=?", (identifier,)).fetchone()
            assert tuple(row)[:4] == expected_state, target
            assert row["evidence_json"] == '{"supporting_games":5}' and row["card_id"] == "known-plural", target
        visible = {item["id"]: item for item in list_opportunities(database, "preserved-repertoire")}
        assert set(visible) == {_stable_id("preserved-repertoire", "weak_known_decision", DISCOVERY_FEN_KEY, target)
                                for target in expected if target != "matching-queued"}
        assert tuple(database.execute("SELECT state,evidence_fingerprint,card_id FROM discovery_admission_intents WHERE id='legacy:mismatched-queued'").fetchone()) == ("queued", "A", "known-plural")
    print("PASS revision-aware PostgreSQL 027 backfill: matching handled; mismatch, ambiguity, wrong card and unfinished intent remain actionable; preparing/failed unhandled")


def test_handled_discovery_postgres_upgrade_and_material_evidence_replay(dsn: str) -> None:
    opportunity_id = _stable_id("preserved-repertoire", "weak_known_decision", DISCOVERY_FEN_KEY, "matching-queued")
    with psycopg.connect(dsn, row_factory=tempo_row_factory) as raw_database:
        database = PostgresConnection(raw_database)
        row = database.execute("SELECT handled_evidence_json FROM repertoire_opportunities WHERE id=?",
                               (opportunity_id,)).fetchone()
        assert row[0] == '{"supporting_games":5}'
        assert database.execute("SELECT COUNT(*) FROM repertoire_opportunities WHERE handled_evidence_json IS NOT NULL").fetchone()[0] == 1
        def publish(supporting_games):
            _publish(database, repertoire_id="preserved-repertoire", kind="weak_known_decision",
                     fen_key=DISCOVERY_FEN_KEY, target="matching-queued", card_id=None,
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


def test_postgres_stale_admission_and_revisioned_same_move_replay(dsn: str) -> None:
    """Use real transactions, leases, unique constraints, and queue persistence."""
    from app.main import discoveries_feed
    repertoire_id = "revision-lifecycle"
    starting_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    fen_key = " ".join(starting_fen.split()[:4])
    opportunity_id = _stable_id(repertoire_id, "weak_known_decision", fen_key, "e2e4")
    original_urls = {name: os.environ.get(name) for name in ("TEMPO_DATABASE_WRITE_URL", "TEMPO_DATABASE_READ_URL")}
    postgres_store.close_pools()
    for name in original_urls:
        os.environ[name] = dsn
    try:
        with postgres_store.connection() as database:
            now = datetime.now(timezone.utc).isoformat()
            database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(?,?,?,?)",
                             (repertoire_id, "Revision lifecycle", "regression", now))
            _publish(database, repertoire_id=repertoire_id, kind="weak_known_decision", fen_key=fen_key,
                     target="e2e4", card_id=None, opponent_move_uci=None, score=1, evidence={"supporting_games": 5})
            fingerprint_a = database.execute("SELECT evidence_fingerprint FROM repertoire_opportunities WHERE id=?",
                                             (opportunity_id,)).fetchone()[0]
            payload = {"opportunity_id": opportunity_id, "selected_move_uci": "e2e4",
                       "evidence_fingerprint": fingerprint_a, "repertoire_id": repertoire_id,
                       "starting_fen": starting_fen, "preview_moves_uci": ["e2e4"],
                       "recommendation": {"move_uci": "e2e4", "preview_moves_uci": ["e2e4"]}}
            intent_a = discovery_commands.accept_discovery(database, payload)["intent_id"]
            line_id = database.execute("SELECT line_id FROM discovery_admission_intents WHERE id=?", (intent_a,)).fetchone()[0]
            database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(?,?,'Accepted','white',?,'[\"e2e4\"]',?)",
                             (line_id, repertoire_id, starting_fen, now))
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color) VALUES('revision-card',?,'response',?,'[\"e2e4\"]','new',?,'opening','white')",
                             (repertoire_id, starting_fen, date.today().isoformat()))
            database.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES(?,'revision-card')", (repertoire_id,))
            database.execute("INSERT INTO opening_graph_publications(repertoire_id,generation,published_at) VALUES(?,1,?)", (repertoire_id, now))
            database.execute("INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json,card_id,decision_fen_key,starting_fen,moves_json,trained_color) VALUES(?,1,?,0,'decision',0,0,?,'revision-card',?,?,'[\"e2e4\"]','white')",
                             (repertoire_id, line_id, json.dumps([fen_key]), fen_key, starting_fen))
            database.execute("INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at,scan_status) VALUES(?,'clean',?,'idle')",
                             (repertoire_id, datetime.now(timezone.utc).isoformat()))
        def lease(intent_id):
            with postgres_store.connection() as database:
                row = database.execute("UPDATE background_tasks SET state='leased',lease_token=? WHERE kind='discovery_admission' AND deduplication_key=? RETURNING *",
                                       (f"lease:{intent_id}", intent_id)).fetchone()
                return {**dict(row), "payload": {"intent_id": intent_id}}
        task_a = lease(intent_a)
        with postgres_store.connection() as database:
            _publish(database, repertoire_id=repertoire_id, kind="weak_known_decision", fen_key=fen_key,
                     target="e2e4", card_id=None, opponent_move_uci=None, score=1, evidence={"supporting_games": 8})
            fingerprint_b = database.execute("SELECT evidence_fingerprint FROM repertoire_opportunities WHERE id=?", (opportunity_id,)).fetchone()[0]
            assert fingerprint_b != fingerprint_a
        # Engine/graph work is already published. Only the actual bounded admission
        # transaction is under test; no network or derived-work sweep is required.
        with patch.object(discovery_admission, "_materialize_admission_branch"), patch.object(discovery_admission, "_ensure_admission_coverage_refresh"):
            assert discovery_admission.execute_admission_intent_slice(task_a) is False
            assert discovery_admission.execute_admission_intent_slice(task_a) is False
            postgres_store.close_pools()  # Verify through newly opened connections.
            with postgres_store.connection() as database:
                assert database.execute("SELECT state FROM discovery_admission_intents WHERE id=?", (intent_a,)).fetchone()[0] == "queued"
                current = database.execute("SELECT * FROM repertoire_opportunities WHERE id=?", (opportunity_id,)).fetchone()
                assert current["evidence_fingerprint"] == fingerprint_b
                assert current["handled_evidence_json"] is None and current["admission_state"] is None
                assert current["card_id"] is None
                assert [item["id"] for item in list_opportunities(database, repertoire_id)] == [opportunity_id]
                payload_b = {**payload, "evidence_fingerprint": fingerprint_b}
                intent_b = discovery_commands.accept_discovery(database, payload_b)["intent_id"]
                assert intent_b != intent_a
                assert discovery_commands.accept_discovery(database, payload_b)["intent_id"] == intent_b
                assert database.execute("SELECT COUNT(*) FROM discovery_admission_intents WHERE opportunity_id=?", (opportunity_id,)).fetchone()[0] == 2
                database.execute("UPDATE repertoire_integrity_state SET checked_at=? WHERE repertoire_id=?",
                                 (datetime.now(timezone.utc).isoformat(), repertoire_id))
            assert opportunity_id in {item["id"] for item in discoveries_feed()["discoveries"]}
            task_b = lease(intent_b)
            assert discovery_admission.execute_admission_intent_slice(task_b) is False
            assert discovery_admission.execute_admission_intent_slice(task_b) is False
        postgres_store.close_pools()
        with postgres_store.connection() as database:
            assert database.execute("SELECT handled_evidence_json=evidence_json FROM repertoire_opportunities WHERE id=?", (opportunity_id,)).fetchone()[0]
            assert database.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='revision-card'").fetchone()[0] == 1
            assert database.execute("SELECT COUNT(*) FROM reviews WHERE card_id='revision-card'").fetchone()[0] == 0
            assert database.execute("SELECT state FROM discovery_admission_intents WHERE id=?", (intent_a,)).fetchone()[0] == "queued"
        assert opportunity_id not in {item["id"] for item in discoveries_feed()["discoveries"]}
        test_postgres_direct_training_requires_current_reviewed_revision(repertoire_id, opportunity_id, fen_key)
    finally:
        postgres_store.close_pools()
        for name, value in original_urls.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    print("PASS real PostgreSQL stale admission, revisioned same-move acceptance, reconnect, and replay")


def test_postgres_direct_training_requires_current_reviewed_revision(repertoire_id: str, opportunity_id: str, fen_key: str) -> None:
    from fastapi import HTTPException
    from app import opportunity_commands
    with postgres_store.connection() as database:
        old_fingerprint = database.execute("SELECT evidence_fingerprint FROM repertoire_opportunities WHERE id=?", (opportunity_id,)).fetchone()[0]
        _publish(database, repertoire_id=repertoire_id, kind="weak_known_decision", fen_key=fen_key,
                 target="e2e4", card_id="revision-card", opponent_move_uci=None, score=1,
                 evidence={"supporting_games": 11})
        reviewed_fingerprint = database.execute("SELECT evidence_fingerprint FROM repertoire_opportunities WHERE id=?", (opportunity_id,)).fetchone()[0]
        assert reviewed_fingerprint != old_fingerprint
    for fingerprint in (None, old_fingerprint):
        with postgres_store.connection() as database:
            payload = {"repertoire_id": repertoire_id, "opportunity_id": opportunity_id}
            if fingerprint is not None:
                payload["evidence_fingerprint"] = fingerprint
            try:
                opportunity_commands.train_opportunity(database, payload)
            except HTTPException as error:
                assert error.status_code == 409
            else:
                raise AssertionError("Legacy or stale Train must not handle current evidence")
            assert database.execute("SELECT handled_evidence_json,admission_state FROM repertoire_opportunities WHERE id=?", (opportunity_id,)).fetchone()[0] is None
            assert database.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='revision-card'").fetchone()[0] == 1
            assert [item["id"] for item in list_opportunities(database, repertoire_id)] == [opportunity_id]
    with postgres_store.connection() as database:
        payload = {"repertoire_id": repertoire_id, "opportunity_id": opportunity_id,
                   "evidence_fingerprint": reviewed_fingerprint}
        assert opportunity_commands.train_opportunity(database, payload)["idempotent"] is False
    postgres_store.close_pools()
    with postgres_store.connection() as database:
        assert opportunity_commands.train_opportunity(database, payload)["idempotent"] is True
        assert database.execute("SELECT handled_evidence_json=evidence_json FROM repertoire_opportunities WHERE id=?", (opportunity_id,)).fetchone()[0]
        assert list_opportunities(database, repertoire_id) == []
        assert database.execute("SELECT COUNT(*) FROM daily_queue WHERE card_id='revision-card'").fetchone()[0] == 1
        assert database.execute("SELECT COUNT(*) FROM reviews WHERE card_id='revision-card'").fetchone()[0] == 0
    print("PASS real PostgreSQL direct Train rejects missing/stale revision, preserves current evidence, confirms matching revision and replays after reconnect")


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
            legacy_cases = {
                "matching-queued": ("queued", "A", "queued", "A", "known-plural"),
                "mismatched-queued": ("queued", "B", "queued", "A", "known-plural"),
                "ambiguous-queued": ("queued", "B", None, None, None),
                "wrong-card-queued": ("queued", "A", "queued", "A", "unknown-plural"),
                "unfinished-intent": ("queued", "A", "preparing", "A", "known-plural"),
                "preparing": ("preparing", "A", "preparing", "A", "known-plural"),
                "failed": ("failed", "A", "failed", "A", "known-plural"),
            }
            for target, (opportunity_state, fingerprint, intent_state, accepted_fingerprint, intent_card) in legacy_cases.items():
                opportunity_id = _stable_id("preserved-repertoire", "weak_known_decision", DISCOVERY_FEN_KEY, target)
                database.execute(
                    "INSERT INTO repertoire_opportunities(id,repertoire_id,kind,fen_key,card_id,status,score,evidence_json,evidence_fingerprint,created_at,updated_at,admission_state,admitted_card_id,seen_at) "
                    "VALUES(%s,'preserved-repertoire','weak_known_decision',%s,'known-plural','active',1,'{\"supporting_games\":5}',%s,'2026-01-01','2026-01-01',%s,'known-plural','2026-01-01')",
                    (opportunity_id, DISCOVERY_FEN_KEY, fingerprint, opportunity_state),
                )
                if intent_state:
                    intent_id = "legacy-admission" if target == "matching-queued" else f"legacy:{target}"
                    database.execute("INSERT INTO discovery_admission_intents(id,opportunity_id,repertoire_id,evidence_fingerprint,starting_fen,selected_move_uci,preview_moves_json,recommendation_json,line_id,state,card_id,created_at,updated_at) VALUES(%s,%s,'preserved-repertoire',%s,'4k3/8/8/8/8/8/8/4K3 w - - 0 1','e1d2','[\"e1d2\"]','{}','legacy-line',%s,%s,'2026-01-01','2026-01-01')",
                                     (intent_id, opportunity_id, accepted_fingerprint, intent_state, intent_card))
            # Seed before the evidence migration, even when later migrations are present.
            evidence_migration_version = 30
            for migration in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
                if 16 < int(migration.name[:3]) < evidence_migration_version:
                    database.execute(migration.read_text(), prepare=False)
                    database.commit()
            main_versions = [row[0] for row in database.execute(
                "SELECT version FROM tempo_schema_migrations ORDER BY version").fetchall()]
            assert main_versions == list(range(1, evidence_migration_version))
            assert main_versions[-1] == 29
            assert database.execute("SELECT to_regclass('opening_evidence_presentations')").fetchone()[0] is None
            database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,revision,due_date) VALUES('opening-upgrade','preserved-repertoire','prefix','rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1','[\"e2e4\"]','white',7,'2026-01-01')")
            database.execute("INSERT INTO daily_queue(queue_date,card_id,position,card_bucket) VALUES('2026-01-01','opening-upgrade',99,'opening')")
            database.execute("INSERT INTO reviews(card_id,rating,reviewed_at,previous_interval,next_interval) VALUES('opening-upgrade','correct','2026-01-02',1,17)")
            for trained_color in ('white', 'black'):
                legacy_id = 'legacy-'+trained_color+'-upgrade'
                starting_fen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
                legacy_moves = json.dumps(['e2e4'] if trained_color=='white' else ['e2e4','e7e5'])
                database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,'Legacy upgrade','upgrade-test','2026-01-01')", (legacy_id,))
                database.execute("INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES(%s,%s,'Legacy line',%s,%s,%s,'2026-01-01')",
                                 (legacy_id+'-line', legacy_id, trained_color, starting_fen, legacy_moves))
                database.execute("INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,trained_color,revision,due_date) VALUES(%s,%s,'prefix',%s,%s,NULL,3,'2026-01-01')",
                                 (legacy_id, legacy_id, starting_fen, legacy_moves))
                # No admission binding: a single eligible owner must backfill correctly.
                database.execute("INSERT INTO daily_queue(queue_date,card_id,position,card_bucket) VALUES('2026-01-01',%s,100,'opening')", (legacy_id,))

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
                ("known-plural", "tactic", 1, 17), ("legacy-black-upgrade", "opening", 0, 0),
                ("legacy-white-upgrade", "opening", 0, 0), ("opening-upgrade", "opening", 0, 0), ("unknown-plural", "tactics", 1, 17)]
            assert database.execute("SELECT card_id,card_bucket FROM daily_queue ORDER BY card_id").fetchall() == [
                ("known-plural", "tactic"), ("legacy-black-upgrade", "opening"),
                ("legacy-white-upgrade", "opening"), ("opening-upgrade", "opening"), ("unknown-plural", "tactics")]
            assert database.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 3
            assert database.execute("SELECT card_id,revision,trained_color FROM opening_evidence_presentations ORDER BY card_id").fetchall() == [
                ('legacy-black-upgrade',3,None), ('legacy-white-upgrade',3,None), ('opening-upgrade',7,'white')]
            assert database.execute("SELECT repertoire_id,effective_trained_color FROM opening_evidence_queue_contexts ORDER BY repertoire_id").fetchall() == [
                ('legacy-black-upgrade','black'), ('legacy-white-upgrade','white'), ('preserved-repertoire','white')]
            assert database.execute("SELECT COUNT(*) FROM opening_evidence_queue_contexts WHERE repertoire_id='preserved-repertoire'").fetchone()[0] == 1
            assert database.execute("SELECT COUNT(*) FROM opening_evidence_observations").fetchone()[0] == 0
            assert database.execute("SELECT COUNT(*) FROM opening_evidence_attempts").fetchone()[0] == 0
            print(f"PASS test_postgres_current_main_upgrade_captures_legacy_evidence_contexts schema29->{POSTGRES_SCHEMA_VERSION}")
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
        with psycopg.connect(rehearsal_dsn) as database:
            assert database.execute("SELECT state,evidence_fingerprint FROM discovery_admission_intents WHERE id='legacy-admission'").fetchone() == ("queued", "A")
            database.execute("INSERT INTO discovery_admission_intents SELECT 'resurfaced-admission',opportunity_id,repertoire_id,'B',starting_fen,selected_move_uci,preview_moves_json,recommendation_json,line_id,state,card_id,last_error,created_at,updated_at FROM discovery_admission_intents WHERE id='legacy-admission' ON CONFLICT DO NOTHING")
            assert database.execute("SELECT COUNT(*) FROM discovery_admission_intents WHERE selected_move_uci='e1d2'").fetchone()[0] == 7
        test_postgres_handled_upgrade_requires_matching_queued_revision_and_card(rehearsal_dsn)
        test_handled_discovery_postgres_upgrade_and_material_evidence_replay(rehearsal_dsn)
        test_postgres_stale_admission_and_revisioned_same_move_replay(rehearsal_dsn)
    finally:
        with psycopg.connect(admin_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                sql.Identifier(database_name)
            ))
    print("PASS populated schema 16 upgrade, idempotent repeat, and preserved business rows")


if __name__ == "__main__":
    main()
