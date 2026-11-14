"""Focused guard contract; PostgreSQL cutover proof is separately required."""

import copy
import json

import pytest
from database.tooling import verify_postgres_cli_state as guard


class FakeDatabase:
    def execute(self, query, params=None):
        assert query == "SELECT to_regclass(%s)"

        class Result:
            def fetchone(self):
                return (None,)

        return Result()


def test_cut62_guard_retains_full_fixed_ledger_and_rejects_tamper(monkeypatch):
    source = (
        guard.CUT62_PROTECTED_TABLES | guard.CUT62_RETIRED_TABLES | {guard.CUT62_LEDGER}
    )
    layout = {name: [["id"], [["id", "text", True]]] for name in source}
    state = {name: {"count": 1, "digest": name} for name in source}
    monkeypatch.setattr(guard, "_cut62_session", lambda database: None)
    monkeypatch.setattr(guard, "_cut62_versions", lambda database, version: None)
    monkeypatch.setattr(
        guard, "_cut62_profile_objects", lambda database, should_exist: None
    )
    monkeypatch.setattr(
        guard,
        "_cut62_catalog",
        lambda database, secret: {"count": 1, "digest": "catalog"},
    )
    monkeypatch.setattr(
        guard,
        "_cut62_sequences",
        lambda database, secret, source: {
            "protected": {"count": 9, "digest": "sequences"},
            "retired": {"count": int(source), "digest": "retired"},
        },
    )
    monkeypatch.setattr(
        guard,
        "_cut62_layout",
        lambda database, names: {name: layout[name] for name in names},
    )
    monkeypatch.setattr(
        guard, "_cut62_table", lambda database, name, key, secret: state[name].copy()
    )
    witness = guard.capture_cut62(FakeDatabase(), b"x" * 32)
    assert len(guard.CUT62_PROTECTED_TABLES) == 181
    assert guard.verify_cut62(FakeDatabase(), witness, b"x" * 32)["to"] == 62
    name = next(iter(guard.CUT62_PROTECTED_TABLES))
    state[name]["digest"] = "tampered"
    with pytest.raises(RuntimeError, match="retained rows changed"):
        guard.verify_cut62(FakeDatabase(), witness, b"x" * 32)
    changed = copy.deepcopy(witness)
    del changed["rows"][name]
    with pytest.raises(RuntimeError, match="coverage"):
        guard.verify_cut62(FakeDatabase(), changed, b"x" * 32)
    changed["version"] = 99
    with pytest.raises(RuntimeError, match="unsupported"):
        guard.verify_cut62(FakeDatabase(), changed, b"x" * 32)


def test_cut62_witness_is_private_exclusive_and_authenticated(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "capture_cut62", lambda database, secret: {"version": 1})
    path = tmp_path / "original.json"
    guard.save_cut62_witness(path, None)
    assert path.stat().st_mode & 0o777 == 0o600
    assert guard.load_cut62_witness(path)[0] == {"version": 1}
    with pytest.raises(FileExistsError):
        guard.save_cut62_witness(path, None)
    payload = json.loads(path.read_text())
    payload["witness"]["version"] = 2
    path.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="authentication"):
        guard.load_cut62_witness(path)


def test_cut62_layout_rejects_unexpected_table(monkeypatch):
    monkeypatch.setattr(
        guard, "table_layout", lambda db: {"extra": (("id",), (("id", "text", True),))}
    )
    with pytest.raises(RuntimeError, match="table coverage"):
        guard._cut62_layout(None, {"expected"})


def test_cut62_rejects_reintroduced_retired_relation(monkeypatch):
    source = (
        guard.CUT62_PROTECTED_TABLES | guard.CUT62_RETIRED_TABLES | {guard.CUT62_LEDGER}
    )
    witness = {
        "version": guard.CUT62_EVIDENCE_VERSION,
        "from": 61,
        "to": 62,
        "layout": dict.fromkeys(source),
        "rows": dict.fromkeys(source - {guard.CUT62_LEDGER}),
    }
    monkeypatch.setattr(guard, "_cut62_session", lambda database: None)
    monkeypatch.setattr(guard, "_cut62_versions", lambda database, version: None)
    monkeypatch.setattr(
        guard, "_cut62_profile_objects", lambda database, should_exist: None
    )

    class Database:
        def execute(self, query, params):
            class Result:
                def fetchone(self):
                    return ("still_exists",)

            return Result()

    with pytest.raises(RuntimeError, match="retired relation remains"):
        guard.verify_cut62(Database(), witness, b"x" * 32)


def test_cut62_sequence_drift_is_rejected(monkeypatch):
    source = (
        guard.CUT62_PROTECTED_TABLES | guard.CUT62_RETIRED_TABLES | {guard.CUT62_LEDGER}
    )
    layout = {name: [["id"], [["id", "text", True]]] for name in source}
    monkeypatch.setattr(guard, "_cut62_session", lambda database: None)
    monkeypatch.setattr(guard, "_cut62_versions", lambda database, version: None)
    monkeypatch.setattr(
        guard, "_cut62_profile_objects", lambda database, should_exist: None
    )
    monkeypatch.setattr(
        guard,
        "_cut62_layout",
        lambda database, names: {name: layout[name] for name in names},
    )
    monkeypatch.setattr(
        guard,
        "_cut62_table",
        lambda database, name, key, secret: {"count": 1, "digest": name},
    )
    monkeypatch.setattr(
        guard,
        "_cut62_catalog",
        lambda database, secret: {"count": 1, "digest": "catalog"},
    )
    monkeypatch.setattr(
        guard,
        "_cut62_sequences",
        lambda database, secret, source: {
            "protected": {"count": 9, "digest": "before"},
            "retired": {"count": int(source), "digest": "retired"},
        },
    )
    witness = guard.capture_cut62(FakeDatabase(), b"x" * 32)
    monkeypatch.setattr(
        guard,
        "_cut62_sequences",
        lambda database, secret, source: {
            "protected": {"count": 9, "digest": "after"},
            "retired": {"count": int(source), "digest": "retired"},
        },
    )
    with pytest.raises(RuntimeError, match="sequences changed"):
        guard.verify_cut62(FakeDatabase(), witness, b"x" * 32)


def test_cut62_native_schema61_to62(tmp_path):
    """Explicitly allocated empty local DB only; never an ambient/serving DB."""
    import os
    from urllib.parse import urlparse

    import psycopg
    from database.tooling.apply_postgres_migrations import (
        MIGRATIONS,
        apply_migrations,
        migration_source,
    )
    from psycopg.types.json import Jsonb

    dsn = os.environ.get("TEMPO_W07_NATIVE_REGRESSION_DSN")
    if not dsn:
        pytest.skip("W07 native disposable database not allocated")
    parsed = urlparse(dsn)
    if parsed.hostname != "127.0.0.1" or parsed.path != "/w07_regression":
        raise ValueError("refusing non-W07 disposable database")
    with psycopg.connect(dsn) as db:
        assert (
            db.execute(
                "SELECT to_regclass('public.tempo_schema_migrations')"
            ).fetchone()[0]
            is None
        )
        for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
            if int(path.name[:3]) <= 61:
                db.execute(migration_source(path), prepare=False)
        db.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES ('synthetic','synthetic','synthetic','2026-01-01')"
        )
        for card, kind in [
            ("opening", "opening"),
            ("defense", "defense"),
            ("endgame", "endgame"),
            ("tactic", "tactic"),
        ]:
            db.execute(
                "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES (%s,'synthetic','prefix','synthetic','[]','2026-01-01',%s)",
                (card, kind),
            )
        db.execute(
            "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES ('fixture-game','synthetic','synthetic','2026-01-01','rapid',0,'white','draw','synthetic','[]')"
        )
        db.execute(
            "INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,created_at,updated_at) VALUES ('finding','fixture-game',1,1,'synthetic',1,'{}','2026-01-01','2026-01-01')"
        )
        db.execute(
            "INSERT INTO threat_training_candidates(id,finding_id,game_id,analysis_version,incident_id,player_ply,evidence_json,source_fingerprint,detector_version,policy_json,created_at,updated_at,card_id) VALUES ('candidate','finding','fixture-game',1,'incident',1,'{}','synthetic',1,'{}','2026-01-01','2026-01-01','defense')"
        )
        db.execute(
            "INSERT INTO daily_queue(id,queue_date,card_id,position,status,review_result_json) VALUES (700002,'2026-01-01','defense',1,'complete','{\"scheduled\":true}')"
        )
        db.execute(
            "INSERT INTO reviews(id,card_id,rating,reviewed_at,previous_interval,next_interval,source_kind,source_ref,invalidated_at) VALUES (700002,'defense','again','2026-01-01',1,0,'study','defense:attempt','2026-01-02')"
        )
        db.execute(
            "INSERT INTO defense_attempts(attempt_id,candidate_id,card_id,queue_entry_id,exercise_revision,move_uci,grade_json,reviewed_at) VALUES ('attempt','candidate','defense',700002,1,'e2e4','{\"status\":\"incorrect\",\"diagnostic\":\"original\"}','2026-01-01')"
        )
        db.execute(
            "INSERT INTO endgame_templates(id,card_id,name,white_material,black_material,trained_color,goal_mix,created_at) VALUES ('template','endgame','synthetic','K','K','white','{}','2026-01-01')"
        )
        db.execute(
            "INSERT INTO endgame_attempts(id,template_id,start_fen,target,created_at) VALUES ('end-attempt','template','synthetic','draw','2026-01-01')"
        )
        db.execute(
            "INSERT INTO tactic_pack_activation(pack_id,active) VALUES ('synthetic-pack',1)"
        )
        db.execute(
            "INSERT INTO tactic_progress(puzzle_id,deck_id,card_id) VALUES ('synthetic-puzzle','synthetic-pack','tactic')"
        )
        db.execute(
            "INSERT INTO daily_chess_snapshots(local_day,snapshot_version,metrics_json,status,updated_at) VALUES ('2025-12-31',1,'{\"historical\":true}','complete','2026-01-01')"
        )
        db.execute(
            "INSERT INTO daily_chess_insights(id,local_day,kind,evidence_json,created_at,updated_at) VALUES ('insight','2026-01-01','synthetic','{}','2026-01-01','2026-01-01')"
        )
        db.execute(
            "INSERT INTO daily_statistics_jobs(local_day,updated_at) VALUES ('2026-01-01','2026-01-01')"
        )
        db.execute(
            "INSERT INTO game_insight_recommendations(motif,miss_count,total_loss_cp,supporting_games_json,recommended_pack_id,updated_at) VALUES ('fork',1,100,'[\"fixture-game\"]','synthetic-pack','2026-01-01')"
        )
        db.execute(
            "INSERT INTO next_opponent_snapshots(version,account,method_version,profile_json,published_at) VALUES ('version','synthetic','1','{}','2026-01-01')"
        )
        db.execute(
            "INSERT INTO next_opponent_accounts(account,profile_version) VALUES ('synthetic','version')"
        )
        db.execute(
            "INSERT INTO game_analysis_jobs(game_id,updated_at) VALUES ('fixture-game','2026-01-01')"
        )
        db.execute(
            "INSERT INTO game_analysis_position_reports(id,game_id,analysis_version,scan_pass,position_index,request_json,state,updated_at) VALUES ('report','fixture-game',1,'shallow',0,'{}','queued','2026-01-01')"
        )
        db.execute(
            "INSERT INTO game_analysis_position_errors(report_id,game_id,error,recorded_at) VALUES ('report','fixture-game','synthetic','2026-01-01')"
        )
        db.execute(
            "INSERT INTO game_analysis_publications(game_id,analysis_version,analysis_evidence_version,prepared_json,result_json,updated_at) VALUES ('fixture-game',1,1,'{}','{}','2026-01-01')"
        )
        db.execute(
            "INSERT INTO reviews(id,card_id,rating,reviewed_at,previous_interval,next_interval) VALUES (700001,'opening','again','2026-01-01',1,0)"
        )
        db.execute(
            "INSERT INTO daily_queue(id,queue_date,card_id,position,status) VALUES (700001,'2026-01-01','opening',1,'issued')"
        )
        db.execute(
            "INSERT INTO tactic_captures(id,card_id,source_kind,captured_at,request_json,result_json) VALUES ('capture','tactic','puzzle_rush','2026-01-01','{\"theme\":\"defense\"}','{}')"
        )
        db.execute(
            "INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,response_json) VALUES ('phone','review.submit','synthetic','pending','{\"card_id\":\"opening\"}')"
        )
        pgn = '[Event "Synthetic"]\n\n1. e4 e5 *'
        db.execute(
            "INSERT INTO product_feedback_sources(source_key,version,payload) VALUES ('source',1,%s)",
            (Jsonb({"raw_pgn": pgn}),),
        )
        db.execute(
            "INSERT INTO product_feedback_captures(source_key,source_revision,source_digest,comparison_revision,payload) VALUES ('source',1,'synthetic','1',%s)",
            (Jsonb({"raw_pgn": pgn}),),
        )
        db.execute(
            "INSERT INTO product_feedback_deliveries(operation_id,source_key,effect_key,command_kind,state,next_attempt_at,payload) VALUES ('delivery','source','effect','again','pending',0,'{\"card_id\":\"opening\"}')"
        )
        db.execute(
            "INSERT INTO study_game_feedback(effect_key,identity_json,source_revision,decision_id,received_at,status,updated_at,card_id,applied_at) VALUES ('effect','{}','1','decision',now(),'applied',now(),'opening',now())"
        )

    with psycopg.connect(dsn) as db:
        original_attempt = db.execute(
            "SELECT row_to_json(a)::text FROM defense_attempts a WHERE attempt_id='attempt'"
        ).fetchone()[0]
    path = tmp_path / "cut62-original.json"
    with psycopg.connect(dsn) as db:
        captured_id = guard.save_cut62_witness(path, db)
    witness, secret = guard.load_cut62_witness(path)
    assert witness["rows"]["defense_attempts"]["count"] == 1
    assert witness["version"] == 5
    assert all(
        witness["rows"][name]["count"] == 1 for name in guard.CUT62_RETIRED_TABLES
    )
    assert witness["sequences"]["retired"]["count"] == 1
    with psycopg.connect(dsn) as db:
        assert (
            guard.check_captured_cut62(db, witness, secret)["witness_id"] == captured_id
        )
        db.execute("UPDATE endgame_attempts SET target='win' WHERE id='end-attempt'")
        alternate_path = tmp_path / "cut62-alternate.json"
        alternate_id = guard.save_cut62_witness(alternate_path, db)
        alternate, alternate_secret = guard.load_cut62_witness(alternate_path)
        assert (
            guard.check_captured_cut62(db, alternate, alternate_secret)["witness_id"]
            == alternate_id
        )
        assert alternate_id != captured_id
        db.rollback()
        db.execute(
            "UPDATE repertoires SET name='tampered-before-cut' WHERE id='synthetic'"
        )
        with pytest.raises(RuntimeError, match="source changed"):
            guard.check_captured_cut62(db, witness, secret)
        db.rollback()
        assert guard.check_captured_cut62(db, witness, secret)["unchanged"]
    apply_migrations(dsn)
    with psycopg.connect(dsn) as db:
        assert guard.verify_cut62(db, witness, secret)["witness_id"] == captured_id
        assert (
            guard.verify_cut62(db, alternate, alternate_secret)["witness_id"]
            == alternate_id
        )
        assert (
            alternate_id != captured_id
        )  # Caller must reject a valid swapped witness.
        assert (
            db.execute(
                "SELECT row_to_json(a)::text FROM defense_attempts a WHERE attempt_id='attempt'"
            ).fetchone()[0]
            == original_attempt
        )
        db.execute(
            "UPDATE defense_attempts SET move_uci='d2d4' WHERE attempt_id='attempt'"
        )
        with pytest.raises(
            RuntimeError, match="retained rows changed in defense_attempts"
        ):
            guard.verify_cut62(db, witness, secret)
        db.rollback()
        guard.verify_cut62(db, witness, secret)
        assert (
            db.execute(
                "SELECT to_regclass('public.game_analysis_position_errors_id_seq')"
            ).fetchone()[0]
            is None
        )
        old = copy.deepcopy(witness)
        old["version"] = 4
        with pytest.raises(RuntimeError, match="unsupported"):
            guard.verify_cut62(db, old, secret)
    apply_migrations(dsn)
    with psycopg.connect(dsn) as db:
        guard.verify_cut62(db, witness, secret)
        db.execute("UPDATE repertoires SET name='tampered' WHERE id='synthetic'")
        with pytest.raises(RuntimeError, match="retained rows changed"):
            guard.verify_cut62(db, witness, secret)
        db.rollback()
        guard.verify_cut62(db, witness, secret)
        sequence_before = db.execute(
            "SELECT last_value,is_called FROM reviews_id_seq"
        ).fetchone()
        try:
            db.execute("SELECT setval('reviews_id_seq',5,true)")
            with pytest.raises(RuntimeError, match="sequences changed"):
                guard.verify_cut62(db, witness, secret)
        finally:
            db.execute("SELECT setval('reviews_id_seq',%s,%s)", sequence_before)
        guard.verify_cut62(db, witness, secret)
        db.execute(
            "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES ('synthetic','lichess','synthetic','2026-01-01T00:00:00Z','rapid',1,'white','draw','synthetic','[]')"
        )
        for name in guard.CUT62_RETIRED_TABLES:
            assert (
                db.execute("SELECT to_regclass(%s)", ("public." + name,)).fetchone()[0]
                is None
            )
        db.rollback()


def test_cut62_sequence_inventory_separates_removed_identity():
    class Result:
        def __init__(self, value):
            self.value = value

        def fetchall(self):
            return self.value

        def fetchone(self):
            return self.value

    class SequenceDB:
        def __init__(self, names):
            self.names = names

        def execute(self, statement):
            if isinstance(statement, str):
                return Result(
                    [
                        (name, "owner", "", 1, 1, 100, 1, 1, False)
                        for name in sorted(self.names)
                    ]
                )
            return Result((1, False))

    source = guard._cut62_sequences(
        SequenceDB(guard.CUT62_PROTECTED_SEQUENCES | guard.CUT62_RETIRED_SEQUENCES),
        b"x" * 32,
        True,
    )
    target = guard._cut62_sequences(
        SequenceDB(guard.CUT62_PROTECTED_SEQUENCES), b"x" * 32, False
    )
    assert source["protected"] == target["protected"]
    assert source["retired"]["count"] == 1
    assert target["retired"]["count"] == 0
    with pytest.raises(RuntimeError, match="sequence coverage"):
        guard._cut62_sequences(
            SequenceDB(guard.CUT62_PROTECTED_SEQUENCES), b"x" * 32, True
        )
    with pytest.raises(RuntimeError, match="sequence coverage"):
        guard._cut62_sequences(
            SequenceDB(guard.CUT62_PROTECTED_SEQUENCES | guard.CUT62_RETIRED_SEQUENCES),
            b"x" * 32,
            False,
        )


def test_schema61_defense_mixed_read_only_census(tmp_path):
    """Synthetic transaction; classify identities and roll back without deletion."""
    import os
    from urllib.parse import urlparse

    import psycopg
    from psycopg.rows import dict_row

    dsn = os.environ.get("TEMPO_W07_DEFENSE_CENSUS_DSN")
    if not dsn:
        pytest.skip("W07 defense-census disposable database not allocated")
    parsed = urlparse(dsn)
    if parsed.hostname != "127.0.0.1" or parsed.path != "/w07_census":
        raise ValueError("refusing non-W07 disposable database")
    with psycopg.connect(dsn, row_factory=dict_row) as db:
        assert (
            db.execute(
                "SELECT max(version) AS v FROM tempo_schema_migrations"
            ).fetchone()["v"]
            == 61
        )
        try:
            db.execute(
                "INSERT INTO repertoires(id,name,source_name,created_at) VALUES ('census-rep','synthetic','synthetic','2026-01-01')"
            )
            for card, kind in [
                ("census-opening", "opening"),
                ("census-tactic", "tactic"),
                ("census-defense", "defense"),
                ("census-issued", "defense"),
                ("census-shared", "defense"),
                ("census-unknown", "future-type"),
            ]:
                db.execute(
                    "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES (%s,'census-rep','prefix','synthetic','[]','2026-01-01',%s)",
                    (card, kind),
                )
            db.execute(
                "INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at) VALUES ('accepted-line','census-rep','accepted','white','synthetic','[\"e2e4\"]','2026-01-01')"
            )
            db.execute(
                "INSERT INTO daily_queue(id,queue_date,card_id,position,status,admission_source) VALUES (900001,'2026-01-01','census-opening',1,'issued',NULL),(900002,'2026-01-01','census-defense',2,'queued','defense:candidate'),(900003,'2026-01-01','census-issued',3,'issued','defense:issued')"
            )
            db.execute(
                "UPDATE queue_attempt_origins SET issued_as_head=1 WHERE queue_entry_id IN (900001,900003)"
            )
            db.execute(
                "INSERT INTO queue_attempt_abandonments(queue_entry_id,card_id,revision,operation_id,request_sha256,origin_sha256,actor,reason,abandoned_at) VALUES (900003,'census-issued',1,'abandoned',repeat('a',64),repeat('b',64),'synthetic','synthetic',now())"
            )
            db.execute(
                "INSERT INTO reviews(id,card_id,rating,reviewed_at,previous_interval,next_interval,source_ref) VALUES (900002,'census-defense','again','2026-01-01',1,0,'defense:attempt')"
            )
            db.execute(
                "INSERT INTO review_schedule_snapshots(review_id,state_json) VALUES (900002,'{}')"
            )
            db.execute(
                "INSERT INTO review_attempt_receipts(attempt_id,card_id,queue_entry_id,outcome,guided,review_id,scheduling_status,result_json) VALUES ('census-receipt','census-defense',900002,'complete',0,900002,'complete','{}')"
            )
            db.execute(
                "INSERT INTO tactic_captures(id,card_id,source_kind,captured_at,request_json,result_json) VALUES ('defense-theme','census-tactic','puzzle_rush','2026-01-01','{\"theme\":\"defense\"}','{}'),('mixed-capture','census-shared','game','2026-01-01','{}','{}')"
            )
            db.execute(
                "INSERT INTO frozen_opening_runs(run_id,plan_checksum,plan_json,created_at,reader_version) VALUES ('census-run','census-hash','{}','2026-01-01','1')"
            )
            db.execute(
                "INSERT INTO frozen_opening_ranks(run_id,card_id,ordinal,fingerprint,memberships_json,definition_json) VALUES ('census-run','census-opening',1,'synthetic','[]','{}')"
            )
            db.execute(
                "UPDATE frozen_opening_activation SET run_id='census-run',epoch=1 WHERE id=1"
            )
            db.execute(
                "INSERT INTO study_position_epochs(repertoire_id,epoch,source_digest,created_at) VALUES ('census-rep','epoch','synthetic',now())"
            )
            db.execute(
                "INSERT INTO study_position_states(repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,source_digest,fsrs_internal_card_id,fsrs_json,due_at,donor_card_id,donor_card_revision,donor_fingerprint,donor_selection_policy) VALUES ('census-rep','standard','position','e2e4','1','epoch','synthetic',4503599627370496,'{}',now(),'census-opening',1,'synthetic','synthetic')"
            )
            db.execute(
                "INSERT INTO study_position_issued_plans(attempt_id,queue_entry_id,card_id,card_revision,repertoire_id,variant,owner_epoch,source_digest,issue_study_day,plan_digest,plan_json,issued_at) VALUES ('position-plan',900001,'census-opening',1,'census-rep','standard','epoch','synthetic','2026-01-01','synthetic','{}',now())"
            )
            db.execute(
                "INSERT INTO study_position_first_responses(event_id,attempt_id,decision_index,repertoire_id,variant,position,owner_epoch,payload_digest,payload_json,observed_at,accepted_at) VALUES ('first-response','position-plan',0,'census-rep','standard','position','epoch','synthetic','{}',now(),now())"
            )
            db.execute(
                "INSERT INTO study_game_feedback(effect_key,identity_json,source_revision,decision_id,received_at,status,updated_at,card_id,applied_at) VALUES ('game-effect','{}','1','decision',now(),'applied',now(),'census-opening',now())"
            )
            db.execute(
                "INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,response_json) VALUES ('mixed-envelope','unknown','synthetic','complete','{\"cards\":[\"census-defense\",\"census-opening\"]}')"
            )
            cards = db.execute(
                "SELECT id,content_type FROM cards WHERE id LIKE 'census-%' ORDER BY id"
            ).fetchall()
            assert {row["id"] for row in cards if row["content_type"] == "defense"} == {
                "census-defense",
                "census-issued",
                "census-shared",
            }
            origins = db.execute(
                "SELECT queue_entry_id,card_id,revision,content_type,issued_as_head FROM queue_attempt_origins WHERE card_id LIKE 'census-%' ORDER BY queue_entry_id"
            ).fetchall()
            assert [
                (
                    row["queue_entry_id"],
                    row["card_id"],
                    row["revision"],
                    row["issued_as_head"],
                )
                for row in origins
            ] == [
                (900001, "census-opening", 1, 1),
                (900002, "census-defense", 1, 0),
                (900003, "census-issued", 1, 1),
            ]
            assert (
                db.execute(
                    "SELECT count(*) AS n FROM tactic_captures WHERE id IN ('defense-theme','mixed-capture')"
                ).fetchone()["n"]
                == 2
            )
            assert (
                db.execute(
                    "SELECT count(*) AS n FROM study_position_first_responses WHERE event_id='first-response'"
                ).fetchone()["n"]
                == 1
            )
            assert (
                db.execute(
                    "SELECT count(*) AS n FROM study_game_feedback WHERE effect_key='game-effect' AND status='applied'"
                ).fetchone()["n"]
                == 1
            )
            assert (
                db.execute(
                    "SELECT count(*) AS n FROM repertoire_lines WHERE id='accepted-line'"
                ).fetchone()["n"]
                == 1
            )
            assert (
                db.execute(
                    "SELECT count(*) AS n FROM review_attempt_receipts WHERE attempt_id='census-receipt' AND queue_entry_id=900002 AND review_id=900002"
                ).fetchone()["n"]
                == 1
            )
            assert (
                db.execute(
                    "SELECT count(*) AS n FROM operation_receipts WHERE operation_id='mixed-envelope'"
                ).fetchone()["n"]
                == 1
            )
            # An interrupted pre-cut witness must match the still-stopped source.
            from psycopg.rows import tuple_row

            db.row_factory = tuple_row
            path = tmp_path / "defense-census-witness.json"
            guard.save_cut62_witness(path, db)
            witness, secret = guard.load_cut62_witness(path)
            assert guard.check_captured_cut62(db, witness, secret)["unchanged"]
            db.execute("SAVEPOINT source_drift")
            db.execute("UPDATE cards SET state='mature' WHERE id='census-opening'")
            with pytest.raises(RuntimeError, match="source changed"):
                guard.check_captured_cut62(db, witness, secret)
            db.execute("ROLLBACK TO SAVEPOINT source_drift")
            assert guard.check_captured_cut62(db, witness, secret)["unchanged"]
            # No deletion set: census-defense also appears in an unknown mixed envelope.
            # The owner must resolve each typed and JSON reference before proposing one.
        finally:
            db.rollback()


def test_cut62_existing_source_witness_check_fails_on_drift(monkeypatch):
    witness = {
        "version": guard.CUT62_EVIDENCE_VERSION,
        "from": 61,
        "to": 62,
        "sentinel": "original",
    }
    monkeypatch.setattr(guard, "capture_cut62", lambda database, secret: dict(witness))
    assert guard.check_captured_cut62(None, witness, b"x" * 32)["unchanged"]
    monkeypatch.setattr(
        guard,
        "capture_cut62",
        lambda database, secret: {**witness, "sentinel": "changed"},
    )
    with pytest.raises(RuntimeError, match="source changed"):
        guard.check_captured_cut62(None, witness, b"x" * 32)


def test_pre62_original_witness_survives_only_expected_checkpoints(monkeypatch):
    class Database:
        version = 59

        def execute(self, query):
            class Result:
                def fetchone(inner):
                    return (self.version if "max(version)" in query else "owner",)

            return Result()

    database = Database()
    source = guard.PRE62_SOURCE_TABLES
    layout = {name: [["id"], [["id", "text", True]]] for name in source}
    state = {name: {"count": 1, "digest": name} for name in source}
    monkeypatch.setattr(guard, "_pre62_session", lambda db: None)
    monkeypatch.setattr(guard, "_cut62_versions", lambda db, version: None)
    monkeypatch.setattr(
        guard, "_pre62_added_objects", lambda db, version, owner, acl: None
    )
    monkeypatch.setattr(
        guard,
        "_cut62_layout",
        lambda db, names: {name: layout.get(name, [["id"], []]) for name in names},
    )
    monkeypatch.setattr(
        guard, "_pre62_table", lambda db, name, key, secret: state[name].copy()
    )
    monkeypatch.setattr(guard, "_pre62_default_table_acl", lambda db: [])
    monkeypatch.setattr(
        guard, "_pre62_sequences", lambda db, secret: {"count": 10, "digest": "seq"}
    )
    monkeypatch.setattr(
        guard, "_pre62_catalog", lambda db, secret: {"count": 1, "digest": "catalog"}
    )
    witness = guard.capture_pre62_59(database, b"x" * 32)
    assert "defense_attempts" in witness["rows"]
    assert "study_position_first_responses" in witness["rows"]
    for version in (59, 60, 61):
        database.version = version
        assert (
            guard.verify_pre62_59(database, witness, b"x" * 32)["observed_schema"]
            == version
        )
    state["defense_attempts"]["digest"] = "modified"
    with pytest.raises(RuntimeError, match="retained rows changed in defense_attempts"):
        guard.verify_pre62_59(database, witness, b"x" * 32)
    state["defense_attempts"]["digest"] = "defense_attempts"
    monkeypatch.setattr(
        guard, "_pre62_sequences", lambda db, secret: {"count": 10, "digest": "changed"}
    )
    with pytest.raises(RuntimeError, match="sequences changed"):
        guard.verify_pre62_59(database, witness, b"x" * 32)
    database.version = 62
    with pytest.raises(RuntimeError, match="unsupported pre62 target"):
        guard.verify_pre62_59(database, witness, b"x" * 32)


def test_pre62_file_never_overwrites_and_rejects_other_domain(tmp_path, monkeypatch):
    witness = {
        "domain": "pre62-source-59",
        "version": 1,
        "from": 59,
        "through": 61,
        "layout": dict.fromkeys(guard.PRE62_SOURCE_TABLES),
        "rows": dict.fromkeys(guard.PRE62_SOURCE_TABLES - {guard.CUT62_LEDGER}),
        "owner": "owner",
        "new_table_acl": [],
        "catalog": {},
        "sequences": {},
    }
    monkeypatch.setattr(
        guard, "capture_pre62_59", lambda database, secret: dict(witness)
    )
    path = tmp_path / "pre62.json"
    guard.save_pre62_59(path, None)
    assert path.stat().st_mode & 0o777 == 0o600
    assert guard.load_pre62_59(path)[0] == witness
    with pytest.raises(FileExistsError):
        guard.save_pre62_59(path, None)
    payload = json.loads(path.read_text())
    payload["witness"]["domain"] = "cut62"
    path.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="authentication"):
        guard.load_pre62_59(path)


def test_pre62_native_original_59_survives_interrupted_60_and_61(tmp_path):
    """The original witness never changes across either disposable migration."""
    import os
    from urllib.parse import urlparse

    import psycopg
    from database.tooling.apply_postgres_migrations import MIGRATIONS, migration_source

    dsn = os.environ.get("TEMPO_W07_PRE62_REGRESSION_DSN")
    if not dsn:
        pytest.skip("W07 pre62 disposable database not allocated")
    parsed = urlparse(dsn)
    if parsed.hostname != "127.0.0.1" or parsed.path != "/w07_pre62":
        raise ValueError("refusing non-W07 disposable database")
    with psycopg.connect(dsn) as db:
        assert (
            db.execute(
                "SELECT to_regclass('public.tempo_schema_migrations')"
            ).fetchone()[0]
            is None
        )
        for path in sorted(MIGRATIONS.glob("[0-9][0-9][0-9]_*.sql")):
            if int(path.name[:3]) <= 59:
                db.execute(migration_source(path), prepare=False)
        db.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES ('r','synthetic','synthetic','2026-01-01')"
        )
        db.execute(
            "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES ('opening','r','prefix','synthetic','[]','2026-01-01','opening'),('defense','r','prefix','synthetic','[]','2026-01-01','defense')"
        )
        db.execute(
            "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES ('game','synthetic','synthetic','2026-01-01','rapid',0,'white','draw','synthetic','[]')"
        )
        db.execute(
            "INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,created_at,updated_at) VALUES ('finding','game',1,1,'synthetic',1,'{}','2026-01-01','2026-01-01')"
        )
        db.execute(
            "INSERT INTO threat_training_candidates(id,finding_id,game_id,analysis_version,incident_id,player_ply,evidence_json,source_fingerprint,detector_version,policy_json,created_at,updated_at,card_id) VALUES ('candidate','finding','game',1,'incident',1,'{}','synthetic',1,'{}','2026-01-01','2026-01-01','defense')"
        )
        db.execute(
            "INSERT INTO defense_attempts(attempt_id,candidate_id,card_id,exercise_revision,move_uci,grade_json,reviewed_at) VALUES ('attempt','candidate','defense',1,'e2e4','{}','2026-01-01')"
        )
        db.execute(
            "INSERT INTO study_position_epochs(repertoire_id,epoch,source_digest,created_at) VALUES ('r','epoch','synthetic',now())"
        )
        db.execute(
            "INSERT INTO study_position_states(repertoire_id,variant,position,expected_uci,policy_version,owner_epoch,source_digest,fsrs_internal_card_id,fsrs_json,due_at) VALUES ('r','standard','position','e2e4','1','epoch','synthetic',4503599627370496,'{}',now())"
        )
        db.execute(
            "INSERT INTO study_position_issued_plans(attempt_id,queue_entry_id,card_id,card_revision,repertoire_id,variant,owner_epoch,source_digest,issue_study_day,plan_digest,plan_json,issued_at) VALUES ('plan',900001,'opening',1,'r','standard','epoch','synthetic','2026-01-01','synthetic','{}',now())"
        )
        db.execute(
            "INSERT INTO study_position_first_responses(event_id,attempt_id,decision_index,repertoire_id,variant,position,owner_epoch,payload_digest,payload_json,observed_at,accepted_at) VALUES ('first','plan',0,'r','standard','position','epoch','synthetic','{}',now(),now())"
        )
        db.execute(
            "INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,created_at,updated_at) VALUES ('kept','synthetic','synthetic','complete','2026-01-01','2026-01-01')"
        )
    witness_path = tmp_path / "pre62-original.json"
    with psycopg.connect(dsn) as db:
        captured_id = guard.save_pre62_59(witness_path, db)
    raw = witness_path.read_bytes()
    witness, secret = guard.load_pre62_59(witness_path)
    assert guard.authenticate_pre62_59(witness, secret) == captured_id
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, guard.__file__, "--pre62-authenticate59", str(witness_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout)["witness_id"] == captured_id

    def verify():
        with psycopg.connect(dsn) as db:
            result = guard.verify_pre62_59(db, witness, secret)
            assert result["witness_id"] == captured_id
            return result["observed_schema"]

    assert verify() == 59
    with psycopg.connect(dsn) as db:
        guard.verify_pre62_59(db, witness, secret)
        assert (
            db.execute("SELECT current_setting('transaction_read_only')").fetchone()[0]
            == "on"
        )
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            db.execute("CREATE TABLE public.pre62_read_only_probe(id integer)")
        db.rollback()
    for autocommit in (False, True):
        with psycopg.connect(dsn, autocommit=autocommit) as db:
            db.execute("SET default_transaction_isolation TO 'repeatable read'")
            if not autocommit:
                db.commit()
            with pytest.raises(RuntimeError, match="requires an active transaction"):
                guard.verify_pre62_59(db, witness, secret, in_write_transaction=True)
            assert db.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    with psycopg.connect(dsn) as db:
        db.execute("SELECT 1")
        with pytest.raises(RuntimeError, match="requires repeatable read write"):
            guard.verify_pre62_59(db, witness, secret, in_write_transaction=True)
        db.rollback()
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ WRITE")
        assert (
            guard.verify_pre62_59(db, witness, secret, in_write_transaction=True)[
                "observed_schema"
            ]
            == 59
        )
        db.execute("SAVEPOINT pre62_ddl_probe")
        db.execute("CREATE TABLE public.pre62_write_probe(id integer)")
        db.execute("ROLLBACK TO SAVEPOINT pre62_ddl_probe")
        assert (
            guard.verify_pre62_59(db, witness, secret, in_write_transaction=True)[
                "observed_schema"
            ]
            == 59
        )
        db.rollback()
    with psycopg.connect(dsn) as db:
        db.execute(
            migration_source(MIGRATIONS / "060_position_first_response_immutable.sql"),
            prepare=False,
        )
    assert verify() == 60
    assert witness_path.read_bytes() == raw
    with psycopg.connect(dsn) as db:
        with pytest.raises(psycopg.errors.CheckViolation):
            db.execute(
                "DELETE FROM study_position_first_responses WHERE event_id='first'"
            )
        db.rollback()
    assert verify() == 60
    with psycopg.connect(dsn) as db:
        db.execute(
            "UPDATE defense_attempts SET move_uci='d2d4' WHERE attempt_id='attempt'"
        )
    with pytest.raises(RuntimeError, match="retained rows changed in defense_attempts"):
        verify()
    with psycopg.connect(dsn) as db:
        db.execute(
            "UPDATE defense_attempts SET move_uci='e2e4' WHERE attempt_id='attempt'"
        )
        db.execute("DELETE FROM operation_receipts WHERE operation_id='kept'")
    with pytest.raises(
        RuntimeError, match="retained rows changed in operation_receipts"
    ):
        verify()
    with psycopg.connect(dsn) as db:
        db.execute(
            "INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,created_at,updated_at) VALUES ('kept','synthetic','synthetic','complete','2026-01-01','2026-01-01')"
        )
        db.execute("ALTER TABLE cards DISABLE TRIGGER frozen_guard_cards")
    with pytest.raises(RuntimeError, match="retained catalog changed"):
        verify()
    with psycopg.connect(dsn) as db:
        db.execute("ALTER TABLE cards ENABLE TRIGGER frozen_guard_cards")
        db.execute("CREATE INDEX pre62_unexpected ON cards(id)")
    with pytest.raises(RuntimeError, match="retained catalog changed"):
        verify()
    with psycopg.connect(dsn) as db:
        db.execute("DROP INDEX pre62_unexpected")
        old = db.execute("SELECT last_value,is_called FROM reviews_id_seq").fetchone()
        db.execute("SELECT setval('reviews_id_seq',5,true)")
    try:
        with pytest.raises(RuntimeError, match="retained sequences changed"):
            verify()
    finally:
        with psycopg.connect(dsn) as db:
            db.execute("SELECT setval('reviews_id_seq',%s,%s)", old)
    assert verify() == 60
    with psycopg.connect(dsn) as db:
        db.execute(
            migration_source(MIGRATIONS / "061_content_origin_receipts.sql"),
            prepare=False,
        )
    assert verify() == 61
    with psycopg.connect(dsn) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ WRITE")
        assert (
            guard.verify_pre62_59(db, witness, secret, in_write_transaction=True)[
                "observed_schema"
            ]
            == 61
        )
        db.execute("SET LOCAL join_collapse_limit=1")
        db.execute("SET LOCAL jit=off")
        count, digest = guard._cut62_defense_reference_digest(db, [])
        selection = {
            "version": 1,
            "target": "synthetic",
            "cohort": "defense-no-activity",
            "keys": [],
            "expected": {"count": count, "reference_digest": digest},
        }
        db.execute(
            "SELECT set_config('tempo.cut62_defense_manifest',%s,true)",
            (json.dumps(selection),),
        )
        db.execute(
            migration_source(MIGRATIONS / "062_three_journey_retirement.sql"),
            prepare=False,
        )
        assert (
            db.execute(
                "SELECT max(version) FROM public.tempo_schema_migrations"
            ).fetchone()[0]
            == 62
        )
        db.rollback()
    with psycopg.connect(dsn) as db:
        assert (
            db.execute(
                "SELECT max(version) FROM public.tempo_schema_migrations"
            ).fetchone()[0]
            == 61
        )
        db.rollback()
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ WRITE")
        corrupted = copy.deepcopy(witness)
        corrupted["rows"]["defense_attempts"]["digest"] = "invalid"
        with pytest.raises(RuntimeError, match="retained rows changed"):
            guard.verify_pre62_59(db, corrupted, secret, in_write_transaction=True)
        db.rollback()
        assert (
            db.execute(
                "SELECT max(version) FROM public.tempo_schema_migrations"
            ).fetchone()[0]
            == 61
        )
    assert witness_path.read_bytes() == raw
    with psycopg.connect(dsn) as db:
        db.execute(
            "INSERT INTO legacy_content_historical_origins(legacy_target_id,legacy_workspace_id,export_id,receipt_id,payload,receipt_digest) VALUES ('t','w','e','r','synthetic','digest')"
        )
    with pytest.raises(RuntimeError, match="not empty"):
        verify()


def test_cut62_witness_id_binds_retired_evidence_and_secret():
    original = {
        "version": guard.CUT62_EVIDENCE_VERSION,
        "rows": {"retired": {"digest": "one"}},
    }
    changed = {
        "version": guard.CUT62_EVIDENCE_VERSION,
        "rows": {"retired": {"digest": "two"}},
    }
    identifier = guard.cut62_witness_id(original, b"x" * 32)
    assert identifier == guard.cut62_witness_id(dict(original), b"x" * 32)
    assert identifier != guard.cut62_witness_id(changed, b"x" * 32)
    assert identifier != guard.cut62_witness_id(original, b"y" * 32)


def test_defense_selection_is_private_sorted_and_bound_to_exact_bytes(tmp_path):
    import hashlib

    path = tmp_path / "selection.json"
    selection = {
        "version": 1,
        "target": "synthetic-target",
        "cohort": "defense-no-activity",
        "keys": [["c", "t"]],
        "expected": {"count": 1, "reference_digest": "a" * 64},
    }
    payload = json.dumps(selection).encode()
    path.write_bytes(payload)
    path.chmod(0o600)
    loaded, digest = guard.load_cut62_defense_selection(path)
    assert loaded == selection
    assert digest == hashlib.sha256(payload).hexdigest()
    selection["keys"].append(["c", "t"])
    selection["expected"]["count"] = 2
    path.write_text(json.dumps(selection))
    with pytest.raises(RuntimeError, match="unique and sorted"):
        guard.load_cut62_defense_selection(path)
    path.chmod(0o644)
    with pytest.raises(RuntimeError, match="private regular file"):
        guard.load_cut62_defense_selection(path)


def test_schema61_defense_recognition_review_history_rehearsal(monkeypatch):
    """Only synthetic exact identities are removed, then the entire transaction rolls back."""
    import copy
    import os
    from urllib.parse import urlparse

    import psycopg
    from psycopg import sql

    dsn = os.environ.get("TEMPO_W07_DEFENSE_CENSUS_DSN")
    if not dsn:
        pytest.skip("W07 defense-census disposable database not allocated")
    parsed = urlparse(dsn)
    if parsed.hostname != "127.0.0.1" or parsed.path != "/w07_census":
        raise ValueError("refusing non-W07 disposable database")
    for autocommit in (False, True):
        with psycopg.connect(dsn, autocommit=autocommit) as idle:
            idle.execute("SET default_transaction_isolation TO 'repeatable read'")
            if not autocommit:
                idle.commit()
            with pytest.raises(RuntimeError, match="requires an active transaction"):
                guard._cut62_draft_apply_history(idle, {}, b"h" * 32, {})
            assert idle.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
            with pytest.raises(
                RuntimeError, match="capture requires an active transaction"
            ):
                guard._cut62_capture_history_v2(idle, {}, {}, b"h" * 32, "a" * 64, {})
            assert idle.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
    with psycopg.connect(dsn) as db:
        db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        assert (
            db.execute("SELECT max(version) FROM tempo_schema_migrations").fetchone()[0]
            == 61
        )
        try:
            db.execute(
                "INSERT INTO repertoires(id,name,source_name,created_at) VALUES ('history-rep','synthetic','synthetic','2026-01-01')"
            )
            db.execute(
                "INSERT INTO repertoires(id,name,source_name,created_at) VALUES ('history-missing','synthetic','synthetic','2026-01-01')"
            )
            for card, kind in [
                ("ordinary", "opening"),
                ("themed", "tactic"),
                ("recognition", "defense"),
                ("move", "defense"),
                ("control", "defense"),
                ("mixed", "defense"),
                ("quiet", "defense"),
            ]:
                db.execute(
                    "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date,content_type) VALUES (%s,%s,'prefix','fen','[]','2026-01-01',%s)",
                    (
                        card,
                        "history-missing"
                        if card in ("quiet", "move")
                        else "history-rep",
                        kind,
                    ),
                )
            db.execute(
                "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json) VALUES ('history-game','synthetic','synthetic','2026-01-01','rapid',0,'white','draw','fen','[]')"
            )
            db.execute(
                "INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,evidence_json,created_at,updated_at) VALUES ('history-finding','history-game',1,1,'synthetic',1,'{}','2026-01-01','2026-01-01')"
            )
            for number, card in enumerate(
                ("recognition", "move", "control", "mixed", "quiet"), 1
            ):
                db.execute(
                    "INSERT INTO threat_training_candidates(id,finding_id,game_id,analysis_version,incident_id,player_ply,evidence_json,source_fingerprint,detector_version,policy_json,created_at,updated_at,card_id) VALUES (%s,'history-finding','history-game',1,%s,%s,'{}','synthetic',1,'{}','2026-01-01','2026-01-01',%s)",
                    ("candidate-" + card, "incident-" + card, number, card),
                )
            db.execute(
                "INSERT INTO threat_analysis_requests(id,request_json,created_at,updated_at) VALUES ('history-request','{}','2026-01-01','2026-01-01')"
            )
            for card in ("recognition", "move", "control", "mixed", "quiet"):
                db.execute(
                    "INSERT INTO threat_candidate_requests(candidate_id,request_id,role) VALUES (%s,'history-request','best')",
                    ("candidate-" + card,),
                )
            for number, card, status in [
                (910001, "ordinary", "issued"),
                (910002, "recognition", "queued"),
                (910003, "move", "complete"),
                (910004, "control", "complete"),
                (910005, "mixed", "queued"),
                (910006, "quiet", "queued"),
            ]:
                db.execute(
                    "INSERT INTO daily_queue(id,queue_date,card_id,position,status) VALUES (%s,'2026-01-01',%s,%s,%s)",
                    (number, card, number - 910000, status),
                )
            db.execute(
                "UPDATE queue_attempt_origins SET issued_as_head=1,issuance_sequence=1,issued_at='2026-01-02T00:00:00Z' WHERE queue_entry_id=910001"
            )
            for attempt, card, qid in [
                ("recognition-id", "recognition", 910002),
                ("recognition-before-move", "move", 910003),
                ("control-shared-id", "control", 910004),
            ]:
                db.execute(
                    "INSERT INTO defense_recognition_submissions(attempt_id,candidate_id,queue_entry_id,exercise_revision,answer_json,recognition_correct,created_at) VALUES (%s,%s,%s,7,'{}',1,'2026-01-01')",
                    (attempt, "candidate-" + card, qid),
                )
            for attempt, card, qid, move in [
                ("move-id", "move", 910003, "e2e4"),
                ("control-shared-id", "control", 910004, ""),
            ]:
                db.execute(
                    "INSERT INTO defense_attempts(attempt_id,candidate_id,card_id,queue_entry_id,exercise_revision,move_uci,grade_json,reviewed_at) VALUES (%s,%s,%s,%s,7,%s,'{\"status\":\"correct\"}','2026-01-01')",
                    (attempt, "candidate-" + card, card, qid, move),
                )
            for rid, card, source in [
                (910001, "ordinary", "attempt:ordinary"),
                (910003, "move", "defense:move-id"),
                (910004, "control", "defense:control-shared-id"),
            ]:
                db.execute(
                    "INSERT INTO reviews(id,card_id,rating,reviewed_at,previous_interval,next_interval,source_kind,source_ref) VALUES (%s,%s,'good','2026-01-02',1,2,'study',%s)",
                    (rid, card, source),
                )
                if card == "ordinary":
                    db.execute(
                        "INSERT INTO review_schedule_snapshots(review_id,state_json) VALUES (%s,'{}')",
                        (rid,),
                    )
                if card == "ordinary":
                    db.execute(
                        "INSERT INTO review_attempt_receipts(attempt_id,card_id,queue_entry_id,outcome,guided,review_id,scheduling_status,result_json) VALUES ('ordinary-attempt',%s,%s,'correct',0,%s,'canonical','{}')",
                        (card, rid, rid),
                    )
            # The Defense writer stores the scheduling result, whose shape is
            # shared with ordinary reviews. Attribution requires the linked
            # attempt/review and absence of independent receipts and commands.
            for card, qid in [("move", 910003), ("control", 910004)]:
                result = {
                    "card_id": card,
                    "next_due": "2026-01-03",
                    "interval_days": 1,
                    "state": "learning",
                    "requeue_today": False,
                    "requeue_after_cards": None,
                    "stability": 1.0,
                    "scheduling_mode": "standard",
                    "hard_correct_streak": 1,
                    "suggest_shorter_prefix": False,
                    "idempotent": False,
                }
                db.execute(
                    "UPDATE daily_queue SET review_result_json=%s WHERE id=%s",
                    (json.dumps(result), qid),
                )
            db.execute(
                "INSERT INTO tactic_captures(id,card_id,source_kind,captured_at,request_json,result_json) VALUES ('defense-theme','themed','puzzle_rush','2026-01-01','{\"theme\":\"defense\"}','{}'),('mixed-history','mixed','game','2026-01-01','{}','{}')"
            )
            db.execute(
                "INSERT INTO study_game_feedback(effect_key,identity_json,source_revision,decision_id,received_at,status,updated_at,card_id,applied_at,payload_json,payload_hash) VALUES ('history-effect','{}','1','decision',now(),'applied',now(),'ordinary',now(),'{\"qualification\":{\"determined_at\":\"2026-01-01T00:00:00Z\"}}','synthetic')"
            )
            db.execute(
                "INSERT INTO frozen_opening_runs(run_id,plan_checksum,plan_json,created_at,reader_version) VALUES ('history-run','history-hash','{}','2026-01-01','1')"
            )
            db.execute(
                "INSERT INTO frozen_opening_ranks(run_id,card_id,ordinal,fingerprint,memberships_json,definition_json) VALUES ('history-run','ordinary',1,'synthetic','[]','{}')"
            )
            db.execute(
                "INSERT INTO frozen_opening_pending(run_id) VALUES ('history-run')"
            )
            db.execute(
                "DELETE FROM priority_repertoire_source_epochs WHERE repertoire_id='history-missing'"
            )
            # Each positive is tied to its own original typed origin; a mixed
            # candidate with an independently retained tactic capture is held.
            assert (
                db.execute(
                    "SELECT count(*) FROM defense_recognition_submissions r JOIN threat_training_candidates t ON t.id=r.candidate_id JOIN daily_queue q ON q.id=r.queue_entry_id AND q.card_id=t.card_id JOIN queue_attempt_origins o ON o.queue_entry_id=q.id AND o.card_id=q.card_id AND o.content_type='defense' WHERE r.attempt_id IN ('recognition-id','recognition-before-move','control-shared-id')"
                ).fetchone()[0]
                == 3
            )
            assert (
                db.execute(
                    "SELECT count(*) FROM defense_attempts a JOIN reviews r ON r.card_id=a.card_id AND r.source_kind='study' AND r.source_ref='defense:'||a.attempt_id WHERE a.attempt_id IN ('move-id','control-shared-id')"
                ).fetchone()[0]
                == 2
            )
            assert (
                db.execute(
                    "SELECT count(*) FROM tactic_captures WHERE card_id='mixed'"
                ).fetchone()[0]
                == 1
            )
            assert (
                db.execute(
                    "SELECT count(*) FROM defense_attempts a JOIN defense_recognition_submissions r USING(attempt_id) WHERE a.attempt_id='control-shared-id' AND a.move_uci='' AND a.exercise_revision=r.exercise_revision"
                ).fetchone()[0]
                == 1
            )
            assert (
                db.execute(
                    "SELECT count(*) FROM defense_recognition_submissions r JOIN threat_training_candidates t ON t.id=r.candidate_id WHERE r.exercise_revision<>t.exercise_revision"
                ).fetchone()[0]
                == 3
            )
            # Fixture-only candidate classifier: every historical reference must
            # bind to this card's candidate, queue and original Defense origin;
            # independent reviews or receipts hold the whole card.
            # Reuse the reviewed C11 independent reference inventory. The
            # historical owner replaces only its own attempts/reviews/receipts.

            migration = (
                guard.CUT62_DRAFT_DEFENSE_SELECTOR + "\n-- END CUT62 DEFENSE SELECTOR"
            )
            inventory = migration.split(
                "AND NOT EXISTS (SELECT 1 FROM public.cards r WHERE r.unlock_after_card_id=c.id)",
                1,
            )[1].split("-- END CUT62 DEFENSE SELECTOR", 1)[0]
            inventory = (
                "AND NOT EXISTS (SELECT 1 FROM public.cards r WHERE r.unlock_after_card_id=c.id)"
                + inventory
            )
            owned = (
                "FROM public.reviews r WHERE",
                "FROM public.review_attempt_receipts r WHERE",
                "JOIN public.defense_attempts r ON",
                "JOIN public.defense_recognition_submissions r ON",
                "JOIN public.review_attempt_receipts r ON",
            )
            inventory = "\n".join(
                line
                for line in inventory.splitlines()
                if not any(item in line for item in owned)
            )
            receipt_guard = migration.split(
                "AND NOT EXISTS (SELECT 1 FROM typed_receipts r WHERE", 1
            )[1].split(
                "AND NOT EXISTS (SELECT 1 FROM public.queue_attempt_origins o WHERE o.card_id=c.id AND o.issuance_sequence",
                1,
            )[0]
            receipt_guard = (
                "AND NOT EXISTS (SELECT 1 FROM typed_receipts r WHERE" + receipt_guard
            )

            def fixture_eligible(card):
                return db.execute(
                    """WITH typed_receipts AS MATERIALIZED (
                    SELECT CASE WHEN pg_input_is_valid(payload_json,'jsonb') THEN payload_json::jsonb END payload,
                           CASE WHEN pg_input_is_valid(response_json,'jsonb') THEN response_json::jsonb END response
                    FROM public.operation_receipts WHERE command_name IN
                    ('cards.review','cards.review.reconcile','queue.attempt_failed','queue.bury','queue.issue_head','opening_evidence.checkpoint')
                ) SELECT EXISTS (
                    SELECT 1 FROM cards c JOIN threat_training_candidates t ON t.card_id=c.id
                    WHERE c.id=%s AND c.content_type='defense'
                      AND (SELECT count(*) FROM threat_training_candidates t2 WHERE t2.card_id=c.id)=1
                      AND EXISTS (SELECT 1 FROM defense_recognition_submissions r WHERE r.candidate_id=t.id)
                      AND NOT EXISTS (SELECT 1 FROM defense_attempts a WHERE a.candidate_id=t.id AND a.card_id<>c.id)
                      AND NOT EXISTS (SELECT 1 FROM defense_attempts a
                        LEFT JOIN daily_queue q ON q.id=a.queue_entry_id AND q.card_id=c.id
                        LEFT JOIN queue_attempt_origins o ON o.queue_entry_id=q.id AND o.card_id=c.id AND o.content_type='defense'
                        WHERE a.card_id=c.id AND (a.candidate_id<>t.id OR o.queue_entry_id IS NULL OR (SELECT count(*) FROM queue_attempt_origins oo WHERE oo.queue_entry_id=q.id AND oo.card_id=c.id AND oo.content_type='defense')<>1
                          OR q.status<>'complete' OR o.last_status<>'complete' OR q.review_result_json IS NULL
                          OR o.review_result_json IS DISTINCT FROM q.review_result_json
                          OR NOT pg_input_is_valid(q.review_result_json,'jsonb')
                          OR (CASE WHEN pg_input_is_valid(q.review_result_json,'jsonb') THEN q.review_result_json::jsonb->>'card_id' END) IS DISTINCT FROM c.id
                          OR (SELECT count(*) FROM reviews r WHERE r.card_id=c.id AND r.source_kind='study' AND r.source_ref='defense:'||a.attempt_id)<>1))
                      AND NOT EXISTS (SELECT 1 FROM defense_recognition_submissions r
                        LEFT JOIN daily_queue q ON q.id=r.queue_entry_id AND q.card_id=c.id
                        LEFT JOIN queue_attempt_origins o ON o.queue_entry_id=q.id AND o.card_id=c.id AND o.content_type='defense'
                        WHERE r.candidate_id=t.id AND (o.queue_entry_id IS NULL OR (SELECT count(*) FROM queue_attempt_origins oo WHERE oo.queue_entry_id=q.id AND oo.card_id=c.id AND oo.content_type='defense')<>1))
                      AND NOT EXISTS (SELECT 1 FROM reviews r WHERE r.card_id=c.id AND NOT EXISTS
                        (SELECT 1 FROM defense_attempts a WHERE a.card_id=c.id AND r.source_kind='study' AND r.source_ref='defense:'||a.attempt_id))
                      AND NOT EXISTS (SELECT 1 FROM review_attempt_receipts rr WHERE rr.card_id=c.id OR rr.queue_entry_id IN (SELECT q.id FROM daily_queue q WHERE q.card_id=c.id))
                      AND NOT EXISTS (SELECT 1 FROM review_schedule_snapshots ss JOIN reviews r ON r.id=ss.review_id WHERE r.card_id=c.id)
                      AND NOT EXISTS (SELECT 1 FROM daily_queue q WHERE q.card_id=c.id AND q.review_result_json IS NOT NULL AND
                        (q.status<>'complete' OR NOT pg_input_is_valid(q.review_result_json,'jsonb') OR
                         (CASE WHEN pg_input_is_valid(q.review_result_json,'jsonb') THEN q.review_result_json::jsonb->>'card_id' END) IS DISTINCT FROM c.id OR
                         NOT EXISTS (SELECT 1 FROM defense_attempts a JOIN reviews r ON r.card_id=a.card_id AND r.source_kind='study' AND r.source_ref='defense:'||a.attempt_id WHERE a.card_id=c.id AND a.candidate_id=t.id AND a.queue_entry_id=q.id)))
                      AND NOT EXISTS (SELECT 1 FROM queue_attempt_origins o JOIN daily_queue q ON q.id=o.queue_entry_id AND q.card_id=o.card_id WHERE o.card_id=c.id AND (o.review_result_json IS DISTINCT FROM q.review_result_json OR (o.review_result_json IS NOT NULL AND o.last_status<>'complete')))
                      AND NOT EXISTS (SELECT 1 FROM daily_queue q WHERE q.card_id=c.id AND q.admission_source IS NOT NULL AND q.admission_source<>'defense:'||t.id)
                      AND NOT EXISTS (SELECT 1 FROM daily_queue q WHERE q.card_id=c.id AND q.status='complete' AND NOT EXISTS
                        (SELECT 1 FROM defense_attempts a WHERE a.card_id=c.id AND a.candidate_id=t.id AND a.queue_entry_id=q.id))
                      AND NOT EXISTS (SELECT 1 FROM tactic_captures tc WHERE tc.card_id=c.id)
                      AND NOT EXISTS (SELECT 1 FROM queue_attempt_origins o WHERE o.card_id=c.id AND o.issuance_sequence IS NOT NULL)
                """
                    + receipt_guard
                    + inventory
                    + ")",
                    (card,),
                ).fetchone()[0]

            def eligible(card):
                expected = fixture_eligible(card)
                actual = any(
                    row[0] == card
                    for row in db.execute(
                        guard._cut62_draft_history_selector()
                    ).fetchall()
                )
                assert actual == expected
                return actual

            assert all(eligible(card) for card in ("recognition", "move", "control"))
            assert not eligible("mixed")
            db.execute("SAVEPOINT wrong_queue")
            db.execute(
                "UPDATE defense_attempts SET queue_entry_id=910001 WHERE attempt_id='move-id'"
            )
            assert (
                db.execute(
                    "SELECT count(*) FROM defense_attempts a JOIN daily_queue q ON q.id=a.queue_entry_id AND q.card_id=a.card_id WHERE a.attempt_id='move-id'"
                ).fetchone()[0]
                == 0
            )
            assert not eligible("move")
            db.execute("ROLLBACK TO SAVEPOINT wrong_queue")
            db.execute("SAVEPOINT wrong_review_identity")
            db.execute("UPDATE reviews SET source_ref='attempt:other' WHERE id=910003")
            assert (
                db.execute(
                    "SELECT count(*) FROM defense_attempts a JOIN reviews r ON r.card_id=a.card_id AND r.source_kind='study' AND r.source_ref='defense:'||a.attempt_id WHERE a.attempt_id='move-id'"
                ).fetchone()[0]
                == 0
            )
            assert not eligible("move")
            db.execute("ROLLBACK TO SAVEPOINT wrong_review_identity")
            db.execute("SAVEPOINT independent_review")
            db.execute(
                "INSERT INTO reviews(id,card_id,rating,reviewed_at,previous_interval,next_interval,source_kind,source_ref) VALUES (910099,'move','good','2026-01-02',1,2,'study','independent')"
            )
            assert not eligible("move")
            db.execute("ROLLBACK TO SAVEPOINT independent_review")
            db.execute("SAVEPOINT retained_review_snapshot")
            db.execute(
                "INSERT INTO review_schedule_snapshots(review_id,state_json) VALUES (910003,'{}')"
            )
            assert not eligible("move")
            db.execute("ROLLBACK TO SAVEPOINT retained_review_snapshot")
            db.execute("SAVEPOINT independent_receipt")
            db.execute(
                "INSERT INTO review_attempt_receipts(attempt_id,card_id,queue_entry_id,outcome,guided,scheduling_status,result_json) VALUES ('independent','move',910003,'complete',0,'complete','{}')"
            )
            assert not eligible("move")
            db.execute("ROLLBACK TO SAVEPOINT independent_receipt")
            db.execute("SAVEPOINT same_id_ordinary_receipt")
            db.execute(
                "INSERT INTO review_attempt_receipts(attempt_id,card_id,queue_entry_id,outcome,guided,review_id,scheduling_status,result_json) VALUES ('move-id','move',910003,'correct',0,910003,'canonical','{}')"
            )
            assert not eligible("move")
            db.execute("ROLLBACK TO SAVEPOINT same_id_ordinary_receipt")
            for payload in (
                {"card_id": "move", "entry_id": 910001},
                {"card_id": "ordinary", "entry_id": 910003},
            ):
                db.execute("SAVEPOINT retained_wrong_queue")
                db.execute(
                    "INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json) VALUES ('history-operation','queue.issue_head','x','pending',%s)",
                    (json.dumps(payload),),
                )
                assert not eligible("move")
                db.execute("ROLLBACK TO SAVEPOINT retained_wrong_queue")
            db.execute("SAVEPOINT missing_completion_result")
            db.execute("UPDATE daily_queue SET review_result_json=NULL WHERE id=910003")
            db.execute(
                "UPDATE queue_attempt_origins SET review_result_json=NULL WHERE queue_entry_id=910003"
            )
            assert not eligible("move")
            assert eligible("recognition")
            db.execute("ROLLBACK TO SAVEPOINT missing_completion_result")
            db.execute("SAVEPOINT incomplete_queue")
            db.execute("UPDATE daily_queue SET status='queued' WHERE id=910003")
            assert not eligible("move")
            db.execute("ROLLBACK TO SAVEPOINT incomplete_queue")
            db.execute("SAVEPOINT divergent_origin")
            db.execute(
                "UPDATE queue_attempt_origins SET review_result_json='{}' WHERE queue_entry_id=910003"
            )
            assert not eligible("move")
            db.execute("ROLLBACK TO SAVEPOINT divergent_origin")
            db.execute("SAVEPOINT wrong_result_owner")
            db.execute(
                "UPDATE daily_queue SET review_result_json=%s WHERE id=910003",
                (json.dumps({"card_id": "ordinary"}),),
            )
            assert not eligible("move")
            db.execute("ROLLBACK TO SAVEPOINT wrong_result_owner")
            for checkpoint in (
                {"origin_queue_entry_id": 910003},
                {"queue_entry_id": 910003},
                {"manifest": {"card_id": "move"}},
            ):
                db.execute("SAVEPOINT retained_checkpoint")
                db.execute(
                    "INSERT INTO operation_receipts(operation_id,command_name,request_hash,state,payload_json) VALUES ('history-checkpoint','opening_evidence.checkpoint','x','pending',%s)",
                    (json.dumps({"checkpoint": checkpoint}),),
                )
                assert not eligible("move")
                db.execute("ROLLBACK TO SAVEPOINT retained_checkpoint")
            db.execute("SAVEPOINT foreign_admission")
            db.execute(
                "UPDATE daily_queue SET admission_source='discovery:other' WHERE id=910002"
            )
            assert not eligible("recognition")
            db.execute("ROLLBACK TO SAVEPOINT foreign_admission")
            db.execute("SAVEPOINT unaccounted_completion")
            db.execute("UPDATE daily_queue SET status='complete' WHERE id=910002")
            assert not eligible("recognition")
            db.execute("ROLLBACK TO SAVEPOINT unaccounted_completion")
            db.execute("SAVEPOINT duplicate_candidate")
            db.execute(
                "UPDATE threat_training_candidates SET card_id='move' WHERE id='candidate-mixed'"
            )
            assert not eligible("move")
            db.execute("ROLLBACK TO SAVEPOINT duplicate_candidate")
            db.execute("SAVEPOINT foreign_attempt")
            db.execute(
                "UPDATE defense_attempts SET card_id='ordinary' WHERE attempt_id='move-id'"
            )
            assert not eligible("move")
            db.execute("ROLLBACK TO SAVEPOINT foreign_attempt")
            db.execute("SAVEPOINT extra_origin_revision")
            db.execute(
                "INSERT INTO queue_attempt_origins SELECT queue_entry_id,card_id,revision+1,queue_date,cycle,admission_kind,admission_repertoire_id,attempt_failed,last_status,review_result_json,legacy,start_fen,moves_json,trained_color,content_type,issued_as_head,issuance_sequence,issued_at FROM queue_attempt_origins WHERE queue_entry_id=910003"
            )
            assert not eligible("move")
            db.execute("ROLLBACK TO SAVEPOINT extra_origin_revision")

            def all_rows():
                return {
                    name: [
                        json.loads(row[0])
                        for row in db.execute(
                            sql.SQL(
                                "SELECT row_to_json(t)::text FROM public.{} t ORDER BY row_to_json(t)::text"
                            ).format(sql.Identifier(name))
                        )
                    ]
                    for name in sorted(guard.CUT62_PROTECTED_TABLES)
                }

            before = all_rows()

            def keyed_digests(tables):
                return {
                    name: guard._cut62_mac(
                        b"h" * 32,
                        "history-fixture:" + name,
                        (
                            json.dumps(row, sort_keys=True, separators=(",", ":"))
                            for row in rows
                        ),
                    )
                    for name, rows in tables.items()
                }

            before_digests = keyed_digests(before)
            expected = copy.deepcopy(before)
            chosen = {
                card
                for card in ("recognition", "move", "control", "mixed")
                if eligible(card)
            }
            assert any(
                row[0] == "quiet"
                for row in db.execute(guard.CUT62_DRAFT_DEFENSE_SELECTOR).fetchall()
            )
            chosen.add("quiet")
            assert chosen == {"recognition", "move", "control", "quiet"}
            proposed = db.execute(guard._cut62_draft_history_selector()).fetchall()
            assert {(row[0], row[3]) for row in proposed} == {
                ("recognition", "recognition_only"),
                ("move", "reviewed"),
                ("control", "reviewed"),
            }
            history_keys = [
                ["control", "candidate-control", "reviewed"],
                ["move", "candidate-move", "reviewed"],
                ["quiet", "candidate-quiet", "no_activity"],
                ["recognition", "candidate-recognition", "recognition_only"],
            ]
            count, groups, reference_digest = guard._cut62_draft_history_reference(
                db, history_keys
            )
            assert count == 4 and groups == {
                "no_activity": 1,
                "recognition_only": 1,
                "reviewed": 2,
            }
            assert len(reference_digest) == 64
            selection = {
                "version": 2,
                "target": "synthetic",
                "cohort": "defense-history",
                "keys": history_keys,
                "expected": {
                    "count": count,
                    "groups": groups,
                    "reference_digest": reference_digest,
                },
            }
            assert guard._cut62_check_draft_history_selection(db, selection) == (
                count,
                groups,
                reference_digest,
            )
            wrong_digest = copy.deepcopy(selection)
            wrong_digest["expected"]["reference_digest"] = "0" * 64
            with pytest.raises(RuntimeError, match="reference evidence changed"):
                guard._cut62_check_draft_history_selection(db, wrong_digest)
            wrong_group = copy.deepcopy(selection)
            wrong_group["keys"][3][2] = "reviewed"
            wrong_group["expected"]["groups"] = {
                "no_activity": 1,
                "recognition_only": 0,
                "reviewed": 3,
            }
            with pytest.raises(RuntimeError, match="reference evidence changed"):
                guard._cut62_check_draft_history_selection(db, wrong_group)
            wrong_key = copy.deepcopy(selection)
            wrong_key["keys"][3] = [
                "unrelated",
                "candidate-unrelated",
                "recognition_only",
            ]
            with pytest.raises(RuntimeError, match="reference evidence changed"):
                guard._cut62_check_draft_history_selection(db, wrong_key)
            db.execute("SAVEPOINT history_reference_drift")
            db.execute(
                "UPDATE defense_recognition_submissions SET answer_json='{\"changed\":true}' WHERE attempt_id='recognition-id'"
            )
            assert (
                guard._cut62_draft_history_reference(db, history_keys)[2]
                != reference_digest
            )
            with pytest.raises(RuntimeError, match="reference evidence changed"):
                guard._cut62_check_draft_history_selection(db, selection)
            db.execute("ROLLBACK TO SAVEPOINT history_reference_drift")
            assert (
                guard._cut62_draft_history_reference(db, history_keys)[2]
                == reference_digest
            )
            # Domain-bind selected identities to both source and projected rows.
            selection_mac = guard._cut62_mac(
                b"h" * 32, "history-selection", sorted(chosen)
            )
            assert selection_mac != guard._cut62_mac(
                b"h" * 32, "history-selection", sorted(chosen | {"mixed"})
            )
            for name, pred in {
                "cards": lambda r: r["id"] in chosen,
                "daily_queue": lambda r: r["card_id"] in chosen,
                "queue_attempt_origins": lambda r: r["card_id"] in chosen,
                "defense_recognition_submissions": lambda r: (
                    r["candidate_id"] in {"candidate-" + c for c in chosen}
                ),
                "defense_attempts": lambda r: r["card_id"] in chosen,
                "reviews": lambda r: r["card_id"] in chosen,
            }.items():
                expected[name] = [row for row in expected[name] if not pred(row)]
            for row in expected["threat_training_candidates"]:
                if row["card_id"] in chosen:
                    row["card_id"] = None
            next(
                row
                for row in expected["priority_repertoire_source_epochs"]
                if row["repertoire_id"] == "history-rep"
            )["version"] += 3
            expected["priority_repertoire_source_epochs"].append(
                {"repertoire_id": "history-missing", "version": 3}
            )
            expected["priority_repertoire_source_epochs"].sort(
                key=lambda row: json.dumps(row, separators=(",", ":"))
            )
            next(
                row
                for row in expected["frozen_opening_runs"]
                if row["run_id"] == "history-run"
            )["invalidated"] = 1
            # Production-format HMAC projection is checked separately from
            # the fixture's own expected-row oracle, for its explicit cohort.
            cohort_sql = "SELECT id AS card_id, 'candidate-'||id AS candidate_id, repertoire_id FROM public.cards WHERE id IN ('recognition','move','control','quiet')"
            layout = guard.table_layout(db)
            history_projected = {
                name: guard._cut62_projected_history_table(
                    db, name, layout[name][0], b"h" * 32, cohort_sql
                )
                for name in guard.CUT62_HISTORY_PROJECTED_TABLES
            }
            # Legacy C11 projection must model the same absent-row upsert.
            quiet_cohort = "SELECT id card_id,'candidate-'||id candidate_id,repertoire_id FROM public.cards WHERE id='quiet'"
            old_epoch_projection = guard._cut62_projected_defense_table(
                db,
                "priority_repertoire_source_epochs",
                layout["priority_repertoire_source_epochs"][0],
                b"h" * 32,
                quiet_cohort,
            )
            db.execute("SAVEPOINT legacy_missing_epoch")
            db.execute(
                "UPDATE threat_training_candidates SET card_id=NULL WHERE id='candidate-quiet'"
            )
            db.execute("DELETE FROM daily_queue WHERE id=910006")
            db.execute("DELETE FROM queue_attempt_origins WHERE queue_entry_id=910006")
            db.execute("DELETE FROM cards WHERE id='quiet'")
            assert (
                guard._cut62_table(
                    db,
                    "priority_repertoire_source_epochs",
                    layout["priority_repertoire_source_epochs"][0],
                    b"h" * 32,
                )
                == old_epoch_projection
            )
            db.execute("ROLLBACK TO SAVEPOINT legacy_missing_epoch")
            projected_digests = keyed_digests(expected)
            witness_mac = guard._cut62_mac(
                b"h" * 32,
                "history-projection",
                [
                    json.dumps(selection_mac, sort_keys=True),
                    reference_digest,
                    json.dumps(groups, sort_keys=True),
                    json.dumps(before_digests, sort_keys=True),
                    json.dumps(projected_digests, sort_keys=True),
                ],
            )
            assert witness_mac

            def issuance_count():
                return db.execute(
                    "SELECT count(*) FROM queue_attempt_origins o JOIN study_game_feedback f ON f.effect_key='history-effect' WHERE o.issued_as_head=1 AND o.issued_at>(f.payload_json::jsonb->'qualification'->>'determined_at')::timestamptz AND o.issuance_sequence IS NOT NULL"
                ).fetchone()[0]

            assert issuance_count() == 1
            from tempo_storage.study.issuance import (
                committed_issuance_count,
            )

            class IssuanceAdapter:
                def execute(self, statement, params=()):
                    return db.execute(statement.replace("?", "%s"), params)

            assert (
                committed_issuance_count(IssuanceAdapter(), "2026-01-01T00:00:00Z") == 1
            )
            # A stamped Defense origin changes the retained Game window and
            # frontier; hold it until an exact retained representation exists.
            db.execute("SAVEPOINT stamped_defense")
            db.execute(
                "UPDATE queue_attempt_origins SET issued_as_head=1,issuance_sequence=2,issued_at='2026-01-03T00:00:00Z' WHERE queue_entry_id=910002"
            )
            assert (
                committed_issuance_count(IssuanceAdapter(), "2026-01-01T00:00:00Z") == 2
            )
            assert (
                db.execute(
                    "SELECT max(issuance_sequence) FROM queue_attempt_origins"
                ).fetchone()[0]
                == 2
            )
            assert not eligible("recognition")
            db.execute("SAVEPOINT remove_stamp")
            db.execute("DELETE FROM daily_queue WHERE id=910002")
            db.execute("DELETE FROM queue_attempt_origins WHERE queue_entry_id=910002")
            assert (
                committed_issuance_count(IssuanceAdapter(), "2026-01-01T00:00:00Z") == 1
            )
            assert (
                db.execute(
                    "SELECT max(issuance_sequence) FROM queue_attempt_origins"
                ).fetchone()[0]
                == 1
            )
            db.execute("ROLLBACK TO SAVEPOINT remove_stamp")
            db.execute("ROLLBACK TO SAVEPOINT stamped_defense")
            sequence = db.execute(
                "SELECT last_value,is_called FROM study_attempt_issuance_sequence"
            ).fetchone()
            for _ in range(2):
                db.execute("SAVEPOINT history_cut")
                db.execute(
                    "UPDATE threat_training_candidates SET card_id=NULL WHERE id IN ('candidate-recognition','candidate-move','candidate-control','candidate-quiet')"
                )
                db.execute(
                    "DELETE FROM defense_recognition_submissions WHERE attempt_id IN ('recognition-id','recognition-before-move','control-shared-id')"
                )
                db.execute(
                    "DELETE FROM defense_attempts WHERE attempt_id IN ('move-id','control-shared-id')"
                )
                db.execute("DELETE FROM reviews WHERE id IN (910003,910004)")
                db.execute(
                    "DELETE FROM daily_queue WHERE id IN (910002,910003,910004,910006)"
                )
                db.execute(
                    "DELETE FROM queue_attempt_origins WHERE queue_entry_id IN (910002,910003,910004,910006) AND content_type='defense'"
                )
                db.execute(
                    "DELETE FROM cards WHERE id IN ('recognition','move','control','quiet') AND content_type='defense'"
                )
                actual = all_rows()
                assert actual == expected
                assert all(
                    guard._cut62_table(db, name, layout[name][0], b"h" * 32)
                    == history_projected[name]
                    for name in guard.CUT62_HISTORY_PROJECTED_TABLES
                )
                assert keyed_digests(actual) == projected_digests
                assert (
                    guard._cut62_mac(
                        b"h" * 32,
                        "history-projection",
                        [
                            json.dumps(selection_mac, sort_keys=True),
                            reference_digest,
                            json.dumps(groups, sort_keys=True),
                            json.dumps(before_digests, sort_keys=True),
                            json.dumps(keyed_digests(actual), sort_keys=True),
                        ],
                    )
                    == witness_mac
                )
                assert issuance_count() == 1
                assert (
                    committed_issuance_count(IssuanceAdapter(), "2026-01-01T00:00:00Z")
                    == 1
                )
                assert (
                    db.execute(
                        "SELECT last_value,is_called FROM study_attempt_issuance_sequence"
                    ).fetchone()
                    == sequence
                )
                db.execute("ROLLBACK TO SAVEPOINT history_cut")
                assert all_rows() == before
                assert keyed_digests(all_rows()) == before_digests
            # Exercise the fixed catalog and all shared gates in the task-owned
            # fixture. A caller catches a postcondition failure and commits.
            binding = {
                "version": 1,
                "target": "synthetic",
                "attempt_id": "11111111-1111-1111-1111-111111111111",
                "backup_filename": "tempo-synthetic.dump",
                "backup_sha256": "a" * 64,
                "selection_sha256": "b" * 64,
            }
            # Temp homonyms must never shadow public trigger/helper targets.
            db.execute("CREATE TEMP TABLE reviews(id bigint)")
            db.execute("CREATE TEMP TABLE frozen_opening_pending(run_id text)")
            db.execute("SAVEPOINT history_replica_guard")
            db.execute("SET LOCAL session_replication_role='replica'")
            with pytest.raises(RuntimeError, match="requires origin replication role"):
                guard._cut62_capture_history_v2(
                    db, selection, binding, b"h" * 32, "b" * 64, binding
                )
            db.execute("ROLLBACK TO SAVEPOINT history_replica_guard")
            original = guard._cut62_capture_history_v2(
                db, selection, binding, b"h" * 32, "b" * 64, binding
            )
            assert original["projected"] == history_projected
            # Re-evaluate the earlier selector-based projection as a bounded
            # fixture oracle; production capture uses validated exact keys.
            prior_cohort = f"""WITH ordinary AS ({guard.CUT62_DRAFT_DEFENSE_SELECTOR}), historical AS ({guard._cut62_draft_history_selector()}),
                eligible AS (SELECT card_id,candidate_id,repertoire_id,'no_activity'::text cohort_group FROM ordinary
                             UNION ALL SELECT card_id,candidate_id,repertoire_id,cohort_group FROM historical),
                approved AS (SELECT value->>0 card_id,value->>1 candidate_id,value->>2 cohort_group FROM jsonb_array_elements(%s::jsonb) value)
                SELECT e.card_id,e.candidate_id,e.repertoire_id FROM eligible e JOIN approved a USING(card_id,candidate_id,cohort_group)"""
            # Resolve the old selector once, then hash its resulting exact
            # rows. Nesting its full plan inside every oracle cursor itself
            # exceeds the production memory limit, even for this tiny fixture.
            prior_rows = db.execute(
                "SELECT * FROM ("
                + prior_cohort
                + ") prior ORDER BY card_id,candidate_id",
                (json.dumps(history_keys, separators=(",", ":")),),
            ).fetchall()
            assert [(row[0], row[1]) for row in prior_rows] == [
                (row[0], row[1]) for row in history_keys
            ]
            prior_values = json.dumps(prior_rows, separators=(",", ":"))
            prior_bounded = "SELECT value->>0 card_id,value->>1 candidate_id,value->>2 repertoire_id FROM jsonb_array_elements(%s::jsonb)"
            for name in sorted(guard.CUT62_HISTORY_PROJECTED_TABLES):
                assert (
                    guard._cut62_projected_history_table(
                        db,
                        name,
                        layout[name][0],
                        b"h" * 32,
                        prior_bounded,
                        (prior_values,),
                    )
                    == original["projected"][name]
                )
            db.execute("SAVEPOINT history_gate_drift")
            db.execute(
                "ALTER TABLE public.reviews DISABLE TRIGGER priority_source_reviews"
            )
            with pytest.raises(RuntimeError, match="history catalog changed"):
                guard._cut62_draft_apply_history(db, original, b"h" * 32, binding)
            db.execute("ROLLBACK TO SAVEPOINT history_gate_drift")
            assert all_rows() == before
            actual_table = guard._cut62_table

            def fail_after_mutation(database, name, pk, secret):
                result = actual_table(database, name, pk, secret)
                if (
                    name == "cards"
                    and database.execute(
                        "SELECT count(*) FROM cards WHERE id IN ('recognition','move','control','quiet')"
                    ).fetchone()[0]
                    == 0
                ):
                    return {**result, "digest": "0" * 64}
                return result

            monkeypatch.setattr(guard, "_cut62_table", fail_after_mutation)
            with pytest.raises(RuntimeError, match="postcondition changed"):
                guard._cut62_draft_apply_history(db, original, b"h" * 32, binding)
            monkeypatch.setattr(guard, "_cut62_table", actual_table)
            db.commit()
            assert all_rows() == before
            assert keyed_digests(all_rows()) == before_digests
            db.rollback()  # close read-only inspection before the next transaction
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ WRITE")
            real_verify = guard.verify_cut62_history_v2

            def fail_post62(*args, **kwargs):
                raise RuntimeError("synthetic post62 failure")

            monkeypatch.setattr(guard, "verify_cut62_history_v2", fail_post62)
            with pytest.raises(RuntimeError, match="synthetic post62 failure"):
                guard.apply_cut62_history_v2(db, original, b"h" * 32, binding)
            monkeypatch.setattr(guard, "verify_cut62_history_v2", real_verify)
            db.commit()
            assert (
                db.execute(
                    "SELECT max(version) FROM public.tempo_schema_migrations"
                ).fetchone()[0]
                == 61
            )
            assert all_rows() == before
            db.rollback()
            db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ WRITE")
            result = guard.apply_cut62_history_v2(db, original, b"h" * 32, binding)
            assert result["observed_schema"] == 62
            db.commit()
            assert all_rows() == expected
            assert keyed_digests(all_rows()) == keyed_digests(expected)
        finally:
            db.rollback()


def test_draft_defense_history_selection_requires_exact_group_counts():
    selection = {
        "version": 2,
        "target": "synthetic",
        "cohort": "defense-history",
        "keys": [
            ["a", "candidate-a", "no_activity"],
            ["b", "candidate-b", "recognition_only"],
            ["c", "candidate-c", "reviewed"],
        ],
        "expected": {
            "count": 3,
            "groups": {"no_activity": 1, "recognition_only": 1, "reviewed": 1},
            "reference_digest": "a" * 64,
        },
    }
    assert (
        guard.validate_cut62_history_selection(selection)
        == selection["expected"]["groups"]
    )
    changed = copy.deepcopy(selection)
    changed["expected"]["groups"]["reviewed"] = 0
    with pytest.raises(RuntimeError, match="reference evidence"):
        guard.validate_cut62_history_selection(changed)
    changed = copy.deepcopy(selection)
    changed["keys"][1][0] = "a"
    changed["keys"][1][1] = "candidate-a"
    with pytest.raises(RuntimeError, match="unique and sorted"):
        guard.validate_cut62_history_selection(changed)


def test_draft_history_catalog_rejects_missing_or_changed_ledger(monkeypatch):
    baseline = [
        ("trigger", "reviews", "public", "known", "trigger", "definition", "digest")
    ]

    class CatalogDatabase:
        def execute(self, query):
            assert query == "SELECT current_setting('server_version_num')::int"

            class Result:
                def fetchone(self):
                    return (180006,)

            return Result()

    db = CatalogDatabase()
    monkeypatch.setattr(guard, "_cut62_history_catalog_snapshot", lambda db: baseline)
    monkeypatch.setattr(guard, "CUT62_HISTORY_TRUSTED_PG180006_CATALOG", None)
    with pytest.raises(RuntimeError, match="missing reviewed"):
        guard._cut62_check_history_catalog(db)
    monkeypatch.setattr(guard, "CUT62_HISTORY_TRUSTED_PG180006_CATALOG", baseline)
    guard._cut62_check_history_catalog(db)
    for altered in (
        baseline + [("foreign_key", "reviews", "public", "new", "fk", "CASCADE", "c")],
        baseline + [("rule", "reviews", "public", "", "new", "DO ALSO", "O")],
        [("trigger", "reviews", "public", "known", "trigger", "definition", "altered")],
        baseline
        + [
            (
                "helper",
                "",
                "public",
                "priority_review_source_changed",
                "",
                "altered",
                "",
            )
        ],
    ):
        monkeypatch.setattr(
            guard, "_cut62_history_catalog_snapshot", lambda db, rows=altered: rows
        )
        with pytest.raises(RuntimeError, match="catalog changed"):
            guard._cut62_check_history_catalog(db)


def test_schema61_draft_history_catalog_rejects_changed_dependencies():
    import hashlib
    import os
    from urllib.parse import urlparse

    import psycopg

    dsn = os.environ.get("TEMPO_W07_DEFENSE_CENSUS_DSN")
    if not dsn:
        pytest.skip("W07 defense-census disposable database not allocated")
    parsed = urlparse(dsn)
    if parsed.hostname != "127.0.0.1" or parsed.path != "/w07_census":
        raise ValueError("refusing non-W07 disposable database")

    def digest(rows):
        return hashlib.sha256(
            json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()

    with psycopg.connect(dsn) as db:
        db.execute("SET LOCAL search_path=pg_catalog,public,pg_temp")
        assert (
            db.execute("SELECT current_setting('server_version_num')::int").fetchone()[
                0
            ]
            == 180006
        )
        assert (
            db.execute(
                "SELECT max(version) FROM public.tempo_schema_migrations"
            ).fetchone()[0]
            == 61
        )
        baseline = digest(guard._cut62_history_catalog_snapshot(db))
        assert baseline == guard.CUT62_HISTORY_CANDIDATE_PG180006_SHA256
        for statement in (
            "CREATE TABLE public.probe_history_fk(id bigint REFERENCES public.reviews(id) ON DELETE CASCADE)",
            "CREATE RULE probe_history_rule AS ON DELETE TO public.reviews DO ALSO SELECT 1",
            "CREATE TRIGGER probe_history_trigger AFTER DELETE ON public.reviews FOR EACH STATEMENT EXECUTE FUNCTION public.frozen_invalidate_pending()",
        ):
            db.execute("SAVEPOINT history_catalog_probe")
            db.execute(statement)
            assert digest(guard._cut62_history_catalog_snapshot(db)) != baseline
            db.execute("ROLLBACK TO SAVEPOINT history_catalog_probe")
        db.execute("SAVEPOINT history_catalog_probe")
        db.execute("CREATE SCHEMA probe_history_external")
        db.execute(
            "CREATE TABLE probe_history_external.ref(id bigint REFERENCES public.reviews(id) ON DELETE SET NULL)"
        )
        assert digest(guard._cut62_history_catalog_snapshot(db)) != baseline
        db.execute("ROLLBACK TO SAVEPOINT history_catalog_probe")
        db.execute("SAVEPOINT history_catalog_probe")
        db.execute(
            "ALTER FUNCTION public.priority_review_source_changed() SET search_path TO pg_catalog, public"
        )
        assert digest(guard._cut62_history_catalog_snapshot(db)) != baseline
        db.execute("ROLLBACK TO SAVEPOINT history_catalog_probe")
        db.execute("SAVEPOINT history_catalog_probe")
        db.execute("ALTER TABLE public.reviews DISABLE TRIGGER priority_source_reviews")
        assert digest(guard._cut62_history_catalog_snapshot(db)) != baseline
        db.execute("ROLLBACK TO SAVEPOINT history_catalog_probe")
        db.execute("SAVEPOINT history_catalog_probe")
        definition = db.execute(
            "SELECT pg_get_functiondef('public.priority_review_source_changed()'::regprocedure)"
        ).fetchone()[0]
        assert "RETURN NULL;" in definition
        db.execute(
            definition.replace("RETURN NULL;", "RETURN NULL; -- synthetic probe"),
            prepare=False,
        )
        assert digest(guard._cut62_history_catalog_snapshot(db)) != baseline
        db.execute("ROLLBACK TO SAVEPOINT history_catalog_probe")
        assert digest(guard._cut62_history_catalog_snapshot(db)) == baseline
        db.rollback()


def test_draft_history_cut_stops_before_dml_without_reviewed_catalog(monkeypatch):
    secret = b"h" * 32
    witness = {
        "domain": "cut62-history-draft-v1",
        "selection": {},
        "binding": {},
        "source": {},
        "projected": {},
    }
    witness["signature"] = guard._cut62_mac(
        secret,
        "cut62:history-witness",
        [json.dumps(witness, sort_keys=True, separators=(",", ":"))],
    )["digest"]
    monkeypatch.setattr(
        guard, "_cut62_check_draft_history_selection", lambda db, selection: None
    )
    monkeypatch.setattr(
        guard,
        "_validate_cut62_history_binding",
        lambda binding, selection, digest, expected: None,
    )
    monkeypatch.setattr(
        guard,
        "authenticate_cut62_history_v2",
        lambda witness, secret, binding: "test-id",
    )
    monkeypatch.setattr(guard, "_cut62_session", lambda db: None)
    monkeypatch.setattr(guard, "_cut62_history_session", lambda db: None)
    monkeypatch.setattr(guard, "CUT62_HISTORY_TRUSTED_PG180006_CATALOG", None)
    from contextlib import nullcontext

    import psycopg

    class Database:
        class info:
            transaction_status = psycopg.pq.TransactionStatus.INTRANS

        def execute(self, statement):
            return None

        def transaction(self):
            return nullcontext()

    with pytest.raises(RuntimeError, match="missing reviewed Defense history catalog"):
        guard._cut62_draft_apply_history(Database(), witness, secret, {})


def test_draft_history_binding_requires_full_original_and_exact_selection_bytes(
    tmp_path,
):
    import hashlib

    selection = {
        "version": 2,
        "target": "synthetic",
        "cohort": "defense-history",
        "keys": [],
        "expected": {
            "count": 0,
            "groups": {"no_activity": 0, "recognition_only": 0, "reviewed": 0},
            "reference_digest": "a" * 64,
        },
    }
    raw = json.dumps(selection).encode()
    path = tmp_path / "selection.json"
    path.write_bytes(raw)
    path.chmod(0o600)
    loaded, digest = guard.load_cut62_history_selection(path)
    assert loaded == selection and digest == hashlib.sha256(raw).hexdigest()
    binding = {
        "version": 1,
        "target": "synthetic",
        "attempt_id": "original-attempt",
        "backup_filename": "tempo-original.dump",
        "backup_sha256": "c" * 64,
        "selection_sha256": digest,
    }
    assert guard._validate_cut62_attempt_binding(binding, loaded, digest) == binding
    for bad in (
        {"target": "synthetic"},
        {**binding, "version": True},
        {**binding, "backup_sha256": "bad"},
        {**binding, "selection_sha256": "d" * 64},
        {**binding, "attempt_id": ""},
    ):
        with pytest.raises(RuntimeError):
            guard._validate_cut62_attempt_binding(bad, loaded, digest)
    path.write_bytes(json.dumps(selection, separators=(",", ":")).encode())
    _, changed = guard.load_cut62_history_selection(path)
    assert changed != digest
    with pytest.raises(RuntimeError):
        guard._validate_cut62_attempt_binding(binding, loaded, changed)
    valid = {**binding, "attempt_id": "11111111-1111-1111-1111-111111111111"}
    assert guard._validate_cut62_history_binding(valid, loaded, digest, valid) == valid
    for bad in (
        {**valid, "attempt_id": "invalid"},
        {**valid, "backup_filename": "unexpected.dump"},
        {**valid, "backup_sha256": "d" * 64},
    ):
        with pytest.raises(RuntimeError):
            guard._validate_cut62_history_binding(bad, loaded, digest, valid)
    for raw_duplicate in (
        b'{"version":2,"version":2}',
        b'{"groups":{"reviewed":0,"reviewed":0}}',
        b'{"expected":{"count":0,"c\\u006funt":0}}',
    ):
        path.write_bytes(raw_duplicate)
        with pytest.raises(RuntimeError, match="duplicate keys"):
            guard.load_cut62_history_selection(path)
    path.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(RuntimeError, match="size limit"):
        guard.load_cut62_history_selection(path)


def test_history_v2_private_original_never_overwrites_and_authenticates(
    tmp_path, monkeypatch
):
    selection = {
        "version": 2,
        "target": "synthetic",
        "cohort": "defense-history",
        "keys": [],
        "expected": {
            "count": 0,
            "groups": {"no_activity": 0, "recognition_only": 0, "reviewed": 0},
            "reference_digest": "a" * 64,
        },
    }
    binding = {
        "version": 1,
        "target": "synthetic",
        "attempt_id": "11111111-1111-1111-1111-111111111111",
        "backup_filename": "tempo-synthetic.dump",
        "backup_sha256": "b" * 64,
        "selection_sha256": "c" * 64,
    }

    def capture(
        database, selection, binding, secret, selection_sha256, expected_binding
    ):
        source = {
            "version": guard.CUT62_EVIDENCE_VERSION,
            "from": 61,
            "to": 62,
            "layout": {
                name: []
                for name in guard.CUT62_PROTECTED_TABLES
                | guard.CUT62_RETIRED_TABLES
                | {guard.CUT62_LEDGER}
            },
            "rows": {
                name: {}
                for name in guard.CUT62_PROTECTED_TABLES | guard.CUT62_RETIRED_TABLES
            },
            "catalog": {},
            "sequences": {},
        }
        witness = {
            "domain": "cut62-history-draft-v1",
            "selection": selection,
            "binding": binding,
            "source": source,
            "projected": {name: {} for name in guard.CUT62_HISTORY_PROJECTED_TABLES},
        }
        witness["signature"] = guard._cut62_mac(
            secret,
            "cut62:history-witness",
            [json.dumps(witness, sort_keys=True, separators=(",", ":"))],
        )["digest"]
        return witness

    monkeypatch.setattr(guard, "_cut62_capture_history_v2", capture)
    path = tmp_path / "original.json"
    identity = guard.save_cut62_history_v2(
        path, None, selection, binding, "c" * 64, binding
    )
    assert path.stat().st_mode & 0o777 == 0o600
    raw = path.read_bytes()
    witness, secret, loaded_id = guard.load_cut62_history_v2(path, binding)
    assert (
        identity
        == loaded_id
        == guard.authenticate_cut62_history_v2(witness, secret, binding)
    )
    with pytest.raises(FileExistsError):
        guard.save_cut62_history_v2(path, None, selection, binding, "c" * 64, binding)
    assert path.read_bytes() == raw
    with pytest.raises(RuntimeError, match="binding changed"):
        guard.load_cut62_history_v2(path, {**binding, "backup_sha256": "d" * 64})
    replacement = tmp_path / "replacement.json"
    replacement_id = guard.save_cut62_history_v2(
        replacement, None, selection, binding, "c" * 64, binding
    )
    assert replacement_id != identity
    _, _, replacement_loaded = guard.load_cut62_history_v2(replacement, binding)
    assert replacement_loaded == replacement_id and replacement_loaded != identity
    with pytest.raises(FileNotFoundError):
        guard.load_cut62_history_v2(tmp_path / "missing.json", binding)
    incomplete = tmp_path / "incomplete.json"
    incomplete.write_text("{")
    incomplete.chmod(0o600)
    with pytest.raises(json.JSONDecodeError):
        guard.load_cut62_history_v2(incomplete, binding)
    failure_path = tmp_path / "interrupted.json"

    def failed_fsync(fd):
        raise OSError("synthetic fsync failure")

    with monkeypatch.context() as patch:
        patch.setattr(guard.os, "fsync", failed_fsync)
        with pytest.raises(OSError, match="synthetic fsync"):
            guard.save_cut62_history_v2(
                failure_path, None, selection, binding, "c" * 64, binding
            )
    assert not failure_path.exists()
    published_path = tmp_path / "published.json"
    real_fsync = guard.os.fsync
    calls = []

    def fail_directory_sync(fd):
        calls.append(fd)
        if len(calls) == 2:
            raise OSError("synthetic directory fsync failure")
        real_fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(guard.os, "fsync", fail_directory_sync)
        with pytest.raises(OSError, match="directory fsync"):
            guard.save_cut62_history_v2(
                published_path, None, selection, binding, "c" * 64, binding
            )
    assert published_path.exists()
    guard.load_cut62_history_v2(published_path, binding)
    with pytest.raises(FileExistsError):
        guard.save_cut62_history_v2(
            published_path, None, selection, binding, "c" * 64, binding
        )
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"secret":"aa","secret":"bb"}')
    duplicate.chmod(0o600)
    with pytest.raises(RuntimeError, match="duplicate keys"):
        guard.load_cut62_history_v2(duplicate, binding)
    payload = json.loads(raw)
    payload["witness"]["source"]["from"] = 60
    path.write_text(json.dumps(payload))
    with pytest.raises(RuntimeError, match="authentication failed"):
        guard.load_cut62_history_v2(path, binding)
