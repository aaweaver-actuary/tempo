"""Exercise the four incident SQL paths with disposable, populated fixtures."""

from __future__ import annotations

# Preserve direct-script imports after relocation from scripts/.
import sys
from pathlib import Path
_REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPOSITORY))
sys.path.insert(0, str(_REPOSITORY / "database"))
sys.path.insert(0, str(_REPOSITORY / "backend"))

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time
import uuid

import psycopg
from psycopg.errors import TransactionTimeout
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
from tempo_legacy_database.adapters import postgres_store
from tempo_legacy_database.connections import background_read_connection
from app.threat_analysis_commands import claim_threat_analysis


DATABASE_URL = "postgresql://postgres@postgres:5432/tempo"
REPERTOIRE_ID = "incident-benchmark-repertoire"
GAME_ID = "incident-benchmark-game"
THREAT_REQUEST_ID = "incident-benchmark-threat-19999"
NOW = datetime.now(timezone.utc).isoformat()


def seed(database) -> None:
    database.execute("INSERT INTO repertoires(id,name,source_name,created_at) VALUES(%s,%s,%s,%s)",
                     (REPERTOIRE_ID, "Synthetic", "synthetic.pgn", NOW))
    database.execute(
        "INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,"
        "start_fen,moves_json,analysis_version) VALUES(%s,'lichess','synthetic',%s,'rapid',1,"
        "'white','1-0',%s,'[]',1)",
        (GAME_ID, NOW, "8/8/8/8/8/8/8/8 w - - 0 1"),
    )
    database.execute(
        "INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,due_date) "
        "VALUES('incident-benchmark-card',%s,'prefix',%s,'[]','2026-09-29')",
        (REPERTOIRE_ID, "8/8/8/8/8/8/8/8 w - - 0 1"),
    )
    database.cursor().executemany(
        "INSERT INTO threat_analysis_requests(id,request_json,state,created_at,updated_at) "
        "VALUES(%s,'{}','queued',%s,%s)",
        [(f"incident-benchmark-threat-{number:05d}", NOW, NOW) for number in range(20000)],
    )
    database.execute(
        "INSERT INTO repertoire_opportunities(id,repertoire_id,kind,fen_key,status,score,"
        "evidence_json,evidence_fingerprint,created_at,updated_at) "
        "VALUES('incident-benchmark-opportunity',%s,'missing_response','synthetic',"
        "'active',1,'{}','synthetic',%s,%s)", (REPERTOIRE_ID, NOW, NOW),
    )
    database.execute(
        "INSERT INTO discovery_recommendation_requests(opportunity_id,request_id,source_game_id,"
        "source_ply,created_at,updated_at) VALUES('incident-benchmark-opportunity',"
        "'incident-benchmark-threat-19999',%s,1,%s,%s)", (GAME_ID, NOW, NOW),
    )
    database.cursor().executemany(
        "INSERT INTO game_move_analysis_legacy(game_id,ply,loss_cp,depth) VALUES(%s,%s,%s,12)",
        [(GAME_ID, ply, 50 if ply % 4 == 0 else 0) for ply in range(2, 82, 2)],
    )
    database.execute(
        "INSERT INTO repertoire_decision_events_legacy(id,game_id,repertoire_id,card_id,ply,"
        "fen_key,expected_uci,actual_uci,outcome,played_at,updated_at) "
        "VALUES('incident-benchmark-event',%s,%s,'incident-benchmark-card',80,'synthetic',"
        "'e2e4','e2e4','success',%s,%s)", (GAME_ID, REPERTOIRE_ID, NOW, NOW),
    )
    database.cursor().executemany(
        "INSERT INTO game_position_occurrences_legacy(game_id,ply,fen_key,move_uci) "
        "VALUES(%s,%s,%s,'e2e4')",
        [(GAME_ID, number, f"synthetic-position-{number:04d}") for number in range(864)],
    )



def threat_request_state() -> dict:
    """Inspect committed state through a fresh connection, including after timeout."""
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as database:
        request = database.execute(
            "SELECT id,state,lease_id,lease_expires_at,attempts,updated_at,last_error "
            "FROM threat_analysis_requests WHERE id=%s", (THREAT_REQUEST_ID,),
        ).fetchone()
        control = database.execute(
            "SELECT * FROM background_activity WHERE source='threat_analysis' AND work_id=%s",
            (THREAT_REQUEST_ID,),
        ).fetchone()
        recommendations = database.execute(
            "SELECT recommendation.opportunity_id,recommendation.request_id,"
            "recommendation.source_game_id,recommendation.source_ply,"
            "opportunity.status,opportunity.card_id "
            "FROM discovery_recommendation_requests recommendation "
            "LEFT JOIN repertoire_opportunities opportunity ON opportunity.id=recommendation.opportunity_id "
            "WHERE recommendation.request_id=%s", (THREAT_REQUEST_ID,),
        ).fetchall()
    return {"request": request, "background_activity": control, "recommendations": recommendations}


def measure() -> None:
    with postgres_store.connection(background=True) as database:
        settings = database.execute_native(
            "SELECT current_setting('transaction_timeout'),current_setting('lock_timeout')",
        ).fetchone()
        print(f"effective background settings: transaction={settings[0]}, lock={settings[1]}")
        expected_lock_timeout = settings[1]
    before_baseline = threat_request_state()
    request = before_baseline["request"]
    assert request is not None and request["state"] == "queued" and request["attempts"] == 0, (
        f"Expected an unclaimed benchmark request; request_state={before_baseline}"
    )
    assert request["lease_id"] is None and request["lease_expires_at"] is None, before_baseline
    assert before_baseline["background_activity"] is None, before_baseline
    assert len(before_baseline["recommendations"]) == 1, before_baseline
    recommendation = before_baseline["recommendations"][0]
    assert recommendation["status"] == "active" and recommendation["card_id"] is None, before_baseline

    os.environ["TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS"] = "50"
    baseline_started = time.perf_counter()
    baseline_timed_out = False
    try:
        with postgres_store.connection(background=True) as database:
            claim_threat_analysis(database, {})
            database.rollback()
    except TransactionTimeout:
        baseline_timed_out = True
    print(f"threat claim at 50ms: timed_out={baseline_timed_out}, elapsed={time.perf_counter()-baseline_started:.3f}s")
    after_baseline = threat_request_state()
    assert after_baseline == before_baseline, (
        "Timed-out or rolled-back baseline claim must not persist a lease; "
        f"before={before_baseline}, request_state={after_baseline}"
    )
    os.environ["TEMPO_POSTGRES_BACKGROUND_TRANSACTION_TIMEOUT_MS"] = "250"
    before_claim = threat_request_state()
    assert before_claim == before_baseline, (
        "Expected benchmark request to remain claimable before the second attempt; "
        f"request_state={before_claim}"
    )
    started = time.perf_counter()
    try:
        with postgres_store.connection(background=True) as database:
            settings = database.execute_native(
                "SELECT current_setting('transaction_timeout'),current_setting('lock_timeout')",
            ).fetchone()
            assert tuple(settings) == ("250ms", expected_lock_timeout), (
                f"Unexpected second-claim settings: {tuple(settings)}"
            )
            claimed = claim_threat_analysis(database, {})
    except Exception as error:
        error.add_note(f"Benchmark second claim failed after {time.perf_counter()-started:.3f}s; request_state={threat_request_state()}")
        raise
    claim_elapsed = time.perf_counter() - started
    after_claim = threat_request_state()
    assert claimed["job"] is not None, (
        "Expected benchmark threat request to remain claimable; "
        f"request_state={after_claim}"
    )
    assert claimed["job"]["id"] == THREAT_REQUEST_ID, (
        f"Expected benchmark request, got {claimed['job']}; request_state={after_claim}"
    )
    request = after_claim["request"]
    assert request is not None and request["state"] == "leased", after_claim
    assert request["lease_id"] == claimed["job"]["lease_id"], after_claim
    assert request["lease_expires_at"] is not None and request["attempts"] == 1, after_claim
    print(f"threat claim: 20000 queued, 1 eligible, committed in {claim_elapsed:.3f}s; baseline rollback and committed lease verified")



def cleanup() -> None:
    with psycopg.connect(DATABASE_URL) as database:
        database.execute("DELETE FROM threat_analysis_requests WHERE id LIKE 'incident-benchmark-threat-%'")
        database.execute("DELETE FROM repertoires WHERE id=%s", (REPERTOIRE_ID,))
        database.execute("DELETE FROM imported_games WHERE id=%s", (GAME_ID,))


def test_postgres_incident_measurements_retain_parent_admission_and_actual_budgets():
    from database.tests.check_postgres_graph_retention import owned_admission_scope

    # These populated SQL fixtures have no interactive caller. Keep parent API
    # health/study admission out of their timing while retaining a real gate,
    # parent lease preservation, and the original database budgets/assertions.
    with owned_admission_scope('incident-query-' + uuid.uuid4().hex):
        measure()
    print('PASS test_postgres_incident_measurements_retain_parent_admission_and_actual_budgets')


def main() -> None:
    if os.getenv("TEMPO_TEST_INSTANCE") != "disposable":
        raise RuntimeError("This check requires the disposable PostgreSQL test instance")
    os.environ["TEMPO_DATABASE_WRITE_URL"] = DATABASE_URL
    os.environ["TEMPO_DATABASE_READ_URL"] = DATABASE_URL
    cleanup()
    try:
        with psycopg.connect(DATABASE_URL) as database:
            seed(database)
        test_postgres_incident_measurements_retain_parent_admission_and_actual_budgets()
    finally:
        postgres_store.close_pools()
        cleanup()
    from database.tests.check_postgres_graph_retention import run as check_graph_retention

    check_graph_retention()


if __name__ == "__main__":
    main()
