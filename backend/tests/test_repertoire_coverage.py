from datetime import date, datetime, timedelta, timezone
import json
import chess
from fastapi.testclient import TestClient
from app.main import app
from tempo_legacy_database.adapters.database_executor import database_writer

import pytest

from tempo_legacy_database import connections as database
from app.services import repertoire_coverage

from app.services.repertoire_coverage import (
    CoverageCandidate,
    blend_probabilities,
    required_reply_moves,
    discover_opponent_positions,
    recent_player_cohort,
)


def test_coverage_threshold_uses_conditional_node_probability_rather_than_root_path_probability():
    candidates = [
        CoverageCandidate("e7e5", 0.98),
        CoverageCandidate("c7c5", 0.02),
    ]
    required = required_reply_moves(candidates, denominator=100, cumulative_target=0.95)
    assert required == {"e7e5", "c7c5"}
    assert 0.0001 * candidates[1].probability < 0.01  # reach probability is irrelevant


def test_coverage_requires_the_union_of_minimum_probability_and_cumulative_mass_replies():
    candidates = [
        CoverageCandidate("a7a6", 0.60),
        CoverageCandidate("b7b6", 0.25),
        CoverageCandidate("c7c6", 0.10),
        CoverageCandidate("d7d6", 0.03),
        CoverageCandidate("e7e6", 0.02),
    ]
    assert required_reply_moves(
        candidates, denominator=40, cumulative_target=0.90
    ) == {"a7a6", "b7b6", "c7c6", "d7d6"}


def test_coverage_uses_explorer_sample_weight_and_maia_smoothing():
    assert blend_probabilities(0.20, 0.50, explorer_games=200) == pytest.approx(0.35)
    assert blend_probabilities(0.20, None, explorer_games=10) == pytest.approx(0.20)
    assert blend_probabilities(None, 0.50, explorer_games=0) == pytest.approx(0.50)


def test_coverage_uses_nearest_rating_and_personal_speed_cohort(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    now = repertoire_coverage._now()
    with database.connection() as database_connection:
        for index, (rating, speed) in enumerate([(1420, "rapid"), (1500, "rapid"), (1580, "blitz")]):
            database_connection.execute(
                """INSERT INTO imported_games(
                       id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json,player_rating
                   ) VALUES(?,'lichess','TempoPlayer',?,?,1,'white','1-0',?,'[\"e2e4\"]',?)""",
                (f"cohort-{index}", now, speed, "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", rating),
            )
        cohort = recent_player_cohort(database_connection, 1100)
    assert cohort["recent_median_rating"] == 1500
    assert cohort["explorer_rating"] == 1400
    assert cohort["maia_elo"] == 1500
    assert cohort["speed_weights"] == pytest.approx({"rapid": 2 / 3, "blitz": 1 / 3})


def test_unknown_or_stale_coverage_data_never_reports_a_repertoire_complete():
    assert blend_probabilities(None, None, explorer_games=0) is None


def test_transposed_repertoire_positions_are_evaluated_once_and_credited_correctly():
    lines = [
        {
            "start_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "trained_color": "white",
            "moves_json": '["g1f3","g8f6","g2g3","g7g6","f1g2"]',
        },
        {
            "start_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
            "trained_color": "white",
            "moves_json": '["g2g3","g7g6","g1f3","g8f6","f1g2"]',
        },
    ]
    positions = discover_opponent_positions(lines, horizon_fullmoves=15)
    transposed = [position for position in positions if position["ply"] == 5]
    assert len(transposed) == 1
    assert len(transposed[0]["routes"]) == 2


def test_opponent_reply_without_a_trained_response_remains_a_coverage_gap():
    positions = discover_opponent_positions(
        [
            {
                "start_fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                "trained_color": "white",
                "moves_json": '["e2e4","c7c5"]',
            }
        ],
        horizon_fullmoves=15,
    )
    after_e4 = next(position for position in positions if position["ply"] == 1)
    assert "c7c5" not in after_e4["covered_replies"]


def test_coverage_refresh_persists_required_gaps_without_marking_unknown_complete(
    tmp_path, monkeypatch
):
    from importlib.util import find_spec
    assert find_spec("app.services.introduction_priorities") is None
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as database_connection:
        database_connection.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('r','R','r.pgn','2026-09-19T00:00:00+00:00')"
        )
        database_connection.execute(
            """INSERT INTO repertoire_lines(
                   id,repertoire_id,name,trained_color,start_fen,moves_json,created_at
               ) VALUES('l','r','line','white',?, ?, '2026-09-19T00:00:00+00:00')""",
            (
                "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
                '["e2e4","e7e5","g1f3"]',
            ),
        )
    run_id = repertoire_coverage.enqueue_coverage_refresh("r")
    repertoire_coverage.set_explorer_session_token("test-session-token")
    node = repertoire_coverage.claim_coverage_node()
    assert node and node["run_id"] == run_id
    monkeypatch.setattr(
        repertoire_coverage,
        "_fetch_explorer",
        lambda *_: {
            "moves": [
                {"uci": "e7e5", "white": 700, "draws": 100, "black": 100},
                {"uci": "c7c5", "white": 50, "draws": 25, "black": 25},
            ]
        },
    )
    repertoire_coverage.execute_coverage_node(node)
    summary = repertoire_coverage.coverage_summary("r")
    gaps = repertoire_coverage.coverage_gaps("r")
    assert summary["required_branches"] == 2
    assert summary["covered_branches"] == 1
    assert summary["is_complete"] is False
    assert summary["status"] in {"running", "complete"} and summary["last_error"] is None
    assert [gap["move_uci"] for gap in gaps] == ["c7c5"]
    with database.read_connection() as database_connection:
        assert database_connection.execute(
            "SELECT explorer_status FROM repertoire_coverage_nodes WHERE id=?", (node["id"],)
        ).fetchone()[0] == "complete"
        assert database_connection.execute(
            "SELECT state FROM background_tasks WHERE kind='repertoire_opportunity' AND deduplication_key='r'"
        ).fetchone() is None
        assert database_connection.execute("SELECT COUNT(*) FROM repertoire_priority_jobs").fetchone()[0] == 0
    repertoire_coverage.set_explorer_session_token(None)


def test_coverage_claim_waits_for_in_memory_lichess_session_and_keeps_job_queued(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as database_connection:
        database_connection.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('r','R','r.pgn','2026-09-19T00:00:00+00:00')"
        )
        database_connection.execute(
            """INSERT INTO repertoire_lines(
                   id,repertoire_id,name,trained_color,start_fen,moves_json,created_at
               ) VALUES('l','r','line','white',?, ?, '2026-09-19T00:00:00+00:00')""",
            ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", '["e2e4","e7e5"]'),
        )
    repertoire_coverage.set_explorer_session_token(None)
    run_id = repertoire_coverage.enqueue_coverage_refresh("r")

    assert repertoire_coverage.claim_coverage_node() is None
    with database.connection() as database_connection:
        status = database_connection.execute(
            "SELECT explorer_status FROM repertoire_coverage_nodes WHERE run_id=?", (run_id,)
        ).fetchone()["explorer_status"]
    assert status == "queued"

    repertoire_coverage.set_explorer_session_token("ephemeral-session-token")
    try:
        assert repertoire_coverage.claim_coverage_node() is not None
    finally:
        repertoire_coverage.set_explorer_session_token(None)


def test_coverage_explorer_forwards_bearer_token_without_persisting_it(monkeypatch):
    observed = {}

    class Response:
        status_code = 200
        is_success = True

        @staticmethod
        def json():
            return {"moves": []}

    class Client:
        def __init__(self, **kwargs):
            observed["headers"] = kwargs["headers"]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        @staticmethod
        def get(*_args, **_kwargs):
            return Response()

    monkeypatch.setattr(repertoire_coverage.httpx, "Client", Client)
    repertoire_coverage.set_explorer_session_token("ephemeral-session-token")
    try:
        repertoire_coverage._fetch_explorer("fen", "rapid", "1600", "ephemeral-session-token")
    finally:
        repertoire_coverage.set_explorer_session_token(None)

    assert observed["headers"]["Authorization"] == "Bearer ephemeral-session-token"
    assert repertoire_coverage.get_explorer_session_token() is None


def test_explorer_session_handoff_keeps_authorization_in_memory_only():
    from app.main import coverage_explorer_session

    response = coverage_explorer_session("Bearer one-session-token")
    assert response == {"registered": True}
    assert repertoire_coverage.get_explorer_session_token() == "one-session-token"

    response = coverage_explorer_session(None)
    assert response == {"registered": False}
    assert repertoire_coverage.get_explorer_session_token() is None


def test_coverage_requeues_after_rejected_session_and_does_not_repeat_bad_token(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as database_connection:
        database_connection.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('r','R','r.pgn','2026-09-19T00:00:00+00:00')"
        )
        database_connection.execute(
            """INSERT INTO repertoire_lines(
                   id,repertoire_id,name,trained_color,start_fen,moves_json,created_at
               ) VALUES('l','r','line','white',?, ?, '2026-09-19T00:00:00+00:00')""",
            ("rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1", '["e2e4","e7e5"]'),
        )
    repertoire_coverage.set_explorer_session_token("rejected-token")
    run_id = repertoire_coverage.enqueue_coverage_refresh("r")
    node = repertoire_coverage.claim_coverage_node()
    assert node is not None

    def reject_request(*_args):
        raise repertoire_coverage.ExplorerAuthenticationError(401)

    monkeypatch.setattr(repertoire_coverage, "_fetch_explorer", reject_request)
    try:
        repertoire_coverage.execute_coverage_node(node)
        assert repertoire_coverage.get_explorer_session_token() is None
        assert repertoire_coverage.claim_coverage_node() is None
        with database.connection() as database_connection:
            status = database_connection.execute(
                "SELECT explorer_status FROM repertoire_coverage_nodes WHERE run_id=?", (run_id,)
            ).fetchone()["explorer_status"]
        assert status == "queued"
    finally:
        repertoire_coverage.set_explorer_session_token(None)


def _seed_decision_route(db):
    now = datetime.now(timezone.utc).isoformat()
    start = chess.STARTING_FEN
    board = chess.Board()
    root_key = " ".join(board.fen().split()[:4])
    board.push_uci("e2e4")
    board.push_uci("e7e5")
    target_key = " ".join(board.fen().split()[:4])
    db.execute("INSERT INTO repertoires(id,name,source_name,created_at,is_main) VALUES('rep','Main','main.pgn',?,1)", (now,))
    db.execute("""INSERT INTO repertoire_lines(id,repertoire_id,name,trained_color,start_fen,moves_json,created_at)
                  VALUES('line','rep','Main','white',?,?,?)""", (start, json.dumps(["e2e4", "e7e5", "g1f3"]), now))
    for card_id, card_start, moves, state in (
        ("root", start, ["e2e4"], "new"),
        ("target", board.fen(), ["g1f3"], "locked"),
    ):
        db.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color)
                      VALUES(?,?,?,?,?,?,?,'opening','white')""",
                   (card_id, "rep", "response", card_start, json.dumps(moves), state, date.today().isoformat()))
        db.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('rep',?)", (card_id,))
    db.execute("""INSERT INTO opening_graph_publications(repertoire_id,generation,published_at)
                  VALUES('rep',1,?)""", (now,))
    for index, card_id, parent, key, moves in (
        (0, "root", None, root_key, ["e2e4"]),
        (1, "target", "root", target_key, ["g1f3"]),
    ):
        db.execute("""INSERT INTO opening_graph_steps(
            repertoire_id,generation,line_id,decision_index,segment_kind,
            first_decision_index,last_decision_index,decision_fen_keys_json,
            card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color)
            VALUES('rep',1,'line',?,'decision',?,?,?, ?,?,?, ?,?,'white')""",
            (index, index, index, json.dumps([key]), card_id, parent, key,
             start if index == 0 else board.fen(), json.dumps(moves)))
    return root_key, target_key



def _seed_source_transition_coverage(db, *, run_id="transition-run", created_at=None,
                                     explorer_status="complete", maia_status="complete",
                                     run_status="complete", probability=0.2, covered=False):
    board = chess.Board()
    board.push_uci("e2e4")
    fen_key = " ".join(board.fen().split()[:4])
    now = created_at or datetime.now(timezone.utc).isoformat()
    node_id = f"{run_id}-node"
    db.execute("""INSERT INTO repertoire_coverage_runs
        (id,repertoire_id,status,settings_json,created_at,updated_at)
        VALUES(?,'rep',?, ?,?,?)""", (run_id, run_status,
        json.dumps({"path_floor": 0.0005, "maia_elo": 1500, "reply_denominator": 20, "cumulative_target": 0.95}), now, now))
    db.execute("""INSERT INTO repertoire_coverage_nodes
        (id,run_id,repertoire_id,fen,fen_key,ply,trained_color,routes_json,
         covered_replies_json,explorer_status,maia_status,explorer_games,updated_at)
        VALUES(?,?,'rep',?,?,1,'white','[["e2e4"]]',?,?,?,?,?)""",
        (node_id, run_id, board.fen(), fen_key,
         json.dumps(["e7e5", "c7c5"] if covered else ["e7e5"]),
         explorer_status, maia_status, 500, now))
    db.execute("""INSERT INTO repertoire_coverage_candidates
        (node_id,move_uci,explorer_probability,maia_probability,covered,source_state)
        VALUES(?,'c7c5',?,?,?,'blended')""", (node_id, probability, probability, int(covered)))
    return node_id, fen_key



def test_issue8_maia_claim_and_partial_submit_survive_explorer_failure(tmp_path, monkeypatch, request):
    from app.services import repertoire_coverage
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    database_writer.start()
    request.addfinalizer(database_writer.stop)
    from importlib.util import find_spec
    assert find_spec("app.services.introduction_priorities") is None
    with database.connection() as db:
        _seed_decision_route(db)
        node_id, _ = _seed_source_transition_coverage(db, run_status="failed",
            explorer_status="failed", maia_status="queued")
        db.execute("""INSERT INTO repertoire_coverage_nodes
            (id,run_id,repertoire_id,fen,fen_key,ply,trained_color,routes_json,covered_replies_json,updated_at)
            VALUES('remaining-node','transition-run','rep',?,'other-position',3,'white','[]','[]',?)""",
            (chess.STARTING_FEN, datetime.now(timezone.utc).isoformat()))
    job = repertoire_coverage.claim_maia_coverage_node()
    assert job and job["node_id"] == node_id
    repertoire_coverage.submit_maia_coverage(node_id, job["lease_id"], [{"move_uci": "c7c5", "probability": 0.3}])
    with database.connection() as db:
        assert db.execute("SELECT maia_status FROM repertoire_coverage_nodes WHERE id=?", (node_id,)).fetchone()[0] == "complete"
        assert db.execute("SELECT COUNT(*) FROM repertoire_priority_jobs").fetchone()[0] == 0
        assert db.execute("SELECT maia_status FROM repertoire_coverage_nodes WHERE id='remaining-node'").fetchone()[0] == "queued"
        assert db.execute("SELECT maia_status FROM repertoire_coverage_nodes WHERE id=?", (node_id,)).fetchone()[0] == "complete"
        assert db.execute("SELECT maia_probability FROM repertoire_coverage_candidates WHERE node_id=? AND move_uci='c7c5'", (node_id,)).fetchone()[0] == 0.3
        assert db.execute("SELECT state FROM background_tasks WHERE kind='repertoire_opportunity'").fetchone() is None



def test_issue8_partial_maia_failure_checkpoints_coverage_without_optional_refresh(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        _seed_decision_route(db)
        node_id, _ = _seed_source_transition_coverage(db, maia_status="leased")
        db.execute("UPDATE repertoire_coverage_nodes SET lease_id='failure-lease' WHERE id=?", (node_id,))
    response = TestClient(app).post('/api/repertoire-coverage/maia/failure',
        json={"node_id": node_id, "lease_id": "failure-lease", "error": "provider unavailable"})
    assert response.status_code == 200
    with database.connection() as db:
        row = db.execute("SELECT maia_status,last_error FROM repertoire_coverage_nodes WHERE id=?", (node_id,)).fetchone()
        assert row["maia_status"] == "failed"
        assert "provider unavailable" in row["last_error"]
        assert db.execute("SELECT state FROM background_tasks WHERE kind='repertoire_opportunity'").fetchone() is None



def test_issue8_independent_claim_skips_superseded_failed_attempt(tmp_path, monkeypatch, request):
    from app.services import repertoire_coverage
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    database_writer.start()
    request.addfinalizer(database_writer.stop)
    with database.connection() as db:
        _seed_decision_route(db)
        old_node, _ = _seed_source_transition_coverage(db, run_id="old-run", run_status="failed",
            explorer_status="failed", maia_status="queued",
            created_at=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat())
        new_node, _ = _seed_source_transition_coverage(db, run_id="new-run", run_status="failed",
            explorer_status="failed", maia_status="queued")
    job = repertoire_coverage.claim_maia_coverage_node()
    assert job and job["node_id"] == new_node
    with database.connection() as db:
        db.execute("UPDATE repertoire_coverage_nodes SET maia_status='leased',lease_id='obsolete-lease' WHERE id=?", (old_node,))
        before = dict(db.execute("SELECT * FROM repertoire_coverage_nodes WHERE id=?", (old_node,)).fetchone())
    with pytest.raises(RuntimeError, match="lease is no longer active"):
        repertoire_coverage.submit_maia_coverage(old_node, "obsolete-lease", [{"move_uci":"c7c5", "probability":0.9}])
    with database.connection() as db:
        assert dict(db.execute("SELECT * FROM repertoire_coverage_nodes WHERE id=?", (old_node,)).fetchone()) == before
        assert db.execute("SELECT maia_probability FROM repertoire_coverage_candidates WHERE node_id=?", (old_node,)).fetchone()[0] == 0.2
