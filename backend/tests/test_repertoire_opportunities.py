"""Issue 4 regressions for auditable repertoire opportunities."""

from datetime import date, datetime, timedelta, timezone
from dataclasses import asdict
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import chess
from fastapi.testclient import TestClient

from app import database
from app.main import app, queue_today, seed_queue
from app.services.repertoire_opportunities import (
    dismiss_opportunity, execute_opportunity_slice,
    list_opportunities, refresh_card_opportunity,
    refresh_node_opportunities, refresh_post_gap_opportunity,
    _calculate_recurring_decisions,
)
from app.services.durable_tasks import claim_task, complete_task, enqueue_task
from app.services.database_executor import database_writer
from app.services import discovery_admission, repertoire_opportunities, threat_pipeline
from app.services.discovery_admission import (
    execute_admission_intent_slice, execute_recommendation_request_slice,
    recommend_missing_continuations,
)
from app.services.threat_pipeline import _request_from_json
from app.services.threat_validation import AnalysisLine, AnalysisReport, EngineScore
from app.services.game_findings import refresh_game_findings


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


def _seed_decision_game(db, number, root_key, target_key, *, miss=True):
    played_at = (datetime.now(timezone.utc) - timedelta(days=number)).isoformat()
    game_id = f"game-{number}"
    moves = ["e2e4", "e7e5", "g1h3" if miss else "g1f3"]
    db.execute("""INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
                  VALUES(?,'lichess','player',?,'rapid',1,'white','*',?,?)""",
               (game_id, played_at, chess.STARTING_FEN, json.dumps(moves)))
    for ply, card_id, key, expected, actual, outcome in (
        (0, "root", root_key, "e2e4", "e2e4", "success"),
        (2, "target", target_key, "g1f3", moves[2], "miss" if miss else "success"),
    ):
        db.execute("""INSERT INTO repertoire_decision_events(
            id,game_id,repertoire_id,card_id,ply,fen_key,expected_uci,actual_uci,
            outcome,played_at,updated_at) VALUES(?,?,'rep',?,?,?,?,?,?,?,?)""",
            (f"{game_id}-{ply}", game_id, card_id, ply, key, expected, actual,
             outcome, played_at, played_at))


def test_issue4_strong_route_weak_target_promotes_without_reviews_or_parent_maturity(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        root_key, target_key = _seed_decision_route(db)
        for number in range(1, 6):
            _seed_decision_game(db, number, root_key, target_key, miss=number <= 4)
        refresh_card_opportunity(db, "rep", "target")
        refresh_card_opportunity(db, "rep", "target")
        items = list_opportunities(db, "rep")
        assert len(items) == 1
        assert items[0]["evidence"]["route_success_count"] == 5
        db.execute("UPDATE settings SET new_cards_per_day=1 WHERE id=1")
        seed_queue(db, date.today().isoformat())
        queued = db.execute("SELECT * FROM daily_queue WHERE card_id='target'").fetchone()
        assert queued and "reached 5 times" in queued["gameplay_priority_reason"]
        assert db.execute("SELECT state FROM cards WHERE id='root'").fetchone()[0] == "new"
        assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
        assert db.execute("SELECT parent_card_id FROM opening_graph_steps WHERE card_id='target'").fetchone()[0] == "root"
    assert "reached 5 times" in queue_today()["cards"][0]["gameplay_priority_reason"]


def test_issue4_one_game_and_successful_unstudied_decision_do_not_promote(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        root_key, target_key = _seed_decision_route(db)
        _seed_decision_game(db, 1, root_key, target_key)
        refresh_card_opportunity(db, "rep", "target")
        assert list_opportunities(db, "rep") == []
        for number in range(2, 7):
            _seed_decision_game(db, number, root_key, target_key, miss=False)
        refresh_card_opportunity(db, "rep", "target")
        assert list_opportunities(db, "rep") == []


def test_discovery_studied_engine_mistakes_exclude_sound_deviations_and_incomplete_horizons(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        root_key, target_key = _seed_decision_route(db)
        db.execute("UPDATE cards SET state='learning',introduced_at=? WHERE id='target'",
                   (date.today().isoformat(),))
        for number in range(1, 7):
            _seed_decision_game(db, number, root_key, target_key, miss=number <= 5)
            game_id = f"game-{number}"
            game_rows = [(0, 20, 0), (2, -130 if number <= 4 else 10,
                                           150 if number <= 4 else 10)]
            if number <= 3:
                game_rows.append((8, -250, 0))
            for ply, evaluation, loss in game_rows:
                db.execute(
                    """INSERT INTO game_move_analysis(game_id,ply,eval_after_cp,loss_cp,depth)
                       VALUES(?,?,?,?,18)""",
                    (game_id, ply, evaluation, loss),
                )
        refresh_card_opportunity(db, "rep", "target")
        discoveries = [item for item in list_opportunities(db, "rep")
                       if item["evidence"].get("analysis_based")]
        assert len(discoveries) == 1
        assert discoveries[0]["fen_key"] == target_key
        evidence = discoveries[0]["evidence"]
        assert evidence["encounter_count"] == 6
        assert evidence["analyzed_count"] == 6
        assert evidence["miss_count"] == 4
        assert evidence["later_sample_count"] == 3
        assert evidence["later_average_change_cp"] == 270
        assert evidence["immediate_average_loss_cp"] == 150
        assert evidence["strong_prefix_count"] == 6


def test_discovery_explicit_locked_decision_survives_daily_cap_and_duplicate_clicks(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        root_key, target_key = _seed_decision_route(db)
        for number in range(1, 6):
            _seed_decision_game(db, number, root_key, target_key)
            db.execute(
                "INSERT INTO game_position_occurrences(game_id,ply,fen_key,move_uci) VALUES(?,?,?,'g1h3')",
                (f"game-{number}", 2, target_key),
            )
        refresh_card_opportunity(db, "rep", "target")
        opportunity_id = list_opportunities(db, "rep")[0]["id"]
        db.execute("UPDATE settings SET new_cards_per_day=0 WHERE id=1")
    client = TestClient(app)
    path = f"/api/repertoires/rep/opportunities/{opportunity_id}/train"
    first = client.post(path)
    assert first.status_code == 200
    assert first.json() == {"card_id": "target", "queued": True, "idempotent": False}
    assert client.post(path).json()["idempotent"] is True
    with database.connection() as db:
        seed_queue(db, date.today().isoformat())
        queued = db.execute("SELECT admission_kind,admission_source FROM daily_queue WHERE card_id='target'").fetchall()
        assert len(queued) == 1
        assert queued[0]["admission_kind"] == "explicit"
        assert queued[0]["admission_source"] == f"discovery:{opportunity_id}"
        assert db.execute("SELECT state FROM cards WHERE id='root'").fetchone()[0] == "new"
        assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
    queued_card = next(item for item in queue_today()["cards"] if item["id"] == "target")
    assert queued_card["encounter_badges"] == ["Seen recently", "Frequent"]
    assert queued_card["encounter_count_30d"] == 5


def test_discovery_black_evaluation_sign_and_mate_are_typed_separately():
    rows = []
    for number in range(1, 6):
        rows.append({
            "fen_key": "black-position", "expected_uci": "g8f6", "game_id": f"game-{number}",
            "ply": 3, "color": "black", "loss_cp": 120 if number <= 3 else 10,
            "mate_before": None, "mate_after": 3 if number == 3 else None,
            "previous_eval_cp": -30, "following_eval_cp": 100 if number <= 2 else None,
            "previous_mate": None, "following_mate": 2 if number == 3 else None,
            "previous_analyzed_count": 1, "previous_weak_count": 0,
        })
    evidence = _calculate_recurring_decisions(rows, 90)[0]["evidence"]
    assert evidence["miss_count"] == 3
    assert evidence["immediate_average_loss_cp"] == 120
    assert evidence["immediate_cp_sample_count"] == 2
    assert evidence["later_average_change_cp"] == 130
    assert evidence["later_sample_count"] == 2
    assert evidence["mate_outcomes"] == [{
        "game_id": "game-3", "preceding_move_mate": None, "third_later_turn_mate": 2,
    }]


def test_discovery_prefix_split_isolates_target_without_transferring_reviews(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        root_key, target_key = _seed_decision_route(db)
        db.execute("""UPDATE cards SET kind='prefix',start_fen=?,moves_json=? WHERE id='target'""",
                   (chess.STARTING_FEN, json.dumps(["e2e4", "e7e5", "g1f3"])))
        for number in range(1, 6):
            _seed_decision_game(db, number, root_key, target_key)
        refresh_card_opportunity(db, "rep", "target")
        opportunity_id = list_opportunities(db, "rep")[0]["id"]
    result = TestClient(app).post(f"/api/repertoires/rep/opportunities/{opportunity_id}/train")
    assert result.status_code == 200, result.text
    continuation_id = result.json()["card_id"]
    assert continuation_id != "target"
    with database.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM prefix_splits WHERE source_card_id='target'").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0
        assert db.execute("SELECT admission_kind FROM daily_queue WHERE card_id=?", (continuation_id,)).fetchone()[0] == "explicit"


def test_discovery_accepted_engine_branch_survives_publication_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    monkeypatch.setattr("app.main.enqueue_opening_graph_rebuild", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.main.enqueue_integrity_scans", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.main.enqueue_coverage_refresh", lambda *args, **kwargs: None)
    database.initialize()
    with database.connection() as db:
        root_key, target_key = _seed_decision_route(db)
        _seed_decision_game(db, 1, root_key, target_key)
        db.execute("UPDATE imported_games SET analysis_state='ready',analysis_version=1 WHERE id='game-1'")
        board = chess.Board()
        board.push_uci("e2e4")
        board.push_uci("e7e5")
        position_fen = board.fen()
        db.execute(
            """INSERT INTO game_move_analysis(game_id,ply,eval_after_cp,loss_cp,depth,
                 position_fen,engine_version,network_version)
               VALUES('game-1',2,-120,140,18,?,'Stockfish 19 WASM','nn-61e7af4bb97d.nnue')""",
            (position_fen,),
        )
        for rank, move, score, variation in (
            (1, "g1f3", 20, ["g1f3", "b8c6"]),
            (2, "b1c3", 10, ["b1c3", "g8f6"]),
        ):
            db.execute(
                """INSERT INTO game_move_analysis_candidates(
                     game_id,ply,rank,candidate_uci,score_cp,principal_variation_json,
                     depth,position_fen,engine_version,network_version)
                   VALUES('game-1',2,?,?,?,?,18,?,'Stockfish 19 WASM','nn-61e7af4bb97d.nnue')""",
                (rank, move, score, json.dumps(variation), position_fen),
            )
        repertoire_opportunities._publish(
            db, repertoire_id="rep", kind="post_gap_weakness", fen_key=target_key,
            target="e7e5", card_id=None, opponent_move_uci="e7e5", score=1,
            evidence={"supporting_games": 1,
                      "findings": [{"game_id": "game-1", "mistake_ply": 2}]},
        )
        opportunity_id = list_opportunities(db, "rep")[0]["id"]
        fingerprint = list_opportunities(db, "rep")[0]["evidence_fingerprint"]
        discovery = list_opportunities(db, "rep")[0]
        assert discovery["trained_color"] == "white"
        assert discovery["source_games"][0]["id"] == "game-1"
    assert recommend_missing_continuations(opportunity_id)["state"] == "waiting"
    execute_recommendation_request_slice({"payload": {"opportunity_id": opportunity_id}})
    with database.connection() as db:
        request_row = db.execute(
            "SELECT id,request_json FROM threat_analysis_requests ORDER BY created_at LIMIT 1",
        ).fetchone()
        full_history_request = _request_from_json(json.loads(request_row["request_json"]))
        assert full_history_request.position_prefix_uci == ("e2e4", "e7e5")
        report = AnalysisReport(full_history_request, (
            AnalysisLine("g1f3", ("g1f3", "b8c6"), EngineScore(cp=20), 14),
            AnalysisLine("b1c3", ("b1c3", "g8f6"), EngineScore(cp=10), 14),
        ), True)
        wrong_history = asdict(report)
        wrong_history["request"]["position_prefix_uci"] = ["d2d4"]
        db.execute(
            "UPDATE threat_analysis_requests SET state='complete',report_json=? WHERE id=?",
            (json.dumps(wrong_history), request_row["id"]),
        )
    assert recommend_missing_continuations(opportunity_id)["state"] == "waiting"
    with database.connection() as db:
        db.execute(
            "UPDATE threat_analysis_requests SET state='complete',report_json=? WHERE id=?",
            (json.dumps(asdict(report)), request_row["id"]),
        )
    recommendation = recommend_missing_continuations(opportunity_id)
    assert recommendation["state"] == "ready"
    assert recommendation["accepted_moves_uci"] == ["g1f3"]
    assert recommendation["candidates"][0]["move_uci"] == "g1f3"
    assert recommendation["candidates"][0]["similarity"] == "exact transposition"
    with TestClient(app) as client:
        accepted = client.post(f"/api/discoveries/{opportunity_id}/accept", json={
            "selected_move_uci": "b1c3", "evidence_fingerprint": fingerprint,
        })
        assert accepted.status_code == 202, accepted.text
        intent_id = accepted.json()["intent_id"]
    with database.connection() as db:
        intent = db.execute("SELECT * FROM discovery_admission_intents WHERE id=?", (intent_id,)).fetchone()
        assert intent["state"] == "preparing"
        saved_line = db.execute("SELECT moves_json FROM repertoire_lines WHERE id=?", (intent["line_id"],)).fetchone()
        assert saved_line and json.loads(saved_line["moves_json"]) == ["b1c3", "g8f6"]
        from app.services.cards import card_id
        target_card_id = card_id(position_fen, ["b1c3"])
        db.execute(
            """INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,
                 content_type,trained_color) VALUES(?,'rep','response',?,'["b1c3"]','new',?,'opening','white')""",
            (target_card_id, position_fen, date.today().isoformat()),
        )
        db.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('rep',?)",
                   (target_card_id,))
        db.execute("UPDATE opening_graph_publications SET generation=2 WHERE repertoire_id='rep'")
        db.execute(
            """INSERT INTO opening_graph_steps(repertoire_id,generation,line_id,decision_index,
                 segment_kind,first_decision_index,last_decision_index,decision_fen_keys_json,
                 card_id,parent_card_id,decision_fen_key,starting_fen,moves_json,trained_color)
               VALUES('rep',2,?,0,'decision',0,0,?, ?,NULL,?,?, '["b1c3"]','white')""",
            (intent["line_id"], json.dumps([target_key]), target_card_id, target_key, position_fen),
        )
        db.execute(
            """INSERT INTO repertoire_integrity_state(repertoire_id,status,checked_at,scan_status)
               VALUES('rep','clean',?,'idle') ON CONFLICT(repertoire_id) DO UPDATE SET
               status='clean',checked_at=excluded.checked_at,scan_status='idle'""",
            (datetime.now(timezone.utc).isoformat(),),
        )
        task = db.execute(
            "SELECT * FROM background_tasks WHERE kind='discovery_admission' AND deduplication_key=?",
            (intent_id,),
        ).fetchone()
        db.execute("UPDATE background_tasks SET state='leased',lease_token='lease' WHERE id=?", (task["id"],))
        work = {**dict(task), "lease_token": "lease", "payload": json.loads(task["payload_json"])}
    assert execute_admission_intent_slice(work) is False
    with database.connection() as db:
        assert db.execute("SELECT state FROM discovery_admission_intents WHERE id=?", (intent_id,)).fetchone()[0] == "queued"
        assert db.execute("SELECT admission_kind FROM daily_queue WHERE card_id=?", (target_card_id,)).fetchone()[0] == "explicit"
        assert db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 0


def test_discoveries_feed_paginates_beyond_first_hundred_per_repertoire(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        db.execute(
            "INSERT INTO repertoires(id,name,source_name,created_at) VALUES('rep','Main','main.pgn',?)",
            (date.today().isoformat(),),
        )
        for index in range(103):
            repertoire_opportunities._publish(
                db, repertoire_id="rep", kind="post_gap_weakness",
                fen_key=" ".join(chess.STARTING_FEN.split()[:4]),
                target=f"candidate-{index}", card_id=None,
                opponent_move_uci=None, score=1,
                evidence={"supporting_games": 1},
            )
    with TestClient(app) as client:
        first = client.get("/api/discoveries?offset=0&limit=100").json()
        last = client.get("/api/discoveries?offset=100&limit=10").json()
    assert first["total"] == last["total"] == 103
    assert len(first["discoveries"]) == 100
    assert first["next_offset"] == 100
    assert len(last["discoveries"]) == 3
    assert last["next_offset"] is None
    assert len({item["id"] for item in first["discoveries"] + last["discoveries"]}) == 103


def test_discovery_recommendation_worker_keeps_foreground_free_and_replays_once(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    database_writer.start()
    try:
        with database.connection() as db:
            root_key, target_key = _seed_decision_route(db)
            _seed_decision_game(db, 1, root_key, target_key)
            db.execute("UPDATE imported_games SET analysis_state='ready',analysis_version=1 WHERE id='game-1'")
            repertoire_opportunities._publish(
                db, repertoire_id="rep", kind="post_gap_weakness", fen_key=target_key,
                target="e7e5", card_id=None, opponent_move_uci="e7e5", score=1,
                evidence={"supporting_games": 1,
                          "findings": [{"game_id": "game-1", "mistake_ply": 2}]},
            )
            opportunity_id = list_opportunities(db, "rep")[0]["id"]
        task = claim_task("discovery_recommendation")
        assert task
        began = threading.Event()
        release = threading.Event()
        original_prepare = discovery_admission._full_history_request

        def paused_prepare(game):
            began.set()
            assert release.wait(5)
            return original_prepare(game)

        monkeypatch.setattr(discovery_admission, "_full_history_request", paused_prepare)
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(execute_recommendation_request_slice, task)
            assert began.wait(2)
            started = time.perf_counter()
            with database.connection() as db:
                assert db.execute("SELECT COUNT(*) FROM imported_games").fetchone()[0] == 1
            assert time.perf_counter() - started < 1
            release.set()
            future.result(timeout=5)
        assert complete_task(task["id"], task["generation"], task["lease_token"])
        enqueue_task("discovery_recommendation", opportunity_id,
                     {"opportunity_id": opportunity_id})
        database.initialize()
        replay = claim_task("discovery_recommendation")
        assert replay
        execute_recommendation_request_slice(replay)
        with database.read_connection() as db:
            assert db.execute("SELECT COUNT(*) FROM threat_analysis_requests").fetchone()[0] == 1
            recommendation = db.execute(
                "SELECT request_id,source_game_id,source_ply FROM discovery_recommendation_requests"
            ).fetchone()
            assert recommendation["source_game_id"] == "game-1"
            assert recommendation["source_ply"] == 2
        claimed_request = threat_pipeline.claim_analysis_request()
        assert claimed_request and claimed_request["id"] == recommendation["request_id"]
        assert claimed_request["request"]["position_prefix_uci"] == ["e2e4", "e7e5"]
    finally:
        database_writer.stop()


def test_issue4_transposed_game_routes_aggregate_at_one_canonical_target(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    intended = ["g1f3", "d7d5", "g2g3", "g8f6", "f1g2"]
    transposed = ["g2g3", "d7d5", "g1f3", "g8f6", "f1g2"]
    board = chess.Board()
    intended_keys = []
    for move in intended:
        intended_keys.append(" ".join(board.fen().split()[:4]))
        board.push_uci(move)
    transposed_board = chess.Board()
    transposed_keys = []
    for move in transposed:
        transposed_keys.append(" ".join(transposed_board.fen().split()[:4]))
        transposed_board.push_uci(move)
    assert intended_keys[4] == transposed_keys[4]
    with database.connection() as db:
        _seed_decision_route(db)
        db.execute("UPDATE opening_graph_steps SET decision_fen_keys_json=? WHERE card_id='root'",
                   (json.dumps([intended_keys[0], intended_keys[2]]),))
        db.execute("UPDATE opening_graph_steps SET decision_fen_key=?,decision_fen_keys_json=? WHERE card_id='target'",
                   (intended_keys[4], json.dumps([intended_keys[4]])))
        for number in range(1, 6):
            game_id = f"transposed-{number}"
            game_moves = transposed if number <= 2 else intended
            keys = transposed_keys if number <= 2 else intended_keys
            played_at = (datetime.now(timezone.utc) - timedelta(days=number)).isoformat()
            db.execute("""INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
                          VALUES(?,'lichess','player',?,'rapid',1,'white','*',?,?)""",
                       (game_id, played_at, chess.STARTING_FEN, json.dumps(game_moves)))
            for ply, card_id, key, outcome in ((0, "root", keys[0], "success"),
                                               (2, "root", keys[2], "success"),
                                               (4, "target", keys[4], "miss")):
                db.execute("""INSERT INTO repertoire_decision_events(id,game_id,repertoire_id,
                    card_id,ply,fen_key,expected_uci,actual_uci,outcome,played_at,updated_at)
                    VALUES(?,?,'rep',?,?,?,?,?,?,?,?)""",
                    (f"{game_id}-{ply}", game_id, card_id, ply, key,
                     "f1g2" if ply == 4 else game_moves[ply],
                     "f1h3" if ply == 4 else game_moves[ply], outcome, played_at, played_at))
        refresh_card_opportunity(db, "rep", "target")
        items = list_opportunities(db, "rep")
        assert len(items) == 1
        assert items[0]["fen_key"] == intended_keys[4]
        assert items[0]["evidence"]["route_success_count"] == 5


def test_issue4_dismissed_unchanged_opportunity_stays_dismissed_until_material_games(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        root_key, target_key = _seed_decision_route(db)
        for number in range(1, 6):
            _seed_decision_game(db, number, root_key, target_key)
        refresh_card_opportunity(db, "rep", "target")
        opportunity_id = list_opportunities(db, "rep")[0]["id"]
        assert dismiss_opportunity(db, "rep", opportunity_id)
        refresh_card_opportunity(db, "rep", "target")
        assert list_opportunities(db, "rep") == []
        for number in range(6, 9):
            _seed_decision_game(db, number, root_key, target_key)
        refresh_card_opportunity(db, "rep", "target")
        assert [item["id"] for item in list_opportunities(db, "rep")] == [opportunity_id]


def test_issue4_personal_common_move_surfaces_without_masters_or_cohort_data(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        _seed_decision_route(db)
        board = chess.Board()
        board.push_uci("e2e4")
        fen_key = " ".join(board.fen().split()[:4])
        now = datetime.now(timezone.utc).isoformat()
        db.execute("""INSERT INTO repertoire_coverage_runs(id,repertoire_id,status,settings_json,created_at,updated_at)
                      VALUES('run','rep','complete',?, ?,?)""",
                   (json.dumps({"path_floor": 0.0005, "maia_elo": 1500}), now, now))
        db.execute("""INSERT INTO repertoire_coverage_nodes(
            id,run_id,repertoire_id,fen,fen_key,ply,trained_color,routes_json,
            covered_replies_json,explorer_status,maia_status,updated_at)
            VALUES('node','run','rep',?, ?,1,'white','[]','["e7e5"]','failed','failed',?)""",
            (board.fen(), fen_key, now))
        for number in range(1, 4):
            game_id = f"gap-{number}"
            db.execute("""INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
                          VALUES(?,'lichess','player',?,'rapid',1,'white','*',?,?)""",
                       (game_id, now, chess.STARTING_FEN, json.dumps(["e2e4", "c7c5"])))
            db.execute("""INSERT INTO game_repertoire_matches(game_id,repertoire_id,classification,updated_at)
                          VALUES(?,'rep','opponent repertoire gap',?)""", (game_id, now))
            db.execute("""INSERT INTO game_position_occurrences(game_id,ply,fen_key,move_uci)
                          VALUES(?,1,?,'c7c5')""", (game_id, fen_key))
        refresh_node_opportunities(db, "rep", "node")
        items = list_opportunities(db, "rep")
        assert len(items) == 1 and items[0]["opponent_move_uci"] == "c7c5"
        assert items[0]["evidence"]["explorer_probability"] is None
        assert items[0]["evidence"]["personal_count"] == 3
        refresh_node_opportunities(db, "rep", "node")
        assert len(list_opportunities(db, "rep")) == 1


def test_issue4_post_gap_finding_reuses_existing_card_and_api_lists_it(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        _seed_decision_route(db)
        root_key = " ".join(chess.STARTING_FEN.split()[:4])
        now = datetime.now(timezone.utc).isoformat()
        db.execute("""INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
                      VALUES('gap','lichess','player',?,'rapid',1,'white','*',?,'["e2e4","c7c5"]')""",
                   (now, chess.STARTING_FEN))
        db.execute("""INSERT INTO game_findings(id,game_id,analysis_version,ply,kind,confidence,
            evidence_json,repertoire_id,card_id,created_at,updated_at)
            VALUES('finding','gap',1,2,'repertoire gap',1,?,'rep','target',?,?)""",
            (json.dumps({"opponent_gap_fen_key": root_key, "opponent_gap_move_uci": "c7c5",
                         "mistake_ply": 2, "mistake_loss_cp": 180}), now, now))
        refresh_post_gap_opportunity(db, "rep", "finding")
        refresh_post_gap_opportunity(db, "rep", "finding")
        assert len(list_opportunities(db, "rep")) == 1
        assert list_opportunities(db, "rep")[0]["card_id"] == "target"
    response = TestClient(app).get("/api/repertoires/rep/opportunities")
    assert response.status_code == 200
    assert response.json()["opportunities"][0]["kind"] == "post_gap_weakness"


def test_issue4_game_finding_records_canonical_opponent_gap_and_engine_consequence(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    now = datetime.now(timezone.utc).isoformat()
    board = chess.Board()
    board.push_uci("e2e4")
    gap_key = " ".join(board.fen().split()[:4])
    board.push_uci("c7c5")
    with database.connection() as db:
        _seed_decision_route(db)
        db.execute("""INSERT INTO cards(id,repertoire_id,kind,start_fen,moves_json,state,due_date,content_type,trained_color)
                      VALUES('gap-card','rep','response',?,'["g1f3"]','new',?,'opening','white')""",
                   (board.fen(), date.today().isoformat()))
        db.execute("INSERT INTO repertoire_cards(repertoire_id,card_id) VALUES('rep','gap-card')")
        db.execute("""INSERT INTO imported_games(id,provider,username,played_at,speed,rated,color,result,start_fen,moves_json)
                      VALUES('gap-game','lichess','player',?,'rapid',1,'white','*',?,?)""",
                   (now, chess.STARTING_FEN, json.dumps(["e2e4", "c7c5", "g1h3"])))
        db.execute("""INSERT INTO game_repertoire_matches(game_id,repertoire_id,is_primary,
                    classification,first_opponent_gap_ply,out_of_book_ply,updated_at)
                    VALUES('gap-game','rep',1,'opponent repertoire gap',1,2,?)""", (now,))
        db.execute("""INSERT INTO game_move_analysis(game_id,ply,eval_before_cp,eval_after_cp,
                    loss_cp,label,depth,best_move_uci,principal_variation_json)
                    VALUES('gap-game',2,20,-180,200,'major mistake',14,'g1f3','["g1f3"]')""")
    refresh_game_findings("gap-game")
    with database.connection() as db:
        finding = db.execute("SELECT * FROM game_findings WHERE game_id='gap-game' AND kind='repertoire gap'").fetchone()
        evidence = json.loads(finding["evidence_json"])
        assert evidence["opponent_gap_fen_key"] == gap_key
        assert evidence["opponent_gap_move_uci"] == "c7c5"
        assert evidence["mistake_loss_cp"] == 200
        assert finding["card_id"] == "gap-card"
        refresh_post_gap_opportunity(db, "rep", finding["id"])
        assert list_opportunities(db, "rep")[0]["card_id"] == "gap-card"


def test_issue4_covered_and_low_probability_replies_do_not_create_noise(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        _seed_decision_route(db)
        board = chess.Board()
        board.push_uci("e2e4")
        fen_key = " ".join(board.fen().split()[:4])
        now = datetime.now(timezone.utc).isoformat()
        db.execute("""INSERT INTO repertoire_coverage_runs(id,repertoire_id,status,settings_json,created_at,updated_at)
                      VALUES('run','rep','complete','{}',?,?)""", (now, now))
        db.execute("""INSERT INTO repertoire_coverage_nodes(id,run_id,repertoire_id,fen,fen_key,ply,
                    trained_color,routes_json,covered_replies_json,explorer_status,maia_status,
                    explorer_games,updated_at) VALUES('node','run','rep',?, ?,1,'white','[]',
                    '["e7e5"]','complete','complete',500,?)""", (board.fen(), fen_key, now))
        for move, probability, covered in (("e7e5", 0.6, 1), ("c7c5", 0.01, 0), ("d7d5", 0.08, 0)):
            db.execute("""INSERT INTO repertoire_coverage_candidates(node_id,move_uci,
                        explorer_probability,maia_probability,covered,source_state)
                        VALUES('node',?,?,?,?,'blended')""",
                       (move, probability, probability, covered))
        refresh_node_opportunities(db, "rep", "node")
        assert [item["opponent_move_uci"] for item in list_opportunities(db, "rep")] == ["d7d5"]
        db.execute("UPDATE repertoire_coverage_nodes SET explorer_status='failed' WHERE id='node'")
        refresh_node_opportunities(db, "rep", "node")
        assert list_opportunities(db, "rep")[0]["evidence"]["explorer_probability"] is None
        assert list_opportunities(db, "rep")[0]["evidence"]["maia_probability"] == 0.08
        db.execute("UPDATE repertoire_coverage_runs SET created_at=? WHERE id='run'",
                   ((datetime.now(timezone.utc) - timedelta(days=8)).isoformat(),))
        refresh_node_opportunities(db, "rep", "node")
        assert list_opportunities(db, "rep") == []
        db.execute("UPDATE repertoire_coverage_runs SET created_at=? WHERE id='run'", (now,))
        refresh_node_opportunities(db, "rep", "node")
        assert len(list_opportunities(db, "rep")) == 1
        db.execute("UPDATE repertoire_coverage_nodes SET covered_replies_json='[\"e7e5\",\"d7d5\"]' WHERE id='node'")
        refresh_node_opportunities(db, "rep", "node")
        assert list_opportunities(db, "rep") == []


def test_issue4_background_scan_yields_to_foreground_and_replays_without_duplication(tmp_path, monkeypatch, request):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "tempo.db")
    database.initialize()
    with database.connection() as db:
        root_key, target_key = _seed_decision_route(db)
        for number in range(1, 6):
            _seed_decision_game(db, number, root_key, target_key)
        db.execute("UPDATE cards SET state='learning',introduced_at=? WHERE id='root'",
                   (date.today().isoformat(),))
        entry_id = db.execute(
            "INSERT INTO daily_queue(queue_date,card_id,position) VALUES(?,'root',0)",
            (date.today().isoformat(),),
        ).lastrowid
    database_writer.start()
    request.addfinalizer(database_writer.stop)
    assert TestClient(app).post("/api/repertoires/rep/opportunities/refresh").status_code == 202
    target_task = None
    for _ in range(12):
        candidate = claim_task("repertoire_opportunity")
        assert candidate
        if candidate["payload"]["phase"] == "cards" and candidate["payload"]["cursor"] == "root":
            target_task = candidate
            break
        execute_opportunity_slice(candidate)
    assert target_task
    entered = threading.Event()
    release = threading.Event()
    original = repertoire_opportunities._calculate_card_evidence

    def paused_calculation(inputs):
        entered.set()
        assert release.wait(timeout=3)
        return original(inputs)

    monkeypatch.setattr(repertoire_opportunities, "_calculate_card_evidence", paused_calculation)
    worker = threading.Thread(target=execute_opportunity_slice, args=(target_task,))
    worker.start()
    try:
        assert entered.wait(timeout=3)
        review = TestClient(app).post("/api/cards/root/review",
                                      json={"outcome": "correct", "queue_entry_id": entry_id})
        assert review.status_code == 200
    finally:
        release.set()
        worker.join(timeout=3)
    assert not worker.is_alive()
    interrupted = claim_task("repertoire_opportunity")
    assert interrupted
    with database.connection() as db:
        db.execute("UPDATE background_tasks SET lease_expires_at=? WHERE id=?",
                   ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), interrupted["id"]))
    replayed = claim_task("repertoire_opportunity")
    assert replayed and replayed["id"] == interrupted["id"]
    assert replayed["lease_token"] != interrupted["lease_token"]
    execute_opportunity_slice(replayed)
    for _ in range(25):
        task = claim_task("repertoire_opportunity")
        if not task:
            break
        execute_opportunity_slice(task)
    with database.connection() as db:
        assert len(list_opportunities(db, "rep")) == 1
        assert db.execute("SELECT COUNT(*) FROM reviews WHERE card_id='root' AND source_kind='study'").fetchone()[0] == 1
        root_summary = db.execute(
            "SELECT * FROM repertoire_decision_gameplay_summaries WHERE repertoire_id='rep' AND fen_key=?",
            (root_key,),
        ).fetchone()
        target_summary = db.execute(
            "SELECT * FROM repertoire_decision_gameplay_summaries WHERE repertoire_id='rep' AND fen_key=?",
            (target_key,),
        ).fetchone()
        assert root_summary["encounter_count"] == root_summary["success_count"] == 5
        assert target_summary["miss_count"] == target_summary["route_success_count"] == 5
